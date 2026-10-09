"""LLM-based character JSON extraction for RQ1 (M5)."""
from __future__ import annotations

import json
import re
from typing import Callable

from pydantic import ValidationError

from hsrsim.graph.compiler import HSRGraphCompiler
from hsrsim.graph import validators
from hsrsim.llm.client import LLMClient, create_llm_client
from hsrsim.simulator.types import Character

VALID_TARGET_STATS = [
    "def_reduction",
    "def_ignore",
    "vuln_apply",
    "dmg_boost",
    "crit_rate",
    "crit_dmg",
    "res_pen",
    "break_effect",
    "super_break_boost",
    "super_break_boost_pct",
    "weaken",
    "weaken_pct",
    "original_mult",
    "elation",
    "punchline",
    "merrymake",
]

DAMAGE_TYPE_HINT = """
damage_type 必须按技能描述选择（不要混用）：
- direct / dot / break：常规乘区
- super_break：超击破（走 f_breakBase / f_be / f_sbBoost；描述含「超击破伤害」）
- elation：欢愉伤害（走 f_elation / f_punchline / f_merrymake）
- true：真实伤害（绕过全部乘区；典型如缇宝结界附加段）
记忆命途角色的大多数攻击仍是 direct；仅真实伤害段标 true。
"""

MECHANISM_HINT = """
机制抽取硬约束（常见翻车点）：
1. 「仅战技伤害提高 / 战技暴伤 / 战技抗穿」→ 对应 modifier 必须带 applies_to_actions: [\"skill\"]，禁止抽成无限定的全局 dmg_boost / crit_dmg。
2. 「仅终结技伤害提高」→ applies_to_actions: [\"ultimate\"]。
3. 「复制战技 / 再次施放战技 / 奇袭」→ 不得改写成全局增伤；单独 effect（如 id=skill_echo）或 synergy_tags 含 skill_echo，modifiers 可为空。
4. 「指定我方单体」→ effect_target / effect.target 用 single_ally，禁止默认 all_allies。
5. 「开拓同行 / 指定角色 / 列车组成员」→ 填写 companion_roster（角色 id 列表）与 companion_on_assist（被同行激活的自增益 effect id）；不要吞掉指定增益。
6. 若描述含「角色加强」且同时出现旧版/加强版技能，只抽取加强后版本。
7. modifiers 可选字段 applies_to_actions：空列表=全部动作；取值如 skill / ultimate / basic_attack / follow_up / memo_skill。
8. 必须填写 combat_profile（细粒度轴，避免人人标成「主通路」）：
   - 主C/输出：role=dps；damage_axes 按核心出伤排序（basic/skill/ultimate/follow_up/memo/dot/break/super_break/elation/true）；
     scaling 填 atk_scaling 或 hp_scaling（或 def_scaling）。
   - 辅助：role=support；support_axes 区分专精与泛用：
     专精例：skill（战技限定）、ultimate、elation、super_break、dot、follow_up、memo；
     泛用例：generic_amp（无限定增伤/双暴）、generic_def（减防）、generic_vuln（易伤）、generic_res（无限定抗穿）。
   - 无动作限定的 dmg_boost/crit_* → 只能进 generic_amp，禁止当成 skill/直伤专精。
"""

PROFILE_AXIS_HINT = """
combat_profile 字段说明（强烈建议填写，抽取后可被打分器直接使用）：
{
  "role": "dps|support|hybrid",
  "damage_axes": ["skill","ultimate",...],
  "scaling": ["atk_scaling"],
  "support_axes": ["skill","generic_amp",...],
  "summary_zh": "战技出伤 / 攻击力缩放"
}
"""

EXTRACTION_PROMPT = """你是一个游戏机制分析专家。请根据以下角色技能描述，提取出结构化的角色数据。

角色描述：
{character_description}

请输出一个 JSON 对象，严格遵循以下 schema：
{json_schema}

要求：
1. 每个技能（actions）必须包含：id, name, type, damage_instances, energy_cost, sp_cost, applies_effects, variable_changes, requires
2. 每个效果（effects）必须包含：id, name, is_buff, duration_turns, max_stacks, modifiers
3. modifiers 的 target_stat 必须是以下之一：{valid_target_stats}
4. variable_changes 的 key 是自定义资源名（如 nihility_stacks），value 是变化量
5. requires 里的条件格式为 "variable_name>=N" 或 "variable_name<=N"；若天赋/被动依赖「队友攻击后」触发，额外加入 "team_attack_feed"。仅**真追击/追加攻击**会 produce 该共享状态（欢愉技即使 type=follow_up 也不算；普攻/战技不算）。
6. {damage_type_hint}
7. {mechanism_hint}
8. {profile_axis_hint}

只输出 JSON，不要有任何其他文字或 markdown 标记。
"""

