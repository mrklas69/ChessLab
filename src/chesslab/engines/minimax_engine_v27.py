"""ChessLab Engine v2.7 (snapshot): Minimax + alpha-beta + endgame heuristika + quiescence + MVV-LVA.

**Zamražený snapshot v2.7** — slouží jako baseline pro sparring proti novějším
verzím (v2.8+). Engine_id ``chesslab-minimax-v27`` v ratings DB drží
historický rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab
minimax" (postupně přepisovaný novou verzí).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v2.8 — viz git
historie commitu, který tento snapshot vytvořil. Při budoucích sparring testech
(v2.9 vs v2.8, atd.) přidáme analogický ``minimax_engine_v28.py`` snapshot.

Detailní rationale pro každou subkomponentu je v původním docstringu — kopíruje
ho. Nech ho být nezměněný, ať diff proti staré verzi je čistý.
---

Negamax framework s alpha-beta pruning. Hloubka 2 plies (vidíme náš tah +
soupeřovu odpověď), v listech **quiescence search** (pokračujeme jen v
"neklidných" pozicích = captures, dokud nedojdeme k quiet pozici), tahy
seřazené přes **MVV-LVA** (silné captures první → α-β cutoffs cuttne dřív).
Eval = material (Greedy v1 hodnoty) + check bonus + endgame king-tropism +
edge distance pro silnější stranu v koncovce.
"""

from __future__ import annotations

import math
import random

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v2.7 (snapshot)"
ENGINE_AUTHOR = "Jan Mrklas"

# Hloubka v plies (= půltahů). 2 = vidíme svůj tah + soupeřovu odpověď.
_DEPTH = 2

# Material values v centipawnech — Kaufman piece values, identické s Greedy v1.
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

# === ENDGAME HEURISTIKA (v2.5) ===
_ENDGAME_MATERIAL_THRESHOLD = 1300
_ENDGAME_MIN_ADVANTAGE = 100
_KING_EDGE_BONUS_PER_SQUARE = 12
_KING_PROXIMITY_BONUS_PER_SQUARE = 3

# === QUIESCENCE LIMITS (v2.6) ===
_QUIESCENCE_MAX_PLIES = 8

# === MOVE ORDERING (v2.7) ===
_MVV_LVA_VICTIM_MULT = 10


def _material_balance(board: chess.Board, our_color: chess.Color) -> int:
    score = 0
    for piece in board.piece_map().values():
        value = _PIECE_VALUES[piece.piece_type]
        if piece.color == our_color:
            score += value
        else:
            score -= value
    return score


def _endgame_bonus(board: chess.Board) -> int:
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
    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0
    score = _material_balance(board, board.turn)
    if board.is_check():
        score -= _CHECK_BONUS
    score += _endgame_bonus(board)
    return score


def _mvv_lva_score(board: chess.Board, move: chess.Move) -> int:
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
    return sorted(moves, key=lambda m: _mvv_lva_score(board, m), reverse=True)


def _quiescence(board: chess.Board, alpha: int, beta: int, ply: int = 0) -> int:
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
        moves = _order_moves(board, board.generate_legal_captures())
    else:
        moves = _order_moves(board, board.legal_moves)

    for move in moves:
        board.push(move)
        try:
            score = -_quiescence(board, -beta, -alpha, ply + 1)
        finally:
            board.pop()

        if score >= beta:
            return beta
        if score > alpha:
            alpha = score

    return alpha


def _negamax(board: chess.Board, depth: int, alpha: int, beta: int) -> int:
    if board.is_game_over():
        return _evaluate_for_side_to_move(board)
    if depth == 0:
        return _quiescence(board, alpha, beta)

    best = -math.inf
    for move in _order_moves(board, board.legal_moves):
        board.push(move)
        try:
            score = -_negamax(board, depth - 1, -beta, -alpha)
        finally:
            board.pop()

        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break

    return int(best)


def choose_move(board: chess.Board) -> chess.Move | None:
    legals = list(board.legal_moves)
    if not legals:
        return None

    scored: list[tuple[int, chess.Move]] = []
    for move in legals:
        board.push(move)
        try:
            # Root: full window (-inf, +inf) per move — exact score pro tie-break.
            score = -_negamax(board, _DEPTH - 1, -math.inf, math.inf)
        finally:
            board.pop()
        scored.append((score, move))

    best_score = max(s for s, _ in scored)
    best_moves = [m for s, m in scored if s == best_score]
    return random.choice(best_moves)


def main() -> None:
    """Entry point pro `chesslab-minimax-v27` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
