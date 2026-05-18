"""ChessLab Engine v3.0 (snapshot): Iterative Deepening (ID) + alpha-beta + endgame heuristika + quiescence (captures + checks) + MVV-LVA.

**Zamražený snapshot v3.0** — slouží jako baseline pro sparring proti novějším
verzím (v3.1+). Engine_id ``chesslab-minimax-v30`` v ratings DB drží historický
rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab minimax"
(postupně přepisovaný novou verzí, aktuálně v3.1 s TT + PV ordering).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v3.1 — viz git
historie commitu, který tento snapshot vytvořil.

---

**Klíčová změna oproti v2.8**: nahrazení **fixed depth 2** za **iterative
deepening** (ID) s **soft time check**. Search jde depth 1, 2, 3, ... do
dostupného time budgetu (`go movetime <ms>` z UCI). Když budget vyprší
uprostřed iterace, vrátíme best move z **poslední DOKONČENÉ** iterace.

**Proč ID a ne fixed depth 3?** Naivní `_DEPTH = 3` v Pythonu by v komplikovaných
middlegame pozicích trval 1-5 sekund per move — `chess.engine.play(Limit(time=0.10))`
nemá hard kill, takže klient by čekal indefinitely (= sparring partie minut
místo sekund). ID adaptivně: triviální pozice → depth 5-6, složitý middlegame
→ depth 2-3, vždy v rámci budgetu.

**Bonus ID property**: "always-have-an-answer" — i kdyby time vypršel během
depth 1, depth 1 výsledek (= jen materiál po protihráčově odpovědi) je lepší
než nic. Random fallback drží engine konzervativně.

**Implementační detaily**:

- `choose_move(board, time_ms)` — top-level ID loop. Sčítá nodes searched v
  `counter` (mutable list pro propagaci do recurse), kontroluje deadline každých
  `_NODES_PER_TIME_CHECK` nodes (1024) — `time.monotonic()` jednou za 1024 nodes
  je acceptable overhead (~1 % budget) místo per-node.
- `_SearchTimeout` exception — abort signal. Propaguje se nahoru přes
  negamax/quiescence rekurzi, top-level ji catchne a nepoužije half-finished
  iteraci.
- `_DEFAULT_TIME_MS = 50` — fallback když UCI klient pošle `go` bez time argů
  (= žádný deadline). Match s default arena/play 0.05s/tah.
- `_TIME_RESERVE_MS = 5` — rezerva odečtená od deadline. UCI klient mě time-outne
  pokud se `bestmove` opozdí o víc než pár ms; raději vrať tah o 5ms dřív.
- `_MAX_DEPTH = 8` — bezpečný cap, ID stejně nedoleze v Pythonu v rozumném
  budgetu (depth 5 v middlegame trvá ~30s). Bez capu by `for depth in count(1)`
  šel do nekonečna v triviálních pozicích (KvK = 0 nodes per depth).

**Vědomě vyloučeno z v3.0** (kandidáti na v3.1+):

- **Transposition table (TT)** — Zobrist hash + dict. ID by re-použila scores
  z předchozích iterací → ~30 % rychlejší = hlouběji v stejném budgetu. ~80
  řádků navíc, kandidát na v3.1.
- **PV move ordering** — best move z předchozí ID iterace zkusit PRVNÍ v další
  → α-β cutoff dřív. Bez TT komplikované (musíme si best_move per depth pamatovat
  manuálně), kandidát na v3.1 spolu s TT.
- **Aspiration windows** — α-β okno úzké kolem score z minulé iterace, re-search
  při miss. Zrychluje deep iterace, ale přidává komplexitu.
- **Killer moves / history heuristic** — non-capture ordering nad MVV-LVA.
- **Null move pruning** — heavy guns, riziko zonk bugu v zugzwang.
- **Mate distance scoring** — zatím vracíme ±MATE_SCORE flat, vidíme jen mate-in-1.

Vše ostatní (eval funkce, quiescence design, MVV-LVA, endgame heuristika)
**1:1 z v2.8** — viz minimax_engine_v28.py snapshot pro porovnání.

Random tie-break z best moves: stejně jako Greedy a v2.x. Plně deterministický
engine by způsobil triple-repetition draws v Aréně.
"""

