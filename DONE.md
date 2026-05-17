# DONE

Hotové úkoly. Nejnovější nahoře.

## 2026-05-17 — UX vlna: audio v `/pgn`, per-skill Stockfish rating, inkrementální Lichess import, klikatelné klasifikační pilly

Čtyři menší navazující iterace v jednom sezení — všechny UX dotahování existujících features (žádné nové subsystémy).

### 1) Audio + materiálová badge + mute toggle v `/pgn`

- **Cíl**: dotáhnout audio partial `_chesslab_audio.html` z `/play` do `/pgn` step-by-step vieweru (SLAP / izomorfismus). Step navigace ← / → → zvuk, materiál badge, mute toggle.
- **Integrace** v `pgn.html`:
  - Include partial v `{% block script %}` (před vlastní inline JS).
  - **Info-bar pod boardem v levém sloupci** (ne v pravém sidebaru jako `/play`) — `<span class="material-badge">` + `<button class="mute-btn">`. Vizuálně blízko šachovnice: material balance = info o pozici, mute = ke zvukům tahů. CSS kopie z `play.html`.
  - `updateBoard()` rozšířena o `updateMaterialBadge(fen)` → badge sleduje každý setPly + initial load.
- **Audio logika v `setPly(n)`**:
  - Zaznamená `oldPly`, spočítá `newPly`. Audio se hraje **jen** pokud `newPly !== oldPly && newPly > 0`.
  - Lichess-style: skok přes víc tahů (klik na ply uprostřed, End, Home) hraje **jen** zvuk posledního tahu, ne sérii.
  - **End zvuk**: pokud `newPly === max && headers.Result !== '*' && !san.endsWith('#')` → `setTimeout(playEnd, 300)`. Bez `'*'` kontroly by hrál end u rozehrané partie; bez `'#'` kontroly by duplikoval s mate sound z `playForSan`.
  - Home (= setPly(0)) → ticho (`newPly > 0` filter).
- **Smoke test**: `/pgn` HTTP 200, partial included, `ChessLabAudio` + `ChessLabMaterial` + `playForSan` + `info-bar` v HTML. Reálné audio v browseru je manuální test (Chrome AudioContext vyžaduje user gesture).

### 2) Per-skill Stockfish rating vedle slideru v `/play` + `/arena`

- **Problém**: dropdowny zobrazují ChessLab Elo jen pro non-skill enginy (`(~1450 Elo)` v option labelu). Stockfish skill-aware má per-skill rating (`stockfish:0` ... `stockfish:20` v `engine_ratings`), v UI neviditelný.
- **Řešení**: žádný nový endpoint — frontend si fetchne existující `GET /api/engines/ratings` (paralelně s `/api/engines/list` přes `Promise.all`), postaví mapu `engine_id → rating` a při změně skill slideru ji projde.
- **Implementace** (oba templates):
  - HTML: `<span class="rating-hint" id="skill-{a,b}-rating-hint">` vedle existujícího `value-label` skill slideru. CSS: `color: #5a3a1a`, monospace, malé.
  - JS:
    - `state.engineRatings = {}` (resp. globální v `arena.html`).
    - Option dostane `data-engine-id="<id>"` — kanál pro lookup po výběru.
    - `updateSkillRatingHint()` / `updateRatingHint(suffix)`: pokud vybraný engine ne-skill-aware nebo nevybraný → prázdné. Jinak `<engineId>:<skill>` lookup → buď `(~1500 Elo)` nebo `(no rating)` pro neexistující záznam (Stockfish s tím skillem ještě nehrál).
    - Volá se z: load engines (initial), `updateEngineSelection` (change dropdown), `skill input` (slider posun).
- **Smoke test**:
  - `/api/engines/ratings` aktuálně vrací `stockfish:5=1500, chesslab-minimax=1128, chesslab-greedy=654, chesslab-random=453`.
  - Pro skill 5 hint `(~1500 Elo)`, pro skill 0-4 a 6-20 `(no rating)` dokud Stockfish na tom skillu nezahraje (vyřeší se přirozeně přes `/arena` runy).
  - `/play`, `/arena` HTTP 200, hint elementy v HTML, `engineRatings` v JS.

### 3) Inkrementální Lichess import (default ON, `since=max+1ms`)

- **Cíl**: zbytečně nestahovat partie, které už v DB jsou. Lichess API podporuje `since=<ms_epoch>` query parametr (vrátí jen partie s `createdAt > since`).
- **Backend**:
  - `lichess.py`: `fetch_user_games(username, max_games, since: int | None = None)` — pokud `since`, přidá do params dictu.
  - `games.py`:
    - Nový helper `latest_lichess_created_at(username) -> int | None` (`SELECT MAX(created_at) FROM games WHERE source='lichess' AND username=? AND created_at IS NOT NULL`). Filtruje per `source='lichess'`, protože chess.com má jiné ID konvence (nechceme aby chess.com timestamp ovlivnil Lichess `since`).
    - `import_lichess_user(username, max_games, force_full=False)`:
      - Pokud `not force_full`: `since = latest_lichess_created_at(...) + 1` (+1 ms aby hraniční partie nepřišla znovu).
      - Pokud user v DB prázdný → `since=None` → full fetch.
      - Předá `since` do `fetch_user_games`.
    - `ImportResult` rozšířen o `since: int | None` + `incremental: bool` (echo pro frontend).
  - `app.py`: `LichessImportRequest.force_full: bool = False`, předáno do `import_lichess_user`.
- **Frontend** `import.html`:
  - Checkbox "Force full re-sync" pod sliderem (default OFF).
  - Form posílá `force_full`.
  - Renderer rozšířen: tři scénáře headline (`+X nových` / `Žádné nové partie (vše už staženo)` / `0 nových (X už v DB)`) + mode badge `inkrementál` (zelený pill) / `full sync` (žlutý pill) + datum `since` v lokálním formátu (`cs-CZ`).
