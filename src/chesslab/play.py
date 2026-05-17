"""Hra proti UCI enginu (Stockfish) — state + akce.

Modul drží jeden globální `GameState` (jeden lokální uživatel = jedna hra).
Persistent engine subprocess otevřený přes celou hru — spawn-per-tah by lagoval
každý tah o 100-200ms. Lock kvůli souběhu requestů (uvicorn může běžet
vícevláknově i pro sync endpointy).

Endpointy v `app.py` volají funkce odsud, ne přímo `_state` — encapsulation.
"""

from __future__ import annotations

import datetime
import threading
from dataclasses import dataclass, field

import chess
import chess.engine
import chess.pgn
from pydantic import BaseModel, Field

from chesslab.engine import _stockfish_path_or_raise

# === Konstanty ===============================================================

# Skill Level je Stockfish UCI option, slider 0-20 (0 = nejslabší).
SKILL_MIN = 0
SKILL_MAX = 20
SKILL_DEFAULT = 5

# Think time = budget enginu na tah v sekundách.
THINK_TIME_MIN = 0.1
THINK_TIME_MAX = 2.0


def default_think_time(skill: int) -> float:
    """Default think time scaled podle skill levelu.

    Lineární interpolace: skill 0 → 0.1s, skill 20 → 1.0s.
    Silnější engine dostane víc času (na max skill s 0.1s by hrál hůř, než by mohl).
    Vzorec: 0.1 + skill × 0.045.
    """
    return round(0.1 + skill * 0.045, 3)


# === Pydantic response modely ================================================
#
# Reusujeme z app.py — modely tady jsou jediný zdroj pravdy o tvaru API odpovědi.


class MoveInfo(BaseModel):
    """Jeden tah, jak ho posíláme klientovi (po hráči nebo po enginu)."""

    uci: str = Field(..., description="UCI notace (např. 'e2e4', 'e7e8q' pro promoci).")
    san: str = Field(..., description="SAN notace (např. 'e4', 'Nf3', 'O-O').")


class PlayStateResponse(BaseModel):
    """Stav hry vrácený po start/move/undo/resign — single source of truth pro frontend."""

    fen: str = Field(..., description="Aktuální FEN pozice po všech provedených tazích.")
    # Mezistav po hráčově tahu (před engine reakcí) — kvůli two-phase animaci.
    # Rošáda / en passant / promoce mají vizuální „skrytý" pohyb (věž, vzatý pěšec,
    # transformace figury), který chessboard.js sám neumí. Klient animuje ve dvou fázích.
    fen_after_player_move: str | None = Field(
        None,
        description="FEN po hráčově tahu, PŘED engine reakcí. None pokud hráč v tomto requestu netáhl.",
    )
    pgn: str = Field(..., description="PGN tahy bez headerů (jako '1. e4 e5 2. Nf3 Nc6').")
    status: str = Field(..., description="Lidsky čitelný status (např. 'Tvůj tah (bílý)', 'Mat — bílý vyhrál').")
    turn: str = Field(..., description="Kdo je na tahu: 'w' / 'b'.")
    player_color: str = Field(..., description="Hráčova barva: 'w' / 'b'.")
    game_over: bool = Field(..., description="True pokud hra skončila (mat/pat/resign/insufficient).")
    result: str | None = Field(None, description="PGN výsledek '1-0' / '0-1' / '1/2-1/2' jen pokud game_over.")
    # Tahy provedené v tomto requestu (může být oba, jeden, nebo žádný — záleží na akci).
    last_player_move: MoveInfo | None = Field(None, description="Hráčův tah z tohoto requestu (None u start/undo/resign).")
    last_engine_move: MoveInfo | None = Field(None, description="Engine tah z tohoto requestu (None pokud engine nehrál).")
    can_undo: bool = Field(..., description="True pokud existují tahy k vrácení (UI disabling).")


# === GameState ==============================================================


