"""ChessLab Engine v2.8 (snapshot): Minimax + alpha-beta + endgame heuristika + quiescence (captures + checks) + MVV-LVA.

**Zamražený snapshot v2.8** — slouží jako baseline pro sparring proti novějším
verzím (v3.0+). Engine_id ``chesslab-minimax-v28`` v ratings DB drží
historický rating; engine_id ``chesslab-minimax`` je vždy "current ChessLab
minimax" (postupně přepisovaný novou verzí).

Logika je 1:1 kopie ``minimax_engine.py`` ze stavu před v3.0 — viz git
historie commitu, který tento snapshot vytvořil. Při budoucích sparring testech
(v3.1 vs v3.0, atd.) přidáme analogický ``minimax_engine_v30.py`` snapshot.

Detailní rationale pro každou subkomponentu je v původním docstringu — kopíruje
ho. Nech ho být nezměněný, ať diff proti staré verzi je čistý.

---

Negamax framework s alpha-beta pruning. Hloubka 2 plies (vidíme náš tah +
soupeřovu odpověď), v listech **quiescence search** (pokračujeme v "neklidných"
pozicích = captures + checks v prvních 2 plies quiescence, dokud nedojdeme k
quiet pozici), tahy seřazené přes **MVV-LVA** (silné captures první → α-β
cutoffs cuttne dřív). Eval = material (Greedy v1 hodnoty) + check bonus +
endgame king-tropism + edge distance pro silnější stranu v koncovce.

**Motivace nad Greedy v1.** Greedy 1-ply lookahead nevidí soupeřovu odpověď.
Konkrétní zjištění z testu 2026-05-17 (Greedy v1 vs Random, KP-vs-K koncovka):
Greedy hodnotil všechny pěšcové tahy stejně (+100 cp, pěšec pořád na šachovnici),
random tie-break ho tlačil ke králi místo k promoci → 10/10 INSUFFICIENT_MATERIAL
remíz. Minimax v hloubce 2 vidí Random response — tahy nechávající pěšec v
ohrožení dostanou nižší skóre (Random ho příští ply vezme) → preferuje
bezpečné postupy.

**Motivace v2.5 nad v2.0.** Test v2.0 vs Greedy v1 (2026-05-17): perf rating
+190.8 Elo, ale **až 50 % partií končilo INSUFFICIENT_MATERIAL** — depth 2
nevidí mate-distance v KR vs K (mat je 20-30 plies daleko), silnější strana
nepostupovala. Cheap fix bez zvyšování depth: v endgame pozici (málo materiálu)
přidat do eval bonus za vzdálenost soupeřova krále od středu (tlačit do rohu)
a blízkost našeho krále k soupeřovu (king opposition). Standardní endgame
mating technika — viz `_endgame_bonus`.

**Motivace v2.6 nad v2.5.** Test v2.0 vs Greedy odhalil **horizon effect** —
konkrétně PGN z game 8 (Minimax-Black vs Greedy-White, 35 plies, prohra):
sekvence Qd5 → Qa4+ check → vynucený Qb5 → Bxb5+ je 4-ply takticky vynuceno,
depth 2 ji nevidí a v listu eval ukáže "fake" materiálovou výhodu, která se
za horizontem ztratí. Quiescence search (extend search v "neklidných"
pozicích = aktivní captures) by tohle chytl bez generálního zvýšení depth.
Standardní implementace: po dosažení depth=0 pokračuj jen v capture tazích
až do quiet pozice. Viz `_quiescence`.

**Motivace v2.7 nad v2.6.** Move ordering přes **MVV-LVA** (Most Valuable
Victim - Least Valuable Aggressor). Alpha-beta cutoff oře větve, jakmile
najdeme score >= beta — ale efektivita pruningu dramaticky závisí na pořadí
prozkoumávaných tahů. Pokud silný capture (PxQ = vyhráváme dámu) projdeme
první, dostane high score → cutoff na zbytek tahů v této vrstvě. Pokud ho
necháme jako poslední, projdeme všechny tiché tahy zbytečně.

MVV-LVA score: `victim_value * 10 - aggressor_value`. Multiplikátor 10
zaručí, že vždy dominuje victim (PxQ=8900 > QxR=4100 > QxB=3270), aggressor
jen rozhoduje tie-break mezi captures se stejnou obětí. Non-captures dostanou
score 0 a jdou po všech captures.

Při fixní depth 2 MVV-LVA **nezvedne sílu** (pořadí nemění best move, jen
rychlost) — přínos uvidíme až ve v3 (depth 3) nebo s iterative deepening.
Cílem v2.7 je čistá izolace přínosu ordering jako stavebního kamene.

**Motivace v2.8 nad v2.7.** Quiescence v v2.6 zachytila horizon effect v
**capture** sekvencích, ale slepá je k **forced check** sekvencím. Klasický
příklad: backrank mate-in-2 přes Qd1+ → Re1 (forced) → Qxe1#. v2.7 vidí Qd1+
jako quiet tah (= non-capture), proto neaktivuje quiescence search, jde do
běžného eval listu a ukáže "fake" remízový skór. v2.8 přidá **checking
non-captures** do quiescence (v prvních 2 plies quiescence, pak už jen captures
proti search explosion).

Implementačně: v non-check větvi `_quiescence` (po stand-pat) doplníme do moves
non-capture moves, které `board.gives_check(move)`. Dedup přes set (capture
s checkem se generuje 2× jinak). Cap `_QUIESCENCE_MAX_CHECK_PLIES = 2` zaručí,
že po 2 plies quiescence už jen captures (jako v2.7) — bez něj by checks
v komplikovaných pozicích explodovaly search a UCI movetime přetekl.

**Bez SEE filter** (= "skip losing checks jako Qh5+ na chráněném poli") —
KISS, alpha-beta vyhodí hloupý check sám: recapture v dalším ply ukáže
záporný score, jen je search trochu pomalejší. SEE by ušetřil compute, ale
přidá ~30-50 řádků; kandidát na v2.9.

**Eval funkce sdílí material konstanty s Greedy v1** — Kaufman materiálové
hodnoty + CHECK_BONUS. Endgame bonus je v2.5 specifický. Mat/pat handling
přes negamax framework (ne přímý bonus jako Greedy).

**Vědomě vyloučeno z v2.8** (kandidáti na v2.9+):

  - **SEE (Static Exchange Evaluation) pruning** — viz výše, KISS.
  - **Killer moves / history heuristic** — non-capture ordering. MVV-LVA
    řeší jen captures; quiet moves jsou v current pořadí python-chess. Killer
    moves (pamatovat 2 tahy, které způsobily β-cutoff ve stejné depth) a
    history heuristic (počítadlo cutoff per from-to) jsou klasická rozšíření.
  - **Iterative deepening + transposition table** — premature optimization
    pro depth 2. ID by automaticky využila zrychlení z MVV-LVA pro jít hloub
    do time budgetu — kandidát na v3.
  - **Mate-distance scoring** — vrátíme ±MATE_SCORE jako pevnou hodnotu,
    search nevybírá nejkratší mat z více options. Pro depth 2 vidíme jen
    mate-in-1, takže OK.
  - **Configurable depth přes UCI option** — sdílený `_protocol.run_uci_loop`
    UCI options nepodporuje. Pro experiment s depth 3 změň `_DEPTH` v tomto
    souboru + `uv sync`.
  - **Positional eval** (PSQT, mobility, king safety) — strict material
    parita s Greedy.

Random tie-break z best moves: stejně jako Greedy. Plně deterministický engine
by způsobil triple-repetition draws v Aréně (N partií ze stejné startovní
pozice → stejné tahy → opakování).
"""

