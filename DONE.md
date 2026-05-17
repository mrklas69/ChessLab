# DONE

Hotové úkoly. Nejnovější nahoře.

## 2026-05-17 — Engine v2.5: endgame heuristika (king tropism + edge distance)

- **`_endgame_bonus(board) -> int`** v `minimax_engine.py` — bonus pro silnější stranu v koncovce. Threshold: total non-pawn non-king material ≤ 1300 cp (pod tím se zapne). Jen pro stm s material balance ≥ 100 cp (= aspoň pawn převaha) — slabší strana bonus nemá. Dvě složky: (a) edge distance soupeřova krále od centra × 12 (max +36 v rohu), (b) (8 - king distance) × 3 (max +21 v opozici). Strop ~57 cp = pod hodnotou pěšce → material calculus stále dominuje, bonus rozhoduje **jen** tie-break tahů krále.
- **Integrace**: volá se na konci `_evaluate_for_side_to_move`. Vrací 0 mimo endgame nebo bez převahy → middlegame eval čistě materiální, žádná deformace.
- **Verze bump**: `v2` → `v2.5`. Display name v `_KNOWN_ENGINES` + `ENGINE_NAME`. Console script `chesslab-minimax` (in-place upgrade — pokud chceme historický v2.0 baseline, je v gitu commit `cec0d0c..` před tímto).
- **Smoke test Aréna** (Minimax v2.5 vs Greedy v1, 20 partií, 0.05s/tah):
  - **18W-0L-2D, 95 %, perf rating +511.5 Elo (exact)** — proti v2.0 (+190.8 Elo): **skok +320 Elo** jen endgame heuristikou.
  - **Draw rate 30-50 % → 5 %**. 18× CHECKMATE (vč. 151/168-tahových KR-vs-K, které engine teď dotahuje), 1× INSUFFICIENT_MATERIAL, 1× SEVENTYFIVE_MOVES (75-tahový limit bez progresu).
- **Quick unit test** před arenou: KR-vs-K white-to-move (slabší král na d7, silnější na d5, věž d1) → bonus 42; stejná pozice, černý na tahu (loser) → 0; choose_move v KR-vs-K (Ke3+Rh1 vs Ke6) → Rh6 (rook cut-off, legit mating technique). Heuristika se aktivuje a netvoří middlegame artefakty (startpos = 0).
- **Vědomě vyloučeno z v2.5** (zaznamenáno IDEAS): scaling endgame_weight (binární threshold stačí pro většinu KR/KQ pozic), passed pawn bonus (pawn endgames jsou jiná dynamika — kandidát na v2.6), KP-vs-K specifické tablebase scoring.

## 2026-05-17 — Engine v2 Minimax + alpha-beta (depth 2)

- **Engine v2 Minimax** (`src/chesslab/engines/minimax_engine.py`) — negamax framework s alpha-beta pruningem, fixní hloubka `_DEPTH = 2` (vidíme náš tah + soupeřovu odpověď). Eval-from-side-to-move (standard pro negamax — odpadá duplikace max/min logiky), eval funkce sdílí konstanty s Greedy v1 (Kaufman material + `_CHECK_BONUS = 30`, `_MATE_SCORE = 100_000`) — záměrně, aby srovnání v2 vs v1 měřilo **jen** přínos alpha-beta search, ne změnu eval. Top-level loop unrolled (potřebujeme zpět tah, ne jen skóre). Random tie-break z best moves (deterministický engine v Aréně = repetition draws). console_script entry `chesslab-minimax`, registrován v `_KNOWN_ENGINES`.
- **Vědomě vyloučeno z v2.0** (každé +50-200 řádků, kazí čistou izolaci přínosu α-β): move ordering (MVV-LVA), quiescence search, iterative deepening + TT, mate-distance scoring, configurable depth přes UCI option (sdílený `_protocol.run_uci_loop` options nepodporuje), positional eval. Vše motivace pro v2.1+ — viz IDEAS.
- **Smoke test v Aréně** (time_per_move 0.05s, 10 partií, alternace barev):
  - **Minimax v2 vs Greedy v1**: 6W-1L-3D, score 7.5/10 (75 %), **perf rating +190.8 Elo** (exact, ne bound). 8/10 partií skončilo matem (CHECKMATE), 3/10 INSUFFICIENT_MATERIAL — engine v koncovkách bez progresivní heuristiky stále občas patuje.
  - **Minimax v2 vs Random v0**: 10-0 sweep, vše matem, **perf rating ≥ +511 Elo** (lower bound). Konzistentně tranzitivní s Greedy vs Random (+301 Elo) → Minimax o ~+210 Elo nad Greedy v0 baseline.