from __future__ import annotations

import math
import random
import time

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v3.0 (snapshot)"
ENGINE_AUTHOR = "Jan Mrklas"

# === ID + TIME MANAGEMENT (v3.0) ============================================

# Hard cap na ID hloubku. ID by v Pythonu pod 1s nedolezla ani na 5, ale bez
# capu by `for depth in count(1)` šlo do nekonečna v triviálních pozicích
# (KvK = každý depth trvá nulu nodes). 8 = bezpečná pojistka.
_MAX_DEPTH = 8

# Mandatory depths — vždy dokončit BEZ time abortu, garantuje v3.0 ≥ v2.8
# baseline quality. Důvod: bez tohoto by ID v komplikovaných pozicích pod
# 100ms budgetem stíhalo jen depth 1 (= de facto Greedy v1) → engine masivně
# slabší než fixed-depth-2 baseline. Trade-off: pro extrémně malé budgety
# (~10-30ms) možná overrun klient timeout (movetime + 1000ms safety margin),
# ale klient margin je 10× větší než depth 2 search v Pythonu, takže OK.
# Phase 2 (depth 3+) běží s deadline a abort se aktivuje.
_MANDATORY_DEPTHS = 2

# Kolik nodes mezi time checky. time.monotonic() je drahý (~100ns-1µs na Windows),
# kontrola per node by zhltla 20 % budgetu. **64 nodes** je konzervativní —
# v patologické pozici (heavy quiescence ~1ms/node) by overrun byl max ~64ms,
# bezpečně pod klient `chess.engine.play()` timeoutem (movetime + 1000ms).
# Bit-wise mask `& 0x3F` je rychlejší než `% 64`.
#
# Předchozí hodnota 1024 občas overrun >1s v komplikovaných pozicích
# → asyncio.TimeoutError v klientu (= partie ztracena). 64 trade-off:
# víc time.monotonic() calls (~1500/sec při 5000 nodes/s) = ~2 % overhead,
# acceptable pro 16× lepší time discipline.
_NODES_PER_TIME_CHECK = 64
_TIME_CHECK_MASK = _NODES_PER_TIME_CHECK - 1  # = 0x3F

# Default time budget pokud UCI klient pošle `go` bez time argů. Match s arena
# default 0.05s = 50ms.
_DEFAULT_TIME_MS = 50

# Rezerva odečtená od deadline. Klient (arena/play) `chess.engine.SimpleEngine.play()`
# používá `asyncio.wait_for(..., movetime + 1000ms)` — máme 1s safety margin
# nad explicit movetime. _TIME_RESERVE_MS odečteme od deadline aby `bestmove`
# stihl dorazit před movetime expirou (UCI flush + arena pipe ~5ms). 10ms je
# bezpečný kompromis (= 90 % budget reálně pro search).
_TIME_RESERVE_MS = 10


class _SearchTimeout(Exception):
    """Abort signal pro ID — raised když deadline vypršel uvnitř negamax/quiescence.

    Pattern: top-level choose_move ji catchne, použije best_move z poslední
    dokončené ID iterace. Search infrastructure (negamax, quiescence) ji jen
    raises, nezachycuje.
    """


# === MATERIAL + EVAL KONSTANTY (1:1 z v2.8) =================================

# Material values v centipawnech — Kaufman piece values, identické s Greedy v1.
# DRY: rozdíl mezi v3.0 a Greedy/v2.x je *jen* search, ne eval.
_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Mat skóre = "preferuj nade vším" / "vyhni se nade vším". Vyšší než suma
# materiálu na šachovnici (~3940 cp). Symetrické ±. **Důležité pro early-exit
# v ID**: pokud iterace najde score >= MATE_SCORE - 100, dál nepokračujeme —
# našli jsme nucený mat, hlubší search ho nepřebije.
_MATE_SCORE = 100_000

