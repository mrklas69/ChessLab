"""ChessLab Engine v0: Random Mover.

Náhodný UCI engine — slouží jako:
  1) baseline v Areně (relativně slabý protivník, perf rating diff dává smysl
     i proti Stockfish skill 0),
  2) sanity test, že UCI integrace s ChessLab Arenou / Play funguje.

Strategie: random.choice z legal_moves. Nic víc. UCI protokol řeší sdílený
modul `_protocol.run_uci_loop`.
"""

from __future__ import annotations

import random

import chess

from chesslab.engines._protocol import run_uci_loop

ENGINE_NAME = "ChessLab Random v0"
ENGINE_AUTHOR = "Jan Mrklas"


def choose_move(board: chess.Board) -> chess.Move | None:
    """Vybere náhodný legální tah. None pokud žádný neexistuje (mat/pat).

    random.choice() potřebuje sekvenci, ne generator → konverze na list.
    `legal_moves` je relativně levné (max ~218 tahů v běžné pozici, typicky
    20-40 v middlegame).
    """
    legals = list(board.legal_moves)
    if not legals:
        return None
    return random.choice(legals)


def main() -> None:
    """Entry point pro `chesslab-random` console script."""
    run_uci_loop(name=ENGINE_NAME, author=ENGINE_AUTHOR, choose_move=choose_move)


if __name__ == "__main__":
    main()
