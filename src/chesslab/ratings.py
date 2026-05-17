"""ChessLab Elo ratings — persistent rating per engine.

**Pozor**: ChessLab Elo je **lokální kalibrace** pro default arena time
budget (0.05s/tah), NE CCRL nebo lichess Elo. Stockfish full-time na CCRL
~3700, ale s 0.05s/tah je výrazně slabší (~2000). Anchor: **Stockfish
skill 5 = 1500 ChessLab Elo** (hardcoded, neaktualizuje se).

**Update model**:
  - Po každé partii v aréně: oba enginy dostanou Elo update přes klasický
    FIDE vzorec `new = current + K * (score - expected)`.
  - K-factor: 40 pro `games_played < 30` (rychlá konvergence pro nový
    engine), 20 pro stable engines (méně volatilní).
  - Anchor (is_anchor=1): K=0 → rating fixní navždy.
  - Nový engine (no row v DB): inserne se s rating = soupeřův rating,
    games_played = 0. První parite začne update z tohoto baseline.

**Engine ID konvence**:
  - Skill-aware engine (Stockfish): `<basename>:<skill>`, např. 'stockfish:5'.
    Jiná síla = jiný rating, takže každý skill level je separátní záznam.
  - Custom ChessLab engine bez skill: `<basename>`, např. 'chesslab-minimax'.
    Při bump verze (v2.6 → v2.7) overwrites display_name, rating se naváže
    na předchozí (= zachovaná historie, ale měření nové verze míchá s starou).
    Pokud chceme separátní rating per verze, museli bychom přidat verzi do
    binárky name (`chesslab-minimax-v27`) — KISS, pro teď držíme jeden ID.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from chesslab.db import connect
from chesslab.engines import display_name_for_path

# === Konstanty ===============================================================

# Anchor: Stockfish skill 5 = 1500 ChessLab Elo. Lokálně kalibrováno pro
# 0.05s/tah default arena time. Pokud bychom anchor změnili, posunul by
# se celý žebříček (proporcionálně) — proto se neměnit lehkovážně.
ANCHOR_ENGINE_ID = "stockfish:5"
ANCHOR_DISPLAY_NAME = "Stockfish (skill 5)"
ANCHOR_RATING = 1500.0

# K-factor pro Elo update. FIDE konvence: vyšší K na začátku (rychlá konvergence),
# nižší K pro etablované enginy (stabilní rating, méně volatility).
_K_NEW_ENGINE = 40       # games_played < 30
_K_STABLE_ENGINE = 20    # games_played >= 30
_NEW_TO_STABLE_THRESHOLD = 30

# Default initial rating pro engine, který nemáme v DB a neznáme. Použije se
# jen v edge case (oba enginy v aréně neznámé, žádný anchor). Reálně by se
# tomu nemělo nikdy stát — Stockfish anchor je seedovaný při init_db.
_FALLBACK_RATING = 1200.0


# === Pydantic model ==========================================================


class EngineRating(BaseModel):
    """Engine rating record pro UI (tabulka /engines, dropdown, arena panel)."""

    engine_id: str = Field(..., description="Stabilní ID: 'stockfish:5' / 'chesslab-greedy' / ...")
    display_name: str
    rating: float = Field(..., description="Current ChessLab Elo.")
    games_played: int
    last_updated: int = Field(..., description="Unix ms timestamp posledního updatu.")
    is_anchor: bool = Field(False, description="True = fixní rating, neaktualizuje se.")


# === Engine ID + display name helpers ========================================


def engine_id_from_path(path: str, skill: int, supports_skill: bool) -> str:
    """Z cesty k binárce + skill → unikátní engine_id.

    Skill-aware enginy mají per-skill rating (Stockfish skill 0 a 20 = jiná
    síla, jiný rating). Non-skill enginy ignorují skill (custom engine
    nemá Skill Level option, hraje vždy stejně).

    **Stockfish normalizace**: oficiální binárka má jméno
    `stockfish-windows-x86-64-avx2.exe`, ale anchor ID je hardcoded `stockfish:5`.
    Bez normalizace by každý Stockfish dostal jiné ID podle jména binárky
    (`stockfish-windows-x86-64-avx2:5` ≠ `stockfish-macos-arm64:5` ≠ anchor),
    anchor by nebyl detekován jako anchor, K=40 by ho posunul. Normalizujeme
    všechny Stockfish basenamy začínající 'stockfish' na 'stockfish'.
    """
    stem = Path(path).stem
    # Normalizace: 'stockfish-windows-x86-64-avx2' → 'stockfish'.
    # Symetricky s `display_name_for_path` registry (která taky používá prefix match).
    if stem.startswith("stockfish"):
        stem = "stockfish"
    if supports_skill:
        return f"{stem}:{skill}"
    return stem


def engine_display_name(path: str, skill: int, supports_skill: bool) -> str:
    """Display name pro UI (s případným suffixem (skill N) pro skill-aware).

    Lookup do `display_name_for_path` registry (Stockfish + _KNOWN_ENGINES),
    pak doplníme '(skill N)' jen pro skill-aware enginy. Pokud registry
    neví, fallback na stem binárky.
    """
    base = display_name_for_path(path) or Path(path).stem
    if supports_skill:
        return f"{base} (skill {skill})"
    return base


# === DB ops ==================================================================


def init_anchor() -> None:
    """Seedne Stockfish skill 5 = 1500 anchor (idempotentně přes INSERT OR IGNORE).

    Volá se z `init_db` lifespan při startu aplikace. Pokud row už existuje
    (re-start), neměním — anchor je hardcoded fixní bod a neměl by drift.
    """
    now_ms = int(time.time() * 1000)
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO engine_ratings "
            "(engine_id, display_name, rating, games_played, last_updated, is_anchor) "
            "VALUES (?, ?, ?, 0, ?, 1)",
            (ANCHOR_ENGINE_ID, ANCHOR_DISPLAY_NAME, ANCHOR_RATING, now_ms),
        )


def get_rating(engine_id: str) -> EngineRating | None:
    """Vrátí EngineRating pro engine_id, nebo None pokud neexistuje."""
    with connect() as conn:
        row = conn.execute(
            "SELECT engine_id, display_name, rating, games_played, last_updated, is_anchor "
            "FROM engine_ratings WHERE engine_id = ?",
            (engine_id,),
        ).fetchone()
    if row is None:
        return None
    return EngineRating(
        engine_id=row["engine_id"],
        display_name=row["display_name"],
        rating=row["rating"],
        games_played=row["games_played"],
        last_updated=row["last_updated"],
        is_anchor=bool(row["is_anchor"]),
    )


def list_ratings() -> list[EngineRating]:
    """Všechny rating records, seřazené sestupně podle ratingu."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT engine_id, display_name, rating, games_played, last_updated, is_anchor "
            "FROM engine_ratings ORDER BY rating DESC"
        ).fetchall()
    return [
        EngineRating(
            engine_id=r["engine_id"],
            display_name=r["display_name"],
            rating=r["rating"],
            games_played=r["games_played"],
            last_updated=r["last_updated"],
            is_anchor=bool(r["is_anchor"]),
        )
        for r in rows
    ]


