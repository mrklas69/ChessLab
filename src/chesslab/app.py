"""FastAPI aplikace ChessLab — webové UI nad lokálním Python backendem."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import chess
import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from chesslab import __version__
from chesslab.arena import (
    N_GAMES_DEFAULT,
    N_GAMES_MAX,
    N_GAMES_MIN,
    SKILL_DEFAULT_A,
    SKILL_DEFAULT_B,
    TIME_DEFAULT,
    TIME_MAX,
    TIME_MIN,
    ArenaConfig,
    ArenaResult,
    run_arena,
)
from chesslab.arena import SKILL_MAX as ARENA_SKILL_MAX
from chesslab.arena import SKILL_MIN as ARENA_SKILL_MIN
from chesslab.db import init_db
from chesslab.engine import (
    DEFAULT_STOCKFISH_PATH,
    EngineAnalysis,
    PositionEval,
    analyse_fen,
    analyse_game_fens,
)
from chesslab.engines import EngineInfo, list_available_engines
from chesslab.classifier import get_or_classify_game
from chesslab.ratings import EngineRating, init_anchor, list_ratings
from chesslab.tournament import (
    MAX_TOURNAMENT_ENGINES,
    MIN_TOURNAMENT_ENGINES,
    N_GAMES_PER_PAIR_DEFAULT,
    N_GAMES_PER_PAIR_MAX,
    N_GAMES_PER_PAIR_MIN,
    TIME_DEFAULT as TOURNAMENT_TIME_DEFAULT,
    TIME_MAX as TOURNAMENT_TIME_MAX,
    TIME_MIN as TOURNAMENT_TIME_MIN,
    TournamentConfig,
    TournamentResult,
    run_tournament,
)
from chesslab.games import (
    GameSummary,
    ImportResult,
    MoveEval,
    count_games,
    get_game_pgn,
    get_move_evals,
    has_classification,
    import_lichess_user,
    list_games,
)
from chesslab.lichess import DEFAULT_MAX_GAMES, HARD_MAX_GAMES
from chesslab.pgn import PgnGame, parse_pgn
from chesslab.play import (
    SKILL_DEFAULT,
    SKILL_MAX,
    SKILL_MIN,
    THINK_TIME_MAX,
    THINK_TIME_MIN,
    PlayStateResponse,
    apply_player_move,
    get_pgn_download,
    resign_game,
    start_game,
    undo_last_move,
)


# === Lifespan ================================================================
# Lifespan = startup/shutdown hook. Při startu inicializujeme DB schema
# (idempotentní — IF NOT EXISTS), takže první spuštění po `uv sync` vytvoří
# `data/chesslab.db` automaticky, žádný extra příkaz.
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # Seed anchor rating (Stockfish skill 5 = 1500). Idempotentní — re-start
    # neměnenrating, jen vytvoří, pokud chybí (čistá DB / nová instalace).
    init_anchor()
    yield


# FastAPI instance — to je hlavní objekt, který uvicorn umí spustit.
# title se zobrazí v /docs (auto-generated OpenAPI dokumentace).
app = FastAPI(title="ChessLab", version=__version__, lifespan=lifespan)

# Jinja2 templates — cestu odvodíme od umístění tohoto modulu, ať to funguje
# i když je projekt instalovaný jako balíček (ne jen běh z source dir).
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Rozcestník — links na všechny features projektu."""
    # TemplateResponse vyžaduje `request` v contextu (FastAPI/Starlette konvence).
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": __version__},
    )


@app.get("/pgn", response_class=HTMLResponse)
def pgn_viewer(request: Request) -> HTMLResponse:
    """PGN viewer — šachovnice + textarea + move list (zatím bez JS glue)."""
    return templates.TemplateResponse(request=request, name="pgn.html")


@app.get("/arena", response_class=HTMLResponse)
def arena_page(request: Request) -> HTMLResponse:
    """Engine arena — dva UCI enginy proti sobě, batch X partií."""
    return templates.TemplateResponse(
        request=request,
        name="arena.html",
        context={
            # Defaults a limity pro inputy — single source of truth z arena.py.
            "default_stockfish_path": DEFAULT_STOCKFISH_PATH,
            "skill_min": ARENA_SKILL_MIN,
            "skill_max": ARENA_SKILL_MAX,
            "skill_default_a": SKILL_DEFAULT_A,
            "skill_default_b": SKILL_DEFAULT_B,
            "time_min": TIME_MIN,
            "time_max": TIME_MAX,
            "time_default": TIME_DEFAULT,
            "n_games_min": N_GAMES_MIN,
            "n_games_max": N_GAMES_MAX,
            "n_games_default": N_GAMES_DEFAULT,
        },
    )