- **Smoke test** (TirelessWoodpusher, 100 partií v DB):
  - Před fixem (= staré chování): `fetched:50, inserted:0, skipped_existing:50` (50 partií zbytečně zatáhnuto + 50× SQL no-op).
  - Po fixu inkrementál (default): `fetched:0, inserted:0, since:1720645025540, incremental:true` (0 partií staženo — Lichess to filtruje serverside).
  - Force full: `fetched:5, inserted:0, skipped_existing:5, since:null, incremental:false` (správně se vrací k starému chování).
- **Server restart pozn.**: `uvicorn --reload` nezachytí změnu signature `fetch_user_games` (přidaný `since` keyword arg) — symptom: response neobsahuje nové fields `since`/`incremental` i po editu. Per memory `feedback_uvicorn_reload_limit`: musí se zastavit + restartovat. Killnout obě úrovně procesů (uvicorn master 29552 + multiprocessing worker 42524) přes `Get-NetTCPConnection` lookup + `Stop-Process -Force`, pak `uv run chesslab` přes Bash background.

### 4) Klikatelné klasifikační pilly + tagy v `/pgn`

- **UX**: souhrn `35 nejlepší · 5 dobrý · 9 nepřesnost · 3 chyba · 2 hrubka` byl pasivní info. Teď klik = navigace.
- **Pilly**: každý dostane `data-cls` + `cursor: pointer` + hover (`filter: brightness(0.92)`).
- **Smart cycle** `jumpToNextOfClass(cls)`: ze `state.classifications` projde záznamy s `classification === cls`, sortne ply ascending, najde **první `> state.ply`** (= "další výskyt po kurzoru"); pokud žádný → wrap na nejnižší. Opakovaný klik logicky pokračuje — `setPly` mezitím posunul `state.ply`, takže další lookup najde další.
- **Tagy v move listu** (`?!`, `?`, `??`, ✓): cursor `help` → `pointer` (klikatelné). Klik handler bind explicit (tag je **sibling** `.move` spanu, ne child — klik default nebubla na move). `setPly(ply)` přes `data-ply`.
- **Smoke test**: `/pgn` HTTP 200, `jumpToNextOfClass` + `data-cls` + `cursor: pointer` na pillech ověřeno v rendered HTML.

### Vědomě vyloučeno z této vlny

- **Audio v `/arena`** — rychlá série tahů by byla otravná (per IDEAS). `/games` stačí handoff do `/pgn` (zdědí audio tam).
- **Promotion zvuk** (`=Q` zatím jako move) — IDEAS.
- **Volume slider** vedle mute — hardcoded volumes 0.12-0.20 stačí, do "moc nahlas" feedbacku.
- **Username filtr v `/games`** — TODO, ale dropdown distinct usernames z DB se hodí až bude > 1 importovaný user.
- **Per-class indicator v graph dots** — graf už zvýrazňuje blunders (red), ale pilly by mohly mít cross-link do grafu (highlight all blunders při hoveru). Nice-to-have.

## 2026-05-17 — Audio + materiálová rovnováha + mute toggle (`/play`)

- **Cíl**: zvuková zpětná vazba na tahy (lichess-style click / capture / check / mate) + průběžná indikace materiálové rovnováhy + persistentní mute toggle, vše bez extérních asset (žádné .mp3/.wav v repu).
- **Nový partial** `src/chesslab/templates/_chesslab_audio.html` — Jinja include s jediným inline `<script>` blokem, žádné závislosti (ani na chessboard.js / jquery). Exposed API pod `window.ChessLabAudio` (audio + mute) a `window.ChessLabMaterial` (FEN → balance). Záměrně globals — partial se hodí kamkoli, kde je potřeba audio, jen include + bind. DRY: až ho zapojím i do `/pgn` / `/arena`, sdílí se stejný JS.
- **Web Audio synthesis** (žádné mp3) — Lazy `AudioContext` při prvním přehrávání (Chrome autoplay policy: AudioContext nesmí vzniknout bez user gesture). Helper `tone(freq, duration, type, volume)` → oscillator + gain envelope (5ms attack + exponential decay) = krátký "click", ne hučení. Zvuky:
  - **Move**: sine 200 Hz / 60 ms, vol 0.18 — subtilní click.
  - **Capture**: square 240 Hz / 70 ms + sine 180 Hz / 70 ms (40 ms gap) — "úder".
  - **Check**: triangle 660 Hz / 150 ms, vol 0.18 — alert tón.
  - **End** (mat / draw / resign): descending sine 440 → 330 → 220 Hz (A4 → A3 cca).
