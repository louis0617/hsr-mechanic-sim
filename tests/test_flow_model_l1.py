"""L1-T1..T4: synthetic flow LP. No character JSON, expectations not loosened."""
from __future__ import annotations

import math

from hsrsim.analytic.bounds import FlowSpec, solve_interval
from hsrsim.analytic.coverage import (
    ActionAdvance,
    ActionBuff,
    EnemyStackVuln,
    ZoneBuff,
    context_with,
    expected_combo_damage,
    iterate_coverage,
    iterate_state,
    stack_rate_needed_for_cap,
    stack_saturation,
)
from hsrsim.simulator.damage_zones import DamageContext, compute_damage
from hsrsim.simulator.types import DamageType, Element
from hsrsim.analytic.flow_model import (
    CounterResource,
    FlowCharacter,
    GainRule,
    solve_flow,
)

# Relative tolerance. 30/115 and 36/114 are exact rationals; 1e-6 is enough
# for HiGHS without swallowing a wrong optimum.
_REL = 1e-6
_ABS = 1e-8


def _close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=_REL, abs_tol=_ABS), (
        f"{actual} != {expected}"
    )


def _pair() -> list[FlowCharacter]:
    """T1 cast. No ultimate: energy_cost>0 and zero gains force u=0."""
    return [
        FlowCharacter(
            id="A",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=3.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=1.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="B",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=2.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=1.0,
            energy_cost=1.0,
        ),
    ]


def _ult_caster(*, err: float) -> list[FlowCharacter]:
    # Basic and skill deal the same direct damage; skill is the only energy
    # source, and the ultimate has positive damage, so the optimum is all skills.
    return [
        FlowCharacter(
            id="C",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=1.0,
            ult_damage=1.0,
            sp_add=1.0,
            sp_need=0.0,
            energy_basic=0.0,
            energy_skill=30.0,
            energy_ult=5.0,
            energy_hit=0.0,
            energy_cost=120.0,
            err=err,
            hits_per_100_av=0.0,
        )
    ]


def test_l1_t1_sp_shadow_price():
    # B swapping one basic for one skill: SP change is -1 (lost basic) and -1
    # (skill cost) = -2, damage change is -1 + 2 = +1. Value per SP is 1/2 = 0.5.
    sol = solve_flow(_pair(), sp_other=0.0)
    _close(sol.rates["A"]["ns"], 1.0)
    _close(sol.rates["A"]["nb"], 0.0)
    _close(sol.rates["A"]["u"], 0.0)
    _close(sol.rates["B"]["ns"], 0.0)
    _close(sol.rates["B"]["nb"], 1.0)
    _close(sol.rates["B"]["u"], 0.0)
    _close(sol.total_damage, 4.0)
    _close(sol.duals["sp_balance"], 0.5)


def test_l1_t2_sp_other():
    sol = solve_flow(_pair(), sp_other=1.0)
    _close(sol.rates["A"]["ns"], 1.0)
    _close(sol.rates["A"]["nb"], 0.0)
    _close(sol.rates["B"]["nb"], 0.5)
    _close(sol.rates["B"]["ns"], 0.5)
    _close(sol.total_damage, 4.5)


def test_l1_t3_energy_err_1():
    sol = solve_flow(_ult_caster(err=1.0))
    _close(sol.rates["C"]["nb"], 0.0)
    _close(sol.rates["C"]["ns"], 1.0)
    _close(sol.rates["C"]["u"], 30.0 / 115.0)


def test_l1_t4_energy_err_1_2():
    sol = solve_flow(_ult_caster(err=1.2))
    _close(sol.rates["C"]["nb"], 0.0)
    _close(sol.rates["C"]["ns"], 1.0)
    _close(sol.rates["C"]["u"], 36.0 / 114.0)


