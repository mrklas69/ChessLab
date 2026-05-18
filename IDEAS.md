# IDEAS

Raw nápady / vize / nice-to-have. Z těchto vznikají úkoly do `TODO.md`, **ne přímo kód**.

## Engine

- **Vlastní šachový engine v Pythonu** — minimax + alpha-beta, evaluation function, postupné iterativní zlepšování. Cílem je experimentování / učení, ne konkurovat Stockfishi.
- ~~**v2 Minimax depth 2 + alpha-beta**~~ — **HOTOVO 2026-05-17**, viz DONE. Reálný skok +191 Elo nad Greedy (odhad +400-600 nesplněn — horizon effect na depth 2).
- ~~**v2.5: Endgame heuristika (king tropism + edge distance)**~~ — **HOTOVO 2026-05-17**, viz DONE. +320 Elo nad v2.0.
- ~~**v2.6: Quiescence search + root search fix**~~ — **HOTOVO 2026-05-17**, viz DONE. Captures-only quiescence + hard depth cap 8 + fresh α/β v root searche. ≥ +636 Elo nad Greedy v1 (20-0-0 sweep), skok +120 Elo nad v2.5.
- ~~**v2.7: Move ordering (MVV-LVA)**~~ — **HOTOVO 2026-05-17**, viz DONE. Captures seřazeny dle `victim*10 - aggressor`. Při fixní depth 2 sílu nezvedlo (20-0-0 sweep vs Greedy = stejný lower bound jako v2.6), je to stavební kámen pro depth 3 / iterative deepening.
- ~~**v2.8: Checks v quiescence**~~ — **HOTOVO 2026-05-17**, viz DONE. Non-capture checks v non-check větvi quiescence, cap 2 plies (`_QUIESCENCE_MAX_CHECK_PLIES`). Bez SEE filtru (KISS). **Síla nejistá**: 2 sparringy à 20 partií dají protichůdné výsledky — @0.05s/tah +215 Elo, @0.15s/tah −35 Elo, CI se překrývají. Plausibilní: výhoda jen na krátkém time budgetu (main search nestihne hloub). Decisive test by chtěl 100+ partií.
- ~~**v3.0: Iterative deepening + soft time check**~~ — **HOTOVO 2026-05-18**, viz DONE. ID s mandatory depth 2 (garantuje v2.8 baseline) + adaptive depth 3+ s deadline. 2× sparring vs v2.8 (100 partií @ 0.10s, 50 partií @ 0.50s) — oba **nesignifikantní** (−24 a −28 Elo, CI překrývají nulu). **Insight**: ID bez TT/PV ordering v Pythonu nepřinese gain — overhead z infrastructure (~9 %) eats případný depth 3 advantage. Refactor je infrastructure pro v3.1+ s TT.
- ~~**v3.1: Transposition table + PV move ordering**~~ — **HOTOVO 2026-05-18**, viz DONE. Klíč `board._transposition_key()`, tuple entry `(depth, score, flag, best_move)`, modulový persistentní dict s soft cap 1M, PV ordering intra-search + ID-root chain. **Decisive +56 Elo nad v2.8** (200 partií, CI [+7, +105], p=0.013). Skok +80 Elo nad v3.0 → potvrzeno: ID v Pythonu **umí** gain, ale jen s TT + PV.
- ~~**v3.2: Killer moves + history heuristic**~~ — **HOTOVO 2026-05-18**, viz DONE. `_killers[ply][0..1]` (preallocated 64×2), `_history` dict `(color,from,to)` += depth². Move ordering: PV → captures MVV-LVA → killers → quiets by history. Clear per `choose_move` (TT persistuje). **Marginal +24 Elo nad v3.1** (100 partií, CI [−43, +92], p=0.27, non-signif ale Bayesian gap konzistentní +24.6). Killer/history v Pythonu mají slabší effect než literatura (+30-80) — možná Python overhead z dict/list tracking eats část benefitu. Decision: akceptováno jako weak positive, pokračujeme.
- **v3.3: Aspiration windows** — úzké α-β okno kolem prev iter score, re-search při miss. Zrychlí deep iterace o ~20 %. ~30 řádků. Standard pattern, nízká komplexita.
- **v3.3 alt: Mate-distance scoring** — `_MATE_SCORE - ply` místo flat. Zatím engine vidí jen mate-in-1 (všechny mate scores jsou stejné). Distance scoring → preferuje rychlejší mat. Vyžaduje TT mate-distance adjustment na store/probe (komplikovanější než zní).
- **v3.3 alt: Null move pruning** — heavy guns (skip move, ověř že eval drop > beta). Riziko zugzwang bugu, vyžaduje opatrné podmínky (žádný NMP v koncovce, žádný NMP v šachu).
- ~~**Engine sparring** — turnaj N enginů, round-robin, ELO výpočet (Bayesian / linear regression).~~ **HOTOVO 2026-05-17**, viz DONE. Round-robin endpoint + Bradley-Terry MM s virtual draw prior + anchor rescale. Recompute z celé matchup matrice po každém turnaji.
- ~~**Per-skill rating pro Stockfish v dropdownu**~~ — **HOTOVO 2026-05-17**, viz DONE. Frontend (`/play`, `/arena`) fetchne `/api/engines/ratings` paralelně s engine listem, postaví mapu `engine_id → rating`, hint `(~XXXX Elo)` vedle skill slideru pro skill-aware enginy. Bez extra endpointu (existující list všech ratingů stačí).
- **Opening book** — DB otevírkové teorie (.bin Polyglot, nebo vlastní z partií).
- **NNUE eval** v Python implementaci (velmi ambiciózní, jen pokud Python výkon nebude blokátor).
- **Arena: live progress** — místo fake odhadu času streamovat per-game výsledky přes SSE. Pro N > 10 by user získal feedback dřív než po 60s.