# Šach bonus — tlačí soupeřova krále. Sémantika "for side to move": stm v šachu
# → eval -= CHECK_BONUS, negace v rodiči → soupeř (= dal šach) vidí +bonus.
_CHECK_BONUS = 30

# === ENDGAME HEURISTIKA (1:1 z v2.5/v2.8) ====================================

_ENDGAME_MATERIAL_THRESHOLD = 1300
_ENDGAME_MIN_ADVANTAGE = 100
_KING_EDGE_BONUS_PER_SQUARE = 12
_KING_PROXIMITY_BONUS_PER_SQUARE = 3

# === QUIESCENCE LIMITS (1:1 z v2.6/v2.8) =====================================

_QUIESCENCE_MAX_PLIES = 8
_QUIESCENCE_MAX_CHECK_PLIES = 2

# === MOVE ORDERING (1:1 z v2.7) ==============================================

_MVV_LVA_VICTIM_MULT = 10


# === EVAL FUNCTIONS (1:1 z v2.8) =============================================


def _material_balance(board: chess.Board, our_color: chess.Color) -> int:
    """Material diff (naše - soupeř) v centipawnech."""
    score = 0
    for piece in board.piece_map().values():
        value = _PIECE_VALUES[piece.piece_type]
        if piece.color == our_color:
            score += value
        else:
            score -= value
    return score


def _endgame_bonus(board: chess.Board) -> int:
    """Endgame king-tropism bonus z perspektivy strany na tahu (stm).

    Aktivuje se jen v koncovce (total non-pawn material ≤ threshold) **A** jen
    pokud má stm materiálovou převahu ≥ pawn. Tlačí soupeřova krále do rohu
    (edge distance) a přibližuje našeho krále k jeho (king opposition) —
    standardní mating pattern pro KR vs K / KQ vs K koncovky.
    """
    our_color = board.turn

    total_material = 0
    for piece in board.piece_map().values():
        if piece.piece_type in (chess.PAWN, chess.KING):
            continue
        total_material += _PIECE_VALUES[piece.piece_type]

    if total_material > _ENDGAME_MATERIAL_THRESHOLD:
        return 0

    if _material_balance(board, our_color) < _ENDGAME_MIN_ADVANTAGE:
        return 0

    our_king = board.king(our_color)
    their_king = board.king(not our_color)
    if our_king is None or their_king is None:
        return 0

    center_squares = (chess.D4, chess.D5, chess.E4, chess.E5)
    their_king_edge_dist = min(
        chess.square_distance(their_king, c) for c in center_squares
    )
    king_dist = chess.square_distance(our_king, their_king)

    edge_bonus = their_king_edge_dist * _KING_EDGE_BONUS_PER_SQUARE
    proximity_bonus = (8 - king_dist) * _KING_PROXIMITY_BONUS_PER_SQUARE

    return edge_bonus + proximity_bonus


def _evaluate_for_side_to_move(board: chess.Board) -> int:
    """Statická eval z perspektivy strany na tahu. Volá se v listech ID/quiescence.

    Návratové hodnoty:
      - is_checkmate → -MATE_SCORE (stm je v matu, nejhorší).
      - is_game_over (jiný terminál) → 0 (remíza, neutrální).
      - jinak → material balance ± CHECK_BONUS (-pokud jsme v šachu) + endgame bonus.
    """
    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0

    score = _material_balance(board, board.turn)
    if board.is_check():
        score -= _CHECK_BONUS
    score += _endgame_bonus(board)
    return score


# === MOVE ORDERING (1:1 z v2.7) ==============================================