from __future__ import annotations

import math
import random

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Minimax v2.8 (snapshot)"
ENGINE_AUTHOR = "Jan Mrklas"

# Hloubka v plies (= půltahů). 2 = vidíme svůj tah + soupeřovu odpověď.
# Změna na 3 = ~10–20× pomalejší (z ~20 → ~400 leaf nodes typicky pro
# middlegame); v Pythonu se v Aréně při time_per_move 0.05s prakticky nestihne.
_DEPTH = 2

# Material values v centipawnech — Kaufman piece values, identické s Greedy v1.
# Záměrně sdílíme konstanty (DRY), aby srovnání v2 vs v1 měřilo *jen* přínos
# search, ne změnu eval. King = 0 — nelze ztratit pravidlově, eval rozdíl je 0.
_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Mat skóre = "preferuj nade vším" / "vyhni se nade vším". Vyšší než suma
# materiálu na šachovnici (~3940 cp na začátku) → alpha-beta odřízne všechny
# non-mate větve, jakmile mate sequence najde. Symetrické ±.
_MATE_SCORE = 100_000

# Šach bonus — drobný tie-break pro agresivní tahy. Klíčové v koncovkách
# (KQ vs K), kde čistě materiální eval nedělá rozdíl mezi tahy → engine by
# stál na místě. Bonus tlačí soupeřova krále.
# Sémantika v negamax leaf: pokud `stm.is_check()` (= jsme na tahu a soupeř
# nám dal šach), eval -= CHECK_BONUS (z naší perspektivy špatně). Po negaci
# v rodičovské vrstvě → soupeř (= náš parent) vidí +CHECK_BONUS = "dal jsem
# šach, dobře pro mě". Symetrické.
_CHECK_BONUS = 30

