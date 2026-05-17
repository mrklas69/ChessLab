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

from chesslab.chesscom import ChessComGame
from chesslab.chesscom import fetch_user_games as fetch_chesscom_user_games
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
    # Inkrementální import: timestamp od kterého jsme fetchovali (max v DB + 1).
    # None = full import (force re-sync, nebo žádná partie usera v DB).
    since: int | None = None
    incremental: bool = False    # True = since byl použit (inkrementál), False = full


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


def insert_games(games: Iterable[LichessGame | ChessComGame]) -> tuple[int, int]:
    """Insertne partie do DB. Vrátí ``(inserted, skipped_existing)``.

    Přijímá oba typy importovaných her (Lichess + chess.com) — schema sloupců
    je stejné, jen `source` field rozlišuje původ. model_dump() v Pydantic dá
    pro oba typy identické keys → SQL bindings fungují out-of-the-box.

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


def latest_lichess_created_at(username: str) -> int | None:
    """Vrátí `MAX(created_at)` pro Lichess partie tohoto usera, nebo None.

    Pro inkrementální import — volající přidá +1 ms a předá jako `since` do
    Lichess API, který vrátí jen novější partie (== nestažené).

    Filtruje per `source='lichess'`, protože ID konvence se může mezi zdroji
    lišit (Lichess 8-char alfanumeric, chess.com UUID) a nechceme aby
    chess.com timestamp ovlivnil Lichess `since`.
    """
    return _latest_created_at_for_source("lichess", username)


def latest_chesscom_created_at(username: str) -> int | None:
    """Analog `latest_lichess_created_at`, filtruje per `source='chesscom'`.

    Pro chess.com je tu drobná sémantická lež: ukládáme `end_time*1000`, takže
    "MAX(created_at)" reálně znamená "konec poslední partie". Pro inkrementál
    je to fakticky správně (vše s `end_time > last_end` je nové), pro UI nezáleží.
    """
    return _latest_created_at_for_source("chesscom", username)


def _latest_created_at_for_source(source: str, username: str) -> int | None:
    """Sdílená DRY logika pro `latest_*_created_at` per zdroj."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS m FROM games "
            "WHERE source = ? AND username = ? AND created_at IS NOT NULL",
            (source, username),
        ).fetchone()
    return row["m"] if row and row["m"] is not None else None


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


def _run_import(
    username: str,
    max_games: int,
    *,
    force_full: bool,
    fetch_fn,
    latest_fn,
) -> ImportResult:
    """Sdílená import orchestrace pro libovolný zdroj (Lichess, chess.com).

    Zodpovědnosti:
      - Výpočet `since` (= MAX(created_at) + 1 ms pro daný zdroj, pokud
        není `force_full`).
      - Per-game error handling — `ValueError` v mappingu spadne do
        `result.errors`, zbytek partií se zpracuje normálně.
      - Hard failures (HTTPStatusError, TimeoutException) propaguje volajícímu
        — `app.py` je převede na HTTP status kódy.

    Args:
        fetch_fn: callable `(username, max_games, since=) -> Iterator[Game]`.
            Lichess i chess.com modul mají stejnou signaturu `fetch_user_games`.
        latest_fn: callable `(username) -> int | None`, vrací MAX(created_at)
            pro daný zdroj+username (nebo None pokud user v DB prázdný).
    """
    # Inkrementální since — default ON, lze vypnout `force_full=True`.
    since: int | None = None
    if not force_full:
        last_ms = latest_fn(username)
        if last_ms is not None:
            # +1 ms aby hraniční partie (created_at == last_ms) nepřišla znova.
            # Lichess `since` je documented jako "after" → exclusive, ale +1
            # bezpečné v každém případě. Pro chess.com filtr je vyhodnocen
            # client-side (`created_at > since`), takže +1 zaručuje strictly
            # newer.
            since = last_ms + 1

    result = ImportResult(
        requested=max_games,
        since=since,
        incremental=(since is not None),
    )
    games_to_insert: list[LichessGame | ChessComGame] = []

    # Generator yielduje hru za hrou. Pokud jedna selže při mapování (ValueError
    # v `_map_to_*_game`), zalogujeme a pokračujeme — zbytek importu to nesmí zhodit.
    gen = fetch_fn(username, max_games, since=since)
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


def import_lichess_user(
    username: str,
    max_games: int,
    force_full: bool = False,
) -> ImportResult:
    """Fetch partií z Lichess + insert do DB. Per-game errory neukončí celek.

    Inkrementální režim: `MAX(created_at) + 1 ms` pro Lichess partie tohoto usera
    se předá jako `since` do API. Lichess vrátí jen partie s `createdAt > since`.
    Pro prvního usera (DB prázdná) se `since` neaplikuje → full fetch.
    INSERT OR IGNORE zůstává safety net pro race conditions.

    Args:
        username: Lichess username (case-insensitive).
        max_games: horní limit počtu partií fetchovaných z API.
        force_full: True → ignoruj `since`, fetchni celou historii (re-sync).
    """
    return _run_import(
        username, max_games,
        force_full=force_full,
        fetch_fn=fetch_user_games,
        latest_fn=latest_lichess_created_at,
    )


def import_chesscom_user(
    username: str,
    max_games: int,
    force_full: bool = False,
) -> ImportResult:
    """Fetch partií z chess.com + insert do DB. Per-game errory neukončí celek.

    Inkrementální režim: na rozdíl od Lichess (server-side `since` query param)
    chess.com nemá filter — fetch projde archivy od nejnovějšího a zastaví,
    jakmile narazí na partii s `created_at <= since`. Méně efektivní (musíme
    stáhnout aspoň jeden nový měsíc), ale stejný kontrakt navenek.

    Args:
        username: chess.com username (case-insensitive).
        max_games: horní limit počtu partií fetchovaných z API.
        force_full: True → ignoruj `since`, fetchni celou historii (re-sync).
    """
    return _run_import(
        username, max_games,
        force_full=force_full,
        fetch_fn=fetch_chesscom_user_games,
        latest_fn=latest_chesscom_created_at,
    )
