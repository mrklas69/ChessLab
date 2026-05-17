# TODO

Aktivní úkoly pro ChessLab MVP. Po dokončení přesouvej do `DONE.md`.

## MVP — fáze 1

- [~] **Stockfish analýza** — textový výpis hotov (viz DONE), zbývají vizuální komponenty:
  - [ ] Vizuální eval bar (svislý prvek vedle boardu).
  - [ ] Best move šipka na šachovnici.
  - [ ] Volitelný auto-trigger při setPly (toggle).
- [ ] **Eval graf přes partii** — křivka hodnocení tahů, identifikace blunderů (drop > 1.5 pawn).
- [ ] **Hraní proti UCI enginu** — drag-and-drop UI (vanilla JS + chessboard.js), výběr enginu, čas/level, auto-promote na dámu.
- [ ] **Engine arena (basic)** — dva UCI enginy proti sobě, X partií, výsledek, ELO tabulka.
- [ ] **Import partií z Lichess API** — stáhnout svoje partie přes `berserk` / `httpx`, uložit do SQLite.

## Infrastruktura

- [ ] **SQLite schema** — partie (PGN), pozice, eval anotace.
- [ ] **Static assets** — chessboard.js + chess.js přes CDN nebo `static/` adresář.
- [ ] **Templates** — Jinja2 base layout, partials pro šachovnici / move list.

## Drobnosti

- [ ] Smazat starou složku `C:\Users\mrkla\source\ChessHub` (harness lock zmizí mimo session).
- [ ] Rozhodnout o licenci (MIT? Apache 2.0?).
- [ ] `.env.example` pro `STOCKFISH_PATH` / `CHESSLAB_PORT`.