- **Pod odhadem +400-600 Elo nad Greedy** (jen +191). Příčina viditelná v PGN: Minimax dělá taktické chyby 3+ plies hluboko (např. Qd5 → Qa4+ → Qb5 → Bxb5+ je 4-ply forced sekvence, kterou depth 2 nevidí — klasický horizon effect). Quiescence / depth 3 jsou jasné next steps; zaznamenáno v IDEAS.
- README aktualizován o v2 řádek v sekci Vlastní enginy.

## 2026-05-17 — Engine v1 Greedy + Arena dropdown + display name registry

- **Engine v1 Greedy Material** (`src/chesslab/engines/greedy_engine.py`) — 1-ply lookahead nad materiálem. Kaufman piece values v centipawnech (P=100, N=320, B=330, R=500, Q=900, K=0). Random tie-break ze sady tahů s max eval (deterministický engine = nuda + repetition draws). Bonus heuristiky: mate (+100000), stalemate (-100000), check (+30). Bez bonusů Greedy konzistentně **patoval Random** v KvK koncovce (10/10 remíz!), s bonusy **7-0-3 vs Random = +301.3 Elo**. console_script entry `chesslab-greedy`, registrován v `_KNOWN_ENGINES`.
- **UCI loop refactor** — sdílený `chesslab/engines/_protocol.py` (`run_uci_loop(name, author, choose_move_fn)`). Random a Greedy moduly se zkrátily na ~20 řádků wrapper kolem callbacku. Žádný code duplication mezi enginy.
- **Aréna UI dropdown** (`templates/arena.html`) — dva `<select>` (Engine A, B) nad path inputem, načítají z `/api/engines/list`. Při změně dropdownu se path input naplní + skill slider se dim (CSS `.skill-disabled` opacity 0.4) když engine `supports_skill = false`. Path input zůstává editovatelný pro custom UCI binárky mimo discovery.
- **Display name fix** — `chesslab/engines/__init__.py` exportuje `display_name_for_path(path) -> str | None` (registry lookup: 'stockfish*' → 'Stockfish', `_KNOWN_ENGINES` → registrované jméno). `arena._engine_display_name()` ho používá přednostně před path-based heurystikou. Výsledek: ArenaResult i PGN headers ukazují „ChessLab Greedy v1" místo „chesslab (skill 0)". Suffix „(skill N)" se přidává jen pro skill-aware enginy (Stockfish). Vyřazené 2 drobnosti z TODO.

## 2026-05-17 — Engine dropdown na `/play` + auto-discovery

