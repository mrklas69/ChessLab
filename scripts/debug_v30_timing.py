"""Debug: porovnání v3.0 a v2.8 na fixed pozicích s různými time budgety.

Cíl: vidět, jestli v3.0 v 100ms reálně stihne depth 2+, nebo abortuje v depth 1
(= de facto Greedy → bombardován v2.8).

Test:
  - 5 reprezentativních pozic (opening / Italian middlegame / KR vs K endgame / tactical / quiet middlegame)
  - 3 time budgety: 50ms, 100ms, 500ms
  - Per (engine, position, time): vypiš bestmove + elapsed wall time
"""

from __future__ import annotations

import time

import chess
import chess.engine

V30 = r"C:\Users\mrkla\source\ChessLab\.venv\Scripts\chesslab-minimax.exe"
V28 = r"C:\Users\mrkla\source\ChessLab\.venv\Scripts\chesslab-minimax-v28.exe"

POSITIONS = [
    ("startpos", chess.Board()),
    ("italian", chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")),
    ("KRvK_endgame", chess.Board("4k3/8/8/8/8/8/8/3RK3 w - - 0 1")),
    ("tactical_pin", chess.Board("r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 1")),
    ("middlegame_quiet", chess.Board("r2q1rk1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 1")),
]

TIMES_MS = [50, 100, 500]


def bench_engine(engine_path: str, label: str) -> None:
    print(f"=== {label} ({engine_path}) ===")
    for pos_name, board in POSITIONS:
        for t_ms in TIMES_MS:
            t0 = time.monotonic()
            with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
                try:
                    result = engine.play(board, chess.engine.Limit(time=t_ms / 1000.0))
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    move = result.move
                    print(f"  {pos_name:<20s} t={t_ms:>4d}ms  move={str(move):<8s}  elapsed={elapsed_ms:>6.1f}ms")
                except Exception as e:
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    print(f"  {pos_name:<20s} t={t_ms:>4d}ms  FAILED: {type(e).__name__}: {e}  elapsed={elapsed_ms:.0f}ms")
    print()


def main() -> int:
    bench_engine(V28, "v2.8 (fixed depth 2)")
    bench_engine(V30, "v3.0 (ID)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
