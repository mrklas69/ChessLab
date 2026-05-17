# TODO

Aktivní úkoly pro ChessLab. Po dokončení přesouvej do `DONE.md`.

## Import / DB — fáze 2

- [ ] **Inkrementální import** — `since` parametr (timestamp poslední importované partie), zrychlí re-import z O(n) na O(přírůstek).
- [ ] **Import z chess.com** — REST API (formát PGN na měsíce, jiný auth model).
- [ ] **Lichess OAuth** (Bearer token) — pro private/correspondence partie + 60 req/min místo 20.
- [ ] **Username filtr v /games** — když user importuje víc účtů, dropdown všech distinct usernames z DB.

## Drobnosti

- [ ] Smazat starou složku `C:\Users\mrkla\source\ChessHub` (harness lock zmizí mimo session).
- [ ] Rozhodnout o licenci (MIT? Apache 2.0?).
- [ ] `.env.example` pro `STOCKFISH_PATH` / `CHESSLAB_PORT` / `CHESSLAB_DB_PATH`.

