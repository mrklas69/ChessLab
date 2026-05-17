# IDEAS

Raw nápady / vize / nice-to-have. Z těchto vznikají úkoly do `TODO.md`, **ne přímo kód**.

## Engine

- **Vlastní šachový engine v Pythonu** — minimax + alpha-beta, evaluation function, postupné iterativní zlepšování. Cílem je experimentování / učení, ne konkurovat Stockfishi.
- ~~**v2 Minimax depth 2 + alpha-beta**~~ — **HOTOVO 2026-05-17**, viz DONE. Reálný skok +191 Elo nad Greedy (odhad +400-600 nesplněn — horizon effect na depth 2).
- **v2.1: Quiescence search** — primární motivace přímo z testu Minimax v2: PGN z game 8 (Minimax-Black vs Greedy-White, 35 plies, prohra). Sekvence Qd5 → Qa4+ check → vynucený Qb5 → Bxb5+ je 4-ply takticky vynuceno, depth 2 ji nevidí. Quiescence (extend search v "neklidných" pozicích = aktivní captures/checks) by tohle chytlo bez generálního zvýšení depth. Standardní implementace: po dosažení depth=0 pokračuj jen v capture/check tazích až do quiet pozice.
- **v2.2: Move ordering (MVV-LVA)** — Most Valuable Victim - Least Valuable Aggressor: prioritizuj captures podle "berem velkou figuru levnou". α-β cutoffs se tím dramaticky zlepší (typicky 2-5× rychlejší search se stejnou hloubkou). Předpoklad pro efektivní depth 3+.
- **v3: Depth 3** — po implementaci move ordering. Vidíme my-opp-my = můžeme plánovat dvoutahové kombinace (např. fork přípravy). Bez move ordering by v Pythonu při 0.05s/tah neměl čas dohrát.
- **Endgame heuristika (king tropism + edge distance)** — motivace z testu Minimax v2 vs Greedy v1 (2026-05-17): až 50 % partií končí INSUFFICIENT_MATERIAL i pro Minimax — depth 2 nevidí mate-distance v KR vs K (mat je 20-30 plies daleko), engine se silnější stranou nepostupuje, soupeř promění pěšce nebo se dosaje na K vs K. Cheap fix bez zvýšení depth: v endgame pozici (málo materiálu na šachovnici) přidat do eval bonus za (a) vzdálenost soupeřova krále od středu (tlačit ho do rohu), (b) blízkost našeho krále k soupeřovu (king opposition). Standard endgame eval pattern, ~30 řádků.
- **Engine sparring** — turnaj N enginů, round-robin, ELO výpočet (Bayesian / linear regression). (Areny 1v1 hotové, turnaj = další krok.)
- **Opening book** — DB otevírkové teorie (.bin Polyglot, nebo vlastní z partií).
- **NNUE eval** v Python implementaci (velmi ambiciózní, jen pokud Python výkon nebude blokátor).
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