- **SAN classifier** `playForSan(san)` — priority `# > + > x > move`. Promotion `e8=Q` jde jako move (zvuk při promoci by chtěl samostatný tier, nice-to-have). Castle `O-O`/`O-O-O` jde jako move (žádný `x`/`+`/`#` v default kingside castle).
- **Materiálová rovnováha** `ChessLabMaterial.fromFen(fen) → {white, black, diff}` v pawn ekvivalentu (P=1, N=B=3, R=5, Q=9, K=0 — lidštější než cp; user vidí "+2 ♔" namísto "+200 cp"). `formatBadge(balance)` produkuje `+2 ♔` / `−1 ♚` / `=` (typografická pomlčka, ne ASCII hyphen). Parsuje jen piece placement field FENu (split na první mezeru).
- **Mute toggle** s localStorage persistence (`chesslab-sounds-muted` = '0'/'1', default unmuted). `setMuted` dispatchuje `chesslab-mute-changed` CustomEvent → multi-instance synchronizace (pokud by partial běžel ve dvou tabech nebo s víc tlačítky). `bindMuteToggle(buttonId)` render `🔊`/`🔇` glyph + title + bind klik. Tlačítko žije v `play.html` `.info-bar` vedle material badge.
- **Integrace v `play.html`**:
  - Nová `.info-bar` v sidebaru mezi `.last-move` a `.moves` (materiál vlevo, mute vpravo).
  - CSS pro `.info-bar` / `.material-badge` (mono font, světlé pozadí jako settings panel — izomorfně) / `.mute-btn` (transparent + tan border, hover light tan).
  - V `applyResponse` audio synchronizováno s two-phase board animací: hráčův tah hraje hned s mezistavem (`fen_after_player_move`), engine tah hraje v 250ms setTimeout zároveň s finálním FEN renderem → zvuk vždy synchronní s tím, co vidím. Single-move case (mat po hráči / engine táhne první při hraní za černého) → jeden zvuk hned.
  - End zvuk při `game_over=True` pokud poslední SAN neměl `#` (jinak duplicit s mate sound). 300ms delay aby zazněl po engine tahu.
  - Material badge update z `data.fen` (finální stav po obou tazích) → `'Materiál: ' + formatBadge(...)`.
  - `ChessLabAudio.bindMuteToggle('mute-toggle')` po `loadEngines()` (sync, nemusí čekat na async fetch).
- **Smoke test** (uživatel ručně v browseru, OK):
  - Zvuky hrají při hráčově i engine tahu, capture / check / mat odlišitelné sluchem.
  - Mute persists přes F5 (localStorage).
  - Material badge mění hodnotu po každém braní.
- **Vědomě vynecháno** (zaznamenáno v IDEAS):
  - **Audio + badge v `/pgn` / `/arena` / `/games`** — partial je obecný, jen include + bind. V `/pgn` step-by-step viewer by zvuk byl smysluplný (každý ← / → = playForSan), v `/arena` spíš otravný (rychlá série), v `/games` při klik na řádek = handoff do `/pgn`, takže nepotřebuje vlastní.
  - **Promotion zvuk** — `=Q` nemá vlastní tier (hraje jako move). Hodilo by se: vyšší triangle pro promoci, kombinovaný s mate/check pokud zároveň.
  - **Volume slider** — momentálně hardcoded volumes (0.12-0.20). Pokud user řekne "moc nahlas", přidám slider vedle mute btn.

## 2026-05-17 — Round-robin turnaj + Bayesian Elo (Bradley-Terry MLE)

- **Cíl**: lepší konvergence rating systému než per-game FIDE Elo s K=40. Round-robin (každý engine vs každý) + batch refit ratingů přes maximum-likelihood Bradley-Terry s anchor a virtual draw priorou.
- **Schema** v `db.py` — nová tabulka `engine_matchups (engine_a_id, engine_b_id, wins_a, wins_b, draws, last_updated)` s composite PK + **kanonické pořadí** `a_id < b_id` lexikograficky (žádné duplikace A vs B == B vs A). Akumuluje se napříč všemi arenami i turnaji. INSERT OR REPLACE pattern: SELECT current → spočítej součet → UPSERT.
- **DB ops v `ratings.py`**: `MatchupRecord` Pydantic, `_canonical_pair(id_a, id_b) -> (canon_a, canon_b, swapped)`, `add_match_results(engine_a_id, engine_b_id, wins_a, wins_b, draws)` (canonicalize + UPSERT s wins swap pokud potřeba), `list_matchups()`.
- **Bradley-Terry MM (Minorization-Maximization)** v `ratings.py`:
  - `fit_bradley_terry_ratings(matchups, anchor_id, anchor_rating) -> dict[player_id, rating]`.
  - Algoritmus: pro každého hráče i, gamma_i_new = W_i / sum_j (n_ij / (gamma_i + gamma_j)), kde W_i = wins + 0.5 * draws (standardní generalizace pro remízy), n_ij = total games mezi i a j. Iteruj dokud max delta gamma < epsilon (1e-6), max 500 iterací.
  - **Anchor rescale**: po každé iteraci vynásob všechny gamma factorem `anchor_target / anchor_current_gamma` → anchor zůstane na svojí target rating (BT je inherently scale-invariant, fixní bod se drží přes rescale).
  - **Konverze**: gamma = 10^(rating/400), rating = 400 * log10(gamma). Stejná škála jako klasický Elo.
- **Virtual draw prior** (klíčové) — bez toho MM **diverguje na sweep matchupech** (gamma vítěze → ∞, poraženého → 0): smoke test odhalil rating "Minimax=377, Random=-980" po 4-engine sweep turnaji. Fix: před iterací enrichni `effective_matchups` o synthetic record s `_BT_PRIOR_DRAWS_PER_PAIR = 1` remízami mezi **každým párem hráčů** (i těmi co spolu reálně nehráli). Garantuje connectivity grafu + finite ratingy. Standardní pattern v BT implementacích (Bayeselo, choix).
- **`recompute_bayesian_ratings(engine_display_names)`** — re-fit ratingů z aktuální matchup matrice + persist do `engine_ratings`. Volá se z `run_tournament` po skončení (= refit z celé historie, nejen z tohoto turnaje).
- **Nový modul** `src/chesslab/tournament.py`:
  - `TournamentConfig` (engines: list[EngineConfig] 2-6, n_games_per_pair 2-20 default 10, time_per_move 0.05-2.0 default 0.05).
  - `MatchupSummary` per pár v tomto runu (W/L/D), `TournamentResult` s n_pairs/n_games_total/matchups/ratings/games/total_time.
  - `_run_match_between(a, b, n_games, time)` — n_games partií s alternací barev, respawn enginů per partii (sdíleno s arena.py přes `_play_one_game`, `_maybe_configure_skill`, `_engine_display_name`).
  - `run_tournament(config)` — for-loop přes všechny páry (i<j), per pár volá `_run_match_between` + `add_match_results`. Na konci `recompute_bayesian_ratings` z celé matrice + vrátí `list_ratings()` sestupně.
  - Limity: MAX_TOURNAMENT_ENGINES = 6 (15 párů × 10 partií = 150 partií × ~3s = ~7.5 min, request budget). Vyšší by riskovalo timeout.
