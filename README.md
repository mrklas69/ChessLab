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
- **Hra proti Stockfish** na `/play` — drag-and-drop, nastavitelný Skill Level (0-20), think time (default scaled), volba barvy, toggle „Show eval" (eval bar) a „Recommended move" (best-move šipka). Highlight posledního engine tahu. Undo (pop 2 plies), Resign, Download PGN. Po skončení hry tlačítko „Analyzovat partii" otevře nový tab s `/pgn` a auto-spustí analýzu (handoff přes localStorage).
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
