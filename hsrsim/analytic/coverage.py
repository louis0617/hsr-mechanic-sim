"""One fixed-point loop for buff coverage, speed buffs, and action advance.

Each iteration blends damage and speed from the current coverages, passes
the current ``adv`` into ``solve_flow``, then recomputes both. The same
tolerance and iteration cap apply to every tracked value. Callers that
omit advances and speed buffs get the B2 coverage iteration unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from hsrsim.analytic.flow_model import (
    CounterResource,
    FlowCharacter,
    FlowSolution,
    solve_flow,
)
from hsrsim.simulator.damage_zones import DamageContext, compute_damage

_ACTION_RATE = {"basic": "nb", "skill": "ns", "ult": "u"}
_MAX_COMBO_BUFFS = 12

# Coverage probabilities multiply. Sparkle's advance lines her buff up with
# Acheron's turn, so the factors are not independent. Kept on every result.
COVERAGE_INDEPENDENCE_GAP = (
    "覆盖率按独立假设相乘：生效的乘 c_i，未生效的乘 (1-c_i)。"
    "花火的拉条会使她的增益与黄泉的行动高度同步，覆盖并不独立。"
    "这是已知的模型偏差。"
)
_DAMAGE_FIELDS = ("basic_damage", "skill_damage", "ult_damage")


@dataclass(frozen=True)
class ActionBuff:
    """One buff whose uptime is an apply-rate times a turn duration.

    Prefer zone shifts (``vuln``, ``dmg_boost``, …). When those are set,
    ``iterate_state`` routes through ``expected_combo_damage``. The legacy
    damage fields remain for single-buff binary mix (L1-T5/T6).

     extensions:
    - ``source_action=\"all\"``: apply rate = nb+ns+u
    - ``enemy_speed>0``: duration ticks on enemy clock
    - ``apply_before_damage=False``: 先算伤再挂 → cold-hit coverage
    - ``coverage_mode=\"sp_blaze\"``: mask from SP→彩焰 refresh flow
    - ``applies_to``: limit zone shifts to listed actions (None = all)
    """

    id: str
    applier_id: str
    recipient_id: str
    source_action: str
    duration_turns: float
    basic_damage: float | None = None
    skill_damage: float | None = None
    ult_damage: float | None = None
    dmg_boost: float = 0.0
    vuln: float = 0.0
    def_reduction: float = 0.0
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    res_pen: float = 0.0
    weaken: float = 0.0
    enemy_speed: float = 0.0
    apply_before_damage: bool = True
    coverage_mode: str = "action"
    sp_gain_basic: float = 0.0
    sp_gain_skill: float = 0.0
    sp_gain_ult: float = 0.0
    blaze_threshold: float = 4.0
    applies_to: frozenset[str] | None = None
    #mirage hit-weighted coverage (ult multi-segment).
    mirage_hits_per_attack: float = 1.0
    # F0-lite /: when set, skip min(1,λ) and use this coverage.
    force_coverage: float | None = None

    def __post_init__(self) -> None:
        if self.source_action not in _ACTION_RATE and self.source_action != "all":
            raise ValueError(
                f"source_action must be basic/skill/ult/all, got {self.source_action!r}"
            )
        if self.duration_turns < 0:
            raise ValueError("duration_turns must be >= 0")
        if self.coverage_mode not in (
            "action",
            "sp_blaze",
            "mirage_residual",
            "mirage_same_hit",
        ):
            raise ValueError(f"unknown coverage_mode {self.coverage_mode!r}")
        if self.applies_to is not None:
            bad = set(self.applies_to) - {"basic", "skill", "ult"}
            if bad:
                raise ValueError(f"applies_to has unknown actions {bad}")
        if float(self.mirage_hits_per_attack) < 1.0:
            raise ValueError("mirage_hits_per_attack must be >= 1")
        if self.force_coverage is not None and not (0.0 <= float(self.force_coverage) <= 1.0):
            raise ValueError("force_coverage must be in [0, 1]")

    def has_zone_shifts(self) -> bool:
        return any(
            float(getattr(self, name)) != 0.0
            for name in _ZONE_SHIFT_FIELDS
        )

    def as_zone_buff(self) -> ZoneBuff:
        return ZoneBuff(
            id=self.id,
            dmg_boost=float(self.dmg_boost),
            vuln=float(self.vuln),
            def_reduction=float(self.def_reduction),
            crit_rate=float(self.crit_rate),
            crit_dmg=float(self.crit_dmg),
            res_pen=float(self.res_pen),
            weaken=float(self.weaken),
        )


_ZONE_SHIFT_FIELDS = (
    "dmg_boost",
    "vuln",
    "def_reduction",
    "crit_rate",
    "crit_dmg",
    "res_pen",
    "weaken",
)


@dataclass
class CoverageResult:
    solution: FlowSolution
    coverages: dict[str, float]
    iterations: int
    converged: bool
    history: list[dict[str, float]]


@dataclass(frozen=True)
class SpeedBuff:
    """Speed while a buff is up. Effective speed is coverage-weighted.

    ``speed`` replaces the recipient's base speed for the covered fraction
    of their turns. Two speed buffs on one recipient are rejected.
    """

    id: str
    applier_id: str
    recipient_id: str
    source_action: str
    duration_turns: float
    speed: float

    def __post_init__(self) -> None:
        if self.source_action not in _ACTION_RATE:
            raise ValueError(f"source_action must be basic/skill/ult, got {self.source_action!r}")
        if self.duration_turns < 0:
            raise ValueError("duration_turns must be >= 0")


@dataclass(frozen=True)
class ActionAdvance:
    """One action-advance: applier actions pull the recipient forward.

    ``ratio`` is the fraction of a turn granted per triggering action.
    ``source_actions`` lists which of the applier's actions trigger it.
    """

    id: str
    applier_id: str
    recipient_id: str
    ratio: float
    source_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.ratio < 0:
            raise ValueError("advance ratio must be >= 0")
        if not self.source_actions:
            raise ValueError(f"advance {self.id} needs at least one source action")
        for action in self.source_actions:
            if action not in _ACTION_RATE:
                raise ValueError(f"source action must be basic/skill/ult, got {action!r}")


@dataclass(frozen=True)
class EnemyStackVuln:
    """Enemy vulnerability from Sparkle figment stacks plus optional cipher.

    Trigger rate is the team's total skill rate ``Σ ns_i``. Expected stacks
    tick on the stack holder's turns (: Sparkle), falling back to
    ``enemy_speed / 100`` when ``stack_holder_id`` is absent. Cipher is an
    ally buff; coverage uses recipient uptime when ``cipher_recipient_id``
    is set, else the legacy enemy-turn clock. Total vulnerability is
    ``stacks × (per_stack + cipher_coverage × cipher_per_stack)`` and lands
    in the vulnerability zone. Cross-zone with damage boost uses the product.
    """

    id: str
    per_stack: float
    max_stacks: float
    stack_duration_turns: float
    enemy_speed: float
    cipher_applier_id: str
    cipher_source_action: str
    cipher_duration_turns: float
    cipher_per_stack: float
    stack_holder_id: str | None = None
    cipher_recipient_id: str | None = None

    def __post_init__(self) -> None:
        if self.max_stacks < 0:
            raise ValueError("max_stacks must be >= 0")
        if self.stack_duration_turns < 0 or self.cipher_duration_turns < 0:
            raise ValueError("durations must be >= 0")
        if self.enemy_speed < 0:
            raise ValueError("enemy_speed must be >= 0")
        if self.cipher_source_action not in _ACTION_RATE:
            raise ValueError(
                f"cipher_source_action must be basic/skill/ult, got {self.cipher_source_action!r}"
            )


@dataclass(frozen=True)
class StackVulnState:
    stacks: float
    cipher_coverage: float
    vuln: float
    saturation: float = 0.0


@dataclass(frozen=True)
class DotStream:
    """Enemy-turn DoT contribution (): ticks = enemy_turns × coverage."""

    id: str
    applier_id: str
    duration_turns: float
    enemy_speed: float
    tick_damage: float
    # Actions that refresh/apply the DoT effect (rate sum → coverage).
    source_actions: tuple[str, ...] = ("basic", "skill", "ult")


def dot_damage_per_100_av(
    stream: DotStream, rates: dict[str, dict[str, float]]
) -> tuple[float, float, float]:
    """Return (dpr, tick_rate, coverage)."""
    row = rates.get(stream.applier_id) or {}
    apply = 0.0
    for action in stream.source_actions:
        key = _ACTION_RATE.get(action)
        if key is None:
            continue
        apply += float(row.get(key, 0.0))
    enemy_turns = _enemy_turns(float(stream.enemy_speed))
    if enemy_turns <= 0.0:
        return 0.0, 0.0, 0.0
    coverage = min(1.0, apply * float(stream.duration_turns) / enemy_turns)
    tick_rate = enemy_turns * coverage
    return tick_rate * float(stream.tick_damage), tick_rate, coverage


@dataclass
class FixedPointResult:
    solution: FlowSolution
    coverages: dict[str, float]
    advances: dict[str, float]
    speeds: dict[str, float]
    iterations: int
    converged: bool
    history: list[dict[str, float]]
    stack_vulns: dict[str, StackVulnState] | None = None
    enum_groups: list[list[str]] | None = None
    combo_notes: list[str] | None = None
    #solution without overflow extras (same coverages/adv), for before/after.
    solution_before_overflow: FlowSolution | None = None
    overflow_report: object | None = None
    dot_damage_per_100_av: float = 0.0
    dot_breakdown: dict[str, dict[str, float]] | None = None


def _uptime(
    applier_id: str,
    source_action: str,
    recipient_id: str,
    duration_turns: float,
    rates: dict[str, dict[str, float]],
) -> float:
    apply_rate = rates[applier_id][_ACTION_RATE[source_action]]
    turns = rates[recipient_id]["nb"] + rates[recipient_id]["ns"]
    if turns <= 0.0:
        return 0.0
    return min(1.0, apply_rate * duration_turns / turns)


def _apply_rate(buff: ActionBuff, rates: dict[str, dict[str, float]]) -> float:
    row = rates[buff.applier_id]
    if buff.source_action == "all":
        return float(row["nb"]) + float(row["ns"]) + float(row["u"])
    return float(row[_ACTION_RATE[buff.source_action]])


def _coverage(buff: ActionBuff, rates: dict[str, dict[str, float]]) -> float:
    if buff.force_coverage is not None:
        return max(0.0, min(1.0, float(buff.force_coverage)))
    if buff.coverage_mode == "sp_blaze":
        row = rates[buff.applier_id]
        sp_rate = (
            float(buff.sp_gain_basic) * float(row["nb"])
            + float(buff.sp_gain_skill) * float(row["ns"])
            + float(buff.sp_gain_ult) * float(row["u"])
        )
        thresh = max(float(buff.blaze_threshold), 1e-12)
        refresh = sp_rate / thresh
        turns = float(rates[buff.recipient_id]["nb"]) + float(
            rates[buff.recipient_id]["ns"]
        )
        if turns <= 0.0:
            return 0.0
        return min(1.0, refresh * float(buff.duration_turns) / turns)

    if buff.coverage_mode == "mirage_same_hit":
        # 同次攻击吃泡影：该次攻击所有段在施加后结算 → 稳态覆盖 ≈1。
        return 1.0

    if buff.coverage_mode == "mirage_residual":
        # 先算伤再挂 working model (engine choice; see evidence).
        # Residual on attack-start from all apply rate; single-hit uses normal-turn
        # clock; multi-hit ult averages cold first segment + warm rest.
        row = rates[buff.applier_id]
        lam_all = float(row["nb"]) + float(row["ns"]) + float(row["u"])
        lam_normal = float(row["nb"]) + float(row["ns"])
        turns = _enemy_turns(float(buff.enemy_speed))
        if turns <= 0.0 or lam_all <= 0.0:
            return 0.0
        n_hits = float(buff.mirage_hits_per_attack)
        if n_hits <= 1.0 + 1e-12:
            # basic/skill: one hit per attack; cold ≈ enemy_turn / normal_turns
            return max(0.0, min(1.0, 1.0 - turns / max(lam_normal, 1e-12)))
        # ult: first segment residual (all-attack clock), later segments warm
        c_first = max(0.0, min(1.0, 1.0 - turns / lam_all))
        return (c_first + (n_hits - 1.0) * 1.0) / n_hits

    apply_rate = _apply_rate(buff, rates)
    if float(buff.enemy_speed) > 0.0:
        turns = _enemy_turns(float(buff.enemy_speed))
    else:
        turns = float(rates[buff.recipient_id]["nb"]) + float(
            rates[buff.recipient_id]["ns"]
        )
    if turns <= 0.0 or apply_rate <= 0.0:
        return 0.0
    if not buff.apply_before_damage:
        return max(
            0.0,
            min(
                1.0,
                (apply_rate * float(buff.duration_turns) - turns) / apply_rate,
            ),
        )
    return min(1.0, apply_rate * float(buff.duration_turns) / turns)


@dataclass(frozen=True)
class ZoneBuff:
    """One buff's zone shifts, applied inside damage_zones when the buff is up."""

    id: str
    dmg_boost: float = 0.0
    vuln: float = 0.0
    def_reduction: float = 0.0
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    res_pen: float = 0.0
    weaken: float = 0.0

    def active_zones(self) -> frozenset[str]:
        return frozenset(
            name for name in _ZONE_SHIFT_FIELDS if float(getattr(self, name)) != 0.0
        )


