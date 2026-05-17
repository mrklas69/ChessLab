"""Parsování PGN — čistá business logika nad python-chess.

Oddělené od FastAPI vrstvy, ať to jde testovat samostatně a později
recyklovat (např. CLI nástroj, batch import z Lichess).
"""

import io

import chess
import chess.pgn
from pydantic import BaseModel, Field

# === Datové modely ===========================================================
#
# Pydantic = validační knihovna (nezávislá na FastAPI). BaseModel automaticky:
#   - validuje typy při vytvoření instance (TypeError místo tichého chování)
#   - serializuje do JSON přes .model_dump() / FastAPI to dělá automaticky
#   - generuje JSON schema pro /docs (OpenAPI)


class PgnMove(BaseModel):
    """Jeden půltah partie ve formě vhodné pro frontend."""

    ply: int = Field(..., description="Pořadí půltahu od 1 (bílý 1. tah = ply 1).")
    san: str = Field(..., description="Standard Algebraic Notation, např. 'Nf3', 'O-O', 'exd5'.")
    uci: str = Field(..., description="UCI notace pro engine, např. 'g1f3', 'e1g1', 'e4d5'.")
    fen_after: str = Field(..., description="FEN pozice PO odehrání tohoto tahu.")


class PgnGame(BaseModel):
    """Partie rozparsovaná z PGN — hlavičky + lineární mainline tahů."""

    headers: dict[str, str] = Field(
        ..., description="PGN hlavičky (Event, White, Black, Date, Result, …)."
    )
    starting_fen: str = Field(
        ..., description="FEN výchozí pozice. Standardní startpos, nebo z FEN tagu (Chr960, středohra)."
    )
    moves: list[PgnMove] = Field(
        ..., description="Mainline půltahy. Variace, komentáře a NAGy jsou pro MVP ignorovány."
    )


# === Parser ==================================================================


def parse_pgn(pgn_text: str) -> PgnGame:
    """Rozparsuje PGN řetězec na strukturovaný PgnGame.

    Použití:
        game = parse_pgn(pgn_text)
        for mv in game.moves:
            print(mv.ply, mv.san, mv.fen_after)

    Raises:
        ValueError: pokud python-chess nedokáže PGN načíst (prázdný vstup,
            poškozená struktura). Endpoint to převede na HTTP 400.
    """
    # python-chess čte PGN jako streamovaný text → musíme ho obalit do
    # in-memory "souboru" přes StringIO (StringIO = file-like API nad str).
    pgn_io = io.StringIO(pgn_text)
    game = chess.pgn.read_game(pgn_io)
    if game is None:
        raise ValueError("PGN se nepodařilo rozparsovat (prázdný nebo poškozený vstup).")

    # game.board() vrátí výchozí pozici partie — buď standardní startpos,
    # nebo pozici z [FEN "..."] tagu (Chess960, vlastní setup).
    board = game.board()
    starting_fen = board.fen()

    moves: list[PgnMove] = []
    # game.mainline() iteruje uzly hlavní linie partie (vynechává sidelines).
    # enumerate(..., start=1) přidá počítadlo začínající od 1 — pro ply.
    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move  # chess.Move objekt (např. Move.from_uci("e2e4"))
        # SAN se musí spočítat PŘED push, protože SAN závisí na pozici
        # (disambiguation typu "Nbd2" funguje jen pokud víme, kde jsou ostatní jezdci).
        san = board.san(move)
        uci = move.uci()
        board.push(move)  # provedeme tah → board je teď v pozici PO tahu
        moves.append(PgnMove(ply=ply, san=san, uci=uci, fen_after=board.fen()))

    # dict(game.headers) — chess.pgn.Headers je dict-like, ale není to čistý dict.
    # Pydantic chce normální dict[str, str], tak ho explicitně zkonvertujeme.
    return PgnGame(
        headers=dict(game.headers),
        starting_fen=starting_fen,
        moves=moves,
    )