def _support_and_main(main_speed: float) -> tuple[list[FlowCharacter], list]:
    from hsrsim.analytic.coverage import ActionBuff

    # Support skill damage is strictly above basic so "skill every turn" is
    # the unique LP optimum. The buff does not change that choice.
    cast = [
        FlowCharacter(
            id="sup",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=2.0,
            sp_add=0.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="main",
            speed=main_speed,
            basic_damage=1.0,
            skill_damage=1.0,
            sp_add=0.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
    ]
    buffs = [
        ActionBuff(
            id="amp",
            applier_id="sup",
            recipient_id="main",
            source_action="skill",
            duration_turns=2.0,
            skill_damage=5.0,
        )
    ]
    return cast, buffs


def test_l1_t5_coverage_full():
    # Support speed 100, all skills: apply rate = 1 per 100 AV.
    # Main speed 150: turn rate = 1.5.
    # c = min(1, 1 * 2 / 1.5) = min(1, 1.333...) = 1.
    from hsrsim.analytic.coverage import iterate_coverage

    cast, buffs = _support_and_main(150.0)
    result = iterate_coverage(cast, buffs)
    assert result.converged
    _close(result.solution.rates["sup"]["ns"], 1.0)
    _close(result.coverages["amp"], 1.0)


def test_l1_t6_coverage_partial():
    # Same apply rate 1 and duration 2.
    # Main speed 250: turn rate = 2.5.
    # c = min(1, 1 * 2 / 2.5) = 0.8.
    from hsrsim.analytic.coverage import iterate_coverage

    cast, buffs = _support_and_main(250.0)
    result = iterate_coverage(cast, buffs)
    assert result.converged
    _close(result.solution.rates["sup"]["ns"], 1.0)
    _close(result.coverages["amp"], 0.8)


def test_l1_t9_coverage_rises_with_sp():
    # Unbuffed basic damage equals unbuffed skill damage. Only the skill
    # applies the buff, and that buff raises skill damage, so once coverage
    # is positive the LP prefers skills. SP (basic +1, skill -1) caps skills
    # at (1 + sp_other) / 2 per 100 AV, so the support cannot skill every turn
    # while sp_other < 1. Duration 1 and turn rate 1 keep coverage equal to
    # the skill rate, which stays below 1 for the points below.
    from hsrsim.analytic.coverage import ActionBuff, iterate_coverage

    sup = FlowCharacter(
        id="sup",
        speed=100.0,
        basic_damage=1.0,
        skill_damage=1.0,
        sp_add=1.0,
        sp_need=1.0,
        energy_cost=1.0,
    )
    buff = ActionBuff(
        id="self_skill",
        applier_id="sup",
        recipient_id="sup",
        source_action="skill",
        duration_turns=1.0,
        skill_damage=4.0,
    )
    coverages = []
    for sp_other in (0.0, 0.4, 0.8):
        result = iterate_coverage([sup], [buff], sp_other=sp_other)
        assert result.converged
        coverages.append(result.coverages["self_skill"])
    assert all(c < 1.0 for c in coverages)
    assert coverages[0] < coverages[1] < coverages[2]


def _meter(
    *,
    ally_a_basic: float,
    ally_a_skill: float,
    ally_b_basic: float,
    ally_b_skill: float,
    ally_sp: bool,
) -> tuple[list[FlowCharacter], CounterResource]:
    holder = FlowCharacter(
        id="holder",
        speed=100.0,
        basic_damage=0.0,
        skill_damage=0.0,
        ult_damage=1.0,
    )
    allies = []
    for cid in ("ally_a", "ally_b"):
        allies.append(
            FlowCharacter(
                id=cid,
                speed=125.0,
                basic_damage=0.0,
                skill_damage=0.0,
                ult_damage=0.0,
                sp_add=1.0 if ally_sp else 0.0,
                sp_need=1.0 if ally_sp else 0.0,
                energy_cost=1.0,
            )
        )
    counter = CounterResource(
        name="meter",
        holder_id="holder",
        cap=9.0,
        consume={"ult": 9.0},
        rules=(
            GainRule(
                predicate="applies_debuff",
                increment=1.0,
                per_action_cap=1.0,
                fires={
                    "ally_a": {"basic": ally_a_basic, "skill": ally_a_skill},
                    "ally_b": {"basic": ally_b_basic, "skill": ally_b_skill},
                },
            ),
            GainRule(
                predicate="holder_skill_grants",
                increment=1.0,
                per_action_cap=1.0,
                fires={"holder": {"skill": 1.0}},
            ),
        ),
    )
    return [holder, *allies], counter


def test_l1_t7_counter_rate():
    # Each ally speed 125 → 1.25 actions. Both actions apply one debuff, cap 1,
    # so each ally contributes 1.25. Two allies: 2.5.
    # Holder speed 100 skills once (the skill grants +1 and is not a debuff): +1.
    # Gain = 3.5. Ult spends 9, so u = 3.5/9.
    cast, counter = _meter(
        ally_a_basic=1.0,
        ally_a_skill=1.0,
        ally_b_basic=1.0,
        ally_b_skill=1.0,
        ally_sp=False,
    )
    sol = solve_flow(cast, counters=[counter])
    _close(sol.resource_gains["counter:meter"], 3.5)
    _close(sol.rates["holder"]["u"], 3.5 / 9.0)
    _close(sol.rates["holder"]["ns"], 1.0)


def test_l1_t10_counter_follows_skills():
    cast, counter = _meter(
        ally_a_basic=0.0,
        ally_a_skill=1.0,
        ally_b_basic=0.0,
        ally_b_skill=1.0,
        ally_sp=True,
    )
    gains = []
    for sp_other in (0.0, 1.0, 2.0):
        sol = solve_flow(cast, sp_other=sp_other, counters=[counter])
        gains.append(sol.resource_gains["counter:meter"])
    assert gains[0] < gains[1] < gains[2]


def test_l1_t11_per_action_cap():
    # ally_a basic fires the predicate 3 times. Without the cap the optimum
    # would use that action and the gain would be 1.25*3 + 1.25 + 1 = 6.
    # per_action_cap=1 keeps every action at one trigger, so the gain stays 3.5.
    cast, counter = _meter(
        ally_a_basic=3.0,
        ally_a_skill=1.0,
        ally_b_basic=1.0,
        ally_b_skill=1.0,
        ally_sp=False,
    )
    sol = solve_flow(cast, counters=[counter])
    _close(sol.resource_gains["counter:meter"], 3.5)
    _close(sol.rates["holder"]["u"], 3.5 / 9.0)


def _turns(sol, char_id: str) -> float:
    rates = sol.rates[char_id]
    return rates["nb"] + rates["ns"]


def _pull_spec(support_speed: float):
    """Support skills every turn (skill damage strictly above basic, no SP cost).

    The skill advances main by half a turn. Main speed is 100, so the
    no-advance turn count is 1. Upper turns are 1 + support_skill_rate * 0.5.
    """
    support = FlowCharacter(
        id="support",
        speed=support_speed,
        basic_damage=1.0,
        skill_damage=2.0,
        ult_damage=0.0,
        sp_add=1.0,
        sp_need=0.0,
        energy_cost=1.0,
    )
    main = FlowCharacter(
        id="main",
        speed=100.0,
        basic_damage=1.0,
        skill_damage=1.0,
        ult_damage=0.0,
        sp_add=1.0,
        sp_need=0.0,
        energy_cost=1.0,
    )
    advance = ActionAdvance(
        id="pull",
        applier_id="support",
        recipient_id="main",
        ratio=0.5,
        source_actions=("skill",),
    )
    return FlowSpec(characters=(support, main), advances=(advance,))


def test_l1_t8_advance_interval():
    # Support speed 100 skills once per 100 AV. Advance ratio 0.5.
    # Lower: adv = 0, main turns = 100/100 = 1.0.
    # Upper: adv = 1.0 * 0.5 = 0.5, main turns = 1.0 + 0.5 = 1.5.
    result = solve_interval(_pull_spec(100.0))
    assert result.lower.converged and result.upper.converged
    assert result.requires_l2 is True
    assert result.reasons == [
        "拉条 pull：support 的 skill 将 main 前拉 0.5"
    ]
    _close(_turns(result.lower.solution, "main"), 1.0)
    _close(_turns(result.upper.solution, "main"), 1.5)
    _close(result.upper.solution.rates["support"]["ns"], 1.0)


def test_l1_t12_faster_support_advance():
    # Support speed 200 skills twice. Upper adv = 2.0 * 0.5 = 1.0,
    # so main turns = 1.0 + 1.0 = 2.0. Lower still ignores the advance:
    # main speed 100 and adv = 0 give 1.0.
    result = solve_interval(_pull_spec(200.0))
    assert result.upper.converged
    assert result.requires_l2 is True
    _close(_turns(result.upper.solution, "main"), 2.0)
    _close(_turns(result.lower.solution, "main"), 1.0)
    _close(result.upper.solution.rates["support"]["ns"], 2.0)


def test_skill_share_scales_with_extra_turns():
    """Share ``r`` is of the full turn budget, including extra_turns.

    nb + ns = spd/100 + extra; ns = r (nb+ns) ⇒ ns = r (spd/100 + extra).
    """
    main = FlowCharacter(
        id="M",
        speed=100.0,
        basic_damage=1.0,
        skill_damage=3.0,
        ult_damage=0.0,
        sp_add=1.0,
        sp_need=1.0,
    )
    sol = solve_flow(
        [main],
        extra_turns={"M": 0.5},
        skill_shares={"M": 1.0},
        sp_other=10.0,
    )
    _close(sol.rates["M"]["nb"], 0.0)
    _close(sol.rates["M"]["ns"], 1.5)
    sol2 = solve_flow(
        [main],
        extra_turns={"M": 0.5},
        skill_shares={"M": 1.0 / 3.0},
        sp_other=10.0,
    )
    _close(sol2.rates["M"]["ns"], 0.5)
    _close(sol2.rates["M"]["nb"], 1.0)


def test_l1_t13_no_av_mechanism():
    spec = FlowSpec(
        characters=(
            FlowCharacter(
                id="A",
                speed=100.0,
                basic_damage=1.0,
                skill_damage=3.0,
                ult_damage=0.0,
                sp_add=1.0,
                sp_need=1.0,
                energy_cost=1.0,
            ),
        )
    )
    result = solve_interval(spec)
    assert result.requires_l2 is False
    assert result.reasons == []
    assert result.lower.converged and result.upper.converged
    for key in ("nb", "ns", "u"):
        _close(result.lower.solution.rates["A"][key], result.upper.solution.rates["A"][key])
    _close(result.lower.solution.total_damage, result.upper.solution.total_damage)
    assert result.lower.advances == result.upper.advances


def _unit_ctx() -> DamageContext:
    # Level 80 vs 80, ATK 2, MV 1, no crit, toughness broken: base zone 2, def zone 0.5, damage 1.
    return DamageContext(
        attacker_level=80,
        attacker_atk=2.0,
        attacker_crit_rate=0.0,
        attacker_crit_dmg=0.0,
        attacker_break_effect=0.0,
        attacker_dmg_boost_pct=0.0,
        attacker_res_pen_pct=0.0,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=0.0,
        defender_level=80,
        defender_def=0.0,
        defender_res_pct=0.0,
        defender_vuln_pct=0.0,
        defender_mit_layers=[],
        defender_toughness_broken=True,
        skill_multiplier=1.0,
        damage_type=DamageType.DIRECT,
        element=Element.PHYSICAL,
    )


def test_l1_t14_two_buffs_full_coverage():
    # dmg boost +50% and vuln +50% are different zones, so both-on is 1.5 * 1.5 = 2.25.
    ctx = _unit_ctx()
    buffs = [
        ZoneBuff(id="boost", dmg_boost=0.5),
        ZoneBuff(id="vuln", vuln=0.5),
    ]
    both, _zones = compute_damage(context_with(ctx, buffs))
    result = expected_combo_damage(ctx, buffs, {"boost": 1.0, "vuln": 1.0})
    _close(result.damage, both)
    _close(result.damage, 2.25)
    assert result.independence_note


def test_l1_t15_two_buffs_half_coverage():
    # Each zone +50%, coverage 0.5. Different zones, so the product of expected
    # zone factors equals the enumeration: 1.25 * 1.25 = 1.5625.
    # Same-zone contrast is also checked: two +50% dmg boosts add to ×2.0 when
    # both are up, not ×2.25, so the enumeration (1.5) is below the product (1.5625).
    ctx = _unit_ctx()
    split = [
        ZoneBuff(id="boost", dmg_boost=0.5),
        ZoneBuff(id="vuln", vuln=0.5),
    ]
    coverages = {"boost": 0.5, "vuln": 0.5}
    result = expected_combo_damage(ctx, split, coverages)
    _close(result.damage, 1.5625)
    _close(result.zone_product, 1.5625)

    same = [
        ZoneBuff(id="a", dmg_boost=0.5),
        ZoneBuff(id="b", dmg_boost=0.5),
    ]
    folded = expected_combo_damage(ctx, same, {"a": 0.5, "b": 0.5})
    _close(folded.damage, 1.5)
    _close(folded.zone_product, 1.5625)
    assert folded.damage < folded.zone_product


def test_l1_t16_three_buffs_one_absent():
    # c = 1, 0.5, 0. The third buff drops out. What remains is the binary mix
    # of the second buff on top of the first: 0.5 * 1.5 + 0.5 * 2.25 = 1.875.
    ctx = _unit_ctx()
    buffs = [
        ZoneBuff(id="always", dmg_boost=0.5),
        ZoneBuff(id="half", vuln=0.5),
        ZoneBuff(id="never", def_reduction=0.5),
    ]
    result = expected_combo_damage(
        ctx, buffs, {"always": 1.0, "half": 0.5, "never": 0.0}
    )
    first, _z = compute_damage(context_with(ctx, [buffs[0]]))
    both, _z = compute_damage(context_with(ctx, [buffs[0], buffs[1]]))
    _close(result.damage, 0.5 * first + 0.5 * both)
    _close(result.damage, 1.875)


def _figment_pair(
    *,
    speed: float,
    skill_damage: float,
    basic_damage: float,
    with_cipher_energy: bool = False,
) -> list[FlowCharacter]:
    """Two free-skill allies. Energy cost blocks ultimates unless asked."""
    out: list[FlowCharacter] = []
    for i, cid in enumerate(("A", "B")):
        kwargs: dict = dict(
            id=cid,
            speed=speed,
            basic_damage=basic_damage,
            skill_damage=skill_damage,
            ult_damage=0.0,
            sp_add=0.0,
            sp_need=0.0,
            energy_cost=1.0,
        )
        if with_cipher_energy and i == 0:
            # One skill per turn funds one ult; cipher coverage reaches 1.
            kwargs["energy_basic"] = 0.0
            kwargs["energy_skill"] = 100.0
            kwargs["energy_ult"] = 0.0
            kwargs["energy_hit"] = 0.0
            kwargs["energy_cost"] = 100.0
            kwargs["err"] = 1.0
            kwargs["hits_per_100_av"] = 0.0
            kwargs["ult_damage"] = 0.01
        out.append(FlowCharacter(**kwargs))
    return out


def _figment_spec(*, enemy_speed: float = 100.0, cipher_applier: str = "A") -> EnemyStackVuln:
    return EnemyStackVuln(
        id="figment",
        per_stack=0.04,
        max_stacks=3.0,
        stack_duration_turns=2.0,
        enemy_speed=enemy_speed,
        cipher_applier_id=cipher_applier,
        cipher_source_action="ult",
        cipher_duration_turns=3.0,
        cipher_per_stack=0.06,
    )


def test_l1_t17_figment_full_stacks():
    # Enemy speed 100 → 1 turn / 100 AV. Two speed-100 skill users give Σ ns = 2.
    # stacks = min(3, 2 × 2 / 1) = 3. A's ult rate 1 → cipher coverage = 1.
    # Full vulnerability = 3 × (0.04 + 0.06) = 0.30.
    cast = _figment_pair(
        speed=100.0, skill_damage=1.0, basic_damage=0.0, with_cipher_energy=True
    )
    result = iterate_state(cast, stack_vulns=[_figment_spec()])
    assert result.converged
    assert result.stack_vulns is not None
    state = result.stack_vulns["figment"]
    _close(state.stacks, 3.0)
    _close(state.cipher_coverage, 1.0)
    _close(state.vuln, 0.30)


def test_l1_t18_figment_stacks_rise_with_skill_rate():
    # Same enemy. Speed 50 → Σ ns = 1 → stacks = 2. Speed 25 → Σ ns = 0.5 → stacks = 1.
    # Only assert stacks < 3 and that higher skill rate raises stacks.
    low = iterate_state(
        _figment_pair(speed=25.0, skill_damage=1.0, basic_damage=0.0),
        stack_vulns=[_figment_spec()],
    )
    mid = iterate_state(
        _figment_pair(speed=50.0, skill_damage=1.0, basic_damage=0.0),
        stack_vulns=[_figment_spec()],
    )
    assert low.converged and mid.converged
    assert low.stack_vulns is not None and mid.stack_vulns is not None
    low_s = low.stack_vulns["figment"].stacks
    mid_s = mid.stack_vulns["figment"].stacks
    assert mid_s < 3.0
    assert low_s < mid_s
    _close(low_s, 1.0)
    _close(mid_s, 2.0)


def test_l1_t19_figment_zero_without_skills():
    # Skill deals nothing, so the LP picks all basics. Σ ns = 0 → stacks = 0 → vuln = 0.
    cast = _figment_pair(speed=100.0, skill_damage=0.0, basic_damage=1.0)
    result = iterate_state(cast, stack_vulns=[_figment_spec()])
    assert result.converged
    assert result.stack_vulns is not None
    state = result.stack_vulns["figment"]
    _close(state.stacks, 0.0)
    _close(state.vuln, 0.0)


def _combo_ctx() -> DamageContext:
    return _unit_ctx()


def _t20_cast() -> list[FlowCharacter]:
    # A is the DPS. J applies a binary vuln. S funds figment stacks via skills.
    return [
        FlowCharacter(
            id="A",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=1.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="J",
            speed=100.0,
            basic_damage=0.0,
            skill_damage=0.01,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="S",
            speed=100.0,
            basic_damage=0.0,
            skill_damage=0.01,
            ult_damage=0.01,
            sp_add=1.0,
            sp_need=0.0,
            energy_basic=0.0,
            energy_skill=100.0,
            energy_ult=0.0,
            energy_hit=0.0,
            energy_cost=100.0,
            err=1.0,
            hits_per_100_av=0.0,
        ),
    ]


def test_l1_t20_jiaoqiu_vuln_plus_figment_same_zone():
    # Figment full: stacks=3, cipher=1 → continuous vuln 0.30.
    # Jiaoqiu-like ActionBuff vuln +0.20 at full coverage → same zone adds to 0.50.
    cast = _t20_cast()
    buff = ActionBuff(
        id="jq_vuln",
        applier_id="J",
        recipient_id="A",
        source_action="skill",
        duration_turns=3.0,
        vuln=0.20,
    )
    stack = _figment_spec(cipher_applier="S")
    ctxs = {"A": {"basic": [_combo_ctx()], "skill": [_combo_ctx()], "ult": []},
            "J": {"basic": [], "skill": [_combo_ctx()], "ult": []},
            "S": {"basic": [], "skill": [_combo_ctx()], "ult": [_combo_ctx()]}}
    result = iterate_state(
        cast, [buff], stack_vulns=[stack], action_contexts=ctxs
    )
    assert result.converged
    assert result.stack_vulns is not None
    state = result.stack_vulns["figment"]
    _close(state.stacks, 3.0)
    _close(state.cipher_coverage, 1.0)
    _close(state.vuln, 0.30)
    cov = result.coverages["jq_vuln"]
    _close(cov, 1.0)
    # Base damage 1.0 with toughness broken. Continuous 0.30 always on;
    # jq vuln adds in the same zone when up → factor 1.50 at c=1.
    expected = 1.0 * (1.0 + 0.30 + 0.20)
    _close(result.solution.rates["A"]["nb"] + result.solution.rates["A"]["ns"], 1.0)
    # Rebuild expected combo damage for A's skill/basic on the fixed-point state.
    from hsrsim.analytic.coverage import expected_combo_damage as ecd

    combo = ecd(
        _combo_ctx(),
        [ZoneBuff(id="jq_vuln", vuln=0.20)],
        {"jq_vuln": cov},
        continuous=ZoneBuff(id="c", vuln=state.vuln),
    )
    _close(combo.damage, expected)
    # Team damage uses the blended A damage; at least A's contribution matches.
    a_dmg = result.solution.rates["A"]["nb"] * combo.damage + result.solution.rates["A"][
        "ns"
    ] * combo.damage
    assert a_dmg > 0.0
    # Same-zone add, not separate product zones: 1.30 * 1.20 = 1.56 would be wrong.
    assert abs(combo.damage - 1.56) > 1e-6
    _close(combo.damage, 1.50)


def test_l1_t21_cross_zone_beats_same_zone_add():
    # Identical to T20 except the ActionBuff lands in dmg_boost, so factors multiply.
    cast = _t20_cast()
    buff = ActionBuff(
        id="jq_boost",
        applier_id="J",
        recipient_id="A",
        source_action="skill",
        duration_turns=3.0,
        dmg_boost=0.20,
    )
    stack = _figment_spec(cipher_applier="S")
    ctxs = {"A": {"basic": [_combo_ctx()], "skill": [_combo_ctx()], "ult": []},
            "J": {"basic": [], "skill": [_combo_ctx()], "ult": []},
            "S": {"basic": [], "skill": [_combo_ctx()], "ult": [_combo_ctx()]}}
    same = iterate_state(
        cast,
        [
            ActionBuff(
                id="jq_vuln",
                applier_id="J",
                recipient_id="A",
                source_action="skill",
                duration_turns=3.0,
                vuln=0.20,
            )
        ],
        stack_vulns=[stack],
        action_contexts=ctxs,
    )
    cross = iterate_state(
        cast, [buff], stack_vulns=[stack], action_contexts=ctxs
    )
    assert same.converged and cross.converged
    assert same.stack_vulns is not None and cross.stack_vulns is not None
    _close(same.stack_vulns["figment"].vuln, 0.30)
    _close(cross.stack_vulns["figment"].vuln, 0.30)
    same_combo = expected_combo_damage(
        _combo_ctx(),
        [ZoneBuff(id="jq_vuln", vuln=0.20)],
        {"jq_vuln": same.coverages["jq_vuln"]},
        continuous=ZoneBuff(id="c", vuln=0.30),
    )
    cross_combo = expected_combo_damage(
        _combo_ctx(),
        [ZoneBuff(id="jq_boost", dmg_boost=0.20)],
        {"jq_boost": cross.coverages["jq_boost"]},
        continuous=ZoneBuff(id="c", vuln=0.30),
    )
    _close(same_combo.damage, 1.50)
    _close(cross_combo.damage, 1.30 * 1.20)
    assert cross_combo.damage > same_combo.damage


def test_l1_t22_figment_saturation_double_trigger():
    # Enemy speed 100 → 1 turn/100 AV. Cap needs Σ ns = 3 * 1 / 2 = 1.5.
    # Three skill users at speed 100 → Σ ns = 3 → saturation = 3 / 1.5 = 2.0.
    # Stacks still clamp at 3.0.
    cast = [
        FlowCharacter(
            id=cid,
            speed=100.0,
            basic_damage=0.0,
            skill_damage=1.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=0.0,
            energy_cost=1.0,
        )
        for cid in ("A", "B", "C")
    ]
    spec = _figment_spec()
    needed = stack_rate_needed_for_cap(spec)
    _close(needed, 1.5)
    result = iterate_state(cast, stack_vulns=[spec])
    assert result.converged
    assert result.stack_vulns is not None
    state = result.stack_vulns["figment"]
    _close(state.stacks, 3.0)
    rates = result.solution.rates
    sat = stack_saturation(rates, spec)
    _close(sat, 2.0)
    _close(state.saturation, 2.0)


def _buff_support_cast(*, buffed_skill: float) -> tuple[list[FlowCharacter], list[ActionBuff]]:
    # Support skill deals 0; basic deals 1. Buff on skill raises main skill damage.
    cast = [
        FlowCharacter(
            id="M",
            speed=100.0,
            basic_damage=0.0,
            skill_damage=10.0,
            ult_damage=0.0,
            sp_add=0.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="S",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=0.0,
            ult_damage=0.0,
            sp_add=0.0,
            sp_need=0.0,
            energy_cost=1.0,
        ),
    ]
    buffs = [
        ActionBuff(
            id="amp",
            applier_id="S",
            recipient_id="M",
            source_action="skill",
            duration_turns=1.0,
            basic_damage=0.0,
            skill_damage=buffed_skill,
            ult_damage=0.0,
        )
    ]
    return cast, buffs


def test_l1_t23_buff_only_skill_preferred_when_buff_doubles():
    from hsrsim.analytic.buff_enum import enumerate_buff_actions

    # Unbuffed skill 10 → buffed 20. Support basic 1. All-skill support beats
    # all-basic: 20 > 10+1. Outer grid must pick ns_S = 1.
    cast, buffs = _buff_support_cast(buffed_skill=20.0)
    result = enumerate_buff_actions(cast, buffs, grid_size=11)
    assert result.best is not None and result.best.result is not None
    _close(result.best.fixed_rates["S"]["skill"], 1.0)
    _close(result.best.result.solution.rates["S"]["ns"], 1.0)
    _close(result.best.result.coverages["amp"], 1.0)
    assert result.defective_damage is not None
    assert result.best.damage is not None
    assert result.best.damage > result.defective_damage


def test_l1_t24_buff_only_skill_skipped_when_buff_tiny():
    from hsrsim.analytic.buff_enum import enumerate_buff_actions

    # +1% on main skill: 10.1. Support basic 1. All-basic team = 11 > 10.1.
    cast, buffs = _buff_support_cast(buffed_skill=10.1)
    result = enumerate_buff_actions(cast, buffs, grid_size=11)
    assert result.best is not None and result.best.result is not None
    _close(result.best.fixed_rates["S"]["skill"], 0.0)
    _close(result.best.result.solution.rates["S"]["nb"], 1.0)
    _close(result.best.result.coverages["amp"], 0.0)


def test_l1_t25_buff_only_interior_optimum():
    from hsrsim.analytic.buff_enum import enumerate_buff_actions

    # SP couples the two skills: ns_M + ns_S <= 1. Buff raises M skill 10 → 100.
    # Continuous optimum ns_S = 79/180 ≈ 0.439; on an 11-point grid the peak
    # must sit strictly between 0 and the turn cap.
    cast = [
        FlowCharacter(
            id="M",
            speed=100.0,
            basic_damage=0.0,
            skill_damage=10.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=1.0,
            energy_cost=1.0,
        ),
        FlowCharacter(
            id="S",
            speed=100.0,
            basic_damage=1.0,
            skill_damage=0.0,
            ult_damage=0.0,
            sp_add=1.0,
            sp_need=1.0,
            energy_cost=1.0,
        ),
    ]
    buffs = [
        ActionBuff(
            id="amp",
            applier_id="S",
            recipient_id="M",
            source_action="skill",
            duration_turns=1.0,
            basic_damage=0.0,
            skill_damage=100.0,
            ult_damage=0.0,
        )
    ]
    result = enumerate_buff_actions(cast, buffs, grid_size=11)
    assert result.best is not None
    ns = result.best.fixed_rates["S"]["skill"]
    assert 0.0 < ns < 1.0, f"expected interior ns, got {ns}"
    # Nearest grid points to 79/180≈0.4389 are 0.4 and 0.5.
    assert abs(ns - 0.4) < 1e-9 or abs(ns - 0.5) < 1e-9


