"""Round-robin engine turnaj + Bayesian Elo refit.

Spustí N enginů proti všem ostatním (N*(N-1)/2 párů) × n_games_per_pair partií
(alternuje barvy), aktualizuje `engine_matchups` matrix, pak refitne **všechny**
ratingy přes Bradley-Terry MLE (Bayesian Elo) z celé matrice.

**Cena**: pro N enginů × P partií per pair: P × N(N-1)/2 partií × ~3s/partii.
4 enginy × 10 partií = 60 partií × ~3s ≈ 3 min. 5 enginů × 10 = 100 × ~3s ≈ 5 min.
Hard limit max_engines + max_n_games_per_pair drží request budget pod ~10 min.

**Sériový batch, blokující endpoint** — pro dlouhé runy hrozí klientský timeout
(httpx default 5s, frontend by měl posílat timeout=600+). Async background job
+ polling je IDEAS task pro budoucnost.

**Recompute z celé matrice**: po tomto turnaji se Bayesian refit aplikuje na
**všechny matchupy v DB** (i z předchozích arén / turnajů), ne jen na nové.
To je správně — MLE odhad ratingů z víc dat je vždy lepší.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

import chess
import chess.engine
import chess.pgn
from pydantic import BaseModel, ConfigDict, Field

from chesslab.arena import (
    MAX_PLIES_PER_GAME,
    EngineConfig,
    GameResult,
    _engine_display_name,
    _maybe_configure_skill,
    _play_one_game,
    _resolve_path,
)
from chesslab.engines import supports_skill_for_path
from chesslab.ratings import (
    EngineRating,
    add_match_results,
    engine_id_from_path,
    recompute_bayesian_ratings,
)

# === Konstanty (limity request budgetu) ======================================

# Max enginů v turnaji. 6 engineů = 15 párů (6*5/2), s 10 partiemi per pair =
# 150 partií × ~3s = ~7.5 min. Vyšší by riskovalo request timeout.
MAX_TOURNAMENT_ENGINES = 6
MIN_TOURNAMENT_ENGINES = 2

# Partie per pár — viz README. Default 10 = rozumný kompromis (Bayesian z 10
# partií per pair konverguje slušně).
N_GAMES_PER_PAIR_MIN = 2
N_GAMES_PER_PAIR_MAX = 20
N_GAMES_PER_PAIR_DEFAULT = 10

# Time per move — sdílíme limity s arenou.
TIME_MIN = 0.05
TIME_MAX = 2.0
TIME_DEFAULT = 0.05


# === Pydantic modely =========================================================


class TournamentConfig(BaseModel):
    """Vstup pro POST /api/tournament/run."""

    engines: list[EngineConfig] = Field(
        ...,
        description=f"Engines v turnaji ({MIN_TOURNAMENT_ENGINES}-{MAX_TOURNAMENT_ENGINES}).",
        min_length=MIN_TOURNAMENT_ENGINES,
        max_length=MAX_TOURNAMENT_ENGINES,
    )
    n_games_per_pair: int = Field(
        N_GAMES_PER_PAIR_DEFAULT,
        description=f"Partií per pár ({N_GAMES_PER_PAIR_MIN}-{N_GAMES_PER_PAIR_MAX}).",
        ge=N_GAMES_PER_PAIR_MIN,
        le=N_GAMES_PER_PAIR_MAX,
    )
    time_per_move: float = Field(
        TIME_DEFAULT,
        description=f"Budget na tah ({TIME_MIN}-{TIME_MAX}s).",
        ge=TIME_MIN,
        le=TIME_MAX,
    )


class MatchupSummary(BaseModel):
    """W/L/D mezi dvěma enginy v rámci tohoto turnaje (NE kumulační, jen aktuální run)."""

    engine_a_id: str
    engine_b_id: str
    a_wins: int
    b_wins: int
    draws: int


class TournamentResult(BaseModel):
    """Výstup z POST /api/tournament/run."""

    n_engines: int
    n_games_per_pair: int
    n_pairs: int
    n_games_total: int
    time_per_move: float

    # Per-pair výsledky (jen z tohoto runu, ne kumulační z matrix).
    matchups: list[MatchupSummary]

    # Ratingy po Bayesian refit z celé matrice (= aktualizované engine_ratings
    # včetně historie před turnajem). Seřazeno sestupně.
    ratings: list[EngineRating]

    # Per-pair PGN partie (volitelné, pro debug v UI; každá je full PGN string).
    # Pokud frontend chce tabulku, vrátíme ji separátně tady. KISS držíme matchup
    # aggregát, full PGN per game se exportuje až na vyžádání (TODO IDEAS).
    games: list[GameResult]

    total_time: float = Field(..., description="Wall-clock čas celého turnaje v sekundách.")

    model_config = ConfigDict(extra="forbid")


# === Run logika ==============================================================


def _run_match_between(
    engine_a: EngineConfig,
    engine_b: EngineConfig,
    n_games: int,
    time_per_move: float,
    pair_index: int,
) -> list[GameResult]:
    """Odehraj `n_games` partií mezi A a B, alternuje barvy.

    Respawn enginů per partii (KISS, viz arena.py docstring). Vrátí list GameResult.
    """
    path_a = _resolve_path(engine_a.path)
    path_b = _resolve_path(engine_b.path)
    name_a = _engine_display_name(engine_a)
    name_b = _engine_display_name(engine_b)

    games: list[GameResult] = []
    for game_index in range(n_games):
        a_is_white = (game_index % 2 == 0)

        eng_a = chess.engine.SimpleEngine.popen_uci(path_a)
        eng_b = chess.engine.SimpleEngine.popen_uci(path_b)
        try:
            _maybe_configure_skill(eng_a, engine_a.skill)
            _maybe_configure_skill(eng_b, engine_b.skill)

            if a_is_white:
                white_eng, black_eng = eng_a, eng_b
                white_name, black_name = name_a, name_b
                white_is = "a"
            else:
                white_eng, black_eng = eng_b, eng_a
                white_name, black_name = name_b, name_a
                white_is = "b"

            board, pgn_game = _play_one_game(
                engine_white=white_eng,
                engine_black=black_eng,
                white_name=white_name,
                black_name=black_name,
                time_per_move=time_per_move,
                # Round number = globální pořadí ve turnaji (pair_index od 1, game_index od 1).
                round_number=pair_index * 1000 + game_index + 1,
            )
        finally:
            # Cleanup engine subprocesses — i při výjimce. quit() je idempotentní.
            for eng in (eng_a, eng_b):
                try:
                    eng.quit()
                except Exception:
                    pass

        # PGN string export.
        exporter = chess.pgn.StringExporter(headers=True, comments=False, variations=False)
        pgn_text = pgn_game.accept(exporter)

        result_str = board.result(claim_draw=True)
        outcome = board.outcome(claim_draw=True)
        termination = outcome.termination.name if outcome else "MAX_PLIES_EXCEEDED"

        games.append(
            GameResult(
                game_index=game_index,
                white_name=white_name,
                black_name=black_name,
                white_is=white_is,
                result=result_str,
                termination=termination,
                plies=board.ply(),
                pgn=pgn_text,
            )
        )

    return games


def run_tournament(config: TournamentConfig) -> TournamentResult:
    """Round-robin turnaj + Bayesian Elo refit z celé matchup matrice.

    Steps:
      1. Validace cest k binárkám.
      2. Pro každý pár (i, j) s i < j: odehraj n_games_per_pair partií.
      3. Aktualizuj engine_matchups (add_match_results — kumulační).
      4. Recompute Bayesian Elo ze všech matchupů (i historických).
      5. Vrátí TournamentResult s matchupy + ratingy.

    Raises:
        FileNotFoundError: binárka některého enginu chybí.
        chess.engine.EngineError: engine padl během partie.
    """
    started = time.monotonic()
    engines = config.engines
    n = len(engines)

    # 1. Validace existence binárek (lépe rychlá chyba teď než uprostřed turnaje).
    for cfg in engines:
        resolved = _resolve_path(cfg.path)
        if not Path(resolved).exists():
            raise FileNotFoundError(f"Engine binárka neexistuje: {resolved}")

    # Spočítej engine_id pro každého (pro persist do matchups + display_name map).
    engine_ids: list[str] = []
    display_names: dict[str, str] = {}
    for cfg in engines:
        resolved = _resolve_path(cfg.path)
        supports_skill = supports_skill_for_path(resolved)
        eid = engine_id_from_path(resolved, cfg.skill, supports_skill)
        engine_ids.append(eid)
        display_names[eid] = _engine_display_name(cfg)

    # 2. Round-robin: pro každý pár (i, j) s i < j.
    all_games: list[GameResult] = []
    matchup_summaries: list[MatchupSummary] = []
    pair_index = 0
    for i in range(n):
        for j in range(i + 1, n):
            pair_index += 1
            games = _run_match_between(
                engine_a=engines[i],
                engine_b=engines[j],
                n_games=config.n_games_per_pair,
                time_per_move=config.time_per_move,
                pair_index=pair_index,
            )
            all_games.extend(games)

            # Spočti W/L/D z pohledu A (i-tý engine).
            wins_a = 0
            wins_b = 0
            draws = 0
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
                # else ("*" pro MAX_PLIES) → nezapočítává se nikam, KISS.

            # 3. Update matchup matrix (kumulační).
            add_match_results(
                engine_a_id=engine_ids[i],
                engine_b_id=engine_ids[j],
                wins_a=wins_a,
                wins_b=wins_b,
                draws=draws,
            )

            matchup_summaries.append(
                MatchupSummary(
                    engine_a_id=engine_ids[i],
                    engine_b_id=engine_ids[j],
                    a_wins=wins_a,
                    b_wins=wins_b,
                    draws=draws,
                )
            )

    # 4. Bayesian Elo refit ze všech matchupů (i historických).
    # Pokud nějaký engine v matrice ještě nebyl, recompute_bayesian_ratings ho
    # založí přes display_names map.
    recompute_bayesian_ratings(engine_display_names=display_names)

    # 5. Vrátíme finální ratingy seřazené sestupně (pro UI panel po turnaji).
    # list_ratings je v ratings.py — re-export.
    from chesslab.ratings import list_ratings
    final_ratings = list_ratings()

    return TournamentResult(
        n_engines=n,
        n_games_per_pair=config.n_games_per_pair,
        n_pairs=n * (n - 1) // 2,
        n_games_total=len(all_games),
        time_per_move=config.time_per_move,
        matchups=matchup_summaries,
        ratings=final_ratings,
        games=all_games,
        total_time=round(time.monotonic() - started, 2),
    )
