# CLAUDE.md — ChessLab

Projektová pravidla pro Claude Code. Doplňuje globální `~/.claude/CLAUDE.md`, **nenahrazuje** je.

## Účel projektu

**ChessLab** — osobní šachová laboratoř. Lokální Python aplikace s webovým UI (FastAPI + vanilla JS + chessboard.js), která zastřešuje analýzu partií, hraní proti enginu, testování vlastních enginů a (později) databázi partií + napojení na externí API (Lichess, chess.com).

Pattern jako Jupyter / ComfyUI: backend běží na localhostu, frontend je browser. Plný nativní výkon (Stockfish), moderní UI, možnost pozdějšího nasazení na server pro veřejné funkce.

**Mimo scope:** online multiplayer (člověk vs. člověk přes net). To dělá lichess líp.

## Stack

| Vrstva | Technologie |
| --- | --- |
| Backend | Python 3.14, FastAPI, uvicorn |
| Chess logika | `python-chess` (parser PGN/FEN, UCI komunikace) |
| Engine | Stockfish binary + UCI protokol |
| Frontend | Vanilla JS, chessboard.js, chess.js (žádný React/Vue — KISS) |
| Šablony | Jinja2 |
| DB | SQLite (později) |
| Package manager | **uv** |

## Reference kód (DRY — nekopírujeme, čerpáme)

Velká část MVP funkčnosti už existuje jako samostatné experimenty. **Při migraci features přebírej kód z těchto zdrojů**, ale nepouštěj se do bezhlavého copy-paste — refaktoruj na FastAPI patterny.

- `C:\Users\mrkla\source\Chess\06_play\play.py` — webový server (stdlib `http.server`), drag-and-drop, hraní proti Stockfish, výběr stylu, PGN tracking. **Hlavní inspirační zdroj pro UI vrstvu.**
- `C:\Users\mrkla\source\Chess\06_play\renderers.py` — 5 stylů renderingu šachovnice.
- `C:\Users\mrkla\source\Chess\06_play\analyze.py` — analyzátor partií + `analysis.md`.
- `C:\Users\mrkla\source\Chess\01_chess_svg/` ... `05_leipfont/` — různé techniky renderingu (vzdělávací sandbox, **netreba migrovat celé**).
- `C:\Users\mrkla\source\ChessTest\chess.engine.example.py` — Stockfish vs. Stockfish skript s HTML auto-refresh.

`Chess/` repo **neměníme** — je to vzdělávací sandbox s vlastním účelem (viz jeho CLAUDE.md).

## Externí závislosti

- **Stockfish:** `C:\Program Files\stockfish\stockfish-windows-x86-64-avx2.exe`. Override přes env `STOCKFISH_PATH`.
- **Python 3.14.3**, **uv 0.11.14+** (binárka v `C:\Users\mrkla\AppData\Roaming\Python\Python314\Scripts\uv.exe`, v User PATH).

## Spuštění dev serveru

```powershell
uv run chesslab
```

Server běží na `http://127.0.0.1:8765`. Override portu/hostu přes env `CHESSLAB_PORT` / `CHESSLAB_HOST`.

## Override globálních maker

**%BEGIN** (rozšíření) — vedle defaultů (git sync, načtení docs, shrnutí stavu, „Příště") **navíc**:

1. Zkontroluj, zda dev server běží (`Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet`).
2. Pokud neběží, spusť ho na pozadí přes Bash tool s `run_in_background=true`:
   ```
   uv run --directory "C:\Users\mrkla\source\ChessLab" chesslab
   ```
3. Otevři `http://127.0.0.1:8765` v prohlížeči (nebo jen oznam URL uživateli).

**%END** (rozšíření) — vedle defaultů (docs sync, cleanup, git commit+push, memory) **navíc**:

1. Zastavit dev server (KillBash background shell, nebo `Get-Process -Name uvicorn,python | Where-Object { $_.MainWindowTitle -like '*chesslab*' } | Stop-Process` — bezpečnější je tracked background shell).

## Konvence

- Komentáře v kódu **česky** (per globální CLAUDE.md), trochu podrobněji — uživatel se Python učí.
- Identifikátory, názvy souborů, branches: **anglicky**.
- KISS, DRY, Single Source of Truth.
- **Žádný React/Vue**, dokud explicitně neschválíme — vanilla JS je default.
- **Žádné mocky enginu** v testech — pouštět skutečný Stockfish (rychlý, lokální).

## Workflow

1. Diskuze konceptu v chatu (`%THINK` pomáhá).
2. Raw nápady → `IDEAS.md`, konkrétní úkoly → `TODO.md`.
3. Kód (vždy malý kus, ne velký commit).
4. Hotové → `TODO.md` → `DONE.md`.
5. (Diary nezavádíme — uživatel ho zatím nechtěl. Až bude potřeba, přidá se `DIARY.md` a do tohoto souboru sekce o něm.)