@dataclass
class GameState:
    """Stav jedné rozjeté hry — board + engine + nastavení."""

    board: chess.Board = field(default_factory=chess.Board)
    # engine = None znamená "žádná hra ještě nezačala" (po startu serveru).
    engine: chess.engine.SimpleEngine | None = None
    # Display name z UCI handshake (`engine.id["name"]`). Použije se v status
    # textu („X přemýšlí…") a v PGN White/Black headeru.
    engine_name: str = "Engine"
    # True pokud engine má UCI option 'Skill Level' (Stockfish ano, custom enginy
    # zatím ne). Drží jednak, jestli configure skill (v start_game), jednak
    # jestli přidat suffix '(skill N)' do PGN headeru.
    engine_supports_skill: bool = False
    player_color: chess.Color = chess.WHITE
    skill: int = SKILL_DEFAULT
    think_time: float = field(default_factory=lambda: default_think_time(SKILL_DEFAULT))
    resigned: bool = False
    # Lock chrání všechny mutace boardu/enginu před souběhem requestů.
    lock: threading.Lock = field(default_factory=threading.Lock)


# Modulový singleton — jeden lokální uživatel = jedna hra. Stejný pattern jako reference.
_state = GameState()


# === Helpery (vnitřní) =======================================================


def _quit_engine_silently() -> None:
    """Zavře engine subprocess, pokud běží. Chyby ignoruje (cleanup-only)."""
    if _state.engine is not None:
        try:
            _state.engine.quit()
        except Exception:
            pass
        _state.engine = None


def _status_text() -> str:
    """Lidsky čitelný status pro sidebar."""
    if _state.resigned:
        # Resign = vyhrává soupeř hráče.
        winner = "černý" if _state.player_color == chess.WHITE else "bílý"
        return f"Vzdal ses — vyhrál {winner}."

    outcome = _state.board.outcome()
    if outcome is not None:
        # outcome.termination je enum (CHECKMATE, STALEMATE, INSUFFICIENT_MATERIAL, …).
        return f"Konec: {outcome.result()} ({outcome.termination.name})"

    if _state.board.turn == _state.player_color:
        color = "bílý" if _state.board.turn == chess.WHITE else "černý"
        check = " (ŠACH!)" if _state.board.is_check() else ""
        return f"Tvůj tah ({color}){check}"
    # Engine name přijde z UCI handshake (`engine.id["name"]`) — pro Stockfish
    # to bude něco jako 'Stockfish 16.1 by ...', pro vlastní engine viz ENGINE_NAME.
    return f"{_state.engine_name} přemýšlí…"


def _pgn_result() -> str:
    """PGN výsledek: '1-0', '0-1', '1/2-1/2' nebo '*' (nedokončeno)."""
    if _state.resigned:
        # Hráč resignoval → vyhrává soupeř.
        return "0-1" if _state.player_color == chess.WHITE else "1-0"
    outcome = _state.board.outcome()
    return outcome.result() if outcome is not None else "*"


def _pgn_moves_only() -> str:
    """PGN bez hlaviček — jen tahy jako '1. e4 e5 2. Nf3 Nc6'.

    Pro sidebar list. Reference si tohle počítá samo přes StringExporter.
    """
    if not _state.board.move_stack:
        return ""
    game = chess.pgn.Game.from_board(_state.board)
    exp = chess.pgn.StringExporter(headers=False, comments=False, variations=False)
    text = game.accept(exp).strip()
    # '*' = PGN značka "nedokončeno" — pro běžící hru rušivá v UI, oříznem.
    return text.rstrip(" *").rstrip()


def _pgn_full(skill: int, player_color: chess.Color) -> str:
    """Kompletní PGN se Seven Tag Roster — pro download."""
    game = chess.pgn.Game.from_board(_state.board)
    player_white = player_color == chess.WHITE
    # Engine display name pro PGN — suffix '(skill N)' přidáme jen pokud engine
    # Skill Level skutečně podporuje (Stockfish ano, vlastní enginy ne).
    engine_label = _state.engine_name
    if _state.engine_supports_skill:
        engine_label = f"{engine_label} (skill {skill})"
    # Seven Tag Roster v pořadí předepsaném PGN standardem (Event, Site, Date, Round, White, Black, Result).
    game.headers["Event"] = "Casual Game"
    game.headers["Site"] = "ChessLab (localhost)"
    game.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
    game.headers["Round"] = "-"
    game.headers["White"] = "Player" if player_white else engine_label
    game.headers["Black"] = engine_label if player_white else "Player"
    game.headers["Result"] = _pgn_result()
    exp = chess.pgn.StringExporter(headers=True, comments=False, variations=False)
    return game.accept(exp)


def _can_undo() -> bool:
    """True pokud existuje aspoň jeden tah, který lze vrátit."""
    return len(_state.board.move_stack) > 0