- **Endpoint** `POST /api/tournament/run` v `app.py`. Synchronní, blokující. Status mapping: 500 pro FileNotFoundError a obecné engine chyby.
- **UI rozšíření `/engines`**:
  - Tlačítko "Spustit round-robin turnaj + Bayesian Elo refit" nahoře (default collapsed form).
  - Form: checkboxy per engine (Stockfish skill-aware má vlastní number input pro skill level), slider n_games_per_pair + time_per_move, Run button.
  - **Live estimate** vedle Run: `6 pár(ů) × 10 partií = 60 partií · ~195s (3.2 min)` — recomputuje při každé změně. UX: user vidí cenu před kliknutím.
  - Status řádek `Hraju turnaj… 32.1s / ~195s` (fake progress).
  - Po dokončení: nový panel s matchup tabulkou + refresh žebříčku.
  - `AbortSignal.timeout(600000)` (10 min hard cap pro frontend fetch).
- **Smoke test E2E**:
  - Manuální Bayesian test (synthetic data): A>B>C tranzitivně OK; **sweep test 4-engine** (3 ze 6 matchupů 4-0-0): Stockfish 1500 → Minimax 1268 → Greedy 1019 → Random 913 (finite, smysluplné — proti buggy verzi bez priorky 377/-789/-980).
  - Endpoint 4 enginy × 6 partií per pár = 36 partií, 78s: matchupy SF-vše sweep 6-0-0, Min-Greedy/Random sweep, Greedy-Random 3-0-3.
  - Po recompute: Stockfish 1500 (anchor, 30 partií) → Minimax 1117 (-383) → Greedy 725 (-775) → Random 576 (-924). Tranzitivně OK, finite, spread realistický (Minimax-Stockfish -383 = ~10% win rate ≈ sweep matchup).
  - Anchor zůstává na 1500 i po refit ✓.
- **Pozorování o ChessLab Elo škále**: Stockfish skill 5 = 1500 anchor je relativně silný anchor pro hierarchii custom enginů — všichni custom (Random/Greedy/Minimax v2.7) spadají do 576-1117 rozsahu. Pro absolute Elo bližší lichess kalibraci by chtělo slabší anchor (Stockfish skill 0 nebo Random=800), ale to by posunulo celou stupnici. Aktuální kalibrace zachovává tradiční "Stockfish ~1500" intuition.
- **Vědomě vyloučeno z této iterace** (zaznamenáno v IDEAS):
  - **Async background job** + polling endpoint — pro 5-min request je hraniční, ale frontend timeout 10 min stačí.
  - **Per-skill rating UI dropdown pro Stockfish** — viz předchozí iterace IDEAS.
  - **Rating chart over time** — historie ratingu engine přes všechny refity.
  - **Tunable prior** — _BT_PRIOR_DRAWS_PER_PAIR hardcoded na 1; vyšší (2-3) by dal větší regularization pro mini turnaje (2-3 partie per pair), menší (0.5) by uvolnil pro big batches.
  - **Confidence intervals** (Glicko-style RD) — Bayesian MM vrací jen point estimate, ne uncertainty. Pro UI badge "vysoká/nízká confidence" by chtělo bootstrap nebo Glicko model.

## 2026-05-17 — ChessLab Elo: persistent engine ratings

- **Cíl**: vidět přibližnou ELO sílu enginů (Random/Greedy/Minimax/Stockfish), ne jen perf_rating_diff per session. Hardcoded anchor + Elo update po každé aréně, persistent v SQLite, samostatná stránka /engines s žebříčkem.
- **Schema** v `db.py` — nová tabulka `engine_ratings (engine_id PK, display_name, rating, games_played, last_updated, is_anchor)`. Engine ID konvence: skill-aware → `<basename>:<skill>` (Stockfish skill 0 a 20 = separátní ratingy, jiná síla), non-skill → `<basename>` (Random/Greedy/Minimax = jeden rating per binárka). Anchor `is_anchor=1` má fixní rating (K=0 update).
- **Nový modul** `src/chesslab/ratings.py`:
  - `engine_id_from_path(path, skill, supports_skill)` — generátor ID s **normalizací Stockfish basename** (oficiální binárka `stockfish-windows-x86-64-avx2.exe` musí mapovat na `stockfish:5`, jinak by anchor nebyl detekován jako anchor a propadl by na K=40). Symetricky s `display_name_for_path` registry.
  - `engine_display_name(path, skill, supports_skill)` — '(skill N)' suffix jen pro skill-aware.
  - `init_anchor()` — `INSERT OR IGNORE` Stockfish skill 5 = 1500. Volá se z FastAPI **lifespan** hooku (idempotentní, re-start neměnenrating).
  - `get_rating`, `list_ratings` (sestupně podle rating), `_upsert_rating` (INSERT OR REPLACE).
  - `elo_update(my, opp, score, k)` — FIDE vzorec `new = my + k * (score - expected)`, expected ze standardní logistiky.
  - `_k_factor(games_played, is_anchor)` — 0 pro anchor, 40 pro `games_played < 30` (rychlá konvergence pro nový engine), 20 pro etablovaný (stability). Drží FIDE pattern.
  - `update_ratings_from_arena(engine_a_id, engine_a_name, engine_b_id, engine_b_name, games)` — per-game Elo update, K se přepočítá po každé partii (nový engine může v rámci 30-partií batch překlopit z K=40 na K=20). Nový engine (no DB row) startuje na rating soupeře (= score 0.5 expected, partie posune správným směrem). Vrací `(EngineRating_a_after, EngineRating_b_after)` pro echo v ArenaResult.
