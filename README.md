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
- **PGN viewer** na `/pgn` — vlož PGN, prohlížej tahy (klávesy ←/→/Home/End, klik na tah, Ctrl+Enter = Load), šachovnice se synchronizuje.
- **Stockfish analýza** v PGN vieweru — tlačítko spustí analýzu aktuální pozice (1s budget): eval v centipawnech / mate + best move + depth, **vertikální eval bar** vlevo od šachovnice, **best-move šipka** přímo na šachovnici, volitelný **Auto** režim (automatická analýza při každé změně tahu).
- **Eval graf přes partii** — tlačítko „Analyzovat partii" pošle celou partii Stockfishi (0.3s/pozici, persistent engine). Výstup = SVG křivka pod layoutem, klik na bod = skok na pozici, červené body = blundery (drop > 1.5 pawn z pohledu hráče, který tahnul).
- **Hra proti enginu** na `/play` — drag-and-drop, **dropdown výběru enginu** (Stockfish nebo vlastní ChessLab engine, auto-discovery z `.venv\Scripts\chesslab-*`), nastavitelný Skill Level (0-20, dimnutý u enginů bez Skill Level option), think time (default scaled), volba barvy, toggle „Show eval" (eval bar) a „Recommended move" (best-move šipka). Highlight posledního engine tahu. Undo (pop 2 plies), Resign, Download PGN. Po skončení hry tlačítko „Analyzovat partii" otevře nový tab s `/pgn` a auto-spustí analýzu (handoff přes localStorage).
- **Engine arena** na `/arena` — dva UCI enginy proti sobě, batch 1-20 partií, alternace barev. Dropdown výběru enginu pro každou stranu (Stockfish / vlastní ChessLab enginy), Skill slider dim u enginu bez Skill Level option, Path input editovatelný (custom UCI binárka mimo discovery). Výstup: score % + performance rating diff + tabulka partií s per-game PGN download.
- **Vlastní enginy** — UCI binárky generované z `chesslab.engines.*` přes `uv sync`, auto-discovery v Play i Aréně, sdílená UCI smyčka (`engines/_protocol.py`). Discovery endpoint: `GET /api/engines/list`.
    - **v0: Random Mover** (`.venv\Scripts\chesslab-random.exe`) — náhodný legální tah.
    - **v1: Greedy Material** (`.venv\Scripts\chesslab-greedy.exe`) — 1-ply lookahead nad materiálem (Kaufman piece values + mate/stalemate/check bonusy). +300 Elo nad Random.
    - **v2.5: Minimax + α-β + endgame heuristika** (`.venv\Scripts\chesslab-minimax.exe`) — negamax depth 2 s alpha-beta, material eval + check bonus + king-tropism / edge distance pro silnější stranu v koncovce (řeší KR-vs-K mate, který depth 2 jinak nevidí). **+511 Elo nad Greedy v1** (95 % skóre v 20 partiích), ≥ +511 nad Random. Skok +320 Elo nad původní v2.0 jen endgame heuristikou.
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

TBD.
