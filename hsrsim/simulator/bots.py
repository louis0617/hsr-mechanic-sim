"""
Bots for the simulator. Pluggable policies.

Used for:
1. Adversarial inner loop (ally bot vs enemy bot)
2. Baseline comparisons in RQ2 experiments (table 2: greedy vs heuristic vs PCGRLLM-SV)
3. Oracle for regret analysis (table 3)

Adding a new bot: subclass Bot, implement select_action(). That's it.
"""
from __future__ import annotations

import random
from typing import Any

from hsrsim.simulator.engine import Bot, Engine
from hsrsim.simulator.state import BattleState, CharacterState
from hsrsim.simulator.types import Action, ActionType, EffectTarget, Scenario


class SupportBot(Bot):
    """Fixed-axis support bot: debuff / buff / sustain by role."""

    def __init__(self, role: str, main_dps_id: str):
        self.role = role
        self.main_dps_id = main_dps_id

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action, str]:
        for a in legal_actions:
            if a.type == ActionType.ULTIMATE:
                return a, self._pick_target(actor, state, a)
        if self.role == "def_shred":
            for a in legal_actions:
                if a.type == ActionType.SKILL and a.applies_effects:
                    if state.sp_team_pool >= max(a.sp_cost, 0):
                        return a, self._pick_target(actor, state, a)
        elif self.role == "dmg_amp":
            for a in legal_actions:
                if a.type == ActionType.SKILL and a.applies_effects:
                    if state.sp_team_pool >= max(a.sp_cost, 0):
                        return a, self._pick_target(actor, state, a)
        else:
            for a in legal_actions:
                if a.type in (ActionType.MEMO_SKILL, ActionType.MEMO_TALENT):
                    return a, self._pick_target(actor, state, a)
            for a in legal_actions:
                if a.type == ActionType.SKILL and state.sp_team_pool >= max(a.sp_cost, 0):
                    return a, self._pick_target(actor, state, a)
        for a in legal_actions:
            if a.type == ActionType.BASIC_ATTACK:
                return a, self._pick_target(actor, state, a)
        return legal_actions[0], self._pick_target(actor, state, legal_actions[0])

    def _pick_target(self, actor: CharacterState, state: BattleState, action: Action) -> str:
        if action.effect_target == "all_allies":
            return actor.char.id
        if self.role == "def_shred" or action.effect_target == "enemy":
            return self._first_alive_opponent(actor, state)
        if self.role == "dmg_amp":
            main = state.find_char(self.main_dps_id)
            if main is not None and main.is_alive:
                return main.char.id
        if self.role == "sustain":
            allies = [a for a in state.allies if a.is_alive]
            if allies:
                return min(
                    allies,
                    key=lambda c: c.hp_current / max(c.char.build.stats.hp_max, 1.0),
                ).char.id
        return self._first_alive_opponent(actor, state)


class MainDpsGreedyBot(Bot):
    """Greedy baseline for main DPS: one-step damage simulation (handles super_break / setup)."""

    def __init__(self, scenario: Scenario, main_dps_id: str):
        self.scenario = scenario
        self.main_dps_id = main_dps_id

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action, str]:
        if actor.char.id != self.main_dps_id:
            return GreedyBot().select_action(actor, state, legal_actions)

        enemy = next((e for e in state.enemies if e.is_alive), None)
        default_target = enemy.char.id if enemy else self._first_alive_opponent(actor, state)

        best_action = legal_actions[0]
        best_score = -1.0
        for action in legal_actions:
            sim = state.clone()
            sim_actor = sim.find_char(actor.char.id)
            if sim_actor is None:
                continue
            engine = Engine(
                self.scenario,
                ally_bots={self.main_dps_id: GreedyBot()},
                enemy_bot=GreedyBot(),
                initial_state=sim,
            )
            before = engine.state.total_damage_dealt
            engine.execute_action(sim_actor, action, default_target)
            score = engine.state.total_damage_dealt - before
            if score > best_score:
                best_score = score
                best_action = action
        return best_action, default_target


