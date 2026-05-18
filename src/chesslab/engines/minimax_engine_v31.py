"""ChessLab Engine v3.1 (snapshot): Iterative Deepening + Transposition Table + PV move ordering.

**Zamražený snapshot v3.1** — slouží jako baseline pro sparring proti novějším
verzím (v3.2+). Engine_id ``chesslab-minimax-v31`` v ratings DB drží historický
rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab minimax"
(postupně přepisovaný novou verzí, aktuálně v3.2 s killer moves + history
heuristic).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v3.2 — viz git
historie commitu, který tento snapshot vytvořil.

---

**Klíčová změna oproti v3.0**: přidána **transposition table (TT)** + **PV move
ordering**. TT cachuje výsledky search per pozice (klíč = `board._transposition_key()`),
ID-root chainuje best move z předchozí iterace → next iter ho zkusí PRVNÍ
(α-β cutoff dřív). Intra-search PV: každý _negamax node po TT probe použije
`tt_move` jako first candidate.

**Cíl v3.1 (decisive)**: zodpovědět "umí ID v Pythonu vůbec gain?". v3.0 sparring
2026-05-18 ukázal že ID **bez TT/PV** v Pythonu nepřinese měřitelný gain (overhead
~9 % eats depth 3 advantage). Pokud TT+PV nezvedne Elo nad v2.8, ID chapter
v Pythonu uzavíráme a pivotujeme (NNUE / opening book / lepší eval).

**TT design choices**:

- **Klíč**: `board._transposition_key()` — private python-chess API, ale stable
  (používá ho `is_repetition()`). Vrací immutable tuple = exact board state
  (piece bitboardy + castling rights + ep square + turn). **Kolize-free** (= je
  to celý state, ne hash). Cena ~200ns per probe (vs zobrist_hash ~1-2µs).
- **Entry layout**: tuple `(depth, score, flag, best_move)`. Flag ∈
  {EXACT=0, LOWER=1, UPPER=2} — viz Negamax/AlphaBeta literatura (Stockfish,
  CPW). Tuple > dataclass kvůli rychlosti (žádný attribute lookup).
- **Replacement**: always-replace (KISS). Stejný klíč přepíše. Real engines
  použivají depth-preferred bucket nebo two-tier, pro náš scale (~5k-50k nodes
  per search) overkill.
- **Lifetime**: modulový `_TT`, persistentní přes celou hru (mezi-tahový reuse
  je významná část zisku — soupeřova odpověď často už ve stromu). Soft cap
  ``_TT_MAX_ENTRIES`` = 1M; při překročení wipe (start fresh, ne LRU eviction
  — Python dict nemá efficient LRU). Pro single-game lab use case bezpečné.
- **Quiescence bez TT**: standardní volba (volatile pozice, dominovaly by TT).

**PV move ordering**:

- **Intra-search** (v `_negamax`): TT probe vrátí `tt_move`, dej ho first
  v ordering. Pokud probe miss, fallback na MVV-LVA jako v v3.0.
- **ID-root** (v `_run_id_iteration`): `prev_best_move` z minulé iterace
  předaný explicitně. Root není v TT lookupován (mohli bychom, ale explicit
  param je čistější — root nemá α/β kontext stejný jako uvnitř).

**Vědomě skipnuto v v3.1** (a proč):

- **Mate-distance scoring**: `_MATE_SCORE` je flat (vidíme jen mate-in-1). TT
  store/probe normalizace na ply-from-root není potřeba. Kandidát na v3.2.
- **Repetition contamination edge case**: `_transposition_key()` neobsahuje
  move history → dvě cesty do stejné pozice (jedna = draw by repetition,
  druhá ne) sdílí klíč. Riziko: vzácný incorrect score. Pro 2-4 ply search
  marginal, dokumentováno.
- **Killer moves / history heuristic**: non-capture ordering nad MVV-LVA.
  Kandidát na v3.2.
- **Aspiration windows**: úzké α-β okno kolem prev score, re-search při miss.
  Kandidát na v3.2.

Vše ostatní (eval, quiescence, MVV-LVA, endgame, time management) **1:1
z v3.0** — viz minimax_engine_v30.py snapshot pro porovnání.

Random tie-break: stejně jako v2.x/v3.0.
"""

