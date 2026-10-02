""": fail-loud wiring gate for declared eidolons / light cones.

Simulator has no preference for any config; declaring an unwired capability
must raise so benchmarks refuse to emit numbers.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# Per-character highest eidolon rank with runtime support.
WIRED_EIDOLON_MAX: dict[str, int] = {
    "acheron": 2,  # E1 CR-on-debuff, E2 abyss + turn_start dream/knot
    "jiaoqiu": 0,
    "sparkle": 0,
    "aventurine": 0,
    "pela": 6,  # E1.5: E4 ice RES on skill; E6 extra hit; SkillAdd baked in JSON
    "fu_xuan": 0,  # E1.5 stub tank for Trend LC leave-out
}

# Light cones with AbilityProperty (a) S1–S5 tables.
WIRED_LC_ABILITY_PROPERTY: frozenset[int] = frozenset(
    {23024, 23029, 23021, 23023, 21015, 22000, 21016}
)

# Light cones with conditional (b) runtime fully or partially wired.
WIRED_LC_CONDITIONAL: frozenset[int] = frozenset(
    {23024, 23029, 23021, 23023, 21015, 22000, 21016}
)

# Partial (b): load allowed; must surface in notes/gaps (do not reject paper team).
LC_CONDITIONAL_GAPS: dict[int, str] = {}

# ParamList indexed by SI-1 (from EquipmentSkillConfig / audit).
_LC_PARAM_LIST: dict[int, list[list[float]]] = {
    23024: [
        [0.36, 0.24, 0.24],
        [0.42, 0.28, 0.28],
        [0.48, 0.32, 0.32],
        [0.54, 0.36, 0.36],
        [0.60, 0.40, 0.40],
    ],
    23029: [
        [0.60, 0.60, 0.10, 2, 0.60, 0.14],
        [0.70, 0.60, 0.12, 2, 0.60, 0.16],
        [0.80, 0.60, 0.14, 2, 0.60, 0.18],
        [0.90, 0.60, 0.16, 2, 0.60, 0.20],
        [1.00, 0.60, 0.18, 2, 0.60, 0.22],
    ],
    23021: [
        [0.32, 0.28, 4, 4, 0.10, 3],
        [0.39, 0.35, 4, 4, 0.11, 3],
        [0.46, 0.42, 4, 4, 0.12, 3],
        [0.53, 0.49, 4, 4, 0.13, 3],
        [0.60, 0.56, 4, 4, 0.14, 3],
    ],
    23023: [
        [0.40, 0.40, 2, 1, 0.10, 2],
        [0.46, 0.46, 2, 1.15, 0.115, 2],
        [0.52, 0.52, 2, 1.30, 0.13, 2],
        [0.58, 0.58, 2, 1.45, 0.145, 2],
        [0.64, 0.64, 2, 1.60, 0.16, 2],
    ],
    # 决心如汗珠般闪耀：#1 基础概率, #2 攻陷减防, #3 持续
    21015: [
        [0.60, 0.12, 1],
        [0.70, 0.13, 1],
        [0.80, 0.14, 1],
        [0.90, 0.15, 1],
        [1.00, 0.16, 1],
    ],
    # 新手任务开始前：#1 EHR, #2 攻击减防目标回能
    22000: [
        [0.20, 4],
        [0.25, 5],
        [0.30, 6],
        [0.35, 7],
        [0.40, 8],
    ],
    # 宇宙市场趋势：#1 DEF%, #2 受击基础概率, #3 灼烧=DEF×, #4 持续
    21016: [
        [0.16, 1.00, 0.40, 2],
        [0.20, 1.05, 0.50, 2],
        [0.24, 1.10, 0.60, 2],
        [0.28, 1.15, 0.70, 2],
        [0.32, 1.20, 0.80, 2],
    ],
}

ACHERON_E1_CRIT_RATE = 0.18


def abyss_multiplier(nihility_ally_count: int, eidolon: int) -> float:
    """Acheron Abyss (奈落) independent original_mult.

    E0 tiers: ≥1 → 1.15, ≥2 → 1.60 (exclude self).
    E2+: highest tier needs one fewer nihility ally → ≥1 → 1.60.
    """
    n = max(0, int(nihility_ally_count))
    e = int(eidolon)
    if e >= 2:
        if n >= 1:
            return 1.60
        return 1.0
    if n >= 2:
        return 1.60
    if n >= 1:
        return 1.15
    return 1.0


def lc_param_list(lc_id: int, superimposition: int) -> list[float]:
    """Return ParamList for LC id at SI 1–5."""
    if lc_id not in _LC_PARAM_LIST:
        raise KeyError(f"unknown light cone ParamList for {lc_id}")
    if not isinstance(superimposition, int) or isinstance(superimposition, bool):
        raise ValueError(f"light cone SI must be int 1–5, got {superimposition!r}")
    if not 1 <= superimposition <= 5:
        raise ValueError(f"light cone SI must be 1–5, got {superimposition}")
    return list(_LC_PARAM_LIST[lc_id][superimposition - 1])


def wired_eidolon_max(character_id: str) -> int:
    """Highest wired eidolon; unlisted characters only allow E0."""
    return int(WIRED_EIDOLON_MAX.get(character_id, 0))


def assert_config_wired(
    *,
    eidolons: Mapping[str, int] | None = None,
    characters: Sequence[Any] | None = None,
    loadout: Mapping[str, Any] | None = None,
    team_spec: Any | None = None,
) -> list[str]:
    """Raise ValueError if declared config exceeds wired capabilities.

    Returns notes/gaps for partial wires (e.g. 23023 FUA). Call from
    ``load_team_spec`` and L1 ``build_l1_input``.
    """
    notes: list[str] = []

    eid_map: dict[str, int] = {}
    if team_spec is not None:
        eid_map.update(dict(getattr(team_spec, "eidolons", {}) or {}))
        if characters is None:
            scenario = getattr(team_spec, "scenario", None)
            if scenario is not None:
                characters = list(getattr(scenario, "allies", []) or [])
    if eidolons:
        eid_map.update({str(k): int(v) for k, v in eidolons.items()})

    if characters:
        for char in characters:
            cid = str(getattr(char, "id", ""))
            e = int(getattr(char, "eidolon", eid_map.get(cid, 0)))
            eid_map[cid] = e

    for cid, e in eid_map.items():
        if not isinstance(e, int) or isinstance(e, bool) or not 0 <= e <= 6:
            raise ValueError(f"eidolons.{cid} must be int 0–6, got {e!r}")
        cap = wired_eidolon_max(cid)
        if e > cap:
            raise ValueError(
                f"eidolon not wired: {cid} declared E{e} but runtime supports "
                f"at most E{cap}; refusing to emit numbers"
            )

    # Collect LC declarations from characters and/or loadout yaml.
    lc_decls: list[tuple[str, int, int]] = []
    if characters:
        for char in characters:
            cid = str(getattr(char, "id", ""))
            lc_id = getattr(char, "light_cone_id", None)
            if lc_id is None:
                continue
            si = int(getattr(char, "light_cone_superimposition", 1) or 1)
            lc_decls.append((cid, int(lc_id), si))

    if loadout:
        chars_cfg = loadout.get("characters") or {}
        for cid, block in chars_cfg.items():
            if not isinstance(block, dict):
                continue
            lc = block.get("light_cone") or {}
            if not lc:
                continue
            lc_id = int(lc["id"])
            si = int(lc.get("superimposition", 1))
            if not any(c == cid and i == lc_id for c, i, _ in lc_decls):
                lc_decls.append((str(cid), lc_id, si))

    for cid, lc_id, si in lc_decls:
        if not 1 <= si <= 5:
            raise ValueError(
                f"{cid} light cone {lc_id} SI={si} out of range 1–5"
            )
        if lc_id not in WIRED_LC_ABILITY_PROPERTY:
            raise ValueError(
                f"light cone {lc_id} AbilityProperty (a) not wired "
                f"(holder={cid}); refusing to emit numbers"
            )
        if lc_id in LC_CONDITIONAL_GAPS:
            notes.append(LC_CONDITIONAL_GAPS[lc_id])
        elif lc_id not in WIRED_LC_CONDITIONAL:
            raise ValueError(
                f"light cone {lc_id} conditional (b) not wired "
                f"(holder={cid}); refusing to emit numbers"
            )

    return notes


# Declared gaps that make paper-team results 「配置不完整」(not silent notes).
# E2: relic (b)/(c) wired → removed from gaps. Eidolon over-limit is fail-loud
# via assert_config_wired (not a standing incompleteness for in-range paper E2/E0).
PAPER_CONFIG_GAPS: tuple[str, ...] = ()

# Per-team declared gaps. complete=True and non-empty gaps must never both appear.
TEAM_CONFIG_GAPS: dict[str, tuple[str, ...]] = {
    "acheron_old_pela_fx_res": (
        "符玄为生存位stub（无秘技/行迹/护盾数值）；第二次留出测完整实现",
    ),
    "acheron_jq_fx": (
        "符玄为生存位stub（无秘技/行迹/护盾数值）；第二次留出测完整实现",
    ),
}

# Closed gaps (documented; not incompleteness): 铁卫2件仅防御不影响伤害口径。
PELA_CLOSED_GAPS: tuple[str, ...] = (
    "佩拉遗器铁卫2件：仅防御面板，不影响伤害口径（已关闭缺口）",
)


def assert_l1_consumes_paper_config(l1_input: Any) -> list[str]:
    """Verify L1 input already multiplies paper-team LC(b) / E1 / Abyss / counters.

    Returns notes for soft warnings (e.g. CR already capped). Raises ValueError
    when a required consume path is missing.

    : gate covers damage consume **and** counter/resource flow consume
    (E2 turn-start dream, mirage→R1 fires).
    """
    notes: list[str] = []
    missing: list[str] = []
    contexts = getattr(l1_input, "action_contexts", None) or {}
    buffs = list(getattr(l1_input, "buffs", None) or [])
    counters = list(getattr(l1_input, "counters", None) or [])
    input_notes = [str(n) for n in (getattr(l1_input, "notes", None) or [])]
    ach_ctx = contexts.get("acheron") or {}
    all_ach = [c for ctxs in ach_ctx.values() for c in ctxs]
    if not all_ach:
        missing.append("acheron action_contexts empty")
    else:
        mults = [float(c.original_mult) for c in all_ach]
        if not any(abs(m - 1.60) < 1e-6 for m in mults):
            missing.append(
                f"acheron original_mult≈1.60 missing (got {sorted(set(mults))})"
            )
        crs = [float(c.attacker_crit_rate) for c in all_ach]
        if max(crs) >= 0.999:
            notes.append("E1 crit_rate path: panel already at cap after +0.18 (or raw)")

    mirage_buffs = [
        b
        for b in buffs
        if "lc_mirage" in str(getattr(b, "id", ""))
        and float(getattr(b, "dmg_boost", 0.0) or 0.0) > 0.0
    ]
    if not mirage_buffs:
        missing.append("23024 泡影：缺少 lc_mirage ActionBuff")
    else:
        modes = {str(getattr(b, "coverage_mode", "")) for b in mirage_buffs}
        if "mirage_residual" not in modes and "mirage_same_hit" not in modes:
            missing.append("23024 泡影：未使用 mirage_residual/mirage_same_hit 覆盖")
        if not any(
            getattr(b, "applies_to", None) is not None
            and "ult" in (getattr(b, "applies_to") or [])
            and "ult_extra" in str(getattr(b, "id", ""))
            for b in mirage_buffs
        ):
            missing.append("23024 泡影：缺少终结技额外 dmg_boost buff（applies_to=ult）")
    if not any("L1_CONSUMES:23024" in n for n in input_notes):
        missing.append("23024 泡影：缺少 L1_CONSUMES 标记")

    has_vuln = any(
        float(getattr(b, "vuln", 0.0) or 0.0) > 0.0
        and "lc_unarmored" in str(getattr(b, "id", ""))
        for b in buffs
    )
    if not has_vuln:
        missing.append("23029 卸甲/穷寇 vuln 未进 L1 buffs")

    mask_buffs = [
        b
        for b in buffs
        if "lc_mask" in str(getattr(b, "id", ""))
        and (
            float(getattr(b, "crit_rate", 0.0) or 0.0) > 0.0
            or float(getattr(b, "crit_dmg", 0.0) or 0.0) > 0.0
        )
    ]
    if not mask_buffs:
        missing.append("23021 假面 CR/CD 未进 L1 buffs")
    else:
        for b in mask_buffs:
            if float(getattr(b, "duration_turns", 0.0) or 0.0) >= 99:
                missing.append("23021 假面仍使用 duration≥99 近似")
                break
            if str(getattr(b, "coverage_mode", "action")) != "sp_blaze":
                missing.append("23021 假面未使用 sp_blaze 覆盖（仍为 basic×8？）")
                break

    # Counter / resource flow consume .
    dream_counters = [c for c in counters if "nihility" in str(getattr(c, "name", ""))]
    if not dream_counters:
        missing.append("残梦计数器未进 L1 counters")
    else:
        preds = {str(getattr(r, "predicate", "")) for c in dream_counters for r in c.rules}
        if "acheron_e2_turn_start" not in preds:
            missing.append("残梦流量：缺少 acheron_e2_turn_start（E2 回合开始 +1）")
        r1_rules = [
            r
            for c in dream_counters
            for r in c.rules
            if str(getattr(r, "predicate", "")) == "acheron_r2_debuff_on_skill_cast"
        ]
        if not r1_rules:
            missing.append("残梦流量：缺少 acheron_r2（R1）规则")
        else:
            basic_fire = float(
                ((r1_rules[0].fires or {}).get("acheron") or {}).get("basic", 0.0)
            )
            if basic_fire <= 0.0:
                missing.append("残梦流量：泡影经 R1 未写入 acheron.basic fires")

    if not any("L1_CONSUMES:ashen_roast_dot" in n for n in input_notes):
        if not list(getattr(l1_input, "dot_streams", None) or []):
            missing.append("椒丘烬煨 DoT 未进 L1 dot_streams")

    if not any("L1_CONSUMES:relic_pioneer_117" in n for n in input_notes):
        missing.append("遗器 Pioneer 117 (b) 未进 L1")
    if not any("L1_CONSUMES:relic_sacerdos_121" in n for n in input_notes):
        missing.append("遗器 Sacerdos 121 (b) 未进 L1")
    if not any("L1_CONSUMES:relic_prisoner_116" in n for n in input_notes):
        missing.append("遗器 Prisoner 116 (b) 未进 L1")
    if not any("L1_CONSUMES:relic_broken_keel_310" in n for n in input_notes):
        missing.append("遗器 Broken Keel 310 (b) 未进 L1")
    if not any("L1_CONSUMES:relic_knight_103" in n for n in input_notes):
        missing.append("遗器 Knight 103 (c) 未登记消费")
    if not any("L1_CONSUMES:jiaoqiu_zone_ult_vuln" in n for n in input_notes):
        missing.append("结界终结技易伤未进 L1")

    if missing:
        raise ValueError(
            "assert_l1_consumes_paper_config failed: " + "; ".join(missing)
        )
    return notes


def config_completeness(
    *,
    team_id: str | None = None,
    eidolons: Mapping[str, int] | None = None,
    loadout: Mapping[str, Any] | None = None,
    characters: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Return whether a declared config is fully wired for numeric claims.

    Paper team ``acheron_direct`` is complete when PAPER_CONFIG_GAPS is empty
    and ``assert_config_wired`` passes (in-range eidolons + wired LC).
    """
    if team_id == "acheron_direct" and loadout is None:
        from hsrsim.loadout.benchmark import load_loadout_config

        loadout = load_loadout_config("acheron_direct") or {}
    if team_id == "acheron_direct" and eidolons is None:
        eidolons = {
            "acheron": 2,
            "jiaoqiu": 0,
            "sparkle": 0,
            "aventurine": 0,
        }
    if team_id and team_id.startswith("acheron_old_pela") and loadout is None:
        from hsrsim.loadout.benchmark import load_loadout_config

        loadout = load_loadout_config(team_id) or {}
    if team_id == "acheron_jq_fx" and loadout is None:
        from hsrsim.loadout.benchmark import load_loadout_config

        loadout = load_loadout_config(team_id) or {}
    wiring_notes = assert_config_wired(
        eidolons=eidolons,
        characters=characters,
        loadout=loadout,
    )
    gaps = list(PAPER_CONFIG_GAPS)
    if team_id:
        for g in TEAM_CONFIG_GAPS.get(team_id, ()):
            if g not in gaps:
                gaps.append(g)
    # Deduplicate wiring partial notes into gaps.
    for n in wiring_notes:
        if n not in gaps:
            gaps.append(n)
    return {
        "complete": False if gaps else True,
        "label": "配置不完整" if gaps else "配置完整",
        "team_id": team_id,
        "gaps": gaps,
        "wiring_notes": wiring_notes,
        "root_cause_enemy_attack": "",
        "e1_1i": {
            "enemy_ally_hit": True,
            "aventurine_talent_fua": True,
            "lc_23023_conditional": True,
            "unnerved": True,
        },
    }