def _mvv_lva_score(board: chess.Board, move: chess.Move) -> int:
    """MVV-LVA skóre pro capture move. Non-captures = 0 (jdou za captures při sortu).

    En passant edge case: `piece_at(to_square)` vrací None pro EP, victim
    forced na PAWN přes `is_en_passant`.
    """
    if board.is_en_passant(move):
        victim_value = _PIECE_VALUES[chess.PAWN]
    else:
        victim_piece = board.piece_at(move.to_square)
        if victim_piece is None:
            return 0
        victim_value = _PIECE_VALUES[victim_piece.piece_type]

    aggressor_piece = board.piece_at(move.from_square)
    aggressor_value = (
        _PIECE_VALUES[aggressor_piece.piece_type] if aggressor_piece else 0
    )
    return victim_value * _MVV_LVA_VICTIM_MULT - aggressor_value


def _order_moves(board: chess.Board, moves) -> list[chess.Move]:
    """Seřadí tahy sestupně dle MVV-LVA skóre. Stable sort zachová pořadí
    python-chess uvnitř stejných skóre.
    """
    return sorted(moves, key=lambda m: _mvv_lva_score(board, m), reverse=True)


# === SEARCH (negamax + quiescence) S TIME CHECK (v3.0) =======================
#
# Counter pro time check je propagovaný jako mutable list `[int]` — Python
# nemá out parameters, list je nejjednodušší way to share mutable int přes
# rekurzi. Bit-wise mask `& _TIME_CHECK_MASK` je rychlejší než `% N`, a `1024 - 1`
# = `0x3FF` = bit-test posledních 10 bitů (true každých 1024 nodes).


def _check_time(deadline: float, counter: list[int]) -> None:
    """Inkrementuje counter, každých N nodes ověří deadline. Raise _SearchTimeout.

    Volá se na začátku _negamax / _quiescence. KISS: pure side-effect funkce.

    `deadline` je absolute time.monotonic() timestamp v sekundách. 0.0 = no
    deadline (= choose_move bez time_ms → ID jede do _MAX_DEPTH).
    """
    counter[0] += 1
    # Bit-wise AND s 0x3FF (= 1023) → true každých 1024 nodes. Drasticky levnější
    # než modulo. `counter[0] & _TIME_CHECK_MASK == 0` je true každých 1024 nodes
    # od počátku (counter starts at 0 → first check on 1024).
    if deadline > 0 and (counter[0] & _TIME_CHECK_MASK) == 0:
        if time.monotonic() >= deadline:
            raise _SearchTimeout


def _quiescence(
    board: chess.Board,
    alpha: int,
    beta: int,
    deadline: float,
    counter: list[int],
    ply: int = 0,
) -> int:
    """Quiescence search — pokračuje za depth=0 jen v "neklidných" pozicích.

    Plně identická logika s v2.8 quiescence, jen rozšířená o `deadline + counter`
    propagaci pro time check. **Time check se uplatní jen v deeper plies** — pro
    krátké quiescence sekvence (1-3 plies) by overhead nestál za to.

    Viz minimax_engine_v28.py docstring pro detailní rationale (stand-pat,
    in-check exception, captures-only, hard depth cap, MVV-LVA ordering,
    non-capture checks v prvních N plies).
    """
    _check_time(deadline, counter)

    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0

    in_check = board.is_check()

    if ply >= _QUIESCENCE_MAX_PLIES:
        if in_check:
            return -_MATE_SCORE
        return _evaluate_for_side_to_move(board)

    if not in_check:
        stand_pat = _evaluate_for_side_to_move(board)
        if stand_pat >= beta:
            return beta
        if stand_pat > alpha:
            alpha = stand_pat

        # v2.8: captures + (v prvních N plies) non-capture checks. Dedup přes set.
        candidates: set[chess.Move] = set(board.generate_legal_captures())
        if ply < _QUIESCENCE_MAX_CHECK_PLIES:
            for m in board.legal_moves:
                if m not in candidates and board.gives_check(m):
                    candidates.add(m)
        moves = _order_moves(board, candidates)
    else:
        # In check — žádný stand-pat, všechny legal moves (escape from check).
        moves = _order_moves(board, board.legal_moves)

    for move in moves:
        board.push(move)
        try:
            score = -_quiescence(board, -beta, -alpha, deadline, counter, ply + 1)
        finally:
            board.pop()

        if score >= beta:
            return beta
        if score > alpha:
            alpha = score

    return alpha


