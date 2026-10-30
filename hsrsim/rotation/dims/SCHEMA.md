# decision_dims / cycle_signature 声明

与角色模板**逻辑并列**（本目录按角色 ID 存放），**不写进搜索器硬编码**。

## `decision_dims.yaml` 字段

```yaml
character_id: "1308"
slug: acheron
decision_dims:
  - name: ult_timing
    values: [immediate_when_full, wait_sparkle_skill_buff]
    default: immediate_when_full
  - name: sp_min
    values: [1, 2, 99]   # 99 = never skill
    default: 1
cycle_signature_fields:
  - kind: resource
    id: slashed_dream
  - kind: modifier_stacks
    id: CRIMSON_KNOT
    "on": enemies_max   # 键必须加引号；YAML 1.1 会把 on 读成布尔值
  - kind: energy
    on: self
  - kind: skill_points
    on: team
  - kind: av_phase
    on: self
```

取值以现有自适应轴为准，不新增未确认维度。

`cycle_signature_fields` 的 `id` 用我方 L2 运行时的变量名或效果 id（黄泉线搜索调用的是 L2）。表里没有的 id 会记为 unresolved，不补写成 0。

`kind: dot_remaining_turns` 读取该效果上 `dot_instance` 不为空时的 `duration_turns`（剩余回合）。杠杆没有独立状态，不写入签名。
