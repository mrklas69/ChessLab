"""Taktický correctness test pro ChessLab Minimax enginy (chytí TT cache bugy).

Cíl: ujistit se že TT/PV ordering neporušil minimax správnost. Spustí engine
na sadě známých taktických pozic s longer budget (500ms = bohatě na depth 3-4)
a assertne že vrátí očekávaný winning move.

Pokud TT score je špatně cached (např. bad flag handling), engine může minout
mate-in-N nebo materiálovou výhru.

Pozice jsou jednoduché (mate-in-1, jednoduchý capture) — nic, co by depth-3
minimax neměl vidět. Pokud engine mine cokoli z těchto → TT bug.

Použití:
    uv run python scripts/tactical_test.py [engine-id]

`engine-id` default = `chesslab-minimax` (aktuální hlavní engine). Pro otestování
snapshotu předej jeho ID, např.:
    uv run python scripts/tactical_test.py chesslab-minimax-v33

Cesta k binárce se resolvuje přes discovery aktivního venv (žádné hardcoded
cesty) — funguje na libovolném stroji po `uv sync`.
"""

from __future__ import annotations

import sys
import time

import chess
import chess.engine

from chesslab.engines import EngineInfo, list_available_engines

DEFAULT_ENGINE_ID = "chesslab-minimax"


# Sada pozic: (jméno, FEN, množina očekávaných UCI tahů, čas v ms).
TACTICAL_CASES = [
    # 1. Scholar's mate setup — Qxf7# je jediný winning move.
    (
        "scholars-mate",
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 3",
        {"f3f7"},
        500,
    ),
    # 2. Back-rank mate-in-1: bk g8 zablokovaný vlastními pěšci f7/g7/h7,
    # wr a1 jede na a8 (rank 8 attack, žádný blok/escape/capture).
    (
        "back-rank-mate-in-1",
        "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1",
        {"a1a8"},
        500,
    ),
    # 3. Free queen capture (KQ vs K endgame, ne KvK draw):
    # wk e1, wq c3, bk e8, bq c7. Qxc7 captures queen, žádný recapture
    # (bk e8 nedosáhne na c7). Pozice po capture = K+Q vs K = winning,
    # NE insufficient material draw.
    (
        "free-queen-capture",
        "4k3/2q5/8/8/8/2Q5/8/4K3 w - - 0 1",
        {"c3c7"},
        500,
    ),
]


def find_engine(engine_id: str) -> EngineInfo:
    """Najdi EngineInfo daného engine_id přes discovery aktivního venv."""
    for e in list_available_engines():
        if e.id == engine_id:
            return e
    raise SystemExit(f"Engine '{engine_id}' nenalezen — proveď `uv sync`.")


def run_case(engine_path: str, name: str, fen: str, expected_moves: set[str], time_ms: int) -> bool:
    """Spustí engine na pozici a ověří že vrátí jeden z expected_moves."""
    board = chess.Board(fen)
    print(f"[{name}]")
    print(f"  FEN: {fen}")
    print(f"  Expected one of: {expected_moves}")

    started = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        result = engine.play(board, chess.engine.Limit(time=time_ms / 1000.0))
    elapsed = (time.monotonic() - started) * 1000.0

    move = result.move
    move_uci = move.uci() if move else None
    ok = move_uci in expected_moves
    status = "OK" if ok else "FAIL"
    print(f"  Got: {move_uci} ({elapsed:.0f}ms) -> {status}")
    print()
    return ok


def main() -> int:
    # argv[1] = engine-id (volitelný), jinak aktuální hlavní engine.
    engine_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENGINE_ID
    engine = find_engine(engine_id)
    print(f"=== Tactical correctness test {engine.name} ===\n")

    all_ok = True
    for name, fen, expected, time_ms in TACTICAL_CASES:
        ok = run_case(engine.path, name, fen, expected, time_ms)
        if not ok:
            all_ok = False

    if all_ok:
        print(f"=== Všechny {len(TACTICAL_CASES)} taktické testy PROŠLY ===")
        return 0
    else:
        print(f"=== Některé testy FAILED — TT bug? Zkontroluj logy ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())