def _negamax(
    board: chess.Board,
    depth: int,
    alpha: int,
    beta: int,
    deadline: float,
    counter: list[int],
) -> int:
    """Negamax search s alpha-beta pruning + time check. Vrací best score pro stm.

    Identická s v2.8 negamax až na `deadline + counter` propagaci. `_check_time`
    raise _SearchTimeout když deadline překročen — propaguje se nahoru přes
    rekurzi, top-level choose_move ji catchne a použije best_move z předchozí
    dokončené iterace.

    Push/pop pattern: chess.Board je mutable, push tah, rekurze, pop. `try/finally`
    zaručí pop i při _SearchTimeout — kritické, jinak by board zůstal v invalid
    stavu pro další ID iteraci.
    """
    _check_time(deadline, counter)

    if board.is_game_over():
        return _evaluate_for_side_to_move(board)
    if depth == 0:
        return _quiescence(board, alpha, beta, deadline, counter)

    best = -math.inf
    for move in _order_moves(board, board.legal_moves):
        board.push(move)
        try:
            score = -_negamax(board, depth - 1, -beta, -alpha, deadline, counter)
        finally:
            # finally garantuje pop i při _SearchTimeout — bez něj by next ID
            # iterace startovala s rozhozeným board state (pushed move zůstal).
            board.pop()

        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break

    return int(best)


# === TOP-LEVEL: Iterative Deepening (v3.0 hlavní novinka) ====================


def _run_id_iteration(
    board: chess.Board,
    legals: list[chess.Move],
    depth: int,
    deadline: float,
    counter: list[int],
) -> tuple[float, list[chess.Move]]:
    """Spustí jednu ID iteraci v dané hloubce. Vrátí (best_score, best_moves).

    Per-root-move time check (cheap, ~30 monotonic calls per iterace) jen pokud
    deadline > 0 — pro mandatory depths se passuje deadline=0.0 (= no abort).

    Root search bez sdílené alphy (freshly -inf/+inf okno per root move) — viz
    v2.8 docstring choose_move pro rationale.

    Raises:
        _SearchTimeout: pokud deadline překročen uprostřed iterace (jen pokud
            deadline > 0). Volající (choose_move) ji catchne a použije best
            z předchozí dokončené iterace.
    """
    iteration_best_score: float = -math.inf
    iteration_best_moves: list[chess.Move] = []

    for move in _order_moves(board, legals):
        # Per-root-move time check (jen pokud deadline aktivní = phase 2).
        # Defense in depth nad counter mechanism uvnitř _negamax.
        if deadline > 0 and time.monotonic() >= deadline:
            raise _SearchTimeout

        board.push(move)
        try:
            # `depth - 1` protože root push už reprezentuje 1 ply do hloubky.
            # Negamax convention: score z child perspective → negace pro naši.
            score = -_negamax(
                board, depth - 1, -math.inf, math.inf, deadline, counter
            )
        finally:
            board.pop()

        if score > iteration_best_score:
            iteration_best_score = score
            iteration_best_moves = [move]
        elif score == iteration_best_score:
            iteration_best_moves.append(move)

    return iteration_best_score, iteration_best_moves


