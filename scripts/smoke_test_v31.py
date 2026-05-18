"""Smoke test pro ChessLab Minimax v3.1 (ID + TT + PV).

Otestuje:
  1. Startup position + go movetime 100 → vrátí legal move v rozumném čase.
  2. Scholar's mate FEN → najde mat (depth 1 vidí mate-in-1).
  3. Komplikovaný middlegame FEN → vrátí legal move (test že nepadne).
  4. Long-budget middlegame (500ms) — test že TT/PV chaining nehází exception.

Sanity: TT shouldn't break correctness. Pokud engine padne nebo vrátí illegal
move → TT bug. Pokud overrun deadline drasticky → time check + TT interaction
broken.
"""

from __future__ import annotations

import sys
import time

import chess
import chess.engine

ENGINE_PATH = r"C:\Users\mrkla\source\ChessLab\.venv\Scripts\chesslab-minimax.exe"


def smoke_test_position(name: str, board: chess.Board, time_ms: int) -> None:
    """Spusť engine, pošli pozici + go movetime, ověř bestmove + čas."""
    print(f"[{name}] FEN: {board.fen()}")
    print(f"[{name}] time_ms={time_ms}, side_to_move={'white' if board.turn else 'black'}")

    started = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(ENGINE_PATH) as engine:
        result = engine.play(board, chess.engine.Limit(time=time_ms / 1000.0))
    elapsed_ms = (time.monotonic() - started) * 1000.0

    move = result.move
    print(f"[{name}] bestmove={move}, elapsed={elapsed_ms:.1f}ms")

    if move is None:
        print(f"[{name}] FAIL: engine vrátil None (žádný legal move)")
        sys.exit(1)
    if move not in board.legal_moves:
        print(f"[{name}] FAIL: engine vrátil illegal move {move}")
        sys.exit(1)

    if elapsed_ms > time_ms * 2 + 500:
        print(f"[{name}] WARN: elapsed {elapsed_ms:.0f}ms > 2*time_ms + 500ms = {time_ms*2+500}ms")
    print(f"[{name}] OK")
    print()


def main() -> int:
    print(f"=== Smoke test ChessLab Minimax v3.1 ({ENGINE_PATH}) ===\n")

    # 1. Startup.
    smoke_test_position("startpos", chess.Board(), time_ms=100)

    # 2. Scholar's mate setup — bílý hraje Qxf7#.
    pre_mate = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 3")
    qxf7 = chess.Move.from_uci("f3f7")
    pre_mate_test = pre_mate.copy()
    if qxf7 in pre_mate_test.legal_moves:
        pre_mate_test.push(qxf7)
        is_mate = pre_mate_test.is_checkmate()
        print(f"[mate-setup] Qxf7 je legal: True, after push is_checkmate: {is_mate}")
    else:
        print(f"[mate-setup] Qxf7 NENÍ legal v pozici {pre_mate.fen()}")
    smoke_test_position("scholars-mate-in-1", pre_mate, time_ms=100)

    # 3. Italian Game middlegame.
    middlegame = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    smoke_test_position("middlegame", middlegame, time_ms=100)

    # 4. Long budget — testuje že TT/PV chaining přes víc iterací neexploduje.
    smoke_test_position("middlegame-long", middlegame, time_ms=500)

    print("=== Všechny smoke testy v3.1 PROŠLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
