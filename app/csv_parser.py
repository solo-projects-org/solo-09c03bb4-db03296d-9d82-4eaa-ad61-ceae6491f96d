"""CSV 上传文件解析与校验。

CSV 要求（表头大小写不敏感、列序不限，但必须包含这三列）::

    subject_id,day,outcome

- subject_id：每人唯一编号，非空、不得重复
- day：结局发生日，1~30 的整数
- outcome：READMISSION / DEATH / CENSORED（兼容中文与常见别名）

解析在独立暂存文件上进行，调用方负责把上传字节先落到空目录中的新文件，
再把路径传入，避免在不可信目录内执行任何代码。
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass

from . import config

REQUIRED_COLUMNS = ("subject_id", "day", "outcome")

OUTCOME_ALIASES = {
    "READMISSION": "READMISSION",
    "REHOSPITALIZATION": "READMISSION",
    "REHOSPITALISATION": "READMISSION",
    "READMIT": "READMISSION",
    "RE_ADMISSION": "READMISSION",
    "再入院": "READMISSION",
    "再次住院": "READMISSION",
    "再住院": "READMISSION",
    "DEATH": "DEATH",
    "DIED": "DEATH",
    "DEAD": "DEATH",
    "死亡": "DEATH",
    "CENSORED": "CENSORED",
    "CENSOR": "CENSORED",
    "CENSORING": "CENSORED",
    "FOLLOW_UP_END": "CENSORED",
    "LOST": "CENSORED",
    "删失": "CENSORED",
    "截尾": "CENSORED",
    "随访截止": "CENSORED",
    "截止": "CENSORED",
}


class CsvValidationError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class ParsedCohort:
    content_sha256: str
    rows: list[tuple[str, int, str]]
    n_records: int


def _normalize_header(name: str) -> str:
    return name.strip().lstrip("﻿").lower().replace("-", "_").replace(" ", "_")


def _norm_outcome(raw: str) -> str:
    key = raw.strip().upper().replace(" ", "_").replace("-", "_")
    if key in OUTCOME_ALIASES:
        return OUTCOME_ALIASES[key]
    # 中文别名不含大写转换问题，再按原样尝试
    if raw.strip() in OUTCOME_ALIASES:
        return OUTCOME_ALIASES[raw.strip()]
    raise CsvValidationError(
        f"结局取值非法：{raw!r}，允许 READMISSION/DEATH/CENSORED（或中文 再入院/死亡/随访截止）"
    )


def parse_csv_bytes(data: bytes) -> ParsedCohort:
    """解析并全量校验 CSV 字节，返回规范化后的逐人记录。"""
    if not data:
        raise CsvValidationError("上传文件为空")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise CsvValidationError(
            f"文件超过大小上限 {config.MAX_UPLOAD_BYTES} 字节（实际 {len(data)}）"
        )

    content_sha256 = hashlib.sha256(data).hexdigest()

    # utf-8-sig 自动吃掉 BOM
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvValidationError(f"CSV 必须为 UTF-8 编码：{exc}") from exc

    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvValidationError("CSV 缺少表头") from exc

    normalized = [_normalize_header(h) for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in normalized]
    if missing:
        raise CsvValidationError(f"CSV 缺少必需列：{', '.join(missing)}；需要 {REQUIRED_COLUMNS}")
    if len(set(normalized)) != len(normalized):
        raise CsvValidationError(f"CSV 表头存在重复列：{header}")
    idx = {name: normalized.index(name) for name in REQUIRED_COLUMNS}

    rows: list[tuple[str, int, str]] = []
    seen_ids: set[str] = set()
    data_lines = 0
    for line_no, record in enumerate(reader, start=2):
        # 跳过完全空白的行（csv 对空行返回 []）
        if not record or all((cell is None or cell.strip() == "") for cell in record):
            continue
        if len(record) != len(header):
            raise CsvValidationError(
                f"列数 {len(record)} 与表头列数 {len(header)} 不一致", line=line_no
            )
        subject_id = record[idx["subject_id"]].strip()
        if not subject_id:
            raise CsvValidationError("subject_id 不能为空", line=line_no)
        if subject_id in seen_ids:
            raise CsvValidationError(f"subject_id 重复：{subject_id!r}", line=line_no)

        raw_day = record[idx["day"]].strip()
        try:
            day = int(raw_day)
        except ValueError as exc:
            raise CsvValidationError(f"day 必须为整数，实际为 {raw_day!r}", line=line_no) from exc
        if not 1 <= day <= config.HORIZON_DAYS:
            raise CsvValidationError(
                f"day 必须在 1~{config.HORIZON_DAYS} 之间，实际为 {day}", line=line_no
            )

        outcome = _norm_outcome(record[idx["outcome"]])
        seen_ids.add(subject_id)
        rows.append((subject_id, day, outcome))
        data_lines += 1

    if data_lines == 0:
        raise CsvValidationError("CSV 没有任何数据行")

    return ParsedCohort(content_sha256=content_sha256, rows=rows, n_records=len(rows))
