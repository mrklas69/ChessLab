"""ChessLab Engine v3.3 (snapshot): ID + TT + PV + Killer/History + PSQT tapered eval (PeSTO).

**Zamražený snapshot v3.3** — slouží jako baseline pro sparring proti novějším
verzím (v3.4+). Engine_id ``chesslab-minimax-v33`` v ratings DB drží historický
rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab minimax"
(postupně přepisovaný novou verzí, aktuálně v3.4 s mobility eval).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v3.4 — viz git
historie commitu, který tento snapshot vytvořil.

---

**Klíčová změna oproti v3.2**: rozšířena evaluation function o **PSQT (Piece-Square
Tables) s tapered eval**:

- **PeSTO tabulky**: 12 tabulek (6 piece types × 2 phases = midgame/endgame),
  hodnoty převzaty z Ronald Friederich's "PeSTO's Evaluation Function" (veřejné,
  defacto standard pro toy enginy). Tabulky reprezentují positional bonus
  (centralization, king safety v MG, king activity v EG, pawn advancement, …)
  v centipawnech přidaných nad base material.
- **Phase computation**: lerp factor mezi MG a EG dle non-pawn-non-king material
  na desce. Phase weights `KNIGHT=1, BISHOP=1, ROOK=2, QUEEN=4`. Startovní
  pozice = phase 24 (max), čistá KvK koncovka = phase 0. Tapered blend:
  `score = (mg * phase + eg * (PHASE_MAX - phase)) / PHASE_MAX`.
- **Index lookup**: PeSTO tabulky jsou zapsané "top-down" (index 0 = a8, 63 = h1).
  Pro bílého `idx = chess.square_mirror(sq)` (flip rank), pro černého `idx = sq`
  přímo (symetrie z perspektivy vlastní barvy).

**Cíl v3.3**: PSQT je textbook HCE feature s odhadovaným ziskem +50-100 Elo
v solid enginech. V Pythonu / našem rozsahu očekáváme menší (eval pass je nyní
~2× drahší než pure material → search dosáhne nižší depth v daném budgetu).
Per [[feedback-killer-history-weak-in-python]] eval features jsou bigger win
než move ordering tweaks.

**Coexistence s `_endgame_bonus`** (king tropism z v2.5): záměrně **ponecháno**.
PeSTO eg king table favorizuje centrum (king activity), `_endgame_bonus` táhne
soupeřova krále k okraji + našeho do těsné blízkosti — částečný overlap, ale
zachycují různé aspekty. První iter neoptimalizovat předčasně, vyhodnotit po
sparringu.

**Plumbing oproti v3.2**:
- `_material_balance` → `_material_plus_psqt(board, our_color)`: single-pass
  iterace `piece_map`, akumuluje (mg_score, eg_score, phase) zároveň, na konci
  blend dle phase. Material je sčítaný do obou (mg i eg) protože base value
  je phase-invariantní.
- `_evaluate_for_side_to_move` volá `_material_plus_psqt` místo `_material_balance`.
- Žádné změny v search loopu / TT / killer/history. Pouze eval.

**Vědomě skipnuto v v3.3** (kandidáti dál):
- Mobility, king safety, pawn structure (additional HCE features)
- Odstranění `_endgame_bonus` (re-vyhodnotit po sparringu — možná duplicate s eg king PSQT)
- Aspiration windows / mate-distance / NMP
- NNUE eval / opening book

Vše ostatní (search, quiescence, MVV-LVA, endgame_bonus, TT, PV, killer/history,
time management) **1:1 z v3.2** — viz minimax_engine_v32.py snapshot.

Random tie-break: stejně jako v2.x/v3.x.
"""

from __future__ import annotations

import math
import random
import time

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v3.3 (snapshot)"
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

# === PSQT (PeSTO tapered eval) — v3.3 nové ===================================
#
# Tabulky převzaty z Ronald Friederich's "PeSTO's Evaluation Function"
# (Chess Programming Wiki, public domain). Defacto standard pro toy enginy.
# Hodnoty jsou v centipawnech, reprezentují POSITIONAL BONUS nad base material
# (NE materiál samotný — to drží `_PIECE_VALUES`).
#
# Konvence indexů: tabulka[0] = a8 (top-left z white perspective),
# tabulka[7] = h8, tabulka[56] = a1, tabulka[63] = h1. Tj. první řádek je
# osmá řada (kde běžně stojí bílá těžká figura na startu). Tento "top-down"
# zápis odpovídá normálnímu pohledu šachisty.
#
# Pro lookup:
#   - bílá figura na square S → idx = chess.square_mirror(S)  (flip rank: a1↔a8)
#   - černá figura na square S → idx = S  (přímý lookup; symetrie perspective)
#
# Verifikace symetrie: bílý pěšec na e7 (chess.E7=52) → idx = mirror(52)=12.
# Černý pěšec na e2 (chess.E2=12) → idx = 12. Stejný PSQT slot = stejný bonus
# (oba "před promocí z vlastního pohledu").

