# Event trigger layer

Triggers express eidolons, traces, talent reactions, light-cone and relic
conditionals, and reactive resource gains. The engine provides an event bus,
battlefield queries, and a single entry point for resource and effect changes
(`apply_effect_mutation`). Descriptors live in `data/hsr/triggers/`.

---

## 0. 目标与非目标

### 0.1 目标

用**数据驱动触发器**表达星魂、行迹、天赋反应、光锥/遗器条件效果与资源反应获取，引擎只提供：

1. 事件广播总线  
2. 通用队伍/战场查询  
3. 资源变更与效果施加的统一入口（可复用 的 `apply_effect_mutation`）

**禁止** `if char.id == "acheron"` 类引擎特例。

### 0.2 非目标（本次不设计实现细节）

| 项 | 归属 |
|---|---|
| 追加攻击独立行动队列 | （事件枚举已预留） |
| DoT 伤害数值结算 | （事件枚举已预留） |
| 三个 UNKNOWN 的**最终**取值 | 暂缓清单；本文只固定工作默认与影响面 |
| A3 全文减益核对 | A3 仍为 ⬜；本文用 `is_buff=False` 作操作定义，留合并口 |

---

## 1. 事件类型枚举

### 1.1 必选（ / ）

| 事件 `on` | 载荷（最少） | 语义 |
|---|---|---|
| `turn_start` | `unit_id`, `turn_kind` | 某单位回合开始阶段进入时 |
| `turn_end` | `unit_id`, `turn_kind` | 某单位回合结束阶段进入时 |
| `action_used` | `actor_id`, `action_id`, `action_kind`, `target_id?`, `turn_kind` | 行动已选定并开始执行（扣 SP/宣告之后、结算之前） |
| `action_resolved` | 同上 + `damage_total?` | 该行动的伤害与效果施加全部完成之后 |
| `hit_taken` | `defender_id`, `attacker_id`, `action_id`, `damage`, `damage_type` | 单段伤害结算并扣 HP 之后 |
| `enemy_killed` | `victim_id`, `killer_id?`, `action_id?`, `transferred_effects?` | 单位 HP≤0 且由本次结算导致死亡 |
| `effect_applied` | `effect_id`, `is_debuff`, `source_id`, `target_id`, `was_refresh`, `stacks_after` | 效果成功挂上/刷新之后（未命中不发） |
| `ultimate_used` | `actor_id`, `action_id`, `turn_kind` | `action_kind==ultimate` 时与 `action_used` 同步再发一条（便于过滤） |

`action_kind` ∈ `basic_attack | skill | ultimate | follow_up | talent | memo_skill | other`（与 `ActionType` 对齐；未知标 `other`）。

`turn_kind` ∈ `NORMAL | INSERTED`（ 插入通道启用前，实战路径上恒为 `NORMAL`）。

`is_debuff` := `not effect.is_buff`（与现有 schema 一致）。A3 完成后若有「假减益」清单，在 filter 层加 `game_debuff: true` 覆盖，不改事件载荷布尔含义。

### 1.2 预留（枚举占位， 可不实现处理器）

| 事件 | 用途 |
|---|---|
| `followup_queued` / `inserted_action_start` / `inserted_action_end` | 追加/额外回合队列 |
| `dot_tick` | 回合开始 DoT 跳伤 |
| `battle_start` / `wave_start` | 开战/波次门控（Izumo 类也可在编排层处理） |
| `stats_finalized` | Broken Keel 等面板阈值（D9.1） |
| `outgoing_damage` | 伤害乘区查询钩（深渊/砂金 E6）；也可实现为 `compute_damage` 内同步查询而非异步事件 |

`outgoing_damage` 建议为**同步查询钩**（算伤时调用），不必进异步总线；描述符里的 `hooks: [outgoing_damage]` 保留。

---

## 2. 触发器 Schema（数据文件）

### 2.1 存放位置

