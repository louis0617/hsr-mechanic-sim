"""
Combat engine. Main simulation loop.

Architecture (mirrors Pfau 2024 §3.1):
    Scenario JSON → Engine → Event stream → metrics computation → reward → outer loop

The engine does NOT make policy decisions. It either:
1. Plays a pre-specified Rotation (for benchmark / replay)
2. Asks a Bot for the next action (for AI-driven optimization)

This separation is critical for RQ2: bots are pluggable (greedy / heuristic / oracle / PPO).
"""
from __future__ import annotations

import json
import logging
import math
import operator
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from hsrsim.simulator.av import advance_forward, moc_cycle_av_cap, moc_cycle_from_av
from hsrsim.simulator.combat_rules import CombatRules, StackTransferRule
from hsrsim.simulator.damage_zones import DamageContext, compute_damage
from hsrsim.simulator.effect_agg import (
    aggregate_effects,
    figment_aura_vuln,
    stat_bonus_for_element,
)
from hsrsim.simulator.ehr import debuff_hit_chance
from hsrsim.simulator.energy import grant_energy
from hsrsim.simulator.state import BattleState, CharacterState
from hsrsim.simulator.field_zone import collect_zone_ult_vuln
from hsrsim.simulator.triggers import (
    APPLY_SOURCE_ACTION_CAST,
    AVENTURINE_FUA_ACTION_ID,
    AVENTURINE_UNNERVED_ID,
    BLIND_BET_ID,
    CRIMSON_KNOT_ID,
    EventBus,
    LC_MIRAGE_ID,
)
from hsrsim.simulator.ult_resource import (
    UltResourceError,
    resolve_ult_resource,
    ult_resource_ready,
)
from hsrsim.rules.wiring import ACHERON_E1_CRIT_RATE, abyss_multiplier, lc_param_list
from hsrsim.simulator.queries import count_units, effect_stacks
from hsrsim.simulator.types import (
    Action,
    ActionType,
    Character,
    DamageInstance,
    DamageType,
    Effect,
    EffectModifier,
    EffectTarget,
    Element,
    Rotation,
    Scenario,
)

logger = logging.getLogger(__name__)

_REQUIREMENT_PATTERN = re.compile(
    r"^(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)\s*(?P<op>>=|<=|==|!=|>|<)\s*(?P<val>-?\d+(?:\.\d+)?)$"
)
_COMPARE_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


# ============================================================
# EVENT TYPES (the output of the simulator, consumed by metrics)
# ============================================================

@dataclass
class Event:
    """A single event in the simulation log. Pfau 2024 calls this 'timestamped event'."""
    timestamp: float           # AV-based clock
    round: int
    event_type: str            # "damage" | "effect_applied" | "effect_expired" | "variable_changed" | "action_executed"
    payload: dict[str, Any]


class NoLegalActionError(RuntimeError):
    """C7: normal turn has no executable action — refuse silent clock advance."""

    def __init__(self, actor: CharacterState, state: BattleState, *, reason: str):
        self.actor_id = actor.char.id
        self.reason = reason
        summary = {
            "actor_id": actor.char.id,
            "reason": reason,
            "av_clock": state.av_clock,
            "av_remaining": actor.av_remaining,
            "hp": actor.hp_current,
            "energy": actor.energy_current,
            "energy_max": actor.char.build.stats.energy_max,
            "sp_pool": state.sp_team_pool,
            "variables": dict(actor.variables),
            "n_actions": len(actor.char.build.actions),
            "n_legal_normal": len(
                [
                    a
                    for a in actor.char.build.actions
                    if a.type != ActionType.ULTIMATE
                ]
            ),
        }
        super().__init__(
            f"No legal action for {actor.char.id} ({reason}): {summary}"
        )
        self.state_summary = summary


@dataclass
class SimulationResult:
    """Output of a single Engine.run() call.
    
    This object is what RQ2 metrics (GSD/SBL/ZUR/SL/DCC) consume.
    Keep it pure-data so it can be serialized for caching / cross-validation.
    """
    events: list[Event]
    final_state_snapshot: dict[str, Any]
    total_damage: float
    rounds_played: int  # retained; not the C9 length metric
    actions_taken: list[tuple[str, str]]  # (actor_id, action_id) sequence — the realized rotation
    cycles_played: int = 0  # C9: moc_cycle_from_av(av_clock) at end
    
    def to_json(self) -> str:
        return json.dumps({
            "total_damage": self.total_damage,
            "rounds_played": self.rounds_played,
            "cycles_played": self.cycles_played,
            "actions_taken": self.actions_taken,
            "final_state": self.final_state_snapshot,
            "events": [
                {"timestamp": e.timestamp, "round": e.round, "type": e.event_type, "payload": e.payload}
                for e in self.events
            ],
        }, indent=2)


# ============================================================
# BOT INTERFACE (pluggable policy)
# ============================================================

class Bot:
    """Abstract base for a policy that picks actions.
    
    Subclasses live in simulator/bots.py. Engine treats them as black boxes.
    """
    def select_action(
        self,
        actor: CharacterState,
        state: BattleState,
        legal_actions: list[Action],
    ) -> tuple[Action, str]:
        """Return (chosen_action, target_id)."""
        raise NotImplementedError


# ============================================================
# ENGINE
# ============================================================

