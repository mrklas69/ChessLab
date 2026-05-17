"""Game DB operations.

Read/write nad tabulkou `games`. Insert je idempotentní (INSERT OR IGNORE),
takže opětovný import stejných partií nevytvoří duplikáty ani nepřepíše
existující řádky (důležité pro budoucí lokální anotace — REPLACE by je
ztratilo).

Modul tady drží i Pydantic modely (GameSummary, GameDetail, ImportResult),
protože jsou těsně svázané se schématem a používá je jak `app.py` (FastAPI
response_model) tak interní volání.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from chesslab.db import connect
from chesslab.lichess import LichessGame, fetch_user_games

# === Pydantic modely =========================================================


class GameSummary(BaseModel):
    """Lehký záznam pro tabulku /games (bez plného PGN — ten je velký)."""

    id: str
    source: str
    username: str
    color: str
    opponent: str | None
    opponent_rating: int | None
    my_rating: int | None
    result: str | None
    termination: str | None
    speed: str | None
    variant: str | None
    rated: int
    opening_eco: str | None
    opening_name: str | None
    created_at: int | None
    plies: int | None


class GameDetail(GameSummary):
    """GameSummary + plný PGN + imported_at — pro detail view / PGN download."""

    pgn: str
    imported_at: int


class ImportResult(BaseModel):
    """Výstup importu — souhrn akce, frontend si ho zobrazí jako toast."""

    requested: int = 0           # kolik max user požadoval
    fetched: int = 0             # kolik partií Lichess vrátil
    inserted: int = 0            # nově přidaných do DB
    skipped_existing: int = 0    # už v DB byly (INSERT OR IGNORE)
    errors: int = 0              # mapping/parsing chyby (per game, neukončí import)
    error_messages: list[str] = []  # první N chybových hlášek (pro debug v UI)


class MoveEval(BaseModel):
    """Eval + klasifikace jedné pozice partie (per ply).

    `classification` je None pro ply=0 (před prvním tahem žádný "tah" neexistuje
    → nelze klasifikovat). Pro ply ≥ 1 je vždy vyplněna (computed z drop ve
    win-probability mezi ply-1 → ply, z pohledu hráče, který táhl).
    """

    ply: int
    eval_cp: int | None = None
    mate_in: int | None = None
    classification: str | None = None  # 'best' | 'good' | 'inaccuracy' | 'mistake' | 'blunder'


# === Insert ==================================================================
# Pojmenované parametry (:foo) přímo mapují na klíče z model_dump() — zero glue.
_INSERT_SQL = """
INSERT OR IGNORE INTO games (
    id, source, username, color, opponent, opponent_rating, my_rating,
    result, termination, speed, variant, rated,
    opening_eco, opening_name, created_at, plies, pgn, imported_at
) VALUES (
    :id, :source, :username, :color, :opponent, :opponent_rating, :my_rating,
    :result, :termination, :speed, :variant, :rated,
    :opening_eco, :opening_name, :created_at, :plies, :pgn, :imported_at
)
"""


def insert_games(games: Iterable[LichessGame]) -> tuple[int, int]:
    """Insertne partie do DB. Vrátí ``(inserted, skipped_existing)``.

    INSERT OR IGNORE: pokud `id` už v DB existuje, statement tichý no-op
    (`cursor.rowcount == 0` ho rozliší od reálného insertu).

    Commit proběhne až při výstupu z `connect()` context manageru — celý batch
    je jedna transakce, takže buď se uloží všechno, nebo nic (přerušení uprostřed
    je atomické).
    """
    inserted = 0
    skipped = 0
    now_ms = int(time.time() * 1000)
    with connect() as conn:
        for g in games:
            row = g.model_dump()
            row["imported_at"] = now_ms
            cur = conn.execute(_INSERT_SQL, row)
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


# === Query ===================================================================
# Sdílený seznam sloupců pro SELECT — zabráníme typu chyb 'zapomněl jsem
# přidat sloupec sem i tam'.
_SUMMARY_COLS = (
    "id, source, username, color, opponent, opponent_rating, my_rating, "
    "result, termination, speed, variant, rated, opening_eco, opening_name, "
    "created_at, plies"
)

_ColorFilter = Literal["all", "white", "black"]
_ResultFilter = Literal["all", "win", "loss", "draw"]


def list_games(
    *,
    username: str | None = None,
    color: _ColorFilter = "all",
    result: _ResultFilter = "all",
    speed: str | None = None,
    limit: int = 500,
) -> list[GameSummary]:
    """Vrátí summary partií podle filtrů, seřazené od nejnovější.

    Filtry se skládají do WHERE klauzule (chybějící filtr = nic neomezuje).
    LIMIT 500 jako safety cap proti zahlcení frontendu (frontend si dál může
    pageovat / filtrovat client-side).
    """
    where: list[str] = []
    params: dict = {}
    if username:
        where.append("username = :username")
        params["username"] = username
    if color != "all":
        where.append("color = :color")
        params["color"] = color
    if result != "all":
        where.append("result = :result")
        params["result"] = result
    if speed:
        where.append("speed = :speed")
        params["speed"] = speed

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params["limit"] = limit
    sql = (
        f"SELECT {_SUMMARY_COLS} FROM games "
        f"{where_sql} ORDER BY created_at DESC LIMIT :limit"
    )
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    # sqlite3.Row → dict → Pydantic. dict() přes konstruktor je nejjednodušší.
    return [GameSummary(**dict(r)) for r in rows]


def get_game_pgn(game_id: str) -> str | None:
    """Vrátí PGN partie podle ID. ``None`` pokud nenajde."""
    with connect() as conn:
        row = conn.execute(
            "SELECT pgn FROM games WHERE id = ?", (game_id,)
        ).fetchone()
    return row["pgn"] if row else None


def count_games() -> int:
    """Celkový počet partií v DB (pro empty-state UI na /games)."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM games").fetchone()
    return row["c"] if row else 0