def _upsert_rating(
    engine_id: str,
    display_name: str,
    rating: float,
    games_played: int,
    is_anchor: bool = False,
) -> None:
    """Insert nebo update row v engine_ratings.

    INSERT OR REPLACE — kompletní přepsání řádku včetně is_anchor flagu
    (anchor logiku držíme v `update_ratings_from_arena`, sem se posílá
    finální stav po update).
    """
    now_ms = int(time.time() * 1000)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO engine_ratings "
            "(engine_id, display_name, rating, games_played, last_updated, is_anchor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (engine_id, display_name, rating, games_played, now_ms, 1 if is_anchor else 0),
        )


# === Elo update logika =======================================================


def _k_factor(games_played: int, is_anchor: bool) -> float:
    """K-factor pro Elo update.

    - Anchor: 0 (rating fixní).
    - Nový engine (< 30 partií): 40 (rychlá konvergence).
    - Stable engine: 20.
    """
    if is_anchor:
        return 0.0
    if games_played < _NEW_TO_STABLE_THRESHOLD:
        return float(_K_NEW_ENGINE)
    return float(_K_STABLE_ENGINE)


def elo_update(my_rating: float, opp_rating: float, score: float, k: float) -> float:
    """Standardní FIDE Elo update vzorec.

    Args:
        my_rating: současný rating hráče.
        opp_rating: rating soupeře.
        score: výsledek partie z pohledu hráče: 1 = výhra, 0.5 = remíza, 0 = prohra.
        k: K-factor (větší = rychlejší změna).

    Returns:
        Nový rating po této partii.
    """
    # Expected score (probability výhry) ze standardní logistické formule.
    # Pokud my=opp, expected = 0.5; každých 400 Elo navíc → 10× větší šance vyhrát.
    expected = 1.0 / (1.0 + 10.0 ** ((opp_rating - my_rating) / 400.0))
    return my_rating + k * (score - expected)


