"""Decisive sparring turnaj v2.8 + reset ratings.

Spustí full reset DB (engine_ratings + engine_matchups), re-seed anchor,
pak round-robin turnaj 6 enginů × 10 partií per pair × 0.10s/tah = 150 partií.

Účel:
- Decisive Elo rating pro Minimax v2.8 (předchozí 2 sparringy à 20 partií dávaly
  protichůdné výsledky, CI se překrývaly s nulou).
- Šťourání všech enginů na čisté DB, anchorováno na Stockfish skill 5 = 1500.

Časový odhad: ~15-20 min (150 × ~40 tahů × 0.10s + spawn overhead).

Spuštění:
    uv run --directory "C:\\Users\\mrkla\\source\\ChessLab" python scripts/full_tournament_v28.py
"""

from __future__ import annotations

import sys
import time

from chesslab.arena import EngineConfig
from chesslab.db import connect, init_db
from chesslab.engines import list_available_engines
from chesslab.ratings import init_anchor
from chesslab.tournament import TournamentConfig, run_tournament


def reset_ratings_db() -> None:
    """Smazat engine_ratings + engine_matchups, znovu seednout anchor."""
    init_db()  # idempotentní, jistota že tabulky existují
    with connect() as conn:
        # Pořadí nezáleží — FK neexistují mezi těmito tabulkami.
        conn.execute("DELETE FROM engine_matchups")
        conn.execute("DELETE FROM engine_ratings")
    # Re-seed Stockfish skill 5 = 1500 anchor.
    init_anchor()
    print("[reset] engine_ratings + engine_matchups smazány, anchor re-seedován.")


def build_config() -> TournamentConfig:
    """Sestaví 6-engine roster pro decisive sparring.

    6 enginů × 10 partií per pair × 15 párů = 150 partií.
    """
    engines_avail = list_available_engines()
    by_id = {e.id: e for e in engines_avail}

    required_ids = [
        "chesslab-random",
        "chesslab-greedy",
        "chesslab-minimax-v27",
        "chesslab-minimax",
        "stockfish",  # použijeme 2× s různým skill levelem
    ]
    for eid in required_ids:
        if eid not in by_id:
            raise RuntimeError(
                f"Engine '{eid}' není dostupný. Spusť `uv sync` (ChessLab enginy) "
                f"nebo zkontroluj STOCKFISH_PATH."
            )

    sf_path = by_id["stockfish"].path

    return TournamentConfig(
        engines=[
            EngineConfig(path=by_id["chesslab-random"].path, skill=0),
            EngineConfig(path=by_id["chesslab-greedy"].path, skill=0),
            EngineConfig(path=by_id["chesslab-minimax-v27"].path, skill=0),
            EngineConfig(path=by_id["chesslab-minimax"].path, skill=0),
            EngineConfig(path=sf_path, skill=5),   # anchor (1500 fixed)
            EngineConfig(path=sf_path, skill=10),  # horní reference
        ],
        n_games_per_pair=10,
        time_per_move=0.10,
    )


def print_results(result) -> None:
    print()
    print("=" * 70)
    print(f"TOURNAMENT DONE  ({result.n_engines} enginů, {result.n_games_total} partií, "
          f"{result.time_per_move}s/tah, wall {result.total_time}s)")
    print("=" * 70)
    print()
    print("=== Final ratings (Bayesian Bradley-Terry) ===")
    for r in result.ratings:
        anchor = " [ANCHOR]" if r.is_anchor else ""
        print(f"  {r.rating:7.1f}  {r.display_name:<40s}  {r.games_played:>3d} games{anchor}")
    print()
    print("=== Per-pair matchups (this tournament) ===")
    for m in result.matchups:
        n = m.a_wins + m.b_wins + m.draws
        a_score = m.a_wins + 0.5 * m.draws
        score_pct = 100.0 * a_score / n if n else 0.0
        print(f"  {m.engine_a_id:<26s} vs {m.engine_b_id:<26s} "
              f"{m.a_wins:>2}-{m.b_wins:<2}={m.draws:<2}  "
              f"A={score_pct:5.1f}%")
    print()


def main() -> int:
    print("[start] Decisive sparring v2.8 turnaj — full DB reset + 150 partií.")
    print()

    # 1. Reset DB.
    reset_ratings_db()
    print()

    # 2. Sestav config.
    config = build_config()
    print(f"[config] {len(config.engines)} enginů, {config.n_games_per_pair} partií per pair, "
          f"{config.time_per_move}s/tah.")
    for i, e in enumerate(config.engines, 1):
        print(f"  {i}. {e.path}  (skill={e.skill})")
    n_pairs = len(config.engines) * (len(config.engines) - 1) // 2
    print(f"[config] {n_pairs} párů × {config.n_games_per_pair} = {n_pairs * config.n_games_per_pair} partií.")
    print()

    # 3. Pusť turnaj.
    started = time.monotonic()
    result = run_tournament(config)
    elapsed = time.monotonic() - started
    print(f"[done] Turnaj odehrán za {elapsed:.1f}s ({elapsed/60:.1f} min).")

    # 4. Print results.
    print_results(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
