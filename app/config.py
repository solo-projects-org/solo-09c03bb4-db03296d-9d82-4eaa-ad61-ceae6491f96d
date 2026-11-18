"""运行配置，全部可通过环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite 数据库文件（持久化卷）
DB_PATH: Path = Path(os.getenv("COHORT_DB_PATH", BASE_DIR / "data" / "cohort.db"))
# 上传文件的暂存目录（解析完成后即删除，不长期保存原始 CSV）
UPLOAD_DIR: Path = Path(os.getenv("COHORT_UPLOAD_DIR", BASE_DIR / "uploads"))
# 分析时间窗（天）
HORIZON_DAYS: int = int(os.getenv("COHORT_HORIZON_DAYS", "30"))
# 单次上传大小上限（字节），默认 50 MB
MAX_UPLOAD_BYTES: int = int(os.getenv("COHORT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))


def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
