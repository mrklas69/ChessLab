"""Klasifikace tahů na základě Stockfish eval per ply.

Lazy on-demand: `POST /api/games/{id}/classify` vyvolá `get_or_classify_game()`,
to zkontroluje cache v DB (`move_evals` tabulka), pokud chybí → zanalyzuje
celou partii Stockfishem (persistent engine, 1× spawn) a uloží do DB.
Re-classify s jiným time_per_move přepíše záznamy (INSERT OR REPLACE).

**Win-probability sigmoid** (lichess formula): eval (cp) → winning_chances
[-1, +1] → win % [0, 100] z pohledu bílého. Pro tah hráče (bílý/černý) se
spočítá drop ve win % z jeho perspektivy mezi pozicí PŘED a PO tahu.

**Klasifikační thresholdy** (drop ve win %):

  - `blunder`   ≥ 20 %  (??)  — kritická chyba (visí materiál, ztracená pozice)
  - `mistake`   ≥ 10 %  (?)   — jasná chyba (suboptimální tah měnící hodnocení)
  - `inaccuracy` ≥ 5 %  (?!)  — drobná nepřesnost
  - `good`      ≥ 2 %         — solidní tah (pod chyboslo, ale ne optimal)
  - `best`      < 2 %  (✓)    — engine-best tah (nebo prakticky ekvivalentní)

Lichess defaults sahají do ?! / ? / ?? — my přidáváme good/best jako vizuální
tagy pro UX (uživatel vidí, že tah byl 'best' v zelené, ne jen 'no tag').

**Mate handling**: mate_in se mapuje na ±100 % win pct (zjednodušení vůči
lichess lineárnímu scale — pro účely klasifikace tahů je rozdíl mizivý).
**Game-over koncové pozice**: reconstruct board z FEN, mat → 100 % matujícímu,
draw → 50 % oběma. Vyhneme se hluché "wp_after = 50 %" defaultu, který by
oklasifikoval mat jako blunder.
"""

from __future__ import annotations

import math

import chess

from chesslab.engine import analyse_game_fens
from chesslab.games import (
    MoveEval,
    get_game_pgn,
    get_move_evals,
    has_classification,
    insert_move_evals,
)
from chesslab.pgn import parse_pgn

# === Win-probability sigmoid (lichess formula) ==============================
#
# Konstanta z lichess-org/lila/modules/analyse: empiricky tunovaná na lidských
# partiích, kalibruje "kolik cp = jaká win rate". cp = +100 → ~59 % win,
# cp = +500 → ~85 %, cp = +1000 → ~97 %. Symetrické pro mínusy.
_SIGMOID_COEFFICIENT = 0.00368208


def _cp_to_winning_chances(cp: int | None, mate_in: int | None) -> float:
    """Eval (perspektiva bílého) → winning_chances v [-1, +1].

    +1 = bílý vyhraje jistě, 0 = vyrovnané, -1 = černý vyhraje jistě.

    Mate: zjednodušení vůči lichess (lineární scale po mate_in) na ±1 —
    pro klasifikaci tahů rozdíl mate-in-1 vs mate-in-30 prakticky nedělá nic.

    cp=None & mate_in=None: vrátí 0 (neutrální). Volající má za úkol tento
    case ošetřit (game_over pozice → reconstruct board → wp dle vítěze).
    """
    if mate_in is not None:
        # mate_in > 0 = bílý matuje (zisk); < 0 = černý matuje (ztráta bílého)
        return 1.0 if mate_in > 0 else -1.0
    if cp is None:
        return 0.0
    # Lichess sigmoid: 2 / (1 + e^(-k*cp)) - 1. Pythonův math.exp si poradí
    # s extreme cp (exp(-36) ≈ 1e-16, exp(36) ≈ 1e16) bez overflow.
    return 2.0 / (1.0 + math.exp(-_SIGMOID_COEFFICIENT * cp)) - 1.0


def _cp_to_win_pct_white(cp: int | None, mate_in: int | None) -> float:
    """Win % bílého v [0, 100] z eval. Tenký wrapper nad winning_chances."""
    return 50.0 + 50.0 * _cp_to_winning_chances(cp, mate_in)


# === Klasifikační thresholdy (drop ve win % z pohledu táhnoucího hráče) =====
#
# Lichess oficiální: ?! ≥ 5, ? ≥ 10, ?? ≥ 20. My přidáváme good (≥ 2) a best
# (< 2) jako vizuální kategorie pro UX — uživatel vidí každý tah obarvený,
# ne jen ty chybové. Best ≈ engine match (drop < 2 % je v rozumném pásmu
# noise, prakticky nerozlišitelné od engine top-1).
_BLUNDER_THRESHOLD = 20.0
_MISTAKE_THRESHOLD = 10.0
_INACCURACY_THRESHOLD = 5.0
_GOOD_THRESHOLD = 2.0


def _classify_drop(wp_drop: float) -> str:
    """Klasifikace dle drop ve win % (z pohledu hráče, který táhl).

    `wp_drop` kladné = hráč si pohoršil (zhoršení win %). Záporné (zlepšení)
    se klasifikuje jako 'best' — žádná penalizace za "měl jsem vyhraně, teď
    mám ještě vyhraně" (engine match nebo lepší než engine, vzácné ale stane se).

    Vrací string konstantu, ne enum — SQLite drží TEXT, JSON serializace
    triviální. Validace správnosti hodnoty je na konzumentovi.
    """
    if wp_drop >= _BLUNDER_THRESHOLD:
        return "blunder"
    if wp_drop >= _MISTAKE_THRESHOLD:
        return "mistake"
    if wp_drop >= _INACCURACY_THRESHOLD:
        return "inaccuracy"
    if wp_drop >= _GOOD_THRESHOLD:
        return "good"
    return "best"