- **Integrace v `run_arena`**: na konci `update_ratings_from_arena` volání, ArenaResult dostal nové fields `engine_{a,b}_rating_{before,after}`, `engine_{a,b}_games_played`. Frontend zobrazí `1500 → 1530 (+30)`. Helper `supports_skill_for_path` v `engines/__init__.py` (registry lookup → bool).
- **Endpoint** `GET /api/engines/ratings` → `list[EngineRating]` sestupně. `EngineInfo` rozšířen o `rating: float | None` + `games_played: int` (naplní se z DB pro non-skill enginy, lazy import kvůli cyklu engines ↔ ratings).
- **Nová stránka** `/engines` + `templates/engines.html` — tabulka pořadí/název/rating/partie/datum. Anchor řádek má světlé pozadí + ⚓ glyph. Provisional engine (games_played < 30) má games count zlatě (vizuální flag low confidence). Intro panel vysvětluje "ChessLab Elo ≠ CCRL/lichess" (lokální kalibrace pro 0.05s/tah).
- **UI integrace**:
  - `/arena` výsledkový panel: 2 nové `.rating-line` řádky pod meta — `A · 1500 → 1530 (+30) (6 partií)`.
  - `/arena` + `/play` engine dropdown: option labels pro non-skill enginy zobrazí `(~1450 Elo)` pokud rating existuje. Pro Stockfish (skill-aware) chybí — per-skill rating chce samostatný endpoint, viz IDEAS.
  - `/` homepage: link na `/engines` v Features.
- **Bug fix během smoke testu**: před fixem `engine_id_from_path('stockfish-windows-x86-64-avx2.exe', 5, True)` vracel `stockfish-windows-x86-64-avx2:5`, ale anchor seed má hardcoded `stockfish:5`. `get_rating(stockfish-windows-x86-64-avx2:5)` → None → `is_anchor=False` → K=40 → po 6 partiích Stockfish skill 5 ztratil 209 Elo z anchoru. **Po normalizaci** (`if stem.startswith("stockfish"): stem = "stockfish"`) anchor zůstává fixní 1500. Cleanup buggy duplicitních entries z DB hand-made (jednorázové, není migration).
- **Smoke test**:
  - Initial: 1 rating (anchor 1500).
  - Arena Minimax v2.7 vs Stockfish skill 5 (6 partií, 0.05s/tah): Minimax 0W-6L-0D. Anchor zůstává **1500 (6 partií)** ✓, Minimax 1396 (start na opponent rating 1500, drop -104 přes 6 ztrát s K=40).
  - Arena Greedy v1 vs Random v0 (6 partií): Greedy 3W-0L-3D (75 %). Oba neznámí → start na _FALLBACK 1200. Greedy → 1249, Random → 1151.
  - Final žebříček: Stockfish 1500 (anchor) > Minimax 1396 > Greedy 1249 > Random 1151. Tranzitivně sedí (Minimax > Greedy > Random), ale **Minimax-Greedy diff jen 147 Elo** vs sweep 20-0-0 minimum +636 Elo. Důvod: K=40 + malé n (6) má vysokou volatilitu; po desítkách arén se rating srovná. **Důležité**: kdo chce přesnou hodnotu, musí pustit víc partií (≥30) — tehdy K klesne na 20 a rating se stabilizuje.
  - `GET /engines` → HTTP 200 (6290 chars), tabulka se renderuje.
- **Vědomě vyloučeno z této iterace** (zaznamenáno v IDEAS pro budoucí iterace):
  - **Per-skill rating pro Stockfish v dropdownu** — viz IDEAS. Vyžadovalo by samostatný endpoint a JS hook na skill slider change.
  - **Bayesian Elo / round-robin turnaj** — viz IDEAS "Engine sparring". Robustnější konvergence při menším počtu partií.
  - **Rating chart over time** — sledovat vývoj ratingu engine přes všechny arény. Pre-history je v `games_played` count, ale per-arena snapshots chybí.
  - **Anchor recalibration** — kdyby user změnil anchor (např. Stockfish skill 10 = 2000), celý žebříček by se posunul; aktuálně držíme jednu pevnou hodnotu. Re-anchor by chtělo "rescale all ratings proportionally".
  - **Verze custom enginu** — ChessLab Minimax v2.6 a v2.7 sdílí ID `chesslab-minimax`, jeden rating. Pro separation by chtělo verzi v binárce name (`chesslab-minimax-v27`).

## 2026-05-17 — Klasifikace tahů (Stockfish + lichess sigmoid + cache)