# === ENDGAME HEURISTIKA (v2.5) ===
#
# Threshold pro aktivaci endgame mode: total non-pawn non-king material na
# šachovnici (sečteno přes obě barvy) v centipawnech. Pod touto hodnotou
# přidá `_evaluate_for_side_to_move` bonus za king-tropism.
#
# 1300 cp ≈ 4 lehké figury celkem (typicky např. RB vs R, nebo R vs BN).
# Klasická koncovka KR vs K má total = 500 cp → bohatě pod thresholdem.
# Middlegame s plnou sadou figur má total ~7080 cp → bonus se nikdy nezapne
# tam, kde by ovlivnil pozici.
_ENDGAME_MATERIAL_THRESHOLD = 1300

# Minimum materiálové převahy pro aktivaci heuristiky. Slabší strana bonus
# nedostává — nemá smysl ji nutit zatlačit silnějšího do rohu, naopak by ji
# to mátlo. Práh 100 cp = pawn převaha (pod tím je remízová pozice).
_ENDGAME_MIN_ADVANTAGE = 100

# Bonus per square: 12 cp × Chebyshev distance jejich krále od středu (max 3 = roh).
# Max příspěvek = 36 cp (jejich král v rohu). Menší než pawn → eval calculus
# pro materiál stále dominuje, bonus jen rozhoduje tie-break tahů krále.
_KING_EDGE_BONUS_PER_SQUARE = 12

# Bonus za king proximity: (8 - king_distance) × 3. Distance 1 (těsně vedle) =
# +21 cp, distance 7 (opačné rohy) = +3 cp. Standardní opozice = +18-21 cp.
_KING_PROXIMITY_BONUS_PER_SQUARE = 3

# === QUIESCENCE LIMITS (v2.6) ===
#
# Hard depth cap pro quiescence search. Bez něj může quiescence v "neklidných"
# pozicích (multi-capture exchange sequences) zabrat desítky plies — UCI go
# movetime 50ms pak může přetéct (arena timeoutuje, partie se ztratí).
# Hodnota 8 = 4 párových výměn — bohatě stačí pro typický exchange sequence
# (Q×P P×Q × ... ne, my máme jen 1 výměnu na queen → 2 plies). Stockfish
# defaultně používá ~6, dáváme 8 jako pojistku.
_QUIESCENCE_MAX_PLIES = 8

# === QUIESCENCE CHECKS (v2.8) ===
#
# Max ply v quiescence, kde generujeme i non-capture checking moves (mimo
# captures). Pak už čistě captures — checks v hlubších plies generují search
# explosion (každý check má v průměru 5-10 legálních responsí, kde každý
# může být další check → exponenciální branching).
#
# Hodnota 2 = my dáme check (ply 0) + soupeř odpoví (ply 1), pak už jen
# captures. Stačí pro 2-tahové taktické sekvence (Qxh7+ Kxh7 = capture
# check, vidí ho 1-ply quiescence; Rxe8+ Kxe8 dtto). Forced mate sekvence
# přes 2+ checks ne, ale ty pokrývá main search depth 2.
_QUIESCENCE_MAX_CHECK_PLIES = 2

# === MOVE ORDERING (v2.7) ===
#
# MVV-LVA multiplier: victim_value × MULT - aggressor_value. Větší multiplier
# = silnější emphasis na victim (nezáleží na aggressor, jen na tom, kolik
# vyhrajeme). 10 je standardní hodnota — zaručí, že žádná aggressor differencí
# nepřebije rozdíl ve victim tier (PxQ = 8900 > QxR = 4100 > QxB = 3270).
# Aggressor ovlivňuje jen tie-break mezi captures se stejnou obětí
# (PxN > BxN > NxN > RxN > QxN, protože "vyhrát nějakou figuru levně" je
# obecně lepší než vyhrát ji draho — kdyby ji soupeř recaptureoval, ztrácíme míň).
_MVV_LVA_VICTIM_MULT = 10