| 来源 | 建议路径 |
|---|---|
| 角色天赋 / 固定机制 | `data/hsr/characters/<id>.json` → `_meta.triggers[]` 或旁路 `data/hsr/triggers/characters/<id>.json` |
| 行迹条件 | 已有 `data/hsr/triggers/conditional_traces.json` → `trace_triggers` |
| 星魂 | 同上 → `rank_triggers`；或角色 `_meta.rank_triggers` |
| 光锥 / 遗器 (b)(c) | `data/hsr/triggers/light_cones/<id>.json`、`relics/<set_id>.json` |

运行时合并：场景加载 → 按 `eidolons` / 行迹解锁 / 配装 ID 过滤激活子集。

### 2.2 资源变更核心形（用户要求字段）

```yaml
# 最小资源触发器
- id: example_gain
  on: effect_applied          # 事件名
  filter:                     # 匹配载荷；全部 AND；缺省 = 匹配该事件一切
    is_debuff: true
  target_unit: owner          # 资源落点，见 §2.4
  change:
    resource: nihility_stacks # 变量 id、energy、sp_team、或 effect_stack:<effect_id>
    delta: 1
  per_action_cap: 1           # 同一 action_id 实例内本触发器最多成功次数；null=不限制
```

### 2.3 完整触发器（扩展字段）

```yaml
Trigger:
  id: string
  on: EventName | list[EventName]
  filter: object                 # 见 §2.5
  target_unit: TargetSelector    # 见 §2.4
  change?: { resource, delta, cap?: number }   # 资源类
  apply_effect?: { effect_id, stacks?: int }   # 施加/叠层
  transfer_stacks?: {            # 死亡转移等
    effect_id: string
    from: "event.victim"
    to: TargetSelector
    mode: "move_all" | "move_n"
  }
  modify_cap?: { effect_id | resource, value }
  damage_mod?: {                 # outgoing_damage 钩
    kind: "multiplier_tiers" | "bonus_per_count"
    ...
  }
  per_action_cap?: number | null
  eidolon_min?: int              # 星魂门控
  owner?: string                 # 触发器归属单位（默认可从文件推断）
  notes?: string
```

`change` 与 `apply_effect` / `transfer_stacks` / `damage_mod` 可并存于同一条（按声明顺序执行）； 实现时若同条混合导致难测，可拆成多条共享 `on`+`filter`。

### 2.4 `target_unit` 选择器

| 值 | 含义 |
|---|---|
| `owner` | 触发器所属角色 |
| `event.actor` / `event.source` / `event.target` / `event.victim` / `event.defender` | 取自事件载荷 |
| `query:<QueryName>` | 见 §3，返回一个单位；并列时用配置破并列 |

### 2.5 `filter` 常用键

| 键 | 例 | 说明 |
|---|---|---|
| `action_kind` | `skill` 或 `[skill, ultimate]` | |
| `action_kind_in_skill_cast` | `true` | 受 `FOLLOWUP_COUNTS_AS_ACTION` 约束的「施放技能」判定，见 §7 |
| `is_debuff` | `true` | `effect_applied` |
| `effect_id` / `effect_id_in` | | |
| `was_refresh` | `false` | 与 `DEBUFF_REFRESH_COUNTS` 联动，见 §7 |
| `turn_kind` | `NORMAL` | 插入行动是否触发由触发器显式声明 |
| `owner_is_event_unit` | `true` | 如 E2：仅自身 `turn_start` |
| `source_side` / `target_side` | `ally`/`enemy` | |

---

## 3. 队伍构成 / 战场状态查询接口

**原则**：条件行迹、星魂、光锥套装一律走查询，不写角色特例。

### 3.1 API（设计签名）

```text
count_units(side, filter, *, exclude=None) -> int
select_unit(side, filter, order_by, *, tie_break, exclude=None) -> unit | None
has_unit(side, filter) -> bool
```

