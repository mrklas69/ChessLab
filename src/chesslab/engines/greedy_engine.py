"""ChessLab Engine v1: Greedy Material.

1-ply lookahead: pro každý legální tah push, evaluuj materiál z naší
perspektivy, vyber max. Random tie-break (deterministic engine = pořád
stejná partie, čili nuda).

Bere vždy nejhodnotnější figuru, kterou může vzít. Pokud nic k braní → vybírá
náhodný z tahů, které neztratí materiál (= max(material_after) je startovní
materiál). Slabost: nevidí žádnou recapture, takže obětuje dámu za pěšce, když
soupeř ji může vzít druhým tahem (Greedy nemá search of opponent response).

**Bonus heuristiky** (jinak by Greedy v koncovce KQR vs K nedoseknul mat — všechny
tahy mají stejný material balance a king-hunt by nikdy nedostal preferenci):
  - Mat (po pushu `is_checkmate()`) → MATE_BONUS = +100000 cp. Preferuj nad vším.
  - Pat → -MATE_BONUS. V koncovce s převahou je pat ztracená výhra, vyhni se.
  - Šach → +CHECK_BONUS = 30 cp. Tlačí soupeřova krále, zúží jeho legální tahy.

Standardní piece values v centipawnech (1 pawn = 100 cp), kompatibilní se
Stockfish eval skórem. Král záměrně != ∞ — všechny tahy enginu jsou legální
(nelze ztratit krále kvůli pravidlu), takže King value nikdy nepřispěje k
diff a může být 0. Necháme 0 pro KISS.
"""

from __future__ import annotations

import random

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Greedy v1"
ENGINE_AUTHOR = "Jan Mrklas"

# Materiálové hodnoty v centipawnech. Hodnoty přibližně podle Larryho Kaufmana
# (běžně používaný standard v engine community). Král = 0 (nelze ho ztratit
# pravidlově — viz docstring modulu).
_PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Mat = "preferuj nade vším". Vyšší než suma celého materiálu na šachovnici
# (~3940 cp na začátku), aby Greedy nikdy nevolil capture místo matu.
_MATE_BONUS = 100_000
# Pat = "vyhni se, pokud máš výběr". Stejně velké, aby Greedy s převahou
# nepatoval (= ztracená výhra). Při nucené pozici se i tak vybere nejmenší zlo.
_STALEMATE_PENALTY = 100_000
# Šach = malý bonus. Drží Greedyho v ofenzivě, tlačí soupeřova krále.
# 30 cp je menší než hodnota pěšce — nezpůsobí oběti, jen tie-break.
_CHECK_BONUS = 30


def _material(board: chess.Board, color: chess.Color) -> int:
    """Spočítá celkovou materiálovou hodnotu figur dané barvy v centipawnech.

    `board.piece_map()` vrací dict {square: Piece}. Iterujeme přes naše figury
    a sčítáme jejich hodnoty.
    """
    total = 0
    for piece in board.piece_map().values():
        if piece.color == color:
            total += _PIECE_VALUES[piece.piece_type]
    return total


def _evaluate_after_move(board: chess.Board, move: chess.Move, our_color: chess.Color) -> int:
    """Push tah, spočítej eval (materiál + bonusy) z naší perspektivy, pop.

    Vrací net score po tahu. Vyšší = pro nás lepší.

    Bonusy (viz konstanty modulu):
      - is_checkmate → +MATE_BONUS (preferuj mat nade vším)
      - is_stalemate → -STALEMATE_PENALTY (pat s převahou = zmařená výhra)
      - is_check → +CHECK_BONUS (drobný tie-break, tlačit krále)

    Push/pop pattern je standard v chess engines — chess.Board je mutable,
    musíme po každém try vrátit stav. Žádný kopírovaní celého boardu = rychlé.
    """
    board.push(move)
    try:
        # Mat / pat se kontroluje PŘED materiálem — bonus přebije všechno ostatní
        # (mate i ztratí všechen materiál pro mat? Ano, jistěže).
        if board.is_checkmate():
            return _MATE_BONUS
        if board.is_stalemate():
            return -_STALEMATE_PENALTY

        our_mat = _material(board, our_color)
        their_mat = _material(board, not our_color)
        score = our_mat - their_mat

        # Šach bonus se přidává k materiálu — nepřebije capture, ale rozhodne
        # tie-break ve prospěch agresivního tahu.
        if board.is_check():
            score += _CHECK_BONUS

        return score
    finally:
        # finally garantuje pop i kdyby _material hodil výjimku (paranoidní,
        # ale levné — push/pop jsou symetrické a leak by rozbil engine).
        board.pop()


def choose_move(board: chess.Board) -> chess.Move | None:
    """Vybere tah s nejvyšším material balance po pushu. Random tie-break.

    Algoritmus:
      1) Pro každý legal move spočítej eval (delta materiálu z naší perspektivy).
      2) Vyber tahy s max eval (může jich být víc — typický opening position
         má 20 stejně hodnocených tahů, všechny nic neberou ani neztrácí).
      3) Z best moves vyber random — bez tie-breaku by engine hrál vždy stejně
         a partie by se zacyklily v opakování (drawh by triple-repetition).

    Vrací None pokud žádný legální tah neexistuje (mat/pat → arena by `go`
    neměla poslat, ale defenzivně).
    """
    legals = list(board.legal_moves)
    if not legals:
        return None

    our_color = board.turn

    # Spočítej eval pro každý move. Komprehenze drží párování (move, eval).
    scored: list[tuple[int, chess.Move]] = [
        (_evaluate_after_move(board, m, our_color), m) for m in legals
    ]

    # Vybrat best score, pak všechny tahy s tímhle score (tie-break množina).
    best_score = max(s for s, _ in scored)
    best_moves = [m for s, m in scored if s == best_score]

    return random.choice(best_moves)


def main() -> None:
    """Entry point pro `chesslab-greedy` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
