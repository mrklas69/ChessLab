"""Sdílená UCI smyčka pro vlastní ChessLab enginy.

Každý ChessLab engine = subprocess komunikující přes UCI protokol. Většina
boilerplate je všude stejná (handshake, parsing 'position', emisi 'bestmove')
— vlastní engine se liší jen v `choose_move(board) -> Move | None` funkci.

Tenhle modul poskytuje:
  - `send_line()` — write to stdout s okamžitým flushem (UCI je line-based,
    jinak by buffering blokoval handshake).
  - `parse_position_command()` — z `position startpos|fen ... [moves ...]`
    sestaví chess.Board.
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

# Typový alias pro engine callback — bere aktuální board, vrátí vybraný tah
# (nebo None pro pozici bez legálních tahů → engine pošle 'bestmove 0000').
ChooseMoveFn = Callable[[chess.Board], chess.Move | None]


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


def run_uci_loop(name: str, author: str, choose_move: ChooseMoveFn) -> None:
    """Kompletní UCI smyčka — čte stdin po řádcích dokud nepřijde 'quit' (nebo EOF).

    Engine modul to volá v `main()`:
        run_uci_loop(name="ChessLab Foo v0", author="...", choose_move=_choose_move)

    `choose_move` dostane aktuální board a vrátí jeden legální tah (nebo None,
    pokud žádný neexistuje — pak engine pošle 'bestmove 0000').

    Stav drží jen v lokální proměnné `board` (žádná persistence mezi spuštěními
    — každé `position` command ji přepíše). Single-threaded, synchronní.

    Všechny UCI parametry 'go' (depth, time, wtime, ...) ignorujeme — vlastní
    enginy budou typicky vracet tah okamžitě nebo si time managují sami.
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
            # Všechny parametry ignorujeme. Volat engine callback.
            move = choose_move(board)
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