| 参数 | 说明 |
|---|---|
| `side` | `allies` \| `enemies` \| `all` |
| `filter` | 见下表；AND |
| `exclude` | 单位 id 或 `self` |
| `order_by` | `effect_stacks:<id> desc\|asc`、`av_remaining asc`、`unit_id asc`… |
| `tie_break` | 默认读全局 `KNOT_TIE_BREAK`；可覆盖 |

### 3.2 `filter` 谓词（可组合）

| 谓词 | 例 | 用途 |
|---|---|---|
| `avatar_base_type` | `Warlock` | 深渊：虚无（机器字段，非显示 `path`） |
| `path` | `nihility` | 仅显示/调试；条件逻辑优先 `avatar_base_type` |
| `has_effect_id` | `aventurine_shield` | |
| `has_shield` | `true` | 砂金 E6；盾为实现层标记或 `effect.tags ∋ shield` |
| `effect_stacks` | `{id: ash_roast, min: 1}` | |
| `eidolon_min` | `2` | 少用；星魂门控通常在触发器顶层 |
| `is_alive` | `true` | 默认 true |

### 3.3 与既有描述符对齐

`conditional_traces.json` 中：

```yaml
condition:
  query: count_allies          # ≡ count_units(allies, filter, exclude=self)
  filter: { avatar_base_type: Warlock }
  exclude_self: true
```

 将 `count_allies` 实现为 `count_units` 的语法糖，**不**再增加按角色硬编码的查询。

---

## 4. 广播点（当前代码锚点）

 在下列位置调用 `EventBus.emit(...)`（名称可改）。**插入行动（）与常规回合广播同一组事件名**，载荷带 `turn_kind`；触发器若只想要常规回合须 `filter.turn_kind: NORMAL`。

| 事件 | 函数 | 广播时机（相对现有语句） | 现锚点 |
|---|---|---|---|
| `turn_start` | `BattleState.begin_normal_turn` | 设置 `phase=turn_start` **之后**、`tick_effects("turn_start")` **之前**（保证 E2 残梦先于 turn_start 持续回合） | `state.py` ~223–228 |
| `turn_end` | `BattleState.end_normal_turn` | 设置 `phase=turn_end` **之后**、`tick_effects("turn_end")` **之前** | `state.py` ~231–240 |
| `action_used` | `Engine._execute_action` | SP/能量/变量 **消耗**写完之后、`_apply_action_effects` **之前** | `engine.py` ~459–495 |
| `ultimate_used` | 同上 | 若 `action.type==ultimate`，紧接 `action_used` | 同上 |
| `effect_applied` | `Engine._apply_action_effects` | 每次 `recipient.apply_effect(...)` 成功返回后（命中失败/`effect_resisted` 不发） | `engine.py` ~426 之后 |
| `hit_taken` | `Engine._resolve_damage` | 扣 HP、记 `damage` 日志之后；若致死则仍先发 `hit_taken` 再发 `enemy_killed` | `engine.py` ~531–551 |
| `enemy_killed` | `Engine._resolve_damage`（或统一 `_notify_death`） | `defender.hp_current==0` 且本段伤害导致死亡时，每个死者一次 | 紧随 `hit_taken` |
| `action_resolved` | `Engine._execute_action` | 全部 `damage_instances` 与效果处理完之后、现有 `action_executed` 日志处 | `engine.py` ~501 前后 |

### 4.1 插入行动（ 预留约定）

- 插入通道**不**走 `next_actor` 的 AV 推进，但须 `emit` 相同事件名。  
- **默认**：插入行动**不**触发持有者的 `turn_start`/`turn_end` 持续回合扣减（与 T17 期望一致）；是否发 `turn_start`/`turn_end` 事件：**默认不发**，除非插入类型显式声明 `emits_turn_boundaries: true`。  
- `action_used` / `action_resolved` / `effect_applied` / `hit_taken` / `enemy_killed`：**发**，`turn_kind=INSERTED`。

### 4.2 与现有 `_log_event` 关系