def update_ratings_from_arena(
    engine_a_id: str,
    engine_a_name: str,
    engine_b_id: str,
    engine_b_name: str,
    games: list,  # list[GameResult] — duck typed (avoid circular import s arena.py)
) -> tuple[EngineRating, EngineRating]:
    """Aplikuje Elo update na oba enginy z výsledků aréně.

    Pro každou partii spočítá score per engine (1/0.5/0 z výhry / remízy /
    prohry A), pak elo_update. Mezi partiemi se K-factor recomputuje
    (po 30té partii drop z 40 na 20).

    Pokud engine v DB neexistuje, vytvoří se s rating = soupeřův rating.
    Anchor (is_anchor=1) se neaktualizuje (K=0), zůstane fixní.

    Returns:
        (rating_a_after, rating_b_after) — final stav po updatu, pro echo
        v ArenaResult panelu.
    """
    # Načtěme current ratingy. Pokud neznámé, použijeme soupeřův rating jako
    # baseline (nový engine startuje na úrovni protihráče, score nad/pod 0.5
    # ho posune správným směrem).
    rating_a_record = get_rating(engine_a_id)
    rating_b_record = get_rating(engine_b_id)

    # Fallback chain: neznámý engine startuje od soupeře, oba neznámé → fallback.
    if rating_a_record is None and rating_b_record is None:
        rating_a = _FALLBACK_RATING
        rating_b = _FALLBACK_RATING
    elif rating_a_record is None:
        rating_a = rating_b_record.rating
        rating_b = rating_b_record.rating
    elif rating_b_record is None:
        rating_a = rating_a_record.rating
        rating_b = rating_a_record.rating
    else:
        rating_a = rating_a_record.rating
        rating_b = rating_b_record.rating

    games_a = rating_a_record.games_played if rating_a_record else 0
    games_b = rating_b_record.games_played if rating_b_record else 0
    is_anchor_a = rating_a_record.is_anchor if rating_a_record else False
    is_anchor_b = rating_b_record.is_anchor if rating_b_record else False

    # Per-game Elo update. K-factor recomputujeme po každé partii (nový engine
    # může v rámci 30-partií areny překlopit z K=40 na K=20).
    for game in games:
        # Score A: 1 pokud bílý A vyhrál nebo černý A vyhrál; 0.5 remíza; 0 jinak.
        # GameResult.result je '1-0', '0-1', '1/2-1/2' (PGN konvence).
        result = game.result
        white_is_a = game.white_is == "a"
        if result == "1/2-1/2":
            score_a = 0.5
        elif result == "1-0":
            score_a = 1.0 if white_is_a else 0.0
        elif result == "0-1":
            score_a = 0.0 if white_is_a else 1.0
        else:
            # Neukončená partie ('*') — preskočíme (neměla by nastat z run_arena).
            continue
        score_b = 1.0 - score_a

        k_a = _k_factor(games_a, is_anchor_a)
        k_b = _k_factor(games_b, is_anchor_b)

        new_a = elo_update(rating_a, rating_b, score_a, k_a)
        new_b = elo_update(rating_b, rating_a, score_b, k_b)

        rating_a, rating_b = new_a, new_b
        games_a += 1
        games_b += 1

    # Persistujeme finální stav. Anchor flag zachováme (anchor zůstane anchor
    # i po N partiích — K=0 zaručil že rating se nehnul, ale games_played
    # roste pro informaci).
    _upsert_rating(engine_a_id, engine_a_name, rating_a, games_a, is_anchor_a)
    _upsert_rating(engine_b_id, engine_b_name, rating_b, games_b, is_anchor_b)

    # Re-fetch pro vrácení čerstvého EngineRating se správným last_updated.
    return get_rating(engine_a_id), get_rating(engine_b_id)