# Pawn — MG: vysoký push bonus na 7. řadě, mírné centralizovaní;
#        EG: extrémně vysoký push bonus (passed pawn = velká hodnota)
_PSQT_MG_PAWN = [
      0,   0,   0,   0,   0,   0,   0,   0,
     98, 134,  61,  95,  68, 126,  34, -11,
     -6,   7,  26,  31,  65,  56,  25, -20,
    -14,  13,   6,  21,  23,  12,  17, -23,
    -27,  -2,  -5,  12,  17,   6,  10, -25,
    -26,  -4,  -4, -10,   3,   3,  33, -12,
    -35,  -1, -20, -23, -15,  24,  38, -22,
      0,   0,   0,   0,   0,   0,   0,   0,
]
_PSQT_EG_PAWN = [
      0,   0,   0,   0,   0,   0,   0,   0,
    178, 173, 158, 134, 147, 132, 165, 187,
     94, 100,  85,  67,  56,  53,  82,  84,
     32,  24,  13,   5,  -2,   4,  17,  17,
     13,   9,  -3,  -7,  -7,  -8,   3,  -1,
      4,   7,  -6,   1,   0,  -5,  -1,  -8,
     13,   8,   8,  10,  13,   0,   2,  -7,
      0,   0,   0,   0,   0,   0,   0,   0,
]

# Knight — silně preferuje centrum (rim = bad), především v MG.
_PSQT_MG_KNIGHT = [
   -167, -89, -34, -49,  61, -97, -15,-107,
    -73, -41,  72,  36,  23,  62,   7, -17,
    -47,  60,  37,  65,  84, 129,  73,  44,
     -9,  17,  19,  53,  37,  69,  18,  22,
    -13,   4,  16,  13,  28,  19,  21,  -8,
    -23,  -9,  12,  10,  19,  17,  25, -16,
    -29, -53, -12,  -3,  -1,  18, -14, -19,
   -105, -21, -58, -33, -17, -28, -19, -23,
]
_PSQT_EG_KNIGHT = [
    -58, -38, -13, -28, -31, -27, -63, -99,
    -25,  -8, -25,  -2,  -9, -25, -24, -52,
    -24, -20,  10,   9,  -1,  -9, -19, -41,
    -17,   3,  22,  22,  22,  11,   8, -18,
    -18,  -6,  16,  25,  16,  17,   4, -18,
    -23,  -3,  -1,  15,  10,  -3, -20, -22,
    -42, -20, -10,  -5,  -2, -20, -23, -44,
    -29, -51, -23, -15, -22, -18, -50, -64,
]

# Bishop — long diagonals + mírné centrum.
_PSQT_MG_BISHOP = [
    -29,   4, -82, -37, -25, -42,   7,  -8,
    -26,  16, -18, -13,  30,  59,  18, -47,
    -16,  37,  43,  40,  35,  50,  37,  -2,
     -4,   5,  19,  50,  37,  37,   7,  -2,
     -6,  13,  13,  26,  34,  12,  10,   4,
      0,  15,  15,  15,  14,  27,  18,  10,
      4,  15,  16,   0,   7,  21,  33,   1,
    -33,  -3, -14, -21, -13, -12, -39, -21,
]
_PSQT_EG_BISHOP = [
    -14, -21, -11,  -8,  -7,  -9, -17, -24,
     -8,  -4,   7, -12,  -3, -13,  -4, -14,
      2,  -8,   0,  -1,  -2,   6,   0,   4,
     -3,   9,  12,   9,  14,  10,   3,   2,
     -6,   3,  13,  19,   7,  10,  -3,  -9,
    -12,  -3,   8,  10,  13,   3,  -7, -15,
    -14, -18,  -7,  -1,   4,  -9, -15, -27,
    -23,  -9, -23,  -5,  -9, -16,  -5, -17,
]

# Rook — silně preferuje 7. řadu (penetrace) + otevřené sloupce v centru.
_PSQT_MG_ROOK = [
     32,  42,  32,  51,  63,   9,  31,  43,
     27,  32,  58,  62,  80,  67,  26,  44,
     -5,  19,  26,  36,  17,  45,  61,  16,
    -24, -11,   7,  26,  24,  35,  -8, -20,
    -36, -26, -12,  -1,   9,  -7,   6, -23,
    -45, -25, -16, -17,   3,   0,  -5, -33,
    -44, -16, -20,  -9,  -1,  11,  -6, -71,
    -19, -13,   1,  17,  16,   7, -37, -26,
]
_PSQT_EG_ROOK = [
     13,  10,  18,  15,  12,  12,   8,   5,
     11,  13,  13,  11,  -3,   3,   8,   3,
      7,   7,   7,   5,   4,  -3,  -5,  -3,
      4,   3,  13,   1,   2,   1,  -1,   2,
      3,   5,   8,   4,  -5,  -6,  -8, -11,
     -4,   0,  -5,  -1,  -7, -12,  -8, -16,
     -6,  -6,   0,   2,  -9,  -9, -11,  -3,
     -9,   2,   3,  -1,  -5, -13,   4, -20,
]

