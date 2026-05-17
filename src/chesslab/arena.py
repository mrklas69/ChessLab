"""Engine arena — dva UCI enginy proti sobě, X partií, agregát + per-game výsledky.

Sériový batch (KISS):
- Respawn obě enginy **per partii** (open + quit uvnitř smyčky). python-chess
  SimpleEngine nevystavuje UCI `ucinewgame` ve veřejné API, takže respawn je
  nejjednodušší cesta k čisté izolaci mezi partiemi (žádný carryover hash,
  search history ani opening repetition table). Overhead ~200ms × N je
  akceptovatelný (4s pro N=20).
- Alternujeme barvy: game 0 → A=white, game 1 → A=black, ... Fair test (oba
  enginy hrají oběma barvama stejně často). U lichý N dostane engine A o 1
  hru navíc s bílým.
- Synchronní endpoint — N×~3s je v rámci request budgetu (max 20 partií).

Persistent state žádný — engine instance žijí jen po dobu jedné partie,
dispose přes try/finally i při výjimce uprostřed hry.
"""

from __future__ import annotations

import datetime
import math
import time
from pathlib import Path

import chess
import chess.engine
import chess.pgn
from pydantic import BaseModel, ConfigDict, Field

from chesslab.engine import _stockfish_path
from chesslab.engines import display_name_for_path

# === Konstanty ===============================================================

# Skill Level je Stockfish UCI option (int 0-20). Pro vlastní UCI engine ho
# tiše ignorujeme — viz `_maybe_configure_skill`.
SKILL_MIN = 0
SKILL_MAX = 20
SKILL_DEFAULT_A = 5
SKILL_DEFAULT_B = 8  # default = mírně asymetrický pár (perf rating dá přesnou hodnotu už od první spuštění; 5 vs 15 dával sweep 0:N a jen lower-bound)

# Time per move = budget enginu na tah (v sekundách).
TIME_MIN = 0.05
TIME_MAX = 2.0
TIME_DEFAULT = 0.1

# Počet partií — horní limit drží request pod ~60s (20 × 30 tahů × 0.1s = 60s).
N_GAMES_MIN = 1
N_GAMES_MAX = 20
N_GAMES_DEFAULT = 6

# Hard ceiling počtu tahů per partii — pojistka proti zacyklené koncovce
# (oba enginy v koncovce KvK shufflují → 50-move rule normálně ukončí, ale
# je dobré mít explicit limit). 300 tahů = 600 plies = víc než většina partií.
MAX_PLIES_PER_GAME = 600


# === Pydantic modely =========================================================


class EngineConfig(BaseModel):
    """Konfigurace jednoho enginu (path + optional skill level).

    `path` — cesta k UCI binárce. Default = STOCKFISH_PATH (z `engine.py`).
    `skill` — aplikuje se přes `engine.configure({"Skill Level": N})` jen pokud
    engine option `Skill Level` podporuje (typicky Stockfish). U custom enginu,
    který Skill Level nemá, se tiše přeskočí.
    `name` — display label v UI a v PGN headerech. Auto-generuje se z path +
    skill, pokud uživatel nepošle vlastní.
    """

    path: str = Field(..., description="Absolutní cesta k UCI binárce.", min_length=1)
    skill: int = Field(
        SKILL_DEFAULT_A,
        description=f"Skill Level pro Stockfish ({SKILL_MIN}-{SKILL_MAX}). U jiných UCI enginů se ignoruje.",
        ge=SKILL_MIN,
        le=SKILL_MAX,
    )
    name: str | None = Field(
        None,
        description="Display label. Pokud None, vygeneruje se z basename(path) + skill.",
    )


class ArenaConfig(BaseModel):
    """Vstupní payload pro spuštění areny."""

    engine_a: EngineConfig
    engine_b: EngineConfig
    n_games: int = Field(
        N_GAMES_DEFAULT,
        description=f"Počet partií ({N_GAMES_MIN}-{N_GAMES_MAX}).",
        ge=N_GAMES_MIN,
        le=N_GAMES_MAX,
    )
    time_per_move: float = Field(
        TIME_DEFAULT,
        description=f"Budget na tah v sekundách ({TIME_MIN}-{TIME_MAX}).",
        ge=TIME_MIN,
        le=TIME_MAX,
    )


