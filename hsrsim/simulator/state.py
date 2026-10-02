"""
Battle state management.

Design choice: state is mutated in-place during simulation for performance,
but a `snapshot()` method produces an immutable dict that goes into the event stream.
The event stream is the sole interface for RQ2 reward synthesis — never read live state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from hsrsim.simulator.av import apply_effect_mutation, av_for_turn
from hsrsim.simulator.types import Character, Effect, Variable

TurnKind = Literal["NORMAL", "INSERTED"]
TurnPhase = Literal["turn_start", "action", "turn_end"]
SkipTickCondition = Literal["timing", "self_source"]


@dataclass
class TurnContext:
    """Active normal/inserted turn context for buff apply and duration ticks."""

    owner_id: str
    kind: TurnKind = "NORMAL"
    phase: TurnPhase = "action"


@dataclass
class CharacterState:
    """Mutable state for a single character during battle."""
    char: Character
    hp_current: float
    energy_current: float
    sp_team_share: int  # team-shared, but stored per char for convenience
    toughness_current: float
    av_remaining: float  # action value: lower = acts sooner

    # Live effects (may be applied/removed each turn)
    active_effects: list[Effect] = field(default_factory=list)

    # Live variable values
    variables: dict[str, float] = field(default_factory=dict)

    # Back-reference for apply-time skip_next_end_tick (set by BattleState).
    battle: BattleState | None = field(default=None, repr=False, compare=False)

    @property
    def is_alive(self) -> bool:
        return self.hp_current > 0

    @property
    def toughness_broken(self) -> bool:
        return self.toughness_current <= 0

    def apply_effect(self, effect: Effect, *, source_id: str | None = None) -> dict[str, Any]:
        """Add or refresh an effect. Stacks if max_stacks > 1.

        When applied during the holder's own NORMAL action phase, may set
        ``skip_next_end_tick`` per ``BattleState.skip_tick_condition``.
        Speed changes rescale ``av_remaining`` via ``apply_effect_mutation``.

        Returns ``{was_refresh, stacks_before, stacks_after}``.
        """
        existing = next((e for e in self.active_effects if e.id == effect.id), None)
        was_refresh = existing is not None
        stacks_before = int(existing.current_stacks) if existing else 0

        def _mutate() -> None:
            inst = effect.model_copy(deep=True)
            if source_id is not None:
                inst.source_id = source_id
            if self._should_skip_next_end_tick(source_id=source_id):
                inst.skip_next_end_tick = True
            cur = next((e for e in self.active_effects if e.id == inst.id), None)
            if cur is None:
                self.active_effects.append(inst)
            else:
                if cur.current_stacks < cur.max_stacks:
                    cur.current_stacks += 1
                cur.duration_turns = max(cur.duration_turns, inst.duration_turns)
                cur.tick_timing = inst.tick_timing
                if source_id is not None:
                    cur.source_id = source_id
                if inst.skip_next_end_tick:
                    cur.skip_next_end_tick = True

        apply_effect_mutation(self, _mutate)
        after = next((e for e in self.active_effects if e.id == effect.id), None)
        stacks_after = int(after.current_stacks) if after else stacks_before
        return {
            "was_refresh": was_refresh,
            "stacks_before": stacks_before,
            "stacks_after": stacks_after,
        }

    def set_effect_stacks(
        self,
        effect: Effect,
        stacks: int,
        *,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Set stacks to an absolute value (equalize / sync). Caps at max_stacks."""
        existing = next((e for e in self.active_effects if e.id == effect.id), None)
        was_refresh = existing is not None
        stacks_before = int(existing.current_stacks) if existing else 0
        target = max(0, min(int(stacks), int(effect.max_stacks)))

        def _mutate() -> None:
            if target <= 0:
                self.active_effects = [
                    e for e in self.active_effects if e.id != effect.id
                ]
                return
            inst = effect.model_copy(deep=True)
            inst.current_stacks = target
            if source_id is not None:
                inst.source_id = source_id
            cur = next((e for e in self.active_effects if e.id == inst.id), None)
            if cur is None:
                self.active_effects.append(inst)
            else:
                cur.current_stacks = target
                cur.duration_turns = max(cur.duration_turns, inst.duration_turns)
                cur.tick_timing = inst.tick_timing
                if source_id is not None:
                    cur.source_id = source_id
                # Keep vuln_at_one / per_extra / dot from template.
                cur.vuln_at_one_stack = inst.vuln_at_one_stack
                cur.vuln_per_extra_stack = inst.vuln_per_extra_stack
                if inst.dot_instance is not None:
                    cur.dot_instance = inst.dot_instance
                    cur.dot_source_id = inst.dot_source_id

        apply_effect_mutation(self, _mutate)
        after = next((e for e in self.active_effects if e.id == effect.id), None)
        stacks_after = int(after.current_stacks) if after else 0
        return {
            "was_refresh": was_refresh,
            "stacks_before": stacks_before,
            "stacks_after": stacks_after,
        }

    def remove_effect(self, effect_id: str) -> bool:
        """Remove one effect by id. Rescales AV if effective_speed changes."""
        before = len(self.active_effects)

        def _mutate() -> None:
            self.active_effects = [e for e in self.active_effects if e.id != effect_id]

        apply_effect_mutation(self, _mutate)
        return len(self.active_effects) < before

    def _should_skip_next_end_tick(self, *, source_id: str | None) -> bool:
        battle = self.battle
        if battle is None or battle.current_turn is None:
            return False
        ct = battle.current_turn
        if ct.kind != "NORMAL" or ct.phase != "action":
            return False
        if ct.owner_id != self.char.id:
            return False
        if battle.skip_tick_condition == "timing":
            return True
        if battle.skip_tick_condition == "self_source":
            return source_id == self.char.id
        return False

    def tick_effects(self, timing: Literal["turn_start", "turn_end"]) -> None:
        """Decrement effects whose ``tick_timing`` matches; remove expired.

        Speed changes from expiry rescale ``av_remaining`` via
        ``apply_effect_mutation``.
        """
        def _mutate() -> None:
            for e in self.active_effects:
                if e.tick_timing != timing:
                    continue
                if timing == "turn_end" and e.skip_next_end_tick:
                    e.skip_next_end_tick = False
                    continue
                if e.duration_turns > 0:
                    e.duration_turns -= 1
            self.active_effects = [
                e for e in self.active_effects if e.duration_turns != 0
            ]

        apply_effect_mutation(self, _mutate)

    def tick_durations(self) -> None:
        """Deprecated global tick. Prefer ``tick_effects`` on the holder's turn."""
        self.tick_effects("turn_end")

    def snapshot(self) -> dict[str, Any]:
        """Immutable view for event stream."""
        return {
            "char_id": self.char.id,
            "hp_current": self.hp_current,
            "hp_max": self.char.build.stats.hp_max,
            "energy_current": self.energy_current,
            "sp_team": self.sp_team_share,
            "toughness_current": self.toughness_current,
            "toughness_broken": self.toughness_broken,
            "av_remaining": self.av_remaining,
            "active_effects": [
                {
                    "id": e.id,
                    "stacks": e.current_stacks,
                    "duration": e.duration_turns,
                    "tick_timing": e.tick_timing,
                    "skip_next_end_tick": e.skip_next_end_tick,
                }
                for e in self.active_effects
            ],
            "variables": dict(self.variables),
            "immortal": bool(self.char.immortal),
        }


