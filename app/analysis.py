"""竞争风险下的 Aalen-Johansen 累计发生概率（CIF）计算。

数据约定（每人一条记录）：
- day：结局发生日，1~30
- outcome：READMISSION（再入院，事件 1）、DEATH（死亡，竞争事件 2）、
  CENSORED（随访截止，普通删失）

估计方法：离散到日尺度的 Aalen-Johansen 乘积极限估计。

- 日初在险人数 Y_t：第 t 天开始时尚未发生任何事件、未删失的人数。
  同日再入院与死亡共用同一个日初在险人数（标准竞争风险风险集定义）。
- 当日原因别危险：h_{1t}=d1_t/Y_t，h_{2t}=d2_t/Y_t（Y_t=0 时为 0）。
- 当日总生存因子：S_t = S_{t-1} * (1 - (d1_t+d2_t)/Y_t)，
  删失者在当日事件计数之后才退出风险集，因此不影响当日危险的分母。
- CIF 增量：ΔCIF_{kt} = S_{t-1} * h_{kt}；CIF 逐日累加。
- 死亡是竞争结局而不是普通删失：它进入事件计数、压低生存因子，
  只有 CENSORED 才按普通删失处理（仅在当日事件后从风险集移除）。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from . import config


def compute_aalen_johansen(records: list[tuple[str, int, str]] | list[Any]) -> dict:
    """输入逐人记录 (subject_id, day, outcome)，返回逐日表与汇总。

    即使某天没有任何事件也会输出该日行（at_risk 可能为 0），覆盖 1~30 全天。
    """
    horizon = config.HORIZON_DAYS

    # 按天归集事件计数
    events: dict[int, dict[str, int]] = {
        d: {"READMISSION": 0, "DEATH": 0, "CENSORED": 0} for d in range(1, horizon + 1)
    }
    n_subjects = 0
    for rec in records:
        # 同时支持 tuple 与 sqlite3.Row
        subject_id, day, outcome = rec[0], int(rec[1]), rec[2]
        if not 1 <= day <= horizon:
            raise ValueError(f"subject {subject_id!r} 的 day={day} 超出 1~{horizon}")
        if outcome not in events[day]:
            raise ValueError(f"subject {subject_id!r} 的结局非法：{outcome!r}")
        events[day][outcome] += 1
        n_subjects += 1

    daily: list[dict] = []
    at_risk = n_subjects
    survival = 1.0  # S_0 = 1
    cif_readm = 0.0
    cif_death = 0.0
    total_readm = total_death = total_censored = 0
    last_activity_day = 0

    for day in range(1, horizon + 1):
        d1 = events[day]["READMISSION"]
        d2 = events[day]["DEATH"]
        dc = events[day]["CENSORED"]
        if d1 or d2 or dc:
            last_activity_day = day

        y_t = at_risk  # 日初在险人数：两类事件共用
        if y_t > 0:
            h1 = d1 / y_t
            h2 = d2 / y_t
            # 当日事件（两类竞争事件）后的生存概率；删失不进入危险
            survival_after = survival * (1.0 - (d1 + d2) / y_t)
            # CIF 增量 = 当日开始时的生存概率 × 原因别危险
            delta_cif1 = survival * h1
            delta_cif2 = survival * h2
        else:
            h1 = h2 = 0.0
            survival_after = survival
            delta_cif1 = delta_cif2 = 0.0

        cif_readm += delta_cif1
        cif_death += delta_cif2

        daily.append(
            OrderedDict(
                day=day,
                at_risk=y_t,
                readmission_count=d1,
                death_count=d2,
                censored_count=dc,
                readmission_cif=round(cif_readm, 10),
                death_cif=round(cif_death, 10),
                survival=round(survival_after, 10),
            )
        )

        # 当日事件后：事件者与删失者一并退出次日风险集
        at_risk = at_risk - d1 - d2 - dc
        survival = survival_after
        total_readm += d1
        total_death += d2
        total_censored += dc

    # 守恒校验：所有人最终都以某种结局离开（30 天窗口结束时仍在险者除外）
    accounted = total_readm + total_death + total_censored
    remaining = n_subjects - accounted
    if remaining < 0:
        raise RuntimeError("事件计数超过总人数，输入数据可能损坏")

    return {
        "n_subjects": n_subjects,
        "n_readmission": total_readm,
        "n_death": total_death,
        "n_censored": total_censored,
        "cumulative_readmission": round(cif_readm, 10),
        "cumulative_death": round(cif_death, 10),
        "last_activity_day": last_activity_day,
        "daily": daily,
        "remaining_at_risk_after_horizon": remaining,
    }