class GameResult(BaseModel):
    """Výsledek jedné odehrané partie."""

    game_index: int = Field(..., description="Pořadí partie (0-based).")
    white_name: str = Field(..., description="Display name enginu hrajícího bílého.")
    black_name: str = Field(..., description="Display name enginu hrajícího černého.")
    # 'a' nebo 'b' — kdo hrál bílého v této partii (pro UI barvení).
    white_is: str = Field(..., description="'a' nebo 'b' — který engine hrál bílého.")
    result: str = Field(..., description="PGN výsledek: '1-0' / '0-1' / '1/2-1/2' / '*' (timeout/max plies).")
    termination: str = Field(..., description="Důvod ukončení (CHECKMATE, STALEMATE, INSUFFICIENT_MATERIAL, ...).")
    plies: int = Field(..., description="Počet půltahů.")
    pgn: str = Field(..., description="Kompletní PGN partie se Seven Tag Roster.")


class ArenaResult(BaseModel):
    """Souhrn celé areny (vrátí se po dokončení všech partií)."""

    # Echo configu — pro UI panel "Co se právě odehrálo".
    engine_a_name: str
    engine_b_name: str
    n_games: int
    time_per_move: float

    # Agregát z pohledu enginu A.
    a_wins: int = Field(..., description="Počet výher A (bez ohledu na barvu).")
    b_wins: int = Field(..., description="Počet výher B.")
    draws: int = Field(..., description="Počet remíz.")
    score_a: float = Field(..., description="Skóre A (W=1, D=0.5). Max = n_games.")
    score_pct_a: float = Field(..., description="Skóre A v procentech (0-100).")
    # Performance rating diff: o kolik je A silnější/slabší než B (v Elo bodech),
    # spočtený z výsledku tohoto matche. None pokud A vyhrál/prohrál vše (vzorec by dělil 0).
    perf_rating_diff: float | None = Field(
        None,
        description="Elo rozdíl A vs B (kladné = A silnější). None pouze pro n_games=0.",
    )
    # Pro extrémní výsledky (100% / 0%) skutečný Elo diff = ±∞. Vracíme místo
    # toho dolní (resp. horní) mez spočtenou z (score ± 0.5)/n_games tricku
    # a flagujeme to — UI pak zobrazí '≥ +X Elo' nebo '≤ -X Elo' místo holého čísla.
    perf_rating_is_bound: bool = Field(
        False,
        description="True = perf_rating_diff je dolní/horní mez (extrémní výsledek), ne přesná hodnota.",
    )

    games: list[GameResult]
    total_time: float = Field(..., description="Wall-clock čas celé areny v sekundách.")

    # ConfigDict je pydantic v2 způsob konfigurace modelu. JSON serializace
    # zaokrouhluje floaty automaticky na repr — pro UI je to OK.
    model_config = ConfigDict(extra="forbid")


# === Helpery (vnitřní) =======================================================


def _engine_display_name(cfg: EngineConfig) -> str:
    """Vrátí display name — buď explicitní z configu, jinak z discovery registry,
    jinak path-based fallback.

    Auto formát (registry path): 'Stockfish (skill 15)' nebo 'ChessLab Random v0'.
    Path-based fallback (unknown engine): 'mybinary (skill 5)'.

    Skill suffix přidáváme jen pro enginy v registry, které Skill Level podporují
    (typicky Stockfish). Pro vlastní enginy bez Skill Level by suffix byl matoucí
    (Random skill 5 = stejně random). Detekce skill-aware = display name nezačíná
    'ChessLab ' (drobná heurystika, drží to izomorfně s `_KNOWN_ENGINES` mapou).
    """
    if cfg.name:
        return cfg.name

    # Pokus 1: registry lookup (Stockfish, chesslab-*).
    registered = display_name_for_path(cfg.path)
    if registered is not None:
        # Stockfish skill = relevant, ChessLab enginy skill ignorují → bez suffixu.
        if registered.startswith("ChessLab "):
            return registered
        return f"{registered} (skill {cfg.skill})"

    # Pokus 2: path-based fallback pro neznámé enginy.
    stem = Path(cfg.path).stem
    short = stem.split("-")[0] if "-" in stem else stem
    return f"{short} (skill {cfg.skill})"


def _resolve_path(path: str) -> str:
    """Pokud je path prázdný/whitespace → vrať default Stockfish path.

    Frontend posílá vždy nějakou hodnotu, ale prázdné políčko je legitimní
    signál "použij default".
    """
    if not path.strip():
        return _stockfish_path()
    return path