class Engine:
    """The combat simulator.
    
    Usage:
        engine = Engine(scenario, ally_bot=GreedyBot(), enemy_bot=DummyBot())
        result = engine.run()
        # result.events feeds into metrics/rotation_aware.py
    """
    def __init__(
        self,
        scenario: Scenario,
        ally_bot: Bot | None = None,
        ally_bots: dict[str, Bot] | None = None,
        enemy_bot: Bot | None = None,
        random_seed: int | None = None,
        initial_state: BattleState | None = None,
        combat_rules: CombatRules | None = None,
        transfer_rules: list[StackTransferRule] | None = None,
        enable_acheron_e0: bool = True,
    ):
        self.scenario = scenario
        self.state = (
            initial_state.clone()
            if initial_state is not None
            else BattleState.from_characters(scenario.allies, scenario.enemies)
        )
        self.ally_bot = ally_bot
        self.ally_bots = ally_bots or {}
        self.enemy_bot = enemy_bot
        self._rng = random.Random(random_seed) if random_seed is not None else random.Random()
        self.events: list[Event] = []
        self._rotation_cursor = 0  # if scenario has a fixed rotation, follow it step-by-step
        # C1: how skip_next_end_tick is decided on apply (default timing).
        self.skip_tick_condition: str = "timing"
        self.state.skip_tick_condition = "timing"  # type: ignore[assignment]
        # Reserved; unused in C1.
        self.BUFF_JUDGE_CLASS: str | None = None
        self.combat_rules = combat_rules or CombatRules()
        self.bus = EventBus(
            state=self.state,
            rules=self.combat_rules,
            transfer_rules=list(transfer_rules or []),
            log=self._log_event,
            acheron_e0_enabled=enable_acheron_e0,
            rng=self._rng,
        )
        self.state.event_emit = self.bus.emit
        self.state.settle_turn_start_dots = self._settle_turn_start_dots
        self.state.toughness_mode = scenario.toughness_mode
        self._insert_polling = False
        #AV-time while any living enemy is toughness-broken (realistic only).
        self.broken_av_total: float = 0.0
        self._broken_clock_prev: float = 0.0

    # -----------------------------
    # Main loop
    # -----------------------------
    def run(self) -> SimulationResult:
        """Run until enemies dead (non-immortal wipe) or max_cycles AV cap (C9)."""
        actions_taken: list[tuple[str, str]] = []
        self.bus.emit("battle_start", {})

        while not self.state.is_terminal() and not self._max_cycles_reached():
            stepped = self._step_normal_turn()
            if stepped is None:
                break
            actor, action = stepped
            actions_taken.append((actor.char.id, action.id))
            
            if self.scenario.damage_target and self.state.total_damage_dealt >= self.scenario.damage_target:
                break
        
        cycles = moc_cycle_from_av(self.state.av_clock)
        hit_cap = self._max_cycles_reached()
        if hit_cap:
            cycles = min(cycles, self.scenario.max_cycles)
        snap = self.state.snapshot()
        snap["max_cycles"] = self.scenario.max_cycles
        snap["hit_cycle_cap"] = hit_cap
        return SimulationResult(
            events=self.events,
            final_state_snapshot=snap,
            total_damage=self.state.total_damage_dealt,
            rounds_played=self.state.round_number,
            actions_taken=actions_taken,
            cycles_played=cycles,
        )

    def _max_cycles_reached(self) -> bool:
        """True when av_clock has reached the end AV of ``scenario.max_cycles``."""
        return self.state.av_clock >= moc_cycle_av_cap(self.scenario.max_cycles)

    def _step_normal_turn(self) -> tuple[CharacterState, Action] | None:
        """C7: pre-check legal/bot before AV commit; then next_actor → choose → execute.

        Empty legal / missing bot raises **before** clock advances.
        Bot selection runs after ``next_actor`` so Oracle lookahead sees committed AV.
        """
        peeked = self.state.peek_next_actor()
        if peeked is None:
            return None
        is_ally = peeked in self.state.allies
        bot = self._resolve_ally_bot(peeked) if is_ally else self.enemy_bot
        legal = self.legal_actions(peeked)
        if not legal:
            raise NoLegalActionError(peeked, self.state, reason="no_legal_actions")
        if bot is None:
            raise NoLegalActionError(peeked, self.state, reason="bot_missing")

        # AV interval about to elapse is under the pre-advance broken state.
        self._accumulate_broken_av()
        actor = self.state.next_actor()
        assert actor is not None and actor.char.id == peeked.char.id
        action, target_id = self._choose_action(actor)
        if action is None:
            raise NoLegalActionError(actor, self.state, reason="bot_returned_none")
        self._execute_action(actor, action, target_id, turn_kind="NORMAL")
        self.state.end_normal_turn(actor)
        self._maybe_advance_round(actor)
        self._poll_ready_ultimates()
        return actor, action

    def _accumulate_broken_av(self) -> None:
        """Credit elapsed AV while enemies were broken (for L1 broken_uptime)."""
        if self.scenario.toughness_mode != "realistic":
            self._broken_clock_prev = self.state.av_clock
            return
        dt = self.state.av_clock - self._broken_clock_prev
        if dt > 0.0 and any(
            e.is_alive and e.toughness_broken for e in self.state.enemies
        ):
            self.broken_av_total += dt
        self._broken_clock_prev = self.state.av_clock

    def broken_uptime(self) -> float:
        """Fraction of combat AV spent with at least one living enemy broken."""
        self._accumulate_broken_av()
        clock = float(self.state.av_clock)
        if clock <= 0.0:
            return 0.0
        return min(1.0, self.broken_av_total / clock)

    def pick_action(self, actor: CharacterState) -> tuple[Action | None, str | None]:
        """Public wrapper for bots / lookahead search."""
        return self._choose_action(actor)

    def _resolve_ally_bot(self, actor: CharacterState) -> Bot | None:
        char_id = actor.char.id
        if char_id in self.ally_bots:
            return self.ally_bots[char_id]
        if "*" in self.ally_bots:
            return self.ally_bots["*"]
        return self.ally_bot

    def legal_actions(self, actor: CharacterState) -> list[Action]:
        """Normal-turn legal actions. Ultimates / follow-ups are insert-only ( / )."""
        return [
            a
            for a in actor.char.build.actions
            if a.type not in (ActionType.ULTIMATE, ActionType.FOLLOW_UP)
            and self._is_legal(actor, a)
        ]

    def legal_insert_ultimates(self, actor: CharacterState) -> list[Action]:
        """Ultimates ready to fire via the insert channel."""
        return [
            a
            for a in actor.char.build.actions
            if a.type == ActionType.ULTIMATE and self._is_legal(actor, a)
        ]

    def execute_action(self, actor: CharacterState, action: Action, target_id: str | None) -> None:
        """Public wrapper for bots / lookahead search.

        Runs the action body then the NORMAL turn-end duration tick.
        ``next_actor`` already opened turn_start → action phase.
        After the normal turn, polls ally bots for ready ultimates ().
        """
        if action.type == ActionType.ULTIMATE:
            # Ultimates must use the insert channel.
            self.execute_inserted_action(actor, action, target_id)
            return
        self.state.skip_tick_condition = self.skip_tick_condition  # type: ignore[assignment]
        if self.state.current_turn is None or self.state.current_turn.owner_id != actor.char.id:
            self.state.begin_normal_turn(actor)
        self._execute_action(actor, action, target_id, turn_kind="NORMAL")
        self.state.end_normal_turn(actor)
        self._maybe_advance_round(actor)
        self._poll_ready_ultimates()

    def execute_inserted_action(
        self,
        actor: CharacterState,
        action: Action,
        target_id: str | None,
    ) -> None:
        """ insert channel: no next_actor, no AV reset, no turn_start/end ticks."""
        self.state.skip_tick_condition = self.skip_tick_condition  # type: ignore[assignment]
        # Do not open/close NORMAL turn context — inserts have no duration ticks.
        saved_turn = self.state.current_turn
        self.state.current_turn = None
        try:
            self._execute_action(actor, action, target_id, turn_kind="INSERTED")
        finally:
            self.state.current_turn = saved_turn

    def insert_action(
        self,
        actor: CharacterState,
        action: Action,
        target_id: str | None,
    ) -> None:
        """Alias for ``execute_inserted_action`` (T17 probe)."""
        self.execute_inserted_action(actor, action, target_id)

    def _poll_ready_ultimates(self) -> None:
        """After a normal action: ask ally bots whose ult resource is ready."""
        if self._insert_polling:
            return
        self._insert_polling = True
        try:
            while not self.state.is_terminal():
                fired = False
                for ally in list(self.state.allies):
                    if not ally.is_alive:
                        continue
                    ready = self.legal_insert_ultimates(ally)
                    if not ready:
                        continue
                    bot = self._resolve_ally_bot(ally)
                    if bot is None:
                        continue
                    from hsrsim.simulator.bots import RandomBot

                    if isinstance(bot, RandomBot):
                        action, target_id = bot.select_action(
                            ally, self.state, ready, rng=self._rng
                        )
                    else:
                        action, target_id = bot.select_action(ally, self.state, ready)
                    if action is None or action.type != ActionType.ULTIMATE:
                        continue
                    if not self._is_legal(ally, action):
                        continue
                    self.execute_inserted_action(ally, action, target_id)
                    fired = True
                    break
                if not fired:
                    break
        finally:
            self._insert_polling = False

    def simulate_actions(
        self,
        max_actions: int,
        ally_bot: Bot | None = None,
        enemy_bot: Bot | None = None,
    ) -> float:
        """Run up to `max_actions` turns on current state; return total damage dealt."""
        saved_ally, saved_enemy = self.ally_bot, self.enemy_bot
        if ally_bot is not None:
            self.ally_bot = ally_bot
        if enemy_bot is not None:
            self.enemy_bot = enemy_bot
        try:
            for _ in range(max_actions):
                if self.state.is_terminal() or self._max_cycles_reached():
                    break
                if self._step_normal_turn() is None:
                    break
        finally:
            self.ally_bot, self.enemy_bot = saved_ally, saved_enemy
        return self.state.total_damage_dealt

    def _maybe_advance_round(self, actor: CharacterState) -> None:
        """Increment round_number only. Does not tick buffs or regenerate SP."""
        if all(c is actor or c.av_remaining > 0 for c in self.state.allies + self.state.enemies):
            self.state.round_number += 1
            self._tick_round()

    # -----------------------------
    # Action selection
    # -----------------------------
    def _choose_action(self, actor: CharacterState) -> tuple[Action | None, str | None]:
        """Either follow scripted rotation or ask the bot."""
        # Path 1: scripted rotation
        if self.scenario.rotation and self._rotation_cursor < len(self.scenario.rotation.steps):
            step = self.scenario.rotation.steps[self._rotation_cursor]
            if step.actor_id == actor.char.id:
                action = next((a for a in actor.char.build.actions if a.id == step.action_id), None)
                if action and action.type != ActionType.ULTIMATE and self._is_legal(actor, action):
                    self._rotation_cursor += 1
                    return action, step.target_id or self._default_target(actor)

        # Path 2: bot decides
        is_ally = actor in self.state.allies
        bot = self._resolve_ally_bot(actor) if is_ally else self.enemy_bot
        legal = self.legal_actions(actor)
        if not legal or bot is None:
            return None, None
        from hsrsim.simulator.bots import RandomBot

        if isinstance(bot, RandomBot):
            return bot.select_action(actor, self.state, legal, rng=self._rng)
        return bot.select_action(actor, self.state, legal)

    def _is_legal(self, actor: CharacterState, action: Action) -> bool:
        """Check if an action is currently executable."""
        # SP cost check
        if action.sp_cost > 0 and self.state.sp_team_pool < action.sp_cost:
            return False
        # Ultimate resource gate (C5)
        if action.type == ActionType.ULTIMATE:
            res = resolve_ult_resource(actor.char, action)
            if not ult_resource_ready(
                actor.energy_current, actor.variables, res
            ):
                return False
        # Custom requirements (TODO: full DSL parser)
        for req in action.requires:
            if not self._evaluate_requirement(actor, req):
                return False
        return True

    def _evaluate_requirement(self, actor: CharacterState, req: str) -> bool:
        """Minimal requirement DSL: `variable>=9`, `hp_pct>0.5`, or bare effect id."""
        req = req.strip()
        match = _REQUIREMENT_PATTERN.match(req)
        if not match:
            if any(e.id == req for e in actor.active_effects):
                return True
            known_effect_ids = {e.id for e in actor.char.build.effects}
            if req in known_effect_ids:
                return False
            is_ally = actor in self.state.allies
            opponents = self.state.enemies if is_ally else self.state.allies
            for opp in opponents:
                if opp.is_alive and any(e.id == req for e in opp.active_effects):
                    return True
            logger.warning("Unparsed requirement %r — treating as unsatisfied", req)
            return False
        var = match.group("var")
        op = match.group("op")
        threshold = float(match.group("val"))
        if var == "hp_pct":
            current = actor.hp_current / max(actor.char.build.stats.hp_max, 1e-9)
        elif any(e.id == var for e in actor.active_effects):
            current = 1.0
        else:
            current = actor.variables.get(var, 0.0)
        return _COMPARE_OPS[op](current, threshold)

    def _default_target(self, actor: CharacterState) -> str:
        """Ally→first alive enemy; enemy→BaseAggro-weighted random ally ()."""
        is_ally = actor in self.state.allies
        if is_ally:
            for c in self.state.enemies:
                if c.is_alive:
                    return c.char.id
            return ""
        from hsrsim.enemies.path_base_aggro import pick_weighted_ally

        picked = pick_weighted_ally(self.state.allies, self._rng)
        return picked.char.id if picked is not None else ""

    def _effective_effect_target(self, eff: Effect, action: Action) -> EffectTarget:
        """Effect.target wins; default SELF falls back to legacy Action.effect_target."""
        if eff.target != EffectTarget.SELF:
            return eff.target
        if action.effect_target == "enemy":
            return EffectTarget.SINGLE_ENEMY
        if action.effect_target == "all_allies":
            return EffectTarget.ALL_ALLIES
        return EffectTarget.SELF

    def _resolve_effect_targets_for_action(
        self,
        actor: CharacterState,
        action: Action,
        eff: Effect,
        target_enemy_id: str | None,
    ) -> list[CharacterState]:
        is_ally = actor in self.state.allies
        allies = self.state.allies if is_ally else self.state.enemies
        enemies = self.state.enemies if is_ally else self.state.allies
        target = self._effective_effect_target(eff, action)

        if target == EffectTarget.SELF:
            return [actor]
        if target == EffectTarget.SINGLE_ALLY:
            if target_enemy_id:
                found = self.state.find_char(target_enemy_id)
                if found is not None and found.is_alive and found in allies:
                    return [found]
            alive_allies = [a for a in allies if a.is_alive]
            if not alive_allies:
                return []
            return [
                min(
                    alive_allies,
                    key=lambda c: c.hp_current / max(c.char.build.stats.hp_max, 1.0),
                )
            ]
        if target == EffectTarget.ALL_ALLIES:
            return [a for a in allies if a.is_alive]
        if target == EffectTarget.SINGLE_ENEMY:
            if target_enemy_id:
                found = self.state.find_char(target_enemy_id)
                if found is not None and found.is_alive:
                    return [found]
            alive_enemies = [e for e in enemies if e.is_alive]
            return [alive_enemies[0]] if alive_enemies else []
        if target == EffectTarget.ALL_ENEMIES:
            return [e for e in enemies if e.is_alive]
        return [actor]

    def _apply_action_effects(
        self,
        actor: CharacterState,
        action: Action,
        target_enemy_id: str | None,
    ) -> None:
        """Apply each effect to the correct recipient(s) based on Effect.target."""
        for eff_id in action.applies_effects:
            eff = next((e for e in actor.char.build.effects if e.id == eff_id), None)
            if eff is None:
                continue
            targets = self._resolve_effect_targets_for_action(
                actor, action, eff, target_enemy_id
            )
            applied_recipients: list[str] = []
            for recipient in targets:
                if not eff.is_buff and eff.base_chance is not None:
                    chance = debuff_hit_chance(
                        float(eff.base_chance),
                        float(actor.char.build.stats.ehr),
                        float(recipient.char.build.stats.effect_res),
                    )
                    roll = self._rng.random()
                    if roll > chance:
                        self._log_event(
                            "effect_resisted",
                            {
                                "source": actor.char.id,
                                "target_id": recipient.char.id,
                                "effect_id": eff_id,
                                "base_chance": eff.base_chance,
                                "ehr": actor.char.build.stats.ehr,
                                "target_effect_res": recipient.char.build.stats.effect_res,
                                "final_chance": chance,
                                "roll": roll,
                            },
                        )
                        continue
                result = recipient.apply_effect(eff, source_id=actor.char.id)
                applied_recipients.append(recipient.char.id)
                is_enemy = recipient in self.state.enemies
                self.bus.emit(
                    "effect_applied",
                    {
                        "effect_id": eff_id,
                        "is_debuff": not eff.is_buff,
                        "source_id": actor.char.id,
                        "target_id": recipient.char.id,
                        "target_side": "enemy" if is_enemy else "ally",
                        "was_refresh": result["was_refresh"],
                        "stacks_before": result["stacks_before"],
                        "stacks_after": result["stacks_after"],
                        "apply_source": APPLY_SOURCE_ACTION_CAST,
                    },
                )
                if eff.action_advance_pct is not None and eff.action_advance_pct > 0:
                    if not (
                        eff.action_advance_skip_self
                        and recipient.char.id == actor.char.id
                    ):
                        delta = advance_forward(recipient, eff.action_advance_pct)
                        self._log_event(
                            "action_advanced",
                            {
                                "source": actor.char.id,
                                "target_id": recipient.char.id,
                                "effect_id": eff_id,
                                "advance_pct": eff.action_advance_pct,
                                "av_delta": delta,
                                "av_remaining": recipient.av_remaining,
                            },
                        )
            self._log_event(
                "effect_applied",
                {
                    "source": actor.char.id,
                    "actor_id": actor.char.id,
                    "effect_id": eff_id,
                    "effect_target_type": self._effective_effect_target(eff, action).value,
                    "target_scope": action.effect_target,
                    "recipients": applied_recipients,
                },
            )

    # -----------------------------
    # Action execution
    # -----------------------------
    def _execute_action(
        self,
        actor: CharacterState,
        action: Action,
        target_id: str | None,
        *,
        turn_kind: str | None = None,
    ):
        """Execute an action: deal damage, apply effects, change resources."""
        primary_target = self.state.find_char(target_id) if target_id else None
        action_kind = (
            action.type.value if hasattr(action.type, "value") else str(action.type)
        )
        if turn_kind is None:
            turn_kind = "NORMAL"
            if self.state.current_turn is not None:
                turn_kind = self.state.current_turn.kind
        self.bus.begin_action(
            actor_id=actor.char.id,
            action_id=action.id,
            action_kind=action_kind,
            turn_kind=turn_kind,
            target_id=target_id,
        )
        # Resource costs / gains (: temp max via action.sp_pool_temp_max; log waste)
        if action.sp_cost > 0:
            amount = int(action.sp_cost)
            self.state.sp_team_pool = max(0, self.state.sp_team_pool - amount)
            self.bus.emit(
                "sp_consumed",
                {
                    "consumer_id": actor.char.id,
                    "amount": amount,
                    "action_id": action.id,
                },
            )
        elif action.sp_cost < 0:
            raw_gain = -action.sp_cost
            before = self.state.sp_team_pool
            soft_cap = self.state.sp_team_max
            # Temporary ceiling for this gain (e.g. ult overflow to 10).
            # Gains never decrease an already-overflowed pool.
            gain_cap = (
                int(action.sp_pool_temp_max)
                if action.sp_pool_temp_max is not None
                else soft_cap
            )
            uncapped = before + raw_gain
            applied_pool = max(before, min(gain_cap, uncapped))
            wasted = max(0, uncapped - applied_pool)
            self.state.sp_team_pool = applied_pool
            if wasted > 0:
                self._log_event(
                    "sp_gain_truncated",
                    {
                        "actor_id": actor.char.id,
                        "action_id": action.id,
                        "raw_gain": raw_gain,
                        "before": before,
                        "after": applied_pool,
                        "cap_used": gain_cap,
                        "sp_team_max": soft_cap,
                        "waste": wasted,
                    },
                )
            #raw_gain (incl. truncated overflow) drives LC 23021 blaze.
            self.bus.emit(
                "sp_gained",
                {
                    "actor_id": actor.char.id,
                    "action_id": action.id,
                    "raw_gain": int(raw_gain),
                    "applied": int(applied_pool - before),
                    "waste": int(wasted),
                },
            )
        
        if action.energy_cost > 0:
            actor.energy_current = max(0, actor.energy_current - action.energy_cost)
        elif action.energy_cost < 0:
            # Declared Action gain: raw magnitude × Stats.err ( / EVENT_TRIGGER_DESIGN §6.2)
            grant_energy(
                actor,
                -action.energy_cost,
                source="action.energy_cost",
                log=self._log_event,
            )
        
        # Variable changes (Nihility stacks, etc)
        for var_id, delta in action.variable_changes.items():
            actor.variables[var_id] = actor.variables.get(var_id, 0) + delta
            self._log_event("variable_changed", {
                "actor_id": actor.char.id, "variable": var_id,
                "delta": delta, "new_value": actor.variables[var_id],
            })

        if action.heal_tally_add is not None:
            hp_ratio, flat = action.heal_tally_add
            tally_add = actor.char.build.stats.hp_max * hp_ratio + flat
            actor.variables["heal_accumulated"] = (
                actor.variables.get("heal_accumulated", 0.0) + tally_add
            )
            self._log_event("variable_changed", {
                "actor_id": actor.char.id,
                "variable": "heal_accumulated",
                "delta": tally_add,
                "new_value": actor.variables["heal_accumulated"],
            })

        used_payload = {
            "actor_id": actor.char.id,
            "action_id": action.id,
            "action_kind": action_kind,
            "target_id": target_id,
            "turn_kind": turn_kind,
            "kind": "inserted" if turn_kind == "INSERTED" else "normal",
        }
        self.bus.emit("action_used", used_payload)
        if action.type == ActionType.ULTIMATE or action_kind == "ultimate":
            self.bus.emit("ultimate_used", used_payload)
        
        # Apply effects (each Effect declares self / ally / enemy target)
        self._apply_action_effects(actor, action, target_id)

        # Deal damage
        damage_total = 0.0
        if (
            actor.char.id == "acheron"
            and action.id == "acheron_ult"
            and self.combat_rules.acheron_ult_e11h
        ):
            before = self.state.total_damage_dealt
            self._resolve_acheron_ult(actor, primary_target, action)
            damage_total += self.state.total_damage_dealt - before
        else:
            for dmg_instance in action.damage_instances:
                before = self.state.total_damage_dealt
                self._resolve_damage(actor, primary_target, dmg_instance, action)
                damage_total += self.state.total_damage_dealt - before

        # E1.5: ally-hit before action_resolved so Trend burn can enter R1 window.
        if actor in self.state.enemies:
            self._resolve_enemy_ally_hit(actor, action)

        self.bus.emit(
            "action_resolved",
            {
                **used_payload,
                "damage_total": damage_total,
            },
        )
        self.bus.end_action()

        self._log_event("action_executed", {
            "actor_id": actor.char.id,
            "action_id": action.id,
            "target_id": target_id,
            "av_clock": self.state.av_clock,
            "kind": used_payload["kind"],
            "turn_kind": turn_kind,
            "resources_after": {
                "sp_pool": self.state.sp_team_pool,
                "actor_energy": actor.energy_current,
                "actor_variables": dict(actor.variables),
            },
        })

        #post-action Blind Bet / FUA insert (own windows).
        if actor.char.id == "aventurine" and action.id == "aventurine_ult":
            n = int(self._rng.randint(1, 7))
            self.bus.grant_blind_bet(actor, float(n), source="aventurine_ult_blind_bet")
        if (
            actor.char.id == "aventurine"
            and action.id == AVENTURINE_FUA_ACTION_ID
        ):
            # Trace 1304103: FUA grants Fortified Wager (existence+duration).
            self.bus.refresh_aventurine_chip_all_allies(source_id=actor.char.id)
        self._poll_aventurine_fua()

    def _resolve_enemy_ally_hit(
        self, enemy: CharacterState, action: Action
    ) -> None:
        """Dummy ally-hit (0 dmg). Hit count: CombatRules override or standard=1."""
        from hsrsim.enemies.benchmark_dummy import DUMMY_HITS_PER_ENEMY_ACTION
        from hsrsim.enemies.path_base_aggro import pick_weighted_ally

        n_hits = self.combat_rules.hits_per_enemy_action
        if n_hits is None:
            n_hits = DUMMY_HITS_PER_ENEMY_ACTION
        force_id = self.combat_rules.ally_hit_force_id
        for _ in range(int(n_hits)):
            if force_id:
                target = self.state.find_char(force_id)
                if target is None or not target.is_alive or target not in self.state.allies:
                    target = pick_weighted_ally(self.state.allies, self._rng)
            else:
                target = pick_weighted_ally(self.state.allies, self._rng)
            if target is None:
                return
            action_kind = (
                action.type.value if hasattr(action.type, "value") else str(action.type)
            )
            self._log_event(
                "ally_hit",
                {
                    "attacker_id": enemy.char.id,
                    "defender_id": target.char.id,
                    "action_id": action.id,
                    "damage": 0.0,
                    "hit_target_rule": (
                        f"force:{force_id}"
                        if force_id
                        else "path_base_aggro_weighted"
                    ),
                },
            )
            self.bus.emit(
                "hit_taken",
                {
                    "defender_id": target.char.id,
                    "attacker_id": enemy.char.id,
                    "action_id": action.id,
                    "action_kind": action_kind,
                    "damage": 0.0,
                    "damage_type": "direct",
                },
            )

    def _poll_aventurine_fua(self) -> None:
        """Insert Blind Bet FUA while charge ≥ 7 (consume via action.variable_changes)."""
        if getattr(self, "_aventurine_fua_polling", False):
            return
        self._aventurine_fua_polling = True
        try:
            while True:
                av = self.state.find_char("aventurine")
                if av is None or not av.is_alive:
                    return
                if float(av.variables.get(BLIND_BET_ID, 0.0)) < 7.0:
                    return
                action = next(
                    (
                        a
                        for a in av.char.build.actions
                        if a.id == AVENTURINE_FUA_ACTION_ID
                    ),
                    None,
                )
                if action is None or not self._is_legal(av, action):
                    return
                target_id = self._default_target(av)
                self.execute_inserted_action(av, action, target_id or None)
        finally:
            self._aventurine_fua_polling = False

    def _resolve_acheron_ult(
        self,
        actor: CharacterState,
        primary_target: CharacterState | None,
        action: Action,
    ) -> None:
        """/h-2: Rainblade erase-per-slash, Thunder Core, talent −20% RES.

        Each Rainblade erases up to 3 Crimson Knots; erase AoE MV =
        ``min(0.60, 0.15 × (1 + k))`` where k = layers erased on that slash.
        Thunder: each Rainblade that hits a knotted target +1 stack (max 3).
        Stygian Resurge: +6×25% ATK; then strip remaining knots.
        """
        instances = list(action.damage_instances)
        rainblades = instances[:3]
        resurge = instances[3] if len(instances) > 3 else None
        res_eff = Effect(
            id="acheron_ult_res_down",
            name="红叶时雨·抗性下降",
            is_buff=False,
            target=EffectTarget.ALL_ENEMIES,
            duration_turns=1,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(target_stat="res", operation="add", value=-0.20)
            ],
        )
        for enemy in self.state.enemies:
            if enemy.is_alive:
                enemy.apply_effect(res_eff, source_id=actor.char.id)
        thunder_hit = DamageInstance(
            multiplier=0.25,
            element=Element.LIGHTNING,
            damage_type=DamageType.DIRECT,
            target="single",
            toughness_dmg=0.0,
        )
        thunder_tpl = Effect(
            id="acheron_thunder_core",
            name="雷心",
            is_buff=True,
            target=EffectTarget.SELF,
            duration_turns=3,
            max_stacks=3,
            current_stacks=1,
            modifiers=[
                EffectModifier(target_stat="dmg_boost", operation="add", value=0.30)
            ],
        )
        for inst in rainblades:
            had_knot = (
                primary_target is not None
                and effect_stacks(primary_target, CRIMSON_KNOT_ID) > 0
            )
            self._resolve_damage(actor, primary_target, inst, action)
            if had_knot:
                actor.apply_effect(thunder_tpl, source_id=actor.char.id)
                k = self._consume_crimson_knots(primary_target, 3)
                if k > 0:
                    extra_mv = min(0.60, 0.15 * (1.0 + float(k)))
                    extra_tpl = DamageInstance(
                        multiplier=extra_mv,
                        element=Element.LIGHTNING,
                        damage_type=DamageType.DIRECT,
                        target="aoe",
                        toughness_dmg=0.0,
                    )
                    for enemy in list(self.state.enemies):
                        if enemy.is_alive:
                            self._resolve_damage(actor, enemy, extra_tpl, action)
        if resurge is not None:
            for enemy in list(self.state.enemies):
                if enemy.is_alive:
                    self._resolve_damage(actor, enemy, resurge, action)
            for _ in range(6):
                tgt = primary_target
                alive = [e for e in self.state.enemies if e.is_alive]
                if len(alive) > 1:
                    tgt = self._rng.choice(alive)
                if tgt is not None and tgt.is_alive:
                    self._resolve_damage(actor, tgt, thunder_hit, action)
        for enemy in self.state.enemies:
            if effect_stacks(enemy, CRIMSON_KNOT_ID) > 0:
                enemy.remove_effect(CRIMSON_KNOT_ID)
        for unit in (*self.state.enemies, actor):
            unit.remove_effect("acheron_ult_res_down")

    def _consume_crimson_knots(
        self, holder: CharacterState | None, n: int
    ) -> int:
        if holder is None or n <= 0:
            return 0
        for e in holder.active_effects:
            if e.id != CRIMSON_KNOT_ID:
                continue
            take = min(int(n), int(e.current_stacks))
            e.current_stacks -= take
            if e.current_stacks <= 0:
                holder.remove_effect(CRIMSON_KNOT_ID)
            return take
        return 0

    def _settle_turn_start_dots(self, holder: CharacterState) -> None:
        """: on NORMAL turn_start, settle DoTs that carry ``dot_instance``.

        Reuses ``compute_damage`` with ``DamageType.DOT`` (existing zone matrix;
        no invented formula). Effects with ``tick_timing=turn_start`` but no
        ``dot_instance`` only lose duration via ``tick_effects`` (gap if data
        never binds MV).
        """
        if not holder.is_alive:
            return
        for eff in list(holder.active_effects):
            if eff.tick_timing != "turn_start" or eff.dot_instance is None:
                continue
            source: CharacterState | None = holder
            if eff.dot_source_id:
                source = self.state.find_char(eff.dot_source_id) or holder
            dmg = eff.dot_instance.model_copy(
                update={"damage_type": DamageType.DOT, "toughness_dmg": 0.0}
            )
            tick_action = Action(
                id=f"dot:{eff.id}",
                name=eff.name or eff.id,
                type=ActionType.TALENT,
                description="turn_start DoT tick",
            )
            ctx = self._build_damage_context(source, holder, dmg, tick_action)
            damage_value, zone_breakdown = compute_damage(ctx)
            was_alive = holder.is_alive
            self._apply_hp_damage(holder, damage_value)
            self.state.total_damage_dealt += damage_value
            self._log_event(
                "damage",
                {
                    "attacker": source.char.id,
                    "defender": holder.char.id,
                    "action": tick_action.id,
                    "element": dmg.element.value
                    if hasattr(dmg.element, "value")
                    else str(dmg.element),
                    "damage_type": DamageType.DOT.value,
                    "raw_damage": damage_value,
                    "zone_breakdown": zone_breakdown,
                    "defender_hp_after": holder.hp_current,
                    "dot_effect_id": eff.id,
                },
            )
            payload = {
                "holder_id": holder.char.id,
                "source_id": source.char.id,
                "effect_id": eff.id,
                "damage": damage_value,
                "turn_kind": "NORMAL",
            }
            self.bus.emit("dot_tick", payload)
            if was_alive and not holder.is_alive:
                self.bus.emit(
                    "enemy_killed",
                    {
                        "victim_id": holder.char.id,
                        "killer_id": source.char.id,
                        "action_id": tick_action.id,
                    },
                )

    def _apply_hp_damage(self, defender: CharacterState, damage_value: float) -> None:
        """Subtract HP. Immortal units floor at 1 (C9); damage still fully counted by caller."""
        floor = 1.0 if defender.char.immortal else 0.0
        defender.hp_current = max(floor, defender.hp_current - damage_value)

    def _resolve_damage(
        self,
        attacker: CharacterState,
        defender: CharacterState | None,
        dmg: DamageInstance,
        action: Action,
    ):
        """Compute and apply a single damage instance."""
        if defender is None or not defender.is_alive:
            return
        if dmg.requires_defender_debuff:
            if not any(not e.is_buff for e in defender.active_effects):
                return
        
        # Build DamageContext from current state
        ctx = self._build_damage_context(attacker, defender, dmg, action)
        
        # Compute via the zone pipeline
        damage_value, zone_breakdown = compute_damage(ctx)
        
        # Apply damage (immortal: HP ≥ 1, still full damage to totals)
        was_alive = defender.is_alive
        self._apply_hp_damage(defender, damage_value)
        self.state.total_damage_dealt += damage_value
        
        # Toughness damage (: always_unbroken skips; realistic reduces until break)
        if self.scenario.toughness_mode != "always_unbroken" and not defender.toughness_broken:
            tough_dmg = dmg.toughness_dmg
            if dmg.element in defender.char.weaknesses:
                before = defender.toughness_current
                defender.toughness_current = max(0, defender.toughness_current - tough_dmg)
                if before > 0 and defender.toughness_current <= 0:
                    # Break action delay: UNKNOWN in L2/datamine → no delay .
                    self._log_event(
                        "toughness_broken",
                        {
                            "defender": defender.char.id,
                            "attacker": attacker.char.id,
                            "action": action.id,
                            "break_action_delay": "UNKNOWN",
                        },
                    )
        
        # Log structured event
        self._log_event("damage", {
            "attacker": attacker.char.id,
            "defender": defender.char.id,
            "action": action.id,
            "element": dmg.element.value,
            "damage_type": dmg.damage_type.value,
            "raw_damage": damage_value,
            "zone_breakdown": zone_breakdown,
            "defender_hp_after": defender.hp_current,
        })
        action_kind = (
            action.type.value if hasattr(action.type, "value") else str(action.type)
        )
        self.bus.emit(
            "damage_hit",
            {
                "attacker_id": attacker.char.id,
                "defender_id": defender.char.id,
                "action_id": action.id,
                "action_kind": action_kind,
                "damage": damage_value,
            },
        )
        self.bus.emit(
            "hit_taken",
            {
                "defender_id": defender.char.id,
                "attacker_id": attacker.char.id,
                "action_id": action.id,
                "damage": damage_value,
                "damage_type": dmg.damage_type.value
                if hasattr(dmg.damage_type, "value")
                else str(dmg.damage_type),
            },
        )
        if was_alive and not defender.is_alive:
            self.bus.emit(
                "enemy_killed",
                {
                    "victim_id": defender.char.id,
                    "killer_id": attacker.char.id,
                    "action_id": action.id,
                },
            )
        self._apply_true_followup(attacker, defender, damage_value, dmg, action)

    def _apply_true_followup(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        primary_damage: float,
        source_dmg: DamageInstance,
        action: Action,
    ) -> None:
        """Ally hits under Cyrene aura: append pct of dealt damage as true damage."""
        if primary_damage <= 0 or not defender.is_alive:
            return
        if source_dmg.damage_type in (
            DamageType.TRUE,
            DamageType.DOT,
            DamageType.BREAK,
            DamageType.SUPER_BREAK,
        ):
            return
        atk_buffs = aggregate_effects(attacker.active_effects, side="attacker")
        pct = atk_buffs.get("true_followup", 0.0)
        if pct <= 0:
            return
        true_dmg = primary_damage * pct
        self._apply_hp_damage(defender, true_dmg)
        self.state.total_damage_dealt += true_dmg
        self._log_event("true_followup", {
            "attacker": attacker.char.id,
            "defender": defender.char.id,
            "source_action": action.id,
            "primary_damage": primary_damage,
            "followup_pct": pct,
            "true_damage": true_dmg,
            "defender_hp_after": defender.hp_current,
        })

    def _build_damage_context(
        self,
        attacker: CharacterState,
        defender: CharacterState,
        dmg: DamageInstance,
        action: Action,
    ) -> DamageContext:
        """Aggregate stats + active effects into DamageContext.

        Attacker-side buffs (dmg_boost, crit, res_pen, break_effect) come from attacker.
        Defender-side debuffs (def_reduction, vuln, weaken) come from defender.
        """
        a_stats = attacker.char.build.stats
        d_stats = defender.char.build.stats
        atk_buffs = aggregate_effects(attacker.active_effects, side="attacker")
        def_debuffs = aggregate_effects(defender.active_effects, side="defender")
        # Figment aura: enemies take increased damage. Only when hitting an enemy.
        aura_vuln = 0.0
        unnerved_cd = 0.0
        if defender in self.state.enemies:
            ally_bags = [a.active_effects for a in self.state.allies if a.is_alive]
            aura_vuln = figment_aura_vuln(
                attacker_effects=attacker.active_effects,
                ally_effects_bags=ally_bags,
            )
            # 惊惶: 我方击中该目标时暴击伤害提高（effect on defender → attacker CD）.
            for eff in defender.active_effects:
                if eff.id != AVENTURINE_UNNERVED_ID:
                    continue
                for mod in eff.modifiers:
                    if mod.target_stat == "crit_dmg" and mod.operation == "add":
                        unnerved_cd += float(mod.value) * float(eff.current_stacks)

        dmg_boost_pct = (
            a_stats.dmg_boost.get("all", 0.0)
            + a_stats.dmg_boost.get(dmg.element, 0.0)
            + stat_bonus_for_element(atk_buffs, "dmg_boost", dmg.element)
            + atk_buffs.get("dmg_boost", 0.0)
        )
        # E2: Pioneer 2pc — DMG vs debuffed enemies (paper: acheron).
        enemy_debuff_n = 0
        if defender in self.state.enemies:
            enemy_debuff_n = sum(
                1 for e in defender.active_effects if not getattr(e, "is_buff", True)
            )
        from hsrsim.loadout.relic_conditionals import (
            collect_relic_crit_dmg_l2,
            collect_relic_def_ignore_l2,
            collect_relic_dmg_boost_l2,
        )

        dmg_boost_pct += collect_relic_dmg_boost_l2(
            attacker.char, enemy_debuff_count=enemy_debuff_n
        )
        pioneer_doubled = bool(
            getattr(attacker, "_pioneer_double_active", False)
        )
        relic_cd = collect_relic_crit_dmg_l2(
            attacker.char,
            enemy_debuff_count=enemy_debuff_n,
            pioneer_doubled=pioneer_doubled,
        )
        enemy_dot_n = 0
        if defender in self.state.enemies:
            enemy_dot_n = sum(
                1
                for e in defender.active_effects
                if getattr(e, "dot_instance", None) is not None
            )
        relic_def_ignore = collect_relic_def_ignore_l2(
            attacker.char, enemy_dot_count=enemy_dot_n
        )
        # LC 23024: vs Mirage Fizzle targets, +p1 dmg; ultimate +p2 extra.
        if (
            attacker.char.light_cone_id == 23024
            and any(e.id == LC_MIRAGE_ID for e in defender.active_effects)
        ):
            params = lc_param_list(
                23024, int(attacker.char.light_cone_superimposition)
            )
            dmg_boost_pct += float(params[1])
            action_kind = (
                action.type.value if hasattr(action.type, "value") else str(action.type)
            )
            if action_kind == "ultimate" or action.type == ActionType.ULTIMATE:
                dmg_boost_pct += float(params[2])

        res_pen_pct = (
            a_stats.res_pen.get("all", 0.0)
            + a_stats.res_pen.get(dmg.element, 0.0)
            + stat_bonus_for_element(atk_buffs, "res_pen", dmg.element)
            + atk_buffs.get("res_pen", 0.0)
        )
        #no fictional weakness RES PEN. Weakness → RES=0 on dummy
        # (element_res table); additive RES zone must not invent +0.20 pen.

        mit_layers: list[float] = []
        if "mit" in def_debuffs:
            mit_layers.append(def_debuffs["mit"])

        def_reduction_pct = def_debuffs.get("def_reduction", 0.0)
        vuln_pct = (
            def_debuffs.get("vuln_apply", 0.0)
            + def_debuffs.get("vuln", 0.0)
            + float(aura_vuln)
            + collect_zone_ult_vuln(
                list(self.state.active_zones),
                action.type.value
                if hasattr(action.type, "value")
                else str(action.type),
            )
        )

        from hsrsim.enemies.benchmark_dummy import element_resistance

        base_res = element_resistance(defender.char, dmg.element)
        defender_res_pct = base_res + float(def_debuffs.get("res", 0.0))

        if dmg.scaling_stat == "defense":
            scaling_base = a_stats.defense + atk_buffs.get("defense", 0.0)
        elif dmg.scaling_stat == "hp":
            scaling_base = a_stats.hp_max + atk_buffs.get("hp_max", 0.0)
        elif dmg.scaling_stat == "heal_accumulated":
            scaling_base = attacker.variables.get("heal_accumulated", 0.0)
        else:
            scaling_base = a_stats.atk + atk_buffs.get("atk", 0.0)

        skill_mult = dmg.multiplier
        if dmg.memoria_base is not None:
            stacks = float(attacker.variables.get("memoria", 0.0))
            threshold = dmg.memoria_enhanced_threshold
            if stacks >= threshold and dmg.memoria_enhanced_per_stack is not None:
                skill_mult = dmg.memoria_enhanced_per_stack * stacks
            else:
                per_four = dmg.memoria_per_four or 0.0
                skill_mult = dmg.memoria_base + math.floor(stacks / 4) * per_four

        # DoT DMG% may live on attacker (Kafka LC) or as a debuff on target (Jiaoqiu).
        attacker_dot_boost_pct = (
            atk_buffs.get("dot_dmg_boost", 0.0)
            + atk_buffs.get("dot_boost", 0.0)
            + def_debuffs.get("dot_dmg_boost", 0.0)
            + def_debuffs.get("dot_boost", 0.0)
        )

        crit_rate = a_stats.crit_rate + atk_buffs.get("crit_rate", 0.0)
        # Acheron E1: +18% CR when defender has any debuff.
        if (
            attacker.char.id == "acheron"
            and int(attacker.char.eidolon) >= 1
            and any(not e.is_buff for e in defender.active_effects)
        ):
            crit_rate += ACHERON_E1_CRIT_RATE
        crit_rate = min(1.0, crit_rate)

        original_mult = float(atk_buffs.get("original_mult", 1.0))
        if attacker.char.id == "acheron":
            nihility_allies = count_units(
                self.state,
                "allies",
                {"path": "nihility", "is_alive": True},
                exclude="self",
                self_id=attacker.char.id,
            )
            original_mult *= abyss_multiplier(
                nihility_allies, int(attacker.char.eidolon)
            )

        return DamageContext(
            attacker_level=attacker.char.level,
            attacker_atk=scaling_base,
            attacker_crit_rate=crit_rate,
            attacker_crit_dmg=a_stats.crit_dmg
            + atk_buffs.get("crit_dmg", 0.0)
            + unnerved_cd
            + float(relic_cd),
            attacker_break_effect=a_stats.break_effect + atk_buffs.get("break_effect", 0.0),
            attacker_dmg_boost_pct=dmg_boost_pct,
            attacker_res_pen_pct=res_pen_pct,
            attacker_def_ignore_pct=atk_buffs.get("def_ignore", 0.0)
            + float(relic_def_ignore),
            attacker_def_reduction_pct=def_reduction_pct,
            attacker_dot_boost_pct=attacker_dot_boost_pct,
            defender_level=defender.char.level,
            defender_def=d_stats.defense,
            defender_res_pct=defender_res_pct,
            defender_vuln_pct=vuln_pct,
            defender_mit_layers=mit_layers,
            defender_toughness_broken=(
                False
                if self.scenario.toughness_mode == "always_unbroken"
                else defender.toughness_broken
            ),
            skill_multiplier=skill_mult,
            extra_flat_damage=0.0,
            damage_type=dmg.damage_type,
            element=dmg.element,
            elation_value=a_stats.elation + atk_buffs.get("elation", 0.0),
            punchline_value=a_stats.punchline + atk_buffs.get("punchline", 0.0),
            merrymake_value=a_stats.merrymake + atk_buffs.get("merrymake", 0.0),
            original_mult=original_mult,
            weaken_pct=def_debuffs.get("weaken", 0.0),
            super_break_boost_pct=atk_buffs.get("super_break_boost", 0.0)
            + atk_buffs.get("super_break_dmg", 0.0),
        )

    # -----------------------------
    # Round-end bookkeeping
    # -----------------------------
    def _tick_round(self):
        """Hook after round_number increments.

        C1: buff durations tick on the holder's own turn (not here).
        SP+1 removed — basic attacks already use sp_cost=-1.
        """
        return

    # -----------------------------
    # Logging
    # -----------------------------
    def _log_event(self, event_type: str, payload: dict[str, Any]):
        self.events.append(Event(
            timestamp=self.state.av_clock,
            round=self.state.round_number,
            event_type=event_type,
            payload=payload,
        ))


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    """Run a scenario from JSON."""
    import argparse
    parser = argparse.ArgumentParser(description="HSR combat simulator")
    parser.add_argument("--config", type=str, required=True, help="Path to scenario JSON")
    parser.add_argument("--bot", type=str, default="greedy", choices=["greedy", "heuristic", "random"])
    parser.add_argument("--output", type=str, default=None, help="Path to write event JSON")
    args = parser.parse_args()
    
    with open(args.config, encoding="utf-8") as f:
        scenario = Scenario.model_validate_json(f.read())
    
    from hsrsim.simulator.bots import GreedyBot, RandomBot
    bot = GreedyBot() if args.bot == "greedy" else RandomBot()
    
    engine = Engine(scenario, ally_bot=bot, enemy_bot=RandomBot())
    result = engine.run()
    
    print(f"Total damage: {result.total_damage:.0f}")
    print(f"Rounds played: {result.rounds_played}")
    print(f"Action sequence: {result.actions_taken}")
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result.to_json())


if __name__ == "__main__":
    main()
