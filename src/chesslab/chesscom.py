"""chess.com Published-Data API klient — stahování partií uživatele z měsíčních archivů.

Public games only (žádný auth potřeba — celý Published-Data tree je veřejný,
narozdíl od interních endpointů). API model je dvoukrokový: nejdřív seznam URL
měsíčních archivů, pak per-měsíc JSON s partiemi. Inkrementál řešíme
client-side filtrem `created_at > since` — API nemá `since` query param jako
Lichess.

API ref: https://www.chess.com/news/view/published-data-api
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from typing import Any

import chess.pgn
import httpx
from pydantic import BaseModel

# === Konstanty ===============================================================
CHESSCOM_API = "https://api.chess.com"
ARCHIVES_PATH = "/pub/player/{username}/games/archives"

# Stejné defaults jako Lichess pro UI konzistenci — uživatel ať nepřemýšlí, že
# jeden zdroj má jiný limit než druhý. Pokud by se v budoucnu choval API
# výrazně jinak (např. chess.com timeout u 500 partií), můžeme rozdělit.
DEFAULT_MAX_GAMES = 100
HARD_MAX_GAMES = 500

# chess.com support tým doporučuje rozumný User-Agent (e-mail kontakt
# v hlavičce — pokud by někdy zatěžovali jejich API, vědí, koho kontaktovat).
# Bez UA chess.com občas vrací 403.
USER_AGENT = "ChessLab/0.1 (contact: mrklas69@gmail.com)"

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# === Mapping helpery =========================================================
# chess.com per-color `result` field má širokou doménu:
#   - výhra: "win" (jediná hodnota)
#   - prohra: "checkmated", "resigned", "timeout", "abandoned", "kingofthehill",
#             "threecheck", "bughousepartnerlose", "lose"
#   - remíza: "agreed", "stalemate", "repetition", "insufficient", "50move",
#             "timevsinsufficient"
# Místo whitelistu prohře (která se může rozšiřovat o nové varianty) explicit
# whitelistujeme remízy a default = loss (vše mimo "win" a draw set).
_DRAW_RESULTS = frozenset({
    "agreed", "stalemate", "repetition", "insufficient",
    "50move", "timevsinsufficient",
})

# Regex extractory pro ECO + opening URL z PGN headers (chess.com je tam vždy
# posílá pro klasické partie). Mohli bychom použít `chess.pgn.read_game().headers`,
# ale to už dělá full PGN parse — regex je 10× rychlejší a stačí.
_ECO_RE = re.compile(r'\[ECO\s+"([^"]+)"\]')
_ECO_URL_RE = re.compile(r'\[ECOUrl\s+"([^"]+)"\]')

# Opening slug má formát 'Nimzowitsch-Larsen-Attack-Modern-Variation-2.Bb2-Nc6-3.e3'
# nebo 'English-Opening-Agincourt-Defense...3.e3-Nf6' (s '...' = response notation
# u černého). Regex useknu vše od prvního výskytu '-N.' NEBO '...N.' (kde N je
# move number) — zbude jen sémantická hlavička teorie.
_MOVE_SUFFIX_RE = re.compile(r'(?:-|\.\.\.)\d+\..*$')


def _parse_opening(pgn: str) -> tuple[str | None, str | None]:
    """Vytáhne (ECO kód, opening name) z PGN headers. Oba mohou být None."""
    eco_match = _ECO_RE.search(pgn)
    eco = eco_match.group(1) if eco_match else None

    name: str | None = None
    url_match = _ECO_URL_RE.search(pgn)
    if url_match:
        # Slug = poslední část URL po '/'.
        slug = url_match.group(1).rsplit("/", 1)[-1]
        # Useknout move suffix '-2.Bb2-...' (pokud existuje).
        slug = _MOVE_SUFFIX_RE.sub("", slug)
        # Slug → human-readable: pomlčky → mezery.
        name = slug.replace("-", " ") if slug else None
    return eco, name


def _count_plies(pgn: str) -> int:
    """Spočítá počet půltahů z PGN přes python-chess. 0 při parse erroru / prázdné PGN."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return 0
    # mainline_moves() je generator — `sum(1 for _ in ...)` ho dotáhne do konce.
    return sum(1 for _ in game.mainline_moves())


def _to_my_result(my_chesscom_result: str | None) -> str | None:
    """Převede chess.com per-color result na 'win'/'loss'/'draw' z mého pohledu."""
    if not my_chesscom_result:
        return None
    if my_chesscom_result == "win":
        return "win"
    if my_chesscom_result in _DRAW_RESULTS:
        return "draw"
    return "loss"


# === Pydantic model ==========================================================


class ChessComGame(BaseModel):
    """Normalizovaný záznam jedné partie z chess.com, hotový pro insert do DB.

    Schema 1:1 jako `LichessGame` — sloupce tabulky `games` se nemění podle
    zdroje, jen `source` field rozlišuje původ. Insert helper `insert_games`
    v `games.py` přijme union obou typů (oba dají stejné keys přes model_dump()).
    """

    id: str
    source: str = "chesscom"
    username: str
    color: str                    # 'white' / 'black' (moje barva)
    opponent: str | None
    opponent_rating: int | None
    my_rating: int | None
    result: str | None            # 'win' / 'loss' / 'draw' (z mého pohledu)
    termination: str | None       # chess.com per-color result code (checkmated/resigned/...)
    speed: str | None             # chess.com time_class: bullet/blitz/rapid/daily
    variant: str | None           # chess.com rules: chess/chess960/atomic/...
    rated: int                    # 0/1
    opening_eco: str | None       # ECO kód z PGN [ECO "A01"]
    opening_name: str | None      # parsed z [ECOUrl "..."] slug
    created_at: int | None        # unix ms (= end_time*1000, viz mapping pozn.)
    plies: int | None
    pgn: str