def _maybe_configure_skill(engine: chess.engine.SimpleEngine, skill: int) -> None:
    """Nastaví `Skill Level` jen pokud ho engine podporuje (Stockfish ano).

    `engine.options` je dict UCI options, které engine při handshake zaregistroval.
    U custom enginu bez `Skill Level` tiše přeskočíme (nechceme arenu padat).
    """
    if "Skill Level" in engine.options:
        engine.configure({"Skill Level": skill})


def _perf_rating_diff(score: float, n_games: int) -> tuple[float | None, bool]:
    """Performance rating diff z výsledku matche (z pohledu A vs B).

    Vzorec: Elo_diff = -400 * log10(1/score_rate - 1).
    - score_rate = 0.5 → diff = 0 (vyrovnaný)
    - score_rate = 0.75 → diff ≈ +191 (A silnější)
    - score_rate = 0.25 → diff ≈ -191
    - score_rate = 0 nebo 1 → skutečný diff = ±∞. Místo None vracíme konzervativní
      mez přes (score ± 0.5)/n_games trick (jakoby A udělal/dostal jednu remízu
      navíc) — to dá dolní (resp. horní) odhad jeho síly. Druhá hodnota tuple
      = is_bound flag, který UI signalizuje '≥' / '≤' v zobrazení.

    Returns:
        (value, is_bound). value=None pouze pro n_games=0 (žádný match nehrán).
    """
    if n_games <= 0:
        return (None, False)
    rate = score / n_games
    is_bound = False
    if rate >= 1.0:
        # 100% výhra → dolní mez: použij (n - 0.5)/n (kdyby místo poslední výhry byla remíza).
        rate = (n_games - 0.5) / n_games
        is_bound = True
    elif rate <= 0.0:
        # 0% (samé prohry) → horní mez: použij 0.5/n (kdyby místo jedné prohry byla remíza).
        rate = 0.5 / n_games
        is_bound = True
    # math.log10 je dekadický logaritmus (Elo vzorec je definován s log10).
    value = round(-400.0 * math.log10(1.0 / rate - 1.0), 1)
    return (value, is_bound)


def _play_one_game(
    engine_white: chess.engine.SimpleEngine,
    engine_black: chess.engine.SimpleEngine,
    white_name: str,
    black_name: str,
    time_per_move: float,
    round_number: int,
) -> tuple[chess.Board, chess.pgn.Game]:
    """Odehraje jednu partii — vrátí finální board + PGN game object.

    Smyčka jde dokud not game_over nebo dokud nepřekročíme MAX_PLIES_PER_GAME
    (safety net proti zacyklené koncovce). Volající dodává čerstvě spawnnuté
    enginy (viz `run_arena` — respawn per game), proto se uvnitř o UCI
    `ucinewgame` nestaráme.
    """
    board = chess.Board()
    # Slovník enginů per barvě pro úhledný loop.
    engines_by_color: dict[chess.Color, chess.engine.SimpleEngine] = {
        chess.WHITE: engine_white,
        chess.BLACK: engine_black,
    }

    plies = 0
    while not board.is_game_over() and plies < MAX_PLIES_PER_GAME:
        engine = engines_by_color[board.turn]
        # engine.play vrací PlayResult, .move je vybraný tah.
        play = engine.play(board, chess.engine.Limit(time=time_per_move))
        if play.move is None:
            # Defenzivní fallback — engine vrátil None místo tahu (nemělo by se stát
            # u rozumného UCI enginu). Považujeme za prohru engine, ale vlastně
            # board.is_game_over() už by mělo být True; ukončíme loop.
            break
        board.push(play.move)
        plies += 1

    # Postavit PGN object z hotového boardu.
    game = chess.pgn.Game.from_board(board)
    game.headers["Event"] = "ChessLab Arena"
    game.headers["Site"] = "ChessLab (localhost)"
    game.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
    # Round = pořadí partie (1-based), drží konvenci PGN.
    game.headers["Round"] = str(round_number)
    game.headers["White"] = white_name
    game.headers["Black"] = black_name
    # Pokud jsme vypadli na MAX_PLIES_PER_GAME, board.result() vrátí '*' (neukončeno).
    game.headers["Result"] = board.result(claim_draw=True)

    return board, game


# === Veřejná funkce ==========================================================