# === Game-over wp (mat / draw v koncové pozici) =============================


def _terminal_wp_white(fen: str) -> float:
    """Win % bílého [0, 100] pro koncovou pozici dle stavu boardu.

    Volá se jen když eval = None (Stockfish nemá co analyzovat = game_over).
    Reconstruct board → mat dává 100 % vítězi, draw 50 %.

    Mat: po posledním tahu je na tahu strana, která je v matu (= prohrála).
    Tj. matoval ten DRUHÝ → vítěz je opačné barvy než `board.turn`.
    """
    board = chess.Board(fen)
    if board.is_checkmate():
        # Vítěz = strana, která NENÍ na tahu (mat dal hráč, který právě táhl).
        winner_is_white = board.turn == chess.BLACK
        return 100.0 if winner_is_white else 0.0
    # Stalemate, insufficient material, fivefold, 75-move — všechno draw.
    return 50.0


# === Hlavní orchestrace =====================================================


def classify_game(pgn: str, time_per_move: float = 0.3) -> list[MoveEval]:
    """Z PGN → list MoveEval (po jednom záznamu per ply, 0..N).

    Args:
        pgn: PGN partie (validní formát, parse_pgn ho zpracuje).
        time_per_move: budget pro Stockfish per pozici v sekundách.
            Default 0.3 ≈ depth 14-17 (rozumný kompromis rychlost / přesnost).

    Returns:
        List MoveEval délky N+1 (ply 0 = startpos až ply N = koncová pozice).
        Záznam pro ply=0 má classification=None (žádný tah jí nepředchází).

    Raises:
        ValueError: pro neplatný PGN (parse_pgn).
        FileNotFoundError: pokud Stockfish binárka neexistuje.
    """
    # 1. PARSE PGN → fens (ply 0 = startpos, ply 1..N = pozice po každém tahu)
    parsed = parse_pgn(pgn)
    fens = [parsed.starting_fen] + [m.fen_after for m in parsed.moves]

    # 2. ANALÝZA — persistent Stockfish, sériová série analyses (řádově
    # rychlejší než spawn-per-pozici, viz analyse_game_fens docstring).
    position_evals = analyse_game_fens(fens, time_per_move=time_per_move)

    # 3. KLASIFIKACE — per ply počítáme drop ve win % z pohledu hráče, který táhl.
    # Předpočítáme wp_white pro každou pozici (včetně game_over fallback).
    wp_white: list[float] = []
    for i, pe in enumerate(position_evals):
        if pe.score_cp is None and pe.mate_in is None:
            # game_over — žádný eval, fallback dle stavu boardu (mat/draw).
            wp_white.append(_terminal_wp_white(fens[i]))
        else:
            wp_white.append(_cp_to_win_pct_white(pe.score_cp, pe.mate_in))

    results: list[MoveEval] = []
    for i, pe in enumerate(position_evals):
        if i == 0:
            # Startovní pozice — žádný tah jí nepředchází, klasifikace neexistuje.
            results.append(
                MoveEval(
                    ply=0,
                    eval_cp=pe.score_cp,
                    mate_in=pe.mate_in,
                    classification=None,
                )
            )
            continue

        # Hráč, který táhl ply `i`: ply 1, 3, 5… = bílý; ply 2, 4, 6… = černý.
        # (Konvence parse_pgn: ply 1 = 1. tah bílého.)
        player_is_white = (i % 2) == 1
        wp_before_white = wp_white[i - 1]
        wp_after_white = wp_white[i]

        # WP z perspektivy hráče: pro bílého = wp_white, pro černého = 100 - wp_white.
        if player_is_white:
            wp_drop = wp_before_white - wp_after_white
        else:
            wp_drop = (100.0 - wp_before_white) - (100.0 - wp_after_white)
            # = wp_after_white - wp_before_white. Algebraicky stejné jako
            # symetrický flip, necháváme explicitní zápis pro čitelnost.

        results.append(
            MoveEval(
                ply=i,
                eval_cp=pe.score_cp,
                mate_in=pe.mate_in,
                classification=_classify_drop(wp_drop),
            )
        )

    return results


def get_or_classify_game(game_id: str, time_per_move: float = 0.3) -> list[MoveEval]:
    """Lookup-or-compute pro klasifikaci partie. Cache v DB.

    Pokud klasifikace existuje (`has_classification`), vrátí cached.
    Jinak: fetch PGN, klasifikuje, uloží, vrátí.

    Args:
        game_id: ID partie v `games` tabulce.
        time_per_move: budget per pozici (jen pro fresh classify, cached jede).

    Returns:
        List MoveEval pro celou partii (ply 0..N).

    Raises:
        KeyError: pokud `game_id` v `games` neexistuje.
        FileNotFoundError: pokud chybí Stockfish (jen pro fresh classify).
    """
    if has_classification(game_id):
        return get_move_evals(game_id)

    pgn = get_game_pgn(game_id)
    if pgn is None:
        raise KeyError(f"Game id '{game_id}' nenalezeno v DB.")

    evals = classify_game(pgn, time_per_move=time_per_move)
    insert_move_evals(game_id, evals, time_per_move=time_per_move)
    return evals
