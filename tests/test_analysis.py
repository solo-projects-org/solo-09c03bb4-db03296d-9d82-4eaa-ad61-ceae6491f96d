"""Aalen-Johansen 计算单元测试，含手算对照与同日事件共用风险集。"""
from __future__ import annotations

import math

from app.analysis import compute_aalen_johansen


def _day(result, day):
    return next(d for d in result["daily"] if d["day"] == day)


def test_hand_calculated_cif():
    """10 人示例，逐天手算 Aalen-Johansen CIF。"""
    records = [
        ("S001", 1, "READMISSION"),
        ("S002", 1, "READMISSION"),
        ("S003", 1, "DEATH"),
        ("S004", 1, "CENSORED"),
        ("S005", 2, "READMISSION"),
        ("S006", 2, "DEATH"),
        ("S007", 3, "CENSORED"),
        ("S008", 4, "DEATH"),
        ("S009", 30, "CENSORED"),
        ("S010", 30, "CENSORED"),
    ]
    r = compute_aalen_johansen(records)

    assert r["n_subjects"] == 10
    assert r["n_readmission"] == 3
    assert r["n_death"] == 3
    assert r["n_censored"] == 4

    d1 = _day(r, 1)
    assert d1["at_risk"] == 10
    assert d1["readmission_count"] == 2
    assert d1["death_count"] == 1
    assert d1["censored_count"] == 1
    assert math.isclose(d1["readmission_cif"], 0.2)
    assert math.isclose(d1["death_cif"], 0.1)
    assert math.isclose(d1["survival"], 0.7)

    d2 = _day(r, 2)
    # 日初在险 = 10 - 2 - 1 - 1 = 6，再入院与死亡共用此分母
    assert d2["at_risk"] == 6
    assert math.isclose(d2["readmission_cif"], 0.2 + 0.7 / 6)
    assert math.isclose(d2["death_cif"], 0.1 + 0.7 / 6)
    assert math.isclose(d2["survival"], 0.7 * (4 / 6))

    d3 = _day(r, 3)
    assert d3["at_risk"] == 4  # 6 - 1 - 1
    assert d3["readmission_count"] == 0 and d3["death_count"] == 0
    assert d3["censored_count"] == 1
    # 无事件日 CIF 不变，生存概率不变
    assert math.isclose(d3["readmission_cif"], d2["readmission_cif"])
    assert math.isclose(d3["survival"], d2["survival"])

    d4 = _day(r, 4)
    assert d4["at_risk"] == 3  # 删失者在第 3 天事件后退出
    assert math.isclose(d4["death_cif"], d3["death_cif"] + d3["survival"] / 3)
    assert math.isclose(d4["survival"], d3["survival"] * (2 / 3))

    # 30 天：两人随访截止
    d30 = _day(r, 30)
    assert d30["at_risk"] == 2
    assert d30["censored_count"] == 2

    # 恒等式：CIF1 + CIF2 + S(30) = 1
    total = d30["readmission_cif"] + d30["death_cif"] + d30["survival"]
    assert math.isclose(total, 1.0, abs_tol=1e-9)

    assert math.isclose(r["cumulative_readmission"], d30["readmission_cif"])
    assert math.isclose(r["cumulative_death"], d30["death_cif"])
    assert len(r["daily"]) == 30


def test_same_day_events_share_at_risk_and_censor_exits_after():
    """同日两类事件共用日初在险人数；删失者当日事件后才退出。"""
    records = [
        ("A", 5, "READMISSION"),
        ("B", 5, "READMISSION"),
        ("C", 5, "DEATH"),
        ("D", 5, "CENSORED"),
        ("E", 5, "CENSORED"),
        ("F", 6, "READMISSION"),
    ]
    r = compute_aalen_johansen(records)
    d5 = _day(r, 5)
    assert d5["at_risk"] == 6
    assert d5["readmission_count"] == 2
    assert d5["death_count"] == 1
    assert d5["censored_count"] == 2
    # 分母为 6 而不是 6-2=4（共用日初风险集），删失也不进分母扣减
    assert math.isclose(d5["readmission_cif"], 2 / 6)
    assert math.isclose(d5["death_cif"], 1 / 6)
    assert math.isclose(d5["survival"], 3 / 6)
    d6 = _day(r, 6)
    # 次日在险 = 6 - 2 - 1 - 2 = 1（事件者与删失者均已退出）
    assert d6["at_risk"] == 1
    assert math.isclose(d6["readmission_cif"], 2 / 6 + (3 / 6) * 1)
    assert math.isclose(d6["survival"], 0.0)


def test_death_is_competing_event_not_censoring():
    """若把死亡误当删失会得到不同结果；本估计中死亡压低生存因子。

    2 人：第 1 天 1 死、第 2 天 1 再入院。
    正确做法：day1 S=0.5, CIF_death=0.5；day2 CIF_readm=0.5*1=0.5。
    若把死亡当删失，day1 危险会被算成 0 且在险错误地保留，结果错误。
    """
    r = compute_aalen_johansen(
        [("A", 1, "DEATH"), ("B", 2, "READMISSION")]
    )
    d1 = _day(r, 1)
    d2 = _day(r, 2)
    assert math.isclose(d1["death_cif"], 0.5)
    assert math.isclose(d1["readmission_cif"], 0.0)
    assert d2["at_risk"] == 1
    assert math.isclose(d2["readmission_cif"], 0.5)
    assert math.isclose(d2["death_cif"], 0.5)
    assert math.isclose(d2["survival"], 0.0)
    total = d2["readmission_cif"] + d2["death_cif"] + d2["survival"]
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_all_censored_no_events():
    records = [("A", 10, "CENSORED"), ("B", 20, "CENSORED")]
    r = compute_aalen_johansen(records)
    assert r["cumulative_readmission"] == 0.0
    assert r["cumulative_death"] == 0.0
    assert _day(r, 10)["at_risk"] == 2
    assert _day(r, 11)["at_risk"] == 1
    assert all(math.isclose(d["survival"], 1.0) for d in r["daily"])


def test_all_readmission_day1():
    records = [(f"A{i}", 1, "READMISSION") for i in range(4)]
    r = compute_aalen_johansen(records)
    d1 = _day(r, 1)
    assert math.isclose(d1["readmission_cif"], 1.0)
    assert math.isclose(d1["survival"], 0.0)
    assert _day(r, 2)["at_risk"] == 0