`_log_event` 继续服务指标/循环轴；触发总线是**同步**副作用通道。 可让 `emit` 同时写一条 log，但触发器匹配不得依赖事后读 log。

---

## 5. 递归保护

触发器执行中再次 `emit` 形成嵌套。

| 项 | 设计 |
|---|---|
| 计数器 | `EventBus.depth`，每次 `emit` 入口 +1，出口 −1 |
| 上限 | `MAX_TRIGGER_DEPTH = 8`（配置项，默认 8） |
| 超限 | **跳过**后续触发器，写 `trigger_depth_exceeded` 日志；不抛崩（避免搜索评测中断） |
| 同事件重入 | 允许（深度计入）；`per_action_cap` 仍按 action 实例计数 |
| 禁止 | 触发器内直接递归调用 `_execute_action` 开新行动而不经 队列（ 前直接禁止） |

深度 8 覆盖：减益 → 残梦 → 再施加集真赤 → 叠层相关钩，而不允许无限互殴。

---

## 6. 与现有机制：`energy_cost` / `variable_changes` / ERR

### 6.1 并存策略（推荐）

| 机制 | 去留 | 职责 |
|---|---|---|
| `Action.energy_cost` | **保留** | 行动自身宣告的耗能 / 回能（负值）；终结技扣能量上限等 |
| `Action.variable_changes` | **保留（消耗侧）** | 终结技 `nihility_stacks -9` 等消耗仍写在 Action 上。战技残梦 **+1 已迁出**：由 R3 与主目标集真赤同步发放，禁止再用 `variable_changes` 正增量（避免双计） |
| `Action.sp_cost` | **保留** | 战技点消耗/回复（花火终结技 `-6`） |
| 事件触发器 `change` | **新增** | **反应式**获取：天赋听减益、受击回能、击杀回能、E2 turn_start、光锥条件等 |

**不迁移**：把战技自带 `variable_changes` 改写成触发器（避免双计）。L1 已说明：战技 `variable_changes` 与「施加减益」是两条规则。

**迁移候选（）**：仅当回能今天**不在** Action 字段、而在外部表（`SPBase`、受击、击杀）时，用触发器 + 数据表接通。

### 6.2 ERR

| 项 | 设计 |
|---|---|
| 字段 | `Stats.err: float`（能量回复效率；1.0 = 100%） |
| 来源 | 配装显式字段（现 `acheron_direct.yaml` 的 `err`）；缺省报错，**代码不默认 1.0**（与 L1 适配器一致） |
| 生效 | **一切能量增加**（`energy_cost<0`、触发器 `resource: energy`、受击/击杀回能）在入账前：`delta = raw_delta * stats.err` |
| 不生效 | 能量**消耗**、SP、自定义变量（残梦）、终结技消耗 |

 须把 `err` 从 yaml 写入 `Stats`（今日仅 L1 读取）。

---

## 7. 三个 UNKNOWN 配置项

写入场景或全局 `configs/combat_rules.yaml`（名称可改）。**最终取值仍在暂缓清单**；设计期工作默认如下（与 L1「每次技能最多 1 次 / 追加攻击未建模」一致）。若与此前口头设定不符，确认 时改正。

| 配置 | 工作默认 | 类型 | 影响范围 |
|---|---|---|---|
| `KNOT_TIE_BREAK` | `stable_unit_id_asc` | enum | 凡 `select_unit(... max/min stacks)` 并列：集真赤施加目标、死亡转移目标、E2「最多」目标、椒丘 E6「最少」目标 |
| `DEBUFF_REFRESH_COUNTS` | `true` | bool | `effect_applied` 且 `was_refresh=true` 时是否算「陷入负面」。**true**（暂定）：重复施加也触发叠层，与「施放技能期间使敌方陷入负面效果即触发」一致；对黄泉开大频率影响显著 |
| `FOLLOWUP_COUNTS_AS_ACTION` | `true` | bool | 判定 `action_kind_in_skill_cast`。**true**（暂定）：追加攻击算「施放技能」（有明确行动过程）；`false` 会低估含追击队伍。最终值待 对照后再定 |