def choose_move(board: chess.Board, time_ms: int | None = None) -> chess.Move | None:
    """Top-level ID search — depth 1, 2 mandatory; depth 3+ adaptive do budgetu.

    **Dvoufázový design** (klíčové!):

    - **Phase 1 (depth 1..MANDATORY_DEPTHS)**: VŽDY dokončit, **bez** time abortu.
      Garantuje v3.0 ≥ v2.8 baseline quality. Bez tohoto by v komplikovaných
      pozicích pod 100ms budgetem stíhalo jen depth 1 (= de facto Greedy v1)
      → engine masivně slabší než fixed-depth-2 baseline.

    - **Phase 2 (depth MANDATORY+1..MAX)**: ID s deadline, abort uprostřed
      iterace = použij best z předchozí dokončené iterace. Time budget se
      uplatní jen tady (tj. pokud zbude čas po mandatory phases).

    **Fallback chain pro deadline**:
      - `time_ms` explicit z UCI `go movetime` → použij to (- _TIME_RESERVE_MS).
      - `time_ms` None (např. `go` bez args, nebo `go depth N`) → `_DEFAULT_TIME_MS`
        = 50ms (match arena default 0.05s).

    **Trade-off s mandatory completion**: pro extrémně malé budgety (~10-30ms)
    může mandatory depth 2 overrun deadline. Klient `chess.engine.play()` má
    safety margin `movetime + 1000ms`, depth 2 v Pythonu trvá max ~200ms, takže
    bezpečné. Pro main usage (0.05-2.0s arena/play) phase 2 typicky doleze
    depth 3-4.

    **Early exit při matu**: pokud iterace najde score >= MATE_SCORE - 100,
    dál nepokračujeme — našli jsme nucený mat, hlubší search ho nepřebije.

    **Forced move shortcut**: pokud `len(legals) == 1`, ID nepotřebujeme — jen
    jediný legální tah, vrátíme ho okamžitě. Šetří search v zugzwang pozicích.

    Random tie-break: stejně jako v2.x. Deterministický engine = triple
    repetition v Aréně.
    """
    legals = list(board.legal_moves)
    if not legals:
        # Žádný legální tah → mat/pat. UCI 'bestmove 0000' (vyřeší `run_uci_loop`).
        return None
    if len(legals) == 1:
        # Forced move — search by byl waste. Časté v zwang pozicích, šachu, recapture.
        return legals[0]

    # Spočítej deadline pro Phase 2 (adaptive). Phase 1 (mandatory) deadline=0.
    # _TIME_RESERVE_MS odečteno aby `bestmove` dorazil k arenenu před movetime
    # expirou. Pokud budget záporný (extrémně malý input), max(1, ...).
    budget_ms = max(1, (time_ms if time_ms is not None else _DEFAULT_TIME_MS) - _TIME_RESERVE_MS)
    adaptive_deadline = time.monotonic() + budget_ms / 1000.0

    counter: list[int] = [0]

    # Fallback best_move — pokud i Phase 1 fail (nikdy by se nemělo stát).
    best_move: chess.Move = random.choice(legals)

    # === Phase 1: Mandatory depths (1..MANDATORY_DEPTHS) ===
    # deadline=0.0 vypne abort v _check_time → vždy dokončí. Garantuje
    # v3.0 ≥ v2.8 quality.
    for depth in range(1, _MANDATORY_DEPTHS + 1):
        score, moves = _run_id_iteration(
            board, legals, depth, deadline=0.0, counter=counter
        )
        best_move = random.choice(moves)
        if score >= _MATE_SCORE - 100:
            # Mat nalezen v mandatory phase → return immediately, hlubší
            # search by ho nepřebil.
            return best_move

    # === Phase 2: Adaptive depths (MANDATORY+1..MAX) ===
    # deadline aktivní → abort uprostřed iterace = best zůstane z mandatory.
    try:
        for depth in range(_MANDATORY_DEPTHS + 1, _MAX_DEPTH + 1):
            score, moves = _run_id_iteration(
                board, legals, depth, deadline=adaptive_deadline, counter=counter
            )
            # Iterace dokončena → komituj best (random tie-break).
            best_move = random.choice(moves)
            if score >= _MATE_SCORE - 100:
                break
    except _SearchTimeout:
        # Time vypršel uprostřed Phase 2 iterace → best_move má hodnotu
        # z poslední dokončené iterace (worst case = z mandatory phase).
        pass

    return best_move


def main() -> None:
    """Entry point pro `chesslab-minimax` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