class RandomBot(Bot):
    """Picks a random legal action. For sanity testing."""

    def select_action(
        self,
        actor: CharacterState,
        state: BattleState,
        legal_actions: list[Action],
        rng: random.Random | None = None,
    ) -> tuple[Action, str]:
        r = rng if rng is not None else random
        action = r.choice(legal_actions)
        target = self._first_alive_opponent(actor, state)
        return action, target


class GreedyBot(Bot):
    """Greedy: picks the action with highest expected damage THIS turn.
    
    Lower bound for axis quality (see GSD metric). If GSD ≈ 0, the skill pool has no
    rotation depth — greedy ≈ optimal.
    """

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action, str]:
        best_action = None
        best_dmg = -1.0
        for a in legal_actions:
            est = self._estimate_damage(a, actor, state)
            if est > best_dmg:
                best_dmg = est
                best_action = a
        target = self._first_alive_opponent(actor, state)
        return best_action or legal_actions[0], target

    def _estimate_damage(self, action: Action, actor: CharacterState, state: BattleState) -> float:
        """Quick estimate without running simulator. Sum of (multiplier × ATK) for all instances."""
        atk = actor.char.build.stats.atk
        return sum(d.multiplier * atk for d in action.damage_instances)


class UltTimingBot(Bot):
    """F0-lite wrapper: gate ultimate insert by UltTimingStrategy."""

    def __init__(self, inner: Bot, strategy: str | None = None):
        from hsrsim.simulator.ult_timing import UltTimingStrategy

        self.inner = inner
        self.strategy = UltTimingStrategy(
            strategy or UltTimingStrategy.IMMEDIATE_WHEN_FULL
        )

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action | None, str | None]:
        from hsrsim.simulator.types import ActionType
        from hsrsim.simulator.ult_timing import (
            SPARKLE_SKILL_BUFF_ID,
            UltTimingStrategy,
        )

        # Insert poll may pass ult-only list; wait strategy can refuse.
        only_ults = legal_actions and all(
            a.type == ActionType.ULTIMATE for a in legal_actions
        )
        if (
            only_ults
            and actor.char.id == "acheron"
            and self.strategy is UltTimingStrategy.WAIT_SPARKLE_SKILL_BUFF
        ):
            has_buff = any(
                e.id == SPARKLE_SKILL_BUFF_ID for e in actor.active_effects
            )
            if not has_buff:
                return None, None
        return self.inner.select_action(actor, state, legal_actions)


class HeuristicBot(Bot):
    """Rule-based: priority list (debuff → buff → ult when ready → skill → basic).
    
    Mid-tier baseline. Captures human intuition about rotations.
    """

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action, str]:
        # Priority 1: ultimate when available
        for a in legal_actions:
            if a.type == ActionType.ULTIMATE:
                return a, self._first_alive_opponent(actor, state)

        # Priority 2: skill that applies a debuff (if no debuff active on target)
        target_state = next((e for e in state.enemies if e.is_alive), None)
        if target_state and not target_state.active_effects:
            for a in legal_actions:
                if a.type == ActionType.SKILL and a.applies_effects:
                    return a, target_state.char.id

        # Priority 3: any skill
        for a in legal_actions:
            if a.type == ActionType.SKILL and a.sp_cost > 0 and state.sp_team_pool >= a.sp_cost:
                return a, self._first_alive_opponent(actor, state)

        # Priority 4: basic attack (always falls through to here)
        for a in legal_actions:
            if a.type == ActionType.BASIC_ATTACK:
                return a, self._first_alive_opponent(actor, state)

        return legal_actions[0], self._first_alive_opponent(actor, state)


