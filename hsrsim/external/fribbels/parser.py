"""Parse Fribbels character conditionals (*.ts) for C0 scalings and combo templates."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from hsrsim.external.fribbels.paths import FRIBBELS_ROOT, character_ts_path

# const foo = basic(e, 0.50, 0.55)  →  C0 = 0.50
_SCALING_RE = re.compile(
    r"const\s+(?P<name>[a-zA-Z0-9_]+)\s*=\s*(?:basic|skill|ult|talent|memoSkill|memoTalent)\(e,\s*"
    r"(?P<c0>-?\d+(?:\.\d+)?),\s*(?P<c3>-?\d+(?:\.\d+)?)\)",
)
# healTallyMultiplierDefault = (e >= 6) ? 50 : (e >= 1) ? 30 : 20
_HEAL_TALLY_MULT_RE = re.compile(
    r"healTallyMultiplierDefault\s*=\s*(?:\(e >= 6\)\s*\?\s*(?P<e6>\d+))?\s*"
    r"(?:\:\s*\(e >= 1\)\s*\?\s*(?P<e1>\d+))?\s*"
    r"(?:\:\s*(?P<c0>\d+))?",
)
# simpler: healTallyMultiplier: healTallyMultiplierDefault in defaults - read literal chain
_HEAL_TALLY_CHAIN_RE = re.compile(
    r"const healTallyMultiplierDefault = \(e >= 6\)\s*\?\s*(\d+)\s*:\s*\(e >= 1\)\s*\?\s*(\d+)\s*:\s*(\d+)",
)
_COMBO_RE = re.compile(
    r"comboTurnAbilities:\s*\[(?P<body>[^\]]+)\]",
    re.DOTALL,
)
_FRIBBELS_CHAR_ID_RE = re.compile(r"id:\s*['\"](\d+)['\"]")


@dataclass
class CharacterFribbelsData:
    char_id: str
    fribbels_game_id: str
    source_path: str
    scalings_c0: dict[str, float] = field(default_factory=dict)
    combo_turn_abilities: list[str] = field(default_factory=list)
    defaults: dict[str, float | int | bool] = field(default_factory=dict)


def parse_c0_scalings(ts_text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for m in _SCALING_RE.finditer(ts_text):
        out[m.group("name")] = float(m.group("c0"))
    return out


def parse_heal_tally_multiplier_c0(ts_text: str) -> int:
    m = _HEAL_TALLY_CHAIN_RE.search(ts_text)
    if m:
        return int(m.group(3))
    return 20


def parse_combo_turn_abilities(ts_text: str) -> list[str]:
    """Return first comboTurnAbilities block (simulation combo, not healSimulation)."""
    for m in _COMBO_RE.finditer(ts_text):
        body = m.group("body")
        if "DEFAULT_SKILL_HEAL" in body or "DEFAULT_ULT_HEAL" in body:
            continue
        tokens = re.findall(r"[A-Z][A-Z0-9_]*", body)
        return tokens
    return []


def parse_default_slider(ts_text: str, name: str) -> int | None:
    m = re.search(rf"{name}:\s*(\d+)", ts_text)
    return int(m.group(1)) if m else None


def hyacine_heal_tally_scaling(scalings: dict[str, float], heal_tally_mult: int = 20) -> float:
    """Fribbels: memoSkillScaling * healTallyMultiplier (C0 default mult=20)."""
    return scalings.get("memoSkillScaling", 0.20) * heal_tally_mult


def evernight_memo_hp_scaling(
    scalings: dict[str, float],
    memoria_stacks: int,
) -> float:
    """Port Evernight.ts MEMO_SKILL hpScaling formula."""
    base = scalings.get("memoSkillScaling", 0.50)
    add = scalings.get("memoSkillAdditionalScaling", 0.10)
    enhanced = scalings.get("memoSkillEnhancedScaling", 0.12)
    if memoria_stacks >= 16:
        return enhanced * memoria_stacks
    return base + math.floor(memoria_stacks / 4) * add


def castorice_memo_skill_scaling(scalings: dict[str, float], enhances: int = 3) -> float:
    if enhances <= 1:
        return scalings.get("memoSkillScaling1", 0.24)
    if enhances == 2:
        return scalings.get("memoSkillScaling2", 0.28)
    return scalings.get("memoSkillScaling3", 0.34)


def castorice_memo_talent_total_scaling(
    scalings: dict[str, float],
    hits: int = 6,
) -> float:
    return hits * scalings.get("memoTalentScaling", 0.40)


def load_character_fribbels(char_id: str) -> CharacterFribbelsData:
    path = character_ts_path(char_id)
    text = path.read_text(encoding="utf-8")
    game_id_m = _FRIBBELS_CHAR_ID_RE.search(text)
    game_id = game_id_m.group(1) if game_id_m else "?"
    scalings = parse_c0_scalings(text)
    combo = parse_combo_turn_abilities(text)
    defaults: dict[str, float | int | bool] = {}
    if char_id == "hyacine":
        defaults["healTallyMultiplier"] = parse_heal_tally_multiplier_c0(text)
    if char_id == "evernight":
        stacks = parse_default_slider(text, "memoriaStacks")
        defaults["memoriaStacks"] = stacks if stacks is not None else 16
    if char_id == "castorice":
        defaults["memoSkillEnhances"] = parse_default_slider(text, "memoSkillEnhances") or 3
        defaults["memoTalentHits"] = parse_default_slider(text, "memoTalentHits") or 6
    return CharacterFribbelsData(
        char_id=char_id,
        fribbels_game_id=game_id,
        source_path=str(path.relative_to(FRIBBELS_ROOT)),
        scalings_c0=scalings,
        combo_turn_abilities=combo,
        defaults=defaults,
    )
