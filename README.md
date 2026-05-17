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
