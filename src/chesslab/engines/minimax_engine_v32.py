"""ChessLab Engine v3.2 (snapshot): ID + TT + PV + Killer Moves + History Heuristic.

**Zamražený snapshot v3.2** — slouží jako baseline pro sparring proti novějším
verzím (v3.3+). Engine_id ``chesslab-minimax-v32`` v ratings DB drží historický
rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab minimax"
(postupně přepisovaný novou verzí, aktuálně v3.3 s PSQT tapered eval).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v3.3 — viz git
historie commitu, který tento snapshot vytvořil.

---

**Klíčová změna oproti v3.1**: přidány dva mechanismy pro **quiet move ordering**:

- **Killer moves**: per-ply tabulka `_killers[ply][0..1]` — dva killer sloty na
  ply (slot 0 = nejnovější, slot 1 = předchozí). Update na β-cutoff pro quiet
  (= non-capture) moves: `[1] = [0]; [0] = move` (s dedup check). Při ordering
  zkus killer moves PŘED ostatními quiet moves (po captures).
- **History heuristic**: agregát `_history[(color, from, to)] += depth²` na
  každý β-cutoff quiet move. Při ordering quiet moves: sort descending by history.
  Reprezentuje "tyhle from/to páry historicky způsobily cutoffs" — heuristika
  že silné quiet moves se opakují přes různé pozice (např. centralizing knight,
  attacking outpost).

**Cíl v3.2**: zlepšit ordering quiet moves nad MVV-LVA. PV/TT řeší prev best,
MVV-LVA captures, killer/history quiet moves. Víc β-cutoffů → deeper search
v stejném budgetu. Odhad +30-80 Elo nad v3.1 (Stockfish bez killer/history
ztratí ~50 Elo per literatura).

**Move ordering finální** (v `_order_moves_with_pv`):
  1. PV move (z TT) — pokud existuje
  2. Captures sorted MVV-LVA
  3. Killer moves (slot 0, slot 1) — pokud quiet a ne v captures
  4. Quiet moves (non-capture, non-killer) sorted descending by history score

**Plumbing oproti v3.1**:
- `_negamax` dostává nový param `ply` (start root = 0). Tracking "depth from
  root" — na rozdíl od `depth` (= remaining depth). Killer table indexed by
  ply.
- Killer/history update jen v β-cutoff větvi (`if alpha >= beta: ... break`)
  a jen pro quiet moves (capture moves jsou už MVV-LVA-orderované, killer/history
  tam nepomáhá).
- `_killers: list[list[chess.Move | None]]` preallocated size `_MAX_PLY=64`.
- `_history: dict[(bool, int, int), int]` — klíč `(color, from_sq, to_sq)`.
- Lifetime: **clear na začátku každého `choose_move`** (KISS, standard pro toy
  enginy). Per-search semantika, ID iterace ho sdílejí. Žádný stale data risk
  mezi tahy.

**Vědomě skipnuto v v3.2** (kandidáti na v3.3+):
- Aspiration windows
- Mate-distance scoring
- Null move pruning
- SEE pruning v quiescence
- NNUE eval / opening book

Vše ostatní (eval, quiescence, MVV-LVA, endgame, TT, PV ordering, time
management) **1:1 z v3.1** — viz minimax_engine_v31.py snapshot.

Random tie-break: stejně jako v2.x/v3.x.
"""

from __future__ import annotations

import math
import random
import time

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v3.2 (snapshot)"
ENGINE_AUTHOR = "Jan Mrklas"

# === ID + TIME MANAGEMENT (1:1 z v3.0/v3.1) ==================================

_MAX_DEPTH = 8
_MANDATORY_DEPTHS = 2
_NODES_PER_TIME_CHECK = 64
_TIME_CHECK_MASK = _NODES_PER_TIME_CHECK - 1  # = 0x3F
_DEFAULT_TIME_MS = 50
_TIME_RESERVE_MS = 10