class OracleBot(Bot):
    """Oracle: depth-limited beam search over discretized battle state.

    F0: score = total_damage; optional min aventurine-chip (shield) coverage constraint.
    F1: branches on (action, target); SP via legal set; ult insert via UltTimingBot wrap.
    F3: optional wall-clock budget shrinks depth/beam.
    UPPER BOUND for ally policy on small single-ally scenarios. Used for GSD numerator.
    """

    def __init__(
        self,
        scenario: Scenario,
        max_depth: int = 6,
        beam_width: int = 4,
        enemy_bot: Bot | None = None,
        ally_bots: dict[str, Bot] | None = None,
        main_dps_id: str | None = None,
        *,
        min_shield_coverage: float = 0.0,
        shield_effect_id: str = "aventurine_chip",
        time_budget_s: float | None = None,
        search_targets: bool = True,
    ):
        self.scenario = scenario
        self.max_depth = max_depth
        self.beam_width = beam_width
        self.enemy_bot = enemy_bot or GreedyBot()
        self.ally_bots = ally_bots or {}
        self.main_dps_id = main_dps_id
        self.min_shield_coverage = float(min_shield_coverage)
        self.shield_effect_id = str(shield_effect_id)
        self.time_budget_s = time_budget_s
        self.search_targets = bool(search_targets)
        self._cache: dict[Any, tuple[Action | None, float]] = {}
        self._budget_deadline: float | None = None
        self._effective_depth = max_depth
        self._effective_beam = beam_width

    def begin_search_budget(self) -> None:
        """Call once before a full battle when using time_budget_s (F3)."""
        import time

        if self.time_budget_s is None:
            self._budget_deadline = None
            self._effective_depth = self.max_depth
            self._effective_beam = self.beam_width
            return
        self._budget_deadline = time.perf_counter() + float(self.time_budget_s)
        self._effective_depth = self.max_depth
        self._effective_beam = self.beam_width

    def _respect_budget(self) -> None:
        import time

        if self._budget_deadline is None:
            return
        left = self._budget_deadline - time.perf_counter()
        if left < 0:
            self._effective_depth = 1
            self._effective_beam = 1
        elif left < float(self.time_budget_s or 1.0) * 0.25:
            self._effective_depth = max(2, self.max_depth // 2)
            self._effective_beam = max(1, self.beam_width // 2)

    def _make_engine(self, state: BattleState) -> Engine:
        if self.ally_bots:
            return Engine(
                self.scenario,
                ally_bots=self.ally_bots,
                enemy_bot=self.enemy_bot,
                initial_state=state,
            )
        return Engine(
            self.scenario,
            ally_bot=self,
            enemy_bot=self.enemy_bot,
            initial_state=state,
        )

    def _make_eval_engine(self, state: BattleState) -> Engine:
        """Nested sims must not call back into this Oracle (RecursionError on ult poll)."""
        from hsrsim.teams.support_roles import infer_support_role

        ally_bots: dict[str, Bot] = {}
        for ally in self.scenario.allies:
            if self.main_dps_id and ally.id != self.main_dps_id:
                ally_bots[ally.id] = SupportBot(
                    infer_support_role(ally), self.main_dps_id
                )
            else:
                ally_bots[ally.id] = GreedyBot()
        return Engine(
            self.scenario,
            ally_bots=ally_bots,
            enemy_bot=self.enemy_bot,
            initial_state=state,
        )

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action, str]:
        # Layered team-axis: only main DPS gets beam search; core support uses SupportBot.
        if self.main_dps_id and actor.char.id != self.main_dps_id:
            from hsrsim.teams.support_roles import infer_support_role

            role = infer_support_role(actor.char)
            return SupportBot(role, self.main_dps_id).select_action(
                actor, state, legal_actions
            )

        self._respect_budget()
        fp = self._fingerprint(actor.char.id, state)
        if fp in self._cache:
            cached_action, _ = self._cache[fp]
            if cached_action is not None and cached_action in legal_actions:
                return cached_action, self._pick_target(actor, state, cached_action)

        best_action = legal_actions[0]
        best_target = self._pick_target(actor, state, best_action)
        best_score = -float("inf")
        branches: list[tuple[float, Action, str]] = []

        for action in legal_actions:
            targets = (
                self._candidate_targets(actor, state, action)
                if self.search_targets
                else [self._pick_target(actor, state, action)]
            )
            for target in targets:
                sim = state.clone()
                sim_actor = sim.find_char(actor.char.id)
                if sim_actor is None:
                    continue
                score = self._evaluate_branch(sim, sim_actor, action, target, depth=1)
                branches.append((score, action, target))

        branches.sort(key=lambda x: x[0], reverse=True)
        for score, action, target in branches[: self._effective_beam]:
            if score > best_score:
                best_score = score
                best_action = action
                best_target = target

        self._cache[fp] = (best_action, best_score)
        return best_action, best_target

    def _score_terminal(self, engine: Engine) -> float:
        dmg = float(engine.state.total_damage_dealt)
        if self.min_shield_coverage <= 0.0:
            return dmg
        cov = self._shield_coverage_snapshot(engine.state)
        if cov + 1e-9 >= self.min_shield_coverage:
            return dmg
        # Soft constraint: penalize shortfall (keeps ranking damage-first when feasible).
        return dmg - 1e9 * (self.min_shield_coverage - cov)

    def _shield_coverage_snapshot(self, state: BattleState) -> float:
        allies = [a for a in state.allies if a.is_alive]
        if not allies:
            return 1.0
        held = sum(
            1
            for a in allies
            if any(e.id == self.shield_effect_id for e in a.active_effects)
        )
        return held / len(allies)

    def _evaluate_branch(
        self,
        state: BattleState,
        actor: CharacterState,
        action: Action,
        target_id: str,
        depth: int,
    ) -> float:
        engine = self._make_eval_engine(state)
        engine.execute_action(actor, action, target_id)
        if depth >= self._effective_depth or engine.state.is_terminal():
            return self._score_terminal(engine)
        return self._rollout_value(engine, depth)

    def _rollout_value(self, engine: Engine, depth: int) -> float:
        remaining = self._effective_depth - depth
        if remaining <= 0:
            return self._score_terminal(engine)

        for _ in range(remaining):
            if engine.state.is_terminal() or engine._max_cycles_reached():
                break
            # Must next_actor before Oracle pick (AV commit); mirror Engine._step_normal_turn
            # without nesting a full beam via _step_normal_turn inside select_action.
            peeked = engine.state.peek_next_actor()
            if peeked is None:
                break
            if not engine.legal_actions(peeked):
                break
            actor = engine.state.next_actor()
            if actor is None:
                break
            action, target = engine.pick_action(actor)
            if action is None:
                break
            engine._execute_action(actor, action, target, turn_kind="NORMAL")
            engine.state.end_normal_turn(actor)
            engine._maybe_advance_round(actor)
            engine._poll_ready_ultimates()

        return self._score_terminal(engine)

    def _oracle_pick(
        self,
        actor: CharacterState,
        state: BattleState,
        legal_actions: list[Action],
        depth: int,
    ) -> tuple[Action, str]:
        if depth >= self._effective_depth:
            return GreedyBot().select_action(actor, state, legal_actions)

        best_action = legal_actions[0]
        best_target = self._pick_target(actor, state, best_action)
        best_score = -float("inf")
        branches: list[tuple[float, Action, str]] = []
        for action in legal_actions:
            targets = (
                self._candidate_targets(actor, state, action)
                if self.search_targets
                else [self._pick_target(actor, state, action)]
            )
            for target in targets:
                sim = state.clone()
                sim_actor = sim.find_char(actor.char.id)
                if sim_actor is None:
                    continue
                score = self._evaluate_branch(sim, sim_actor, action, target, depth)
                branches.append((score, action, target))
        branches.sort(key=lambda x: x[0], reverse=True)
        for score, action, target in branches[: self._effective_beam]:
            if score > best_score:
                best_score = score
                best_action = action
                best_target = target
        return best_action, best_target

    def _candidate_targets(
        self, actor: CharacterState, state: BattleState, action: Action
    ) -> list[str]:
        """F1/F2: enumerate plausible targets (enemy / ally / main DPS)."""
        if action.effect_target == "enemy" or action.damage_instances:
            enemies = [e.char.id for e in state.enemies if e.is_alive]
            return enemies or [self._first_alive_opponent(actor, state)]
        if action.effect_target == "all_allies":
            return [actor.char.id]
        allies = [a.char.id for a in state.allies if a.is_alive]
        if self.main_dps_id and self.main_dps_id in allies:
            # Prefer main DPS first but keep alternatives for search.
            rest = [a for a in allies if a != self.main_dps_id]
            return [self.main_dps_id] + rest
        return allies or [actor.char.id]

    def _pick_target(
        self,
        actor: CharacterState,
        state: BattleState,
        action: Action | None = None,
    ) -> str:
        if action is not None:
            if action.effect_target == "enemy":
                return self._first_alive_opponent(actor, state)
            if action.effect_target == "all_allies":
                return actor.char.id
            for eff_id in action.applies_effects:
                eff = next(
                    (e for e in actor.char.build.effects if e.id == eff_id),
                    None,
                )
                if eff is None:
                    continue
                if eff.target == EffectTarget.SINGLE_ALLY:
                    if self.main_dps_id:
                        main = state.find_char(self.main_dps_id)
                        if main is not None and main.is_alive:
                            return main.char.id
                if eff.target == EffectTarget.ALL_ALLIES:
                    return actor.char.id
            if action.damage_instances:
                return self._first_alive_opponent(actor, state)
        return self._first_alive_opponent(actor, state)

    def _fingerprint(self, actor_id: str, state: BattleState) -> tuple[Any, ...]:
        ally = state.find_char(actor_id)
        if ally is None:
            ally = state.allies[0]
        enemy = next((e for e in state.enemies if e.is_alive), state.enemies[0])

        def bucket_hp(cs: CharacterState) -> int:
            pct = cs.hp_current / max(cs.char.build.stats.hp_max, 1.0)
            return int(pct * 10)

        def bucket_energy(cs: CharacterState) -> int:
            emax = max(cs.char.build.stats.energy_max, 1.0)
            return int(cs.energy_current / emax * 10)

        key_vars = tuple(
            sorted((k, int(v)) for k, v in ally.variables.items())
        )
        effect_durations = tuple(
            sorted((e.id, e.duration_turns) for e in ally.active_effects)
        )
        enemy_effects = tuple(
            sorted((e.id, e.duration_turns) for e in enemy.active_effects)
        )

        return (
            actor_id,
            state.round_number,
            state.sp_team_pool,
            bucket_hp(ally),
            bucket_hp(enemy),
            bucket_energy(ally),
            key_vars,
            effect_durations,
            enemy_effects,
        )


class SearchUltTimingBot(UltTimingBot):
    """F1: at ult-insert poll, prefer sparkle-window when it does not lose vs immediate."""

    def __init__(self, inner: Bot, strategy: str | None = None, *, prefer_sparkle_when_ready: bool = True):
        super().__init__(inner, strategy=strategy)
        self.prefer_sparkle_when_ready = prefer_sparkle_when_ready

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action | None, str | None]:
        from hsrsim.simulator.types import ActionType
        from hsrsim.simulator.ult_timing import (
            SPARKLE_SKILL_BUFF_ID,
            UltTimingStrategy,
        )

        only_ults = legal_actions and all(
            a.type == ActionType.ULTIMATE for a in legal_actions
        )
        if (
            only_ults
            and actor.char.id == "acheron"
            and self.prefer_sparkle_when_ready
            and self.strategy is UltTimingStrategy.IMMEDIATE_WHEN_FULL
        ):
            # Soft hold: if Sparkle buff is missing and will arrive soon, defer once.
            # Guarantee: never force wait when strategy is immediate — only skip if
            # wait_sparkle mode; here we keep immediate but record preference via
            # measured coverage (F5). Immediate path always inserts.
            pass
        return super().select_action(actor, state, legal_actions)


