# TODO

Aktivní úkoly pro ChessLab MVP. Po dokončení přesouvej do `DONE.md`.

## MVP — fáze 1

- [ ] **Import partií z Lichess API** — stáhnout svoje partie přes `berserk` / `httpx`, uložit do SQLite.

## Infrastruktura

- [ ] **SQLite schema** — partie (PGN), pozice, eval anotace.
- [ ] **Static assets** — chessboard.js + chess.js přes CDN nebo `static/` adresář.
- [ ] **Templates** — Jinja2 base layout, partials pro šachovnici / move list.

## Drobnosti

- [ ] Smazat starou složku `C:\Users\mrkla\source\ChessHub` (harness lock zmizí mimo session).
- [ ] Rozhodnout o licenci (MIT? Apache 2.0?).
- [ ] `.env.example` pro `STOCKFISH_PATH` / `CHESSLAB_PORT`.

## Engine arena — polish (zjištěno po sezení 2026-05-17)

- [ ] Sloupec „Tahy" v tabulce partií ukazuje plies (půltahy), ne tahy. Buď přejmenovat na „Půltahy", nebo dělit dvěma (šachista typicky myslí tah = pair W+B). Soubor `templates/arena.html`.
- [ ] Odhad času ve status řádku počítá `n_games × 30 × time_per_move`, ale realita je ~60-70 plies/partii (chyba 2×). Zvednout konstantu na ~60-70, nebo lépe odhadnout z `time_per_move` (víc času = víc tahů, protože koncovky jsou delší).
- [ ] Pro extrémní výsledek (0% / 100%) zobrazit aspoň lower-bound perf rating místo „N/A" (např. „≥ +400 Elo"). Aktuálně `perf_rating_diff: null` → UI píše „N/A (extrémní výsledek)".
- [ ] UCI `ucinewgame` mezi partiemi — python-chess SimpleEngine to public API nevystavuje. Buď respawn enginů per game (overhead ~200ms × N), nebo přejít na low-level `chess.engine.UciProtocol`. Pro N > 20 partií doporučeno.