def _build_response(
    last_player_move: MoveInfo | None = None,
    last_engine_move: MoveInfo | None = None,
    fen_after_player_move: str | None = None,
) -> PlayStateResponse:
    """Sestaví aktuální PlayStateResponse — jediný zdroj pravdy o tvaru odpovědi."""
    turn = "w" if _state.board.turn == chess.WHITE else "b"
    player_color = "w" if _state.player_color == chess.WHITE else "b"
    # game_over zahrnuje resign i přírodní konec (mat/pat/insufficient).
    game_over = _state.resigned or _state.board.is_game_over()
    result = _pgn_result() if game_over else None

    return PlayStateResponse(
        fen=_state.board.fen(),
        fen_after_player_move=fen_after_player_move,
        pgn=_pgn_moves_only(),
        status=_status_text(),
        turn=turn,
        player_color=player_color,
        game_over=game_over,
        result=result,
        last_player_move=last_player_move,
        last_engine_move=last_engine_move,
        can_undo=_can_undo(),
    )


def _engine_move() -> MoveInfo:
    """Engine táhne. Volat výhradně pod _state.lock a jen když engine != None.

    Vrací MoveInfo s UCI + SAN tahu. SAN musí být počítán PŘED push (závisí na pozici).
    """
    assert _state.engine is not None, "engine musí být inicializován"
    result = _state.engine.play(_state.board, chess.engine.Limit(time=_state.think_time))
    move = result.move
    assert move is not None, "Stockfish vrátil None místo tahu — toto by se nemělo stát"
    # SAN PŘED push — po pushi by board byl v jiné pozici a SAN by neměl smysl.
    san = _state.board.san(move)
    _state.board.push(move)
    return MoveInfo(uci=move.uci(), san=san)


def _parse_drag_move(src: str, dst: str) -> chess.Move:
    """Drag pošle 'e2' a 'e4'. Zkusíme UCI bez i s 'q' (auto-promote na dámu).

    UI neumí výběr proměny (MVP), default = dáma (95% případů).
    Reference (Chess/06_play/play.py) má stejnou logiku.
    """
    for promo_suffix in ["", "q"]:
        uci = src + dst + promo_suffix
        try:
            move = chess.Move.from_uci(uci)
            if move in _state.board.legal_moves:
                return move
        except ValueError:
            # Špatně formátovaný UCI (např. 'e2x9') — zkusíme další variantu nebo padneme do raise.
            continue
    raise ValueError(f"Tah {src}-{dst} není platný.")


# === Veřejné akce ============================================================


def start_game(
    color: chess.Color,
    skill: int,
    think_time: float,
    engine_path: str | None = None,
) -> PlayStateResponse:
    """Inicializuje novou hru — restart enginu, reset boardu, configure skill.

    Pokud hráč hraje za černého, engine táhne hned na začátku (last_engine_move
    v odpovědi). Jinak je hráč na tahu a engine čeká.

    Args:
        engine_path: cesta k UCI binárce. None → default Stockfish (`STOCKFISH_PATH`).
            Skill Level se aplikuje jen pokud engine option `Skill Level` podporuje
            (Stockfish ano, vlastní ChessLab enginy zatím ne — tiše se přeskočí).

    Raises:
        FileNotFoundError: pokud Stockfish binárka neexistuje (default path) nebo
            pokud zadaná `engine_path` na disku není.
    """
    from pathlib import Path  # lokální import — používáme jen tady (validace cesty).

    with _state.lock:
        _quit_engine_silently()

        _state.board = chess.Board()
        _state.resigned = False
        _state.player_color = color
        _state.skill = skill
        _state.think_time = think_time

        # Resolve path: None / prázdný string → default Stockfish.
        if engine_path is None or not engine_path.strip():
            path = _stockfish_path_or_raise()
        else:
            path = engine_path
            if not Path(path).exists():
                raise FileNotFoundError(f"Engine binárka neexistuje: {path}")

        # Spawn engine subprocess.
        _state.engine = chess.engine.SimpleEngine.popen_uci(path)

        # Zachytit display name z UCI handshake (`engine.id` po popen_uci je dict
        # s 'name' a 'author', pokud je engine poslal). Pro Stockfish to bude
        # např. 'Stockfish 16.1 by ...'. Fallback 'Engine' pro UCI binárky, které
        # `id name` neposílají (vzácné, ale defenzivně).
        _state.engine_name = _state.engine.id.get("name", "Engine")

        # Skill Level configure jen pokud engine UCI option má (Stockfish ano,
        # vlastní enginy ne). Stejný pattern jako arena._maybe_configure_skill.
        _state.engine_supports_skill = "Skill Level" in _state.engine.options
        if _state.engine_supports_skill:
            _state.engine.configure({"Skill Level": skill})

        # Hraje-li hráč černého, engine táhne první (otevírá hru).
        engine_move: MoveInfo | None = None
        if _state.board.turn != _state.player_color:
            engine_move = _engine_move()

        return _build_response(last_engine_move=engine_move)