## Analýza

- ~~**Klasifikace tahů** podle Stockfish: brilliant / great / good / inaccuracy / mistake / blunder (per chess.com / lichess).~~ **HOTOVO 2026-05-17**, viz DONE. Lazy on-demand klasifikace s lichess sigmoid + cache v `move_evals`, barevné tagy v /pgn vieweru. (Brilliant/great heuristika vynechána — vyžadovala by sacrifice/only-move detekci.)
- ~~**Klikatelné klasifikační pilly + tagy v `/pgn`**~~ — **HOTOVO 2026-05-17**, viz DONE. Pilly v souhrnu (`20 nejlepší · 5 chyba`) klikatelné, klik = smart next výskyt po `state.ply` (wrap-around); tagy v move list (`?!`, `??`, `✓`) taky klikatelné = skok na konkrétní tah.
- **Opening identification** — porovnání s ECO databází.
- **Endgame tablebases** — Syzygy 6-figure pro perfektní analýzu koncovek.
- **Pattern recognition** — vidlice, špíz, vazba, mat v X tahů (taktická anotace).

## UI

- **Analysis board** ve stylu lichess: hlavní šachovnice + 3 best lines od enginu + eval graf.
- **Heatmap útoků/obran** — barevné zvýraznění polí.
- **Coordinate trainer** — trénink hledání polí naslepo.
- **Vlastní theming** šachovnice (převzít z `Chess/01-05` — 5 hotových stylů).
- ~~**Audio feedback na tahy + materiálová badge + mute toggle**~~ — **HOTOVO 2026-05-17 na `/play`**, viz DONE.
- ~~**Audio + material badge v `/pgn` step-by-step vieweru**~~ — **HOTOVO 2026-05-17**, viz DONE. Partial `_chesslab_audio.html` v sidebaru pod boardem, audio jen pro poslední ply (skok přes víc tahů = jediný zvuk), end zvuk při dosažení konce partie pokud SAN sám nekončí matem. `/arena` přeskočeno (rychlá série = otravné).
- **Promotion zvuk** v `ChessLabAudio` — `=Q`/`=R`/`=B`/`=N` má zatím default move sound. Mohl by mít vlastní vyšší triangle tier, kombinovaný s check/mate pokud zároveň.
- **Volume slider** vedle mute toggle — momentálně hardcoded volumes 0.12-0.20 v `tone()`. Pokud user řekne "moc nahlas", přidat slider 0-1 s localStorage persistence (stejný pattern jako mute key).

## Data

- ~~**Import partií** z chess.com API (REST), Lichess.~~ **HOTOVO 2026-05-17**, viz DONE. Lichess přes oficiální export API (NDJSON stream, `since` query param). chess.com přes `/pub/player/{name}/games/archives` → měsíční JSON s client-side filterem. Oba inkrementální (default ON), shared `_run_import` orchestrace, oba zdroje sdílejí UI pattern přes per-section JS handler.
- **Sjednocená DB partií** — tag-based filtry (otevírka, soupeř, výsledek, datum).
- **PGN editor** — anotace + variace, export.
- **Vlastní GM partie databáze** — Mega Database / TWIC import.
- **Lichess OAuth** (Bearer token) — pro private/correspondence partie + 60 req/min místo 20. Currently low priority — žádná konkrétní bolest.

## Distribuce / komunita

- **Cloud deploy** (Render / Fly.io) — public read-only verze.
- **Online multiplayer** — mimo scope MVP, ale teoreticky možné přes WebSockets + Stockfish-backed anti-cheat. **Argument proti:** lichess to dělá líp, nemá smysl konkurovat.
- **Tournament management** — pairing (Swiss / round-robin), live results.

## Mimo šach (možné spin-off)

- **LaTeX export** partií / diagramů — viz existující `C:\Users\mrkla\source\latex-chess`.