@dataclass(frozen=True)
class ComboDamage:
    damage: float
    zone_product: float
    independence_note: str
    n_buffs: int
    enum_groups: tuple[tuple[str, ...], ...] = ()
    n_enum_groups: int = 0


def context_with(ctx: DamageContext, buffs: list[ZoneBuff]) -> DamageContext:
    """Stack every listed buff into a copy of ``ctx``. Zones are not pre-multiplied."""
    out = ctx
    for buff in buffs:
        out = replace(
            out,
            attacker_dmg_boost_pct=out.attacker_dmg_boost_pct + buff.dmg_boost,
            defender_vuln_pct=out.defender_vuln_pct + buff.vuln,
            attacker_def_reduction_pct=out.attacker_def_reduction_pct + buff.def_reduction,
            attacker_crit_rate=min(1.0, out.attacker_crit_rate + buff.crit_rate),
            attacker_crit_dmg=out.attacker_crit_dmg + buff.crit_dmg,
            attacker_res_pen_pct=out.attacker_res_pen_pct + buff.res_pen,
            weaken_pct=out.weaken_pct + buff.weaken,
        )
    return out


def _zone_components(buffs: list[ZoneBuff]) -> list[list[ZoneBuff]]:
    """Connected components: edge if two buffs share an additive zone field."""
    n = len(buffs)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    zones = [b.active_zones() for b in buffs]
    for i in range(n):
        for j in range(i + 1, n):
            if zones[i] & zones[j]:
                union(i, j)
    groups: dict[int, list[ZoneBuff]] = {}
    for i, buff in enumerate(buffs):
        groups.setdefault(find(i), []).append(buff)
    return list(groups.values())


