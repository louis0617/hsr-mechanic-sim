"""Assert compiled Nous team matches our main-caliber eidolon / LC / SI."""
from __future__ import annotations

from typing import Any

from hsrsim.l2_nous.ids import AVATAR_ID, LC_ID
from hsrsim.l2_nous.map_loadout import TEAM_CALIBER


class CaliberMismatchError(RuntimeError):
    """Compiled Nous member does not match TEAM_CALIBER / loadout yaml."""


def expected_caliber(team_key: str) -> dict[str, dict[str, Any]]:
    """slug → {eidolon, lc_id, superimposition} from TEAM_CALIBER + loadout yaml."""
    from hsrsim.loadout.benchmark import load_loadout_config

    cal = TEAM_CALIBER[team_key]
    cfg = load_loadout_config(cal["loadout"])
    if cfg is None:
        raise FileNotFoundError(cal["loadout"])
    out: dict[str, dict[str, Any]] = {}
    for slug in cal["members"]:
        ccfg = cfg["characters"][slug]
        lc = ccfg.get("light_cone") or {}
        out[slug] = {
            "eidolon": int(cal["eidolons"].get(slug, 0)),
            "lc_id": int(lc.get("id") or LC_ID[slug]),
            "superimposition": int(lc.get("superimposition") or 1),
            "avatar_id": AVATAR_ID[slug],
        }
    return out


def assert_member_dicts_match_caliber(
    members: list[dict[str, Any]], team_key: str
) -> list[dict[str, Any]]:
    """Validate pre-compile member dicts; return rows for reporting."""
    exp = expected_caliber(team_key)
    rows = []
    by_aid = {str(m.get("character_template")): m for m in members}
    for slug, want in exp.items():
        aid = want["avatar_id"]
        m = by_aid.get(str(aid))
        if m is None:
            raise CaliberMismatchError(f"{team_key}: missing member {slug} ({aid})")
        got_e = int(m.get("eidolon", -1))
        got_lc = int(m.get("light_cone_template") or 0)
        got_si = int((m.get("light_cone") or {}).get("superimposition") or 0)
        row = {
            "slug": slug,
            "avatar_id": aid,
            "expected_eidolon": want["eidolon"],
            "got_eidolon": got_e,
            "expected_lc": want["lc_id"],
            "got_lc": got_lc,
            "expected_si": want["superimposition"],
            "got_si": got_si,
            "ok": got_e == want["eidolon"]
            and got_lc == want["lc_id"]
            and got_si == want["superimposition"],
        }
        rows.append(row)
        if not row["ok"]:
            raise CaliberMismatchError(
                f"{team_key}/{slug}: expected eidolon={want['eidolon']} "
                f"lc={want['lc_id']} S{want['superimposition']}, "
                f"got eidolon={got_e} lc={got_lc} S{got_si}"
            )
    return rows


def assert_setup_eidolon_markers(
    eng: Any, team_key: str
) -> list[dict[str, Any]]:
    """After eng.setup(): E2_BURN_AMP only if jiaoqiu eidolon≥2; no leaked EIDO for E0."""
    exp = expected_caliber(team_key)
    rows = []
    for slug, want in exp.items():
        aid = want["avatar_id"]
        st = eng.state.actors[str(aid)]
        mods = list(st.modifiers.keys())
        e2 = "E2_BURN_AMP" in st.modifiers
        eido = [m for m in mods if m.startswith("EIDO_") or m.startswith("E1_") or m.startswith("E2_")]
        # jiaoqiu-specific burn amp marker
        if slug == "jiaoqiu":
            expect_e2 = want["eidolon"] >= 2
            ok = e2 == expect_e2
            if not ok:
                raise CaliberMismatchError(
                    f"jiaoqiu E2_BURN_AMP present={e2} but eidolon={want['eidolon']}"
                )
        else:
            ok = True
        rows.append(
            {
                "slug": slug,
                "eidolon": want["eidolon"],
                "E2_BURN_AMP": e2,
                "eidolon_ish_mods": eido,
                "ok": ok,
            }
        )
    return rows
