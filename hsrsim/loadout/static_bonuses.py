"""Static (a) panel bonuses from confirmed light cones + relic sets (D9)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PanelBonus:
    """Additive panel offsets before/alongside relic mains+subs."""

    atk_base: float = 0.0  # LC base ATK (added to character promotion base)
    hp_base: float = 0.0
    def_base: float = 0.0
    atk_pct: float = 0.0
    hp_pct: float = 0.0
    def_pct: float = 0.0
    spd_pct: float = 0.0
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    ehr: float = 0.0
    effect_res: float = 0.0
    notes: list[str] = field(default_factory=list)


# Lv80 = Base + Add*(80-1) on MaxLevel=80 promotion row (SRDC).
_LC_LV80 = {
    # id: (hp, atk, def)
    23024: (489.6 + 7.2 * 79, 293.76 + 4.32 * 79, 183.6 + 2.7 * 79),
    23021: (538.56 + 7.92 * 79, 244.8 + 3.6 * 79, 214.2 + 3.15 * 79),
    23029: (440.64 + 6.48 * 79, 269.28 + 3.96 * 79, 244.8 + 3.6 * 79),
    23023: (489.6 + 7.2 * 79, 195.84 + 2.88 * 79, 306.0 + 4.5 * 79),
    21015: (440.64 + 6.48 * 79, 220.32 + 3.24 * 79, 153.0 + 2.25 * 79),
    22000: (440.64 + 6.48 * 79, 220.32 + 3.24 * 79, 153.0 + 2.25 * 79),
    # 宇宙市场趋势：Promo6 Base + Add*(80-1)
    21016: (489.6 + 7.2 * 79, 171.36 + 2.52 * 79, 183.6 + 2.7 * 79),
}

# AbilityProperty (a) by SI 1–5 ( / EquipmentSkillConfig).
_LC_PROP_BY_SI: dict[int, list[dict[str, float]]] = {
    23024: [
        {"crit_dmg": 0.36},
        {"crit_dmg": 0.42},
        {"crit_dmg": 0.48},
        {"crit_dmg": 0.54},
        {"crit_dmg": 0.60},
    ],
    23021: [
        {"crit_dmg": 0.32},
        {"crit_dmg": 0.39},
        {"crit_dmg": 0.46},
        {"crit_dmg": 0.53},
        {"crit_dmg": 0.60},
    ],
    23029: [
        {"ehr": 0.60},
        {"ehr": 0.70},
        {"ehr": 0.80},
        {"ehr": 0.90},
        {"ehr": 1.00},
    ],
    23023: [
        {"def_pct": 0.40},
        {"def_pct": 0.46},
        {"def_pct": 0.52},
        {"def_pct": 0.58},
        {"def_pct": 0.64},
    ],
    21015: [{}, {}, {}, {}, {}],  # (a) none; (b) Exposed on-hit
    22000: [
        {"ehr": 0.20},
        {"ehr": 0.25},
        {"ehr": 0.30},
        {"ehr": 0.35},
        {"ehr": 0.40},
    ],
    21016: [
        {"def_pct": 0.16},
        {"def_pct": 0.20},
        {"def_pct": 0.24},
        {"def_pct": 0.28},
        {"def_pct": 0.32},
    ],
}

# Confirmed set (a) portions — cavern 4pc + planar 2pc first recommendations
_SET_A = {
    "pioneer_117": {"crit_rate": 0.04},  # 4pc PropertyList; 2pc is (b)
    "izumo_314": {"atk_pct": 0.12},  # + CR if same path (applied separately)
    "sacerdos_121": {"spd_pct": 0.06},  # 2pc; 4pc is (b)
    "broken_keel_310": {"effect_res": 0.10},  # team CD is (b) threshold
    "prisoner_116": {"atk_pct": 0.12},  # 2pc; 4pc DEF ignore is (b)
    "knight_103": {"def_pct": 0.15},  # 2pc; 4pc shield absorb is (c)
    "pan_cosmic_303": {"ehr": 0.10},  # ATK-from-EHR applied after EHR known
    "messenger_108": {"spd_pct": 0.06},  # 2pc Hackerspace; Pela 惯例 2+2 速度
}

IZUMO_SAME_PATH_CR = 0.12
PAN_COSMIC_ATK_FROM_EHR = 0.25  # ATK += min(0.25, 0.25*ehr) * base_atk


def light_cone_bonus(lc_id: int, superimposition: int = 1) -> PanelBonus:
    if not isinstance(superimposition, int) or isinstance(superimposition, bool):
        raise ValueError(f"light cone SI must be int 1–5, got {superimposition!r}")
    if not 1 <= superimposition <= 5:
        raise ValueError(f"light cone SI must be 1–5, got {superimposition}")
    if lc_id not in _LC_LV80:
        raise KeyError(f"unknown light cone {lc_id}")
    if lc_id not in _LC_PROP_BY_SI:
        raise KeyError(f"unknown light cone AbilityProperty for {lc_id}")
    hp, atk, de = _LC_LV80[lc_id]
    b = PanelBonus(atk_base=atk, hp_base=hp, def_base=de)
    props = _LC_PROP_BY_SI[lc_id][superimposition - 1]
    for k, v in props.items():
        setattr(b, k, getattr(b, k) + v)
    b.notes.append(f"LC {lc_id} S{superimposition} (a) applied")
    return b


def set_bonus_a(cavern: str, planar: str) -> PanelBonus:
    b = PanelBonus()
    for key in (cavern, planar):
        if key not in _SET_A:
            raise KeyError(f"unknown set key {key}")
        for k, v in _SET_A[key].items():
            setattr(b, k, getattr(b, k) + v)
        b.notes.append(f"set {key} (a) applied")
    return b


def merge_bonuses(*parts: PanelBonus) -> PanelBonus:
    out = PanelBonus()
    for p in parts:
        for field_name in (
            "atk_base",
            "hp_base",
            "def_base",
            "atk_pct",
            "hp_pct",
            "def_pct",
            "spd_pct",
            "crit_rate",
            "crit_dmg",
            "ehr",
            "effect_res",
        ):
            setattr(out, field_name, getattr(out, field_name) + getattr(p, field_name))
        out.notes.extend(p.notes)
    return out


def pan_cosmic_atk_flat(base_atk: float, ehr: float) -> float:
    """ATK flat from Pan-Cosmic: min(25%, 25%*EHR) * base_atk."""
    return min(PAN_COSMIC_ATK_FROM_EHR, PAN_COSMIC_ATK_FROM_EHR * ehr) * base_atk
