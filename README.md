# ChessLab

Osobní šachová laboratoř — lokální Python aplikace s webovým UI, která zastřešuje analýzu partií, hraní proti enginu, testování vlastních enginů a napojení na externí šachové API.

## Stack

- **Python 3.14** + **FastAPI** + **uvicorn**
- **python-chess** — PGN, FEN, UCI komunikace s enginy
- **Stockfish** (binary) — analytický engine
- **Vanilla JS** + **chessboard.js** + **chess.js** — frontend (žádný framework)
- **uv** — package manager

## Co umí

- **Rozcestník** na `/` — homepage s odkazy na features.
- **PGN viewer** na `/pgn` — vlož PGN, prohlížej tahy (klávesy ←/→/Home/End, klik na tah, Ctrl+Enter = Load), šachovnice se synchronizuje. **Audio feedback** (Web Audio synthesis) při krokování + **materiálová badge** pod boardem + mute toggle s localStorage persistence (sdílený partial `_chesslab_audio.html`).
- **Stockfish analýza** v PGN vieweru — tlačítko spustí analýzu aktuální pozice (1s budget): eval v centipawnech / mate + best move + depth, **vertikální eval bar** vlevo od šachovnice, **best-move šipka** přímo na šachovnici, volitelný **Auto** režim (automatická analýza při každé změně tahu).
- **Eval graf přes partii** — tlačítko „Analyzovat partii" pošle celou partii Stockfishi (0.3s/pozici, persistent engine). Výstup = SVG křivka pod layoutem, klik na bod = skok na pozici, červené body = blundery (drop > 1.5 pawn z pohledu hráče, který tahnul).
- **Hra proti enginu** na `/play` — drag-and-drop, **dropdown výběru enginu** (Stockfish nebo vlastní ChessLab engine, auto-discovery z `.venv\Scripts\chesslab-*`), nastavitelný Skill Level (0-20, dimnutý u enginů bez Skill Level option, **ChessLab Elo hint `(~XXXX Elo)` vedle slideru** pro skill-aware enginy), think time (default scaled), volba barvy, toggle „Show eval" (eval bar) a „Recommended move" (best-move šipka). Highlight posledního engine tahu. Audio feedback na tahy + materiálová badge + mute toggle. Undo (pop 2 plies), Resign, Download PGN. Po skončení hry tlačítko „Analyzovat partii" otevře nový tab s `/pgn` a auto-spustí analýzu (handoff přes localStorage).
- **Engine arena** na `/arena` — dva UCI enginy proti sobě, batch 1-20 partií, alternace barev. Dropdown výběru enginu pro každou stranu (Stockfish / vlastní ChessLab enginy), Skill slider dim u enginu bez Skill Level option + **per-skill ChessLab Elo hint** vedle slideru (`stockfish:N` lookup), Path input editovatelný (custom UCI binárka mimo discovery). Výstup: score % + performance rating diff + tabulka partií s per-game PGN download + **ChessLab Elo update** (rating change `1500 → 1530 (+30)` per engine).
- **ChessLab Elo žebříček** na `/engines` — persistent ChessLab Elo per engine, dva update modely: (1) klasická **FIDE Elo** po každé areně (K=40 pro <30 partií, pak K=20); (2) **round-robin turnaj + Bayesian Elo refit** přímo z `/engines` (Bradley-Terry MLE z celé matchup matrice s virtual draw priorou + anchor rescale). Anchor `Stockfish skill 5 = 1500` (lokální kalibrace pro 0.05s/tah, ne CCRL). Matchup matrix `engine_matchups` akumuluje W/L/D napříč arenami i turnaji → batch refit vždy z kompletní historie. Tabulka s pořadím, ratingem, počtem partií a datem posledního updatu. Tag ⚓ pro anchor, žluté zvýraznění pro provisional (málo partií = vyšší volatilita). Engine dropdowny v `/play` a `/arena` zobrazí rating non-skill enginů přímo v option labelu.
- **Import partií z Lichess + chess.com** na `/import` — stáhne public partie uživatele (bez OAuth, 1-500 partií/request, per-zdroj formulář). **Default inkrementální** (Lichess server-side `since=<ms>` filter; chess.com client-side filter `created_at > since` při čtení měsíčních archivů → early exit, jakmile narazí na starou partii); checkbox „Force full re-sync" pro repair. Idempotentní jako safety net (INSERT OR IGNORE). Uložení do SQLite `data/chesslab.db`. Měřeno: 500 chess.com partií ~10s (~50 p/s — měsíční batche jsou paradoxně rychlejší než Lichess single-stream).
- **Browser stažených partií** na `/games` — tabulka s filtry (barva / výsledek / tempo), klik na řádek otevře partii v `/pgn` a auto-spustí Stockfish analýzu celé partie (+ automaticky načte cached klasifikaci tahů, pokud existuje).
- **Klasifikace tahů** v `/pgn` (pro partie z DB) — tlačítko „Klasifikovat tahy" pošle celou partii Stockfishi a per ply klasifikuje (best ✓ / good / inaccuracy ?! / mistake ? / blunder ??) na základě **lichess-style sigmoid** (drop ve win % z pohledu táhnoucího hráče). Barevné tagy vedle každého tahu v move list + souhrn pillů (kolik z které kategorie). **Klikatelné pilly + tagy**: klik na pill `5 chyba` = smart next výskyt po aktuálním ply (wrap-around); klik na tag `??` = skok přímo na ten tah. Cache v SQLite (`move_evals` tabulka per game_id+ply) — opětovné otevření partie zobrazí tagy okamžitě.
- **Vlastní enginy** — UCI binárky generované z `chesslab.engines.*` přes `uv sync`, auto-discovery v Play i Aréně, sdílená UCI smyčka (`engines/_protocol.py`). Discovery endpoint: `GET /api/engines/list`. Princip: každý engine reprezentuje konkrétní algoritmický stupeň, snapshoty drženy jako benchmark variant (ne mrtvá historie).
    - **v0: Random Mover** (`chesslab-random`) — náhodný legální tah.
    - **v1: Greedy Material** (`chesslab-greedy`) — 1-ply lookahead nad materiálem (Kaufman piece values + mate/stalemate/check bonusy). +300 Elo nad Random.
    - **v3.2: Minimax — aktivní hlavní engine** (`chesslab-minimax`) — negamax + α-β + quiescence search (captures + non-capture checks, cap 8/2 plies) + endgame king-tropism eval + iterative deepening + transposition table + PV move ordering + killer moves + history heuristic. **+56 Elo decisive** nad v2.8 milestonem (z v3.1, n=200, p=0.013), v3.2 marginal +24 Elo nad v3.1.
    - **v2.7: Minimax (snapshot)** (`chesslab-minimax-v27`) — textbook depth 2 + α-β + endgame eval + quiescence (captures) + MVV-LVA. ≥ +636 Elo nad Greedy (20-0-0 sweep, lower bound). Drženo jako *čistý minimax* benchmark.
    - **v3.1: Minimax (snapshot)** (`chesslab-minimax-v31`) — v2.8 + iterative deepening + transposition table + PV move ordering. Drženo jako *decisive TT/PV milestone* benchmark před killer/history tweaky.
- **OpenAPI dokumentace** na `/docs`, health endpoint na `/health`.

Roadmapa viz [TODO.md](TODO.md) a [IDEAS.md](IDEAS.md).

## Spuštění

```powershell
uv run chesslab
```

Otevřít `http://127.0.0.1:8765`.

Volitelné env proměnné:

| Proměnná | Default | Co dělá |
| --- | --- | --- |
| `CHESSLAB_HOST` | `127.0.0.1` | Host/interface, na kterém server poslouchá. |
| `CHESSLAB_PORT` | `8765` | TCP port. |
| `STOCKFISH_PATH` | `C:\Program Files\stockfish\stockfish-windows-x86-64-avx2.exe` | Cesta ke Stockfish binárce. |
| `CHESSLAB_DB_PATH` | `<project>/data/chesslab.db` | Cesta k SQLite DB (importované partie). |

## Vývoj

Závislosti:

```powershell
uv sync
```

Přidat balíček:

```powershell
uv add <jméno-balíčku>
```

Dev server s auto-reloadem se startuje stejným `uv run chesslab` (reload je zapnutý defaultně v dev módu).

## Licence

MIT — viz [LICENSE](LICENSE).