class _SearchTimeout(Exception):
    """Abort signal pro ID — raised když deadline vypršel uvnitř negamax/quiescence."""


# === MATERIAL + EVAL KONSTANTY (1:1 z v2.8/v3.x) =============================

_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
_MATE_SCORE = 100_000
_CHECK_BONUS = 30

# === ENDGAME HEURISTIKA (1:1 z v2.5/v2.8/v3.x) ===============================

_ENDGAME_MATERIAL_THRESHOLD = 1300
_ENDGAME_MIN_ADVANTAGE = 100
_KING_EDGE_BONUS_PER_SQUARE = 12
_KING_PROXIMITY_BONUS_PER_SQUARE = 3

# === QUIESCENCE LIMITS (1:1 z v2.6/v2.8/v3.x) ================================

_QUIESCENCE_MAX_PLIES = 8
_QUIESCENCE_MAX_CHECK_PLIES = 2

# === MOVE ORDERING (1:1 z v2.7) ==============================================

_MVV_LVA_VICTIM_MULT = 10

# === TRANSPOSITION TABLE (1:1 z v3.1) ========================================

_TT_EXACT = 0
_TT_LOWER = 1
_TT_UPPER = 2
_TT_MAX_ENTRIES = 1_000_000
_TT: dict[tuple, tuple[int, int, int, chess.Move | None]] = {}


def _tt_maybe_clear() -> None:
    """Soft cap check — pokud TT překročil _TT_MAX_ENTRIES, wipe."""
    if len(_TT) > _TT_MAX_ENTRIES:
        _TT.clear()


# === KILLER MOVES + HISTORY HEURISTIC (v3.2 nové) ============================

# Max ply, který killer table umí indexovat. ID v Pythonu nedoleze ani depth 5
# v rozumném budgetu, takže ply > 64 = nemožné. Preallocate fixed list = rychlejší
# než dict lookup.
_MAX_PLY = 64

# Killer slots per ply — standardní volba 2. Bigger v praxi marginal (slot 0/1
# zachytává ~95 % užitečných quiet cutoffs per literatuře).
_KILLER_SLOTS = 2

# Killer table: list[list[Move | None]] of size [_MAX_PLY][_KILLER_SLOTS].
# Klíč = ply (depth from root). Hodnoty = quiet moves co způsobily β-cutoff.
# Preallocated jako nested list of None (rychlejší než dict z hash overhead).
_killers: list[list[chess.Move | None]] = [
    [None] * _KILLER_SLOTS for _ in range(_MAX_PLY)
]

# History table: dict[(color, from_sq, to_sq)] -> int.
# Color je nutný protože stejný from/to znamená jiný move pro bílého vs černého
# (rošáda, pawn pushy). Score += depth² na β-cutoff (depth² váhuje cutoffs
# v hlubším search výš — standardní formule).
_history: dict[tuple[bool, int, int], int] = {}


def _killers_history_clear() -> None:
    """Reset killer + history na začátku každého `choose_move`.

    Per-search lifetime — KISS, žádný cross-move stale data risk. ID iterace
    v rámci jednoho `choose_move` ho sdílí (= killer z depth 2 iter pomůže
    depth 3 iter).
    """
    for slots in _killers:
        slots[0] = None
        slots[1] = None
    _history.clear()


def _is_capture(board: chess.Board, move: chess.Move) -> bool:
    """True pokud move je capture (vč. en passant)."""
    return board.is_capture(move)


def _killer_store(ply: int, move: chess.Move) -> None:
    """Posun slotů: [1] = [0]; [0] = move. Dedup check (pokud move už v slotu 0,
    nepřepisuj — jinak by se ztratil slot 1 a oba sloty by byly stejné).

    Volá se z β-cutoff větve _negamax pro quiet moves.
    """
    if ply >= _MAX_PLY:
        return
    slots = _killers[ply]
    if slots[0] == move:
        return  # už je top killer, nic nedělej
    # Posun starého slot 0 do slot 1, nový move na slot 0.
    slots[1] = slots[0]
    slots[0] = move