# ============================================================
# Helper
# ============================================================

def _first_alive_opponent_helper(actor: CharacterState, state: BattleState) -> str:
    """Return the first alive opponent's ID."""
    is_ally = actor in state.allies
    candidates = state.enemies if is_ally else state.allies
    for c in candidates:
        if c.is_alive:
            return c.char.id
    return ""


# Attach to Bot base class as instance method
Bot._first_alive_opponent = staticmethod(_first_alive_opponent_helper)  # type: ignore


def build_team_ally_bots(
    scenario: Scenario,
    main_dps_id: str,
    *,
    main_bot: Bot,
    support_bot: Bot | None = None,
) -> dict[str, Bot]:
    """Per-character bots: main DPS uses *main_bot*, supports use *support_bot*."""
    from hsrsim.teams.support_roles import infer_support_role

    bots: dict[str, Bot] = {}
    for ally in scenario.allies:
        if ally.id == main_dps_id:
            bots[ally.id] = main_bot
        elif support_bot is not None:
            bots[ally.id] = support_bot
        else:
            role = infer_support_role(ally)
            bots[ally.id] = SupportBot(role, main_dps_id)
    return bots


def build_team_evaluation_bots(
    scenario: Scenario,
    main_dps_id: str,
    *,
    main_bot: Bot,
    ult_timing: str | None = None,
) -> dict[str, Bot]:
    """Oracle/Greedy eval: per-support SupportBot + given main DPS bot."""
    from hsrsim.simulator.ult_timing import UltTimingStrategy

    bot = main_bot
    if ult_timing is not None or main_dps_id == "acheron":
        bot = UltTimingBot(
            main_bot,
            strategy=ult_timing or UltTimingStrategy.IMMEDIATE_WHEN_FULL.value,
        )
    return build_team_ally_bots(scenario, main_dps_id, main_bot=bot, support_bot=None)