# === High-level import orchestrace ===========================================
# Tady se spojí fetch (lichess.py) + insert (insert_games výše) + per-game
# error handling. `app.py` jen forwarduje výjimky → HTTP statusy a nemusí
# rozumět detailům.


# Kolik chybových hlášek max uložit do ImportResult.error_messages (jinak by
# si user mohl naplnit paměť opakovaným voláním s broken username).
_MAX_ERROR_MESSAGES = 10


# === Move evals (klasifikace tahů) ===========================================
# Plní se lazy on-demand z `classifier.classify_game()`. Uložíme všechny plies
# najednou (transakce přes connect() context manager), čteme jako list seřazený
# podle ply.


# INSERT OR REPLACE — při re-classify (jiný time_per_move) přepíšeme existující
# záznam. PK (game_id, ply) garantuje, že je max 1 řádek per (game, ply).
_INSERT_MOVE_EVAL_SQL = """
INSERT OR REPLACE INTO move_evals (
    game_id, ply, eval_cp, mate_in, classification, analyzed_at, time_per_move
) VALUES (
    :game_id, :ply, :eval_cp, :mate_in, :classification, :analyzed_at, :time_per_move
)
"""


def insert_move_evals(game_id: str, evals: list[MoveEval], time_per_move: float) -> None:
    """Uloží evals pro celou partii do `move_evals`. Idempotentní (REPLACE).

    Celý batch je jedna transakce přes `connect()` — buď se uloží všechno,
    nebo nic. Pro 80-plies partii to je 81 řádků, naprosto bezbolestné.
    """
    now_ms = int(time.time() * 1000)
    with connect() as conn:
        for ev in evals:
            row = ev.model_dump()
            row["game_id"] = game_id
            row["analyzed_at"] = now_ms
            row["time_per_move"] = time_per_move
            conn.execute(_INSERT_MOVE_EVAL_SQL, row)


def get_move_evals(game_id: str) -> list[MoveEval]:
    """Vrátí seznam evals pro partii, seřazený podle ply. Prázdný list pokud nic."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ply, eval_cp, mate_in, classification "
            "FROM move_evals WHERE game_id = ? ORDER BY ply",
            (game_id,),
        ).fetchall()
    return [MoveEval(**dict(r)) for r in rows]


def has_classification(game_id: str) -> bool:
    """True pokud existuje aspoň jeden záznam v `move_evals` pro tento game_id.

    Lehký check (LIMIT 1) — používá se pro lookup-or-compute pattern v
    `classifier.get_or_classify_game()`.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM move_evals WHERE game_id = ? LIMIT 1", (game_id,)
        ).fetchone()
    return row is not None


def import_lichess_user(username: str, max_games: int) -> ImportResult:
    """Fetch partií z Lichess + insert do DB. Per-game errory neukončí celek.

    Network/auth errory (HTTPStatusError, TimeoutException) propaguje volajícímu
    — to jsou hard failures, ne 'jedna partie se nepovedla'.
    """
    result = ImportResult(requested=max_games)
    games_to_insert: list[LichessGame] = []

    # Generator yielduje hru za hrou. Pokud jedna selže při mapování (ValueError
    # v _map_to_lichess_game), zalogujeme a pokračujeme — zbytek importu to
    # nesmí zhodit.
    gen = fetch_user_games(username, max_games)
    while True:
        try:
            g = next(gen)
        except StopIteration:
            break
        except ValueError as exc:
            result.errors += 1
            if len(result.error_messages) < _MAX_ERROR_MESSAGES:
                result.error_messages.append(str(exc))
            continue
        games_to_insert.append(g)

    result.fetched = len(games_to_insert)
    if games_to_insert:
        ins, skip = insert_games(games_to_insert)
        result.inserted = ins
        result.skipped_existing = skip
    return result