- **Schema** v `db.py` — nová tabulka `move_evals` (game_id, ply, eval_cp, mate_in, classification, analyzed_at, time_per_move) s composite PK `(game_id, ply)` + FK `ON DELETE CASCADE`. Idempotentní `CREATE IF NOT EXISTS` (per pattern celé schemata). Per-tah eval; ply=0 = startovní pozice, classification=NULL (žádný tah jí nepředchází). Per `INSERT OR REPLACE` upsert pattern → re-classify s jiným time_per_move přepíše záznamy.
- **DB ops** v `games.py`: `MoveEval` Pydantic model + `insert_move_evals(game_id, evals, time_per_move)` (batch transakce, model_dump → SQL params) + `get_move_evals(game_id) -> list[MoveEval]` + `has_classification(game_id) -> bool` (lehký LIMIT 1 check pro lookup-or-compute pattern).
- **Klasifikační modul** `src/chesslab/classifier.py` (nový):
  - **Lichess sigmoid** `_cp_to_winning_chances(cp, mate_in)` — `2 / (1 + exp(-0.00368208 * cp)) - 1`, vrátí [-1, +1] z perspektivy bílého. Mate hardcoded na ±1 (zjednodušení vůči lichess lineárnímu scale po mate_in — pro klasifikaci tahů rozdíl mate-in-1 vs mate-in-30 prakticky nedělá nic).
  - **`_cp_to_win_pct_white`** → [0, 100] %. Sanity: cp=+100 → 59 %, cp=+500 → 86 %, cp=+1000 → 97 %.
  - **`_classify_drop(wp_drop)`** — drop ve win % z pohledu hráče: ≥20 % blunder, ≥10 % mistake, ≥5 % inaccuracy, ≥2 % good, jinak best. Lichess defaults ?!/?/?? + naše doplnění good/best (lichess je nerozlišuje, my chceme barevný tag pro UX i u řadových tahů).
  - **`_terminal_wp_white(fen)`** — reconstruct board z FEN, mat → 100 % matujícímu (vítěz = strana NEna tahu po posledním tahu), draw → 50 %. Bez tohoto by koncový mat dostal "wp_after = 50 % default" a klasifikoval se jako blunder z výhry.
  - **`classify_game(pgn, time_per_move=0.3)`** — parse PGN → fens, persistent Stockfish přes `analyse_game_fens`, per ply spočítá wp_drop z perspektivy táhnoucího hráče (white pro lichý ply, black pro sudý). Vrátí list MoveEval délky N+1.
  - **`get_or_classify_game(game_id, time_per_move=0.3)`** — lookup-or-compute: `has_classification` → vrátí cached, jinak fetch PGN, klasifikuje, uloží, vrátí.
- **Endpoints** v `app.py`:
  - `POST /api/games/{id}/classify?time_per_move=0.3` — fresh nebo cached compute, vrátí `ClassificationResponse {game_id, evals, cached}`. Status mapping: 404 partie chybí, 400 špatný time_per_move, 500 chybějící Stockfish.
  - `GET /api/games/{id}/classification` — jen cached, 404 pokud chybí (žádný Stockfish call). Pro frontend "zkontroluj, jestli existuje, a pokud ne, zobraz tlačítko Klasifikovat".
- **Frontend** `templates/pgn.html`:
  - **CSS**: `.cls-best/-good/-inaccuracy/-mistake/-blunder` (zelená/šedá/žlutá/oranžová/červená) pro tagy v move list, `.cls-pill.*` pro souhrn pillů.
  - **HTML**: nový container pod eval grafem s tlačítkem "Klasifikovat tahy" + status + souhrn (např. "35 nejlepších · 5 dobrých · 9 nepřesností · 3 chyby · 2 hrubky").
  - **JS**: `state.classifications` mapa ply→eval, `state.gameId` z handoff. `classifyGame()` (POST), `loadCachedClassification(gameId)` (GET cached), `applyClassifications(evals)` (state + re-render moves + summary), `renderClsSummary(evals)` (pill per kategorie, skip 0 výskytů), `renderMoveHtml(move)` (přidá `<span class="cls-tag">` s glyph: ✓ / (nic) / ?! / ? / ??). Tooltip s českým popiskem.
  - **Tlačítko enabled jen pokud `state.gameId` existuje** — klasifikace cache v DB per game_id, ručně vložený PGN do textarea nemá game_id (= no cache). Tooltip "Vyžaduje partii z /games".
  - **Handoff `/games → /pgn`**: `games.html` ukládá `chesslab-pending-game-id` do localStorage vedle PGN. `pgn.html` handler ho přečte, naplní `state.gameId`, po loadu zkusí `loadCachedClassification` → pokud cached, tagy se hned zobrazí (bez Stockfish call).
- **Smoke test E2E**: random partie z DB (`xkOmmJ8X`, 54 plies):
  - `GET /classification` před classify → HTTP 404 (jak má být).
  - `POST /classify` fresh → 15.8s, 55 evals, `cached=False`. Klasifikace: 35 best / 5 good / 9 inaccuracy / 3 mistake / 2 blunder = 54 ✓ (ply 0 nemá classification).
  - `POST /classify` podruhé → **0.2s, cached=True** (79× rychlejší cache hit ✓).
  - `GET /classification` po classify → HTTP 200.
  - Terminal mate handling ověřen: ply 51 měl `eval_cp=None` (game_over), klasifikace 'blunder' korektně spočtena z `_terminal_wp_white` fallbacku.
- **Vědomě vyloučeno z této iterace** (zaznamenáno v IDEAS pro budoucí iterace):
  - **Brilliant/Great kategorie** (lichess !!/!) — vyžadovaly by sacrifice detekci + only-move check, navíc ~100 řádků a okrajový případ.
  - **Souhrn v `/games` tabulce** + sloupec s mini-grafem (2! 5? 1??) + filter "show games with N+ blunders".
  - **Asynchronní background job** (queue + status endpoint) — pro on-demand jednu partii je 15-30s wait akceptovatelné, pro batch klasifikaci všech 100 partií ne. Až bude potřeba.
  - **Custom time_per_move v UI** — backend přijímá, frontend posílá default 0.3s (slider přidáme, pokud bude poptávka).
  - **Klasifikace ručně vloženého PGN** (bez game_id) — vyžadovala by samostatný endpoint `POST /api/classifier/classify_pgn` bez DB persistence. Workaround: importuj přes `/import` → otevři přes `/games`.

## 2026-05-17 — Engine v2.7: Move ordering (MVV-LVA)