可选枚举补全：`KNOT_TIE_BREAK ∈ { stable_unit_id_asc, stable_unit_id_desc, lowest_av_remaining, random_seeded }`。

---

## 8. 黄泉集真赤 / 残梦 R1–R5（**仅 E0**）

来源：天赋原文（「任意单位施放技能期间使敌方陷入负面…每次施放技能最多触发 1 次」；「消灭后转移至集真赤最多者」）。  
**E2+ 额外来源另条 trigger，不并进 R1–R5。**  
仓库内原先无编号条文；下列编号为本设计对原文的操作化，供数据与 T21–T27 对齐。

| 规则 | 操作化 |
|---|---|
| **R1** | 任一单位的一次「施放技能」（§7）期间，若产生至少一次合格的敌方减益 `effect_applied`，则黄泉 `nihility_stacks += 1`，且为选定敌人施加 1 层 `crimson_knot`。`per_action_cap: 1`（该次技能实例最多触发一套）。 |
| **R2** | 合格减益的定义：`is_debuff=true` 且目标为敌方；是否含刷新由 `DEBUFF_REFRESH_COUNTS` 决定。 |
| **R3** | 若同一次技能使**多个**敌人陷入减益：集真赤加给其中 `crimson_knot` **层数最多**者；并列 → `KNOT_TIE_BREAK`。若仅单个敌人，加给该敌人。 |
| **R4** | 黄泉**自身终结技期间**不得新附集真赤（原文）；`filter` 含 `acheron_ult_active: false` 或引擎在 ult 上下文设抑制旗标（旗标由 `ultimate_used`/`action_resolved` 维护，非角色特例分支表）。 |
| **R5** | `enemy_killed`（或离场）：死者持有的 `crimson_knot` **全部转移**给场上存活且层数**最多**的敌人；并列 → `KNOT_TIE_BREAK`。无存活敌人则层数消失。 |

### 8.1 数据表达示例（E0）

```yaml
# data/hsr/triggers/characters/acheron_e0_crimson.yaml
owner: acheron
eidolon_max: 0   # 文档约定：R1–R5 仅 E0 档启用；E1+ 仍可用同一文件直至被 E 分支覆盖
config_refs: [KNOT_TIE_BREAK, DEBUFF_REFRESH_COUNTS, FOLLOWUP_COUNTS_AS_ACTION]

triggers:
  - id: acheron_r1_r3_dream_and_knot
    on: effect_applied
    filter:
      is_debuff: true
      target_side: enemy
      action_kind_in_skill_cast: true
      was_refresh: false          # 若 DEBUFF_REFRESH_COUNTS=true，加载器改写为可选
      acheron_ult_active: false
    per_action_cap: 1
    # 同一次技能内：先记「本技能已减益的敌人」集合；cap 耗尽前合并为一次结算
    change:
      resource: nihility_stacks
      delta: 1
    target_unit: owner
    apply_effect:
      effect_id: crimson_knot
      stacks: 1
      target_unit: query:enemy_max_stacks
      query:
        effect_id: crimson_knot
        among: event.debuff_targets_this_action
        tie_break: $KNOT_TIE_BREAK

  - id: acheron_r5_transfer_on_kill
    on: enemy_killed
    filter:
      victim_has_effect: crimson_knot
    transfer_stacks:
      effect_id: crimson_knot
      from: event.victim
      to: query:enemy_max_stacks
      query:
        effect_id: crimson_knot
        among: enemies_alive
        exclude: event.victim
        tie_break: $KNOT_TIE_BREAK
      mode: move_all
```

### 8.2 `acheron_ult` 是否读取 `crimson_knot`

| 现状 | 结论 |
|---|---|
| 当前 `acheron_ult` JSON | 固定倍率段；`applies_effects: []`；**不读取**集真赤层数 |
| 游戏原文 | 啼泽雨斩消去最多 3 层并按消去层数提高倍率；返渡移除全部 |

