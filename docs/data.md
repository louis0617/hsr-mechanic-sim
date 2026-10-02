# Data acquisition

This repository does not redistribute Honkai: Star Rail unpack dumps.

## Mar-7th / StarRailRes

Skill description text (for example `data/hsr/descriptions/acheron.txt`) originates from
[Mar-7th/StarRailRes](https://github.com/Mar-7th/StarRailRes).

```bash
python scripts/fetch_starrail_res.py
```

## Dimbreath / TurnBasedGameData

Numeric `ParamList` rows used by skill-point helpers come from a datamine subset.
`scripts/fetch_datamine.py` pulls the needed ExcelOutput files into
`data/external/turnbasedgamedata/`.

`data/external/turnbasedgamedata/ExcelOutput/AvatarSkillConfig.json` in this
repository is a synthetic example for Sparkle talent `1130604` at level 10
(values documented in `docs/shared_trigger_rules.md`), so tests run without a
full clone.