def _material_balance(board: chess.Board, our_color: chess.Color) -> int:
    """Material diff (naše - soupeř) v centipawnech.

    Jeden průchod přes piece_map (dict {square: Piece}), nasčítá obě strany
    s opačnými znaménky podle barvy.
    """
    score = 0
    for piece in board.piece_map().values():
        value = _PIECE_VALUES[piece.piece_type]
        if piece.color == our_color:
            score += value
        else:
            score -= value
    return score


def _endgame_bonus(board: chess.Board) -> int:
    """Endgame king-tropism bonus z perspektivy strany na tahu (stm).

    Aktivuje se jen v koncovce (total non-pawn material ≤ threshold) **A** jen
    pokud má stm materiálovou převahu ≥ pawn. Bonus tlačí soupeřova krále do
    rohu (edge distance) a přibližuje našeho krále k jeho (opozice) — standardní
    mating pattern pro KR vs K / KQ vs K koncovky, které depth-2 search jinak
    neumí dotáhnout (mat je 20-30 plies daleko, nevidíme).

    Sémantika "for side to move": stejná konvence jako `_evaluate_for_side_to_move`.
    Bonus je z pohledu stm — pokud stm je silnější strana, dostane plus
    (motivuje její tahy do správného směru); pokud stm je slabší strana, bonus
    je 0 (žádná deformace její eval).

    Vrátí 0 mimo endgame nebo bez převahy → integrace do eval je bezpečná
    i v middlegame (nikdy nezasáhne tam, kde by ovlivnil materiální calculus).

    Max bonus ~57 cp (36 edge + 21 proximity) — strop pod hodnotou pěšce, takže
    materiál stále vede; bonus rozhoduje **jen** tie-break mezi tahy, které
    materiál nemění.
    """
    our_color = board.turn

    # Total non-pawn non-king material — jeden průchod přes piece_map.
    # Pawns a kings nepočítáme: pawns mají vlastní endgame dynamiku (promoce),
    # kings jsou vždy 2 na šachovnici. Pro endgame mode chceme měřit "kolik
    # útočných figur zbývá".
    total_material = 0
    for piece in board.piece_map().values():
        if piece.piece_type in (chess.PAWN, chess.KING):
            continue
        total_material += _PIECE_VALUES[piece.piece_type]

    if total_material > _ENDGAME_MATERIAL_THRESHOLD:
        return 0

    # Bonus jen pro silnější stranu — slabší strana by zatlačení soupeře do
    # rohu nedávalo smysl (chce remízu, ne mat).
    if _material_balance(board, our_color) < _ENDGAME_MIN_ADVANTAGE:
        return 0

    our_king = board.king(our_color)
    their_king = board.king(not our_color)
    # board.king() vrací Optional[int] — None znamená "král chybí na šachovnici".
    # Pravidlově nemožné v reálné partii (král se neztrácí), ale defenzivně
    # ošetříme — kdybychom někdy testovali nepravidlovou pozici, neshodíme engine.
    if our_king is None or their_king is None:
        return 0

    # Chebyshev distance soupeřova krále od *nejbližšího* centrálního pole.
    # python-chess `square_distance` je Chebyshev (max(|df|, |dr|)) — přesně
    # to, co chceme pro "vzdálenost od středu" (král v rohu má dist 3 od d4/e4/d5/e5).
    # Min přes 4 centrální pole protože soupeřův král se může nacházet stejně blízko
    # k d4 jako k e5 — bereme to nejlepší (= nejmenší distance).
    center_squares = (chess.D4, chess.D5, chess.E4, chess.E5)
    their_king_edge_dist = min(
        chess.square_distance(their_king, c) for c in center_squares
    )
    # their_king_edge_dist: 0 (v centru) až 3 (v rohu).

    # Chebyshev distance mezi králi. 1 = přilehlá pole (king opposition není
    # legální, ale "knight's move" distance = 2 je standardní opozice).
    # 7 = opačné rohy.
    king_dist = chess.square_distance(our_king, their_king)

    edge_bonus = their_king_edge_dist * _KING_EDGE_BONUS_PER_SQUARE
    proximity_bonus = (8 - king_dist) * _KING_PROXIMITY_BONUS_PER_SQUARE

    return edge_bonus + proximity_bonus


