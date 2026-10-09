"""Fribbels ↔ L2 external anchor for Acheron (same conditions, zone-by-zone).

Two modes
---------
1. **solo**: L2 closes all teammate effects (Acheron-only scenario, paper loadout,
   standard dummy). Fribbels uses Acheron.ts + LC 23024 + wearer sets with
   *no* teammate conditionals.
2. **team_full**: Fribbels fills paper supports (Jiaoqiu / Sparkle / Aventurine +
   LCs) with all-on switches; L2 applies the same full-coverage buff package
   before measuring a single skill / ult.

Acceptance: relative |L2−Fribbels|/Fribbels ≤ 3% on single-action damage under
matched conditions. Zones Fribbels does not model are listed separately and
excluded from the gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from hsrsim.enemies.benchmark_dummy import DUMMY_LEVEL, build_benchmark_dummy
from hsrsim.loadout.relic_conditionals import (
    KEEL_TEAM_CD,
    PIONEER_2PC_DMG,
    PIONEER_4PC_CD_AT_3,
    SACERDOS_4PC_CD,
    SACERDOS_4PC_MAX_STACKS,
)
from hsrsim.rules.wiring import ACHERON_E1_CRIT_RATE, abyss_multiplier, lc_param_list
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.combat_rules import CombatRules
from hsrsim.simulator.damage_zones import DamageContext, compute_damage
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.triggers import (
    ASHEN_ROAST_ID,
    AVENTURINE_UNNERVED_ID,
    LC_MIRAGE_ID,
    SLASHED_DREAM_ID,
    SPARKLE_FIGMENT_ID,
    crimson_knot_template,
)
from hsrsim.simulator.types import (
    DamageType,
    Effect,
    EffectModifier,
    EffectTarget,
    Element,
    Scenario,
)
from hsrsim.teams.loader import load_team_spec

REL_GATE = 0.03

FRIBBELS_SKILL_MV = 1.60
FRIBBELS_ULT_RAIN = 0.24
FRIBBELS_ULT_KNOT = 0.15
FRIBBELS_ULT_RESURGE = 1.20
FRIBBELS_THUNDER_HIT = 0.25
FRIBBELS_TALENT_RES_PEN = 0.20

# Sparkle talent L10 + ult Cipher (E1.1d): per-stack aura vuln.
SPARKLE_FIGMENT_PER_STACK = 0.04
SPARKLE_CIPHER_PER_STACK = 0.06
SPARKLE_FIGMENT_MAX_STACKS = 3
SPARKLE_CIPHER_EFFECT_ID = "sparkle_enemy_vuln"

ZONE_BUCKETS = (
    ("f_base", "基础"),
    ("f_origMult", "其他·奈落(original_mult)"),
    ("f_dmgBoost", "增伤"),
    ("f_crit", "暴击"),
    ("f_def", "防御"),
    ("f_res", "抗性"),
    ("f_vuln", "易伤"),
    ("f_weaken", "其他·虚弱"),
    ("f_mit", "其他·减伤层"),
    ("f_broken", "其他·韧性未破"),
)

# Multiplicative zones gated at ≤3%. f_base excluded when L2 is multi-hit
# (Fribbels folds MV into one scaling; L2 emits per-segment bases).
GATED_ZONES = frozenset(
    {
        "f_origMult",
        "f_dmgBoost",
        "f_crit",
        "f_def",
        "f_res",
        "f_vuln",
        "f_weaken",
        "f_mit",
        "f_broken",
    }
)


FRIBBELS_UNMODELED: tuple[dict[str, str], ...] = (
    {
        "id": "zone_proc_dream",
        "name": "结界叠烬→残梦",
        "note": "Fribbels 无 AV/结界时序；单次伤害对照不涉及残梦流量",
    },
    {
        "id": "quad_ascendance",
        "name": "四相断我溢出缓冲",
        "note": "Fribbels 无残梦溢出状态机",
    },
    {
        "id": "mirage_hit_timing",
        "name": "泡影同次命中时序",
        "note": "Fribbels emptyBubblesDebuff 为静态开关；对照时 L2 预挂泡影对齐",
    },
    {
        "id": "lc23029_fribbels_double_add",
        "name": "23029 Fribbels UI 卸甲+穷寇双开可加总",
        "note": (
            "ThoseManySprings.ts 双开时 +0.10+0.14；原文为升级替换。"
            "本锚点按穷寇 ParamList[5]=0.14（与 L2/L1 一致），不跟 UI 双加"
        ),
    },
)


@dataclass
class ZoneRow:
    zone_id: str
    label: str
    l2: float | None
    fribbels: float | None
    rel_abs: float | None
    fribbels_unmodeled: bool = False
    note: str = ""


@dataclass
class ActionCompare:
    action: str
    mv: float
    l2_damage: float
    fribbels_damage: float
    rel_abs: float
    pass_gate: bool
    zones: list[ZoneRow] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    panel: dict[str, float] = field(default_factory=dict)
    conditionals: dict[str, Any] = field(default_factory=dict)


def fribbels_ult_mv(*, crimson_knot_stacks: int = 9, thunder_hits: int = 6) -> float:
    s = int(crimson_knot_stacks)
    return (
        3 * FRIBBELS_ULT_RAIN
        + 3 * FRIBBELS_ULT_KNOT
        + FRIBBELS_ULT_KNOT * s
        + FRIBBELS_ULT_RESURGE
        + int(thunder_hits) * FRIBBELS_THUNDER_HIT
    )


def _paper_chars() -> dict[str, Any]:
    spec = load_team_spec("acheron_direct")
    return {c.id: c.model_copy(deep=True) for c in spec.scenario.allies}


def _panel(ach) -> dict[str, float]:
    st = ach.build.stats
    return {
        "atk": float(st.atk),
        "crit_rate": float(st.crit_rate),
        "crit_dmg": float(st.crit_dmg),
        "lightning_dmg": float(st.dmg_boost.get(Element.LIGHTNING, 0.0) or 0.0),
        "level": int(getattr(ach, "level", 80) or 80),
    }


def _sparkle_skill_cd(sparkle) -> float:
    cd = float(sparkle.build.stats.crit_dmg)
    return 0.45 + 0.24 * cd


def _lc_mirage_params_from_si(si: int) -> tuple[float, float]:
    params = lc_param_list(23024, si)
    return float(params[1]), float(params[2])


def build_fribbels_ctx(
    *,
    panel: dict[str, float],
    mv: float,
    action: str,
    mode: str,
    thunder_stacks: int = 0,
    nihility_others: int = 0,
    eidolon: int = 2,
    empty_bubbles: bool = True,
    mirage_dmg: float = 0.24,
    mirage_ult_extra: float = 0.40,
    pioneer_debuffed: bool = True,
    pioneer_doubled: bool = True,
    e1_debuffed: bool = True,
    izumo_same_path: bool = False,
    ashen_stacks: int = 0,
    zone_ult_vuln: bool = False,
    unarmored_vuln: float = 0.0,
    figment_vuln: float = 0.0,
    sparkle_cd_buff: float = 0.0,
    unnerved_cd: float = 0.0,
    keel_cd: float = 0.0,
    sacerdos_cd: float = 0.0,
    lc_23023_shield_cd: float = 0.0,
    enemy_def: float = 1000.0,
    enemy_level: int = DUMMY_LEVEL,
    broken: bool = False,
) -> tuple[DamageContext, dict[str, Any]]:
    dmg = float(panel["lightning_dmg"])
    if empty_bubbles:
        dmg += float(mirage_dmg)
        if action == "ult":
            dmg += float(mirage_ult_extra)
    if thunder_stacks > 0:
        dmg += 0.30 * int(thunder_stacks)
    if pioneer_debuffed:
        dmg += PIONEER_2PC_DMG

    cr = float(panel["crit_rate"])
    if izumo_same_path:
        cr += 0.12
    if e1_debuffed and eidolon >= 1:
        cr += ACHERON_E1_CRIT_RATE
    cr = min(1.0, cr)

    cd = float(panel["crit_dmg"])
    if pioneer_debuffed:
        cd += PIONEER_4PC_CD_AT_3 * (2.0 if pioneer_doubled else 1.0)
    cd += (
        float(sparkle_cd_buff)
        + float(unnerved_cd)
        + float(keel_cd)
        + float(sacerdos_cd)
        + float(lc_23023_shield_cd)
    )

    res_pen = FRIBBELS_TALENT_RES_PEN if action == "ult" else 0.0

    vuln = 0.0
    if ashen_stacks > 0:
        vuln += 0.15 + max(0, int(ashen_stacks) - 1) * 0.05
    if zone_ult_vuln and action == "ult":
        vuln += 0.15
    vuln += float(unarmored_vuln)
    vuln += float(figment_vuln)

    orig = abyss_multiplier(int(nihility_others), int(eidolon))
    notes = {
        "mode": mode,
        "action": action,
        "mv": mv,
        "thunder_stacks": thunder_stacks,
        "nihility_others": nihility_others,
        "original_mult": orig,
        "dmg_boost": dmg,
        "crit_rate": cr,
        "crit_dmg": cd,
        "res_pen": res_pen,
        "vuln": vuln,
        "empty_bubbles": empty_bubbles,
        "pioneer_debuffed": pioneer_debuffed,
        "izumo_same_path": izumo_same_path,
        "ashen_stacks": ashen_stacks,
        "zone_ult_vuln": bool(zone_ult_vuln and action == "ult"),
        "sparkle_cd_buff": sparkle_cd_buff,
        "unnerved_cd": unnerved_cd,
        "keel_cd": keel_cd,
        "sacerdos_cd": sacerdos_cd,
        "lc_23023_shield_cd": lc_23023_shield_cd,
        "unarmored_vuln": unarmored_vuln,
        "figment_vuln": figment_vuln,
    }
    ctx = DamageContext(
        attacker_level=int(panel["level"]),
        attacker_atk=float(panel["atk"]),
        attacker_crit_rate=cr,
        attacker_crit_dmg=cd,
        attacker_break_effect=0.0,
        attacker_dmg_boost_pct=dmg,
        attacker_res_pen_pct=res_pen,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=0.0,
        defender_level=int(enemy_level),
        defender_def=float(enemy_def),
        defender_res_pct=0.0,
        defender_vuln_pct=vuln,
        defender_mit_layers=[],
        defender_toughness_broken=bool(broken),
        skill_multiplier=float(mv),
        damage_type=DamageType.DIRECT,
        element=Element.LIGHTNING,
        original_mult=float(orig),
    )
    return ctx, notes


def _zone_rows(
    l2_bd: dict[str, float],
    fri_bd: dict[str, float],
    *,
    l2_hit_count: int = 1,
) -> list[ZoneRow]:
    rows: list[ZoneRow] = []
    for zid, label in ZONE_BUCKETS:
        a = l2_bd.get(zid)
        b = fri_bd.get(zid)
        rel = None
        if a is not None and b is not None and abs(b) > 1e-12:
            rel = abs(float(a) - float(b)) / abs(float(b))
        note = ""
        unmodeled = False
        if zid == "f_base" and l2_hit_count > 1:
            note = (
                "L2 多段命中基础伤害加权均值 vs Fribbels 聚合 MV 单次基础；"
                "不计入乘区门槛（总伤已对照）"
            )
            unmodeled = True  # exclude from gate (not Fribbels-missing; aggregation)
        rows.append(
            ZoneRow(
                zone_id=zid,
                label=label,
                l2=None if a is None else float(a),
                fribbels=None if b is None else float(b),
                rel_abs=rel,
                fribbels_unmodeled=unmodeled,
                note=note,
            )
        )
    return rows


def _fail_zones(rows: list[ZoneRow], gate: float = REL_GATE) -> list[str]:
    out = []
    for r in rows:
        if r.fribbels_unmodeled or r.rel_abs is None:
            continue
        if r.zone_id not in GATED_ZONES:
            continue
        if r.rel_abs > gate + 1e-12:
            out.append(
                f"{r.label}({r.zone_id}): L2={r.l2:.4f} Fribbels={r.fribbels:.4f} "
                f"|rel|={r.rel_abs:.4f}"
            )
    return out


def _count_hits(eng: Engine, action_id: str) -> int:
    return sum(
        1
        for ev in eng.events
        if ev.event_type == "damage" and ev.payload.get("action") == action_id
    )


def _apply_mirage(enemy, source_id: str = "acheron") -> None:
    enemy.apply_effect(
        Effect(
            id=LC_MIRAGE_ID,
            name="泡影",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=1,
            max_stacks=1,
            current_stacks=1,
            modifiers=[],
        ),
        source_id=source_id,
    )


def _apply_ashen(enemy, stacks: int = 5, source_id: str = "jiaoqiu", tmpl: Effect | None = None) -> None:
    if tmpl is None:
        # Fallback: talent vuln 0.15 + 0.05×(stacks-1) baked as single vuln modifier.
        vuln = 0.15 + max(0, int(stacks) - 1) * 0.05
        enemy.active_effects = [e for e in enemy.active_effects if e.id != ASHEN_ROAST_ID]
        enemy.apply_effect(
            Effect(
                id=ASHEN_ROAST_ID,
                name="烬煨",
                is_buff=False,
                target=EffectTarget.SINGLE_ENEMY,
                duration_turns=99,
                max_stacks=5,
                current_stacks=int(stacks),
                modifiers=[
                    EffectModifier(target_stat="vuln", operation="add", value=float(vuln))
                ],
            ),
            source_id=source_id,
        )
        return
    enemy.active_effects = [e for e in enemy.active_effects if e.id != ASHEN_ROAST_ID]
    copy = tmpl.model_copy(deep=True)
    enemy.set_effect_stacks(copy, int(stacks), source_id=source_id)


def _ashen_tmpl_from_jiaoqiu(jq) -> Effect | None:
    for eff in jq.build.effects:
        if eff.id == ASHEN_ROAST_ID:
            return eff.model_copy(deep=True)
    return None


def _force_action(eng: Engine, action_id: str) -> None:
    actor = eng.state.find_char("acheron")
    assert actor is not None
    action = next(a for a in actor.char.build.actions if a.id == action_id)
    enemy = eng.state.enemies[0]
    if action.type.value == "ultimate" or action_id.endswith("_ult"):
        eng.execute_inserted_action(actor, action, enemy.char.id)
    else:
        eng.execute_action(actor, action, enemy.char.id)


def _apply_zone_ult_vuln(eng: Engine) -> None:
    from hsrsim.simulator.field_zone import (
        JIAOQIU_ULT_ZONE_ID,
        JIAOQIU_ZONE_DURATION,
        JIAOQIU_ZONE_MAX_TRIGGERS,
        JIAOQIU_ZONE_PROC_CHANCE,
        JIAOQIU_ZONE_ULT_VULN,
        FieldZone,
    )

    eng.state.active_zones = [
        z for z in eng.state.active_zones if z.id != JIAOQIU_ULT_ZONE_ID
    ]
    eng.state.active_zones.append(
        FieldZone(
            id=JIAOQIU_ULT_ZONE_ID,
            source_id="jiaoqiu",
            remaining_turns=JIAOQIU_ZONE_DURATION,
            proc_base_chance=JIAOQIU_ZONE_PROC_CHANCE,
            proc_effect_id=ASHEN_ROAST_ID,
            max_triggers=JIAOQIU_ZONE_MAX_TRIGGERS,
            triggers_left=JIAOQIU_ZONE_MAX_TRIGGERS,
            ult_vuln=float(JIAOQIU_ZONE_ULT_VULN),
        )
    )


def _apply_unnerved(enemy, cd: float = 0.15, source_id: str = "aventurine") -> None:
    enemy.apply_effect(
        Effect(
            id=AVENTURINE_UNNERVED_ID,
            name="惊惶",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=99,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(target_stat="crit_dmg", operation="add", value=float(cd))
            ],
        ),
        source_id=source_id,
    )


def _apply_cd_buff(acheron, effect_id: str, name: str, amount: float, source_id: str) -> None:
    acheron.apply_effect(
        Effect(
            id=effect_id,
            name=name,
            is_buff=True,
            target=EffectTarget.SINGLE_ALLY,
            duration_turns=99,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="crit_dmg", operation="add", value=float(amount)
                )
            ],
        ),
        source_id=source_id,
    )


def _apply_unarmored(
    enemy, vuln: float = 0.15, source_id: str = "jiaoqiu", *, cornered: bool = False
) -> None:
    enemy.apply_effect(
        Effect(
            id="lc_unarmored",
            name="穷寇" if cornered else "卸甲",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=99,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(target_stat="vuln", operation="add", value=float(vuln))
            ],
        ),
        source_id=source_id,
    )


def _apply_sparkle_figment_full(sparkle, stacks: int = SPARKLE_FIGMENT_MAX_STACKS) -> None:
    """Full-coverage: pre-stack Figment on Sparkle (aura read at damage time)."""
    sparkle.active_effects = [
        e for e in sparkle.active_effects if e.id != SPARKLE_FIGMENT_ID
    ]
    sparkle.apply_effect(
        Effect(
            id=SPARKLE_FIGMENT_ID,
            name="幻相",
            is_buff=True,
            target=EffectTarget.SELF,
            duration_turns=99,
            max_stacks=SPARKLE_FIGMENT_MAX_STACKS,
            current_stacks=int(stacks),
            modifiers=[
                EffectModifier(
                    target_stat="vuln",
                    operation="add",
                    value=float(SPARKLE_FIGMENT_PER_STACK),
                )
            ],
        ),
        source_id="sparkle",
    )


def _apply_sparkle_cipher(acheron) -> None:
    """Full-coverage: Cipher on attacker → +0.06 per figment stack."""
    acheron.active_effects = [
        e for e in acheron.active_effects if e.id != SPARKLE_CIPHER_EFFECT_ID
    ]
    acheron.apply_effect(
        Effect(
            id=SPARKLE_CIPHER_EFFECT_ID,
            name="谜诡",
            is_buff=True,
            target=EffectTarget.SINGLE_ALLY,
            duration_turns=99,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="vuln",
                    operation="add",
                    value=float(SPARKLE_CIPHER_PER_STACK),
                )
            ],
        ),
        source_id="sparkle",
    )


def figment_aura_vuln_value(
    *, stacks: int = SPARKLE_FIGMENT_MAX_STACKS, cipher: bool = True
) -> float:
    per = SPARKLE_FIGMENT_PER_STACK + (
        SPARKLE_CIPHER_PER_STACK if cipher else 0.0
    )
    return float(stacks) * per


def vuln_composition(
    *,
    ashen_stacks: int,
    unarmored_vuln: float,
    figment_vuln: float,
    zone_ult_vuln: bool,
    action: str,
) -> dict[str, float]:
    """Additive vuln% parts at a single-action moment (for skill vs ult audit)."""
    ashen = 0.0
    if ashen_stacks > 0:
        ashen = 0.15 + max(0, int(ashen_stacks) - 1) * 0.05
    zone = 0.15 if (zone_ult_vuln and action == "ult") else 0.0
    parts = {
        "ashen_roast": ashen,
        "lc_23029": float(unarmored_vuln),
        "sparkle_figment_aura": float(figment_vuln),
        "jiaoqiu_zone_ult": zone,
    }
    parts["total"] = sum(parts.values())
    parts["f_vuln"] = 1.0 + parts["total"]
    return parts


def _apply_thunder(acheron, stacks: int = 3) -> None:
    acheron.apply_effect(
        Effect(
            id="acheron_thunder_core",
            name="雷心",
            is_buff=True,
            target=EffectTarget.SELF,
            duration_turns=3,
            max_stacks=3,
            current_stacks=int(stacks),
            modifiers=[
                EffectModifier(target_stat="dmg_boost", operation="add", value=0.30)
            ],
        ),
        source_id="acheron",
    )


def _sum_action_damage(eng: Engine, action_id: str) -> tuple[float, dict[str, float]]:
    total = 0.0
    zone_acc: dict[str, float] = {}
    zone_w: dict[str, float] = {}
    for ev in eng.events:
        if ev.event_type != "damage":
            continue
        if ev.payload.get("action") != action_id:
            continue
        dmg = float(ev.payload["raw_damage"])
        total += dmg
        bd = ev.payload.get("zone_breakdown") or {}
        for zid, fac in bd.items():
            zone_acc[zid] = zone_acc.get(zid, 0.0) + float(fac) * dmg
            zone_w[zid] = zone_w.get(zid, 0.0) + dmg
    mean = {
        z: (zone_acc[z] / zone_w[z]) if zone_w.get(z, 0) > 0 else 0.0 for z in zone_acc
    }
    return total, mean


def _solo_engine(ach) -> Engine:
    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    enemy.build.stats.speed = 1.0
    sc = Scenario(
        name="fribbels_anchor_solo",
        allies=[ach],
        enemies=[enemy],
        max_cycles=1,
        toughness_mode="always_unbroken",
    )
    return Engine(
        sc,
        ally_bot=GreedyBot(),
        random_seed=42,
        combat_rules=CombatRules(acheron_ult_e11h=True),
    )


def _team_engine(chars: dict[str, Any]) -> Engine:
    allies = [chars[k] for k in ("acheron", "jiaoqiu", "sparkle", "aventurine")]
    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    enemy.build.stats.speed = 1.0
    sc = Scenario(
        name="fribbels_anchor_team",
        allies=allies,
        enemies=[enemy],
        max_cycles=1,
        toughness_mode="always_unbroken",
    )
    return Engine(
        sc,
        ally_bot=GreedyBot(),
        random_seed=42,
        combat_rules=CombatRules(
            acheron_ult_e11h=True, zone_proc_counts_for_dream=True
        ),
    )


def _action_to_dict(a: ActionCompare) -> dict[str, Any]:
    return {
        "action": a.action,
        "mv": a.mv,
        "l2_damage": a.l2_damage,
        "fribbels_damage": a.fribbels_damage,
        "rel_abs": a.rel_abs,
        "pass_gate": a.pass_gate,
        "fail_reasons": a.fail_reasons,
        "panel": a.panel,
        "conditionals": a.conditionals,
        "zones": [
            {
                "zone_id": z.zone_id,
                "label": z.label,
                "l2": z.l2,
                "fribbels": z.fribbels,
                "rel_abs": z.rel_abs,
                "fribbels_unmodeled": z.fribbels_unmodeled,
                "note": z.note,
            }
            for z in a.zones
        ],
    }


def compare_solo() -> dict[str, Any]:
    chars = _paper_chars()
    ach0 = chars["acheron"]
    panel = _panel(ach0)
    si = int(getattr(ach0, "light_cone_superimposition", 1) or 1)
    mirage_dmg, mirage_ult = _lc_mirage_params_from_si(si)
    results: dict[str, ActionCompare] = {}

    for action, action_id, mv, thunder in (
        ("skill", "acheron_skill", FRIBBELS_SKILL_MV, 0),
        (
            "ult",
            "acheron_ult",
            fribbels_ult_mv(crimson_knot_stacks=9, thunder_hits=6),
            3,
        ),
    ):
        eng = _solo_engine(ach0.model_copy(deep=True))
        actor = eng.state.find_char("acheron")
        enemy = eng.state.enemies[0]
        assert actor is not None
        _apply_mirage(enemy)
        if action == "ult":
            enemy.apply_effect(crimson_knot_template(stacks=9), source_id="acheron")
            actor.variables[SLASHED_DREAM_ID] = 9.0
            _apply_thunder(actor, stacks=3)
        eng.events.clear()
        _force_action(eng, action_id)
        l2_dmg, l2_zones = _sum_action_damage(eng, action_id)

        fri_ctx, cond = build_fribbels_ctx(
            panel=panel,
            mv=mv,
            action=action,
            mode="solo",
            thunder_stacks=thunder,
            nihility_others=0,
            eidolon=int(ach0.eidolon),
            empty_bubbles=True,
            mirage_dmg=mirage_dmg,
            mirage_ult_extra=mirage_ult,
            pioneer_debuffed=True,
            pioneer_doubled=True,
            e1_debuffed=True,
            izumo_same_path=False,
            enemy_def=float(enemy.char.build.stats.defense),
            enemy_level=int(getattr(enemy.char, "level", DUMMY_LEVEL)),
        )
        fri_dmg, fri_zones = compute_damage(fri_ctx)
        rel = abs(l2_dmg - fri_dmg) / fri_dmg if fri_dmg > 1e-9 else None
        hits = _count_hits(eng, action_id)
        zones = _zone_rows(l2_zones, fri_zones, l2_hit_count=hits)
        fails = _fail_zones(zones)
        if rel is not None and rel > REL_GATE:
            fails.insert(0, f"total_damage |rel|={rel:.4f} > {REL_GATE}")
        results[action] = ActionCompare(
            action=action,
            mv=mv,
            l2_damage=l2_dmg,
            fribbels_damage=float(fri_dmg),
            rel_abs=float(rel) if rel is not None else 0.0,
            pass_gate=bool(rel is not None and rel <= REL_GATE and not fails),
            zones=zones,
            fail_reasons=fails,
            panel=panel,
            conditionals={**cond, "l2_hit_count": hits},
        )

    return {
        "mode": "solo",
        "description": "L2 关闭全部队友效果；Fribbels 无队辅条件",
        "gate": REL_GATE,
        "actions": {k: _action_to_dict(v) for k, v in results.items()},
        "all_pass": all(v.pass_gate for v in results.values()),
        "fribbels_unmodeled": list(FRIBBELS_UNMODELED),
    }


def compare_team_full_coverage() -> dict[str, Any]:
    chars = _paper_chars()
    ach0 = chars["acheron"]
    sparkle = chars["sparkle"]
    jq = chars["jiaoqiu"]
    aven = chars["aventurine"]
    panel = _panel(ach0)
    si = int(getattr(ach0, "light_cone_superimposition", 1) or 1)
    mirage_dmg, mirage_ult = _lc_mirage_params_from_si(si)
    sparkle_cd = _sparkle_skill_cd(sparkle)
    unnerved = 0.15
    jq_si = int(getattr(jq, "light_cone_superimposition", 1) or 1)
    params29 = lc_param_list(23029, jq_si)
    # Ashen DoT present → Cornered ParamList[5] (replace Unarmored ParamList[2]).
    unarmored = float(params29[5]) if len(params29) > 5 else float(params29[2])
    figment_vuln = figment_aura_vuln_value(
        stacks=SPARKLE_FIGMENT_MAX_STACKS, cipher=True
    )
    keel = 2 * KEEL_TEAM_CD
    sacerdos = SACERDOS_4PC_CD * SACERDOS_4PC_MAX_STACKS
    aven_si = int(getattr(aven, "light_cone_superimposition", 1) or 1)
    lc23 = lc_param_list(23023, aven_si)
    shield_cd = float(lc23[1]) if len(lc23) > 1 else 0.40

    results: dict[str, ActionCompare] = {}
    vuln_comps: dict[str, dict[str, float]] = {}
    for action, action_id, mv, thunder in (
        ("skill", "acheron_skill", FRIBBELS_SKILL_MV, 0),
        (
            "ult",
            "acheron_ult",
            fribbels_ult_mv(crimson_knot_stacks=9, thunder_hits=6),
            3,
        ),
    ):
        eng = _team_engine({k: v.model_copy(deep=True) for k, v in chars.items()})
        actor = eng.state.find_char("acheron")
        sparkle_st = eng.state.find_char("sparkle")
        enemy = eng.state.enemies[0]
        assert actor is not None and sparkle_st is not None
        _apply_mirage(enemy)
        _apply_ashen(enemy, stacks=5, tmpl=_ashen_tmpl_from_jiaoqiu(jq))
        _apply_unnerved(enemy, cd=unnerved)
        _apply_unarmored(enemy, vuln=unarmored, cornered=True)
        _apply_sparkle_figment_full(sparkle_st)
        _apply_sparkle_cipher(actor)
        _apply_cd_buff(actor, "sparkle_skill_buff", "花火战技暴伤", sparkle_cd, "sparkle")
        _apply_cd_buff(actor, "relic_keel_team_cd", "Keel Team CD", keel, "sparkle")
        _apply_cd_buff(
            actor, "relic_sacerdos_4pc_cd", "Sacerdos 4pc", sacerdos, "sparkle"
        )
        _apply_cd_buff(
            actor, "lc_23023_shield_cd", "砂金光锥盾暴伤", shield_cd, "aventurine"
        )
        if action == "ult":
            enemy.apply_effect(crimson_knot_template(stacks=9), source_id="acheron")
            actor.variables[SLASHED_DREAM_ID] = 9.0
            _apply_thunder(actor, stacks=3)
            _apply_zone_ult_vuln(eng)
        vuln_comps[action] = vuln_composition(
            ashen_stacks=5,
            unarmored_vuln=unarmored,
            figment_vuln=figment_vuln,
            zone_ult_vuln=(action == "ult"),
            action=action,
        )
        eng.events.clear()
        _force_action(eng, action_id)
        l2_dmg, l2_zones = _sum_action_damage(eng, action_id)

        fri_ctx, cond = build_fribbels_ctx(
            panel=panel,
            mv=mv,
            action=action,
            mode="team_full",
            thunder_stacks=thunder,
            nihility_others=1,
            eidolon=int(ach0.eidolon),
            empty_bubbles=True,
            mirage_dmg=mirage_dmg,
            mirage_ult_extra=mirage_ult,
            pioneer_debuffed=True,
            pioneer_doubled=True,
            e1_debuffed=True,
            izumo_same_path=True,
            ashen_stacks=5,
            zone_ult_vuln=(action == "ult"),
            unarmored_vuln=unarmored,
            figment_vuln=figment_vuln,
            sparkle_cd_buff=sparkle_cd,
            unnerved_cd=unnerved,
            keel_cd=keel,
            sacerdos_cd=sacerdos,
            lc_23023_shield_cd=shield_cd,
            enemy_def=float(enemy.char.build.stats.defense),
            enemy_level=int(getattr(enemy.char, "level", DUMMY_LEVEL)),
        )
        fri_dmg, fri_zones = compute_damage(fri_ctx)
        rel = abs(l2_dmg - fri_dmg) / fri_dmg if fri_dmg > 1e-9 else None
        hits = _count_hits(eng, action_id)
        zones = _zone_rows(l2_zones, fri_zones, l2_hit_count=hits)
        fails = _fail_zones(zones)
        if rel is not None and rel > REL_GATE:
            fails.insert(0, f"total_damage |rel|={rel:.4f} > {REL_GATE}")
        results[action] = ActionCompare(
            action=action,
            mv=mv,
            l2_damage=l2_dmg,
            fribbels_damage=float(fri_dmg),
            rel_abs=float(rel) if rel is not None else 0.0,
            pass_gate=bool(rel is not None and rel <= REL_GATE and not fails),
            zones=zones,
            fail_reasons=fails,
            panel=panel,
            conditionals={**cond, "l2_hit_count": hits},
        )

    skill_v = vuln_comps["skill"]["f_vuln"]
    ult_v = vuln_comps["ult"]["f_vuln"]
    return {
        "mode": "team_full_coverage",
        "description": (
            "Fribbels 填入椒丘/花火/砂金+光锥，条件全开（含幻相满层+谜诡、穷寇）；"
            "L2 同配装预挂全覆盖增益后测单次战技/终结技"
        ),
        "gate": REL_GATE,
        "actions": {k: _action_to_dict(v) for k, v in results.items()},
        "all_pass": all(v.pass_gate for v in results.values()),
        "vuln_composition": vuln_comps,
        "vuln_skill_vs_ult": {
            "skill_f_vuln": skill_v,
            "ult_f_vuln": ult_v,
            "delta": ult_v - skill_v,
            "expected_zone_ult": 0.15,
            "delta_matches_zone": abs((ult_v - skill_v) - 0.15) < 1e-9,
            "cause_named": (
                "战技/终结技易伤差应=结界终结技专属 15%；"
                "旧差 0.11 因战技耗点叠 1 层幻相(+4%)而终结技未预挂。"
                "现两端均预挂幻相满层+谜诡，差=0.15。"
            ),
        },
        "fribbels_unmodeled": list(FRIBBELS_UNMODELED),
    }


def run_external_anchor() -> dict[str, Any]:
    solo = compare_solo()
    team = compare_team_full_coverage()
    return {
        "gate": REL_GATE,
        "solo": solo,
        "team_full_coverage": team,
        "exit_pass": bool(solo["all_pass"] and team["all_pass"]),
        "fribbels_unmodeled": list(FRIBBELS_UNMODELED),
    }