def _history_bump(color: bool, move: chess.Move, depth: int) -> None:
    """Inkrement history score pro (color, from, to) o depth².

    Standardní formule — depth² váhuje deeper cutoffs víc. Pro depth 4 cutoff
    bonus = 16 (vs depth 2 = 4) → silnější signál že tenhle move je užitečný
    napříč pozicemi.
    """
    key = (color, move.from_square, move.to_square)
    _history[key] = _history.get(key, 0) + depth * depth


def _history_score(color: bool, move: chess.Move) -> int:
    """Lookup history score pro (color, from, to). 0 pokud nikdy nebyl cutoff."""
    return _history.get((color, move.from_square, move.to_square), 0)


# === EVAL FUNCTIONS (1:1 z v2.8/v3.x) ========================================


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
    """Endgame king-tropism bonus z perspektivy strany na tahu (stm)."""
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
    """Statická eval z perspektivy strany na tahu."""
    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0

    score = _material_balance(board, board.turn)
    if board.is_check():
        score -= _CHECK_BONUS
    score += _endgame_bonus(board)
    return score


# === MOVE ORDERING (v2.7 MVV-LVA + v3.1 PV + v3.2 killer/history) ============


def _mvv_lva_score(board: chess.Board, move: chess.Move) -> int:
    """MVV-LVA skóre pro capture move. Non-captures = 0."""
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
    """Seřadí tahy sestupně dle MVV-LVA skóre (jen captures, quiet = 0 → arbitrary).

    Používá se v quiescence (kde nemáme ply context pro killer/history).
    """
    return sorted(moves, key=lambda m: _mvv_lva_score(board, m), reverse=True)


def _order_moves_with_pv_killers_history(
    board: chess.Board,
    moves,
    pv_move: chess.Move | None,
    ply: int,
) -> list[chess.Move]:
    """Plné v3.2 ordering: PV → captures MVV-LVA → killers → quiets by history.

    **Algoritmus**:
      1. Materializuj moves do listu (potřebujeme víc průchodů).
      2. PV move first (pokud existuje a je legal v tomto kontextu).
      3. Rozděl zbytek na captures vs quiets.
      4. Captures sort descending by MVV-LVA.
      5. Killer moves (slot 0, slot 1): pokud jsou v quiets a ne v captures,
         dej je před quiet zbytek (deduplikace přes membership check).
      6. Quiety bez killers: sort descending by history score (0 default).
      7. Final: [PV] + captures + killers + quiet_rest.

    **Edge cases**:
      - `pv_move` not in moves (TT klíč collision unlikely, ale defensive) →
        fallback bez PV.
      - `ply >= _MAX_PLY` → killers neaccessible (preallocated jen do 64);
        skip killer phase.
      - Killer slot je None nebo už je v captures (capture/killer overlap při
        promoci): skip.

    Cost: O(n log n) sort + O(k) killer scan, k=2. Pro n≤35 v praxi.
    """
    moves_list = list(moves)
    if not moves_list:
        return []

    # PV move first (deduplikace).
    ordered: list[chess.Move] = []
    if pv_move is not None and pv_move in moves_list:
        ordered.append(pv_move)
        moves_list = [m for m in moves_list if m != pv_move]

    # Rozděl zbytek na captures vs quiets (single pass).
    captures: list[chess.Move] = []
    quiets: list[chess.Move] = []
    for m in moves_list:
        if _is_capture(board, m):
            captures.append(m)
        else:
            quiets.append(m)

    # Captures sort MVV-LVA descending.
    captures.sort(key=lambda m: _mvv_lva_score(board, m), reverse=True)
    ordered.extend(captures)

    # Killers: zkus slot 0 a slot 1 z _killers[ply], pokud jsou v quiets.
    killer_moves_used: list[chess.Move] = []
    if ply < _MAX_PLY:
        for slot in _killers[ply]:
            if slot is not None and slot in quiets and slot not in killer_moves_used:
                killer_moves_used.append(slot)
    ordered.extend(killer_moves_used)

    # Quiety bez killers, sort by history score.
    color = board.turn
    quiet_rest = [m for m in quiets if m not in killer_moves_used]
    quiet_rest.sort(key=lambda m: _history_score(color, m), reverse=True)
    ordered.extend(quiet_rest)

    return ordered