@app.get("/play", response_class=HTMLResponse)
def play_page(request: Request) -> HTMLResponse:
    """Herní stránka — hraní proti Stockfish (drag-and-drop, settings sidebar)."""
    return templates.TemplateResponse(
        request=request,
        name="play.html",
        context={
            # Defaults pro <input> elementy v template — single source of truth.
            "skill_min": SKILL_MIN,
            "skill_max": SKILL_MAX,
            "skill_default": SKILL_DEFAULT,
            "think_time_min": THINK_TIME_MIN,
            "think_time_max": THINK_TIME_MAX,
        },
    )


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request) -> HTMLResponse:
    """Import partií z externích zdrojů (zatím jen Lichess)."""
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context={
            "default_max_games": DEFAULT_MAX_GAMES,
            "hard_max_games": HARD_MAX_GAMES,
        },
    )


@app.get("/games", response_class=HTMLResponse)
def games_page(request: Request) -> HTMLResponse:
    """Browser stažených partií — tabulka s filtry, klik → analýza v /pgn."""
    return templates.TemplateResponse(request=request, name="games.html")


@app.get("/engines", response_class=HTMLResponse)
def engines_page(request: Request) -> HTMLResponse:
    """ChessLab Elo žebříček + round-robin turnaj."""
    return templates.TemplateResponse(
        request=request,
        name="engines.html",
        context={
            "tournament_min_engines": MIN_TOURNAMENT_ENGINES,
            "tournament_max_engines": MAX_TOURNAMENT_ENGINES,
            "n_games_per_pair_min": N_GAMES_PER_PAIR_MIN,
            "n_games_per_pair_max": N_GAMES_PER_PAIR_MAX,
            "n_games_per_pair_default": N_GAMES_PER_PAIR_DEFAULT,
            "tournament_time_min": TOURNAMENT_TIME_MIN,
            "tournament_time_max": TOURNAMENT_TIME_MAX,
            "tournament_time_default": TOURNAMENT_TIME_DEFAULT,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Health-check endpoint pro budoucí monitoring / deploy probes."""
    return {"status": "ok", "version": __version__}


@app.get("/api/engines/list", response_model=list[EngineInfo])
def api_engines_list() -> list[EngineInfo]:
    """Vrátí seznam dostupných enginů (Stockfish + vlastní ChessLab enginy).

    UI (Play, Arena) to používá pro dropdown výběru. Pořadí: Stockfish první
    (default volba), pak vlastní enginy abecedně.
    """
    return list_available_engines()


@app.get("/api/engines/ratings", response_model=list[EngineRating])
def api_engines_ratings() -> list[EngineRating]:
    """ChessLab Elo ratingy všech enginů, kteří někdy hráli v aréně.

    Seřazeno sestupně podle ratingu. Anchor (Stockfish skill 5 = 1500) je
    vždy přítomný (seedovaný při startu).
    """
    return list_ratings()


@app.post("/api/tournament/run", response_model=TournamentResult)
def api_tournament_run(config: TournamentConfig) -> TournamentResult:
    """Round-robin turnaj N enginů + Bayesian Elo refit z celé matchup matrice.

    **Sériový blokující endpoint** — pro 4 enginy × 10 partií = ~3 min, pro 6×10 = ~7 min.
    Klient musí zvolit timeout >= 600s.

    Status mapping:
      - 404: žádný engine v configu (Pydantic validace by mělo chytit dřív)
      - 500: binárka enginu chybí (path FileNotFoundError) nebo engine error.
    """
    try:
        return run_tournament(config)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        # python-chess engine errors, MM divergence atd. — všechno 500 s detailem.
        raise HTTPException(status_code=500, detail=f"Turnaj selhal: {exc}") from exc


# === PGN API =================================================================


class PgnParseRequest(BaseModel):
    """Vstupní payload pro /api/pgn/parse — jeden řetězec s PGN partií."""

    pgn: str = Field(
        ...,
        description="PGN text jedné partie (víc partií = parsuje se pouze první).",
        min_length=1,
    )


@app.post("/api/pgn/parse", response_model=PgnGame)
def api_pgn_parse(req: PgnParseRequest) -> PgnGame:
    """Rozparsuje PGN a vrátí hlavičky + lineární seznam tahů s FEN po každém tahu.

    Frontend tohle použije pro PGN viewer: pohyb šipkami ←/→ jen mění
    `fen_after[i]` na šachovnici, žádná další round-trip na server.
    """
    try:
        return parse_pgn(req.pgn)
    except ValueError as exc:
        # FastAPI to převede na JSON odpověď: {"detail": "..."} se statusem 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# === Engine API ==============================================================


class EngineAnalyseRequest(BaseModel):
    """Vstupní payload pro /api/engine/analyse — FEN + budget analýzy."""

    fen: str = Field(..., description="FEN pozice k analýze.", min_length=1)
    time: float = Field(
        1.0,
        description="Budget analýzy v sekundách (0.1–10.0).",
        gt=0.0,
        le=10.0,
    )


@app.post("/api/engine/analyse", response_model=EngineAnalysis)
def api_engine_analyse(req: EngineAnalyseRequest) -> EngineAnalysis:
    """Pošle pozici Stockfishi, vrátí eval (cp/mate) + best move + depth.

    Spawn-per-request — žádný persistent engine. Lag startu ~50–200ms + budget.
    """
    try:
        return analyse_fen(req.fen, req.time)
    except FileNotFoundError as exc:
        # 500 = server-side config problem (chybějící binárka), ne klientova chyba.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        # 400 = klient poslal špatný FEN nebo koncovou pozici.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class EngineAnalyseGameRequest(BaseModel):
    """Vstupní payload pro /api/engine/analyse_game — list FEN pozic celé partie."""

    fens: list[str] = Field(
        ...,
        description="Seznam FEN pozic v pořadí (typicky startpos + pozice po každém půltahu).",
        min_length=1,
        max_length=400,  # hard cap proti zabití serveru dlouhou partií (400 půltahů = 200 tahů)
    )
    time_per_move: float = Field(
        0.3,
        description="Budget na pozici v sekundách (0.05–2.0).",
        ge=0.05,
        le=2.0,
    )


class EngineAnalyseGameResponse(BaseModel):
    """Výstup /api/engine/analyse_game — list eval per pozici + metadata."""

    evals: list[PositionEval]
    time_per_move: float = Field(..., description="Použitý budget per pozici (echo z requestu).")
    total_time: float = Field(..., description="Skutečný wall-clock čas celé analýzy v sekundách.")


@app.post("/api/engine/analyse_game", response_model=EngineAnalyseGameResponse)
def api_engine_analyse_game(req: EngineAnalyseGameRequest) -> EngineAnalyseGameResponse:
    """Zanalyzuje sérii pozic jedním persistentním Stockfishem (rychlejší než spawn-per).

    Synchronní endpoint — pro 80 pozic × 0.3s = ~24s wait. Streaming přidáme,
    pokud se ukáže jako UX problém (zatím KISS).
    """
    import time as _time

    started = _time.monotonic()
    try:
        evals = analyse_game_fens(req.fens, req.time_per_move)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return EngineAnalyseGameResponse(
        evals=evals,
        time_per_move=req.time_per_move,
        total_time=_time.monotonic() - started,
    )


# === Play API ================================================================


class PlayStartRequest(BaseModel):
    """Vstupní payload pro /api/play/start — start nové hry proti UCI enginu."""

    color: str = Field(
        ...,
        description="Barva hráče: 'w' (bílý, hraje první) nebo 'b' (černý, engine táhne první).",
        pattern="^[wb]$",
    )
    skill: int = Field(
        SKILL_DEFAULT,
        description=f"UCI engine Skill Level ({SKILL_MIN}–{SKILL_MAX}). Aplikuje se jen pokud engine option 'Skill Level' podporuje (Stockfish ano, vlastní enginy ne).",
        ge=SKILL_MIN,
        le=SKILL_MAX,
    )
    think_time: float = Field(
        ...,
        description=f"Budget enginu na tah v sekundách ({THINK_TIME_MIN}–{THINK_TIME_MAX}).",
        ge=THINK_TIME_MIN,
        le=THINK_TIME_MAX,
    )
    engine_path: str | None = Field(
        None,
        description="Cesta k UCI binárce. None / prázdný string → default Stockfish (env STOCKFISH_PATH).",
    )


class PlayMoveRequest(BaseModel):
    """Vstupní payload pro /api/play/move — hráčův tah ve formě from/to (chessboard.js onDrop)."""

    from_sq: str = Field(
        ...,
        description="Výchozí pole, např. 'e2'.",
        # alias 'from' — 'from' je Python keyword, nelze použít jako název atributu.
        alias="from",
        pattern="^[a-h][1-8]$",
    )
    to_sq: str = Field(
        ...,
        description="Cílové pole, např. 'e4'.",
        alias="to",
        pattern="^[a-h][1-8]$",
    )

    # populate_by_name=True dovolí FastAPI číst payload jako {"from": "...", "to": "..."}
    # (klient posílá), a zároveň atribut na Python straně se jmenuje from_sq.
    model_config = {"populate_by_name": True}


@app.post("/api/play/start", response_model=PlayStateResponse)
def api_play_start(req: PlayStartRequest) -> PlayStateResponse:
    """Start nové hry proti Stockfish. Restartuje engine, resetuje board.

    Pokud hraje hráč za černého, engine táhne hned (last_engine_move v odpovědi).
    """
    color = chess.WHITE if req.color == "w" else chess.BLACK
    try:
        return start_game(
            color=color,
            skill=req.skill,
            think_time=req.think_time,
            engine_path=req.engine_path,
        )
    except FileNotFoundError as exc:
        # Chybějící engine binárka — server-side config problém.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/play/move", response_model=PlayStateResponse)
def api_play_move(req: PlayMoveRequest) -> PlayStateResponse:
    """Hráč táhne (from/to), engine automaticky reaguje, pokud hra běží dál.

    Vrací stav s oběma tahy (last_player_move + last_engine_move),
    nebo jen hráčův tah, pokud po něm skončila hra (mat).
    """
    try:
        return apply_player_move(req.from_sq, req.to_sq)
    except ValueError as exc:
        # Neplatný tah, není tvůj tah, hra skončila, engine nepřipraven — vše 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/undo", response_model=PlayStateResponse)
def api_play_undo() -> PlayStateResponse:
    """Vrátí poslední hráčův + engine tah (pop 2 plies), hráč je zase na tahu.

    Funguje i po resign / matu — vrátí flag a vrátí pozici před koncem.
    """
    try:
        return undo_last_move()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/resign", response_model=PlayStateResponse)
def api_play_resign() -> PlayStateResponse:
    """Hráč se vzdal. Vrátí finální stav s game_over=true a result podle barvy."""
    try:
        return resign_game()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/play/pgn")
def api_play_pgn() -> Response:
    """Stáhne PGN aktuální hry se Seven Tag Roster.

    Content-Disposition: attachment donutí browser stáhnout soubor.
    Funguje i pro rozjetou hru (Result="*") i po skončení/rezignaci.
    """
    try:
        pgn = get_pgn_download()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=pgn,
        media_type="application/x-chess-pgn; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="game.pgn"'},
    )


# === Arena API ===============================================================


@app.post("/api/arena/run", response_model=ArenaResult)
def api_arena_run(req: ArenaConfig) -> ArenaResult:
    """Spustí arenu — odehraje N partií enginy A vs B (alternuje barvy), vrátí výsledky.

    Synchronní batch — pro N=20 × ~3s = ~60s blokuje request. Vyšší N nebo delší
    time_per_move riskují timeout, validace v Pydantic to drží v rozumných mezích.
    """
    try:
        return run_arena(req)
    except FileNotFoundError as exc:
        # Engine binárka neexistuje — server-side config problem (cesta dodaná klientem,
        # ale i tak 500 — chybí lokální resource, není to syntax error v requestu).
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        # python-chess engine errors (EngineTerminatedError, EngineError, ...) — 500.
        raise HTTPException(status_code=500, detail=f"Engine chyba: {exc}") from exc


# === Import API (Lichess) ====================================================


class LichessImportRequest(BaseModel):
    """Vstupní payload pro /api/import/lichess."""

    username: str = Field(
        ...,
        description="Lichess username (case-insensitive).",
        min_length=1,
        max_length=64,
    )
    max_games: int = Field(
        DEFAULT_MAX_GAMES,
        description=f"Max počet partií ke stažení (1–{HARD_MAX_GAMES}).",
        ge=1,
        le=HARD_MAX_GAMES,
    )
    force_full: bool = Field(
        False,
        description=(
            "True → ignoruj last import timestamp a fetchni celou historii "
            "(re-sync / repair). Default False = inkrementální (jen nové partie)."
        ),
    )


@app.post("/api/import/lichess", response_model=ImportResult)
def api_import_lichess(req: LichessImportRequest) -> ImportResult:
    """Stáhne partie uživatele z Lichess + uloží do DB (UPSERT, žádné duplikáty).

    Synchronní endpoint — pro 100 partií typicky 5–15s wait. Vyšší max_games
    riskují klientův timeout (lze přidat streaming/SSE později).

    Status mapping:
      - 404: user neexistuje
      - 429: Lichess rate limit
      - 502: jiný HTTP error z Lichess API
      - 504: timeout
      - 400: validation error (špatné parametry)
    """
    try:
        return import_lichess_user(req.username, req.max_games, force_full=req.force_full)
    except ValueError as exc:
        # Validation error (prázdný username, max_games mimo rozsah) — bylo by
        # už chyceno Pydantic, ale safety net.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:200]  # truncate ať nezahltíme error message
        if status in (404, 429):
            raise HTTPException(status_code=status, detail=f"Lichess API: {body}") from exc
        # Jiný HTTP error mapujeme na 502 Bad Gateway (problém s upstreamem).
        raise HTTPException(status_code=502, detail=f"Lichess API ({status}): {body}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Lichess API timeout") from exc


# === Games API ===============================================================


@app.get("/api/games", response_model=list[GameSummary])
def api_games_list(
    username: str | None = None,
    color: Literal["all", "white", "black"] = "all",
    result: Literal["all", "win", "loss", "draw"] = "all",
    speed: str | None = None,
    limit: int = 500,
) -> list[GameSummary]:
    """Vrátí seznam stažených partií podle filtrů (od nejnovější).

    Query params: ?username=foo&color=white&result=win&speed=blitz&limit=100.
    Bez filtru = všechny partie v DB (LIMIT 500 safety cap).
    """
    # Cap zvenku, ať klient nemůže poslat limit=1000000.
    safe_limit = max(1, min(limit, 500))
    return list_games(
        username=username,
        color=color,
        result=result,
        speed=speed,
        limit=safe_limit,
    )


@app.get("/api/games/count")
def api_games_count() -> dict[str, int]:
    """Celkový počet partií v DB. Pro empty-state UI na /games."""
    return {"count": count_games()}


@app.get("/api/games/{game_id}/pgn")
def api_game_pgn(game_id: str) -> Response:
    """Vrátí PGN partie jako plain text (frontend ho dá do /pgn přes localStorage)."""
    pgn = get_game_pgn(game_id)
    if pgn is None:
        raise HTTPException(status_code=404, detail=f"Partie {game_id!r} nenalezena")
    return Response(
        content=pgn,
        media_type="application/x-chess-pgn; charset=utf-8",
    )


# === Classification API ======================================================
# Per-tah klasifikace (best/good/inaccuracy/mistake/blunder) z Stockfish eval.
# Lazy on-demand: POST spustí výpočet (synchronní, ~30s pro 80-plies partii),
# GET vrátí cached. Re-classify s jiným time_per_move přepíše záznamy.


class ClassificationResponse(BaseModel):
    """Výstup endpointů /classify a /classification."""

    game_id: str
    evals: list[MoveEval]
    cached: bool = Field(
        ...,
        description="True pokud vráceno z cache, False pokud čerstvě spočteno.",
    )


@app.post("/api/games/{game_id}/classify", response_model=ClassificationResponse)
def api_game_classify(game_id: str, time_per_move: float = 0.3) -> ClassificationResponse:
    """Spustí klasifikaci partie (nebo vrátí cached, pokud existuje).

    Query param `time_per_move` (default 0.3s) určuje Stockfish budget per pozici
    pro fresh classify. Pokud cache existuje, parametr se ignoruje (čte se as-is).

    Synchronní — pro 80-plies partii ~24s wait. Klient by měl ukázat loader/progress.
    """
    if not (0.05 <= time_per_move <= 2.0):
        raise HTTPException(
            status_code=400,
            detail=f"time_per_move musí být v rozsahu 0.05–2.0s (dostal {time_per_move}).",
        )
    was_cached = has_classification(game_id)
    try:
        evals = get_or_classify_game(game_id, time_per_move=time_per_move)
    except KeyError as exc:
        # Partie neexistuje v DB.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        # Stockfish binárka chybí.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        # Pravděpodobně rozbitý PGN — neměl by nastat (insertujeme jen validní),
        # ale safety net.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ClassificationResponse(game_id=game_id, evals=evals, cached=was_cached)


@app.get("/api/games/{game_id}/classification", response_model=ClassificationResponse)
def api_game_classification(game_id: str) -> ClassificationResponse:
    """Vrátí cached klasifikaci. 404 pokud ještě nebyla spočtena.

    Pro on-demand fresh compute použij POST /classify. Tento GET nikdy nespouští
    Stockfish — slouží frontendu k 'check, jestli existuje, a pokud ne, zobraz
    tlačítko Klasifikovat'.
    """
    if not has_classification(game_id):
        raise HTTPException(
            status_code=404,
            detail=f"Partie {game_id!r} ještě nebyla klasifikována (POST /classify ji spustí).",
        )
    evals = get_move_evals(game_id)
    return ClassificationResponse(game_id=game_id, evals=evals, cached=True)