def _evaluate_for_side_to_move(board: chess.Board) -> int:
    """Statická eval z perspektivy strany na tahu (`board.turn`).

    Volá se v listech minimax stromu. **NEPUSHUJE** — volající už pozici
    pushnul.

    Konvence "for side to move" je standard pro negamax framework: jeden eval
    funkční prototyp, mezi vrstvami se skóre neguje. Eliminuje duplikaci
    max/min logiky.

    Návratové hodnoty:
      - is_checkmate → -MATE_SCORE (stm je v matu, nejhorší možný výsledek).
      - is_game_over (jiný terminál: stalemate / insufficient material /
        fivefold / 75-move) → 0 (remíza, neutrální).
      - jinak → material balance ± CHECK_BONUS (-pokud jsme v šachu) +
        endgame king-tropism bonus (jen pro silnější stranu v koncovce).
    """
    if board.is_checkmate():
        # Stm je obětí matu — z jeho perspektivy nejhorší možný výsledek.
        return -_MATE_SCORE
    # is_game_over() bez `claim_draw` zahrne forced remízy (stalemate,
    # insufficient material, fivefold, 75-move). Claimable (3-fold, 50-move)
    # ne — engine si je v tomto search nemůže nárokovat.
    if board.is_game_over():
        return 0

    score = _material_balance(board, board.turn)

    # Šach z perspektivy stm = stm je *v* šachu = bad. Negace v rodiči udělá
    # symetrický +bonus pro stranu, která šach dala. Viz docstring konstanty.
    if board.is_check():
        score -= _CHECK_BONUS

    # Endgame heuristika (v2.5) — bezpečně vrací 0 mimo koncovku nebo bez
    # převahy, takže middlegame eval zůstává čistě materiální.
    score += _endgame_bonus(board)

    return score


def _mvv_lva_score(board: chess.Board, move: chess.Move) -> int:
    """MVV-LVA skóre pro capture move (v2.7).

    Vyšší skóre = silnější capture = zkusit dřív v alpha-beta search.
    Non-captures dostanou 0 (skončí za všemi captures při sestupném sortu).

    Vzorec: `victim_value * MULT - aggressor_value`. Multiplier garantuje, že
    rozdíl ve victim tier vždy přebije rozdíl v aggressor:
      - PxQ (vyhrát dámu pěšcem): 900*10 - 100 = 8900
      - QxR: 500*10 - 900 = 4100
      - QxB: 330*10 - 900 = 2400
      - RxN: 320*10 - 500 = 2700
    Aggressor jen rozhoduje tie-break mezi captures se stejnou obětí (PxN
    před BxN před QxN — vyhrát figuru levně > vyhrát ji draho, kdyby soupeř
    recaptureoval, ztrácíme míň).

    **En passant edge case**: `board.piece_at(move.to_square)` vrací None
    pro EP capture, protože beraný pěšec stojí na *jiném* poli (vedle
    útočníka, ne v cíli). `board.is_en_passant(move)` to detekuje → victim
    forced na PAWN. Bez tohoto by EP capture dostal score 0 (= non-capture)
    a propadl by za všechny ostatní captures = špatné ordering.
    """
    # En passant: victim je vždy PAWN, ale `piece_at(to_square)` vrátí None
    # (beraný pěšec stojí na sousedním poli, ne v cíli tahu).
    if board.is_en_passant(move):
        victim_value = _PIECE_VALUES[chess.PAWN]
    else:
        victim_piece = board.piece_at(move.to_square)
        if victim_piece is None:
            # Non-capture — score 0, půjde za všemi captures.
            return 0
        victim_value = _PIECE_VALUES[victim_piece.piece_type]

    aggressor_piece = board.piece_at(move.from_square)
    # Legal move vždy má figuru na from_square — defenzivní fallback by
    # ošetřil teoreticky nelegální vstup, ale legal_moves to negarantuje.
    aggressor_value = (
        _PIECE_VALUES[aggressor_piece.piece_type] if aggressor_piece else 0
    )

    return victim_value * _MVV_LVA_VICTIM_MULT - aggressor_value


