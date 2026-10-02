# Shared trigger rules and skill-point caps

L1 and L2 load counter-gain rules from the same JSON. Skill-point caps use a talent descriptor plus a small ParamList file.

---

## L1 / L2 共用残梦规则

### 中间表示

文件：`data/hsr/triggers/counter_gains.json`

每条规则字段：`id`、`resource`、`holder_id`、`increment`、`per_action_cap`、`kind`、`filter`、`l2_hook`。

| kind | 含义 | L1 | L2 |
|---|---|---|---|
| `action_inherent` | 行动自带层（R3） | `GainRule.fires={actor:{skill:1}}` | EventBus `l2_hook=acheron_r3` |
| `applies_debuff_during_skill_cast` | 施放技能期间施减益（R2） | 按全队 `applies_effects` 展开 fires | EventBus `l2_hook=acheron_r2` |

代码：`hsrsim/rules/counter_gains.py`  
- L1：`adapter._counter_resource` → `specs_to_l1_gain_rules`（**不再**从 `Action.variable_changes` 推断 R3）  
- L2：`EventBus` 启动时 `load_counter_gain_specs()`，R3/R2 增量取自同一 JSON

### 测试

- 结构性：`tests/test_shared_rules_sp_caps.py::test_d1_1_acheron_direct_l1_l2_counter_rules_match` — 指纹（来源/增量/上限）一致，fires 与编译结果一致  
- ****：真实黄泉数据下 L1 黄泉战技残梦系数 = **2**（R3+R2）

---

## L2 战技点上限

### 花火天赋 1130604（先报告、后接线）

| 项 | 值 |
|---|---|
| SkillID / Level | 1130604 / L10 |
| ParamList | `[2.0, 0.04, 2.0, 3.0]` |
| TextMapEN | *While Sparkle is on the battlefield, additionally increases the max number of Skill Points by **#3[i]**…* |
| `#3[i]` → 0-based index 2 | **2.0** |
| 队上限 | base **5** + **2** = **7** |

描述符：`data/hsr/triggers/sp_team_max.json`（仅登记会加上限的天赋 skill_id，避免误读其他角色天赋 ParamList）。

### 溢出上限 10

- `sparkle.json` `_meta.schema_gaps.ult_sp_overflow_cap=10` → loader 写入 `ultimate.sp_pool_temp_max=10`  
- 引擎：`gain_cap = action.sp_pool_temp_max or sp_team_max`；`applied = max(before, min(gain_cap, before+raw))`（溢出池不会被普通回点压回 soft max）  
- 截断写日志：`sp_gain_truncated`（含 `waste`）

### 测试

- `sp_team_max==7`（含花火）  
- 大招在 soft max 满时可到 10  
- 普攻在 soft max 满时截断并记浪费  

---

## L1 known_gaps

已写入 `adapter._base_gaps`：

> L1 为稳态流量模型，不建模战技点上限截断。脉冲式回点（如花火大招一次回 6 点）在池满时的溢出浪费无法体现，L1 会略高估可用战技点。

---