from __future__ import annotations

import math
import random
import time

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v3.1 (snapshot)"
ENGINE_AUTHOR = "Jan Mrklas"

# === ID + TIME MANAGEMENT (1:1 z v3.0) =======================================

_MAX_DEPTH = 8
_MANDATORY_DEPTHS = 2
_NODES_PER_TIME_CHECK = 64
_TIME_CHECK_MASK = _NODES_PER_TIME_CHECK - 1  # = 0x3F
_DEFAULT_TIME_MS = 50
_TIME_RESERVE_MS = 10


class _SearchTimeout(Exception):
    """Abort signal pro ID — raised když deadline vypršel uvnitř negamax/quiescence."""


# === MATERIAL + EVAL KONSTANTY (1:1 z v2.8/v3.0) =============================

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

# === ENDGAME HEURISTIKA (1:1 z v2.5/v2.8/v3.0) ===============================

_ENDGAME_MATERIAL_THRESHOLD = 1300
_ENDGAME_MIN_ADVANTAGE = 100
_KING_EDGE_BONUS_PER_SQUARE = 12
_KING_PROXIMITY_BONUS_PER_SQUARE = 3

# === QUIESCENCE LIMITS (1:1 z v2.6/v2.8/v3.0) ================================

_QUIESCENCE_MAX_PLIES = 8
_QUIESCENCE_MAX_CHECK_PLIES = 2

# === MOVE ORDERING (1:1 z v2.7) ==============================================

_MVV_LVA_VICTIM_MULT = 10

# === TRANSPOSITION TABLE (v3.1 nové) =========================================

# Flag = jak interpretovat uložené score vůči současnému α/β oknu.
#  - EXACT: score je přesný minimax výsledek (žádný α-β cut nastal).
#  - LOWER: β-cutoff nastal → skutečné score ≥ stored (lower bound).
#  - UPPER: žádný move nepřekonal α (fail-low) → skutečné score ≤ stored (upper bound).
# Tj. LOWER score je použitelný jen pokud beat současné β; UPPER jen pokud
# pod-/rovno současné α.
_TT_EXACT = 0
_TT_LOWER = 1
_TT_UPPER = 2

# Soft cap na velikost TT (entries). Při překročení full wipe (KISS — Python
# dict nemá rychlý LRU evict). 1M entries × ~200B per entry (tuple +
# transposition_key tuple) ≈ 200 MB worst case, akceptovatelné pro lab use.
# V praxi se nepřekročí — naše searches mají ~5k-50k unique pozic per search.
_TT_MAX_ENTRIES = 1_000_000

# Modulový TT dict — persistuje přes celou hru (mezi-tahový reuse je
# významná část zisku). Klíč = board._transposition_key() (immutable tuple,
# kolize-free). Hodnota = tuple(depth, score, flag, best_move).
#
# **Single-engine assumption**: TT je globální. Pokud běží dva enginy ve stejném
# procesu (test scenario), sdílejí TT — non-issue v UCI subprocess setupu
# (každý engine = vlastní proces = vlastní _TT).
_TT: dict[tuple, tuple[int, int, int, chess.Move | None]] = {}


def _tt_maybe_clear() -> None:
    """Soft cap check — pokud TT překročil _TT_MAX_ENTRIES, wipe.

    KISS varianta LRU/aging. Volá se na začátku choose_move (= raz za tah).
    Wipe ztratí veškerou cache, ale next search ji naplní zpět (~100ms na
    100k entries v Python dict).
    """
    if len(_TT) > _TT_MAX_ENTRIES:
        _TT.clear()


# === EVAL FUNCTIONS (1:1 z v2.8/v3.0) ========================================


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


# === MOVE ORDERING (1:1 z v2.7 + PV extension v3.1) ==========================


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
    """Seřadí tahy sestupně dle MVV-LVA skóre."""
    return sorted(moves, key=lambda m: _mvv_lva_score(board, m), reverse=True)