**设计**： 不把「读层数」塞进通用 `change`；用行动级描述符（建议挂在 action `_meta`）：

```yaml
# 示意：行动段修饰，仍非 char.id 硬编码
acheron_ult:
  segments:
    - id: rainblade
      consume_effect: { id: crimson_knot, max: 3 }
      damage_bonus_per_consumed_stack: <from ParamList>
    - id: stygian
      remove_effect_all_enemies: crimson_knot
```

实现可归 或紧随的数据修补任务；事件层提供 `effect_consumed` 预留事件供光锥监听。

---

## 9. 必须机制的数据表达示例

### 9.1 黄泉 E2（turn_start）

原文：行迹「奈落」最高档所需虚无数 −1；自身回合开始 +1 残梦，并为集真赤最多的敌人 +1 层。

```yaml
- id: acheron_e2_turn_start
  eidolon_min: 2
  on: turn_start
  filter:
    owner_is_event_unit: true
    turn_kind: NORMAL
  target_unit: owner
  change: { resource: nihility_stacks, delta: 1 }
  apply_effect:
    effect_id: crimson_knot
    stacks: 1
    target_unit: query:enemy_max_stacks
    query:
      effect_id: crimson_knot
      among: enemies_alive
      tie_break: $KNOT_TIE_BREAK
# 深渊档位修正见 9.2 eidolon_mods（已有 conditional_traces.json）
```

### 9.2 黄泉深渊行迹（队伍构成查询）

```yaml
# 已存在于 conditional_traces.json — 此处为规范形
- id: acheron_abyss
  on: outgoing_damage          # 同步钩
  filter:
    action_kind_in: [basic_attack, skill, ultimate]
  damage_mod:
    kind: multiplier_tiers
    count_query:
      query: count_units
      side: allies
      filter: { avatar_base_type: Warlock }
      exclude: self
    tiers:
      - { min: 1, mult: 1.15 }
      - { min: 2, mult: 1.60 }
  eidolon_mods:
    - { eidolon_min: 2, highest_tier_count_delta: -1 }
      # 计数值有效值 = raw + delta 后再套档（E2：1 名即可 1.60×）
```

### 9.3 砂金 E6（持盾队友数）

```yaml
- id: aventurine_e6_shield_allies
  eidolon_min: 6
  on: outgoing_damage
  filter: { owner_is_attacker: true }
  damage_mod:
    kind: bonus_per_count
    count_query:
      query: count_units
      side: allies
      filter: { has_shield: true }
      exclude: self          # 原文「队友」；若实机含自身盾，确认后改 exclude
    per: 0.5
    cap: 1.5
```

### 9.4 椒丘 E6（上限 9 + 死亡转给**最少**者）

与黄泉 R5 **方向相反**（max → min）。

```yaml
- id: jiaoqiu_e6_cap
  eidolon_min: 6
  on: battle_start            # 或加载时静态 apply
  modify_cap: { effect_id: ashen_roast, value: 9 }

- id: jiaoqiu_e6_transfer_on_kill
  eidolon_min: 6
  on: enemy_killed
  filter: { victim_has_effect: ashen_roast }
  transfer_stacks:
    effect_id: ashen_roast
    from: event.victim
    to: query:enemy_min_stacks
    query:
      effect_id: ashen_roast
      among: enemies_alive
      exclude: event.victim
      tie_break: $KNOT_TIE_BREAK
    mode: move_all
# Param 每层全抗降低 3% 为效果 modifiers，属面板/效果定义，非本触发器
```

### 9.5 花火终结技回 SP + 溢出上限 10

行动自带回复仍用 `sp_cost: -6`（并存）。溢出银行用触发器：

