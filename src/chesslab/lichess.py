"""Lichess API klient — stahování partií uživatele přes oficiální export endpoint.

Public games only (bez OAuth), což stačí pro 99 % use case. Auth (Bearer token)
přidáme později, pokud bude potřeba private/correspondence import + vyšší rate
limit. Auth verze by jen přidala `headers={'Authorization': f'Bearer {token}'}`.

API ref: https://lichess.org/api#tag/Games/operation/apiGamesUser

Endpoint vrací NDJSON stream (jeden JSON objekt per řádek). Tenhle modul ho
parsuje a mapuje na `LichessGame` (= schema sloupců v tabulce `games`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel

# === Konstanty ===============================================================
LICHESS_API = "https://lichess.org"
GAMES_USER_PATH = "/api/games/user/{username}"

DEFAULT_MAX_GAMES = 100
# Hard cap proti náhodnému stažení 10 000 partií (bez auth pomalé +
# rate limit risk). Pokud user chce víc, můžeme zvednout — zatím KISS.
HARD_MAX_GAMES = 500

# Connect / read timeout. read=60s — Lichess stream může pomalu drippovat,
# 60s mezi řádky je hluboce za hranicí normálního provozu, ale ne tak málo,
# aby pomalé spojení padalo.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


class LichessGame(BaseModel):
    """Normalizovaný záznam jedné partie z Lichess, hotový pro insert do DB.

    Pole odpovídají sloupcům tabulky `games` (viz `db.py`). Pydantic používáme
    jen pro typový kontrakt — Lichess garantuje formát, takže žádné runtime
    validace nepotřebujeme.
    """

    id: str
    source: str = "lichess"
    username: str
    color: str                    # 'white' / 'black' (moje barva)
    opponent: str | None          # None pokud anonymous (vzácné)
    opponent_rating: int | None
    my_rating: int | None
    result: str | None            # 'win' / 'loss' / 'draw' (z mého pohledu)
    termination: str | None       # Lichess 'status' field raw (mate/resign/...)
    speed: str | None             # bullet/blitz/rapid/classical/correspondence/ultraBullet
    variant: str | None           # standard/chess960/atomic/...
    rated: int                    # 0/1 (SQLite nemá BOOL)
    opening_eco: str | None
    opening_name: str | None
    created_at: int | None        # unix ms
    plies: int | None
    pgn: str


def fetch_user_games(
    username: str,
    max_games: int = DEFAULT_MAX_GAMES,
) -> Iterator[LichessGame]:
    """Streamuje partie uživatele z Lichess API.

    Generator — yielduje hru za hrou, nečeká na celou response. Volající si
    může dělat commit-per-game nebo bufferovat do batch insertu.

    Raises:
        ValueError: prázdný username nebo `max_games` mimo rozsah.
        httpx.HTTPStatusError: 404 (user neexistuje), 429 (rate limit), 5xx.
        httpx.TimeoutException: server nereaguje.
    """
    if not username.strip():
        raise ValueError("username nesmí být prázdný")
    if not (1 <= max_games <= HARD_MAX_GAMES):
        raise ValueError(f"max_games musí být 1–{HARD_MAX_GAMES}, dostali: {max_games}")

    url = LICHESS_API + GAMES_USER_PATH.format(username=username.strip())
    # Query parametry:
    #   pgnInJson=true  → pgn jako pole v JSONu (jinak by endpoint vracel raw PGN stream)
    #   opening=true    → opening detect (eco + name)
    #   clocks/evals=false → zmenšuje response (nepotřebujeme)
    # Defaults necháváme: moves=true, tags=true, sort=dateDesc.
    params = {
        "max": max_games,
        "pgnInJson": "true",
        "opening": "true",
        "clocks": "false",
        "evals": "false",
    }
    headers = {"Accept": "application/x-ndjson"}

    with httpx.Client(timeout=_TIMEOUT) as client:
        # stream() nečte tělo do paměti — iter_lines() chodí line-by-line,
        # vhodné pro NDJSON i pro stovky partií.
        with client.stream("GET", url, params=params, headers=headers) as resp:
            # U chybové response (404, 429, ...) musíme nejdřív načíst tělo —
            # raise_for_status() vytváří HTTPStatusError se sídlem na `response`,
            # ale `response.text` u stream() vyhodí ResponseNotRead(), pokud
            # tělo nebylo načteno. Načteme ho explicitně PŘED raise, aby měl
            # exception handler v app.py přístup k popisu chyby.
            if resp.status_code >= 400:
                resp.read()
            resp.raise_for_status()
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue  # poslední řádek NDJSON bývá prázdný
                game_json = json.loads(line)
                yield _map_to_lichess_game(game_json, username)


def _map_to_lichess_game(g: dict[str, Any], username: str) -> LichessGame:
    """Zmapuje raw Lichess game JSON na `LichessGame` (= DB row schema).

    Lichess `players.{white,black}.user.id` je vždy lowercase — porovnáváme
    case-insensitive (`uname.lower()`).
    """
    players = g.get("players", {})
    white = players.get("white", {})
    black = players.get("black", {})
    uname_lower = username.strip().lower()

    # Identifikace barvy — který hráč jsme my? `.user` může chybět (anonymous
    # nebo AI bot — Stockfish na Lichessu jako `aiLevel`).
    white_id = ((white.get("user") or {}).get("id") or "").lower()
    black_id = ((black.get("user") or {}).get("id") or "").lower()
    if white_id == uname_lower:
        color = "white"
        me, opp = white, black
    elif black_id == uname_lower:
        color = "black"
        me, opp = black, white
    else:
        # /api/games/user/{username} vrací JEN partie tohoto uživatele,
        # takže by tohle nemělo nastat. Pokud ano, někde je chyba.
        raise ValueError(
            f"Partie {g.get('id')!r}: username {username!r} není ani bílý ani černý "
            f"(white={white_id!r}, black={black_id!r})"
        )

    # Výsledek z mého pohledu. Lichess 'winner' = 'white' / 'black', chybí pro draw.
    winner = g.get("winner")
    if winner is None:
        result = "draw"
    elif winner == color:
        result = "win"
    else:
        result = "loss"

    opening = g.get("opening") or {}
    moves_str = g.get("moves", "") or ""
    # Počet půltahů = počet SAN tokenů v 'moves' (oddělené mezerami).
    plies = len(moves_str.split()) if moves_str else 0

    opp_user = opp.get("user") or {}
    return LichessGame(
        id=g["id"],
        username=username,
        color=color,
        opponent=opp_user.get("name"),
        opponent_rating=opp.get("rating"),
        my_rating=me.get("rating"),
        result=result,
        termination=g.get("status"),
        speed=g.get("speed"),
        variant=g.get("variant"),
        rated=1 if g.get("rated") else 0,
        opening_eco=opening.get("eco"),
        opening_name=opening.get("name"),
        created_at=g.get("createdAt"),
        plies=plies,
        pgn=g.get("pgn", "") or "",
    )
