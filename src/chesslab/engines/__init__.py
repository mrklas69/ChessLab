"""Vlastní ChessLab enginy (UCI standalone) + discovery dostupných enginů.

Každý ChessLab engine v tomto subpackage = samostatný Python skript komunikující
přes UCI protokol na stdin/stdout. Po `uv sync` se přes [project.scripts] entry
v pyproject.toml přeloží na binárku v `.venv/Scripts/` (Windows) nebo
`.venv/bin/` (Unix), kterou pak Arena/Play UI dostane jako 'path' k enginu.

Verze (od nejjednodušší):
  v0 — random_engine: náhodný legální tah, žádná evaluace, žádný search.

Discovery (`list_available_engines`): kombinuje hardcoded Stockfish (pokud
binárka na disku existuje) + auto-glob `chesslab-*` v scripts dir aktivního
venv. Endpoint `GET /api/engines/list` ji exponuje pro UI dropdowny.
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from pydantic import BaseModel, Field

from chesslab.engine import _stockfish_path

# Mapping basename → (display name, supports_skill).
# Když přidám nový vlastní engine, doplním sem řádek. Pokud chybí, fallback
# vygeneruje display name z basenamu a supports_skill bude False.
_KNOWN_ENGINES: dict[str, tuple[str, bool]] = {
    "chesslab-random": ("ChessLab Random v0", False),
    "chesslab-greedy": ("ChessLab Greedy v1", False),
    "chesslab-minimax": ("ChessLab Minimax v2.7", False),
}


class EngineInfo(BaseModel):
    """Záznam o dostupném enginu — pro dropdown v UI (Play, Arena)."""

    id: str = Field(..., description="Stabilní ID (basename binárky), např. 'stockfish' nebo 'chesslab-random'.")
    name: str = Field(..., description="Display name pro UI, např. 'Stockfish' nebo 'ChessLab Random v0'.")
    path: str = Field(..., description="Absolutní cesta k binárce.")
    supports_skill: bool = Field(
        ...,
        description="True pokud engine má UCI option 'Skill Level' (Stockfish ano, vlastní enginy zatím ne). UI podle toho disabluje skill slider.",
    )


def _scripts_dir() -> Path:
    """Vrátí scripts dir aktivního Python prostředí.

    Na Windows typicky `<venv>/Scripts`, na Unixu `<venv>/bin`. sysconfig
    je standardní knihovní cesta — funguje konzistentně přes platformy.
    Použijeme ho pro auto-discovery `chesslab-*` binárek.
    """
    return Path(sysconfig.get_path("scripts"))


def _resolve_chesslab_engine(path: Path) -> EngineInfo | None:
    """Z cesty k binárce vyrobí EngineInfo. None pokud nejde o náš engine.

    Filtruje:
      - jen soubory (ne dirs / symlinks na dirs),
      - basename začínající 'chesslab-' a NE 'chesslab' (= hlavní app server).

    Display name + supports_skill bere z _KNOWN_ENGINES, jinak fallback
    'ChessLab <Suffix>' a supports_skill=False.
    """
    if not path.is_file():
        return None

    # `.stem` odřízne `.exe` (Windows) nebo nic (Unix bez extension).
    stem = path.stem
    if not stem.startswith("chesslab-"):
        return None
    # Hlavní app server 'chesslab' (bez pomlčky) nás taky netreba — glob 'chesslab-*'
    # to už filtruje, ale jistota.
    if stem == "chesslab":
        return None

    if stem in _KNOWN_ENGINES:
        name, supports_skill = _KNOWN_ENGINES[stem]
    else:
        # Fallback pro engine, který existuje na disku, ale nemáme ho v mapě
        # (např. user dev branch s rozpracovaným v1). Zkapitalizujeme suffix.
        suffix = stem.removeprefix("chesslab-")
        name = f"ChessLab {suffix.capitalize()}"
        supports_skill = False

    return EngineInfo(
        id=stem,
        name=name,
        path=str(path),
        supports_skill=supports_skill,
    )


def display_name_for_path(path: str) -> str | None:
    """Vrátí display name pro engine binárku, NEBO None pokud ho neznáme.

    Lookup logika:
      1) Stockfish (basename začíná 'stockfish') → 'Stockfish'.
      2) `chesslab-*` v `_KNOWN_ENGINES` → registrovaný display name.
      3) Jinak None → volající si vyřeší fallback (např. path-based heurystika
         v arena.py).

    Použití: arena.py / play.py má z UI cestu k binárce, ale display label
    v PGN headeru a v results panelu chce čitelný (ne 'stockfish-windows-x86-64-avx2').
    Místo spawnu UCI handshakem pro `id name` (drahé) lookup do registry.
    """
    from pathlib import Path

    stem = Path(path).stem
    if stem.startswith("stockfish"):
        return "Stockfish"
    if stem in _KNOWN_ENGINES:
        return _KNOWN_ENGINES[stem][0]
    return None


def list_available_engines() -> list[EngineInfo]:
    """Vrátí seznam dostupných enginů pro UI dropdown.

    Pořadí: Stockfish první (default, pokud existuje), pak vlastní enginy
    seřazené abecedně podle basename (deterministicky).

    Stockfish přidáme jen pokud binárka skutečně existuje (jinak by dropdown
    nabízel nefunkční volbu). Vlastní enginy se hledají v scripts dir aktivního
    venv přes glob `chesslab-*` — automaticky to chytí každý nový z [project.scripts]
    po `uv sync`.
    """
    engines: list[EngineInfo] = []

    # Stockfish (jen pokud na disku — neukazovat broken default v UI).
    sf_path = _stockfish_path()
    if Path(sf_path).exists():
        engines.append(
            EngineInfo(
                id="stockfish",
                name="Stockfish",
                path=sf_path,
                supports_skill=True,
            )
        )

    # Vlastní enginy — auto-glob v scripts dir aktivního venv.
    scripts = _scripts_dir()
    if scripts.exists():
        # `sorted` + `glob('chesslab-*')` chytne i Unix variantu bez .exe.
        # Pro Windows přijdou i .exe i .py wrappery (.py wrapper má jiné name,
        # přesto by ho `is_file()` propustil) — drobnost, žádný .py tam být nemá.
        for path in sorted(scripts.glob("chesslab-*")):
            info = _resolve_chesslab_engine(path)
            if info is not None:
                engines.append(info)

    return engines
