"""Community cycle axes (≥2 sources, full citations). Not frame-perfect video axes."""
from __future__ import annotations

from hsrsim.rotation.schema import CycleAxis, MemberPolicy

SRC_MIYOUSHE_JQ = {
    "platform": "米游社",
    "title": "【V2.4攻略】角色攻略 椒丘｜机制/养成/就业解析",
    "author": "米游社攻略帖",
    "date": "2024-08（V2.4）",
    "url": "https://www.miyoushe.com/sr/article/56831356",
    "nature": "角色攻略：循环写法 EAA/AAE、速度序、配队",
}
SRC_17173_JQ = {
    "platform": "17173",
    "title": "椒丘｜行迹星魂详细解析",
    "author": "17173",
    "date": "2024-08-14",
    "url": "http://news.17173.com/content/08142024/161503481.shtml",
    "nature": "机制攻略：开局战技→普攻→普攻；常规普攻→普攻→战技；满层后多普攻",
}
SRC_MIYOUSHE_ACHERON = {
    "platform": "米游社",
    "title": "【V2.1攻略】角色攻略 黄泉｜机制/养成/就业解析",
    "author": "米游社攻略帖",
    "date": "2024-03（V2.1）",
    "url": "https://www.miyoushe.com/sr/article/50731743",
    "nature": "配队与速度序：花火/佩拉＞生存＞黄泉；花火 160+；黄泉不堆速",
}
SRC_TAPTAP_JQ = {
    "platform": "TapTap",
    "title": "【V2.4攻略】角色攻略 椒丘｜机制/养成/就业解析",
    "author": "转载米游社同文",
    "date": "2024-08",
    "url": "https://www.taptap.cn/moment/575340015098268141",
    "nature": "独立平台转载：EAA 循环、速度序椒丘＞佩拉＞生存＞黄泉、队友 160+",
}
SRC_MIYOUSHE_JQ_ONELINER = {
    "platform": "米游社",
    "title": "【V2.4攻略】「椒丘」全方位·一图流",
    "author": "米游社攻略帖",
    "date": "2024-08（V2.4）",
    "url": "https://www.miyoushe.com/sr/article/56831322",
    "nature": "一图流：实战 EAA 覆盖增益并产点",
}
SRC_TGBUS = {
    "platform": "电玩巴士 TGBUS",
    "title": "《崩坏:星穹铁道》黄泉 角色攻略",
    "author": "TGBUS",
    "date": "2024",
    "url": "https://m.tgbus.com/news/219146",
    "nature": "操作：花火战技拉条后到下回合开始前的窗内开黄泉终结技；黄泉尽量战技",
}
SRC_MIYOUSHE_FLOW = {
    "platform": "米游社",
    "title": "【V2.1攻略】巡海游侠-黄泉全方位攻略",
    "author": "米游社攻略帖",
    "date": "2024-03",
    "url": "https://www.miyoushe.com/sr/article/50733687",
    "nature": "输出流程：黄泉全 E、亮 Q 就点；佩拉 EA；花火一直 E 拉黄泉",
}


GUIDE_SPEED_JQ = {
    "sparkle": 161.0,
    "jiaoqiu": 160.0,
    "aventurine": 160.0,
    "acheron": 101.0,
}
GUIDE_SPEED_PELA = {
    "sparkle": 161.0,
    "pela": 160.0,
    "aventurine": 160.0,
    "acheron": 101.0,
}
GUIDE_SPEED_MORT = {
    "sparkle": 161.0,
    "mortenaxblade": 160.0,
    "aventurine": 160.0,
    "acheron": 101.0,
}
GUIDE_SPEED_MORT_JQ = {
    "jiaoqiu": 160.0,
    "mortenaxblade": 160.0,
    "aventurine": 160.0,
    "acheron": 101.0,
}
GUIDE_SPEED_FX = {
    "sparkle": 161.0,
    "pela": 160.0,
    "jiaoqiu": 160.0,
    "fu_xuan": 160.0,
    "acheron": 101.0,
}
GUIDE_SPEED_SOURCES = [
    {**SRC_MIYOUSHE_JQ, "nature": "其他三位尽量 160+；黄泉尽量原始速度；顺序椒丘＞佩拉＞生存＞黄泉"},
    {**SRC_MIYOUSHE_ACHERON, "nature": "花火 160+；花火/佩拉＞生存＞黄泉；黄泉不堆速"},
]


