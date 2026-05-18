"""Sdílená UCI smyčka pro vlastní ChessLab enginy.

Každý ChessLab engine = subprocess komunikující přes UCI protokol. Většina
boilerplate je všude stejná (handshake, parsing 'position', emisi 'bestmove')
— vlastní engine se liší jen v `choose_move(board, time_ms) -> Move | None`
funkci.

Tenhle modul poskytuje:
  - `send_line()` — write to stdout s okamžitým flushem (UCI je line-based,
    jinak by buffering blokoval handshake).
  - `parse_position_command()` — z `position startpos|fen ... [moves ...]`
    sestaví chess.Board.
  - `parse_go_time()` — z `go movetime <N>` / `go wtime <W> btime <B>` vrátí
    rozpočet pro **side-to-move** v ms (None pokud bez limitu).
  - `run_uci_loop(name, author, choose_move)` — kompletní UCI smyčka.
    Engine modul = ~20 řádků wrapper kolem této funkce.

Podporujeme minimum UCI: uci, isready, ucinewgame, position, go, quit.
Ostatní (stop, ponderhit, setoption, debug, register) tiše ignorujeme —
arena ani Play UI je neposílají.
"""

from __future__ import annotations

import sys
from typing import Callable

import chess

# Typový alias pro engine callback — bere aktuální board + time_ms (rozpočet
# na tah v milisekundách, None = bez limitu = engine si rozhodne sám). Vrátí
# vybraný tah, nebo None pro pozici bez legálních tahů → engine pošle 'bestmove 0000'.
#
# **Backward compatibility**: starší enginy (random, greedy, minimax v2.x) měly
# signaturu bez time_ms. Po přechodu na nový protokol musí všechny enginy
# přijmout (a typicky ignorovat) time_ms param. Hloubkový search v3.0+ ho
# používá pro iterative deepening.
ChooseMoveFn = Callable[[chess.Board, int | None], chess.Move | None]

# Heuristika pro `go wtime <W> btime <B>` (no explicit movetime): kolik z remaining
# clock alokovat per move. Default 1/30 = předpokládáme zbývající ~30 tahů do
# konce partie. Stockfish používá komplexnější (1/45 + bonuses), my KISS.
# Pokud arena/play posílají movetime explicit, tahle heuristika se neuplatní.
_TIME_FRACTION_PER_MOVE = 30


def send_line(line: str) -> None:
    """Pošle řádek na stdout + okamžitý flush.

    UCI protokol je line-based, ale Python defaultně stdout bufferuje — arena
    by neviděla naši odpověď, dokud se buffer nezavře (= proces neskončí).
    flush=True to vynutí po každém řádku.
    """
    print(line, flush=True)


def parse_position_command(args: list[str], current: chess.Board) -> chess.Board:
    """Sestaví NOVÝ chess.Board z argumentů `position` UCI příkazu.

    Příklady (token list bez vedoucího 'position'):
      ['startpos']
      ['startpos', 'moves', 'e2e4', 'e7e5']
      ['fen', '<piece>', '<color>', '<castling>', '<ep>', '<half>', '<full>']
      ['fen', ..., 'moves', 'e2e4', 'g8f6']

    Vrací NOVÝ board. `current` je fallback při parse erroru (defenzivní —
    nemělo by se trigger v běžné komunikaci s arenou nebo Play UI).
    """
    if not args:
        return current

    # 'moves' je keyword oddělující "kde začínáme" od "jaké tahy aplikovat".
    if "moves" in args:
        moves_idx = args.index("moves")
        head = args[:moves_idx]
        moves = args[moves_idx + 1:]
    else:
        head = args
        moves = []

    # Sestavit výchozí board z hlavičky.
    if head[0] == "startpos":
        new_board = chess.Board()
    elif head[0] == "fen":
        # FEN má 6 polí oddělených mezerou — split() je rozdělil, slepíme zpět.
        fen_str = " ".join(head[1:7])
        new_board = chess.Board(fen=fen_str)
    else:
        # Neznámý prefix → defenzivní fallback na předchozí stav.
        return current

    # Aplikovat tahy. UCI formát: 'e2e4' (běžný), 'e7e8q' (promoce na dámu),
    # 'e1g1' (rošáda — král z e1 na g1; UCI nepoužívá zkratku O-O).
    for uci in moves:
        new_board.push(chess.Move.from_uci(uci))

    return new_board