```yaml
- id: sparkle_ult_sp_overflow_bank
  on: action_used
  filter: { action_id: sparkle_ult }   # 或 action_kind: ultimate + owner
  # 伪过程：raw = 6；apply min(pool+raw, max)；overflow = raw - applied
  change:
    resource: sp_team
    delta: 6
    soft_cap: sp_team_max            # 先受队上限钳制
  overflow_to:
    resource: sparkle_sp_overflow     # 角色变量
    cap: 10

- id: sparkle_overflow_drain
  on: turn_end
  filter: { any_ally_turn: true }    # 我方任一角色 turn_end 后
  # 若 sp_team < max 且 overflow>0：转移直至满或耗尽
  change:
    resource: sp_team
    delta_from: sparkle_sp_overflow
    stop_at: sp_team_max
```

（精确「我方角色回合结束」过滤在 用 `turn_end` + `unit_side: ally` 实现。）

### 9.6 能量获取（ 接通；本设计定形）

```yaml
# 技能 SPBase → 行动结束回能（示例数值来自数据文件，不在设计里写死）
- id: energy_on_skill_spbase
  on: action_resolved
  filter: { action_kind_in: [basic_attack, skill, ultimate] }
  target_unit: event.actor
  change:
    resource: energy
    delta: { from_table: AvatarSkillConfig.SPBase, skill_id: $action.skill_id }
    # 入账前 × Stats.err

- id: energy_on_hit_taken
  on: hit_taken
  filter: { defender_side: ally }
  target_unit: event.defender
  change:
    resource: energy
    delta: { from_table: hit_energy, unknown_until_A4: true }
    # × Stats.err

- id: energy_on_kill
  on: enemy_killed
  filter: { killer_side: ally }
  target_unit: event.killer
  change:
    resource: energy
    delta: { from_table: kill_energy, unknown_until_A4: true }
    # × Stats.err
```

黄泉 `energy_max=0`：能量触发器对无能量角色 **no-op**（或加载时不挂）；残梦只走变量触发器。

### 9.7 条件光锥与套装（D9.1 b/c）

```yaml
# 黄泉专武 23024：对持有「幻灭泡影」目标增伤（示意）
- id: lc_23024_mirage_dmg
  on: outgoing_damage
  filter:
    target_has_effect: mirage_fizzle
  damage_mod: { kind: dmg_boost_add, value: { from_lc_param: 2 } }

# Pioneer 2pc：对有减益目标增伤
- id: relic_pioneer_2pc
  on: outgoing_damage
  filter: { target_debuff_count_min: 1 }
  damage_mod: { kind: dmg_boost_add, value: 0.12 }

# Izumo：编排/battle_start 查询同命途队友
- id: planar_izumo_cr
  on: battle_start
  filter:
    count_query:
      query: count_units
      side: allies
      filter: { avatar_base_type: $owner.avatar_base_type }
      exclude: self
      min: 1
  apply_effect: { effect_id: izumo_cr_buff, target_unit: owner }

# Eagle 4pc：终结技后行动提前 — (c)，/推条前可先标 requires_l2
- id: relic_eagle_4pc
  on: action_resolved
  filter: { action_kind: ultimate }
  change: { resource: action_advance_pct, delta: 0.25 }  # 或调用 advance_forward
```

Broken Keel：`on: stats_finalized`，`filter: { owner_effect_res_gte: 0.30 }` → 全队 CD；不进战斗 tick 总线亦可。

---

## 10. A3 减益核对（现状）

| 项 | 状态 |
|---|---|
| A3 任务 | `TODO_L1_L2_MASTER.md` 仍为 ⬜；`DATA_FIELD_SCAN_ENERGY_SP.md` §3 待填 |
| 本设计操作定义 | `effect_applied.is_debuff = not is_buff` |
| 减益判定 | `effect_applied.is_debuff = not is_buff` |

当前四人敌方 `is_buff=False` 效果（供 A3 对照，非最终判定）：黄泉 `nihility_def_shred`；花火 `sparkle_enemy_vuln`；椒丘战技/终结相关易伤与灼烧类；砂金追击易伤等——完整表留给 A3。

---