def _order_moves_with_pv(
    board: chess.Board, moves, pv_move: chess.Move | None
) -> list[chess.Move]:
    """Seřadí tahy s PV move first, zbytek MVV-LVA.

    Pokud `pv_move` je None nebo není v `moves` (TT entry z jiného move kontextu
    — nemělo by nastat při správně-stavěném klíči, ale defenzivně checkneme),
    fallback na pure MVV-LVA. Linear scan `pv_move in moves` je O(n), n ≤ ~35,
    negligible.
    """
    if pv_move is None:
        return _order_moves(board, moves)

    # `moves` může být generator (board.legal_moves) — materializujeme do listu
    # protože potřebujeme dva průchody (membership check + sort rest).
    moves_list = list(moves)
    if pv_move not in moves_list:
        # PV move není legal v této pozici — TT klíč collision unlikely (klíč
        # je full state), ale defenzivně. Fallback na MVV-LVA.
        return _order_moves(board, moves_list)

    rest = [m for m in moves_list if m != pv_move]
    sorted_rest = sorted(rest, key=lambda m: _mvv_lva_score(board, m), reverse=True)
    return [pv_move] + sorted_rest


# === SEARCH (negamax + quiescence) S TIME CHECK + TT (v3.1) ==================


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
    """Quiescence search — bez TT (volatile pozice, dominovaly by cache).

    1:1 z v3.0/v2.8.
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
) -> int:
    """Negamax search s alpha-beta pruning + TT probe/store + PV ordering.

    **TT flow**:
      1. Probe ``_TT`` na začátku. Hit + `entry.depth >= depth` + flag compatible
         s α/β oknem → return cached score (= cutoff).
      2. Hit (i kdyby depth nestačil) → použij `entry.best_move` jako PV pro
         move ordering.
      3. Po dokončení search → store entry s flag dle vztahu best score k α/β:
         - `best <= alpha_orig` → UPPER (žádný move nepřekonal α = fail-low)
         - `best >= beta` → LOWER (β-cutoff = ostatní moves netřeba)
         - jinak → EXACT (přesný minimax v okně)

    **alpha_orig**: snapshot α před iterací — potřebujeme původní α (ne updated
    po move loop) pro fail-low detekci. Bez tohoto by store flag byl chybný
    a budoucí probe by vrátil wrong cached score.
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
            # Score je dost hluboko spočítán — můžeme ho použít pokud flag fits.
            if tt_flag == _TT_EXACT:
                return tt_score
            if tt_flag == _TT_LOWER and tt_score >= beta:
                # Lower bound ≥ beta → β-cutoff, ostatní moves netřeba.
                return tt_score
            if tt_flag == _TT_UPPER and tt_score <= alpha:
                # Upper bound ≤ alpha → fail-low, nepřebije současný alpha.
                return tt_score
        # Pokud score nelze použít (mělčí depth nebo flag nefits), aspoň
        # `tt_move` zůstává validní pro PV ordering.

    if depth == 0:
        # Quiescence nepoužívá TT — viz docstring v3.1.
        return _quiescence(board, alpha, beta, deadline, counter)

    alpha_orig = alpha  # snapshot pro fail-low / EXACT detekci při store
    best = -math.inf
    best_move: chess.Move | None = None

    # PV ordering: tt_move first, zbytek MVV-LVA.
    for move in _order_moves_with_pv(board, board.legal_moves, tt_move):
        board.push(move)
        try:
            score = -_negamax(board, depth - 1, -beta, -alpha, deadline, counter)
        finally:
            # finally garantuje pop i při _SearchTimeout (board nesmí zůstat
            # v inconsistent state).
            board.pop()

        if score > best:
            best = score
            best_move = move
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break  # β-cutoff

    # === TT STORE ===
    # Flag decision: jakou hranici tohle score reprezentuje?
    best_int = int(best)
    if best_int <= alpha_orig:
        flag = _TT_UPPER  # fail-low: žádný move nepřebil původní α
    elif best_int >= beta:
        flag = _TT_LOWER  # fail-high: β-cutoff, score je lower bound
    else:
        flag = _TT_EXACT  # actual minimax value v okně
    _TT[tt_key] = (depth, best_int, flag, best_move)

    return best_int


# === TOP-LEVEL: Iterative Deepening + ID-root PV chaining (v3.1) =============


