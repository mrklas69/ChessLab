"""FastAPI aplikace ChessLab — webové UI nad lokálním Python backendem."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from chesslab import __version__

# FastAPI instance — to je hlavní objekt, který uvicorn umí spustit.
# title se zobrazí v /docs (auto-generated OpenAPI dokumentace).
app = FastAPI(title="ChessLab", version=__version__)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Placeholder homepage. Později nahradíme Jinja2 templatem se šachovnicí."""
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <title>ChessLab</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }}
        h1 {{ border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }}
        code {{ background: #f4f1e8; padding: 2px 6px; border-radius: 3px; }}
    </style>
</head>
<body>
    <h1>♔ ChessLab v{__version__}</h1>
    <p>Skeleton běží. Zatím jen tahle stránka a <a href="/docs">/docs</a> (OpenAPI).</p>
    <p>Další krok: migrovat UI z <code>Chess/06_play/play.py</code>.</p>
</body>
</html>"""


@app.get("/health")
def health() -> dict[str, str]:
    """Health-check endpoint pro budoucí monitoring / deploy probes."""
    return {"status": "ok", "version": __version__}
