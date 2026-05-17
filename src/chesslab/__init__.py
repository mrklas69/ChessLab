"""ChessLab — osobní šachová laboratoř."""

__version__ = "0.1.0"


def main() -> None:
    """Console entry point: spustí dev server přes uvicorn.

    Volá se z CLI jako `uv run chesslab` (viz pyproject.toml [project.scripts]).
    Port a host čteme z env, defaulty rozumné pro lokální vývoj.
    """
    # Lazy import — uvicorn potřebujeme jen při spuštění serveru,
    # ne když někdo importuje chesslab kvůli __version__ apod.
    import os
    import uvicorn

    host = os.environ.get("CHESSLAB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHESSLAB_PORT", "8765"))

    uvicorn.run(
        "chesslab.app:app",
        host=host,
        port=port,
        reload=True,  # auto-reload při editu zdrojáků (dev mode)
    )