# Queen — vyrovnaná, v MG nepenalizuje předčasný vývin extrémně.
_PSQT_MG_QUEEN = [
    -28,   0,  29,  12,  59,  44,  43,  45,
    -24, -39,  -5,   1, -16,  57,  28,  54,
    -13, -17,   7,   8,  29,  56,  47,  57,
    -27, -27, -16, -16,  -1,  17,  -2,   1,
     -9, -26,  -9, -10,  -2,  -4,   3,  -3,
    -14,   2, -11,  -2,  -5,   2,  14,   5,
    -35,  -8,  11,   2,   8,  15,  -3,   1,
     -1, -18,  -9,  10, -15, -25, -31, -50,
]
_PSQT_EG_QUEEN = [
     -9,  22,  22,  27,  27,  19,  10,  20,
    -17,  20,  32,  41,  58,  25,  30,   0,
    -20,   6,   9,  49,  47,  35,  19,   9,
      3,  22,  24,  45,  57,  40,  57,  36,
    -18,  28,  19,  47,  31,  34,  39,  23,
    -16, -27,  15,   6,   9,  17,  10,   5,
    -22, -23, -30, -16, -16, -23, -36, -32,
    -33, -28, -22, -43,  -5, -32, -20, -41,
]

# King — KLÍČOVÁ tapered tabulka. MG: silně preferuje roh (krytí za pěšci);
#        EG: centrum (king activity, support of passed pawns).
_PSQT_MG_KING = [
    -65,  23,  16, -15, -56, -34,   2,  13,
     29,  -1, -20,  -7,  -8,  -4, -38, -29,
     -9,  24,   2, -16, -20,   6,  22, -22,
    -17, -20, -12, -27, -30, -25, -14, -36,
    -49,  -1, -27, -39, -46, -44, -33, -51,
    -14, -14, -22, -46, -44, -30, -15, -27,
      1,   7,  -8, -64, -43, -16,   9,   8,
    -15,  36,  12, -54,   8, -28,  24,  14,
]
_PSQT_EG_KING = [
    -74, -35, -18, -18, -11,  15,   4, -17,
    -12,  17,  14,  17,  17,  38,  23,  11,
     10,  17,  23,  15,  20,  45,  44,  13,
     -8,  22,  24,  27,  26,  33,  26,   3,
    -18,  -4,  21,  24,  27,  23,   9, -11,
    -19,  -3,  11,  21,  23,  16,   7,  -9,
    -27, -11,   4,  13,  14,   4,  -5, -17,
    -53, -34, -21, -11, -28, -14, -24, -43,
]

# Lookupy podle piece_type. python-chess: PAWN=1, KNIGHT=2, BISHOP=3, ROOK=4,
# QUEEN=5, KING=6. Použijeme dict (jen 6 záznamů, hot path je `_material_plus_psqt`
# kde děláme dict lookup — Python dict O(1), srovnatelné s list lookupem pro
# malé n).
_PSQT_MG: dict[chess.PieceType, list[int]] = {
    chess.PAWN:   _PSQT_MG_PAWN,
    chess.KNIGHT: _PSQT_MG_KNIGHT,
    chess.BISHOP: _PSQT_MG_BISHOP,
    chess.ROOK:   _PSQT_MG_ROOK,
    chess.QUEEN:  _PSQT_MG_QUEEN,
    chess.KING:   _PSQT_MG_KING,
}
_PSQT_EG: dict[chess.PieceType, list[int]] = {
    chess.PAWN:   _PSQT_EG_PAWN,
    chess.KNIGHT: _PSQT_EG_KNIGHT,
    chess.BISHOP: _PSQT_EG_BISHOP,
    chess.ROOK:   _PSQT_EG_ROOK,
    chess.QUEEN:  _PSQT_EG_QUEEN,
    chess.KING:   _PSQT_EG_KING,
}

