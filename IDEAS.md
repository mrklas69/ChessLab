# IDEAS

Raw nápady / vize / nice-to-have. Z těchto vznikají úkoly do `TODO.md`, **ne přímo kód**.

## Engine

- **Vlastní šachový engine v Pythonu** — minimax + alpha-beta, evaluation function, postupné iterativní zlepšování. Cílem je experimentování / učení, ne konkurovat Stockfishi.
- ~~**v2 Minimax depth 2 + alpha-beta**~~ — **HOTOVO 2026-05-17**, viz DONE. Reálný skok +191 Elo nad Greedy (odhad +400-600 nesplněn — horizon effect na depth 2).
- ~~**v2.5: Endgame heuristika (king tropism + edge distance)**~~ — **HOTOVO 2026-05-17**, viz DONE. +320 Elo nad v2.0.
- ~~**v2.6: Quiescence search + root search fix**~~ — **HOTOVO 2026-05-17**, viz DONE. Captures-only quiescence + hard depth cap 8 + fresh α/β v root searche. ≥ +636 Elo nad Greedy v1 (20-0-0 sweep), skok +120 Elo nad v2.5.
- ~~**v2.7: Move ordering (MVV-LVA)**~~ — **HOTOVO 2026-05-17**, viz DONE. Captures seřazeny dle `victim*10 - aggressor`. Při fixní depth 2 sílu nezvedlo (20-0-0 sweep vs Greedy = stejný lower bound jako v2.6), je to stavební kámen pro depth 3 / iterative deepening.
- **v2.8: Checks v quiescence** — jen agresivní (= dávající šach) tahy, ne všechny non-captures. Riziko: search explosion (checks generují velkou škálu pokračování). Klasická obrana: SEE-based filtering (jen checks, které nejsou losing). Pravděpodobně až po MVV-LVA, kdy bude pruning efektivnější.
- **v3: Depth 3** — po implementaci move ordering. Vidíme my-opp-my = můžeme plánovat dvoutahové kombinace (např. fork přípravy). Bez move ordering by v Pythonu při 0.05s/tah neměl čas dohrát.
- ~~**Engine sparring** — turnaj N enginů, round-robin, ELO výpočet (Bayesian / linear regression).~~ **HOTOVO 2026-05-17**, viz DONE. Round-robin endpoint + Bradley-Terry MM s virtual draw prior + anchor rescale. Recompute z celé matchup matrice po každém turnaji.
- **Per-skill rating pro Stockfish v dropdownu** — aktuálně dropdown ukazuje rating jen pro non-skill enginy (Random/Greedy/Minimax = jeden rating per binárka). Stockfish skill 0-20 má 21 separátních ratingů; chtělo by samostatný endpoint `GET /api/engines/rating?engine_id=stockfish:N` a JS hook na změnu skill slideru → dotáhnout rating, zobrazit vedle slideru.
- **Opening book** — DB otevírkové teorie (.bin Polyglot, nebo vlastní z partií).
- **NNUE eval** v Python implementaci (velmi ambiciózní, jen pokud Python výkon nebude blokátor).
- **Arena: live progress** — místo fake odhadu času streamovat per-game výsledky přes SSE. Pro N > 10 by user získal feedback dřív než po 60s.

## Analýza

- ~~**Klasifikace tahů** podle Stockfish: brilliant / great / good / inaccuracy / mistake / blunder (per chess.com / lichess).~~ **HOTOVO 2026-05-17**, viz DONE. Lazy on-demand klasifikace s lichess sigmoid + cache v `move_evals`, barevné tagy v /pgn vieweru. (Brilliant/great heuristika vynechána — vyžadovala by sacrifice/only-move detekci.)
- **Opening identification** — porovnání s ECO databází.
- **Endgame tablebases** — Syzygy 6-figure pro perfektní analýzu koncovek.
- **Pattern recognition** — vidlice, špíz, vazba, mat v X tahů (taktická anotace).

## UI

- **Analysis board** ve stylu lichess: hlavní šachovnice + 3 best lines od enginu + eval graf.
- **Heatmap útoků/obran** — barevné zvýraznění polí.
- **Coordinate trainer** — trénink hledání polí naslepo.
- **Vlastní theming** šachovnice (převzít z `Chess/01-05` — 5 hotových stylů).

## Data

- **Import partií** z chess.com API (REST), Lichess (`berserk`).
- **Sjednocená DB partií** — tag-based filtry (otevírka, soupeř, výsledek, datum).
- **PGN editor** — anotace + variace, export.
- **Vlastní GM partie databáze** — Mega Database / TWIC import.

## Distribuce / komunita

- **Cloud deploy** (Render / Fly.io) — public read-only verze.
- **Online multiplayer** — mimo scope MVP, ale teoreticky možné přes WebSockets + Stockfish-backed anti-cheat. **Argument proti:** lichess to dělá líp, nemá smysl konkurovat.
- **Tournament management** — pairing (Swiss / round-robin), live results.

## Mimo šach (možné spin-off)

- **LaTeX export** partií / diagramů — viz existující `C:\Users\mrkla\source\latex-chess`.
