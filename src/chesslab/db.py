"""SQLite vrstva ChessLab.

Jeden lokální soubor `data/chesslab.db` v rootu projektu, override přes env
`CHESSLAB_DB_PATH` (stejný pattern jako STOCKFISH_PATH).

Schema je idempotentní (CREATE TABLE IF NOT EXISTS) — `init_db()` se volá při
startu aplikace, žádné migrace zatím neřešíme (KISS, schema je single source
of truth tady v souboru). Když ho rozšíříme, přidáme ALTER nebo migration
nástroj — to si necháme na později.

Modul drží jen connection helper + init. Konkrétní queries patří do
`games.py` (modulový separation of concerns).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# === Cesta k DB =============================================================
# Default: <project_root>/data/chesslab.db. Root je o 2 úrovně výš než tento
# soubor (src/chesslab/db.py → src/chesslab → src → ChessLab).
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chesslab.db"


def db_path() -> Path:
    """Vrátí aktuální cestu k DB (env override má přednost).

    Funkce, ne konstanta, protože env může nastat až runtime (např. pytest fixture).
    """
    env = os.environ.get("CHESSLAB_DB_PATH")
    return Path(env) if env else _DEFAULT_DB_PATH


# === Schema =================================================================
# Jeden velký SQL skript, executescript() ho rozparsuje na statements.
# IF NOT EXISTS všude → idempotentní, init_db() se může volat opakovaně.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              TEXT PRIMARY KEY,           -- Lichess game ID (nebo jiný unikátní ID per source)
    source          TEXT NOT NULL,              -- 'lichess', později 'chesscom', 'manual'
    username        TEXT NOT NULL,              -- moje username (pro filtr 'moje partie')
    color           TEXT NOT NULL,              -- 'white' / 'black'
    opponent        TEXT,                       -- jméno protihráče (NULL pro AI / anonymous)
    opponent_rating INTEGER,
    my_rating       INTEGER,
    result          TEXT,                       -- 'win' / 'loss' / 'draw'
    termination     TEXT,                       -- 'mate' / 'resign' / 'time' / 'draw' / ...
    speed           TEXT,                       -- 'bullet' / 'blitz' / 'rapid' / 'classical' / 'correspondence'
    variant         TEXT,                       -- 'standard' / 'chess960' / 'atomic' / ...
    rated           INTEGER,                    -- 0 / 1 (SQLite nemá BOOL, INT je kanonický)
    opening_eco     TEXT,                       -- ECO kód, např. 'B12'
    opening_name    TEXT,                       -- 'Caro-Kann Defense: Advance'
    created_at      INTEGER,                    -- unix ms (Lichess timestamp formát)
    plies           INTEGER,                    -- počet půltahů
    pgn             TEXT NOT NULL,              -- plný PGN se Seven Tag Roster
    imported_at     INTEGER NOT NULL            -- unix ms (kdy jsme to stáhli)
);

-- Indexy pro typické filtry. SQLite je rychlý i bez nich na pár tisíc partií,
-- ale když user importuje 5 000 partií, query bez indexu sekvenčně skenuje.
CREATE INDEX IF NOT EXISTS idx_games_username   ON games (username);
CREATE INDEX IF NOT EXISTS idx_games_created_at ON games (created_at);
CREATE INDEX IF NOT EXISTS idx_games_source     ON games (source);

-- Per-ply eval z Stockfish analýzy + klasifikace tahu (best/good/inaccuracy/
-- mistake/blunder). Plní se lazy on-demand (POST /api/games/{id}/classify),
-- není povinná — partie bez klasifikace se zobrazí normálně, jen bez tagů.
-- Composite PK (game_id, ply) zajistí, že existuje max 1 záznam per pozice;
-- re-classify s jiným time_per_move přepíše (INSERT OR REPLACE v insert SQL).
CREATE TABLE IF NOT EXISTS move_evals (
    game_id          TEXT NOT NULL,           -- FK → games.id
    ply              INTEGER NOT NULL,         -- 0 = startovní pozice, 1 = po 1. tahu, …
    eval_cp          INTEGER,                  -- centipawn eval (perspektiva bílého), NULL pro mate/game_over
    mate_in          INTEGER,                  -- mate-in-N (kladné = bílý matuje), NULL pokud není mate
    classification   TEXT,                     -- 'best'|'good'|'inaccuracy'|'mistake'|'blunder', NULL pro ply=0
    analyzed_at      INTEGER NOT NULL,         -- unix ms (kdy se to spočítalo)
    time_per_move    REAL NOT NULL,            -- budget použitý pro analýzu (pro re-analýzu rozlišení)
    PRIMARY KEY (game_id, ply),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);

-- ChessLab Elo per engine. ID je 'stockfish:5' pro skill-aware (Stockfish skill
-- 0-20 = 21 separátních ratingů, jiná síla = jiný rating), 'chesslab-greedy'
-- pro custom enginy bez skill. Anchor (is_anchor=1) má fixní rating, neaktualizuje
-- se ani po N partiích — slouží jako referenční bod pro absolutní škálu.
-- Default anchor: Stockfish skill 5 = 1500 ChessLab Elo (NE CCRL, lokální
-- kalibrace pro 0.05s/tah default arena time budget).
CREATE TABLE IF NOT EXISTS engine_ratings (
    engine_id        TEXT PRIMARY KEY,         -- 'stockfish:5', 'chesslab-minimax', …
    display_name     TEXT NOT NULL,            -- 'Stockfish (skill 5)', 'ChessLab Minimax v2.7'
    rating           REAL NOT NULL,            -- current Elo
    games_played     INTEGER NOT NULL DEFAULT 0,
    last_updated     INTEGER NOT NULL,         -- unix ms
    is_anchor        INTEGER NOT NULL DEFAULT 0  -- 1 = fixní rating, neaktualizuje se
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Context manager pro SQLite connection.

    - Auto-commit při úspěšném ukončení bloku, rollback při výjimce.
    - `row_factory = sqlite3.Row` → výsledky se chovají jako dict (row['col']).
    - Adresář DB se vytvoří lazy při prvním connectu.

    Použití:
        with connect() as conn:
            conn.execute("SELECT ... ")
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False — FastAPI handler může běžet v jiném threadu než
    # init. Pro náš use case (single-process, krátké queries) bezpečné.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Vytvoří tabulky/indexy, pokud neexistují. Volá se při startu aplikace."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
