# DONE

Hotové úkoly. Nejnovější nahoře.

## 2026-05-17 — Stockfish analýza: vizuální komponenty

- **Eval bar** — svislý 24×420 px sloupec vlevo od šachovnice. Lineární škála ±1000 cp → 0–100 % bílé, mate = plný extrém. Číselný popisek uvnitř (`+1.5` / `-M3`), barva textu podle převahy. Smooth `transition: height 0.25s`.
- **Best move šipka** — SVG overlay nad boardem, `<line>` + marker (lichess-zelená `#15781B`, opacity 0.7). Souřadnice čtené z `getBoundingClientRect()` reálných `.square-XX` elementů (chessboard.js má 2px border → konstantní vzorec by šipku posunul).
- **Auto-trigger** — checkbox „Auto" vedle tlačítka analýzy. Při zapnutí spustí analýzu, `setPly()` ji opakuje při každé změně pozice. **AbortController** ruší rozjetý fetch při rychlém proklikávání → UI nebliká starou odpovědí. Backendový Stockfish dojede do konce sám (persistent engine vyřešíme až bude potřeba).
- README aktualizován o nové features.

## 2026-05-17 — PGN viewer + Stockfish analýza (textový výpis)

- **PGN viewer end-to-end** (TODO → DONE):
  - Backend: `chesslab.pgn.parse_pgn` + `POST /api/pgn/parse` (PGN → headers + lineární mainline tahy s FEN po každém půltahu).
  - Jinja2 templates: `base.html` (sdílený layout), `index.html` (rozcestník — nahradil placeholder homepage), `pgn.html` (chessboard.js z CDN, textarea, move list, klávesy ←/→/Home/End, Ctrl+Enter = Load, klikatelné tahy).
  - Figury z jsdelivr GitHub mirror (`cdn.jsdelivr.net/gh/oakmac/chessboardjs@1.0.0/website/img/chesspieces/wikipedia/`) — npm package chessboard.js figury **neobsahuje**, jen JS+CSS.
- **Stockfish analýza** (částečně — textový výpis hotov, vizuální komponenty v TODO):
  - `chesslab.engine.analyse_fen` (spawn-per-request přes `SimpleEngine.popen_uci` v `with` bloku — auto-cleanup, žádný leak procesů).
  - `POST /api/engine/analyse` — FEN + time (0.1–10s) → eval (cp / mate) + best move (UCI+SAN) + depth.
  - UI v `pgn.html`: tlačítko „Analyzovat pozici (Stockfish 1s)", barevně rozlišený výpis (kdo má výhodu), auto-clear při změně tahu.
- `.claude/` přidáno do `.gitignore` (lokální Claude Code metadata, nepatří do repa).

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