def _order_moves(board: chess.Board, moves) -> list[chess.Move]:
    """Seřadí tahy sestupně dle MVV-LVA skóre (silné captures první).

    Wraps python-chess move iterator (LegalMoveGenerator nebo PseudoLegal)
    do listu — `sorted()` potřebuje materializovaný container. Pro typický
    middlegame ~30 moves to je trivial overhead, ale ANO, materializuje to
    legal_moves dvakrát (jednou v sortu, jednou nikoliv — generator byl by
    šlo zachovat). KISS: list comprehension stačí.

    Stable sort (Python `sorted`) zachová původní pořadí pro tahy se stejným
    skóre — non-captures (všechny score 0) zůstanou v pořadí python-chess.
    Mezi captures se stejným MVV-LVA score (vzácné) také stabilní.
    """
    return sorted(moves, key=lambda m: _mvv_lva_score(board, m), reverse=True)


def _quiescence(board: chess.Board, alpha: int, beta: int, ply: int = 0) -> int:
    """Quiescence search — pokračuje za depth=0 jen v "neklidných" pozicích.

    Volá se z `_negamax` v listech místo přímého `_evaluate_for_side_to_move`.
    Cíl: vyhnout se horizon effectu (poslední tah v search byl capture, eval
    ukáže "fake" materiální výhodu, kterou recapture v dalším ply zničí).

    **Stand-pat pattern**: hráč na tahu NEMUSÍ nic captureovat. Statická eval
    pozice je jeho lower bound — pokud je už nad beta, soupeř to nahoře nepustí
    (cutoff). Pokud je nad alpha, povýšíme alpha. Pak procházíme jen captures,
    abychom našli lepší (nebo dorazili k quiet pozici).

    **In-check exception**: v šachu stand-pat nedává smysl — musíme udělat něco
    (jinak nelegální), eval pozice není garance ničeho. Generujeme **all legal
    moves** (escape from check), ne jen captures. Bez tohoto by engine "stál
    na místě" v šachu (= ignoroval check) a search by byl nesmyslný.

    **Captures-only** (nimo check): `generate_legal_captures()` vrací captures
    včetně en passant a capture-promotions (zákonné chování python-chess).
    Quiet promotions (pěšec na 8. bez beraní) **NE** — quiescence chce
    minimalismus, quiet tahy patří do main search.

    **Hard depth cap** (`_QUIESCENCE_MAX_PLIES`): bez něj může quiescence v
    multi-capture sequences přetéct UCI movetime budget (0.05s v Aréně). Při
    dosažení capu vrátíme stand_pat (= nejlepší dostupný odhad). Standardní
    obrana v komerčních enginech (Stockfish ~6 plies).

    **Move ordering** (v2.7): MVV-LVA na captures (silnější capture první →
    α-β cutoff dřív). V in-check branchi seřadíme i non-captures (captures
    půjdou first, escape captures jako "vezmu šachujícího" se velmi často
    ukážou jako best response, brzký cutoff šetří search v escape sekvenci).

    **Non-capture checks** (v2.8): v non-check větvi, pokud `ply <
    _QUIESCENCE_MAX_CHECK_PLIES`, doplníme do moves i non-capture tahy, které
    dávají soupeři šach (`board.gives_check`). Dedup přes set (capture + check
    by se generoval 2×). Sortí přes MVV-LVA: captures dostanou positive score
    → půjdou první, checking non-captures = score 0 → půjdou za nimi. Bez SEE
    filtru = "hloupé" checks (Qh5+ na chráněném poli) projdou, α-β je v dalším
    ply odřízne.

    Návratová hodnota: best score pro side-to-move (negamax konvence).
    """
    # Terminál checks — stejně jako v _evaluate / _negamax. Mate/draw mají
    # speciální hodnoty, jinak by stand-pat z _evaluate vracela 0 nebo
    # -MATE_SCORE a duplikovala bychom logiku.
    if board.is_checkmate():
        return -_MATE_SCORE
    if board.is_game_over():
        return 0

    in_check = board.is_check()

    # Hard depth cap. Když narazíme na limit, vrátíme stand_pat (resp. -MATE
    # pokud jsme v šachu = chybí escape v capu rekurze — defenzivní fallback).
    # Tahle hranice se uplatní extrémně zřídka (sequenced exchange > 8 plies),
    # ale chrání nás před timeoutem v patologických pozicích.
    if ply >= _QUIESCENCE_MAX_PLIES:
        if in_check:
            # V šachu a vyčerpaný budget — vrátíme worst case (nemůžeme garantovat
            # escape v rámci limitu). Negamax v parent zvolí jiný tah.
            return -_MATE_SCORE
        return _evaluate_for_side_to_move(board)

    if not in_check:
        # Stand-pat: současný eval je lower bound, hráč může captures vynechat.
        stand_pat = _evaluate_for_side_to_move(board)
        if stand_pat >= beta:
            # Beta cutoff už na stand-pat — soupeř to nahoře nepustí, dál
            # se vůbec nedíváme.
            return beta
        if stand_pat > alpha:
            alpha = stand_pat

        # v2.8: captures + (v prvních N plies) non-capture checks.
        # Set kvůli dedup — capture, který zároveň dává šach (typicky Qxf7+),
        # by se jinak generoval dvakrát. chess.Move je hashable (immutable
        # tuple-like), takže `in set` funguje bez custom __hash__.
        candidates: set[chess.Move] = set(board.generate_legal_captures())
        if ply < _QUIESCENCE_MAX_CHECK_PLIES:
            # board.gives_check(m) je efektivní — python-chess to spočte bez
            # full push (pseudo-push interně, ~O(1) per move). Pro typický
            # middlegame s ~30 legals je to ~30 × O(1) = zanedbatelné.
            for m in board.legal_moves:
                if m not in candidates and board.gives_check(m):
                    candidates.add(m)
        # MVV-LVA ordering: captures (score > 0) první, checking non-captures
        # (score 0) za nimi, stable sort zachová pořadí python-chess uvnitř.
        moves = _order_moves(board, candidates)
    else:
        # In check — žádný stand-pat (eval pozice neplatí jako lower bound,
        # musíme reagovat). Všechny legal moves, aby quiescence chytila i
        # escape sekvence, které končí ziskem materiálu. MVV-LVA seřadí
        # captures (escape přes "vezmu šachujícího" je typicky nejsilnější
        # escape) před non-captures (king moves, blocks).
        moves = _order_moves(board, board.legal_moves)

    for move in moves:
        board.push(move)
        try:
            # Negamax rekurze do dítěte: po pushi je na tahu soupeř, jeho
            # quiescence score negujeme pro naši perspektivu. Swap alpha/beta
            # je standardní (skóre se zrcadlí, hranice se musí přizpůsobit).
            score = -_quiescence(board, -beta, -alpha, ply + 1)
        finally:
            board.pop()

        if score >= beta:
            return beta
        if score > alpha:
            alpha = score

    return alpha