def run_arena(config: ArenaConfig) -> ArenaResult:
    """Spustí arenu — odehraje N partií enginy A vs B, alternuje barvy.

    Sériový batch, blokující volání. Pro N=20 × ~3s = ~60s wall-clock.

    Raises:
        FileNotFoundError: jeden z enginů na disku neexistuje.
        chess.engine.EngineError: engine se nepodařilo spustit / odpadl během hry.
    """
    # Resolve cest (prázdné string → default Stockfish).
    path_a = _resolve_path(config.engine_a.path)
    path_b = _resolve_path(config.engine_b.path)

    # Validace existence — explicitní message lepší než cryptic FileNotFoundError z popen.
    if not Path(path_a).exists():
        raise FileNotFoundError(f"Engine A binárka neexistuje: {path_a}")
    if not Path(path_b).exists():
        raise FileNotFoundError(f"Engine B binárka neexistuje: {path_b}")

    name_a = _engine_display_name(config.engine_a)
    name_b = _engine_display_name(config.engine_b)

    started = time.monotonic()

    games: list[GameResult] = []
    a_wins = 0
    b_wins = 0
    draws = 0
    score_a = 0.0

    # Respawn obě enginy per partii — viz docstring modulu. Čistá izolace,
    # overhead ~200ms × N je zanedbatelný proti délce partie (sekundy).
    for game_index in range(config.n_games):
        # Alternujeme: sudé indexy → A bílý, lichý → B bílý.
        # Při lichý N dostane A o jeden start s bílým navíc (ne ideál pro fair test,
        # ale pro batch ≤20 partií zanedbatelné — výsledek pak má naturální variance).
        a_is_white = (game_index % 2 == 0)

        engine_a = chess.engine.SimpleEngine.popen_uci(path_a)
        engine_b = chess.engine.SimpleEngine.popen_uci(path_b)
        try:
            _maybe_configure_skill(engine_a, config.engine_a.skill)
            _maybe_configure_skill(engine_b, config.engine_b.skill)

            if a_is_white:
                white_eng, black_eng = engine_a, engine_b
                white_name, black_name = name_a, name_b
                white_is = "a"
            else:
                white_eng, black_eng = engine_b, engine_a
                white_name, black_name = name_b, name_a
                white_is = "b"

            board, pgn_game = _play_one_game(
                engine_white=white_eng,
                engine_black=black_eng,
                white_name=white_name,
                black_name=black_name,
                time_per_move=config.time_per_move,
                round_number=game_index + 1,
            )
        finally:
            # Cleanup engine subprocesses — i při výjimce. quit() je idempotentní.
            for eng in (engine_a, engine_b):
                try:
                    eng.quit()
                except Exception:
                    pass

        # Vyhodnocení výsledku z pohledu enginu A.
        result_str = board.result(claim_draw=True)
        # outcome je None, pokud partie skončila na MAX_PLIES (board.is_game_over() False).
        outcome = board.outcome(claim_draw=True)
        termination = outcome.termination.name if outcome else "MAX_PLIES_EXCEEDED"

        # Aktualizace skóre podle toho, kdo byl A a kdo vyhrál.
        if result_str == "1-0":
            if a_is_white:
                a_wins += 1
                score_a += 1.0
            else:
                b_wins += 1
        elif result_str == "0-1":
            if a_is_white:
                b_wins += 1
            else:
                a_wins += 1
                score_a += 1.0
        elif result_str == "1/2-1/2":
            draws += 1
            score_a += 0.5
        # else (např. "*" z MAX_PLIES_EXCEEDED) → nezapočítává se nikam,
        # zůstane viditelné v games list, ale neovlivní skóre. KISS.

        # PGN export — Seven Tag Roster + tahy.
        exporter = chess.pgn.StringExporter(headers=True, comments=False, variations=False)
        pgn_text = pgn_game.accept(exporter)

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

    score_pct = round(100.0 * score_a / config.n_games, 1) if config.n_games > 0 else 0.0
    perf_value, perf_is_bound = _perf_rating_diff(score_a, config.n_games)
    return ArenaResult(
        engine_a_name=name_a,
        engine_b_name=name_b,
        n_games=config.n_games,
        time_per_move=config.time_per_move,
        a_wins=a_wins,
        b_wins=b_wins,
        draws=draws,
        score_a=score_a,
        score_pct_a=score_pct,
        perf_rating_diff=perf_value,
        perf_rating_is_bound=perf_is_bound,
        games=games,
        total_time=round(time.monotonic() - started, 2),
    )
