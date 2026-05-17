# DONE

Hotové úkoly. Nejnovější nahoře.

## 2026-05-17 — Engine arena (`/arena`)

- **Backend** `src/chesslab/arena.py` (nový modul) — `EngineConfig` (path + skill), `ArenaConfig` (engine A, B, n_games 1-20, time_per_move 0.05-2.0), `GameResult` per partie (white_name, white_is `'a'`/`'b'`, result, termination, plies, full PGN se Seven Tag Roster), `ArenaResult` (agregát W/L/D, score, score %, perf_rating_diff). `run_arena()` drží persistentní enginy přes celý batch (try/finally pro cleanup), alternuje barvy (sudé partie A=bílý, liché B=bílý), `Skill Level` aplikuje jen pokud engine UCI option má (`engine.options` check — custom engine bez Skill Level přežije). Safety net `MAX_PLIES_PER_GAME = 600` proti zacyklené koncovce. Perf rating: `-400 × log10(1/score_rate - 1)`, vrací `None` pro 0%/100% skóre (logaritmus by dělil 0).
- **Endpoint** `POST /api/arena/run` — vstup `ArenaConfig`, výstup `ArenaResult`. Pydantic validace drží limity (n_games 1-20, time 0.05-2.0s, skill 0-20). FileNotFoundError → 500 (chybějící binárka). Sériový batch — pro 20 partií × ~3s = ~60s blokujícího requestu.
- **Frontend** `templates/arena.html` — dva engine boxy (path text + skill slider 0-20), pak N partií + čas/tah slidery. Run button + status řádek s fake progress (`Hraju… 4.2s / ~18s`). Po dokončení agregátový panel (score line A vs B, W/L/D, perf rating diff, čas/partie) + tabulka partií (color-coded result podle a/b, termination, plies, per-game PGN download přes Blob — žádný server-side state).
- **Index** updated — Engine arena přesunuta z „Brzy přijde" do Features. README touchnut nebyl (zaktualizuje se s další iterací).
- **Vědomě vyloučeno z MVP**: live board během běhu (SSE/polling), background tasks pro 100+ partií, custom UCI options kromě Skill Level, `ucinewgame` mezi partiemi (python-chess SimpleEngine to public API nevystavuje — engine si nese hash z předchozí hry). Sezení odhalilo drobné UI nálezy → viz TODO „Engine arena — polish".

## 2026-05-17 — Hraní proti Stockfish (`/play`)

- **Backend** `src/chesslab/play.py` (nový modul) — `GameState` dataclass (board + persistent Stockfish + player_color + skill + think_time + resigned + Lock), modulový singleton, akce `start_game()` / `apply_player_move()` / `undo_last_move()` / `resign_game()` / `get_pgn_download()`. Persistent engine přes celou hru (spawn-per-tah = 100-200ms lag), `engine.configure({"Skill Level": N})`. Auto-promote na dámu (UCI fallback `+ 'q'`). PGN se Seven Tag Roster pro download.
- **Endpointy** v `app.py`: `GET /play`, `POST /api/play/start` (color + skill + think_time, engine táhne první když hráč=černý), `POST /api/play/move` (from/to, validace + engine reakce sériově), `POST /api/play/undo` (pop 2 plies / 1 pokud jediný), `POST /api/play/resign`, `GET /api/play/pgn` (download). Pydantic modely s `from`/`to` aliasy (Python keyword).
- **Frontend** `templates/play.html` — chessboard.js board (`onDragStart` zakáže drag mimo hráčův turn, `onDrop` validuje přes backend), settings panel (color radio + skill slider 0-20 + think_time slider 0.1-2.0s, auto-scaling `0.1 + skill × 0.045` default), sidebar (status, last_move, PGN list, akční tlačítka). **Show eval** toggle (eval bar) a **Recommended move** toggle (zelená best-move šipka) — dva nezávislé checkboxy, fetch jen pokud aspoň jeden ON, render selektivně. **Highlight posledního engine tahu** lichess-style (box-shadow inset tint na from/to `.square-XX`). **„Analyzovat partii →"** tlačítko enabled po game_over → uloží PGN do `localStorage['chesslab-pending-pgn']` → otevře `/pgn` v novém tabu. `/pgn` na load přečte storage, naplní textarea, trigger `loadPGN()` + `analyseGame()`.
- **Two-phase animace** pro rošádu / en passant / promoci — backend vrací `fen_after_player_move`, klient animuje 1) na mezistav, 2) po 250ms na finál (po engine reakci). chessboard.js v `onDrop` vidí jen pohyb krále/pěšce, o věži/vzatém pěšci e.p. neví → bez tohoto fix se vše animuje současně s engine tahem.
- **Index** updated — `/play` link v Features, removed z „Brzy přijde". README rozšířen.

## 2026-05-17 — Eval graf přes partii

- **Backend** `POST /api/engine/analyse_game` — vstup list FEN, výstup list `{ply, score_cp, mate_in}` + `total_time`. **Persistent Stockfish** v rámci requestu (open 1×, sériová analýza N pozic, close) — pro 80 pozic řádově rychlejší než spawn-per-position. Default `time_per_move=0.3s` (limit 0.05–2.0), max 400 pozic.
- **Engine refactor** — extrakce `_stockfish_path_or_raise()` + `_score_to_cp_mate()` helperů (DRY, sdíleno mezi `analyse_fen` a `analyse_game_fens`). Nový model `PositionEval` (lehký, bez best_move, s `game_over` flagem pro koncové pozice).
- **Frontend** — tlačítko „Analyzovat partii (0.3s/tah)" pod layoutem. SVG graf 100% × 140 px, padding 6/8 px, středová zero line, křivka brown `#5a3a1a`, vertikální cursor sleduje `state.ply`. Klik na bod = `setPly()`. **Blundery** (drop > 150 cp z pohledu hráče, který tahnul) jako červené body s tooltip `ply N · SAN · ±X.X (BLUNDER)`. Mate clamp ±1000 cp (izomorfně s eval barem).
- **Fake progress** v status řádku (`Analyzuji… 3.2s / ~24s`) — backend nemá streaming, počítáme `elapsed / N×0.3`.
- **ResizeObserver** na SVG → re-render při změně šířky okna (souřadnice v px).
- README aktualizován.

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
