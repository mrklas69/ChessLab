# IDEAS

Raw nápady / vize / nice-to-have. Z těchto vznikají úkoly do `TODO.md`, **ne přímo kód**.

## Engine

- **Vlastní šachový engine v Pythonu** — minimax + alpha-beta, evaluation function, postupné iterativní zlepšování. Cílem je experimentování / učení, ne konkurovat Stockfishi.
- **Engine sparring** — turnaj N enginů, round-robin, ELO výpočet (Bayesian / linear regression). (Areny 1v1 hotové, turnaj = další krok.)
- **Opening book** — DB otevírkové teorie (.bin Polyglot, nebo vlastní z partií).
- **NNUE eval** v Python implementaci (velmi ambiciózní, jen pokud Python výkon nebude blokátor).
- **Arena: defaultní vyrovnaný matchup** — místo skill 5 vs 15 (vždy 0%/100%, perf rating N/A) zvážit default např. 5 vs 8 (vyrovnanější, perf rating dá smysl už od první spuštění). Diskuse: demonstrace převahy vs. užitečnost metriky.
- **Arena: live progress** — místo fake odhadu času streamovat per-game výsledky přes SSE. Pro N > 10 by user získal feedback dřív než po 60s.

## Analýza

- **Klasifikace tahů** podle Stockfish: brilliant / great / good / inaccuracy / mistake / blunder (per chess.com / lichess).
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