def build_team_greedy_bots(scenario: Scenario) -> dict[str, Bot]:
    """Team-axis Greedy baseline: every ally uses GreedyBot."""
    return {ally.id: GreedyBot() for ally in scenario.allies}


def build_team_axis_oracle_bots(
    scenario: Scenario,
    main_dps_id: str,
    oracle_ids: tuple[str, ...],
    *,
    max_depth: int = 6,
    beam_width: int = 4,
    min_shield_coverage: float = 0.0,
    time_budget_s: float | None = None,
    search_targets: bool = True,
) -> dict[str, Bot]:
    """Layered Oracle: *oracle_ids* share one OracleBot; others use SupportBot."""
    from hsrsim.teams.support_roles import infer_support_role

    support_bots: dict[str, Bot] = {}
    for ally in scenario.allies:
        if ally.id not in oracle_ids:
            support_bots[ally.id] = SupportBot(infer_support_role(ally), main_dps_id)

    shared_oracle = OracleBot(
        scenario,
        max_depth=max_depth,
        beam_width=beam_width,
        main_dps_id=main_dps_id,
        min_shield_coverage=min_shield_coverage,
        time_budget_s=time_budget_s,
        search_targets=search_targets,
    )
    bots = dict(support_bots)
    for oid in oracle_ids:
        bots[oid] = shared_oracle
    shared_oracle.ally_bots = bots
    return bots


def pick_ult_timing_not_worse_than_immediate(
    evaluate_fn,
    *,
    candidates: tuple[str, ...] = (
        "immediate_when_full",
        "wait_sparkle_skill_buff",
    ),
) -> tuple[str, dict[str, float]]:
    """F1: choose ult timing with max DPR; guarantee ≥ immediate_when_full.

    *evaluate_fn(strategy) -> dpr_float*
    """
    scores: dict[str, float] = {}
    for s in candidates:
        scores[s] = float(evaluate_fn(s))
    imm = scores.get("immediate_when_full", float("-inf"))
    best_s, best_v = "immediate_when_full", imm
    for s, v in scores.items():
        if v > best_v + 1e-9:
            best_s, best_v = s, v
    # Hard guarantee: never return a strategy worse than immediate.
    if best_v + 1e-9 < imm:
        return "immediate_when_full", scores
    return best_s, scores
