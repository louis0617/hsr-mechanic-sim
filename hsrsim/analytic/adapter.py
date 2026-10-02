"""Map rebuilt character JSON and a BenchmarkLoadout panel into L1 inputs.

Does not solve the LP and does not call the simulator. Missing UNKNOWN
arguments raise. Numbers that are not in the data are not filled in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from hsrsim.analytic.coverage import (
    COVERAGE_INDEPENDENCE_GAP,
    ActionAdvance,
    ActionBuff,
    DotStream,
    EnemyStackVuln,
)
from hsrsim.analytic.flow_model import (
    CounterResource,
    FlowCharacter,
    GainRule,
)
from hsrsim.enemies.benchmark_dummy import element_resistance
from hsrsim.catalog.loader import CHARACTERS_DIR, load_character
from hsrsim.loadout.benchmark import apply_team_benchmark, load_loadout_config
from hsrsim.simulator.damage_zones import DamageContext, compute_damage
from hsrsim.simulator.types import (
    Action,
    ActionType,
    Character,
    DamageType,
    Effect,
    Element,
    Stats,
)

#removed fictional weakness RES PEN (+0.20). Weakness → RES=0 via
# element_res table only. Additive RES zone must not invent constants.


def _elem_key(value: object) -> str:
    return value.value if isinstance(value, Element) else str(value)

#  (DATAMINE_ENERGY_SP.md): skills with no SPBase. Acheron is the one
# in this team. Others are not in the exception tables, so the verified
# mode is basic 20 / skill 30 / ult 5. The character JSON does not store
# SPBase; AvatarSkillConfig.json is not in the repo.
_SPBASE_ABSENT = frozenset({130801, 130802, 130803, 130804})
_SPBASE_MODE = {
    ActionType.BASIC_ATTACK: 20.0,
    ActionType.SKILL: 30.0,
    ActionType.ULTIMATE: 5.0,
}

_ACTION_KEY = {
    ActionType.BASIC_ATTACK: "basic",
    ActionType.SKILL: "skill",
    ActionType.ULTIMATE: "ult",
}

_STACK_VULN_EFFECT_IDS = frozenset({"sparkle_figment", "sparkle_enemy_vuln"})

_REPO = Path(__file__).resolve().parents[2]
_TEAMS = _REPO / "data" / "hsr" / "teams"


@dataclass
class CharacterRow:
    """One ally after mapping. ``err`` stays None when the panel has no ERR."""

    id: str
    speed_json: float
    speed_panel: float
    basic_damage: float
    skill_damage: float
    ult_damage: float
    sp_add: float
    sp_need: float
    sp_ult: float
    energy_basic: float
    energy_skill: float
    energy_ult: float
    energy_cost: float
    err: float | None
    uses_counter: bool
    counter_cap: float | None


@dataclass
class L1Input:
    """B1–B4 structures plus the gaps the adapter refused to invent."""

    known_gaps: list[str]
    inconsistencies: list[str]
    rows: list[CharacterRow]
    buffs: list[ActionBuff]
    advances: list[ActionAdvance]
    counters: list[CounterResource]
    characters: list[FlowCharacter] | None
    energy_hit: float
    hits_per_100_av: float
    enemy: Character | None = None
    stack_vulns: list[EnemyStackVuln] = field(default_factory=list)
    action_contexts: dict[str, dict[str, list[DamageContext]]] = field(default_factory=dict)
    model_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dot_streams: list[DotStream] = field(default_factory=list)


def build_l1_input(
    team_id: str,
    *,
    energy_hit: float,
    hits_per_100_av: float,
    err_by_id: Mapping[str, float] | None = None,
    enemy: Character | None = None,
    broken_uptime: float = 0.0,
    figment_counts_for_dream: bool | None = None,
    mirage_same_hit: bool = False,
    acheron_ult_e11h: bool = True,
    acheron_ult_s_hist: Mapping[int, float] | None = None,
    acheron_thunder_mean_stacks: float | None = None,
) -> L1Input:
    """Build the L1 input for one team.

    ``energy_hit`` and ``hits_per_100_av`` have no default: the datamine
    has no ally hit-energy field (). Pass them, including zeros.

    Enemy defense / speed / toughness / weaknesses / ``element_res`` come from
    the same scenario enemy L2 uses (``load_team_spec`` → benchmark dummy).
    Do not pass separate defender_* floats.

    ``broken_uptime`` is the L2-measured fraction of AV spent with the enemy
    toughness-broken under the same scenario (); enters ``f_broken`` as
    ``0.9 + 0.1 × uptime``. Default 0 = always unbroken.

    ERR is read from the loadout yaml field ``err``. A missing key raises;
    this function does not substitute a value. ``err_by_id`` may override
    the yaml, but every member must be present in that mapping.

    ``figment_counts_for_dream`` is ignored ( archived); kept for call-site
    compatibility with pre-holder-fix scripts.

    ``acheron_ult_s_hist``: L2-measured P(S) at ult open (crimson knot stacks).
    Used to expect erase-AoE MVs and thunder stacks. If omitted while e11h is
    on, defaults to {9: 1.0} and notes the fallback.
    ``acheron_thunder_mean_stacks``: optional override for thunder ActionBuff
    dmg_boost (= stacks × 0.30); else derived from ``s_hist``.
    """
    _ = figment_counts_for_dream
    if energy_hit is None or hits_per_100_av is None:
        raise ValueError("energy_hit and hits_per_100_av must be passed explicitly")
    broken_u = max(0.0, min(1.0, float(broken_uptime)))
    from hsrsim.teams.loader import load_team_spec

    team = _load_team(team_id)
    members = list(team["members"])
    eidolons = _require_eidolons(team, members)
    main_dps = team.get("main_dps")
    if main_dps is not None and main_dps not in members:
        raise ValueError(f"main_dps {main_dps} is not a team member")

    if enemy is None:
        enemy = load_team_spec(team_id).scenario.enemies[0]
    weaknesses = [_elem_key(w) for w in enemy.weaknesses]
    enemy_speed = float(enemy.build.stats.speed)

    loaded: list[Character] = []
    raw_metas: dict[str, dict] = {}
    base_speed: dict[str, float] = {}
    raw_actions: dict[str, list[dict]] = {}
    for cid in members:
        path = _character_path(cid)
        raw = json.loads(path.read_text(encoding="utf-8"))
        char = load_character(path)
        loaded.append(char)
        raw_metas[cid] = raw.get("_meta") or {}
        if not isinstance(raw_metas[cid].get("enhanced"), bool):
            raise ValueError(f"{cid} _meta.enhanced is required; refusing to default a version")
        base_speed[cid] = float(char.build.stats.speed)
        raw_actions[cid] = list(raw["build"]["actions"])

    panel_chars, _reports = apply_team_benchmark(
        loaded, team_id, raw_metas=raw_metas
    )
    #stamp eidolon + assert wiring (LC stamped in apply_benchmark).
    stamped: list[Character] = []
    for c in panel_chars:
        stamped.append(c.model_copy(update={"eidolon": int(eidolons[c.id])}))
    panel_chars = stamped
    by_id = {c.id: c for c in panel_chars}

    from hsrsim.rules.wiring import assert_config_wired, config_completeness

    wiring_notes = assert_config_wired(
        eidolons=eidolons,
        characters=panel_chars,
        loadout=load_loadout_config(team_id),
    )
    completeness = config_completeness(
        team_id=team_id,
        eidolons=eidolons,
        characters=panel_chars,
        loadout=load_loadout_config(team_id),
    )

    gaps = _base_gaps(eidolons)
    gaps.extend(wiring_notes)
    for cid in members:
        for item in raw_metas[cid].get("schema_gaps") or []:
            note = item.get("note")
            if note:
                gaps.append(str(note))
    inconsistencies: list[str] = []
    model_notes: list[str] = []
    notes: list[str] = [
        f"【{completeness['label']}】complete={completeness['complete']}；"
        f"gaps={len(completeness['gaps'])}。",
        "角色 JSON 只有单一 sp_cost，没有 BPNeed/BPAdd。"
        "按已确认的符号约定转换：正数是消耗，负数是回复。"
        "花火终结技的负 sp_cost 记入 sp_ult，随终结技频率变化，不进 sp_other。",
        "敌人防御/速度/韧性/弱点/各属性抗性取自场景敌人对象（与 L2 同一 benchmark dummy）。",
    ]
    err_from_loadout = _err_from_loadout(team_id, members)
    notes.append(
        "ERR 取自配装显式字段 err，来源：无 ERR 主词条，取基础值。代码不默认这个数。"
        + " 本次 "
        + ", ".join(f"{cid}={err_from_loadout[cid]}" for cid in members)
        + "。"
    )

    rows: list[CharacterRow] = []
    buffs: list[ActionBuff] = []
    advances: list[ActionAdvance] = []
    counters: list[CounterResource] = []
    stack_vulns: list[EnemyStackVuln] = []
    action_contexts: dict[str, dict[str, list[DamageContext]]] = {}
    flow: list[FlowCharacter] | None = []
    thunder_mean_stacks = 3.0
    s_hist_for_notes: dict[int, float] | None = None
    model_notes.append(
        "descriptions manifest 的 elation 桶混入了 silverwolflv999 等角色，"
        "与期望名单不一致，ignored for this team."
    )

    for cid in members:
        char = by_id[cid]
        actions = _index_actions(char)
        _note_sp_shape(cid, actions, inconsistencies)
        sp_add, sp_need, sp_ult = _sp_coeffs(cid, actions, inconsistencies)
        uses_counter, counter = _counter_resource(
            char,
            panel_chars,
            raw_actions,
            inconsistencies,
            notes,
        )
        if counter is not None:
            counters.append(counter)
        energy = _energy_gains(cid, char, actions, raw_actions[cid], uses_counter, notes)
        if uses_counter:
            energy_cost = 0.0
        else:
            energy_cost = _energy_cost(cid, char, actions, inconsistencies)
        damages = {}
        action_contexts[cid] = {}
        for key in ("basic", "skill", "ult"):
            # Pela E6: extras need a pre-existing defender debuff. Ult applies 通解 after
            # its own hits → same-action E6 ≈ 0; skill/basic expect high tongjie/21015/E4 uptime.
            e6_cov = 0.0
            if cid == "pela":
                e6_cov = 0.85 if key in ("basic", "skill") else 0.0
            damages[key] = _action_damage(
                char,
                actions.get(key),
                weaknesses,
                enemy=enemy,
                vuln=0.0,
                def_reduction=0.0,
                broken_uptime=broken_u,
                allies=panel_chars,
                action_key=key,
                e6_debuff_coverage=e6_cov,
            )
            action_contexts[cid][key] = _action_contexts(
                char,
                actions.get(key),
                weaknesses,
                enemy=enemy,
                broken_uptime=broken_u,
                allies=panel_chars,
                action_key=key,
                e6_debuff_coverage=e6_cov,
            )
        if cid == "acheron" and acheron_ult_e11h:
            s_hist = dict(acheron_ult_s_hist) if acheron_ult_s_hist else {9: 1.0}
            s_hist_for_notes = s_hist
            thunder_mean_stacks = (
                float(acheron_thunder_mean_stacks)
                if acheron_thunder_mean_stacks is not None
                else _expected_thunder_stacks(s_hist)
            )
            extra_ult, extra_notes = _acheron_ult_e11h_extras(
                char,
                weaknesses,
                enemy=enemy,
                broken_uptime=broken_u,
                allies=panel_chars,
                s_hist=s_hist,
                s_hist_is_fallback=acheron_ult_s_hist is None,
            )
            action_contexts[cid]["ult"].extend(extra_ult)
            action_contexts[cid]["ult"] = [
                _with_acheron_ult_e11h_zones(c) for c in action_contexts[cid]["ult"]
            ]
            notes.extend(extra_notes)
        err = err_from_loadout[cid]
        if err_by_id is not None:
            if cid not in err_by_id:
                raise ValueError(f"err_by_id missing {cid}")
            err = float(err_by_id[cid])
        cap = counter.cap if counter is not None else None
        rows.append(
            CharacterRow(
                id=cid,
                speed_json=base_speed[cid],
                speed_panel=float(char.build.stats.speed),
                basic_damage=damages["basic"],
                skill_damage=damages["skill"],
                ult_damage=damages["ult"],
                sp_add=sp_add,
                sp_need=sp_need,
                sp_ult=sp_ult,
                energy_basic=energy["basic"],
                energy_skill=energy["skill"],
                energy_ult=energy["ult"],
                energy_cost=energy_cost,
                err=None if err is None else float(err),
                uses_counter=uses_counter,
                counter_cap=cap,
            )
        )
        if flow is not None:
            if err is None:
                flow = None
            else:
                flow.append(
                    FlowCharacter(
                        id=cid,
                        speed=float(char.build.stats.speed),
                        basic_damage=damages["basic"],
                        skill_damage=damages["skill"],
                        ult_damage=damages["ult"],
                        sp_add=sp_add,
                        sp_need=sp_need,
                        sp_ult=sp_ult,
                        energy_basic=energy["basic"],
                        energy_skill=energy["skill"],
                        energy_ult=energy["ult"],
                        energy_hit=float(energy_hit),
                        energy_cost=energy_cost,
                        err=float(err),
                        hits_per_100_av=float(hits_per_100_av),
                    )
                )
        buffs.extend(
            _buffs_for(
                char,
                panel_chars,
                actions,
                weaknesses,
                main_dps=None if main_dps is None else str(main_dps),
                enemy=enemy,
                inconsistencies=inconsistencies,
                gaps=gaps,
                model_notes=model_notes,
                skip_effect_ids=_STACK_VULN_EFFECT_IDS,
                ashen_steady_stacks=(
                    _prior_ashen_stacks(char, float(enemy_speed))
                    if cid == "jiaoqiu"
                    else None
                ),
            )
        )
        stack = _stack_vuln_for(
            char,
            actions,
            enemy_speed=float(enemy_speed),
            main_dps=None if main_dps is None else str(main_dps),
            inconsistencies=inconsistencies,
        )
        if stack is not None:
            stack_vulns.append(stack)
        advances.extend(
            _advances_for(
                char,
                actions,
                main_dps=None if main_dps is None else str(main_dps),
                inconsistencies=inconsistencies,
            )
        )

    # /g-4: LC(b) ActionBuffs (23024 mirage / 23029 vuln / 23021 mask SP-flow).
    buffs.extend(
        _paper_lc_b_action_buffs(
            panel_chars,
            main_dps=None if main_dps is None else str(main_dps),
            notes=notes,
            enemy_speed=float(enemy_speed),
            sp_by_id={
                r.id: (r.sp_add, r.sp_need, r.sp_ult) for r in rows
            },
            mirage_same_hit=bool(mirage_same_hit),
        )
    )
    # E2: paper relic (b)/(c) conditionals.
    from hsrsim.loadout.relic_conditionals import paper_relic_action_buffs

    relic_buffs, _relic_notes = paper_relic_action_buffs(panel_chars, notes=notes)
    buffs.extend(relic_buffs)
    #zone ult vuln — coverage refined after rates known (force prior = 1).
    from hsrsim.simulator.field_zone import JIAOQIU_ZONE_ULT_VULN

    if any(c.id == "jiaoqiu" for c in panel_chars):
        for ally in panel_chars:
            buffs.append(
                ActionBuff(
                    id=f"jiaoqiu_zone_ult_vuln__{ally.id}",
                    applier_id="jiaoqiu",
                    recipient_id=ally.id,
                    source_action="ult",
                    duration_turns=3.0,
                    vuln=float(JIAOQIU_ZONE_ULT_VULN),
                    applies_to=frozenset({"ult"}),
                )
            )
        notes.append(
            f"L1_CONSUMES:jiaoqiu_zone_ult_vuln={JIAOQIU_ZONE_ULT_VULN} "
            "applies_to=ult；覆盖由椒丘终结技×3 回合； refine 可 force_coverage"
        )
    if acheron_ult_e11h and any(c.id == "acheron" for c in panel_chars):
        thunder_boost = 0.30 * float(thunder_mean_stacks)
        buffs.append(
            ActionBuff(
                id="acheron_thunder_core",
                applier_id="acheron",
                recipient_id="acheron",
                source_action="ult",
                duration_turns=3.0,
                dmg_boost=thunder_boost,
            )
        )
        hist_note = (
            f"s_hist={dict(sorted((s_hist_for_notes or {9: 1.0}).items()))}"
        )
        notes.append(
            f"L1_CONSUMES: 雷心 ActionBuff dmg_boost={thunder_boost:.4f} "
            f"(mean_stacks={thunder_mean_stacks:.3f}×0.30) duration=3 source=ult；"
            f"{hist_note}；天赋减抗只写在 ult 上下文。"
        )
    dot_streams = _jiaoqiu_ashen_dot_streams(
        panel_chars,
        enemy=enemy,
        enemy_speed=float(enemy_speed),
        weaknesses=weaknesses,
        broken_uptime=broken_u,
        notes=notes,
    )

    _note_buff_collisions(buffs, notes)
    notes.append(
        "EnemyStackVuln 期望层数按连续量并入易伤乘区；与 ActionBuff 同乘区位移在 "
        "expected_combo_damage 内相加后枚举。饱和度 = 触发速率 / 达上限所需速率。"
    )
    result = L1Input(
        known_gaps=gaps,
        inconsistencies=inconsistencies,
        rows=rows,
        buffs=buffs,
        advances=advances,
        counters=counters,
        characters=flow,
        energy_hit=float(energy_hit),
        hits_per_100_av=float(hits_per_100_av),
        enemy=enemy,
        stack_vulns=stack_vulns,
        action_contexts=action_contexts,
        notes=notes,
        model_notes=model_notes,
        dot_streams=dot_streams,
    )
    if team_id == "acheron_direct":
        from hsrsim.rules.wiring import assert_l1_consumes_paper_config

        consume_notes = assert_l1_consumes_paper_config(result)
        notes.extend(consume_notes)
        result.notes = notes
    return result


def _err_from_loadout(team_id: str, members: list[str]) -> dict[str, float]:
    cfg = load_loadout_config(team_id)
    if cfg is None:
        raise FileNotFoundError(f"no loadout config for {team_id}")
    characters = cfg.get("characters") or {}
    out: dict[str, float] = {}
    for cid in members:
        block = characters.get(cid)
        if not isinstance(block, dict) or "err" not in block:
            raise ValueError(
                f"{team_id} loadout missing explicit err for {cid}; refusing to default"
            )
        out[cid] = float(block["err"])
    return out


def _load_team(team_id: str) -> dict:
    path = _TEAMS / f"{team_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"team config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _character_path(character_id: str) -> Path:
    candidates = [
        CHARACTERS_DIR / f"{character_id}.json",
        CHARACTERS_DIR / "supports" / f"{character_id}.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"Character not found: {character_id}")


def _require_eidolons(team: dict, members: list[str]) -> dict[str, int]:
    raw = team.get("eidolons")
    if not isinstance(raw, dict):
        raise ValueError("team config missing eidolons; refusing to assume E0")
    out: dict[str, int] = {}
    for cid in members:
        if cid not in raw:
            raise ValueError(f"eidolons missing {cid}")
        value = raw[cid]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
            raise ValueError(f"eidolons.{cid} must be an integer 0–6, got {value!r}")
        out[cid] = value
    return out


def _base_gaps(eidolons: dict[str, int]) -> list[str]:
    from hsrsim.rules.wiring import PAPER_CONFIG_GAPS, config_completeness

    completeness = config_completeness(
        team_id="acheron_direct",
        eidolons=eidolons,
    )
    gaps = [
        f"【{completeness['label']}】论文队结果不得当作完整配置出数；缺口见下。",
        *list(PAPER_CONFIG_GAPS),
        "受击回能在解包中不存在（：UNKNOWN）。只使用调用方传入的 energy_hit 与 hits_per_100_av。"
        "：敌方行动按命途 BaseAggro 加权打 1 个我方目标（0 伤事件）；L1 取期望命中份额。",
        "DoT 未建模。持续伤害乘区不进本次直伤。",
        "角色 JSON 尚未按解包的 BPNeed / BPAdd 拆分，战技点仍由单一 sp_cost 按符号约定转换。",
        COVERAGE_INDEPENDENCE_GAP,
        "解包中存在 10 名角色的加强版数据：魏尔特、卡芙卡、银狼、希儿、刃、镜流、藿藿、花火、黑天鹅、流萤。"
        "未来接入这些角色时必须显式指定 _meta.enhanced。"
        "工具面向新角色上线，老角色加强属同类事件，版本标识是必需字段，代码不默认。",
        "花火大行迹的全队攻击 45%、战技穿透 10%，以及 E1 的攻击 40% 与速度 15%，是条件效果；"
        "等事件触发层（）后再接。",
        "同乘区多条增益（含椒丘易伤与花火幻相期望易伤）经 expected_combo_damage："
        "期望层数作连续加项并入易伤乘区，二值增益按同乘区连通分量枚举；跨乘区连乘。"
        "覆盖率独立假设见上条。",
        "L1 为稳态流量模型，战技点初值摊销为显式输入 "
        "`sp_other = 初始战技点 / (战斗 AV / 100)`（非求解器松弛）。"
        "脉冲式回点（如花火大招一次回 6 点）在池满时的溢出浪费仍不建模，"
        "L1 会略高估可用战技点。",
        " 溢出修正（能量/残梦）：有效消耗 = 名义消耗 + E[X²]/(2·E[X])，"
        "X 为单次获取量、分布由行动速率加权。假设获取事件相互独立、攒满后立即消耗；"
        "战技点不适用（非攒满即消耗）。",
        "覆盖率仍用 min(1, λ)。min(1,λ) 与 1−exp(−λ) 分别对应施加间隔完全规律与完全随机两极端；"
        "花火暴伤（战技占比高、间隔近随机）更吻合指数形式，敌方减益（多源频繁）更吻合 min。"
        "覆盖误差依赖 bot 策略。",
        "击破伤害与属性击破特效不在这两个模型里。击破后行动推迟比例在 L2/解包中无来源（UNKNOWN），"
        "realistic 模式不推迟。击破乘区由 L2 同场景测得的 broken_uptime 显式传入 L1。",
        "幻相经 sp_consumed→敌方 effect_applied 曾被 R1 计入；现用 apply_source 门闩排除。"
        "FIGMENT_COUNTS_FOR_DREAM 工作定义为不计入。"
        "幻相层数挂花火、光环易伤、不发敌方 effect_applied；R1 自然不计。"
        "黄泉终结技 Rainblade 消去集真赤 / 雷心 / 天赋减抗（`CombatRules.acheron_ult_e11h`）。",
        "黄泉 E3–E6 / 椒丘·花火·砂金 E1+ 星魂未接通；声明超过已接通上限时 wiring 门闩拒绝出数。",
        "：砂金追击速率 L1 取期望（敌方行动×命中份额×坚垣筹码覆盖 / 7）；"
        "追击不进 LP 动作空间，残梦外部增益见 CounterResource.external_gain。",
    ]
    _ = eidolons
    return gaps


def _index_actions(char: Character) -> dict[str, Action]:
    out: dict[str, Action] = {}
    for action in char.build.actions:
        key = _ACTION_KEY.get(action.type)
        if key is None:
            continue
        if key in out:
            raise ValueError(f"{char.id} has two {key} actions")
        out[key] = action
    for key in ("basic", "skill", "ult"):
        if key not in out:
            raise ValueError(f"{char.id} missing {key} action")
    return out


def _note_sp_shape(cid: str, actions: dict[str, Action], inconsistencies: list[str]) -> None:
    for key, action in actions.items():
        if not hasattr(action, "sp_cost"):
            continue
    inconsistencies.append(
        f"{cid}：JSON 未拆 BPNeed/BPAdd，三个行动都只有 sp_cost"
        f"（普攻 {actions['basic'].sp_cost}，战技 {actions['skill'].sp_cost}，"
        f"终结技 {actions['ult'].sp_cost}）。"
    )


def _sp_coeffs(
    cid: str,
    actions: dict[str, Action],
    inconsistencies: list[str],
) -> tuple[float, float, float]:
    basic = float(actions["basic"].sp_cost)
    skill = float(actions["skill"].sp_cost)
    ult = float(actions["ult"].sp_cost)
    sp_add = 0.0
    sp_need = 0.0
    if basic < 0:
        sp_add = -basic
    elif basic > 0:
        inconsistencies.append(f"{cid} 普攻 sp_cost={basic} 为正，不能放进 sp_add")
        raise ValueError(f"{cid} basic sp_cost is positive")
    if skill > 0:
        sp_need = skill
    elif skill < 0:
        inconsistencies.append(f"{cid} 战技 sp_cost={skill} 为负，L1 没有战技回复系数")
        raise ValueError(f"{cid} skill sp_cost is negative")
    sp_ult = 0.0 if ult == 0.0 else -ult
    return sp_add, sp_need, sp_ult


def _skill_id(raw_actions: list[dict], action_id: str) -> int | None:
    for raw in raw_actions:
        if raw.get("id") != action_id:
            continue
        level = raw.get("skill_level") or {}
        sid = level.get("skill_id")
        return None if sid is None else int(sid)
    return None


def _energy_gains(
    cid: str,
    char: Character,
    actions: dict[str, Action],
    raw_actions: list[dict],
    uses_counter: bool,
    notes: list[str],
) -> dict[str, float]:
    if uses_counter:
        return {"basic": 0.0, "skill": 0.0, "ult": 0.0}
    gains: dict[str, float] = {}
    for key, action in actions.items():
        sid = _skill_id(raw_actions, action.id)
        if sid in _SPBASE_ABSENT:
            raise ValueError(
                f"{cid} {action.id} skill_id {sid} has no SPBase but the character uses energy"
            )
        atype = action.type
        if atype not in _SPBASE_MODE:
            raise ValueError(f"{cid} {action.id} has no  SPBase mode")
        gains[key] = _SPBASE_MODE[atype]
    notes.append(
        f"{cid} 回能取  众数（普攻 20、战技 30、终结技 5）。"
        "JSON 无 SPBase；AvatarSkillConfig 不在仓库内。该角色不在  例外表。"
    )
    return gains


def _energy_cost(
    cid: str,
    char: Character,
    actions: dict[str, Action],
    inconsistencies: list[str],
) -> float:
    sp_need = float(char.build.stats.energy_max)
    ult_cost = float(actions["ult"].energy_cost)
    if ult_cost != sp_need:
        inconsistencies.append(
            f"{cid} 终结技 energy_cost={ult_cost} 与 energy_max/SPNeed={sp_need} 不一致。"
            "能量上限采用 energy_max。"
        )
    return sp_need


def _counter_resource(
    char: Character,
    allies: list[Character],
    raw_actions: dict[str, list[dict]],
    inconsistencies: list[str],
    notes: list[str],
) -> tuple[bool, CounterResource | None]:
    if float(char.build.stats.energy_max) > 0:
        return False, None
    variable = None
    for var in char.build.variables:
        if var.max_value < float("inf"):
            variable = var
            break
    if variable is None:
        raise ValueError(f"{char.id} energy_max is 0 and no finite variable; refusing to invent energy")
    consume_key = None
    consume_amount = None
    for action in char.build.actions:
        key = _ACTION_KEY.get(action.type)
        if key is None:
            continue
        delta = float(action.variable_changes.get(variable.id, 0.0))
        if delta < 0:
            if consume_key is not None:
                raise ValueError(f"{char.id} has two actions that spend {variable.id}")
            consume_key = key
            consume_amount = -delta
    if consume_key is None or consume_amount is None:
        raise ValueError(f"{char.id} energy_max is 0 but no action spends {variable.id}")
    if consume_amount != float(variable.max_value):
        inconsistencies.append(
            f"{char.id} 计数器消耗 {consume_amount} 与上限 {variable.max_value} 不一致。"
        )
    #gains from shared trigger specs (same source as L2 EventBus), not JSON variable_changes.
    from hsrsim.rules.counter_gains import specs_for_resource, specs_to_l1_gain_rules

    specs = specs_for_resource(variable.id)
    holder_specs = [s for s in specs if s.holder_id == char.id]
    if not holder_specs:
        inconsistencies.append(
            f"{char.id} 计数器 {variable.id} 在 data/hsr/triggers/counter_gains.json 中无获取规则。"
        )
    rules = specs_to_l1_gain_rules(holder_specs, allies)
    notes.append(
        f"{char.id} 残梦/计数器获取规则来自 data/hsr/triggers/counter_gains.json"
        f"（{len(holder_specs)} 条），与 L2 EventBus 共用；不再从 Action.variable_changes 推断。"
    )
    notes.append(
        f"{char.id} 无 SPBase（skill_id 在  缺失表 {_skill_ids(char.id, raw_actions)}），走计数器。"
        f"上限取变量 {variable.id} 的 max_value={variable.max_value}，对应 AvatarConfig.SPNeed。"
    )
    return True, CounterResource(
        name=variable.id,
        holder_id=char.id,
        cap=float(variable.max_value),
        consume={consume_key: consume_amount},
        rules=tuple(rules),
        #四相断我 soft buffer (max 3) after dream hard cap 9.
        overflow_buffer=3.0 if char.id == "acheron" else 0.0,
    )


def _skill_ids(cid: str, raw_actions: dict[str, list[dict]]) -> list[int]:
    out = []
    for raw in raw_actions.get(cid, []):
        sid = (raw.get("skill_level") or {}).get("skill_id")
        if sid is not None:
            out.append(int(sid))
    return out


def _debuff_fires(allies: list[Character]) -> dict[str, dict[str, float]]:
    fires: dict[str, dict[str, float]] = {}
    for ally in allies:
        effects = {e.id: e for e in ally.build.effects}
        per: dict[str, float] = {}
        for action in ally.build.actions:
            key = _ACTION_KEY.get(action.type)
            if key is None:
                continue
            if any(not effects[eid].is_buff for eid in action.applies_effects if eid in effects):
                per[key] = 1.0
        if per:
            fires[ally.id] = per
    return fires


def _own_stack_fires(char: Character, variable_id: str) -> dict[str, float]:
    per: dict[str, float] = {}
    for action in char.build.actions:
        key = _ACTION_KEY.get(action.type)
        if key is None:
            continue
        delta = float(action.variable_changes.get(variable_id, 0.0))
        if delta > 0:
            per[key] = delta
    return per


def _action_damage(
    char: Character,
    action: Action | None,
    weaknesses: list[str],
    *,
    enemy: Character,
    stats: Stats | None = None,
    vuln: float = 0.0,
    def_reduction: float = 0.0,
    broken_uptime: float = 0.0,
    allies: list[Character] | None = None,
    action_key: str | None = None,
    e6_debuff_coverage: float = 0.0,
) -> float:
    if action is None or not action.damage_instances:
        return 0.0
    stats = stats or char.build.stats
    total = 0.0
    cov = max(0.0, min(1.0, float(e6_debuff_coverage)))
    for inst in action.damage_instances:
        # E6 conditional extras: need pre-existing defender debuff coverage.
        if getattr(inst, "requires_defender_debuff", False):
            if cov <= 1e-12:
                continue
            mult = float(inst.multiplier) * cov
        else:
            mult = float(inst.multiplier)
        total += _instance_damage(
            char,
            stats,
            mult,
            inst.element,
            inst.damage_type,
            inst.scaling_stat,
            weaknesses,
            enemy=enemy,
            vuln=vuln,
            def_reduction=def_reduction,
            broken_uptime=broken_uptime,
            allies=allies,
            action_key=action_key,
        )
    return total


def _action_contexts(
    char: Character,
    action: Action | None,
    weaknesses: list[str],
    *,
    enemy: Character,
    stats: Stats | None = None,
    vuln: float = 0.0,
    def_reduction: float = 0.0,
    broken_uptime: float = 0.0,
    allies: list[Character] | None = None,
    action_key: str | None = None,
    e6_debuff_coverage: float = 0.0,
) -> list[DamageContext]:
    """Unbuffed DamageContext list for one action (one entry per instance)."""
    if action is None or not action.damage_instances:
        return []
    stats = stats or char.build.stats
    out: list[DamageContext] = []
    cov = max(0.0, min(1.0, float(e6_debuff_coverage)))
    for inst in action.damage_instances:
        if getattr(inst, "requires_defender_debuff", False):
            # Scale conditional extras by expected pre-existing debuff coverage.
            if cov <= 1e-12:
                continue
            ctx = _instance_context(
                char,
                stats,
                inst.multiplier * cov,
                inst.element,
                inst.damage_type,
                inst.scaling_stat,
                weaknesses,
                enemy=enemy,
                vuln=vuln,
                def_reduction=def_reduction,
                broken_uptime=broken_uptime,
                allies=allies,
                action_key=action_key,
            )
            out.append(ctx)
            continue
        out.append(
            _instance_context(
                char,
                stats,
                inst.multiplier,
                inst.element,
                inst.damage_type,
                inst.scaling_stat,
                weaknesses,
                enemy=enemy,
                vuln=vuln,
                def_reduction=def_reduction,
                broken_uptime=broken_uptime,
                allies=allies,
                action_key=action_key,
            )
        )
    return out


def _erase_mvs_for_s(s: int) -> list[float]:
    """Per-Rainblade erase AoE MVs for opening knot stacks S ()."""
    left = max(0, int(s))
    out: list[float] = []
    for _ in range(3):
        if left <= 0:
            break
        k = min(3, left)
        left -= k
        out.append(min(0.60, 0.15 * (1.0 + float(k))))
    return out


def _thunder_stacks_for_s(s: int) -> int:
    """Stacks gained in one ult: one per Rainblade that hit a knotted target."""
    left = max(0, int(s))
    gained = 0
    for _ in range(3):
        if left <= 0:
            break
        gained += 1
        left -= min(3, left)
    return min(3, gained)


def _expected_thunder_stacks(s_hist: Mapping[int, float]) -> float:
    total = sum(float(w) for w in s_hist.values())
    if total <= 0:
        return 0.0
    return sum(
        float(w) * float(_thunder_stacks_for_s(int(s))) for s, w in s_hist.items()
    ) / total


def _expected_erase_mvs(s_hist: Mapping[int, float]) -> list[float]:
    total = sum(float(w) for w in s_hist.values())
    if total <= 0:
        return []
    acc = [0.0, 0.0, 0.0]
    for s, w in s_hist.items():
        p = float(w) / total
        for i, mv in enumerate(_erase_mvs_for_s(int(s))):
            acc[i] += p * mv
    return [m for m in acc if m > 1e-12]


def _acheron_ult_e11h_extras(
    char: Character,
    weaknesses: list[str],
    *,
    enemy: Character,
    broken_uptime: float,
    allies: list[Character],
    s_hist: Mapping[int, float],
    s_hist_is_fallback: bool,
) -> tuple[list[DamageContext], list[str]]:
    """Erase AoE + Thunder 6×0.25 from S distribution (not fixed S=9)."""
    erase_mvs = _expected_erase_mvs(s_hist)
    extras: list[DamageContext] = []
    for mv in (*erase_mvs, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25):
        extras.append(
            _instance_context(
                char,
                char.build.stats,
                mv,
                Element.LIGHTNING,
                DamageType.DIRECT,
                "atk",
                weaknesses,
                enemy=enemy,
                vuln=0.0,
                def_reduction=0.0,
                broken_uptime=broken_uptime,
                allies=allies,
                action_key="ult",
            )
        )
    fb = "（fallback S=9；请传入 L2 实测 s_hist）" if s_hist_is_fallback else ""
    notes = [
        "L1_CONSUMES: 终结技附加段："
        f"期望消去 MV={ [round(m, 4) for m in erase_mvs] } +6×0.25 雷心；"
        f"s_hist={dict(sorted((int(k), float(v)) for k, v in s_hist.items()))}{fb}；"
        "ult 上下文 +0.20 res_pen（天赋减抗）。"
    ]
    return extras, notes


def _with_acheron_ult_e11h_zones(ctx: DamageContext) -> DamageContext:
    return replace(
        ctx,
        attacker_res_pen_pct=float(ctx.attacker_res_pen_pct) + 0.20,
    )


def _instance_context(
    char: Character,
    stats: Stats,
    multiplier: float,
    element: Element,
    damage_type: DamageType,
    scaling_stat: str,
    weaknesses: list[str],
    *,
    enemy: Character,
    vuln: float,
    def_reduction: float,
    broken_uptime: float = 0.0,
    allies: list[Character] | None = None,
    action_key: str | None = None,
) -> DamageContext:
    if scaling_stat == "defense":
        scaling = float(stats.defense)
    elif scaling_stat == "hp":
        scaling = float(stats.hp_max)
    else:
        scaling = float(stats.atk)
    elem_key = _elem_key(element)
    dmg_boost = _table_get(stats.dmg_boost, "all") + _table_get(stats.dmg_boost, elem_key)
    res_pen = _table_get(stats.res_pen, "all") + _table_get(stats.res_pen, elem_key)
    defender_res = element_resistance(enemy, element)
    crit_rate = float(stats.crit_rate)
    original_mult = 1.0
    #Acheron E1 CR under expected enemy debuffs; Abyss original_mult.
    if char.id == "acheron":
        from hsrsim.rules.wiring import ACHERON_E1_CRIT_RATE, abyss_multiplier

        if int(char.eidolon) >= 1:
            crit_rate = min(1.0, crit_rate + ACHERON_E1_CRIT_RATE)
        nihility = 0
        for ally in allies or []:
            if ally.id == char.id:
                continue
            path = ally.path
            path_s = path.value if hasattr(path, "value") else str(path)
            if path_s == "nihility":
                nihility += 1
        original_mult = abyss_multiplier(nihility, int(char.eidolon))
    #LC 23024 泡影 dmg is an ActionBuff with derived coverage (not baked here).
    return DamageContext(
        attacker_level=int(char.level),
        attacker_atk=scaling,
        attacker_crit_rate=crit_rate,
        attacker_crit_dmg=float(stats.crit_dmg),
        attacker_break_effect=float(stats.break_effect),
        attacker_dmg_boost_pct=dmg_boost,
        attacker_res_pen_pct=res_pen,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=def_reduction,
        defender_level=int(enemy.level),
        defender_def=float(enemy.build.stats.defense),
        defender_res_pct=float(defender_res),
        defender_vuln_pct=vuln,
        defender_mit_layers=[],
        defender_toughness_broken=False,
        defender_broken_uptime=float(broken_uptime),
        skill_multiplier=float(multiplier),
        damage_type=damage_type if isinstance(damage_type, DamageType) else DamageType(damage_type),
        element=element if isinstance(element, Element) else Element(element),
        original_mult=original_mult,
    )


def _instance_damage(
    char: Character,
    stats: Stats,
    multiplier: float,
    element: Element,
    damage_type: DamageType,
    scaling_stat: str,
    weaknesses: list[str],
    *,
    enemy: Character,
    vuln: float,
    def_reduction: float,
    broken_uptime: float = 0.0,
    allies: list[Character] | None = None,
    action_key: str | None = None,
) -> float:
    ctx = _instance_context(
        char,
        stats,
        multiplier,
        element,
        damage_type,
        scaling_stat,
        weaknesses,
        enemy=enemy,
        vuln=vuln,
        def_reduction=def_reduction,
        broken_uptime=broken_uptime,
        allies=allies,
        action_key=action_key,
    )
    value, _zones = compute_damage(ctx)
    return float(value)


def _paper_lc_b_action_buffs(
    panel_chars: list[Character],
    *,
    main_dps: str | None,
    notes: list[str],
    enemy_speed: float,
    sp_by_id: dict[str, tuple[float, float, float]],
    mirage_same_hit: bool = False,
) -> list[ActionBuff]:
    """L1 ActionBuffs for paper-team LC conditionals (23024 / 23029 / 23021)."""
    from hsrsim.rules.wiring import lc_param_list

    out: list[ActionBuff] = []
    by_id = {c.id: c for c in panel_chars}
    recipient = main_dps or "acheron"
    if recipient not in by_id:
        recipient = next(iter(by_id), "acheron")

    # 23024 Mirage: duration 1 enemy turn; per-action hit-weighted coverage .
    ach = by_id.get("acheron")
    if ach is not None and ach.light_cone_id == 23024:
        params = lc_param_list(23024, int(ach.light_cone_superimposition or 1))
        p1, p2 = float(params[1]), float(params[2])
        ult_action = next(
            (a for a in ach.build.actions if _ACTION_KEY.get(a.type) == "ult"),
            None,
        )
        ult_hits = float(
            len(ult_action.damage_instances) if ult_action is not None else 4
        )
        mode = "mirage_same_hit" if mirage_same_hit else "mirage_residual"
        for action, n_hits in (("basic", 1.0), ("skill", 1.0), ("ult", ult_hits)):
            out.append(
                ActionBuff(
                    id=f"lc_mirage__acheron_{action}",
                    applier_id="acheron",
                    recipient_id="acheron",
                    source_action="all",
                    duration_turns=1.0,
                    dmg_boost=p1,
                    enemy_speed=float(enemy_speed),
                    apply_before_damage=bool(mirage_same_hit),
                    coverage_mode=mode,
                    mirage_hits_per_attack=n_hits,
                    applies_to=frozenset({action}),
                )
            )
        out.append(
            ActionBuff(
                id="lc_mirage_ult_extra__acheron",
                applier_id="acheron",
                recipient_id="acheron",
                source_action="all",
                duration_turns=1.0,
                dmg_boost=p2,
                enemy_speed=float(enemy_speed),
                apply_before_damage=bool(mirage_same_hit),
                coverage_mode=mode,
                mirage_hits_per_attack=ult_hits,
                applies_to=frozenset({"ult"}),
            )
        )
        notes.append(
            "L1_CONSUMES:23024 泡影：分动作命中加权；"
            f"coverage_mode={mode}；终结技另 +ParamList[2]。"
        )

    # 23029: Unarmored/Cornered vuln on main DPS damage.
    jq = by_id.get("jiaoqiu")
    if jq is not None and jq.light_cone_id == 23029:
        params = lc_param_list(23029, int(jq.light_cone_superimposition or 1))
        # Expected wearer DoT (烬煨 has dot_instance) → Cornered ParamList[5].
        has_dot_expect = any(
            e.dot_instance is not None for e in jq.build.effects
        )
        vuln = float(params[5]) if has_dot_expect else float(params[2])
        # Enemy debuff: every ally's hits (not only main DPS).
        for ally in panel_chars:
            out.append(
                ActionBuff(
                    id=f"lc_unarmored__{ally.id}",
                    applier_id="jiaoqiu",
                    recipient_id=ally.id,
                    source_action="skill",
                    duration_turns=float(params[3]),
                    vuln=vuln,
                )
            )
        notes.append(
            f"L1_CONSUMES:23029 卸甲/穷寇 vuln={vuln:.2f}→全体攻击者"
            f"（{'穷寇' if has_dot_expect else '卸甲'}；duration={params[3]}；敌方减益）。"
        )

    # 23021 Mask: coverage from wearer SP recovery → 彩焰 / threshold → refresh.
    sp = by_id.get("sparkle")
    if sp is not None and sp.light_cone_id == 23021:
        params = lc_param_list(23021, int(sp.light_cone_superimposition or 1))
        cr = float(params[4])
        cd = float(params[1])
        refresh_dur = float(params[2])  # S1: 4
        thresh = float(params[3])  # S1: 4
        sp_add, _sp_need, sp_ult = sp_by_id.get(sp.id, (1.0, 1.0, 6.0))
        for ally in panel_chars:
            if ally.id == sp.id:
                continue
            out.append(
                ActionBuff(
                    id=f"lc_mask__{ally.id}",
                    applier_id="sparkle",
                    recipient_id=ally.id,
                    source_action="basic",  # unused in sp_blaze mode
                    duration_turns=refresh_dur,
                    crit_rate=cr,
                    crit_dmg=cd,
                    coverage_mode="sp_blaze",
                    sp_gain_basic=float(sp_add),
                    sp_gain_skill=0.0,
                    sp_gain_ult=float(sp_ult),
                    blaze_threshold=thresh,
                )
            )
        notes.append(
            f"L1_CONSUMES:23021 假面 CR={cr:.2f}/CD={cd:.2f}；"
            f"覆盖=SP流量(basic×{sp_add}+ult×{sp_ult})/{thresh}×持续{refresh_dur}"
            "（含花火终结技回点；已删 basic×8）。"
        )

    # 21015 决心·攻陷：佩拉命中施加减防 → 全队攻击吃 def_reduction。
    pela = by_id.get("pela")
    if pela is not None and pela.light_cone_id == 21015:
        params = lc_param_list(21015, int(pela.light_cone_superimposition or 1))
        shred = float(params[1])
        turns = float(params[2])
        for ally in panel_chars:
            out.append(
                ActionBuff(
                    id=f"lc_exposed__{ally.id}",
                    applier_id="pela",
                    recipient_id=ally.id,
                    source_action="basic",
                    duration_turns=turns,
                    def_reduction=shred,
                )
            )
        notes.append(
            f"L1_CONSUMES:21015 攻陷 def_reduction={shred:.2f} duration={turns}；"
            "佩拉 basic/skill/ult 命中刷新；全队攻击者受益"
        )
    return out


def _jiaoqiu_ashen_dot_streams(
    panel_chars: list[Character],
    *,
    enemy: Character,
    enemy_speed: float,
    weaknesses: list[str],
    broken_uptime: float,
    notes: list[str],
) -> list[DotStream]:
    """Ashen Roast DoT: ticks = enemy_turn_rate × coverage (, from )."""
    jq = next((c for c in panel_chars if c.id == "jiaoqiu"), None)
    if jq is None:
        return []
    ashen = next((e for e in jq.build.effects if e.id == "ashen_roast"), None)
    if ashen is None or ashen.dot_instance is None:
        return []
    di = ashen.dot_instance
    element = di.element if isinstance(di.element, Element) else Element(di.element)
    dtype = (
        di.damage_type
        if isinstance(di.damage_type, DamageType)
        else DamageType(di.damage_type)
    )
    ctx = _instance_context(
        jq,
        jq.build.stats,
        float(di.multiplier),
        element,
        dtype,
        getattr(di, "scaling_stat", None) or "atk",
        weaknesses,
        enemy=enemy,
        vuln=0.0,
        def_reduction=0.0,
        broken_uptime=broken_uptime,
        allies=panel_chars,
        action_key=None,
    )
    tick, zones = compute_damage(ctx)
    notes.append(
        f"L1_CONSUMES:ashen_roast_dot MV={di.multiplier} dtype={dtype.value} "
        f"tick_base={tick:.1f} zones={ {k: round(float(v),4) for k,v in zones.items()} }；"
        "跳数=敌方回合速率×烬煨覆盖；易伤另乘期望层。"
    )
    # Apply actions that inflict ashen_roast.
    apply_actions: list[str] = []
    for action in jq.build.actions:
        key = _ACTION_KEY.get(action.type)
        if key is None:
            continue
        if "ashen_roast" in (action.applies_effects or []):
            apply_actions.append(key)
    if not apply_actions:
        apply_actions = ["basic", "skill", "ult"]
    return [
        DotStream(
            id="ashen_roast_dot",
            applier_id="jiaoqiu",
            duration_turns=float(ashen.duration_turns),
            enemy_speed=float(enemy_speed),
            tick_damage=float(tick),
            source_actions=tuple(dict.fromkeys(apply_actions)),
        )
    ]


def _buffs_for(
    char: Character,
    allies: list[Character],
    actions: dict[str, Action],
    weaknesses: list[str],
    *,
    main_dps: str | None,
    enemy: Character,
    inconsistencies: list[str],
    gaps: list[str],
    model_notes: list[str],
    skip_effect_ids: frozenset[str] = frozenset(),
    ashen_steady_stacks: float | None = None,
) -> list[ActionBuff]:
    by_effect = {e.id: e for e in char.build.effects}
    out: list[ActionBuff] = []
    applied: set[str] = set()
    # One ActionBuff per (effect, recipient); multi-action applies (e.g. Ashen on
    # basic/skill/ult) share that buff — steady stacks via ashen_steady_stacks.
    emitted: set[tuple[str, str]] = set()
    for key in ("skill", "ult", "basic"):
        action = actions[key]
        seen_on_action: set[str] = set()
        for eid in action.applies_effects:
            if eid in seen_on_action:
                continue
            seen_on_action.add(eid)
            if eid in skip_effect_ids:
                applied.add(eid)
                continue
            effect = by_effect.get(eid)
            if effect is None:
                inconsistencies.append(f"{char.id} {action.id} 引用了不存在的效果 {eid}")
                continue
            applied.add(eid)
            if effect.conditions:
                gaps.append(f"效果 {eid} 带条件，运行时未实现，未转成增益。")
                continue
            if not _modeled_modifiers(effect):
                gaps.append(f"效果 {eid} 没有已建模的数值修饰，未转成伤害增益。")
                continue
            source = {"basic": "basic", "skill": "skill", "ult": "ult"}[key]
            recipients = _recipients(char, action, effect, allies, main_dps, inconsistencies)
            for recipient in recipients:
                key_er = (eid, recipient.id)
                if key_er in emitted:
                    continue
                emitted.add(key_er)
                steady = ashen_steady_stacks if eid == "ashen_roast" else None
                zone_kwargs, use_zones = _zone_buff_kwargs(
                    effect, recipient, ashen_steady_stacks=steady
                )
                if use_zones:
                    out.append(
                        ActionBuff(
                            id=f"{eid}__{recipient.id}",
                            applier_id=char.id,
                            recipient_id=recipient.id,
                            source_action=source,
                            duration_turns=float(effect.duration_turns),
                            **zone_kwargs,
                        )
                    )
                    continue
                # RES-only shred that didn't map to this recipient (wrong element): skip.
                only_res = all(
                    m.target_stat == "res"
                    or m.target_stat == "dot_dmg_boost"
                    for m in effect.modifiers
                ) and any(m.target_stat == "res" for m in effect.modifiers)
                if only_res:
                    continue
                buffed = _buffed_damages(
                    recipient,
                    effect,
                    weaknesses,
                    enemy=enemy,
                )
                out.append(
                    ActionBuff(
                        id=f"{eid}__{recipient.id}",
                        applier_id=char.id,
                        recipient_id=recipient.id,
                        source_action=source,
                        duration_turns=float(effect.duration_turns),
                        basic_damage=buffed["basic"],
                        skill_damage=buffed["skill"],
                        ult_damage=buffed["ult"],
                    )
                )
    for effect in char.build.effects:
        if effect.id in skip_effect_ids:
            applied.add(effect.id)
            continue
        if effect.id not in applied:
            inconsistencies.append(f"{char.id} 效果 {effect.id} 没有任何行动引用，未转成增益。")
            model_notes.append(
                f"{char.id} 的效果 {effect.id} 没有任何行动的 applies_effects 引用。"
                "效果写在 JSON 里，触发没有接上，未转成增益。"
            )
    return out


def _stack_vuln_for(
    char: Character,
    actions: dict[str, Action],
    *,
    enemy_speed: float,
    main_dps: str | None,
    inconsistencies: list[str],
) -> EnemyStackVuln | None:
    by_effect = {e.id: e for e in char.build.effects}
    figment = by_effect.get("sparkle_figment")
    cipher = by_effect.get("sparkle_enemy_vuln")
    if figment is None and cipher is None:
        return None
    if figment is None or cipher is None:
        raise ValueError(
            f"{char.id} has only one of sparkle_figment / sparkle_enemy_vuln; refusing to invent the other"
        )
    figment_vuln = _single_vuln_modifier(figment)
    cipher_vuln = _single_vuln_modifier(cipher)
    ult = actions["ult"]
    if "sparkle_enemy_vuln" not in ult.applies_effects:
        inconsistencies.append(
            f"{char.id} 谜诡存在，但终结技 applies_effects 未引用 sparkle_enemy_vuln。"
        )
    # Cipher is an ally buff; coverage for damage uses main DPS as recipient when known.
    cipher_recipient = main_dps if main_dps and main_dps != char.id else None
    return EnemyStackVuln(
        id=f"{char.id}_figment_cipher",
        per_stack=figment_vuln,
        max_stacks=float(figment.max_stacks),
        stack_duration_turns=float(figment.duration_turns),
        enemy_speed=float(enemy_speed),
        cipher_applier_id=char.id,
        cipher_source_action="ult",
        cipher_duration_turns=float(cipher.duration_turns),
        cipher_per_stack=cipher_vuln,
        stack_holder_id=char.id,
        cipher_recipient_id=cipher_recipient,
    )


def _single_vuln_modifier(effect: Effect) -> float:
    vulns = [
        float(mod.value)
        for mod in effect.modifiers
        if mod.target_stat == "vuln" and mod.operation == "add"
    ]
    if len(vulns) != 1:
        raise ValueError(f"{effect.id} must have exactly one add vuln modifier, got {vulns}")
    return vulns[0]


def _table_get(table: dict, key: str) -> float:
    if key in table:
        return float(table[key])
    try:
        enum_key = Element(key)
    except ValueError:
        return 0.0
    if enum_key in table:
        return float(table[enum_key])
    return 0.0


def _effect_target_name(effect: Effect, action: Action, applier_id: str, inconsistencies: list[str]) -> str:
    name = effect.target.value if hasattr(effect.target, "value") else str(effect.target)
    action_target = str(action.effect_target)
    if name == action_target:
        return name
    inconsistencies.append(
        f"{applier_id} {action.id} 的 effect_target={action_target} "
        f"与效果 {effect.id} 的 target={name} 不一致。"
    )
    if name == "self" and action_target in ("enemy", "single_enemy", "all_enemies", "single_ally", "all_allies"):
        inconsistencies.append(
            f"{effect.id} 的 target 落在模型默认值 self，改按行动的 effect_target={action_target}。"
        )
        return action_target
    return name


def _modeled_modifiers(effect: Effect) -> bool:
    modeled = {
        "crit_dmg",
        "crit_rate",
        "atk",
        "vuln",
        "def_reduction",
        "weaken",
        "res",  # enemy RES shred → ActionBuff.res_pen
    }
    return any(mod.target_stat in modeled or mod.target_stat.startswith("dmg_boost") for mod in effect.modifiers)


def _zone_buff_kwargs(
    effect: Effect,
    recipient: Character,
    *,
    ashen_steady_stacks: float | None = None,
) -> tuple[dict[str, float], bool]:
    """Map effect modifiers onto ActionBuff zone fields.

    Returns ``(kwargs, use_zones)``. Zone path is used only when every modeled
    modifier lands in a damage zone (no ATK bake). ATK still uses the legacy
    damage-field blend because it changes the base, not a multiplicative zone.
    """
    shifts = {
        "dmg_boost": 0.0,
        "vuln": 0.0,
        "def_reduction": 0.0,
        "crit_rate": 0.0,
        "crit_dmg": 0.0,
        "res_pen": 0.0,
        "weaken": 0.0,
    }
    saw_zone = False
    for mod in effect.modifiers:
        if mod.operation != "add":
            raise ValueError(f"{effect.id} modifier operation {mod.operation} is not mapped")
        stat = mod.target_stat
        if stat == "atk":
            return {}, False
        if stat == "vuln":
            #Ashen Roast uses steady expected stacks when provided.
            if (
                effect.vuln_at_one_stack is not None
                and effect.vuln_per_extra_stack is not None
            ):
                max_s = max(float(effect.max_stacks), 1.0)
                stacks = (
                    max_s
                    if ashen_steady_stacks is None
                    else max(0.0, min(max_s, float(ashen_steady_stacks)))
                )
                if stacks <= 0.0:
                    shifts["vuln"] += 0.0
                else:
                    shifts["vuln"] += float(effect.vuln_at_one_stack) + (
                        stacks - 1.0
                    ) * float(effect.vuln_per_extra_stack)
            else:
                shifts["vuln"] += float(mod.value)
            saw_zone = True
        elif stat == "def_reduction":
            shifts["def_reduction"] += float(mod.value)
            saw_zone = True
        elif stat == "crit_rate":
            shifts["crit_rate"] += float(mod.value)
            saw_zone = True
        elif stat == "crit_dmg":
            shifts["crit_dmg"] += float(mod.value)
            saw_zone = True
        elif stat == "weaken":
            shifts["weaken"] += float(mod.value)
            saw_zone = True
        elif stat == "res":
            # Enemy RES shred → attacker res_pen, but only for matching-element dealers.
            # Pela E4 is ice RES; non-ice attackers (e.g. Acheron lightning) must not gain it.
            elements = {
                (inst.element.value if hasattr(inst.element, "value") else str(inst.element))
                for action in recipient.build.actions
                for inst in action.damage_instances
            }
            eid = str(effect.id)
            need = "ice" if "ice" in eid else None
            if need is not None and need not in elements:
                continue
            shifts["res_pen"] += -float(mod.value)
            saw_zone = True
        elif stat == "dot_dmg_boost":
            continue
        elif stat.startswith("dmg_boost"):
            key = stat.split(".", 1)[1] if "." in stat else "all"
            elements = {
                (inst.element.value if hasattr(inst.element, "value") else str(inst.element))
                for action in recipient.build.actions
                for inst in action.damage_instances
            }
            if key == "all" or key in elements:
                shifts["dmg_boost"] += float(mod.value)
                saw_zone = True
        else:
            return {}, False
    if not saw_zone:
        return {}, False
    return shifts, True


def _recipients(
    applier: Character,
    action: Action,
    effect: Effect,
    allies: list[Character],
    main_dps: str | None,
    inconsistencies: list[str],
) -> list[Character]:
    target = _effect_target_name(effect, action, applier.id, inconsistencies)
    if target in ("single_enemy", "enemy", "all_enemies"):
        return list(allies)
    if target in ("single_ally", "ally"):
        if main_dps is None:
            raise ValueError(f"{effect.id} targets one ally but the team has no main_dps")
        return [next(c for c in allies if c.id == main_dps)]
    if target == "all_allies":
        return list(allies)
    return [applier]


def _buffed_damages(
    recipient: Character,
    effect: Effect,
    weaknesses: list[str],
    *,
    enemy: Character,
) -> dict[str, float]:
    stats = recipient.build.stats.model_copy(deep=True)
    vuln = 0.0
    def_reduction = 0.0
    for mod in effect.modifiers:
        if mod.operation != "add":
            raise ValueError(f"{effect.id} modifier operation {mod.operation} is not mapped")
        if mod.target_stat == "crit_dmg":
            stats.crit_dmg = float(stats.crit_dmg) + float(mod.value)
        elif mod.target_stat == "crit_rate":
            stats.crit_rate = min(1.0, float(stats.crit_rate) + float(mod.value))
        elif mod.target_stat == "atk":
            stats.atk = float(stats.atk) + float(mod.value)
        elif mod.target_stat == "vuln":
            vuln += float(mod.value)
        elif mod.target_stat == "def_reduction":
            def_reduction += float(mod.value)
        elif mod.target_stat == "weaken":
            continue
        elif mod.target_stat.startswith("dmg_boost"):
            key = mod.target_stat.split(".", 1)[1] if "." in mod.target_stat else "all"
            stats.dmg_boost[key] = float(stats.dmg_boost.get(key, 0.0)) + float(mod.value)
        elif mod.target_stat == "dot_dmg_boost":
            continue
    actions = _index_actions(recipient)
    return {
        key: _action_damage(
            recipient,
            actions[key],
            weaknesses,
            enemy=enemy,
            stats=stats,
            vuln=vuln,
            def_reduction=def_reduction,
        )
        for key in ("basic", "skill", "ult")
    }


def _advances_for(
    char: Character,
    actions: dict[str, Action],
    *,
    main_dps: str | None,
    inconsistencies: list[str],
) -> list[ActionAdvance]:
    by_effect = {e.id: e for e in char.build.effects}
    out: list[ActionAdvance] = []
    for key, action in actions.items():
        for eid in action.applies_effects:
            effect = by_effect.get(eid)
            if effect is None or effect.action_advance_pct is None:
                continue
            if effect.action_advance_skip_self or (effect.target or "") in ("single_ally", "ally"):
                if main_dps is None:
                    raise ValueError(f"{effect.id} advances an ally but the team has no main_dps")
                recipient = main_dps
            else:
                recipient = char.id
            if action.effect_target == "self" and recipient != char.id:
                inconsistencies.append(
                    f"{char.id} {action.id} effect_target=self，但 {effect.id} "
                    f"行动提前 {effect.action_advance_pct} 且 skip_self。"
                    f"接受者记为队伍 main_dps={recipient}。"
                )
            out.append(
                ActionAdvance(
                    id=effect.id,
                    applier_id=char.id,
                    recipient_id=recipient,
                    ratio=float(effect.action_advance_pct),
                    source_actions=(_ACTION_KEY[action.type],),
                )
            )
    return out


def _note_buff_collisions(buffs: list[ActionBuff], notes: list[str]) -> None:
    seen: dict[tuple[str, str], str] = {}
    reported: set[tuple[str, str]] = set()
    for buff in buffs:
        for field_name in ("basic_damage", "skill_damage", "ult_damage"):
            if getattr(buff, field_name) is None:
                continue
            key = (buff.recipient_id, field_name)
            prev = seen.get(key)
            if prev is not None and key not in reported:
                notes.append(
                    f"{buff.recipient_id} 的 {field_name} 上有多条增益"
                    f"（{prev} 与 {buff.id}）。不得把各条的满覆盖伤害线性混合；"
                    "求解时对同一行动做 2^n 组合枚举，每组调用 damage_zones。"
                    "覆盖率独立假设见 known_gaps。"
                )
                reported.add(key)
            elif prev is None:
                seen[key] = buff.id


def _prior_ashen_stacks(jq: Character, enemy_speed: float) -> float:
    """Speed-prior steady stacks before rates are known (SupportBot ~ skill)."""
    from hsrsim.analytic.jiaoqiu_zone_l1 import expected_ashen_stacks

    turns = float(jq.build.stats.speed) / 100.0
    rates = {"nb": 0.0, "ns": turns * 0.85, "u": turns * 0.15}
    return float(expected_ashen_stacks(rates, enemy_speed=enemy_speed)["stacks"])


def refine_l1_e13(
    inp: "L1Input",
    rates: Mapping[str, Mapping[str, float]],
    *,
    panel_chars: list[Character] | None = None,
    enemy_speed: float = 95.0,
    enemy_effect_res: float = 0.3,
    zone_proc_counts: bool | None = None,
    fua_dream_external: float = 0.0,
    ult_timing: str | None = None,
    search_coverage: Mapping[str, float] | None = None,
) -> tuple["L1Input", dict]:
    """Recompute ashen / zone cov / zone dream / sparkle-at-ult after rates known."""
    from hsrsim.analytic.jiaoqiu_zone_l1 import (
        expected_ashen_stacks,
        zone_coverage,
        zone_dream_rate,
        zone_proc_hit_chance,
    )
    from hsrsim.rules.counter_gains import zone_proc_counts_for_dream
    from hsrsim.simulator.field_zone import JIAOQIU_ZONE_ULT_VULN
    from hsrsim.simulator.ult_timing import (
        UltTimingStrategy,
        sparkle_buff_coverage_at_action,
    )

    jq_rates = dict(rates.get("jiaoqiu") or {})
    ehr = 0.0
    if panel_chars:
        jq = next((c for c in panel_chars if c.id == "jiaoqiu"), None)
        if jq is not None:
            ehr = float(jq.build.stats.ehr)
    hit = zone_proc_hit_chance(ehr=ehr, enemy_effect_res=enemy_effect_res)
    zcov = zone_coverage(jq_rates)
    counts = zone_proc_counts_for_dream(override=zone_proc_counts)
    zd = zone_dream_rate(
        enemy_speed=enemy_speed,
        zone_cov=zcov,
        hit_chance=hit,
        jq_ult_rate=float(jq_rates.get("u", 0.0)),
        counts_for_dream=counts,
    )
    ash = expected_ashen_stacks(
        jq_rates,
        zone_proc_rate=float(zd["raw"]),
        enemy_speed=enemy_speed,
    )
    meta: dict = {
        "zone_cov": zcov,
        "zone_dream": zd,
        "ashen": ash,
        "hit_chance": hit,
    }

    new_buffs: list[ActionBuff] = []
    for b in inp.buffs or []:
        if str(b.id).startswith("ashen_roast__"):
            new_buffs.append(replace(b, vuln=float(ash["vuln"])))
        elif str(b.id).startswith("jiaoqiu_zone_ult_vuln__"):
            new_buffs.append(
                replace(
                    b,
                    vuln=float(JIAOQIU_ZONE_ULT_VULN),
                    force_coverage=float(zcov),
                )
            )
        elif "sparkle_skill_buff" in str(b.id) and ult_timing is not None:
            from hsrsim.analytic.coverage import _coverage

            lam = float(_coverage(b, {k: dict(v) for k, v in rates.items()}))
            override = dict(search_coverage) if search_coverage else None
            if ult_timing == "search_measured":
                if not override:
                    raise ValueError("search_measured requires search_coverage")
                cov_by_act = {
                    act: sparkle_buff_coverage_at_action(
                        "search_measured",
                        act,
                        lambda_coverage=lam,
                        immediate_by_action=override,
                    )
                    for act in ("basic", "skill", "ult")
                }
            else:
                timing = UltTimingStrategy(ult_timing)
                cov_by_act = {
                    act: sparkle_buff_coverage_at_action(
                        timing,
                        act,
                        lambda_coverage=lam,
                        immediate_by_action=override,
                    )
                    for act in ("basic", "skill", "ult")
                }
            meta["sparkle_coverage_by_action"] = dict(cov_by_act)
            meta["ult_timing"] = ult_timing
            # Split into per-action force_coverage buffs (rest of function unchanged below)
            meta["sparkle_at_ult_cov"] = cov_by_act["ult"]
            meta["sparkle_at_basic_cov"] = cov_by_act["basic"]
            meta["sparkle_at_skill_cov"] = cov_by_act["skill"]
            meta["sparkle_lambda_cov"] = lam
            #per-action strategy coverage (basic/skill/ult), not λ-only.
            for act in ("basic", "skill", "ult"):
                new_buffs.append(
                    replace(
                        b,
                        id=f"{b.id}__{act}",
                        force_coverage=float(cov_by_act[act]),
                        applies_to=frozenset({act}),
                    )
                )
        else:
            new_buffs.append(b)

    ext = float(fua_dream_external) + float(zd["rate"])
    new_counters = []
    for c in inp.counters or []:
        if c.name == "nihility_stacks" and c.holder_id == "acheron":
            new_counters.append(replace(c, external_gain=ext))
        else:
            new_counters.append(c)
    meta["dream_external"] = ext
    notes = list(inp.notes or [])
    notes.append(
        f"L1_CONSUMES: refine ashen_stacks={ash['stacks']:.3f} "
        f"vuln={ash['vuln']:.4f} zone_cov={zcov:.4f} "
        f"zone_dream={zd['rate']:.4f} ext={ext:.4f}"
    )
    return replace(inp, buffs=new_buffs, counters=new_counters, notes=notes), meta
