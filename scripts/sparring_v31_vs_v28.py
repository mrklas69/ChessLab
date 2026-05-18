"""Decisive sparring: ChessLab Minimax v3.1 (ID + TT + PV) vs v2.8 (fixed depth 2).

Spustí 100 H2H partií @ 0.10s/tah, alternuje barvy. Aktualizuje matchup matrix
(kumulační) a refitne Bayesian Elo z celé matrice.

Cíl: **decisive odpověď** na "umí ID v Pythonu vůbec gain (s TT + PV ordering)?"
v3.0 sparring 2026-05-18 ukázal že ID **bez TT/PV** nepřinese signif gain
(−24 a −28 Elo, CI překrývalo nulu). Pokud v3.1 také nesignif, ID chapter
v Pythonu uzavíráme a pivotujeme.

Výstup:
  - W/L/D z H2H + Wilson 95% CI
  - Aktualizovaný Bayesian Elo rating
  - 1-sided p-value pro H1: "v3.1 silnější než v2.8"

Template: 1:1 z sparring_v30_vs_v28.py.
"""

from __future__ import annotations

import math
import sys
import time

from chesslab.arena import EngineConfig
from chesslab.engines import list_available_engines, supports_skill_for_path
from chesslab.ratings import (
    add_match_results,
    engine_id_from_path,
    list_ratings,
    recompute_bayesian_ratings,
)
from chesslab.tournament import _run_match_between

N_GAMES = 100
TIME_PER_MOVE = 0.10


def find_engine(engine_id: str) -> str:
    """Najdi cestu k binárce daného engine_id."""
    for e in list_available_engines():
        if e.id == engine_id:
            return e.path
    raise RuntimeError(f"Engine '{engine_id}' nenalezen — proveď `uv sync`.")


def wilson_ci(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval pro proportion p̂ = wins/n."""
    if n == 0:
        return (0.0, 1.0)
    p_hat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2 * n)) / denom
    spread = z * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))


def score_to_elo(score: float) -> float:
    """Score (0-1) → Elo diff. Elo = -400 * log10((1-s)/s)."""
    if score <= 0:
        return float("-inf")
    if score >= 1:
        return float("inf")
    return -400.0 * math.log10((1 - score) / score)


def binomial_one_sided_p(wins_v31: int, n_decisive: int) -> float:
    """1-sided p-value pro H1: 'v3.1 silnější'. Pod H0 (50/50): P(wins ≥ k | n)."""
    if n_decisive == 0:
        return 1.0
    p = 0.0
    for k in range(wins_v31, n_decisive + 1):
        p += math.comb(n_decisive, k) * (0.5 ** n_decisive)
    return p


def main() -> int:
    print("=" * 70)
    print("SPARRING: ChessLab Minimax v3.1 (ID + TT + PV) vs v2.8 (fixed depth 2)")
    print(f"  {N_GAMES} partií, {TIME_PER_MOVE}s/tah, alternující barvy")
    print("=" * 70)
    print()

    v31_path = find_engine("chesslab-minimax")  # alias pro current = v3.1
    v28_path = find_engine("chesslab-minimax-v28")
    print(f"[engines]")
    print(f"  v3.1: {v31_path}")
    print(f"  v2.8: {v28_path}")
    print()

    engine_a = EngineConfig(path=v31_path, skill=0)  # A = v3.1
    engine_b = EngineConfig(path=v28_path, skill=0)  # B = v2.8

    eid_a = engine_id_from_path(v31_path, 0, supports_skill_for_path(v31_path))
    eid_b = engine_id_from_path(v28_path, 0, supports_skill_for_path(v28_path))
    print(f"[engine IDs] A={eid_a}, B={eid_b}")
    print()

    print(f"[sparring] Hraje se {N_GAMES} partií...")
    started = time.monotonic()
    games = _run_match_between(
        engine_a=engine_a,
        engine_b=engine_b,
        n_games=N_GAMES,
        time_per_move=TIME_PER_MOVE,
        pair_index=131,  # arbitrary; jen pro round number
    )
    elapsed = time.monotonic() - started
    print(f"[sparring] DONE za {elapsed:.1f}s ({elapsed/60:.1f} min, {elapsed/N_GAMES:.1f}s/partii)")
    print()

    wins_a = wins_b = draws = no_result = 0
    for g in games:
        if g.result == "1/2-1/2":
            draws += 1
        elif g.result == "1-0":
            if g.white_is == "a":
                wins_a += 1
            else:
                wins_b += 1
        elif g.result == "0-1":
            if g.white_is == "a":
                wins_b += 1
            else:
                wins_a += 1
        else:
            no_result += 1

    n_played = wins_a + wins_b + draws
    score_a = wins_a + 0.5 * draws
    score_pct = 100.0 * score_a / n_played if n_played else 0.0

    print("=" * 70)
    print(f"H2H RESULTS (v3.1 perspective)")
    print("=" * 70)
    print(f"  Wins v3.1:    {wins_a}")
    print(f"  Wins v2.8:    {wins_b}")
    print(f"  Draws:        {draws}")
    print(f"  No result:    {no_result} (MAX_PLIES)")
    print(f"  Total played: {n_played}")
    print(f"  v3.1 score:   {score_a:.1f}/{n_played} ({score_pct:.1f}%)")
    print()

    ci_low, ci_high = wilson_ci(score_a, n_played)
    print(f"  Wilson 95% CI:  [{ci_low*100:.1f}%, {ci_high*100:.1f}%]")
    print(f"  Elo point est:  {score_to_elo(score_a/n_played):+.0f} Elo")
    print(f"  Elo 95% CI:     [{score_to_elo(ci_low):+.0f}, {score_to_elo(ci_high):+.0f}] Elo")
    print()

    n_decisive = wins_a + wins_b
    p_value = binomial_one_sided_p(wins_a, n_decisive)
    print(f"  Decisive games: {n_decisive} (excluding draws)")
    print(f"  1-sided p-value (H1: v3.1 > v2.8): {p_value:.4f}")
    if p_value < 0.05:
        print(f"  -> STATISTICALLY SIGNIFICANT @ alpha=0.05 (v3.1 lepsi)")
    else:
        print(f"  -> not significant @ alpha=0.05")
    print()

    add_match_results(
        engine_a_id=eid_a,
        engine_b_id=eid_b,
        wins_a=wins_a,
        wins_b=wins_b,
        draws=draws,
    )
    print(f"[persist] add_match_results zapsal: A={wins_a}, B={wins_b}, draws={draws}")
    print()

    display_names = {
        eid_a: "ChessLab Minimax v3.1",
        eid_b: "ChessLab Minimax v2.8 (snapshot)",
    }
    recompute_bayesian_ratings(engine_display_names=display_names)
    print(f"[persist] Bayesian Elo refit z celé matchup matrice")
    print()

    print("=" * 70)
    print("FINAL RATINGS (Bayesian Bradley-Terry, celá DB)")
    print("=" * 70)
    for r in list_ratings():
        anchor = " [ANCHOR]" if r.is_anchor else ""
        print(f"  {r.rating:7.1f}  {r.display_name:<42s}  {r.games_played:>3d} games{anchor}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