# === SEARCH (negamax + quiescence) S TIME CHECK + TT + KILLER/HISTORY ========


def _check_time(deadline: float, counter: list[int]) -> None:
    """Inkrementuje counter, každých N nodes ověří deadline. Raise _SearchTimeout."""
    counter[0] += 1
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
    """Quiescence search — bez TT a bez killer/history (volatile pozice, captures
    už MVV-LVA-orderované).

    1:1 z v3.1.
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

        candidates: set[chess.Move] = set(board.generate_legal_captures())
        if ply < _QUIESCENCE_MAX_CHECK_PLIES:
            for m in board.legal_moves:
                if m not in candidates and board.gives_check(m):
                    candidates.add(m)
        moves = _order_moves(board, candidates)
    else:
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
    ply: int,
) -> int:
    """Negamax search s alpha-beta + TT + PV + killer/history ordering + cutoff update.

    **v3.2 změna oproti v3.1**: dodán param `ply` (depth from root, start = 0).
    Killer table indexovaná ply. Na β-cutoff: pokud cutoff move je quiet,
    update killer + history.

    **TT flow** (stejné jako v3.1): probe → optional return, store na konci s flag.
    """
    _check_time(deadline, counter)

    if board.is_game_over():
        return _evaluate_for_side_to_move(board)

    # === TT PROBE ===
    tt_key = board._transposition_key()
    tt_entry = _TT.get(tt_key)
    tt_move: chess.Move | None = None
    if tt_entry is not None:
        tt_depth, tt_score, tt_flag, tt_move = tt_entry
        if tt_depth >= depth:
            if tt_flag == _TT_EXACT:
                return tt_score
            if tt_flag == _TT_LOWER and tt_score >= beta:
                return tt_score
            if tt_flag == _TT_UPPER and tt_score <= alpha:
                return tt_score

    if depth == 0:
        return _quiescence(board, alpha, beta, deadline, counter)

    alpha_orig = alpha
    best = -math.inf
    best_move: chess.Move | None = None

    # v3.2 plné ordering: PV → captures MVV-LVA → killers → quiets by history.
    ordered_moves = _order_moves_with_pv_killers_history(
        board, board.legal_moves, tt_move, ply
    )

    for move in ordered_moves:
        # Snapshot zda je quiet PŘED push (po push se pozice změní a `is_capture`
        # by referencoval novou board state). is_capture čte z aktuální pozice
        # (před tahem) což chceme.
        is_quiet = not _is_capture(board, move)

        board.push(move)
        try:
            score = -_negamax(
                board, depth - 1, -beta, -alpha, deadline, counter, ply + 1
            )
        finally:
            board.pop()

        if score > best:
            best = score
            best_move = move
        if best > alpha:
            alpha = best
        if alpha >= beta:
            # β-cutoff. Pokud move byl quiet, update killer + history.
            # Captures neukládáme — MVV-LVA ordering je už natolik dobrý že
            # killer/history by jen plnily tabulky bez gainu.
            if is_quiet:
                # Pre-push board.turn = stm v tomhle node (= barva co dělala move).
                # Po push se turn flipne, takže pop a re-check? Ne — po pop je
                # board zase v původním stavu, board.turn = stm.
                _killer_store(ply, move)
                _history_bump(board.turn, move, depth)
            break

    # === TT STORE ===
    best_int = int(best)
    if best_int <= alpha_orig:
        flag = _TT_UPPER
    elif best_int >= beta:
        flag = _TT_LOWER
    else:
        flag = _TT_EXACT
    _TT[tt_key] = (depth, best_int, flag, best_move)

    return best_int


# === TOP-LEVEL: ID + PV chaining + killer/history clear (v3.2) ===============


def _run_id_iteration(
    board: chess.Board,
    legals: list[chess.Move],
    depth: int,
    deadline: float,
    counter: list[int],
    prev_best_move: chess.Move | None = None,
) -> tuple[float, chess.Move | None, list[chess.Move]]:
    """Spustí jednu ID iteraci. Root = ply 0.

    **v3.2 změna**: passuje `ply=1` do `_negamax` (root push posune ply o 1).
    Root sám killer/history neaktualizuje — pouze child nodes.
    """
    iteration_best_score: float = -math.inf
    iteration_best_moves: list[chess.Move] = []

    # Root ordering: prev_best_move first, zbytek captures+killers+quiets.
    # Pro root pyly=0 — killer table na ply 0 je obvykle prázdná pokud je
    # první iter (depth 1), ale od iter 2 už může mít killers ze sub-search.
    ordered_legals = _order_moves_with_pv_killers_history(
        board, legals, prev_best_move, ply=0
    )

    for move in ordered_legals:
        if deadline > 0 and time.monotonic() >= deadline:
            raise _SearchTimeout

        board.push(move)
        try:
            # Root push → child node je ply 1.
            score = -_negamax(
                board, depth - 1, -math.inf, math.inf, deadline, counter, ply=1
            )
        finally:
            board.pop()

        if score > iteration_best_score:
            iteration_best_score = score
            iteration_best_moves = [move]
        elif score == iteration_best_score:
            iteration_best_moves.append(move)

    pv_move = iteration_best_moves[0] if iteration_best_moves else None
    return iteration_best_score, pv_move, iteration_best_moves


def choose_move(board: chess.Board, time_ms: int | None = None) -> chess.Move | None:
    """Top-level ID search — depth 1, 2 mandatory; depth 3+ adaptive.

    **v3.2 změna oproti v3.1**: na začátku reset killer + history tabulky
    (per-search lifetime). TT zůstává persistentní (jako v3.1).
    """
    legals = list(board.legal_moves)
    if not legals:
        return None
    if len(legals) == 1:
        return legals[0]

    # TT soft cap check (persistent) + killer/history reset (per-search).
    _tt_maybe_clear()
    _killers_history_clear()

    budget_ms = max(1, (time_ms if time_ms is not None else _DEFAULT_TIME_MS) - _TIME_RESERVE_MS)
    adaptive_deadline = time.monotonic() + budget_ms / 1000.0

    counter: list[int] = [0]

    best_move: chess.Move = random.choice(legals)
    pv_for_next: chess.Move | None = None

    # === Phase 1: Mandatory depths (1..MANDATORY_DEPTHS) ===
    for depth in range(1, _MANDATORY_DEPTHS + 1):
        score, pv_for_next, moves = _run_id_iteration(
            board, legals, depth,
            deadline=0.0, counter=counter,
            prev_best_move=pv_for_next,
        )
        best_move = random.choice(moves)
        if score >= _MATE_SCORE - 100:
            return best_move

    # === Phase 2: Adaptive depths (MANDATORY+1..MAX) ===
    try:
        for depth in range(_MANDATORY_DEPTHS + 1, _MAX_DEPTH + 1):
            score, pv_for_next, moves = _run_id_iteration(
                board, legals, depth,
                deadline=adaptive_deadline, counter=counter,
                prev_best_move=pv_for_next,
            )
            best_move = random.choice(moves)
            if score >= _MATE_SCORE - 100:
                break
    except _SearchTimeout:
        pass

    return best_move


def main() -> None:
    """Entry point pro `chesslab-minimax` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