REFINEMENT_PROMPT = """你之前提取的角色数据有以下问题：

{validation_report}

当前 JSON：
{current_json}

请修正上述问题，输出修正后的完整 JSON。只输出 JSON，不要有其他文字。
尤其检查：战技限定是否写成了全局增伤；指定单体是否误标 all_allies；战技复制是否被改成 dmg_boost；combat_profile 的 damage_axes/support_axes/scaling 是否按核心出伤方式填写。
"""

SCHEMA_REFINEMENT_PROMPT = """JSON 不符合 schema，校验错误如下：

{validation_report}

当前 JSON：
{current_json}

请修正所有 schema 错误（例如 dmg_boost/res_pen 的 key 只能是元素名或 all；modifiers 用 target_stat 而非 stat；applies_to_actions 必须是字符串数组），输出完整 JSON。只输出 JSON。
"""

VALID_ELEMENT_KEYS = frozenset(
    {"physical", "fire", "ice", "lightning", "wind", "quantum", "imaginary", "all"}
)


class GraphExtractor:
    """Extract Character JSON from natural language, validated via graph compiler."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model: str = "deepseek-v4-pro",
        llm_fn: Callable[[str], str] | None = None,
    ):
        self.llm = llm_client
        self.model = model
        self._llm_fn = llm_fn
        self.compiler = HSRGraphCompiler()

    def extract(
        self,
        character_description: str,
        *,
        expected_chains: list[tuple[str, str]] | None = None,
        max_rounds: int = 5,
    ) -> Character:
        schema = json.dumps(Character.model_json_schema(), ensure_ascii=False, indent=2)
        prompt = EXTRACTION_PROMPT.format(
            character_description=character_description,
            json_schema=schema,
            valid_target_stats=", ".join(VALID_TARGET_STATS),
            damage_type_hint=DAMAGE_TYPE_HINT.strip(),
            mechanism_hint=MECHANISM_HINT.strip(),
            profile_axis_hint=PROFILE_AXIS_HINT.strip(),
        )
        char_json = self._call_llm(prompt)
        char = self._parse_character_with_refinement(char_json)

        for _ in range(max_rounds):
            G = self.compiler.compile(char)
            report = validators.run_all_validators(G, expected_chains=expected_chains)
            if report["overall_pass"]:
                return self._ensure_combat_profile(char)

            feedback = self._format_feedback(report)
            refine_prompt = REFINEMENT_PROMPT.format(
                validation_report=feedback,
                current_json=char.model_dump_json(indent=2),
            )
            char_json = self._call_llm(refine_prompt)
            char = self._parse_character_with_refinement(char_json)

        return self._ensure_combat_profile(char)

    @staticmethod
    def _ensure_combat_profile(char: Character) -> Character:
        """Fill combat_profile from kit inference when LLM omitted axes."""
        from hsrsim.app.profiles import infer_main_profile, infer_support_profile
        from hsrsim.app.tiers import looks_like_support_kit
        from hsrsim.simulator.types import CombatProfileData

        existing = char.combat_profile
        has_axes = bool(
            existing
            and (existing.damage_axes or existing.support_axes or existing.scaling)
        )
        if has_axes:
            return char
        if looks_like_support_kit(char):
            p = infer_support_profile(char)
            dps = infer_main_profile(char)
            has_dmg = any(a.damage_instances for a in char.build.actions)
            payload = CombatProfileData(
                role="support",
                damage_axes=dps.damage_axes if has_dmg else [],
                scaling=dps.scaling if has_dmg else [],
                support_axes=p.support_axes,
                summary_zh=p.summary_zh,
            )
        else:
            p = infer_main_profile(char)
            payload = CombatProfileData(
                role="dps",
                damage_axes=p.damage_axes,
                scaling=p.scaling,
                support_axes=[],
                summary_zh=p.summary_zh,
            )
        return char.model_copy(update={"combat_profile": payload})

    def _parse_character_with_refinement(self, char_json: str, *, max_schema_rounds: int = 2) -> Character:
        """Parse LLM JSON; on schema errors ask LLM to fix before giving up."""
        last_error: ValidationError | None = None
        current = char_json
        for attempt in range(max_schema_rounds + 1):
            try:
                return self._parse_character(current)
            except ValidationError as exc:
                last_error = exc
                if attempt >= max_schema_rounds or self._llm_fn is None and self.llm is None:
                    raise
                feedback = exc.errors(include_url=False)
                refine_prompt = SCHEMA_REFINEMENT_PROMPT.format(
                    validation_report=json.dumps(feedback, ensure_ascii=False, indent=2),
                    current_json=current,
                )
                current = self._call_llm(refine_prompt)
        assert last_error is not None
        raise last_error

    def _call_llm(self, prompt: str) -> str:
        if self._llm_fn is not None:
            return self._llm_fn(prompt)
        llm = self.llm if self.llm is not None else create_llm_client()
        # Thinking mode consumes completion budget on reasoning_content first.
        max_tokens = 32000 if getattr(llm, "thinking", False) else 8000
        return llm.complete(
            prompt,
            max_tokens=max_tokens,
            json_mode=True,
            system="You are a game mechanics analyst. Output valid JSON only.",
        )

    @staticmethod
    def _sanitize_element_stat_dict(raw: dict | None) -> dict[str, float]:
        """Keep only valid Element / 'all' keys; fold element-prefixed aliases."""
        if not isinstance(raw, dict):
            return {}
        out: dict[str, float] = {}
        for key, value in raw.items():
            if not isinstance(value, (int, float)):
                continue
            k = str(key)
            if k in VALID_ELEMENT_KEYS:
                out[k] = float(value)
                continue
            prefix = k.split("_", 1)[0]
            if prefix in VALID_ELEMENT_KEYS and prefix != "all":
                out[prefix] = out.get(prefix, 0.0) + float(value)
        return out

    @staticmethod
    def _normalize_character_payload(data: dict) -> dict:
        """Fix common LLM schema slips before pydantic validation."""
        build = data.get("build")
        if not isinstance(build, dict):
            return data
        stats = build.get("stats")
        if isinstance(stats, dict):
            for field in ("dmg_boost", "res_pen"):
                if field in stats:
                    stats[field] = GraphExtractor._sanitize_element_stat_dict(stats.get(field))
        effect_target_aliases = {
            "single_enemy": "enemy",
            "all_enemies": "enemy",
            "enemies": "enemy",
            "ally": "single_ally",
            "single_ally": "single_ally",
            "team": "all_allies",
            "all_allies": "all_allies",
        }
        for action in build.get("actions") or []:
            if not isinstance(action, dict):
                continue
            et = action.get("effect_target")
            if isinstance(et, str) and et in effect_target_aliases:
                action["effect_target"] = effect_target_aliases[et]
        for effect in build.get("effects") or []:
            if not isinstance(effect, dict):
                continue
            for mod in effect.get("modifiers") or []:
                if not isinstance(mod, dict):
                    continue
                if "target_stat" not in mod and "stat" in mod:
                    mod["target_stat"] = mod.pop("stat")
                # ensure applies_to_actions is a list of strings
                ata = mod.get("applies_to_actions")
                if ata is None:
                    mod["applies_to_actions"] = []
                elif isinstance(ata, str):
                    mod["applies_to_actions"] = [ata]
                elif not isinstance(ata, list):
                    mod["applies_to_actions"] = []
        # pass through companion metadata defaults
        data.setdefault("synergy_tags", data.get("synergy_tags") or [])
        data.setdefault("companion_roster", data.get("companion_roster") or [])
        data.setdefault("companion_on_assist", data.get("companion_on_assist") or [])
        return data

    @staticmethod
    def _parse_character(raw: str) -> Character:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        data = GraphExtractor._normalize_character_payload(data)
        return Character.model_validate(data)

    @staticmethod
    def _format_feedback(report: dict) -> str:
        lines: list[str] = []
        cov = report["coverage"]
        if not cov["passed"]:
            lines.append(
                f"- coverage: FAILED - 缺少乘区节点: {cov['missing']}\n"
                "  → 检查是否漏掉 effect modifier 或 damage_type 对应的乘区"
            )
        reach = report["trigger_reachability"]
        if not reach["passed"]:
            lines.append(
                f"- trigger_reachability: FAILED - 链路不通: {reach['failed']}\n"
                "  → setup 技能应 trigger 状态，状态 dependency 到乘区，burst 技能 buff 该乘区"
            )
        loc = report["parameter_locality"]
        if not loc["passed"]:
            lines.append(f"- parameter_locality: FAILED - {loc['violations']}")
        down = report["downstream_reachability"]
        if not down["passed"]:
            lines.append(f"- downstream_reachability: FAILED - GSD={down['gsd']:.3f}")
        return "\n".join(lines) if lines else "unknown validation failure"
