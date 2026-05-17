# DONE

Hotové úkoly. Nejnovější nahoře.

## 2026-05-17 — Bootstrap projektu

- Diskuze konceptu, vymezení scope (online multiplayer mimo scope, focus na analýzu + engine arena + Lichess API).
- Volba názvu **ChessLab** (původní pracovní „ChessHub").
- Volba stacku: Python 3.14 + FastAPI + uvicorn + python-chess + vanilla JS + chessboard.js + SQLite + uv.
- Inventarizace existujících šachových adresářů (`Chess/`, `ChessTest/`, `latex-chess/`) → strategie A: konsolidace přes referenci, ne kopírování.
- Instalace `uv` (pip --user), přidání Python user Scripts do User PATH.
- `uv init --package` skeleton.
- Závislosti přidané přes `uv add` (fastapi, uvicorn[standard], python-chess, jinja2, httpx).
- Minimální FastAPI app s placeholder homepage + `/health`.
- Skeleton dokumentace: `CLAUDE.md` (s override maker %BEGIN/%END), `README.md`, `TODO.md`, `DONE.md`, `IDEAS.md`.
- Rozšířený `.gitignore`.
- První git commit.
- GitHub repo `ChessLab` (public) + push.