def _run_id_iteration(
    board: chess.Board,
    legals: list[chess.Move],
    depth: int,
    deadline: float,
    counter: list[int],
    prev_best_move: chess.Move | None = None,
) -> tuple[float, chess.Move | None, list[chess.Move]]:
    """Spustí jednu ID iteraci v dané hloubce. Vrátí (best_score, best_move_for_pv, best_moves_tieset).

    **PV chaining (v3.1 nové)**: `prev_best_move` z minulé ID iterace je první
    ve sortu legals. Když α-β alpha startuje z prvního move (= prev best, často
    silný), zbytek je likely fail-low → rychlé cutoffy. To je klíčový speedup
    pro deeper iterace.

    Per-root-move time check jen pokud deadline > 0 (mandatory phase passuje
    deadline=0 = no abort).

    Root search bez sdílené alphy (freshly -inf/+inf okno per root move) —
    zachováno z v2.8/v3.0 protože ID-root chce přesné score per move (ne jen
    bound) pro tie-break.

    Returns:
        (best_score, best_move_for_next_iter_pv, best_moves_tieset)
        - best_move_for_next_iter_pv: deterministicky první z tie setu (předáme
          do next iter jako PV). Pro UCI emit používáme `random.choice(tieset)`
          v choose_move pro tie-break — ID-PV chaining naopak chce stabilní volbu
          (jinak by se PV "houpalo" mezi iteracemi a ztrácel by efekt).
    """
    iteration_best_score: float = -math.inf
    iteration_best_moves: list[chess.Move] = []

    # PV chaining: prev best první, zbytek MVV-LVA.
    ordered_legals = _order_moves_with_pv(board, legals, prev_best_move)

    for move in ordered_legals:
        if deadline > 0 and time.monotonic() >= deadline:
            raise _SearchTimeout

        board.push(move)
        try:
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

    # PV move = první z tie setu (stabilní), pro next iter ordering.
    pv_move = iteration_best_moves[0] if iteration_best_moves else None
    return iteration_best_score, pv_move, iteration_best_moves


def choose_move(board: chess.Board, time_ms: int | None = None) -> chess.Move | None:
    """Top-level ID search — depth 1, 2 mandatory; depth 3+ adaptive; TT + PV chain.

    **Dvoufázový design** (1:1 z v3.0):
      - Phase 1 (depth 1..MANDATORY): VŽDY dokončit, bez time abortu.
      - Phase 2 (depth MANDATORY+1..MAX): adaptive, abort uprostřed iterace = best
        z předchozí dokončené.

    **v3.1 nové**: TT soft-cap check + PV chaining mezi iteracemi. Po každé
    dokončené iteraci si pamatujeme `pv_for_next` (stabilní = první z tie setu),
    příští iter ho předá do `_run_id_iteration` jako `prev_best_move`.
    """
    legals = list(board.legal_moves)
    if not legals:
        return None
    if len(legals) == 1:
        return legals[0]

    # TT soft cap check — raz za tah.
    _tt_maybe_clear()

    budget_ms = max(1, (time_ms if time_ms is not None else _DEFAULT_TIME_MS) - _TIME_RESERVE_MS)
    adaptive_deadline = time.monotonic() + budget_ms / 1000.0

    counter: list[int] = [0]

    # Fallback best_move pokud i Phase 1 by failnula (defensive).
    best_move: chess.Move = random.choice(legals)
    # PV move pro chaining mezi iteracemi (None na začátku — depth 1 nemá co PV).
    pv_for_next: chess.Move | None = None

    # === Phase 1: Mandatory depths (1..MANDATORY_DEPTHS) ===
    for depth in range(1, _MANDATORY_DEPTHS + 1):
        score, pv_for_next, moves = _run_id_iteration(
            board, legals, depth,
            deadline=0.0, counter=counter,
            prev_best_move=pv_for_next,
        )
        # Random tie-break pro UCI emit (zabraňuje deterministic repetition v Aréně).
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
        # Time vypršel v Phase 2 → best_move zůstává z poslední dokončené iter
        # (worst case = z mandatory phase).
        pass

    return best_move


def main() -> None:
    """Entry point pro `chesslab-minimax` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