- **`_mvv_lva_score(board, move) -> int`** v `minimax_engine.py` — `victim_value * 10 - aggressor_value`. Multiplikátor 10 zaručí, že **rozdíl ve victim tier vždy přebije rozdíl v aggressor**: PxQ=8900 (vyhráváme dámu) > QxR=4100 > QxB=2400. Aggressor jen rozhoduje tie-break mezi captures se stejnou obětí — pro jezdce: PxN=3100 > NxN=2880 > BxN=2870 > RxN=2700 > QxN=2300 (vyhrát figuru levně > vyhrát ji draho, protože při recapture ztrácíme míň). Non-captures dostanou score 0 → půjdou za všemi captures. **En passant edge case**: `board.piece_at(move.to_square)` vrátí `None` pro EP (beraný pěšec stojí na sousedním poli), `board.is_en_passant(move)` to detekuje → victim hardcoded PAWN.
- **`_order_moves(board, moves) -> list[Move]`** — wrap nad `sorted(reverse=True, key=mvv_lva_score)`. Stable sort zachová původní pořadí pro tahy se stejným scorem (non-captures zůstanou v pořadí python-chess).
- **Integrace** v `_negamax` (top vrstva pro depth > 0) i `_quiescence` (oba branche: captures-only i in-check legal_moves). Root `choose_move` ordering nepotřebuje — má full window per move (žádný pruning), ordering by nezměnil výsledek.
- **Verze bump**: `v2.6` → `v2.7`. Display name v `_KNOWN_ENGINES` + `ENGINE_NAME`. In-place upgrade — historický v2.6 baseline je v gitu commit `75b12cf`.
- **Smoke test Aréna** (Minimax v2.7 vs Greedy v1, 20 partií, 0.05s/tah, alternace barev):
  - **20W-0L-0D, 100 %, ≥ +636.4 Elo** (lower bound — sweep, **identický s v2.6**). Predikce splněna: **MVV-LVA při fixní depth 2 sílu nezvedne** (pořadí tahů nemění best move při full search, jen rychlost). Pro přímé měření přínosu by chtělo depth 3 nebo node-count benchmark (ne Elo).
  - **100 % CHECKMATE** termination, stejné jako v2.6. Avg plies 59 (vs v2.6 76 — kratší match, vedlejší efekt random tie-breaku v různém pořadí, drobnost).
  - Total time 38.9s (1.95s/game) vs v2.6 ~32s na 5/5 sweep — mizivý rozdíl, sort overhead vs pruning benefit se vyrovnají při depth 2.
  - Vyvážený split 10W (white) + 10W (black) — engine není color-biased.
- **Vědomě vyloučeno z v2.7** (zaznamenáno IDEAS): killer moves / history heuristic (non-capture ordering), SEE pruning (filtruje "blbé" captures Q×P chráněný P), iterative deepening + TT (kandidát na v2.8 — automaticky využije zrychlení z MVV-LVA pro jít hloub), checks v quiescence (v3+, search explosion bez SEE).
- **Měřitelný přínos uvidíme až ve v2.8 nebo v3** — MVV-LVA je stavební kámen, ne samostatná Elo bullet. Kandidát na další iteraci: iterative deepening, který automaticky vyladí depth podle time budgetu (depth 2 jako fallback, depth 3+ kde to MVV-LVA umožní).

## 2026-05-17 — Engine v2.6: Quiescence search + root search fix

- **`_quiescence(board, alpha, beta, ply=0) -> int`** v `minimax_engine.py` — captures-only quiescence search v listech minimax stromu. Stand-pat pattern (eval pozice je lower bound, hráč nemusí captureovat), beta cutoff už na stand-pat, in-check větev iteruje **všechny** legal moves (escape from check) místo jen captures (jinak by engine "stál na místě" v šachu = nelegální). Hard depth cap `_QUIESCENCE_MAX_PLIES = 8` (Stockfish ~6) — bez něj může quiescence v multi-capture exchange sequences přetéct UCI movetime budget (0.05s v Aréně) → arena timeout, partie ztracena. Při dosažení capu vrátíme stand_pat (resp. -MATE pokud in_check, defenzivní fallback).
- **Integrace v `_negamax`** — terminál rozdělen: `is_game_over` → přímý `_evaluate_for_side_to_move`, `depth == 0` → `_quiescence`. Důvod: po game_over captures nejsou (pozice definitivně skončena), kdežto na depth=0 chceme extend search v capture pozici.
- **Root search bug fix** v `choose_move` (existoval od v2.0): původní kód sdílel `alpha` mezi top-level iteracemi → standardní alpha-beta pruning pattern. Problém: druhý+ tah dostane jen *upper bound* score z alpha-beta cutoffu (víme jen "≤ alpha", ne přesnou hodnotu). Tie-break přes porovnání skóre pak falešně rozšiřoval `best_moves` set — např. in-check pozici (Kxe2 score 0, Kf1 cutoff score 0) `random.choice` mohl vybrat Kf1 místo Kxe2 (volná věž neuznána). Fix: každý root move searchuje s freshly (-inf, +inf) oknem; cena = top-level pruning ztracen (pruning v child zůstává), pro depth 2 zanedbatelné. Standardní PVS (Principal Variation Search s re-search cutoff tahů) by řešilo bez ztráty pruningu, ale je v2.7+ optimalizace.
- **Unit test in-check pozice** (`4k3/8/8/8/8/8/4r3/4K3 w - - 0 1` = WK e1, BR e2, BK e8): před fixem engine vracel Kf1 (random tie-break z falešného setu), po fixu vrací deterministicky Kxe2 (capture věže = exact score 0, ostatní tahy -500).
- **Verze bump**: `v2.5` → `v2.6`. Display name v `_KNOWN_ENGINES` + `ENGINE_NAME`. Console script `chesslab-minimax` (in-place upgrade — historický v2.5 baseline je v gitu commit `b9988e1` před tímto).
- **Smoke test Aréna** (Minimax v2.6 vs Greedy v1, 20 partií, 0.1s/tah, alternace barev):
  - **20W-0L-0D, 100 %, perf rating ≥ +636.4 Elo** (lower bound — sweep). Proti v2.5 (+511 exact): **skok +120 Elo** quiescence + root fix dohromady.
  - **100 % CHECKMATE** termination (vs v2.5: 18× CHECKMATE + 2× draw). Greedy už nemá únikové cesty — quiescence chytá taktiky v listech, kde Greedy v2.5 občas projel.
  - Avg plies 76 (= ~38 tahů), range 27-156. Rychlé matty v openingu (Greedy padne na 4-ply taktiku), dlouhé v koncovkách (endgame heuristika z v2.5 dotáhne KR-vs-K).
  - **Ověření 0.05s/tah** (default arena slider): 5/5 sweep za 32s, žádný timeout. Quiescence cap funguje — typický průměr 3-5 plies, depth 8 se aktivuje extrémně zřídka.