# Phase weights (PeSTO standard). Pěšci a králové neovlivňují phase — fáze hry
# se klasicky určuje podle "kolik těžkých figur ještě na desce je".
# Startovní pozice: 4*N (=4) + 4*B (=4) + 4*R*2 (=8) + 2*Q*4 (=8) = 24.
_PSQT_PHASE_WEIGHT: dict[chess.PieceType, int] = {
    chess.PAWN:   0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK:   2,
    chess.QUEEN:  4,
    chess.KING:   0,
}
_PSQT_PHASE_MAX = 24

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
    """Material diff (naše - soupeř) v centipawnech.

    Používá se v `_endgame_bonus` (decision: máme alespoň `_ENDGAME_MIN_ADVANTAGE`?).
    Pro hlavní eval se používá `_material_plus_psqt` (single-pass material + PSQT).
    """
    score = 0
    for piece in board.piece_map().values():
        value = _PIECE_VALUES[piece.piece_type]
        if piece.color == our_color:
            score += value
        else:
            score -= value
    return score


def _material_plus_psqt(board: chess.Board, our_color: chess.Color) -> int:
    """Material + PSQT tapered eval v jednom passu přes `piece_map`.

    **Logika**:
      1. Pro každou figuru na desce přičti k `mg` a `eg` její material value
         (base material je phase-invariantní, ale potřebujeme ho v obou
         akumulátorech pro finální blend).
      2. Přičti PSQT bonus z MG i EG tabulky (lookup index závisí na barvě:
         bílá flipne rank, černá přímý).
      3. Sleduj `phase` = sum non-pawn-non-king material weights. Startovní
         pozice = 24 (max), čistá KvK = 0.
      4. Final blend: `(mg * phase + eg * (PHASE_MAX - phase)) // PHASE_MAX`.

    Znaménko: `mg/eg` jsou akumulované z perspektivy `our_color` (naše figury
    se přičítají, soupeřovy odečítají), takže blend je rovnou skóre které
    chceme vrátit.

    **Edge cases**:
      - Pokud na desce zbyly jen pěšci + králové: `phase = 0`, pure eg eval.
      - Pokud někdo má promotion → 9 dam (theoretical max): `phase` může
        přerůst 24 (9 dam * 4 = 36). Clamp na `_PSQT_PHASE_MAX` aby blend
        zůstal sane (jinak by `mg * phase / 24` přerostlo a mg bonus by
        dominoval i v koncovce s materiálovou převahou).

    Cost: O(pieces) ~32 piece iterations, 4 dict lookupů + 2 list lookupů per
    iteration. V Pythonu ~2× drahší než pure `_material_balance` — search
    v daném budgetu dosáhne mírně nižší depth, ale eval kvalita to (snad)
    vyváží. Měřitelné v sparringu vs v3.2.
    """
    mg = 0
    eg = 0
    phase = 0
    # piece_map() vrací dict {square: Piece}. Iterace přes items je rychlejší
    # než lookup `board.piece_at(sq)` pro každý square v range(64).
    for sq, piece in board.piece_map().items():
        pt = piece.piece_type
        material = _PIECE_VALUES[pt]
        # Sign: +1 pokud figura naše, -1 pokud soupeřova. Eliminuje větvení
        # uvnitř loopu (klasický branch elimination trick).
        sign = 1 if piece.color == our_color else -1

        # Material — phase-invariantní (do obou akumulátorů stejně).
        mg += sign * material
        eg += sign * material

        # PSQT lookup. Bílá figura na S → PeSTO index = mirror(S) (flip rank).
        # Černá figura na S → přímý index (symetrie z perspektivy vlastní barvy).
        if piece.color == chess.WHITE:
            idx = chess.square_mirror(sq)
        else:
            idx = sq
        mg += sign * _PSQT_MG[pt][idx]
        eg += sign * _PSQT_EG[pt][idx]

        # Phase weight (pawns/kings = 0, takže promotion-safe pro pěšce).
        phase += _PSQT_PHASE_WEIGHT[pt]

    # Clamp phase pro případ multiple promotions (9 queens = phase 36 > 24).
    # Bez clampu by tapered blend dal `mg * 36 / 24 = 1.5 * mg` (mg score
    # by se nadhodnotil). Standardní safeguard.
    if phase > _PSQT_PHASE_MAX:
        phase = _PSQT_PHASE_MAX

    # Tapered blend: mg má váhu `phase / PHASE_MAX`, eg `(PHASE_MAX - phase) / PHASE_MAX`.
    # Integer divize na konci — ztráta přesnosti < 1 cp, nezavadná.
    return (mg * phase + eg * (_PSQT_PHASE_MAX - phase)) // _PSQT_PHASE_MAX


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
    """Statická eval z perspektivy strany na tahu.

    **v3.3 změna**: `_material_balance` → `_material_plus_psqt` (material + PSQT
    tapered v jednom passu). Zbytek (check bonus, endgame king-tropism) 1:1
    z v3.2.
    """
    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0

    score = _material_plus_psqt(board, board.turn)
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
