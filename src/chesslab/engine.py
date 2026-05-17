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


class PositionEval(BaseModel):
    """Lehký záznam eval pro jednu pozici v partii — bez best move, jen skóre.

    Používá se pro graf eval(ply) přes celou partii (analyse_game_fens).
    Bez best_move = méně dat po síti, rychlejší serializace.
    """

    ply: int = Field(..., description="Index půltahu: 0 = startpos, 1 = po 1. tahu bílého, …")
    # game_over=True signalizuje koncovou pozici, kde engine nebyl puštěn.
    # Score atributy jsou v tom případě None.
    game_over: bool = Field(False, description="True pokud pozice je mat/pat/insufficient → engine neběžel.")
    score_cp: int | None = Field(None, description="Centipawn skóre z pohledu bílého (None pokud mate/game_over).")
    mate_in: int | None = Field(None, description="Mate v X tazích z pohledu bílého (None pokud cp/game_over).")


def _stockfish_path() -> str:
    """Cesta ke Stockfish binárce — env STOCKFISH_PATH nebo default."""
    return os.environ.get("STOCKFISH_PATH", DEFAULT_STOCKFISH_PATH)


def _stockfish_path_or_raise() -> str:
    """Vrátí cestu ke Stockfishi, nebo zvedne FileNotFoundError s nápovědou."""
    sf_path = _stockfish_path()
    if not Path(sf_path).exists():
        raise FileNotFoundError(
            f"Stockfish binárka neexistuje: {sf_path}. "
            f"Nainstaluj Stockfish nebo nastav env STOCKFISH_PATH."
        )
    return sf_path


def _score_to_cp_mate(score: chess.engine.Score) -> tuple[int | None, int | None]:
    """Rozdělí PovScore.white() na (score_cp, mate_in) tuple.

    Pomocná funkce — používá se v analyse_fen i analyse_game_fens, aby
    rozhodovací logika kolem mate vs. cp byla na jednom místě (DRY).
    """
    if score.is_mate():
        return None, score.mate()
    return score.score(), None


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

    sf_path = _stockfish_path_or_raise()

    # Context manager (`with`) zaručí engine.quit() i při výjimce — žádný leak procesů.
    with chess.engine.SimpleEngine.popen_uci(sf_path) as engine:
        info = engine.analyse(board, chess.engine.Limit(time=time))

        # info["score"] je PovScore (Point Of View Score) — relativní k hrajícímu.
        # .white() ho převede na absolutní perspektivu bílého (kladné = bílý lepší).
        score_cp, mate_in = _score_to_cp_mate(info["score"].white())

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


def analyse_game_fens(fens: list[str], time_per_move: float = 0.3) -> list[PositionEval]:
    """Zanalyzuje seznam pozic jedním persistentním Stockfishem.

    Persistent engine = spawn jen 1× (~100 ms overhead), pak series of analyse().
    Pro 80 pozic je to o řád rychlejší než spawn-per-pozici.

    Args:
        fens: list FEN řetězců v pořadí, v jakém se mají analyzovat (typicky
            startpos + pozice po každém půltahu = N+1 záznamů pro partii s N tahy).
        time_per_move: budget na pozici v sekundách (default 0.3 ≈ depth 14–17).

    Returns:
        List PositionEval stejné délky jako vstup. Koncové pozice se neanalyzují
        (engine by se rozbil) — vrátí se s game_over=True a score=None.

    Raises:
        ValueError: pro neplatný FEN v listu (chess.Board to vyhodí samo).
        FileNotFoundError: pokud Stockfish binárka neexistuje.
    """
    sf_path = _stockfish_path_or_raise()

    # Připravíme si boardy předem — validace FEN proběhne tady (rychle, sériově),
    # ne až v půlce dlouhé analýzy. Plus víme, které pozice jsou koncové.
    boards: list[chess.Board] = [chess.Board(f) for f in fens]

    results: list[PositionEval] = []
    # Persistent engine — open once, použijeme pro všechny pozice, then quit.
    with chess.engine.SimpleEngine.popen_uci(sf_path) as engine:
        for ply, board in enumerate(boards):
            if board.is_game_over():
                # Koncová pozice → engine.analyse() by hodil chybu. Skip, vrať placeholder.
                results.append(PositionEval(ply=ply, game_over=True))
                continue

            info = engine.analyse(board, chess.engine.Limit(time=time_per_move))
            score_cp, mate_in = _score_to_cp_mate(info["score"].white())
            results.append(PositionEval(ply=ply, score_cp=score_cp, mate_in=mate_in))

    return results