def apply_player_move(src: str, dst: str) -> PlayStateResponse:
    """Aplikuje hráčův tah, pak nechá engine reagovat (pokud hra běží dál).

    Vrací odpověď s oběma tahy (last_player_move + last_engine_move).
    Engine tah je None pokud po hráčově tahu skončila hra (mat/pat).

    Raises:
        ValueError: tah není legální, není hráčův tah, hra už skončila, engine není připraven.
    """
    with _state.lock:
        if _state.engine is None:
            raise ValueError("Engine není připraven — začni novou hru.")
        if _state.resigned or _state.board.is_game_over():
            raise ValueError("Hra už skončila.")
        if _state.board.turn != _state.player_color:
            # Tohle by se nemělo stát při normálním UI flow (request-response je sync),
            # ale chrání před race condition / dvojklikem.
            raise ValueError("Není tvůj tah.")

        move = _parse_drag_move(src, dst)
        # SAN PŘED push (závisí na pozici).
        player_san = _state.board.san(move)
        _state.board.push(move)
        # FEN snapshot HNED PO hráčově tahu — klient ho používá pro two-phase
        # animaci u rošády/en passant/promoce (chessboard.js o "skrytém" pohybu
        # věže ani vzatém pěšci neví, animuje to společně s engine reakcí).
        fen_after_player = _state.board.fen()
        player_info = MoveInfo(uci=move.uci(), san=player_san)

        # Engine reakce, pokud hra ještě běží.
        engine_info: MoveInfo | None = None
        if not _state.board.is_game_over():
            engine_info = _engine_move()

        return _build_response(
            last_player_move=player_info,
            last_engine_move=engine_info,
            fen_after_player_move=fen_after_player,
        )


def undo_last_move() -> PlayStateResponse:
    """Vrátí poslední hráčův + engine tah (pop 2 plies), aby byl hráč zase na tahu.

    Edge cases:
      - Hráč jako bílý, ještě nezahrál → move_stack prázdný → ValueError.
      - Hráč jako černý hned po startu (engine táhl jednou) → pop 1 ply,
        hráč je na tahu hned (před prvním vlastním tahem už nelze).
      - Po resignu → vyresetuje resigned flag, vrátí 2 plies.
      - Po matu → vyresetuje game_over implicitně (po pop už pozice není matem).

    Raises:
        ValueError: žádné tahy k vrácení.
    """
    with _state.lock:
        if _state.engine is None:
            raise ValueError("Engine není připraven — začni novou hru.")
        if len(_state.board.move_stack) == 0:
            raise ValueError("Žádné tahy k vrácení.")

        # Resign flag zrušíme — po undo už hra zase běží.
        _state.resigned = False

        # Pop 2 plies (hráč + engine), pokud možno. Pop 1 jen pokud na stacku je
        # jediný tah (engine otevřel hru za bílého, hráč ještě nezahrál).
        pops = 2 if len(_state.board.move_stack) >= 2 else 1
        for _ in range(pops):
            _state.board.pop()

        return _build_response()


def resign_game() -> PlayStateResponse:
    """Hráč vzdal. Nastaví resigned flag, vrátí finální stav.

    Engine se nezavírá — uživatel může chtít „nová hra" hned a šetříme spawn.

    Raises:
        ValueError: hra už skončila / není rozjetá.
    """
    with _state.lock:
        if _state.engine is None:
            raise ValueError("Engine není připraven — začni novou hru.")
        if _state.resigned or _state.board.is_game_over():
            raise ValueError("Hra už skončila.")

        _state.resigned = True
        return _build_response()


def get_pgn_download() -> str:
    """Vrátí kompletní PGN se Seven Tag Roster pro download.

    Raises:
        ValueError: žádná hra neběží.
    """
    with _state.lock:
        if _state.engine is None:
            raise ValueError("Není rozjetá hra.")
        return _pgn_full(skill=_state.skill, player_color=_state.player_color)
