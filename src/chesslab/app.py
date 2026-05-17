"""FastAPI aplikace ChessLab — webové UI nad lokálním Python backendem."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from chesslab import __version__
from chesslab.engine import EngineAnalysis, analyse_fen
from chesslab.pgn import PgnGame, parse_pgn

# FastAPI instance — to je hlavní objekt, který uvicorn umí spustit.
# title se zobrazí v /docs (auto-generated OpenAPI dokumentace).
app = FastAPI(title="ChessLab", version=__version__)

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


@app.get("/health")
def health() -> dict[str, str]:
    """Health-check endpoint pro budoucí monitoring / deploy probes."""
    return {"status": "ok", "version": __version__}


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
