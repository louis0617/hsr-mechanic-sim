"""Event trigger bus (): emit, depth limit, Acheron R1–R5, stack transfer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from hsrsim.simulator.combat_rules import (
    ActionTriggerContext,
    CombatRules,
    StackTransferRule,
)
from hsrsim.simulator.energy import grant_spbase_on_action
from hsrsim.simulator.queries import effect_stacks, select_unit
from hsrsim.simulator.state import BattleState, CharacterState
from hsrsim.simulator.types import Effect, EffectModifier, EffectTarget
from hsrsim.rules.counter_gains import (
    CounterGainSpec,
    l1_l2_rule_fingerprint,
    load_counter_gain_specs,
    zone_proc_counts_for_dream,
)
from hsrsim.simulator.field_zone import (
    JIAOQIU_ULT_ZONE_ID,
    JIAOQIU_ZONE_DURATION,
    JIAOQIU_ZONE_MAX_TRIGGERS,
    JIAOQIU_ZONE_PROC_CHANCE,
    JIAOQIU_ZONE_ULT_VULN,
    FieldZone,
)
from hsrsim.rules.wiring import lc_param_list

CRIMSON_KNOT_ID = "crimson_knot"
SLASHED_DREAM_ID = "nihility_stacks"
QUAD_ASC_ID = "quadrivalent_ascendance"
QUAD_ASC_MAX = 3
# Trace 1308101 赤鬼 ParamList [[5, 3]]: battle-start dream/knot + max quad stacks.
RED_GHOST_BATTLE_START = 5
SPARKLE_FIGMENT_ID = "sparkle_figment"
ASHEN_ROAST_ID = "ashen_roast"
LC_MIRAGE_ID = "lc_mirage_fizzle"
LC_UNARMORED_ID = "lc_unarmored"
LC_EXPOSED_ID = "lc_exposed"  # 21015 攻陷
LC_TREND_BURN_ID = "lc_trend_burn"  # 21016 宇宙市场趋势灼烧
LC_23023_FUA_VULN_ID = "lc_23023_fua_vuln"
LC_MASK_ALLY_ID = "lc_mask"
LC_BLAZE_VAR = "lc_blaze_stacks"
LC_23023_CD_ID = "lc_23023_cd"
LC_23023_VULN_ID = "lc_23023_vuln"
AVENTURINE_CHIP_ID = "aventurine_chip"
AVENTURINE_UNNERVED_ID = "aventurine_unnerved"
BLIND_BET_ID = "blind_bet"
BLIND_BET_EXTRA_CD_ID = "blind_bet_extra_cd"
BLIND_BET_FUA_COST = 7.0
BLIND_BET_MAX = 10.0
# Talent L10 ParamList [1, 7, 0.25, 0.5, 2]: extra Blind Bet amount / FUA hits / MV / RES / ICD.
BLIND_BET_EXTRA_GAIN = 1.0
BLIND_BET_EXTRA_ICD = 2.0
# Trace 1304103: FUA grants Fortified Wager (existence+duration only).
CHIP_TRACE_DURATION = 3
AVENTURINE_FUA_ACTION_ID = "aventurine_fua"

#how an effect reached the field (R1 only counts action_cast).
APPLY_SOURCE_ACTION_CAST = "action_cast"
APPLY_SOURCE_TRIGGERED = "triggered"

LogFn = Callable[[str, dict[str, Any]], None]


def crimson_knot_template(*, stacks: int = 1) -> Effect:
    return Effect(
        id=CRIMSON_KNOT_ID,
        name="集真赤",
        name_en="Crimson Knot",
        is_buff=False,
        target=EffectTarget.SINGLE_ENEMY,
        duration_turns=-1,
        max_stacks=9,  #hard cap 9 (not 99)
        current_stacks=max(1, min(9, stacks)),
        modifiers=[],
    )


def is_skill_cast_kind(action_kind: str, rules: CombatRules) -> bool:
    if action_kind in ("basic_attack", "skill", "ultimate"):
        return True
    if action_kind == "follow_up" and rules.followup_counts_as_action:
        return True
    return False


def _defender_has_wearer_dot(defender: CharacterState, wearer_id: str) -> bool:
    """True if defender has a DoT inflicted by wearer (dot_instance + source match)."""
    for e in defender.active_effects:
        if e.dot_instance is None:
            continue
        src = e.source_id or e.dot_source_id
        if src == wearer_id:
            return True
    return False


@dataclass
class EventBus:
    state: BattleState
    rules: CombatRules = field(default_factory=CombatRules)
    transfer_rules: list[StackTransferRule] = field(default_factory=list)
    log: LogFn | None = None
    # Suppress crimson knot / talent during Acheron ult (R4).
    acheron_ult_active: bool = False
    depth: int = 0
    depth_exceeded_count: int = 0
    action_ctx: ActionTriggerContext | None = None
    acheron_e0_enabled: bool = True
    #shared counter-gain specs (same JSON as L1 adapter).
    counter_gain_specs: list[CounterGainSpec] = field(default_factory=list)
    # Optional RNG for LC EHR rolls (set by Engine).
    rng: Any = None
    _handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not self.counter_gain_specs:
            self.counter_gain_specs = load_counter_gain_specs()
        if self.acheron_e0_enabled and not any(
            r.effect_id == CRIMSON_KNOT_ID for r in self.transfer_rules
        ):
            self.transfer_rules.append(
                StackTransferRule(
                    effect_id=CRIMSON_KNOT_ID,
                    direction="max",
                    requires_ally_id="acheron",
                )
            )
        self.on("effect_applied", self._on_effect_applied)
        self.on("action_resolved", self._on_action_resolved)
        self.on("action_used", self._on_action_used)
        self.on("enemy_killed", self._on_enemy_killed)
        self.on("sp_consumed", self._on_sp_consumed)
        self.on("sp_gained", self._on_sp_gained)
        self.on("toughness_recovered", self._on_toughness_recovered)
        self.on("turn_start", self._on_turn_start)
        self.on("damage_hit", self._on_damage_hit)
        self.on("battle_start", self._on_battle_start)
        self.on("hit_taken", self._on_hit_taken)
        self.on("effect_applied", self._on_shield_provided_for_lc23023)

    def registered_counter_gain_fingerprint(
        self, resource: str = SLASHED_DREAM_ID
    ) -> list[dict[str, Any]]:
        """Structural view of L2-registered counter gains (for L1/L2 equality tests)."""
        specs = [s for s in self.counter_gain_specs if s.resource == resource]
        return l1_l2_rule_fingerprint(specs)

    def _spec_by_hook(self, hook: str) -> CounterGainSpec | None:
        for s in self.counter_gain_specs:
            if s.l2_hook == hook:
                return s
        return None

    def on(self, event: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def begin_action(
        self,
        *,
        actor_id: str,
        action_id: str,
        action_kind: str,
        turn_kind: str = "NORMAL",
        target_id: str | None = None,
    ) -> None:
        self.action_ctx = ActionTriggerContext(
            actor_id=actor_id,
            action_id=action_id,
            action_kind=action_kind,
            turn_kind=turn_kind,
            target_id=target_id,
        )
        if action_kind == "ultimate" and actor_id == "acheron":
            self.acheron_ult_active = True

    def end_action(self) -> None:
        if self.action_ctx and self.action_ctx.action_kind == "ultimate":
            if self.action_ctx.actor_id == "acheron":
                self.acheron_ult_active = False
        self.action_ctx = None

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        payload = dict(payload or {})
        if self.depth >= self.rules.max_trigger_depth:
            self.depth_exceeded_count += 1
            if self.log:
                self.log(
                    "trigger_depth_exceeded",
                    {"event": event, "depth": self.depth, "payload": payload},
                )
            return
        self.depth += 1
        try:
            for handler in list(self._handlers.get(event, [])):
                handler(payload)
        finally:
            self.depth -= 1

    def _find_acheron(self) -> CharacterState | None:
        for a in self.state.allies:
            if a.char.id == "acheron" and a.is_alive:
                return a
        return None

    def _dream_cap(self, acheron: CharacterState) -> float:
        for v in acheron.char.build.variables:
            if v.id == SLASHED_DREAM_ID:
                return float(v.max_value)
        return 9.0

    def _grant_dream(self, acheron: CharacterState, *, source: str) -> None:
        cur = float(acheron.variables.get(SLASHED_DREAM_ID, 0.0))
        acheron.variables[SLASHED_DREAM_ID] = min(
            self._dream_cap(acheron), cur + 1.0
        )
        if self.log:
            self.log(
                "variable_changed",
                {
                    "actor_id": acheron.char.id,
                    "variable": SLASHED_DREAM_ID,
                    "delta": 1.0,
                    "new_value": acheron.variables[SLASHED_DREAM_ID],
                    "source": source,
                },
            )

    def _try_dream_knot_package(
        self,
        acheron: CharacterState,
        *,
        source: str,
        knot_target: CharacterState | None,
        from_trigger: str,
    ) -> bool:
        """+1 dream and +1 knot while under cap; at dream=9 overflow → 四相断我.

        （撤销 g-2「满层仍挂集真赤」）:
        - dream < 9: +1 dream + (if target) +1 knot（knot 上限 9）
        - dream == 9: **不**再挂集真赤；溢出 → +1 四相（cap 3），否则 waste
        依据：残梦与集真赤同步；叠满后不再叠加，满后再试挂 → 四相。
        """
        cap = self._dream_cap(acheron)
        cur = float(acheron.variables.get(SLASHED_DREAM_ID, 0.0))

        if cur < cap:
            if knot_target is not None and knot_target.is_alive:
                self._apply_knot(
                    knot_target,
                    source_id=acheron.char.id,
                    from_trigger=from_trigger,
                )
            self._grant_dream(acheron, source=source)
            return True

        # Dream at hard cap: no new knot; overflow into Quadrivalent Ascendance.
        quad = float(acheron.variables.get(QUAD_ASC_ID, 0.0))
        if quad < QUAD_ASC_MAX:
            acheron.variables[QUAD_ASC_ID] = quad + 1.0
            if self.log:
                self.log(
                    "variable_changed",
                    {
                        "actor_id": acheron.char.id,
                        "variable": QUAD_ASC_ID,
                        "delta": 1.0,
                        "new_value": acheron.variables[QUAD_ASC_ID],
                        "source": source,
                    },
                )
            return True

        if self.log:
            self.log(
                "dream_overflow_wasted",
                {
                    "actor_id": acheron.char.id,
                    "dream": cur,
                    "cap": cap,
                    "quad": quad,
                    "quad_max": QUAD_ASC_MAX,
                    "source": source,
                },
            )
            self.log(
                "dream_knot_capped",
                {
                    "actor_id": acheron.char.id,
                    "dream": cur,
                    "cap": cap,
                    "source": source,
                    "knot_still_applied": False,
                },
            )
        return False

    def _convert_quad_after_ult(self, acheron: CharacterState) -> None:
        """After ultimate resolves: N 四相 → N 残梦 + N 集真赤 on a random/selected enemy."""
        n = int(float(acheron.variables.get(QUAD_ASC_ID, 0.0)))
        if n <= 0:
            return
        acheron.variables[QUAD_ASC_ID] = 0.0
        if self.log:
            self.log(
                "variable_changed",
                {
                    "actor_id": acheron.char.id,
                    "variable": QUAD_ASC_ID,
                    "delta": -float(n),
                    "new_value": 0.0,
                    "source": "acheron_ult_quad_convert",
                },
            )
        knot_target = select_unit(
            self.state,
            "enemies",
            order_by=f"effect_stacks:{CRIMSON_KNOT_ID}:desc",
            tie_break=self.rules.knot_tie_break,
        )
        for _ in range(n):
            self._grant_dream(acheron, source="acheron_ult_quad_convert")
            if knot_target is not None and knot_target.is_alive:
                self._apply_knot(
                    knot_target,
                    source_id=acheron.char.id,
                    from_trigger="acheron_ult_quad_convert",
                )

    def _apply_knot(
        self,
        target: CharacterState,
        *,
        source_id: str,
        from_trigger: str,
    ) -> None:
        existing = next(
            (e for e in target.active_effects if e.id == CRIMSON_KNOT_ID), None
        )
        was_refresh = existing is not None
        stacks_before = int(existing.current_stacks) if existing else 0
        if stacks_before >= 9:
            # Hard cap 9: refuse without stacking .
            if self.log:
                self.log(
                    "crimson_knot_capped",
                    {
                        "target_id": target.char.id,
                        "stacks": stacks_before,
                        "from_trigger": from_trigger,
                    },
                )
            return
        target.apply_effect(crimson_knot_template(stacks=1), source_id=source_id)
        stacks_after = effect_stacks(target, CRIMSON_KNOT_ID)
        self.emit(
            "effect_applied",
            {
                "effect_id": CRIMSON_KNOT_ID,
                "is_debuff": True,
                "source_id": source_id,
                "target_id": target.char.id,
                "target_side": "enemy",
                "was_refresh": was_refresh,
                "stacks_before": stacks_before,
                "stacks_after": stacks_after,
                "from_trigger": from_trigger,
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def _on_effect_applied(self, payload: dict[str, Any]) -> None:
        # E2 Pioneer 4pc: wearer applies debuff → CD effect doubles for 1 turn.
        if (
            payload.get("is_debuff")
            and payload.get("target_side") == "enemy"
            and payload.get("source_id") == "acheron"
        ):
            ach = self.state.find_char("acheron")
            if ach is not None:
                setattr(ach, "_pioneer_double_active", True)
                setattr(ach, "_pioneer_double_round", int(self.state.round_number))
        if not self.acheron_e0_enabled:
            return
        ctx = self.action_ctx
        if ctx is None:
            return
        if not is_skill_cast_kind(ctx.action_kind, self.rules):
            return
        #no apply_source gate. Any in-window enemy debuff (incl. future
        # zone) shares per-action cap 1 via talent_settled.
        #figment is a Sparkle self-buff — never enemy effect_applied,
        # so R1 excludes it naturally (no FIGMENT switch).
        if not payload.get("is_debuff"):
            return
        if payload.get("target_side") != "enemy":
            return
        if payload.get("was_refresh") and not self.rules.debuff_refresh_counts:
            return
        if self.acheron_ult_active:
            return
        # Crimson Knot is the talent's payload, not a talent-triggering infliction.
        if payload.get("effect_id") == CRIMSON_KNOT_ID:
            return
        # E1.2: zone enemy-action ashen — gated by ZONE_PROC_COUNTS_FOR_DREAM.
        if payload.get("from_trigger") == "zone_proc":
            if not zone_proc_counts_for_dream(
                override=self.rules.zone_proc_counts_for_dream
            ):
                return
        target_id = payload.get("target_id")
        if not target_id:
            return
        ctx.note_debuff_target(str(target_id))

    def _on_toughness_recovered(self, payload: dict[str, Any]) -> None:
        """Mirror BattleState recover emit into the engine event log."""
        if self.log:
            self.log("toughness_recovered", dict(payload))

    def _on_sp_consumed(self, payload: dict[str, Any]) -> None:
        """: team SP spend → Sparkle gains sparkle_figment stacks.

        Figment is a self-buff on Sparkle. Enemy vuln is an aura read at damage
        time — no enemy effect_applied (R1 naturally does not count).
        """
        amount = int(payload.get("amount") or 0)
        if amount <= 0:
            return
        sparkle, tmpl = self._sparkle_figment_holder_and_template()
        if sparkle is None or tmpl is None:
            return
        consumer_id = str(payload.get("consumer_id") or "unknown")
        for _ in range(amount):
            stacks_before = effect_stacks(sparkle, SPARKLE_FIGMENT_ID)
            result = sparkle.apply_effect(tmpl, source_id="sparkle")
            self.emit(
                "effect_applied",
                {
                    "effect_id": SPARKLE_FIGMENT_ID,
                    "is_debuff": False,
                    "source_id": "sparkle",
                    "target_id": sparkle.char.id,
                    "target_side": "ally",
                    "was_refresh": result["was_refresh"],
                    "stacks_before": stacks_before,
                    "stacks_after": result["stacks_after"],
                    "apply_source": APPLY_SOURCE_TRIGGERED,
                    "consumer_id": consumer_id,
                },
            )

    def _sparkle_figment_holder_and_template(
        self,
    ) -> tuple[CharacterState | None, Effect | None]:
        """On-field Sparkle + figment template; (None, None) if absent/dead."""
        sparkle = None
        for ally in self.state.allies:
            if ally.char.id == "sparkle" and ally.is_alive:
                sparkle = ally
                break
        if sparkle is None:
            return None, None
        for eff in sparkle.char.build.effects:
            if eff.id == SPARKLE_FIGMENT_ID:
                return sparkle, Effect(
                    id=eff.id,
                    name=eff.name,
                    name_en=eff.name_en,
                    is_buff=True,
                    target=EffectTarget.SELF,
                    duration_turns=eff.duration_turns,
                    max_stacks=eff.max_stacks,
                    current_stacks=1,
                    modifiers=list(eff.modifiers),
                    tick_timing=eff.tick_timing,
                )
        return sparkle, None

    def _sparkle_figment_template(self) -> Effect | None:
        """Compat: figment template only (holder ignored)."""
        _, tmpl = self._sparkle_figment_holder_and_template()
        return tmpl

    def _on_action_used(self, payload: dict[str, Any]) -> None:
        """E1.2: jiaoqiu ult equalize+open zone; enemy action → zone ashen proc."""
        actor_id = str(payload.get("actor_id") or "")
        action_id = str(payload.get("action_id") or "")
        action_kind = str(payload.get("action_kind") or "")
        actor = self.state.find_char(actor_id)
        if actor is None:
            return
        if actor_id == "jiaoqiu" and action_kind == "ultimate":
            self._jiaoqiu_ult_equalize_and_open_zone(actor)
            return
        if actor in self.state.enemies:
            self._try_zone_ashen_proc(actor)

    def _ashen_template(self) -> Effect | None:
        jq = self.state.find_char("jiaoqiu")
        if jq is None:
            return None
        for eff in jq.char.build.effects:
            if eff.id == ASHEN_ROAST_ID:
                return eff.model_copy(deep=True)
        return None

    def _jiaoqiu_ult_equalize_and_open_zone(self, jiaoqiu: CharacterState) -> None:
        """原文顺序：统一烬煨最高层 → 开启结界 →（随后引擎结算伤害，吃结界终结技易伤）。"""
        tmpl = self._ashen_template()
        if tmpl is not None:
            alive = [e for e in self.state.enemies if e.is_alive]
            hi = max((effect_stacks(e, ASHEN_ROAST_ID) for e in alive), default=0)
            if hi > 0:
                for e in alive:
                    before = effect_stacks(e, ASHEN_ROAST_ID)
                    if before == hi:
                        # Refresh duration at equalized stacks.
                        e.set_effect_stacks(tmpl, hi, source_id=jiaoqiu.char.id)
                        continue
                    result = e.set_effect_stacks(
                        tmpl, hi, source_id=jiaoqiu.char.id
                    )
                    self.emit(
                        "effect_applied",
                        {
                            "effect_id": ASHEN_ROAST_ID,
                            "is_debuff": True,
                            "source_id": jiaoqiu.char.id,
                            "target_id": e.char.id,
                            "target_side": "enemy",
                            "was_refresh": result["was_refresh"],
                            "stacks_before": result["stacks_before"],
                            "stacks_after": result["stacks_after"],
                            "apply_source": APPLY_SOURCE_ACTION_CAST,
                            "from_trigger": "jiaoqiu_ult_equalize",
                        },
                    )
        # Open / refresh zone (ParamList L10: dur=3, p=0.6, ult_vuln=0.15, max=6).
        zone = FieldZone(
            id=JIAOQIU_ULT_ZONE_ID,
            source_id=jiaoqiu.char.id,
            remaining_turns=JIAOQIU_ZONE_DURATION,
            proc_base_chance=JIAOQIU_ZONE_PROC_CHANCE,
            proc_effect_id=ASHEN_ROAST_ID,
            max_triggers=JIAOQIU_ZONE_MAX_TRIGGERS,
            triggers_left=JIAOQIU_ZONE_MAX_TRIGGERS,
            ult_vuln=JIAOQIU_ZONE_ULT_VULN,
            per_enemy_proc_round={},
        )
        self.state.active_zones = [
            z for z in self.state.active_zones if z.id != JIAOQIU_ULT_ZONE_ID
        ]
        self.state.active_zones.append(zone)
        if self.log:
            self.log(
                "zone_opened",
                {
                    "zone_id": zone.id,
                    "source_id": zone.source_id,
                    "remaining_turns": zone.remaining_turns,
                    "triggers_left": zone.triggers_left,
                    "ult_vuln": zone.ult_vuln,
                    "proc_base_chance": zone.proc_base_chance,
                },
            )

    def _try_zone_ashen_proc(self, enemy: CharacterState) -> None:
        """结界：敌方行动时基础概率叠烬煨；全局≤6 ∩ 每敌每回合≤1。"""
        zones = [
            z
            for z in self.state.active_zones
            if z.is_active()
            and z.proc_effect_id == ASHEN_ROAST_ID
            and z.triggers_left > 0
        ]
        if not zones:
            return
        # One proc attempt per enemy action across overlapping zones (paper: one zone).
        zone = zones[0]
        eid = enemy.char.id
        if zone.per_enemy_proc_round.get(eid) == self.state.round_number:
            return
        source = self.state.find_char(zone.source_id)
        if source is None or not source.is_alive:
            return
        tmpl = self._ashen_template()
        if tmpl is None:
            return
        from hsrsim.simulator.ehr import debuff_hit_chance

        ehr = float(source.char.build.stats.ehr)
        effect_res = float(enemy.char.build.stats.effect_res)
        final_chance = debuff_hit_chance(float(zone.proc_base_chance), ehr, effect_res)
        roll = self.rng.random() if self.rng is not None else 0.0
        if roll > final_chance:
            if self.log:
                self.log(
                    "effect_resisted",
                    {
                        "source": source.char.id,
                        "target_id": eid,
                        "effect_id": ASHEN_ROAST_ID,
                        "base_chance": zone.proc_base_chance,
                        "ehr": ehr,
                        "target_effect_res": effect_res,
                        "final_chance": final_chance,
                        "roll": roll,
                        "from_trigger": "zone_proc",
                    },
                )
            # Still consume per-enemy-per-round slot? 原文：触发成功才算；失败可再试同回合？
            # 「每个敌方目标每回合只能触发 1 次」— 指效果触发，失败不占。不写 round mark.
            return
        result = enemy.apply_effect(tmpl, source_id=source.char.id)
        zone.triggers_left -= 1
        zone.per_enemy_proc_round[eid] = self.state.round_number
        self.emit(
            "effect_applied",
            {
                "effect_id": ASHEN_ROAST_ID,
                "is_debuff": True,
                "source_id": source.char.id,
                "target_id": eid,
                "target_side": "enemy",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
                "from_trigger": "zone_proc",
            },
        )
        if self.log:
            self.log(
                "zone_proc",
                {
                    "zone_id": zone.id,
                    "enemy_id": eid,
                    "triggers_left": zone.triggers_left,
                    "counts_for_dream": bool(
                        zone_proc_counts_for_dream(
                            override=self.rules.zone_proc_counts_for_dream
                        )
                    ),
                },
            )

    def _tick_zones_on_source_turn(self, unit_id: str) -> None:
        """结界持续：源角色回合开始 −1。"""
        kept: list[FieldZone] = []
        for z in self.state.active_zones:
            if z.source_id != unit_id:
                kept.append(z)
                continue
            z.remaining_turns -= 1
            if self.log:
                self.log(
                    "zone_tick",
                    {
                        "zone_id": z.id,
                        "source_id": z.source_id,
                        "remaining_turns": z.remaining_turns,
                    },
                )
            if z.remaining_turns > 0:
                kept.append(z)
            elif self.log:
                self.log(
                    "zone_expired",
                    {"zone_id": z.id, "source_id": z.source_id},
                )
        self.state.active_zones = kept

    def _on_action_resolved(self, payload: dict[str, Any]) -> None:
        self._apply_spbase_energy(payload)
        self._apply_sacerdos_4pc(payload)
        self._apply_acheron_on_action_resolved(payload)

    def _apply_sacerdos_4pc(self, payload: dict[str, Any]) -> None:
        """Sacerdos 121 4pc: skill/ult on single ally → +18% CD, 2 turns, max 2.

        Paper bot may pass an enemy id as action target while Effect.target=single_ally
        resolves to an ally; apply to that ally (prefer acheron / non-self).
        """
        _ = payload
        ctx = self.action_ctx
        if ctx is None or ctx.actor_id != "sparkle":
            return
        if ctx.action_kind not in ("skill", "ultimate"):
            return
        sparkle = self.state.find_char("sparkle")
        if sparkle is None:
            return
        target = None
        if ctx.target_id:
            cand = self.state.find_char(str(ctx.target_id))
            if cand is not None and cand in self.state.allies and cand.is_alive:
                target = cand
        if target is None or target.char.id == "sparkle":
            # Match SINGLE_ALLY fallback used by skill buff when bot aimed at enemy.
            ach = self.state.find_char("acheron")
            if ach is not None and ach.is_alive:
                target = ach
            else:
                alive = [
                    a
                    for a in self.state.allies
                    if a.is_alive and a.char.id != "sparkle"
                ]
                target = alive[0] if alive else None
        if target is None:
            return
        from hsrsim.loadout.relic_conditionals import (
            SACERDOS_4PC_CD,
            SACERDOS_4PC_DURATION,
            SACERDOS_4PC_MAX_STACKS,
        )
        from hsrsim.simulator.types import Effect, EffectModifier, EffectTarget

        tmpl = Effect(
            id="relic_sacerdos_4pc_cd",
            name="Sacerdos Relived Ordeal 4pc",
            is_buff=True,
            target=EffectTarget.SINGLE_ALLY,
            duration_turns=int(SACERDOS_4PC_DURATION),
            max_stacks=int(SACERDOS_4PC_MAX_STACKS),
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="crit_dmg",
                    operation="add",
                    value=float(SACERDOS_4PC_CD),
                )
            ],
        )
        target.apply_effect(tmpl, source_id="sparkle")

    def _apply_spbase_energy(self, payload: dict[str, Any]) -> None:
        """: SPBase × Stats.err on skill-cast action_resolved. Hit/kill: UNKNOWN."""
        ctx = self.action_ctx
        if ctx is None:
            return
        actor = self.state.find_char(ctx.actor_id)
        if actor is None:
            return
        action = next(
            (a for a in actor.char.build.actions if a.id == ctx.action_id),
            None,
        )
        skill_id = action.skill_id if action is not None else None
        grant_spbase_on_action(
            actor,
            action_kind=ctx.action_kind,
            skill_id=skill_id,
            log=self.log,
        )

    def _apply_acheron_on_action_resolved(self, payload: dict[str, Any]) -> None:
        if not self.acheron_e0_enabled:
            return
        ctx = self.action_ctx
        if ctx is None:
            return
        acheron = self._find_acheron()

        # Ult clears all crimson knots (game text); then convert 四相断我 → dream+knot.
        if (
            ctx.actor_id == "acheron"
            and ctx.action_kind == "ultimate"
            and acheron is not None
        ):
            for enemy in self.state.enemies:
                if effect_stacks(enemy, CRIMSON_KNOT_ID) > 0:
                    enemy.remove_effect(CRIMSON_KNOT_ID)
            self._convert_quad_after_ult(acheron)
            return

        if acheron is None or self.acheron_ult_active:
            return

        # R3: Acheron skill inherent — driven by counter_gains.json (l2_hook=acheron_r3).
        r3 = self._spec_by_hook("acheron_r3")
        if (
            r3 is not None
            and r3.kind == "action_inherent"
            and ctx.actor_id == str(r3.filter.get("actor_id") or "acheron")
            and ctx.action_kind == str(r3.filter.get("action_type") or "skill")
            and not ctx.skill_r3_settled
        ):
            ctx.skill_r3_settled = True
            main = None
            if ctx.target_id:
                main = self.state.find_char(ctx.target_id)
            if main is None or not main.is_alive or main not in self.state.enemies:
                main = next((e for e in self.state.enemies if e.is_alive), None)
            for _ in range(int(round(r3.increment))):
                self._try_dream_knot_package(
                    acheron,
                    source="acheron_r3",
                    knot_target=main,
                    from_trigger="acheron_r3",
                )

        # R2: debuff during a skill cast — driven by counter_gains.json (l2_hook=acheron_r2).
        r2 = self._spec_by_hook("acheron_r2")
        if (
            r2 is not None
            and r2.kind == "applies_debuff_during_skill_cast"
            and ctx.debuff_targets
            and not ctx.talent_settled
            and is_skill_cast_kind(ctx.action_kind, self.rules)
        ):
            ctx.talent_settled = True
            target = select_unit(
                self.state,
                "enemies",
                order_by=f"effect_stacks:{CRIMSON_KNOT_ID}:desc",
                tie_break=self.rules.knot_tie_break,
                among_ids=list(ctx.debuff_targets),
            )
            for _ in range(int(round(r2.increment))):
                self._try_dream_knot_package(
                    acheron,
                    source=f"acheron_r2:{ctx.actor_id}:{ctx.action_kind}",
                    knot_target=target,
                    from_trigger="acheron_r2",
                )

    def _on_enemy_killed(self, payload: dict[str, Any]) -> None:
        victim_id = payload.get("victim_id")
        if not victim_id:
            return
        victim = self.state.find_char(str(victim_id))
        if victim is None:
            return
        for rule in self.transfer_rules:
            if rule.requires_ally_id:
                ally = self.state.find_char(rule.requires_ally_id)
                if ally is None or not ally.is_alive:
                    continue
            self.transfer_stacks(
                effect_id=rule.effect_id,
                victim=victim,
                direction=rule.direction,
            )

    def _on_turn_start(self, payload: dict[str, Any]) -> None:
        """Acheron E2 + Aventurine Blind Bet ICD + zone duration tick."""
        unit_id = str(payload.get("unit_id") or "")
        self._tick_zones_on_source_turn(unit_id)
        if unit_id == "acheron":
            ach = self.state.find_char("acheron")
            if ach is not None and getattr(ach, "_pioneer_double_active", False):
                applied_round = int(getattr(ach, "_pioneer_double_round", -1))
                if int(self.state.round_number) > applied_round:
                    setattr(ach, "_pioneer_double_active", False)
        if unit_id == "aventurine":
            unit = self.state.find_char("aventurine")
            if unit is not None:
                cd = float(unit.variables.get(BLIND_BET_EXTRA_CD_ID, 0.0))
                if cd > 0.0:
                    unit.variables[BLIND_BET_EXTRA_CD_ID] = max(0.0, cd - 1.0)
                    if self.log:
                        self.log(
                            "variable_changed",
                            {
                                "actor_id": "aventurine",
                                "variable": BLIND_BET_EXTRA_CD_ID,
                                "delta": -1.0,
                                "new_value": unit.variables[BLIND_BET_EXTRA_CD_ID],
                                "source": "blind_bet_extra_icd_tick",
                            },
                        )
        if not self.acheron_e0_enabled:
            return
        if unit_id != "acheron":
            return
        e2 = self._spec_by_hook("acheron_e2_turn_start")
        if e2 is None or e2.kind != "turn_start":
            return
        acheron = self._find_acheron()
        min_e = int(e2.filter.get("min_eidolon") or 0)
        if acheron is None or int(acheron.char.eidolon) < min_e:
            return
        knot_target = select_unit(
            self.state,
            "enemies",
            order_by=f"effect_stacks:{CRIMSON_KNOT_ID}:desc",
            tie_break=self.rules.knot_tie_break,
        )
        for _ in range(int(round(e2.increment))):
            self._try_dream_knot_package(
                acheron,
                source="acheron_e2_turn_start",
                knot_target=knot_target,
                from_trigger="acheron_e2_turn_start",
            )

    def _on_battle_start(self, payload: dict[str, Any]) -> None:
        """赤鬼开战残梦/集真赤 + LC 23021 假面开战持续 + E2 Keel team CD."""
        _ = payload
        self._apply_acheron_red_ghost_battle_start()
        self._apply_lc_mask_battle_start()
        self._apply_broken_keel_team_cd()
        self._apply_pela_team_ehr()

    def _apply_pela_team_ehr(self) -> None:
        """佩拉行迹「秘策」：在场时我方全体效果命中 +10%（SkillTree 1106102）。"""
        pela = self.state.find_char("pela")
        if pela is None or not pela.is_alive:
            return
        tmpl = next(
            (e for e in pela.char.build.effects if e.id == "pela_trace_team_ehr"),
            None,
        )
        if tmpl is None:
            return
        for ally in self.state.allies:
            if not ally.is_alive:
                continue
            if float(ally.variables.get("_pela_team_ehr_applied", 0.0)) >= 1.0:
                continue
            ally.apply_effect(tmpl, source_id=pela.char.id)
            ally.variables["_pela_team_ehr_applied"] = 1.0
            # Hit-chance readers use Stats.ehr (not effect aggregate).
            ally.char.build.stats.ehr = float(ally.char.build.stats.ehr) + 0.10
            if self.log:
                self.log(
                    "effect_applied",
                    {
                        "effect_id": "pela_trace_team_ehr",
                        "is_debuff": False,
                        "source_id": pela.char.id,
                        "target_id": ally.char.id,
                        "target_side": "ally",
                        "ehr_after": ally.char.build.stats.ehr,
                        "from_trigger": "pela_trace_team_ehr",
                    },
                )

    def _apply_broken_keel_team_cd(self) -> None:
        """Broken Keel 310: wearer RES≥30% → all allies +10% CD (paper: sparkle/aven)."""
        from hsrsim.loadout.relic_conditionals import (
            KEEL_TEAM_CD,
            keel_team_cd_if_eligible,
        )
        from hsrsim.simulator.types import Effect, EffectModifier, EffectTarget

        for wearer_id in ("sparkle", "aventurine"):
            wearer = self.state.find_char(wearer_id)
            if wearer is None or not wearer.is_alive:
                continue
            bonus = keel_team_cd_if_eligible(wearer.char)
            if bonus <= 0:
                continue
            for ally in self.state.allies:
                if not ally.is_alive:
                    continue
                tmpl = Effect(
                    id=f"relic_keel_cd__{wearer_id}",
                    name="Broken Keel Team CD",
                    is_buff=True,
                    target=EffectTarget.SELF,
                    duration_turns=99,
                    max_stacks=1,
                    current_stacks=1,
                    modifiers=[
                        EffectModifier(
                            target_stat="crit_dmg",
                            operation="add",
                            value=float(KEEL_TEAM_CD),
                        )
                    ],
                )
                ally.apply_effect(tmpl, source_id=wearer_id)

    def _apply_acheron_red_ghost_battle_start(self) -> None:
        """Trace 1308101: +5 残梦 +5 集真赤 on a random enemy (ParamList [[5,3]])."""
        if not self.acheron_e0_enabled:
            return
        acheron = self._find_acheron()
        if acheron is None:
            return
        knot_target = select_unit(
            self.state,
            "enemies",
            order_by=f"effect_stacks:{CRIMSON_KNOT_ID}:desc",
            tie_break=self.rules.knot_tie_break,
        )
        for _ in range(RED_GHOST_BATTLE_START):
            self._try_dream_knot_package(
                acheron,
                source="acheron_red_ghost_battle_start",
                knot_target=knot_target,
                from_trigger="acheron_red_ghost_battle_start",
            )

    def _apply_lc_mask_battle_start(self) -> None:
        """LC 23021 Mask: battle-start duration = ParamList[5] (S1: 3), not 99."""
        for ally in self.state.allies:
            if not ally.is_alive:
                continue
            if ally.char.light_cone_id != 23021:
                continue
            params = lc_param_list(23021, int(ally.char.light_cone_superimposition))
            duration = int(params[5])
            self._grant_lc_mask_to_teammates(ally, duration=duration)

    def _grant_lc_mask_to_teammates(
        self, wearer: CharacterState, *, duration: int
    ) -> None:
        params = lc_param_list(
            23021, int(wearer.char.light_cone_superimposition)
        )
        cd_bonus = float(params[1])
        cr_bonus = float(params[4])
        for teammate in self.state.allies:
            if not teammate.is_alive or teammate.char.id == wearer.char.id:
                continue
            buff = Effect(
                id=LC_MASK_ALLY_ID,
                name="假面",
                name_en="Mask",
                is_buff=True,
                target=EffectTarget.SINGLE_ALLY,
                duration_turns=int(duration),
                max_stacks=1,
                current_stacks=1,
                modifiers=[
                    EffectModifier(
                        target_stat="crit_rate",
                        operation="add",
                        value=cr_bonus,
                    ),
                    EffectModifier(
                        target_stat="crit_dmg",
                        operation="add",
                        value=cd_bonus,
                    ),
                ],
            )
            result = teammate.apply_effect(buff, source_id=wearer.char.id)
            self.emit(
                "effect_applied",
                {
                    "effect_id": LC_MASK_ALLY_ID,
                    "is_debuff": False,
                    "source_id": wearer.char.id,
                    "target_id": teammate.char.id,
                    "target_side": "ally",
                    "was_refresh": result["was_refresh"],
                    "stacks_before": result["stacks_before"],
                    "stacks_after": result["stacks_after"],
                    "apply_source": APPLY_SOURCE_TRIGGERED,
                    "duration_turns": int(duration),
                },
            )

    def _on_sp_gained(self, payload: dict[str, Any]) -> None:
        """LC 23021 彩焰: wearer recovers SP (raw_gain, incl. truncate) → blaze stacks."""
        actor_id = str(payload.get("actor_id") or "")
        raw_gain = int(payload.get("raw_gain") or 0)
        if raw_gain <= 0 or not actor_id:
            return
        wearer = self.state.find_char(actor_id)
        if wearer is None or not wearer.is_alive:
            return
        if wearer.char.light_cone_id != 23021:
            return
        params = lc_param_list(
            23021, int(wearer.char.light_cone_superimposition)
        )
        threshold = int(params[3])  # S1: 4
        refresh_duration = int(params[2])  # S1: 4
        for _ in range(raw_gain):
            cur = float(wearer.variables.get(LC_BLAZE_VAR, 0.0)) + 1.0
            if cur >= threshold:
                wearer.variables[LC_BLAZE_VAR] = 0.0
                if self.log:
                    self.log(
                        "variable_changed",
                        {
                            "actor_id": wearer.char.id,
                            "variable": LC_BLAZE_VAR,
                            "delta": -float(threshold - 1),
                            "new_value": 0.0,
                            "source": "lc_blaze_refresh",
                        },
                    )
                self._grant_lc_mask_to_teammates(
                    wearer, duration=refresh_duration
                )
            else:
                wearer.variables[LC_BLAZE_VAR] = cur
                if self.log:
                    self.log(
                        "variable_changed",
                        {
                            "actor_id": wearer.char.id,
                            "variable": LC_BLAZE_VAR,
                            "delta": 1.0,
                            "new_value": cur,
                            "source": "lc_blaze_gain",
                        },
                    )

    def _on_damage_hit(self, payload: dict[str, Any]) -> None:
        """LC on-hit conditionals (23024 mirage, 23029 unarmored, 23023 FUA vuln)."""
        attacker_id = str(payload.get("attacker_id") or "")
        defender_id = str(payload.get("defender_id") or "")
        action_kind = str(payload.get("action_kind") or "")
        attacker = self.state.find_char(attacker_id)
        defender = self.state.find_char(defender_id)
        if attacker is None or defender is None or not defender.is_alive:
            return
        if defender not in self.state.enemies:
            return
        lc_id = attacker.char.light_cone_id
        if lc_id is None:
            return
        si = int(attacker.char.light_cone_superimposition)
        if lc_id == 23024:
            self._apply_lc_mirage(attacker, defender)
        elif lc_id == 23029 and action_kind in (
            "basic_attack",
            "skill",
            "ultimate",
        ):
            self._apply_lc_unarmored(attacker, defender, si)
        elif lc_id == 21015 and action_kind in (
            "basic_attack",
            "skill",
            "ultimate",
        ):
            self._apply_lc_exposed(attacker, defender, si)
        elif lc_id == 22000 and action_kind in (
            "basic_attack",
            "skill",
            "ultimate",
        ):
            self._apply_lc_22000_energy(attacker, defender, si)
        elif lc_id == 23023 and action_kind == "follow_up":
            self._apply_lc_23023_fua_vuln(attacker, defender, si)
        # Pela talent: extra energy when hitting a debuffed enemy.
        if attacker.char.id == "pela":
            self._pela_talent_energy_on_hit(attacker, defender)

    def _apply_lc_mirage(
        self, attacker: CharacterState, defender: CharacterState
    ) -> None:
        ctx = self.action_ctx
        if ctx is not None and defender.char.id in ctx.lc_mirage_marked:
            return
        if ctx is not None:
            ctx.lc_mirage_marked.add(defender.char.id)
        debuff = Effect(
            id=LC_MIRAGE_ID,
            name="泡影",
            name_en="Mirage Fizzle",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=1,
            max_stacks=1,
            current_stacks=1,
            modifiers=[],
        )
        result = defender.apply_effect(debuff, source_id=attacker.char.id)
        self.emit(
            "effect_applied",
            {
                "effect_id": LC_MIRAGE_ID,
                "is_debuff": True,
                "source_id": attacker.char.id,
                "target_id": defender.char.id,
                "target_side": "enemy",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def _apply_lc_unarmored(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        si: int,
    ) -> None:
        params = lc_param_list(23029, si)
        chance = float(params[1])
        vuln = float(params[2])
        turns = int(params[3])
        upgraded_vuln = float(params[5])
        # 原文：「若目标处于装备者施加的持续伤害状态」→ 升级卸甲为穷寇。
        #generic wearer DoT (dot_instance + source/dot_source_id == wearer).
        # No ashen_roast name hardcode — 烬煨满足条件则自然升级.
        has_wearer_dot = _defender_has_wearer_dot(defender, attacker.char.id)
        if has_wearer_dot:
            # Approx: apply Cornered vuln directly (skips Unarmored→upgrade sequence).
            vuln = upgraded_vuln
            chance = 1.0
        ehr = float(attacker.char.build.stats.ehr)
        effect_res = float(defender.char.build.stats.effect_res)
        from hsrsim.simulator.ehr import debuff_hit_chance

        final_chance = debuff_hit_chance(chance, ehr, effect_res)
        roll = self.rng.random() if self.rng is not None else 0.0
        if roll > final_chance:
            if self.log:
                self.log(
                    "effect_resisted",
                    {
                        "source": attacker.char.id,
                        "target_id": defender.char.id,
                        "effect_id": LC_UNARMORED_ID,
                        "base_chance": chance,
                        "ehr": ehr,
                        "target_effect_res": effect_res,
                        "final_chance": final_chance,
                        "roll": roll,
                    },
                )
            return
        debuff = Effect(
            id=LC_UNARMORED_ID,
            name="卸甲" if not has_wearer_dot else "穷寇",
            name_en="Unarmored" if not has_wearer_dot else "Cornered",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=turns,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="vuln",
                    operation="add",
                    value=vuln,
                )
            ],
        )
        result = defender.apply_effect(debuff, source_id=attacker.char.id)
        self.emit(
            "effect_applied",
            {
                "effect_id": LC_UNARMORED_ID,
                "is_debuff": True,
                "source_id": attacker.char.id,
                "target_id": defender.char.id,
                "target_side": "enemy",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def _apply_lc_exposed(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        si: int,
    ) -> None:
        """21015 决心：命中时若无攻陷则施加减防（ParamList #1 概率 / #2 减防 / #3 持续）。"""
        params = lc_param_list(21015, si)
        chance = float(params[0])
        shred = float(params[1])
        turns = int(params[2])
        if any(e.id == LC_EXPOSED_ID for e in defender.active_effects):
            return
        ehr = float(attacker.char.build.stats.ehr)
        effect_res = float(defender.char.build.stats.effect_res)
        from hsrsim.simulator.ehr import debuff_hit_chance

        final_chance = debuff_hit_chance(chance, ehr, effect_res)
        roll = self.rng.random() if self.rng is not None else 0.0
        if roll > final_chance:
            return
        debuff = Effect(
            id=LC_EXPOSED_ID,
            name="攻陷",
            name_en="Ensnared",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=turns,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="def_reduction",
                    operation="add",
                    value=shred,
                )
            ],
        )
        result = defender.apply_effect(debuff, source_id=attacker.char.id)
        self.emit(
            "effect_applied",
            {
                "effect_id": LC_EXPOSED_ID,
                "is_debuff": True,
                "source_id": attacker.char.id,
                "target_id": defender.char.id,
                "target_side": "enemy",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def _apply_lc_22000_energy(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        si: int,
    ) -> None:
        """22000：攻击防御力被降低的目标时回能 ParamList#2。"""
        has_def_down = any(
            (not e.is_buff)
            and any(m.target_stat == "def_reduction" for m in e.modifiers)
            for e in defender.active_effects
        )
        if not has_def_down:
            return
        params = lc_param_list(22000, si)
        gain = float(params[1]) if len(params) > 1 else 8.0
        before = float(attacker.energy_current)
        attacker.energy_current = min(
            float(attacker.char.build.stats.energy_max), before + gain
        )
        if self.log:
            self.log(
                "energy_gain",
                {
                    "source": "lc_22000",
                    "character_id": attacker.char.id,
                    "amount": gain,
                    "energy_before": before,
                    "energy_after": attacker.energy_current,
                },
            )

    def _pela_talent_energy_on_hit(
        self, attacker: CharacterState, defender: CharacterState
    ) -> None:
        """天赋数据采集：目标有减益时额外能量（E6 烘焙 L12=11）；每行动至多一次。"""
        ctx = self.action_ctx
        if ctx is not None and ctx.pela_talent_energy_done:
            return
        has_debuff = any(not e.is_buff for e in defender.active_effects)
        if not has_debuff:
            return
        if ctx is not None:
            ctx.pela_talent_energy_done = True
        gain = 11.0
        before = float(attacker.energy_current)
        attacker.energy_current = min(
            float(attacker.char.build.stats.energy_max), before + gain
        )
        if self.log:
            self.log(
                "energy_gain",
                {
                    "source": "pela_talent",
                    "character_id": attacker.char.id,
                    "amount": gain,
                    "energy_before": before,
                    "energy_after": attacker.energy_current,
                },
            )

    def _on_hit_taken(self, payload: dict[str, Any]) -> None:
        """Ally hit: Trend burn (21016) and/or Aventurine Blind Bet.

        Engine still owns FUA insert; Blind Bet only mutates counters.
        Trend burn must run while action_ctx is alive (engine hits before
        action_resolved) so R1 can count the enemy debuff once per action.
        """
        defender_id = str(payload.get("defender_id") or "")
        attacker_id = str(payload.get("attacker_id") or "")
        defender = self.state.find_char(defender_id)
        if defender is None or defender not in self.state.allies:
            return
        self._try_lc_trend_burn(defender, attacker_id)
        if not any(e.id == AVENTURINE_CHIP_ID for e in defender.active_effects):
            return
        aventurine = self.state.find_char("aventurine")
        if aventurine is None or not aventurine.is_alive:
            return
        self.grant_blind_bet(aventurine, 1.0, source="talent_chip_hit")
        # Extra Blind Bet: only when the hit target is Aventurine himself holding chip.
        if defender.char.id != "aventurine":
            return
        cd = float(aventurine.variables.get(BLIND_BET_EXTRA_CD_ID, 0.0))
        if cd > 0.0:
            return
        self.grant_blind_bet(
            aventurine, BLIND_BET_EXTRA_GAIN, source="talent_extra_blind_bet"
        )
        aventurine.variables[BLIND_BET_EXTRA_CD_ID] = BLIND_BET_EXTRA_ICD
        if self.log:
            self.log(
                "variable_changed",
                {
                    "actor_id": "aventurine",
                    "variable": BLIND_BET_EXTRA_CD_ID,
                    "delta": BLIND_BET_EXTRA_ICD,
                    "new_value": BLIND_BET_EXTRA_ICD,
                    "source": "talent_extra_blind_bet_icd",
                },
            )

    def _try_lc_trend_burn(
        self, wearer: CharacterState, attacker_id: str
    ) -> None:
        """21016 宇宙市场趋势：存护受击 → 对攻击者挂灼烧 debuff（残梦路径）。

        ParamList: DEF% / base chance / burn=DEF× / duration.
        DoT tick damage deferred; existence+is_debuff is enough for R1.
        """
        if int(wearer.char.light_cone_id or 0) != 21016:
            return
        path = getattr(wearer.char, "path", None)
        path_s = path.value if hasattr(path, "value") else str(path or "")
        if path_s.lower() not in ("preservation", "存护"):
            return
        enemy = self.state.find_char(attacker_id)
        if enemy is None or enemy not in self.state.enemies or not enemy.is_alive:
            enemy = next((e for e in self.state.enemies if e.is_alive), None)
        if enemy is None:
            return
        si = int(wearer.char.light_cone_superimposition or 1)
        params = lc_param_list(21016, si)
        chance = float(params[1])
        turns = int(params[3])
        from hsrsim.simulator.ehr import debuff_hit_chance

        ehr = float(wearer.char.build.stats.ehr)
        effect_res = float(enemy.char.build.stats.effect_res)
        final_chance = debuff_hit_chance(chance, ehr, effect_res)
        roll = self.rng.random() if self.rng is not None else 0.0
        if roll > final_chance:
            if self.log:
                self.log(
                    "effect_resisted",
                    {
                        "source": wearer.char.id,
                        "target_id": enemy.char.id,
                        "effect_id": LC_TREND_BURN_ID,
                        "base_chance": chance,
                        "ehr": ehr,
                        "target_effect_res": effect_res,
                        "final_chance": final_chance,
                        "roll": roll,
                    },
                )
            return
        burn_ratio = float(params[2])
        defense = float(wearer.char.build.stats.defense)
        debuff = Effect(
            id=LC_TREND_BURN_ID,
            name="宇宙市场趋势·灼烧",
            name_en="Trend of the Universal Market Burn",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=turns,
            max_stacks=1,
            current_stacks=1,
            modifiers=[],
            # Presence-only for R1; tick value recorded for audit.
            dot_instance=None,
        )
        result = enemy.apply_effect(debuff, source_id=wearer.char.id)
        payload = {
            "effect_id": LC_TREND_BURN_ID,
            "is_debuff": True,
            "source_id": wearer.char.id,
            "target_id": enemy.char.id,
            "target_side": "enemy",
            "was_refresh": result["was_refresh"],
            "stacks_before": result["stacks_before"],
            "stacks_after": result["stacks_after"],
            "apply_source": APPLY_SOURCE_TRIGGERED,
            "from_trigger": "lc_21016_trend",
            "burn_def_ratio": burn_ratio,
            "wearer_defense": defense,
        }
        self.emit("effect_applied", payload)
        if self.log:
            self.log("effect_applied", payload)

    def grant_blind_bet(
        self, aventurine: CharacterState, amount: float, *, source: str
    ) -> None:
        cur = float(aventurine.variables.get(BLIND_BET_ID, 0.0))
        new = min(BLIND_BET_MAX, cur + float(amount))
        delta = new - cur
        if delta <= 0.0:
            return
        aventurine.variables[BLIND_BET_ID] = new
        if self.log:
            self.log(
                "variable_changed",
                {
                    "actor_id": aventurine.char.id,
                    "variable": BLIND_BET_ID,
                    "delta": delta,
                    "new_value": new,
                    "source": source,
                },
            )

    def refresh_aventurine_chip_all_allies(
        self, *, source_id: str, duration: int = CHIP_TRACE_DURATION
    ) -> None:
        """Existence-only Fortified Wager refresh (skill / FUA trace)."""
        tmpl = Effect(
            id=AVENTURINE_CHIP_ID,
            name="坚垣筹码",
            name_en="Fortified Wager",
            is_buff=True,
            target=EffectTarget.ALL_ALLIES,
            duration_turns=int(duration),
            max_stacks=1,
            current_stacks=1,
            modifiers=[],
        )
        for ally in self.state.allies:
            if not ally.is_alive:
                continue
            result = ally.apply_effect(tmpl, source_id=source_id)
            self.emit(
                "effect_applied",
                {
                    "effect_id": AVENTURINE_CHIP_ID,
                    "is_debuff": False,
                    "source_id": source_id,
                    "target_id": ally.char.id,
                    "target_side": "ally",
                    "was_refresh": result["was_refresh"],
                    "stacks_before": result["stacks_before"],
                    "stacks_after": result["stacks_after"],
                    "apply_source": APPLY_SOURCE_TRIGGERED,
                },
            )

    def _on_shield_provided_for_lc23023(self, payload: dict[str, Any]) -> None:
        """23023 (b): 为我方目标提供护盾时 → 装备者暴击伤害提高 (ParamList #2/#3)."""
        if str(payload.get("effect_id") or "") != AVENTURINE_CHIP_ID:
            return
        if payload.get("is_debuff"):
            return
        source_id = str(payload.get("source_id") or "")
        wearer = self.state.find_char(source_id)
        if wearer is None or not wearer.is_alive:
            return
        if int(wearer.char.light_cone_id or 0) != 23023:
            return
        # Dedup: one CD buff grant per action window.
        ctx = self.action_ctx
        if ctx is not None:
            flag = getattr(ctx, "lc_23023_cd_granted", False)
            if flag:
                return
            setattr(ctx, "lc_23023_cd_granted", True)
        si = int(wearer.char.light_cone_superimposition)
        params = lc_param_list(23023, si)
        cd = float(params[1])
        turns = int(params[2])
        buff = Effect(
            id=LC_23023_CD_ID,
            name="命运从未公平·暴伤",
            name_en="Inherently Unjust Destiny CD",
            is_buff=True,
            target=EffectTarget.SELF,
            duration_turns=turns,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="crit_dmg",
                    operation="add",
                    value=cd,
                )
            ],
        )
        result = wearer.apply_effect(buff, source_id=wearer.char.id)
        self.emit(
            "effect_applied",
            {
                "effect_id": LC_23023_CD_ID,
                "is_debuff": False,
                "source_id": wearer.char.id,
                "target_id": wearer.char.id,
                "target_side": "ally",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def _apply_lc_23023_fua_vuln(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        si: int,
    ) -> None:
        """23023 (b): 追加攻击击中 → 基础概率易伤 (ParamList #4/#5/#6)."""
        params = lc_param_list(23023, si)
        chance = float(params[3])
        vuln = float(params[4])
        turns = int(params[5])
        ehr = float(attacker.char.build.stats.ehr)
        effect_res = float(defender.char.build.stats.effect_res)
        from hsrsim.simulator.ehr import debuff_hit_chance

        final_chance = debuff_hit_chance(chance, ehr, effect_res)
        roll = self.rng.random() if self.rng is not None else 0.0
        if roll > final_chance:
            if self.log:
                self.log(
                    "effect_resisted",
                    {
                        "source": attacker.char.id,
                        "target_id": defender.char.id,
                        "effect_id": LC_23023_VULN_ID,
                        "base_chance": chance,
                        "ehr": ehr,
                        "target_effect_res": effect_res,
                        "final_chance": final_chance,
                        "roll": roll,
                    },
                )
            return
        debuff = Effect(
            id=LC_23023_VULN_ID,
            name="命运从未公平·易伤",
            name_en="Inherently Unjust Destiny Vuln",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=turns,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(
                    target_stat="vuln",
                    operation="add",
                    value=vuln,
                )
            ],
        )
        result = defender.apply_effect(debuff, source_id=attacker.char.id)
        self.emit(
            "effect_applied",
            {
                "effect_id": LC_23023_VULN_ID,
                "is_debuff": True,
                "source_id": attacker.char.id,
                "target_id": defender.char.id,
                "target_side": "enemy",
                "was_refresh": result["was_refresh"],
                "stacks_before": result["stacks_before"],
                "stacks_after": result["stacks_after"],
                "apply_source": APPLY_SOURCE_TRIGGERED,
            },
        )

    def transfer_stacks(
        self,
        *,
        effect_id: str,
        victim: CharacterState,
        direction: str = "max",
    ) -> CharacterState | None:
        """Move all stacks of ``effect_id`` from victim to max/min living enemy."""
        src = next((e for e in victim.active_effects if e.id == effect_id), None)
        if src is None or src.current_stacks <= 0:
            return None
        move = int(src.current_stacks)
        order = "desc" if direction == "max" else "asc"
        dest = select_unit(
            self.state,
            "enemies",
            order_by=f"effect_stacks:{effect_id}:{order}",
            tie_break=self.rules.knot_tie_break,
            exclude=victim.char.id,
        )
        victim.remove_effect(effect_id)
        if dest is None or not dest.is_alive:
            if self.log:
                self.log(
                    "stacks_discarded",
                    {
                        "effect_id": effect_id,
                        "from": victim.char.id,
                        "stacks": move,
                    },
                )
            return None
        for _ in range(move):
            tmpl = Effect(
                id=effect_id,
                name=src.name,
                name_en=src.name_en,
                is_buff=src.is_buff,
                target=src.target,
                duration_turns=src.duration_turns,
                max_stacks=src.max_stacks,
                current_stacks=1,
                modifiers=list(src.modifiers),
            )
            dest.apply_effect(tmpl, source_id="transfer")
        if self.log:
            self.log(
                "stacks_transferred",
                {
                    "effect_id": effect_id,
                    "from": victim.char.id,
                    "to": dest.char.id,
                    "stacks": move,
                    "direction": direction,
                },
            )
        return dest