def expected_combo_damage(
    ctx: DamageContext,
    buffs: list[ZoneBuff],
    coverages: dict[str, float],
    *,
    continuous: ZoneBuff | None = None,
) -> ComboDamage:
    """Expected damage with same-zone enumeration and cross-zone products.

    ``continuous`` is always on (not enumerated). Expected stack vulnerability
    uses this path: its value is added into the vulnerability zone before the
    binary buffs apply. Buffs that share an additive zone form one component
    and are enumerated together; independent components multiply.
    """
    if len(buffs) > _MAX_COMBO_BUFFS:
        names = ", ".join(b.id for b in buffs)
        raise ValueError(
            f"buff combination has {len(buffs)} buffs, above the limit of {_MAX_COMBO_BUFFS}: {names}"
        )
    ids = [b.id for b in buffs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate zone buff ids: {ids}")
    for buff in buffs:
        if buff.id not in coverages:
            raise KeyError(f"coverage missing for {buff.id}")

    base_ctx = context_with(ctx, [continuous]) if continuous is not None else ctx
    base, _zones = compute_damage(base_ctx)
    if not buffs:
        return ComboDamage(
            damage=float(base),
            zone_product=float(base),
            independence_note=COVERAGE_INDEPENDENCE_GAP,
            n_buffs=0,
            enum_groups=(),
            n_enum_groups=0,
        )

    components = _zone_components(buffs)
    enum_groups = tuple(tuple(b.id for b in group) for group in components)
    n_enum = sum(1 for group in components if len(group) >= 2)

    if base == 0.0:
        total = _enum_component(base_ctx, buffs, coverages)
        return ComboDamage(
            damage=total,
            zone_product=0.0,
            independence_note=COVERAGE_INDEPENDENCE_GAP,
            n_buffs=len(buffs),
            enum_groups=enum_groups,
            n_enum_groups=n_enum,
        )

    factor = 1.0
    for group in components:
        expected = _enum_component(base_ctx, group, coverages)
        factor *= float(expected) / float(base)
    total = float(base) * factor

    product = 1.0
    for buff in buffs:
        c = float(coverages[buff.id])
        only, _z = compute_damage(context_with(base_ctx, [buff]))
        product *= c * (float(only) / float(base)) + (1.0 - c)
    product *= float(base)

    return ComboDamage(
        damage=total,
        zone_product=product,
        independence_note=COVERAGE_INDEPENDENCE_GAP,
        n_buffs=len(buffs),
        enum_groups=enum_groups,
        n_enum_groups=n_enum,
    )


def _enum_component(
    ctx: DamageContext,
    buffs: list[ZoneBuff],
    coverages: dict[str, float],
) -> float:
    total = 0.0
    n = len(buffs)
    for mask in range(1 << n):
        prob = 1.0
        active: list[ZoneBuff] = []
        for i, buff in enumerate(buffs):
            c = float(coverages[buff.id])
            if mask & (1 << i):
                prob *= c
                active.append(buff)
            else:
                prob *= 1.0 - c
        if prob == 0.0:
            continue
        damage, _zones = compute_damage(context_with(ctx, active))
        total += prob * float(damage)
    return total


def _enemy_turns(enemy_speed: float) -> float:
    return float(enemy_speed) / 100.0


def expected_figment_stacks(
    rates: dict[str, dict[str, float]],
    *,
    duration_turns: float,
    max_stacks: float,
    enemy_speed: float,
    stack_holder_id: str | None = None,
) -> float:
    """Steady-state expected figment stacks from team skill consumption.

    Duration ticks on the holder's turns (Sparkle). Fallback ``enemy_speed/100``
    keeps legacy synthetic tests without a holder id.
    """
    trigger = sum(float(row["ns"]) for row in rates.values())
    if stack_holder_id and stack_holder_id in rates:
        row = rates[stack_holder_id]
        turns = float(row["nb"]) + float(row["ns"])
    else:
        turns = _enemy_turns(enemy_speed)
    if turns <= 0.0:
        return 0.0
    return min(float(max_stacks), trigger * float(duration_turns) / turns)


def cipher_coverage_on_enemy(
    rates: dict[str, dict[str, float]],
    *,
    applier_id: str,
    source_action: str,
    duration_turns: float,
    enemy_speed: float,
    recipient_id: str | None = None,
) -> float:
    """Cipher (谜诡) uptime. Prefer recipient turn clock (ally buff); else enemy."""
    if recipient_id and recipient_id in rates:
        return _uptime(
            applier_id,
            source_action,
            recipient_id,
            duration_turns,
            rates,
        )
    apply_rate = rates[applier_id][_ACTION_RATE[source_action]]
    turns = _enemy_turns(enemy_speed)
    if turns <= 0.0:
        return 0.0
    return min(1.0, float(apply_rate) * float(duration_turns) / turns)


def stack_vuln_value(stacks: float, cipher_c: float, spec: EnemyStackVuln) -> float:
    return float(stacks) * (
        float(spec.per_stack) + float(cipher_c) * float(spec.cipher_per_stack)
    )


def stack_trigger_rate(rates: dict[str, dict[str, float]]) -> float:
    return sum(float(row["ns"]) for row in rates.values())


def stack_rate_needed_for_cap(spec: EnemyStackVuln) -> float:
    """Skill-rate needed for expected stacks to reach the cap."""
    turns = _enemy_turns(spec.enemy_speed)
    if turns <= 0.0 or float(spec.stack_duration_turns) <= 0.0:
        return float("inf")
    return float(spec.max_stacks) * turns / float(spec.stack_duration_turns)


def stack_saturation(rates: dict[str, dict[str, float]], spec: EnemyStackVuln) -> float:
    needed = stack_rate_needed_for_cap(spec)
    if needed == 0.0 or needed == float("inf"):
        return 0.0
    return stack_trigger_rate(rates) / needed


def _stack_state(
    specs: list[EnemyStackVuln],
    stacks: dict[str, float],
    cipher_c: dict[str, float],
    rates: dict[str, dict[str, float]] | None = None,
) -> dict[str, StackVulnState]:
    state: dict[str, StackVulnState] = {}
    for spec in specs:
        s = float(stacks[spec.id])
        c = float(cipher_c[spec.id])
        sat = stack_saturation(rates, spec) if rates is not None else 0.0
        state[spec.id] = StackVulnState(
            stacks=s,
            cipher_coverage=c,
            vuln=stack_vuln_value(s, c, spec),
            saturation=sat,
        )
    return state


def _continuous_vuln_buff(
    specs: list[EnemyStackVuln],
    stacks: dict[str, float],
    cipher_c: dict[str, float],
) -> ZoneBuff | None:
    total = 0.0
    for spec in specs:
        total += stack_vuln_value(stacks[spec.id], cipher_c[spec.id], spec)
    if total == 0.0:
        return None
    return ZoneBuff(id="__continuous_stack_vuln__", vuln=total)


def _uses_zone_combo(
    buffs: list[ActionBuff],
    stack_vulns: list[EnemyStackVuln],
    action_contexts: dict[str, dict[str, DamageContext]] | None,
) -> bool:
    if any(b.has_zone_shifts() for b in buffs):
        return True
    return bool(stack_vulns) and action_contexts is not None


def _apply_zone_combo(
    characters: list[FlowCharacter],
    buffs: list[ActionBuff],
    coverages: dict[str, float],
    stack_vulns: list[EnemyStackVuln],
    stacks: dict[str, float],
    cipher_c: dict[str, float],
    action_contexts: dict[str, dict[str, DamageContext]],
) -> tuple[list[FlowCharacter], dict[str, StackVulnState], list[list[str]], list[str]]:
    state = _stack_state(stack_vulns, stacks, cipher_c)
    continuous = _continuous_vuln_buff(stack_vulns, stacks, cipher_c)
    notes: list[str] = [
        "幻相期望层数按连续量并入易伤乘区（常驻加项），不离散成 0/1/2/3。"
        "只靠期望推不出层数分布；连续加项与椒丘等二值易伤在乘区内相加后再枚举。"
    ]
    enum_groups: list[list[str]] = []
    seen_groups: set[tuple[str, ...]] = set()
    updates: dict[str, dict[str, float]] = {c.id: {} for c in characters}
    by_recipient: dict[str, list[ActionBuff]] = {}
    for buff in buffs:
        if not buff.has_zone_shifts():
            continue
        by_recipient.setdefault(buff.recipient_id, []).append(buff)

    for ch in characters:
        ctxs = action_contexts.get(ch.id)
        if ctxs is None:
            raise KeyError(f"action_contexts missing {ch.id}")
        all_recipient_buffs = by_recipient.get(ch.id, [])
        for field, action in (
            ("basic_damage", "basic"),
            ("skill_damage", "skill"),
            ("ult_damage", "ult"),
        ):
            action_buffs = [
                b
                for b in all_recipient_buffs
                if b.applies_to is None or action in b.applies_to
            ]
            zone_buffs = [b.as_zone_buff() for b in action_buffs]
            cov = {b.id: coverages[b.id] for b in action_buffs}
            raw = ctxs.get(action)
            if raw is None:
                updates[ch.id][field] = float(getattr(ch, field))
                continue
            ctx_list = raw if isinstance(raw, list) else [raw]
            if not ctx_list:
                updates[ch.id][field] = float(getattr(ch, field))
                continue
            total = 0.0
            product_total = 0.0
            n_enum = 0
            last_groups: tuple[tuple[str, ...], ...] = ()
            for ctx in ctx_list:
                result = expected_combo_damage(
                    ctx, zone_buffs, cov, continuous=continuous
                )
                total += float(result.damage)
                product_total += float(result.zone_product)
                n_enum = max(n_enum, int(result.n_enum_groups))
                last_groups = result.enum_groups
            updates[ch.id][field] = total
            for group in last_groups:
                key = tuple(group)
                if key not in seen_groups:
                    seen_groups.add(key)
                    enum_groups.append(list(group))
            if n_enum:
                notes.append(
                    f"{ch.id}.{action}: n_enum_groups={n_enum} "
                    f"enum={total:.6g} product={product_total:.6g} "
                    f"delta={total - product_total:.6g}"
                )

    # Legacy damage-only buffs still linear-blend on top when no zone shifts.
    damage_only = [b for b in buffs if not b.has_zone_shifts()]
    blended_base = [
        replace(ch, **updates[ch.id]) if updates[ch.id] else ch for ch in characters
    ]
    if damage_only:
        blended_base = _blend(blended_base, damage_only, coverages)
    return blended_base, state, enum_groups, notes


def _blend(
    characters: list[FlowCharacter],
    buffs: list[ActionBuff],
    coverages: dict[str, float],
) -> list[FlowCharacter]:
    by_id = {c.id: c for c in characters}
    touched: set[tuple[str, str]] = set()
    updates: dict[str, dict[str, float]] = {c.id: {} for c in characters}
    for buff in buffs:
        if buff.recipient_id not in by_id:
            raise KeyError(f"buff {buff.id} recipient {buff.recipient_id} not in the cast")
        if buff.applier_id not in by_id:
            raise KeyError(f"buff {buff.id} applier {buff.applier_id} not in the cast")
        c = coverages[buff.id]
        base = by_id[buff.recipient_id]
        for field in _DAMAGE_FIELDS:
            buffed = getattr(buff, field)
            if buffed is None:
                continue
            key = (buff.recipient_id, field)
            if key in touched:
                raise ValueError(f"two buffs set {field} on {buff.recipient_id}")
            touched.add(key)
            unbuffed = getattr(base, field)
            updates[buff.recipient_id][field] = c * float(buffed) + (1.0 - c) * float(unbuffed)
    out: list[FlowCharacter] = []
    for ch in characters:
        if updates[ch.id]:
            out.append(replace(ch, **updates[ch.id]))
        else:
            out.append(ch)
    return out


def _known(characters: list[FlowCharacter]) -> dict[str, FlowCharacter]:
    return {c.id: c for c in characters}


def _require_cast(by_id: dict[str, FlowCharacter], actor_id: str, what: str) -> None:
    if actor_id not in by_id:
        raise KeyError(f"{what} refers to {actor_id}, which is not in the cast")


def _apply_speeds(
    characters: list[FlowCharacter],
    speed_buffs: list[SpeedBuff],
    coverages: dict[str, float],
) -> tuple[list[FlowCharacter], dict[str, float]]:
    by_id = _known(characters)
    touched: set[str] = set()
    updates: dict[str, float] = {}
    for buff in speed_buffs:
        _require_cast(by_id, buff.recipient_id, f"speed buff {buff.id} recipient")
        _require_cast(by_id, buff.applier_id, f"speed buff {buff.id} applier")
        if buff.recipient_id in touched:
            raise ValueError(f"two speed buffs on {buff.recipient_id}")
        touched.add(buff.recipient_id)
        base = float(by_id[buff.recipient_id].speed)
        c = coverages[buff.id]
        updates[buff.recipient_id] = c * float(buff.speed) + (1.0 - c) * base
    speeds = {ch.id: updates.get(ch.id, float(ch.speed)) for ch in characters}
    out: list[FlowCharacter] = []
    for ch in characters:
        if ch.id in updates:
            out.append(replace(ch, speed=updates[ch.id]))
        else:
            out.append(ch)
    return out, speeds


def _adv_from_rates(
    advances: list[ActionAdvance],
    rates: dict[str, dict[str, float]],
    character_ids: list[str],
) -> dict[str, float]:
    adv = {cid: 0.0 for cid in character_ids}
    for item in advances:
        rate = 0.0
        for action in item.source_actions:
            rate += rates[item.applier_id][_ACTION_RATE[action]]
        adv[item.recipient_id] = adv[item.recipient_id] + rate * float(item.ratio)
    return adv


def iterate_state(
    characters: list[FlowCharacter],
    buffs: list[ActionBuff] | None = None,
    *,
    speed_buffs: list[SpeedBuff] | None = None,
    advances: list[ActionAdvance] | None = None,
    stack_vulns: list[EnemyStackVuln] | None = None,
    action_contexts: dict[str, dict[str, DamageContext]] | None = None,
    fixed_rates: dict[str, dict[str, float]] | None = None,
    apply_advance: bool = False,
    sp_other: float = 0.0,
    counters: list[CounterResource] | None = None,
    apply_overflow: bool = True,
    dot_streams: list[DotStream] | None = None,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> FixedPointResult:
    """Shared fixed point for coverage, speed, advance, and stack vulnerability.

    Coverages start at 0.5. Action advance starts at 0 and is updated only
    when ``apply_advance`` is true. Stack counts start at 0; cipher coverage
    starts at 0.5. Every tracked value must move by less than ``tol``. At
    ``max_iter`` the last values are returned with ``converged=False``.
    No damping.

    ``fixed_rates`` pins buff-only (or other) actions for the outer grid in
    ``enumerate_buff_actions``; every inner ``solve_flow`` call receives them.

    ``apply_overflow`` (): fold fill-then-spend average overshoot into
    energy / counter consume; extras refresh from the latest rates in-loop.
    """
    from hsrsim.analytic.overflow import build_overflow_report

    buffs = list(buffs or [])
    speed_buffs = list(speed_buffs or [])
    advances = list(advances or [])
    stack_vulns = list(stack_vulns or [])
    counters = list(counters or [])
    dot_streams = list(dot_streams or [])
    fixed_rates = {cid: dict(actions) for cid, actions in (fixed_rates or {}).items()}
    by_id = _known(characters)
    ids = [c.id for c in characters]
    for item in advances:
        _require_cast(by_id, item.applier_id, f"advance {item.id} applier")
        _require_cast(by_id, item.recipient_id, f"advance {item.id} recipient")
    for spec in stack_vulns:
        _require_cast(by_id, spec.cipher_applier_id, f"stack vuln {spec.id} cipher applier")
    for cid in fixed_rates:
        _require_cast(by_id, cid, "fixed_rates")

    cov_ids = [b.id for b in buffs] + [s.id for s in speed_buffs]
    if len(cov_ids) != len(set(cov_ids)):
        raise ValueError(f"duplicate buff ids: {cov_ids}")
    adv_ids = [a.id for a in advances]
    if len(adv_ids) != len(set(adv_ids)):
        raise ValueError(f"duplicate advance ids: {adv_ids}")
    stack_ids = [s.id for s in stack_vulns]
    if len(stack_ids) != len(set(stack_ids)):
        raise ValueError(f"duplicate stack vuln ids: {stack_ids}")

    base_speeds = {c.id: float(c.speed) for c in characters}
    zero_adv = {cid: 0.0 for cid in ids}
    empty_stacks = {
        s.id: StackVulnState(stacks=0.0, cipher_coverage=0.0, vuln=0.0, saturation=0.0)
        for s in stack_vulns
    }
    use_combo = _uses_zone_combo(buffs, stack_vulns, action_contexts)
    if any(b.has_zone_shifts() for b in buffs) and action_contexts is None:
        raise ValueError("action_contexts is required when ActionBuffs carry zone shifts")
    needs_loop = bool(
        buffs
        or speed_buffs
        or stack_vulns
        or (apply_advance and advances)
        or apply_overflow
        or dot_streams
    )
    energy_extra: dict[str, float] = {}
    counter_extra: dict[str, float] = {}
    overflow_report = None

    def _solve_with(
        sped: list[FlowCharacter],
        *,
        adv: dict[str, float],
        e_extra: dict[str, float],
        c_extra: dict[str, float],
    ) -> FlowSolution:
        return solve_flow(
            sped,
            sp_other=float(sp_other),
            counters=counters or None,
            extra_turns=adv if apply_advance else None,
            fixed_rates=fixed_rates or None,
            energy_consume_extra=e_extra or None,
            counter_consume_extra=c_extra or None,
        )

    def _enemy_vuln_for_dot(
        coverages: dict[str, float],
        stack_state: dict[str, StackVulnState] | None,
    ) -> float:
        """Expected defender vuln on DoT ticks (no crit — DamageType.DOT matrix)."""
        vuln = 0.0
        seen_ashen = False
        seen_unarmored = False
        for b in buffs:
            bid = str(b.id)
            if bid.startswith("ashen_roast__") and not seen_ashen:
                vuln += float(b.vuln) * float(coverages.get(bid, 0.0))
                seen_ashen = True
            elif "lc_unarmored" in bid and not seen_unarmored:
                # One enemy Unarmored; ActionBuff is replicated per attacker.
                vuln += float(b.vuln) * float(coverages.get(bid, 0.0))
                seen_unarmored = True
        if stack_state:
            for st in stack_state.values():
                vuln += float(st.vuln)
        return vuln

    def _with_dots(
        sol: FlowSolution,
        *,
        coverages: dict[str, float] | None = None,
        stack_state: dict[str, StackVulnState] | None = None,
    ) -> tuple[FlowSolution, float, dict[str, dict[str, float]]]:
        if not dot_streams:
            return sol, 0.0, {}
        cov_map = coverages or {}
        extra_vuln = _enemy_vuln_for_dot(cov_map, stack_state)
        vuln_mult = 1.0 + float(extra_vuln)
        breakdown: dict[str, dict[str, float]] = {}
        total_dot = 0.0
        for stream in dot_streams:
            dpr0, tick_rate, cov = dot_damage_per_100_av(stream, sol.rates)
            dpr = dpr0 * vuln_mult
            breakdown[stream.id] = {
                "damage_per_100_av": dpr,
                "tick_rate_per_100_av": tick_rate,
                "coverage": cov,
                "tick_damage_base": float(stream.tick_damage),
                "enemy_vuln": extra_vuln,
                "vuln_mult": vuln_mult,
                "tick_damage_effective": float(stream.tick_damage) * vuln_mult,
            }
            total_dot += dpr
        if total_dot <= 0.0:
            return sol, 0.0, breakdown
        sol.total_damage = float(sol.total_damage) + float(total_dot)
        return sol, float(total_dot), breakdown

    if not needs_loop:
        sol = _solve_with(characters, adv=zero_adv, e_extra={}, c_extra={})
        sol, dot_total, dot_bd = _with_dots(sol, coverages={}, stack_state=None)
        return FixedPointResult(
            solution=sol,
            coverages={},
            advances=dict(zero_adv),
            speeds=base_speeds,
            iterations=1,
            converged=True,
            history=[],
            stack_vulns=empty_stacks if stack_vulns else None,
            dot_damage_per_100_av=dot_total,
            dot_breakdown=dot_bd or None,
        )

    current_c = {cid: 0.5 for cid in cov_ids}
    current_adv = dict(zero_adv)
    current_stacks = {s.id: 0.0 for s in stack_vulns}
    current_cipher = {s.id: 0.5 for s in stack_vulns}
    history: list[dict[str, float]] = [dict(current_c)] if cov_ids else []
    sol: FlowSolution | None = None
    speeds = dict(base_speeds)
    last_stack_state = empty_stacks
    last_enum_groups: list[list[str]] = []
    last_combo_notes: list[str] = []
    for k in range(1, max_iter + 1):
        if use_combo:
            stacked, last_stack_state, last_enum_groups, last_combo_notes = _apply_zone_combo(
                characters,
                buffs,
                current_c,
                stack_vulns,
                current_stacks,
                current_cipher,
                action_contexts or {},
            )
        else:
            stacked = _blend(characters, buffs, current_c)
            if stack_vulns:
                continuous = _continuous_vuln_buff(
                    stack_vulns, current_stacks, current_cipher
                )
                factor = 1.0 + (0.0 if continuous is None else continuous.vuln)
                if factor != 1.0:
                    stacked = [
                        replace(
                            ch,
                            basic_damage=float(ch.basic_damage) * factor,
                            skill_damage=float(ch.skill_damage) * factor,
                            ult_damage=float(ch.ult_damage) * factor,
                        )
                        for ch in stacked
                    ]
                last_stack_state = _stack_state(
                    stack_vulns, current_stacks, current_cipher
                )
            else:
                last_stack_state = empty_stacks
            last_enum_groups = []
            last_combo_notes = []
        sped, speeds = _apply_speeds(stacked, speed_buffs, current_c)
        sol = _solve_with(
            sped,
            adv=current_adv,
            e_extra=energy_extra,
            c_extra=counter_extra,
        )
        nxt_c = {b.id: _coverage(b, sol.rates) for b in buffs}
        for sb in speed_buffs:
            nxt_c[sb.id] = _uptime(
                sb.applier_id,
                sb.source_action,
                sb.recipient_id,
                sb.duration_turns,
                sol.rates,
            )
        nxt_adv = (
            _adv_from_rates(advances, sol.rates, ids) if apply_advance else dict(zero_adv)
        )
        nxt_stacks = {
            s.id: expected_figment_stacks(
                sol.rates,
                duration_turns=s.stack_duration_turns,
                max_stacks=s.max_stacks,
                enemy_speed=s.enemy_speed,
                stack_holder_id=s.stack_holder_id,
            )
            for s in stack_vulns
        }
        nxt_cipher = {
            s.id: cipher_coverage_on_enemy(
                sol.rates,
                applier_id=s.cipher_applier_id,
                source_action=s.cipher_source_action,
                duration_turns=s.cipher_duration_turns,
                enemy_speed=s.enemy_speed,
                recipient_id=s.cipher_recipient_id,
            )
            for s in stack_vulns
        }
        nxt_energy_extra: dict[str, float] = {}
        nxt_counter_extra: dict[str, float] = {}
        if apply_overflow:
            overflow_report = build_overflow_report(
                characters, sol.rates, counters=counters
            )
            nxt_energy_extra = overflow_report.energy_extra()
            nxt_counter_extra = overflow_report.counter_extra()
            sol.overflow = overflow_report
        if cov_ids:
            history.append(nxt_c)
        c_ok = all(abs(nxt_c[i] - current_c[i]) < tol for i in cov_ids)
        a_ok = all(abs(nxt_adv[i] - current_adv[i]) < tol for i in ids)
        s_ok = all(abs(nxt_stacks[i] - current_stacks[i]) < tol for i in stack_ids)
        z_ok = all(abs(nxt_cipher[i] - current_cipher[i]) < tol for i in stack_ids)
        e_ok = all(
            abs(nxt_energy_extra.get(i, 0.0) - energy_extra.get(i, 0.0)) < tol
            for i in set(nxt_energy_extra) | set(energy_extra)
        )
        k_ok = all(
            abs(nxt_counter_extra.get(i, 0.0) - counter_extra.get(i, 0.0)) < tol
            for i in set(nxt_counter_extra) | set(counter_extra)
        )
        if c_ok and a_ok and s_ok and z_ok and e_ok and k_ok:
            if use_combo:
                stacked, last_stack_state, last_enum_groups, last_combo_notes = _apply_zone_combo(
                    characters,
                    buffs,
                    nxt_c,
                    stack_vulns,
                    nxt_stacks,
                    nxt_cipher,
                    action_contexts or {},
                )
            last_stack_state = _stack_state(
                stack_vulns, nxt_stacks, nxt_cipher, sol.rates
            ) if stack_vulns else None
            before = None
            if apply_overflow:
                before = _solve_with(
                    sped, adv=nxt_adv, e_extra={}, c_extra={}
                )
            sol, dot_total, dot_bd = _with_dots(
                sol,
                coverages=nxt_c,
                stack_state=last_stack_state
                if isinstance(last_stack_state, dict)
                else None,
            )
            return FixedPointResult(
                solution=sol,
                coverages=nxt_c,
                advances=nxt_adv,
                speeds=speeds,
                iterations=k,
                converged=True,
                history=history,
                stack_vulns=last_stack_state,
                enum_groups=last_enum_groups or None,
                combo_notes=last_combo_notes or None,
                solution_before_overflow=before,
                overflow_report=overflow_report,
                dot_damage_per_100_av=dot_total,
                dot_breakdown=dot_bd or None,
            )
        current_c = nxt_c
        current_adv = nxt_adv
        current_stacks = nxt_stacks
        current_cipher = nxt_cipher
        energy_extra = nxt_energy_extra
        counter_extra = nxt_counter_extra
    assert sol is not None
    if use_combo:
        stacked, last_stack_state, last_enum_groups, last_combo_notes = _apply_zone_combo(
            characters,
            buffs,
            current_c,
            stack_vulns,
            current_stacks,
            current_cipher,
            action_contexts or {},
        )
    last_stack_state = (
        _stack_state(stack_vulns, current_stacks, current_cipher, sol.rates)
        if stack_vulns
        else None
    )
    before = None
    if apply_overflow:
        before = _solve_with(
            sped, adv=current_adv, e_extra={}, c_extra={}
        )
    sol, dot_total, dot_bd = _with_dots(
        sol,
        coverages=current_c,
        stack_state=last_stack_state
        if isinstance(last_stack_state, dict)
        else None,
    )
    return FixedPointResult(
        solution=sol,
        coverages=current_c,
        advances=current_adv,
        speeds=speeds,
        iterations=max_iter,
        converged=False,
        history=history,
        stack_vulns=last_stack_state,
        enum_groups=last_enum_groups or None,
        combo_notes=last_combo_notes or None,
        solution_before_overflow=before,
        overflow_report=overflow_report,
        dot_damage_per_100_av=dot_total,
        dot_breakdown=dot_bd or None,
    )


def iterate_coverage(
    characters: list[FlowCharacter],
    buffs: list[ActionBuff],
    *,
    sp_other: float = 0.0,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> CoverageResult:
    """Start every coverage at 0.5 and iterate until all changes are < tol.

    Stops at ``max_iter`` with ``converged=False`` if the tolerance is not
    met. The returned coverages are the last iteration, not a truncated
    average. This is the buff-only view of ``iterate_state``.
    """
    state = iterate_state(
        characters,
        buffs,
        sp_other=sp_other,
        max_iter=max_iter,
        tol=tol,
    )
    return CoverageResult(
        solution=state.solution,
        coverages=state.coverages,
        iterations=state.iterations,
        converged=state.converged,
        history=state.history,
    )
