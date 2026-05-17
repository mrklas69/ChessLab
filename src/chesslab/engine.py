"""Wrapper nad python-chess.engine — analýza FEN pozic Stockfishem.

Spawn-per-request model (KISS): každý dotaz nastartuje vlastní engine přes
`SimpleEngine.popen_uci`, po dokončení ho zavře (context manager).
Žádný persistent state, žádný thread management. Start lag ~50–200ms je
akceptovatelný pro interaktivní použití. Persistentní engine zavedeme,
až se ukáže jako bottleneck (např. při engine arena = stovky pozic).
"""

import os
from pathlib import Path

import chess
import chess.engine
from pydantic import BaseModel, Field

# Default cesta — Windows install Stockfishe. Override přes env STOCKFISH_PATH.
DEFAULT_STOCKFISH_PATH = r"C:\Program Files\stockfish\stockfish-windows-x86-64-avx2.exe"


class EngineAnalysis(BaseModel):
    """Výsledek analýzy jedné pozice — eval + best move + meta."""

    # score_cp a mate_in jsou exkluzivní: vždy jen jedno z nich má hodnotu.
    # Pokud Stockfish vidí mat → mate_in (int, znaménko = strana která matuje).
    # Jinak → score_cp (int v centipawnech, z pohledu bílého).
    score_cp: int | None = Field(
        None,
        description="Skóre v centipawnech z pohledu bílého (kladné = výhoda bílého). None pokud mate.",
    )
    mate_in: int | None = Field(
        None,
        description="Mate v X tazích (kladné = bílý matuje, záporné = černý). None pokud není mate.",
    )
    best_move_uci: str = Field(..., description="Nejlepší tah v UCI notaci, např. 'g1f3'.")
    best_move_san: str = Field(..., description="Nejlepší tah v SAN notaci, např. 'Nf3'.")
    depth: int = Field(..., description="Hloubka prohledávání, kterou engine dosáhl.")
    time: float = Field(..., description="Čas analýzy v sekundách (request budget).")


def _stockfish_path() -> str:
    """Cesta ke Stockfish binárce — env STOCKFISH_PATH nebo default."""
    return os.environ.get("STOCKFISH_PATH", DEFAULT_STOCKFISH_PATH)


def analyse_fen(fen: str, time: float = 1.0) -> EngineAnalysis:
    """Analyzuje FEN pozici Stockfishem, vrátí eval + best move.

    Args:
        fen: pozice v FEN notaci.
        time: budget na analýzu v sekundách (default 1.0s ≈ depth 18–22 na běžném HW).

    Raises:
        ValueError: pro neplatný FEN nebo koncovou pozici (mat/pat/insufficient).
        FileNotFoundError: pokud Stockfish binárka na disku neexistuje.
    """
    # chess.Board(fen) hodí ValueError pro neplatný FEN — validace zadarmo.
    board = chess.Board(fen)

    # Koncové pozice nemají best move → engine analýza nemá smysl, vrať 400.
    if board.is_game_over():
        raise ValueError("Pozice je koncová (mat/pat/insufficient material), engine nemá co analyzovat.")

    sf_path = _stockfish_path()
    if not Path(sf_path).exists():
        raise FileNotFoundError(
            f"Stockfish binárka neexistuje: {sf_path}. "
            f"Nainstaluj Stockfish nebo nastav env STOCKFISH_PATH."
        )

    # Context manager (`with`) zaručí engine.quit() i při výjimce — žádný leak procesů.
    with chess.engine.SimpleEngine.popen_uci(sf_path) as engine:
        info = engine.analyse(board, chess.engine.Limit(time=time))

        # info["score"] je PovScore (Point Of View Score) — relativní k hrajícímu.
        # .white() ho převede na absolutní perspektivu bílého (kladné = bílý lepší).
        score = info["score"].white()
        if score.is_mate():
            mate_in = score.mate()  # int; kladné = bílý matuje, záporné = černý
            score_cp = None
        else:
            mate_in = None
            score_cp = score.score()  # int v centipawnech

        # PV = Principal Variation, list tahů ve formátu chess.Move.
        # PV[0] = nejlepší tah z pohledu enginu.
        best_move = info["pv"][0]
        best_uci = best_move.uci()
        best_san = board.san(best_move)  # SAN musí být počítán PŘED jakýmkoliv push

        return EngineAnalysis(
            score_cp=score_cp,
            mate_in=mate_in,
            best_move_uci=best_uci,
            best_move_san=best_san,
            depth=info.get("depth", 0),
            time=time,
        )