# === Fetch logic ==============================================================


def fetch_user_games(
    username: str,
    max_games: int = DEFAULT_MAX_GAMES,
    since: int | None = None,
) -> Iterator[ChessComGame]:
    """Streamuje partie uživatele z chess.com (od nejnovější).

    Dvoukrokový API flow:
        1. `GET /pub/player/{username}/games/archives` → list URL měsíčních archivů
           (chronologicky asc, např. 2018/07 → 2025/11).
        2. Pro každý měsíc od nejnovějšího: `GET <archive_url>` → JSON `{games: [...]}`.

    Yielduje max `max_games` partií, nebo zastaví, jakmile narazí na partii s
    `created_at <= since` (předpoklad: archivy i partie v měsíci jsou seřazené
    chronologicky asc → po obrácení procházíme od nejnovější a starší zastaví
    early exit z celé smyčky).

    Args:
        since: Unix ms timestamp. Filter `created_at > since`. None = full historie.

    Raises:
        ValueError: prázdný username nebo `max_games` mimo rozsah.
        httpx.HTTPStatusError: 404 (user neexistuje), 429 (rate limit), 5xx.
        httpx.TimeoutException: server nereaguje.
    """
    if not username.strip():
        raise ValueError("username nesmí být prázdný")
    if not (1 <= max_games <= HARD_MAX_GAMES):
        raise ValueError(f"max_games musí být 1–{HARD_MAX_GAMES}, dostali: {max_games}")

    uname = username.strip()
    # chess.com URL je case-insensitive, ale `/games/archives` endpoint vrací
    # HTTP 301 redirect z capitalized → lowercase formy (např. "Bete1geuse" →
    # "bete1geuse"). httpx default redirecty nefolowuje, takže lowercaseujeme
    # už v URL — funguje konzistentně bez follow_redirects=True.
    uname_url = uname.lower()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    with httpx.Client(timeout=_TIMEOUT, headers=headers) as client:
        # Krok 1: archivy. Returns {archives: [url, url, ...]} chronologically asc.
        archives_url = CHESSCOM_API + ARCHIVES_PATH.format(username=uname_url)
        resp = client.get(archives_url)
        resp.raise_for_status()
        archives: list[str] = resp.json().get("archives", [])

        # Krok 2: měsíce od nejnovějšího → zachová sort 'newest first' pro user
        # expectation + ve spojení se `since` umožní early exit, jakmile narazíme
        # na starou partii.
        yielded = 0
        for archive_url in reversed(archives):
            if yielded >= max_games:
                return

            month_resp = client.get(archive_url)
            month_resp.raise_for_status()
            month_games: list[dict[str, Any]] = month_resp.json().get("games", [])

            # Per-měsíc: chess.com vrací asc → projedeme reversed (newest first).
            for g in reversed(month_games):
                if yielded >= max_games:
                    return
                end_time_s = g.get("end_time") or 0
                created_at_ms = end_time_s * 1000  # end_time je v sekundách
                if since is not None and created_at_ms <= since:
                    # Stará partie — vše dál (uvnitř měsíce i v dalších měsících)
                    # je ještě starší → můžeme rovnou skončit celý fetch.
                    return
                yield _map_to_chesscom_game(g, uname)
                yielded += 1


def _map_to_chesscom_game(g: dict[str, Any], username: str) -> ChessComGame:
    """Zmapuje raw chess.com game JSON na ChessComGame (= DB row schema)."""
    white = g.get("white") or {}
    black = g.get("black") or {}

    # chess.com `username` field zachovává case (např. "Bete1geuse"), ale URL
    # cesta `/pub/player/{name}` je case-insensitive. Porovnáváme lowercase.
    uname_lower = username.lower()
    white_name_lower = (white.get("username") or "").lower()
    black_name_lower = (black.get("username") or "").lower()

    if white_name_lower == uname_lower:
        color = "white"
        me, opp = white, black
    elif black_name_lower == uname_lower:
        color = "black"
        me, opp = black, white
    else:
        # Měsíční archiv vrací jen partie tohoto usera → tohle by nemělo nastat.
        raise ValueError(
            f"Partie {g.get('uuid')!r}: username {username!r} není ani bílý ani černý "
            f"(white={white_name_lower!r}, black={black_name_lower!r})"
        )

    pgn = g.get("pgn", "") or ""
    eco, opening_name = _parse_opening(pgn)
    end_time_s = g.get("end_time") or 0

    return ChessComGame(
        id=g["uuid"],
        username=username,
        color=color,
        opponent=opp.get("username"),
        opponent_rating=opp.get("rating"),
        my_rating=me.get("rating"),
        result=_to_my_result(me.get("result")),
        # termination = chess.com per-color result kód (z mého pohledu). U win
        # je to redundantní s `result`, u loss/draw vyjadřuje JAK partie skončila
        # (checkmated/resigned/timeout/abandoned/stalemate/repetition/...).
        termination=me.get("result"),
        speed=g.get("time_class"),
        variant=g.get("rules"),
        rated=1 if g.get("rated") else 0,
        opening_eco=eco,
        opening_name=opening_name,
        # POZN. sémantiky: Lichess `createdAt` je začátek partie, chess.com
        # `end_time` je konec. Pro inkrementál (monotónní timestamp) to nevadí.
        # Pro UI zobrazení vzniká drobná nepřesnost (rozdíl typicky < 1h u blitzu).
        # Pokud by někdy vadilo, lze ze StartTime+UTCDate v PGN headerech.
        created_at=(end_time_s * 1000) or None,
        plies=_count_plies(pgn),
        pgn=pgn,
    )