def parse_go_time(args: list[str], side_to_move: chess.Color) -> int | None:
    """Z argumentů `go` extrahuje time budget na **side_to_move** v milisekundách.

    UCI `go` má víc time variant:
      - `go movetime <ms>` — explicitní rozpočet na tah (priorita).
      - `go wtime <W> btime <B> [winc <Wi> binc <Bi>] [movestogo <M>]` — clock-style,
        engine si time management dělá sám. KISS: budget = clock / 30.
      - `go depth <N>`, `go nodes <N>`, `go infinite` — bez time limitu.
      - `go` bez args — bez limitu (= vrať tah okamžitě).

    Args:
        args: token list bez vedoucího 'go' (např. ['movetime', '100']).
        side_to_move: chess.WHITE nebo chess.BLACK — pro výběr wtime vs btime.

    Returns:
        int budget v ms, nebo None pokud bez time limitu (engine rozhodne sám).
    """
    # Helper: získej int hodnotu za keyword v args (None pokud chybí nebo parse fail).
    def _int_after(keyword: str) -> int | None:
        if keyword in args:
            idx = args.index(keyword)
            if idx + 1 < len(args):
                try:
                    return int(args[idx + 1])
                except ValueError:
                    return None
        return None

    # Movetime má prioritu — explicit per-move rozpočet.
    movetime = _int_after("movetime")
    if movetime is not None:
        return movetime

    # Clock-style: vyber správnou hodinu podle side-to-move.
    clock_key = "wtime" if side_to_move == chess.WHITE else "btime"
    clock = _int_after(clock_key)
    if clock is not None and clock > 0:
        # Increment (Fischer time) přidáme k rozpočtu, protože ho stejně dostaneme.
        inc_key = "winc" if side_to_move == chess.WHITE else "binc"
        inc = _int_after(inc_key) or 0
        # KISS time management: clock/30 + increment. Movestogo by se hodilo
        # zahrnout (clock / movestogo místo /30), ale arena/play typicky neposílají.
        return max(1, clock // _TIME_FRACTION_PER_MOVE + inc)

    # depth/nodes/infinite/empty → engine rozhodne sám (typicky vrátí okamžitě).
    return None


def run_uci_loop(name: str, author: str, choose_move: ChooseMoveFn) -> None:
    """Kompletní UCI smyčka — čte stdin po řádcích dokud nepřijde 'quit' (nebo EOF).

    Engine modul to volá v `main()`:
        run_uci_loop(name="ChessLab Foo v0", author="...", choose_move=_choose_move)

    `choose_move` dostane aktuální board + time_ms budget (None = bez limitu)
    a vrátí jeden legální tah (nebo None, pokud žádný neexistuje — pak engine
    pošle 'bestmove 0000').

    Stav drží jen v lokální proměnné `board` (žádná persistence mezi spuštěními
    — každé `position` command ji přepíše). Single-threaded, synchronní.
    """
    board = chess.Board()

    # `for raw in sys.stdin` čte řádek po řádkem až do EOF (rodičovský proces
    # zavřel stdin) — čistší než `while True: input()`.
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        tokens = line.split()
        cmd = tokens[0]
        args = tokens[1:]

        if cmd == "uci":
            # Handshake: identifikace + uciok. Žádné UCI options nenabízíme.
            send_line(f"id name {name}")
            send_line(f"id author {author}")
            send_line("uciok")
        elif cmd == "isready":
            # Sync check — odpovíme okamžitě (vždy ready, žádný state init).
            send_line("readyok")
        elif cmd == "ucinewgame":
            # Reset boardu — arena obvykle hned posílá 'position startpos',
            # ale jistota neuškodí.
            board = chess.Board()
        elif cmd == "position":
            board = parse_position_command(args, board)
        elif cmd == "go":
            # Parsuj time budget (movetime / wtime / btime / fallback None).
            time_ms = parse_go_time(args, board.turn)
            move = choose_move(board, time_ms)
            if move is None:
                # Žádný legální tah → UCI 'null move' = '0000'. Defenzivní fallback,
                # arena by 'go' na finální pozici neměla poslat.
                send_line("bestmove 0000")
            else:
                send_line(f"bestmove {move.uci()}")
        elif cmd == "quit":
            # Čistý exit. Žádný cleanup netreba.
            return
        # Ostatní příkazy (stop, ponderhit, debug, setoption, register, ...)
        # tiše ignorujeme — minimální UCI spec pro hru ve známém prostředí.