def _negamax(board: chess.Board, depth: int, alpha: int, beta: int) -> int:
    """Negamax search s alpha-beta pruning. Vrací best score pro side-to-move.

    Negamax framework: minimax kde si všechny vrstvy myslí "maximalizuji",
    skóre se mezi vrstvami negují. Eliminuje duplikaci max/min logiky a
    eval funkce vrací score z perspektivy "kdo je na tahu" — tím se logika
    sjednotí.

    Klasický wikipedia framework:
        function negamax(node, depth, alpha, beta):
            if terminal: return heuristic(node)
            for child in children(node):
                score = -negamax(child, depth-1, -beta, -alpha)
                alpha = max(alpha, score)
                if alpha >= beta: break  # cutoff
            return alpha (nebo max score)

    Alpha-beta intuice: alpha = "minimum score, který si stm garantuje".
    Beta = "maximum, který soupeř (rodič) povolí". Pokud najdeme score >= beta,
    soupeř má v rodičovské vrstvě lepší alternativu a nepustí nás sem → cutoff.

    **Mate handling**: vracíme ±MATE_SCORE jako pevnou hodnotu (bez distance
    correction). Pro depth 2 to znamená: search najde mate-in-1, ale když má
    víc cest k matu, vybere arbitrárně. Mate-distance scoring = v2.1.

    **Push/pop pattern**: chess.Board je mutable. Push tah, rekurze, pop —
    `try/finally` zaručí pop i při výjimce uprostřed search (rare, ale levné).
    """
    # Terminál: end-of-game NEBO hloubka 0.
    # is_game_over() je důležitá i při depth > 0 — search bez ní by generoval
    # `legal_moves` z už-skončené pozice (= [] → smyčka by se neprovedla,
    # vrátili bychom -inf místo eval). Cleaner: vyhodnotit explicitně.
    # Pokud je hra u konce, vrať přímou eval (žádná quiescence — captures
    # už nedávají smysl, pozice je definitivně skončená).
    if board.is_game_over():
        return _evaluate_for_side_to_move(board)
    # Pokud došla hloubka, pokračuj quiescence searchem — extend search v
    # captures pozici (řeší horizon effect). Quiescence si sama ošetří
    # in-check edge case (escape from check místo jen captures).
    if depth == 0:
        return _quiescence(board, alpha, beta)

    best = -math.inf
    # MVV-LVA ordering (v2.7): silné captures první → α-β cutoff dřív.
    # Při depth 2 to nezvedne sílu (pořadí nemění best move při full search),
    # jen zrychlí — stavební kámen pro depth 3+ a iterative deepening.
    for move in _order_moves(board, board.legal_moves):
        board.push(move)
        try:
            # Rekurze do dítěte: po pushi je na tahu soupeř. Negamax framework
            # vrátí skóre z jeho perspektivy → negujeme pro naši. Swap alpha/beta
            # je standardní (skóre se zrcadlí, hranice se musí přizpůsobit).
            score = -_negamax(board, depth - 1, -beta, -alpha)
        finally:
            board.pop()

        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            # Beta cutoff — soupeř má nahoře lepší alternativu, dál nehledej.
            # Zbylé tahy v této vrstvě jsou irelevantní pro nadřazený výběr.
            break

    # int() pro typ — math.inf je float, ale po první iteraci je `best` int
    # (score z _evaluate je int). Při zero legal moves bychom vrátili -inf,
    # ale ten případ pokrývá `is_game_over()` výše.
    return int(best)