def _adaptive_member(
    cid: str,
    *,
    sp_min: int,
    ult: str = "immediate_when_full",
    target: str | None = None,
) -> MemberPolicy:
    cycle = ["basic"] if sp_min >= 99 else ["skill"]
    return MemberPolicy(
        cid,
        cycle,
        ult=ult,
        default_target=target,
        mode="adaptive",
        sp_min=sp_min,
    )


# 抢点优先级（高先）：花火一直 E；黄泉尽量战技；辅助维持减益；砂金最后。
# 速度序「辅＞生存＞黄泉」是配速不是抢点（米游社黄泉文 vs TGBUS/流程文）。
COMMUNITY_SP_PRIORITY_JQ = ["sparkle", "acheron", "jiaoqiu", "aventurine"]
COMMUNITY_SP_PRIORITY_PELA = ["sparkle", "acheron", "pela", "aventurine"]


def community_jq_axis() -> CycleAxis:
    """社区轴：自适应 SP（非严格序列）。"""
    return CycleAxis(
        axis_id="community_jq_adaptive",
        team_id="acheron_direct",
        notes=(
            "自适应：战技点≥k 放战技否则普攻。"
            "抢点：花火＞黄泉＞椒丘＞砂金（花火一直 E、黄泉尽量战技；"
            "速度序椒丘＞生存＞黄泉是配速，不是最后 1 点归属）。"
            "k：花火/黄泉/椒丘=1；砂金=2（仅盈余，纸面最大 vs 生存）。"
        ),
        sources=[SRC_MIYOUSHE_JQ, SRC_TAPTAP_JQ, SRC_17173_JQ, SRC_MIYOUSHE_FLOW, SRC_TGBUS],
        sp_priority=list(COMMUNITY_SP_PRIORITY_JQ),
        members={
            "sparkle": _adaptive_member("sparkle", sp_min=1, target="acheron"),
            "jiaoqiu": _adaptive_member("jiaoqiu", sp_min=1),
            "acheron": _adaptive_member("acheron", sp_min=1),
            "aventurine": _adaptive_member("aventurine", sp_min=2),
        },
    )


def community_pela_axis() -> CycleAxis:
    return CycleAxis(
        axis_id="community_pela_adaptive",
        team_id="acheron_old_pela_res",
        notes=(
            "自适应：花火一直 E、黄泉全 E/尽量战技、佩拉有点就 E（原 EA 序列的 SP 可行编码）。"
            "抢点：花火＞黄泉＞佩拉＞砂金。砂金 k=2。"
        ),
        sources=[SRC_MIYOUSHE_ACHERON, SRC_MIYOUSHE_FLOW, SRC_TGBUS],
        sp_priority=list(COMMUNITY_SP_PRIORITY_PELA),
        members={
            "sparkle": _adaptive_member("sparkle", sp_min=1, target="acheron"),
            "pela": _adaptive_member("pela", sp_min=1),
            "acheron": _adaptive_member("acheron", sp_min=1),
            "aventurine": _adaptive_member("aventurine", sp_min=2),
        },
    )


def pela_start_plus_jiaoqiu_skills() -> CycleAxis:
    """F-e: 佩拉队轴结构 + 椒丘技能，作为搜椒丘队的起点。"""
    pela = community_pela_axis()
    members = {
        "sparkle": pela.members["sparkle"],
        "acheron": pela.members["acheron"],
        "aventurine": pela.members["aventurine"],
        "jiaoqiu": MemberPolicy("jiaoqiu", ["skill"], ult="immediate_when_full"),
    }
    return CycleAxis(
        axis_id="start_pela_axis_plus_jq_skills",
        team_id="acheron_direct",
        notes="F-e 起点：佩拉老队轴（辅助全战技）+ 椒丘技能；椒丘循环先按佩拉全 E，不预置社区 EAA。",
        sources=list(pela.sources),
        members=members,
    )