- **Discovery** v `chesslab/engines/__init__.py` (`list_available_engines()`): Stockfish first (jen pokud binárka na disku), pak auto-glob `chesslab-*` v `sysconfig.get_path("scripts")` aktivního venv. `EngineInfo` Pydantic model s flagem `supports_skill` (Stockfish ano, vlastní zatím ne — drží mapa `_KNOWN_ENGINES`).
- **Endpoint** `GET /api/engines/list` vrátí seznam pro UI dropdowny.
- **Backend `play.py`** — `GameState` rozšířen o `engine_name` (= UCI handshake `id name`, např. „Stockfish 16.1 by ..." nebo „ChessLab Random v0") a `engine_supports_skill`. `start_game()` přijímá optional `engine_path` (None → default Stockfish), `engine.configure({"Skill Level": ...})` jen pokud option k dispozici (`if "Skill Level" in engine.options`). `_status_text` a `_pgn_full` používají dynamic engine name (PGN header `[White/Black "<name>"]` s případným suffixem „(skill N)" jen pro skill-aware enginy).
- **Frontend `play.html`** — `<select id="engine">` jako první item v Settings, načítá z `/api/engines/list` při load (default = first option = Stockfish). `onChange` přepíše `state.engineName/enginePath/engineSupportsSkill`; když `!supports_skill` → třída `.skill-disabled` dimne slider (CSS opacity 0.4 + pointer-events none). `startGame()` posílá `engine_path` v payloadu. „Stockfish: <move>" v last-move textu nahrazeno za `state.engineName + ': ' + ...`.
- **Smoke test**: `/api/engines/list` vrátí 2 enginy s correct flags. Start hry s `chesslab-random.exe` → engine na e4 odpoví h6 (random tah). PGN header `[Black "ChessLab Random v0"]` bez „(skill)" suffixu (správně, Skill Level Random nemá).
- README aktualizován („Hra proti enginu" + auto-discovery zmínka). Drobnost z TODO drobností o display name v Areně zůstává otevřená (UI Areny dropdown ještě nedostala — separate iterace).

## 2026-05-17 — Vlastní engine v0: Random Mover (UCI standalone)

- **Nový subpackage** `src/chesslab/engines/` (`__init__.py` + `random_engine.py`). Random Mover = náhodný legální tah, žádná evaluace, žádný search. Účel: baseline pro Arenu + dogfooding UCI integrace.
- **UCI handshake** — minimální spec: `uci` (id name + id author + uciok), `isready` (readyok), `ucinewgame` (reset boardu), `position` (parser pro startpos/fen + optional moves), `go` (ignoruje všechny parametry, vrátí náhodný legální tah okamžitě), `quit`. Ostatní příkazy (stop, ponderhit, setoption, ...) tiše ignorujeme. `print(..., flush=True)` proti stdout bufferingu. `bestmove 0000` defenzivně pro pozici bez legálních tahů.
- **Console script entry** `chesslab-random = "chesslab.engines.random_engine:main"` v `pyproject.toml`. `uv sync` vygeneruje `.venv\Scripts\chesslab-random.exe` (Windows). Plnohodnotný UCI binary kompatibilní se SimpleEngine.popen_uci → Arena UI ho najde jako každou jinou binárku.
- **Smoke test** v Areně: Random vs Stockfish skill 0, 4 partie, time 0.05s. Výsledek 0-4 sweep pro Stockfish (4× CHECKMATE), perf rating `-338.0 Elo` s `is_bound: true`. Žádný UCI hang / parser error. Mat v 7 plies v partii #2 (Random rozkýval Scholar's Mate variant).
- **README** rozšířen o sekci „Vlastní enginy" s cestou k binárce. Identifikováno (drobnost, ne fix): `_engine_display_name` z `chesslab-random` extrahuje jen `chesslab` (split na první `-`) — sub-optimal, viz TODO drobností.

## 2026-05-17 — Arena: default matchup 5 vs 15 → 5 vs 8

- `SKILL_DEFAULT_B` v `arena.py`: 15 → 8. Důvod: 5 vs 15 dával vždy sweep (0:N nebo N:0) a tím jen lower-bound perf rating; 5 vs 8 je dost asymetrické, aby silnější vyhrál většinu partií, ale slabší občas remizoval/vyhrál → perf rating dá přesnou hodnotu už od první spuštění (víc užitečná metrika než demonstrace převahy). Přesunuto z IDEAS.md.

## 2026-05-17 — Engine arena polish (4 nálezy)

- **Tabulka partií „Tahy" počítá full moves**, ne plies (`Math.ceil(plies/2)` ve frontendu). Backend dál posílá `plies` (čistší primitivum), UI je převede na šachistickou konvenci tah = pár W+B. Soubor `templates/arena.html`.
- **Odhad času ve status řádku 30 → 65 plies/partii.** Konstanta `AVG_PLIES_PER_GAME = 65` (chyba 2× opravena, fake progress je teď v rozumném řádu).
- **Lower-bound perf rating pro 100% / 0% skóre** místo holého „N/A". Backend `_perf_rating_diff` vrací `(value, is_bound)` tuple — pro extrémní výsledek použije `(score ± 0.5)/n_games` trick (jakoby A udělal/dostal o jednu remízu navíc) → dolní/horní mez. Přidán field `perf_rating_is_bound: bool` do `ArenaResult`. UI prefixuje „≥ +X Elo" nebo „≤ -X Elo". `null` zůstává jen pro `n_games=0` edge.
- **UCI `ucinewgame` přes respawn enginů per partii.** python-chess `SimpleEngine` `ucinewgame` veřejnou API nevystavuje — nejjednodušší cesta k čisté izolaci je `popen_uci` + `quit` uvnitř smyčky nad partiemi. Overhead ~200ms × N (4s pro N=20, ~7% pro N=2 × krátký time_per_move). Refactor `run_arena()`: `try/finally` per game zaručuje cleanup i při výjimce uprostřed partie. Aktualizovány docstrings modulu a `_play_one_game()`.
- **Smoke test**: 2× Stockfish skill 20 vs 0, score 2-0 pro A, `perf_rating_diff=190.8` s `perf_rating_is_bound=true`, alternace barev korektní.

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