- **Vědomě vyloučeno z v2.6** (zaznamenáno IDEAS): checks v quiescence (search explosion bez SEE-filtering, plánováno v2.8), MVV-LVA move ordering (v2.7, zlepší pruning v negamax i quiescence), SEE pruning špatných captures (Q×P chráněný P), iterative deepening + TT, PVS re-search v root pro top-level pruning.
- **Otevřená otázka**: přesný breakdown přínosu (kolik z +120 Elo je quiescence vs root fix) — pro izolaci by chtělo revert na v2.5 + samostatný root fix test + arena run. Drobnost; net effect je čistý win.

## 2026-05-17 — Lichess import + SQLite + browser partií (`/import`, `/games`)

- **SQLite vrstva** `src/chesslab/db.py` — connection context manager (auto commit/rollback, `row_factory=Row`), schema přes `executescript(_SCHEMA)` s `IF NOT EXISTS` všude (idempotentní, žádné migrace). Single source of truth pro DB cestu: default `<project>/data/chesslab.db`, override `CHESSLAB_DB_PATH`. `init_db()` se volá z FastAPI **lifespan** hooku — první start aplikace vytvoří `data/` adresář + tabulky automaticky. Schema: jedna tabulka `games` s 18 sloupci (id PRIMARY KEY pro UPSERT, plný PGN jako TEXT, indexy na username/created_at/source).
- **Lichess klient** `src/chesslab/lichess.py` — httpx `Client.stream()` + `iter_lines()` nad NDJSON endpointem `/api/games/user/{username}` (žádný OAuth = veřejné partie, rate limit 20 req/min). Query `pgnInJson=true&opening=true`, defaults sort `dateDesc`. Pydantic `LichessGame` přesně mapuje na DB sloupce. Generator vrací hru za hrou, mapping ValueError per-game neukončí celý import. `DEFAULT_MAX_GAMES = 100`, `HARD_MAX_GAMES = 500`.
- **Games DB ops** `src/chesslab/games.py` — `insert_games()` přes `INSERT OR IGNORE` (UPSERT pattern — opětovný stažení nepřepíše existující řádky, kritické pro budoucí lokální anotace, kdybychom dělali REPLACE, ztratili bychom je). `list_games()` s filtry color/result/speed (Literal typehints), `get_game_pgn()`, `count_games()`. Pydantic modely `GameSummary` (bez PGN, pro list view) / `GameDetail` (s PGN) / `ImportResult` (frontend toast). High-level orchestrace `import_lichess_user()` chytí ValueError per-game (`error_messages` capped na 10), propaguje httpx výjimky na endpoint.
- **FastAPI endpointy** v `app.py`: `GET /import` + `GET /games` (page routes), `POST /api/import/lichess` (status mapping: 404 user neexistuje, 429 rate limit, 502 jiný upstream, 504 timeout), `GET /api/games` (Literal query params), `GET /api/games/count` (empty-state UI), `GET /api/games/{id}/pgn` (plain `application/x-chess-pgn` pro localStorage handoff). Lifespan hook volá `init_db()` při startu.
- **Templates** `import.html` + `games.html` — sdílený styl s `arena.html` (form layout, status řádek, výsledkový panel). Import: username input s localStorage persistence (`chesslab-lichess-username`), slider 1-500, fake progress timer. Games browser: filter bar (color/result/speed selecty s auto-reload on change), tabulka s datum/tempo/barva (♔/♚ glyph)/soupeř/rating/výsledek (W:+ L:− D:½, color-coded)/konec/tahy (plies→full moves)/otevírka. Klik na řádek = fetch PGN → `localStorage['chesslab-pending-pgn']` → `window.open('/pgn', '_blank')` — **stejný handoff pattern jako `/play` → analýza** (DRY, `/pgn` hook už existoval z předchozí iterace).
- **Bug fix během smoke testu**: httpx `stream()` response — pro chybový status (404) `resp.text` v exception handleru selhal s `ResponseNotRead`. Fix v `lichess.py`: `if resp.status_code >= 400: resp.read()` před `raise_for_status()` — explicitně načte tělo uvnitř stream context manageru, aby měl handler v `app.py` přístup k Lichess error message.
- **Smoke test**: `/api/import/lichess {username:"TirelessWoodpusher", max_games:5}` → 5/5 inserted; re-run → 0/5 inserted, 5/5 skipped (idempotence ✓); `max_games:100` → 95 inserted + 5 skipped za 4.3s; filtr `?color=white&result=loss` vrátil 2 ze 100; 404 test (bad username) vrátil HTTP 404 s body `{"detail": "Lichess API: {\"error\":\"Not found\"}"}` (po fixu).
- **Vědomě vyloučeno z této iterace** (zaznamenáno v TODO): inkrementální import (since timestamp), chess.com import, OAuth/Bearer token, username dropdown v `/games` (zatím text-less filter), per-pozice eval anotace v DB (= klasifikace tahů, řeší se v IDEAS).
- Index updated — Lichess přesunut z „Brzy přijde" do Features (přidány 2 položky: Import + Browser). README rozšířen o sekci a `CHESSLAB_DB_PATH` env.

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