def choose_move(board: chess.Board, _time_ms: int | None = None) -> chess.Move | None:
    """Top-level výběr tahu. Pro každý legal move spusť negamax v hloubce
    `_DEPTH - 1`, vyber tah s max skórem (random tie-break).

    `_time_ms` UCI budget ignorován — fixed depth 2 search trvá pod 50ms i
    v komplikované pozici, daleko pod arena/play budgetem. Snapshot zachovává
    historické chování (v3.0+ používá time budget pro iterative deepening).

    Důvod, proč zde NEpoužíváme negamax přímo (= rekurzivní volání s vyšší
    hloubkou): negamax vrací int (best score), my chceme zpět *konkrétní tah*.
    Unrolled top-level vrstva je čistá cesta.

    Score dítěte z naší perspektivy: po pushi je soupeř na tahu, child negamax
    vrátí skóre z jeho perspektivy → negujeme pro naši.

    **Root search bez sdílené alphy** (oprava bugu z v2.0-v2.5): každý top-level
    tah se searchuje s freshly (-inf, +inf) oknem. Pokud bychom sdíleli alphu
    mezi iteracemi (= standardní pruning pattern), druhý+ tah by dostal jen
    *upper bound* score z alpha-beta cutoffu, ne přesnou hodnotu. Tie-break přes
    porovnání skóre by pak falešně rozšířil best_moves set (Kxe2 = exact 0,
    Kf1 = cutoff 0 = "nevíme přesně, jen že ≤ 0" → falešný tie → random.choice
    může vybrat Kf1). Pro správný root výběr potřebujeme exact score per tah.

    Cena: top-level pruning ztracen (pruning v child zůstává zachován). Pro
    _DEPTH=2 je root branching factor ~30 tahů × 1 ply pruning navíc = mizivé
    zpomalení. Standardní alternativa (PVS = Principal Variation Search s
    re-search cutoff tahů) je v2.7+ optimalizace; nepotřebujeme ji teď.

    Random tie-break: bez něj by engine hrál vždy stejně a partie v Aréně by
    se zacyklily v trojím opakování → falešná draw rate. Stejný pattern jako
    Greedy v1.
    """
    legals = list(board.legal_moves)
    if not legals:
        # Žádný legální tah → mat/pat. UCI `bestmove 0000` (vyřeší
        # `_protocol.run_uci_loop`).
        return None

    scored: list[tuple[int, chess.Move]] = []

    for move in legals:
        board.push(move)
        try:
            # `_DEPTH - 1` protože top-level již reprezentuje 1 ply do hloubky.
            # Pro _DEPTH=2: search v dítěti s depth=1 → leaf po dalším pushi.
            # Full window (-inf, +inf) per root move — viz docstring, exact
            # score pro každý kandidát je nutný pro správný tie-break.
            score = -_negamax(board, _DEPTH - 1, -math.inf, math.inf)
        finally:
            board.pop()

        scored.append((score, move))

    # Vybrat tahy s maximálním skóre (může jich být víc, hlavně v openingu
    # kde mnoho tahů má identické material). Random tie-break.
    best_score = max(s for s, _ in scored)
    best_moves = [m for s, m in scored if s == best_score]

    return random.choice(best_moves)


def main() -> None:
    """Entry point pro `chesslab-minimax` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
