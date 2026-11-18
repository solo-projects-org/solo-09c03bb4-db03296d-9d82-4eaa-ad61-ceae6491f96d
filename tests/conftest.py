"""pytest 公共夹具：每个测试使用独立的临时 SQLite 数据库与上传目录。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 保证可以 import app 包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    up_dir = tmp_path / "uploads"
    db_dir.mkdir()
    up_dir.mkdir()
    monkeypatch.setenv("COHORT_DB_PATH", str(db_dir / "test.db"))
    monkeypatch.setenv("COHORT_UPLOAD_DIR", str(up_dir))
    monkeypatch.setenv("COHORT_HORIZON_DAYS", "30")

    # 重新导入配置/数据库/服务，使环境变量生效且状态干净
    for name in [
        "app.config",
        "app.database",
        "app.analysis",
        "app.csv_parser",
        "app.services",
        "app.schemas",
        "app.main",
    ]:
        sys.modules.pop(name, None)

    from fastapi.testclient import TestClient

    config = importlib.import_module("app.config")
    database = importlib.import_module("app.database")
    main = importlib.import_module("app.main")
    services = importlib.import_module("app.services")

    database.init_db()
    with TestClient(main.app) as client:
        yield client, database, services

    # 清理后台线程状态（TestClient 退出后守护线程会自行结束）
    services._stop_event.set()
