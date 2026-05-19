# ENGINES.md — princip & hranice vlastních enginů

Trvalá reference pro vývoj vlastních enginů v `src/chesslab/engines/`. Každý engine reprezentuje **konkrétní algoritmický stupeň**. Cílem **NENÍ** maximalizovat ELO za každou cenu, ale **optimalizovat ELO / LOC při zachování principu**.

Když chceš přidat feature, polož si otázku: *patří tahle technika ještě do principu tohoto enginu, nebo už drifting do jiné paradigma?* Pokud drifting → nový engine, ne enhancement existujícího.

## Random v0 (`chesslab-random`)

| | |
| --- | --- |
| **Princip** | Náhodný legální tah, žádná evaluace, žádný search. |
| **Strop** | Cokoli kromě `random.choice(legal_moves)`. |
| **Baseline** | Spodní hranice síly v ChessLab Elo žebříčku (~742 Elo, anchor stockfish:5 = 1500). |

## Greedy v1 / „Chamtivec" (`chesslab-greedy`)

| | |
| --- | --- |
| **Princip** | 1-ply lookahead nad materiálovou funkcí + minimální taktické bonusy (mate/stalemate/check), aby Greedy neztrácel KvK koncovku proti Random. Žádný protihráčův tah do úvahy. |
| **Strop** | Hloubka > 1, α-β, quiescence, jakákoli forma minimaxu. Pokud bys přidal „co odpoví soupeř" → už to není Greedy. |
| **Baseline** | +300 Elo nad Random (~926 Elo). Demo, že i triviální material counting drasticky překoná náhodu. |

## Minimax v3.x (`chesslab-minimax`)

| | |
| --- | --- |
| **Princip** | N-ply negamax + α-β + **klasický HCE engine** (Hand-Crafted Evaluation). Patří sem všechno, co tvoří *standardní textbook + modern computer chess* před érou neural networks. |
| **Strop — eval** | Klasické features: material, PSQT (piece-square tables), mobility, king safety, pawn structure, endgame king-tropism. **Mimo princip:** NNUE / neural net eval (jiné paradigma — patří do samostatného „NNUE" enginu). |
| **Strop — search** | Negamax + α-β + quiescence + standardní enhancements: iterative deepening, transposition table, PV move ordering, MVV-LVA, killer moves, history heuristic, aspiration windows, null move pruning, LMR (Late Move Reductions), futility pruning, SEE (Static Exchange Evaluation). **Mimo princip:** MCTS (Monte-Carlo Tree Search — jiné paradigma). |
| **Strop — knowledge** | **Mimo princip:** opening book (Polyglot .bin), endgame tablebases (Syzygy). Patří k samostatným enginům typu „Booked v0" / „Tablebase v0", které delegujou na Minimax mimo book/tb. |
| **Aktuální** | v3.2 (~1250 Elo) — má: depth 2+ID + α-β + quiescence + endgame eval + TT + PV + killer/history. |
| **Snapshoty** | v2.7 (textbook depth-2 + MVV-LVA, ~1183 Elo), v3.1 (decisive TT/PV milestone, ~1225 Elo). |

## Pravidla pro evoluci

1. **Před každým enhancementem zvaž ELO/LOC poměr.** +24 Elo za 200 řádků (killer/history v Pythonu) je horší než +56 Elo za 80 řádků (TT+PV v Pythonu).
2. **Sparring sample size**: minimum 100+ partií pro decisive závěr. Time budget matters — výsledek se může převrátit mezi 0.05s a 0.15s/tah. Viz `MEMORY.md`.
3. **Snapshoty drž jako benchmark variant**, ne mrtvou historii. Když enhancement nepřinese decisive výsledek (p ≥ 0.05) ani didakticky nereprezentuje jiný stupeň → smazat snapshot, historie zůstává v gitu.
4. **Drifting do jiné paradigma → nový engine.** Neimplementuj NNUE / MCTS / opening book *dovnitř* `minimax_engine.py`. Vytvoř samostatný modul (např. `nnue_engine.py`) + entry point v pyproject.

## Pipeline pro nové enginy mimo Minimax (až někdy)

| Plánovaný engine | Princip | Vztah k Minimax |
| --- | --- | --- |
| **Booked v0** | Polyglot opening book lookup; pokud out-of-book → delegate na Minimax. | Wrapper kolem Minimax. |
| **Tablebased v0** | Syzygy 6-figure tablebase v koncovce; jinak delegate na Minimax. | Wrapper kolem Minimax. |
| **NNUE v0** | Neural net eval (768→256→32→1 nebo podobně), search = Minimax-style negamax+α-β, ale eval = NN forward pass. | Vlastní engine, sdílí search infrastructure (přes refaktor `_protocol.py`?). |
| **MCTS v0** | Monte-Carlo Tree Search s rollouty (random / policy). | Úplně jiný search, jen sdílí UCI loop. |

Tohle je jen pipeline, žádný z nich není v aktivním scope. Viz [IDEAS.md](IDEAS.md).