ToughnessMode = Literal["realistic", "always_unbroken"]


@dataclass
class BattleState:
    """Top-level mutable state for the entire battle."""
    allies: list[CharacterState]
    enemies: list[CharacterState]

    sp_team_pool: int = 3  # default starting SP, max 5 (or 7 with Sparkle)
    sp_team_max: int = 5

    round_number: int = 0
    av_clock: float = 0.0  # global action-value clock

    total_damage_dealt: float = 0.0

    current_turn: TurnContext | None = None
    skip_tick_condition: SkipTickCondition = "timing"
    #mirrors Scenario.toughness_mode (engine copies on construct).
    toughness_mode: ToughnessMode = "realistic"
    # E1.2: active battlefield zones (generic).
    active_zones: list = field(default_factory=list)
    #sync trigger bus hook (event_name, payload).
    event_emit: Callable[[str, dict[str, Any]], None] | None = field(
        default=None, repr=False, compare=False
    )
    #settle turn_start DoTs (damage) before duration decrement.
    settle_turn_start_dots: Callable[[CharacterState], None] | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def from_characters(cls, allies: list[Character], enemies: list[Character]) -> "BattleState":
        """Initialize battle state from character definitions."""
        from hsrsim.rules.sp_caps import team_sp_max_from_allies

        def init_char(char: Character) -> CharacterState:
            cs = CharacterState(
                char=char,
                hp_current=char.build.stats.hp_max,
                energy_current=char.build.stats.energy_max * 0.5,  # start at 50% energy
                sp_team_share=3,
                toughness_current=char.toughness_max,
                av_remaining=0.0,
                variables={v.id: v.value for v in char.build.variables},
            )
            cs.av_remaining = av_for_turn(cs)
            return cs
        state = cls(
            allies=[init_char(c) for c in allies],
            enemies=[init_char(c) for c in enemies],
            sp_team_pool=3,
            sp_team_max=team_sp_max_from_allies(allies),
        )
        state._bind_characters()
        return state

    def _bind_characters(self) -> None:
        for cs in self.allies + self.enemies:
            cs.battle = self

    def find_char(self, char_id: str) -> CharacterState | None:
        for c in self.allies + self.enemies:
            if c.char.id == char_id:
                return c
        return None

    def peek_next_actor(self) -> CharacterState | None:
        """Who would act next, without advancing AV or opening a turn (C7)."""
        all_alive = [c for c in self.allies + self.enemies if c.is_alive]
        if not all_alive:
            return None
        return min(all_alive, key=lambda c: c.av_remaining)

    def next_actor(self) -> CharacterState | None:
        """Return the character with the lowest AV, advance clock, begin NORMAL turn."""
        if self.current_turn is not None:
            # Previous turn was not closed (e.g. skipped action). Clear context.
            self.current_turn = None
        actor = self.peek_next_actor()
        if actor is None:
            return None
        all_alive = [c for c in self.allies + self.enemies if c.is_alive]
        elapsed = actor.av_remaining
        self.av_clock += elapsed
        for c in all_alive:
            c.av_remaining -= elapsed
        actor.av_remaining = av_for_turn(actor)
        self.begin_normal_turn(actor)
        return actor

    def begin_normal_turn(self, actor: CharacterState) -> None:
        """Turn-start phase then leave context in action phase.

        Order (): toughness recover () → emit turn_start → DoT →
        duration −1 → action. Inserted actions never call this ().
        """
        self.current_turn = TurnContext(
            owner_id=actor.char.id, kind="NORMAL", phase="turn_start"
        )
        self._maybe_recover_toughness(actor)
        if self.event_emit is not None:
            self.event_emit(
                "turn_start",
                {
                    "unit_id": actor.char.id,
                    "turn_kind": "NORMAL",
                },
            )
        if self.settle_turn_start_dots is not None:
            self.settle_turn_start_dots(actor)
        actor.tick_effects("turn_start")
        self.current_turn.phase = "action"

    def _maybe_recover_toughness(self, actor: CharacterState) -> None:
        """On enemy NORMAL turn start: restore full toughness if broken (realistic)."""
        if self.toughness_mode != "realistic":
            return
        if actor not in self.enemies:
            return
        if not actor.toughness_broken:
            return
        actor.toughness_current = float(actor.char.toughness_max)
        if self.event_emit is not None:
            self.event_emit(
                "toughness_recovered",
                {
                    "unit_id": actor.char.id,
                    "toughness_current": actor.toughness_current,
                    "toughness_max": float(actor.char.toughness_max),
                },
            )

    def end_normal_turn(self, actor: CharacterState) -> None:
        """Turn-end phase for the current NORMAL turn owner."""
        if self.current_turn is None:
            self.current_turn = TurnContext(
                owner_id=actor.char.id, kind="NORMAL", phase="turn_end"
            )
        else:
            self.current_turn.phase = "turn_end"
        if self.current_turn.owner_id == actor.char.id and self.current_turn.kind == "NORMAL":
            if self.event_emit is not None:
                self.event_emit(
                    "turn_end",
                    {
                        "unit_id": actor.char.id,
                        "turn_kind": "NORMAL",
                    },
                )
            actor.tick_effects("turn_end")
        self.current_turn = None

    def is_terminal(self) -> bool:
        """All allies dead or all enemies dead."""
        return (not any(c.is_alive for c in self.allies) or
                not any(c.is_alive for c in self.enemies))

    def snapshot(self) -> dict[str, Any]:
        return {
            "round": self.round_number,
            "av_clock": self.av_clock,
            "sp_pool": self.sp_team_pool,
            "total_damage": self.total_damage_dealt,
            "current_turn": None
            if self.current_turn is None
            else {
                "owner": self.current_turn.owner_id,
                "kind": self.current_turn.kind,
                "phase": self.current_turn.phase,
            },
            "allies": [c.snapshot() for c in self.allies],
            "enemies": [c.snapshot() for c in self.enemies],
        }

    def clone(self) -> "BattleState":
        """Deep-ish copy for lookahead search. Character definitions are shared."""
        cloned = BattleState(
            allies=[self._clone_char_state(c) for c in self.allies],
            enemies=[self._clone_char_state(c) for c in self.enemies],
            sp_team_pool=self.sp_team_pool,
            sp_team_max=self.sp_team_max,
            round_number=self.round_number,
            av_clock=self.av_clock,
            total_damage_dealt=self.total_damage_dealt,
            toughness_mode=self.toughness_mode,
            current_turn=None
            if self.current_turn is None
            else TurnContext(
                owner_id=self.current_turn.owner_id,
                kind=self.current_turn.kind,
                phase=self.current_turn.phase,
            ),
            skip_tick_condition=self.skip_tick_condition,
        )
        cloned._bind_characters()
        return cloned

    @staticmethod
    def _clone_char_state(cs: CharacterState) -> CharacterState:
        return CharacterState(
            char=cs.char,
            hp_current=cs.hp_current,
            energy_current=cs.energy_current,
            sp_team_share=cs.sp_team_share,
            toughness_current=cs.toughness_current,
            av_remaining=cs.av_remaining,
            active_effects=[e.model_copy(deep=True) for e in cs.active_effects],
            variables=dict(cs.variables),
        )
