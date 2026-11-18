"""分析编排：后台串行重算、重启恢复、修订交错保护。"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from . import database as db
from .analysis import compute_aalen_johansen

logger = logging.getLogger("cohort.analysis_service")

# 后台计算线程的全局控制
_worker_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None
_running_revision: Optional[int] = None
_stop_event = threading.Event()


def _run_revision(revision: int) -> None:
    """执行单个修订的重算并落库。"""
    global _running_revision
    _running_revision = revision
    try:
        row = db.get_analysis_row(revision)
        if row is None:
            logger.warning("修订 %s 无分析记录，跳过", revision)
            return
        if row["status"] != "PENDING":
            logger.info("修订 %s 状态为 %s，无需重算", revision, row["status"])
            return

        logger.info("开始重算修订 %s", revision)
        records = db.load_records(revision)
        result = compute_aalen_johansen(records)
        # 落库内部会再次校验 CURRENT 身份；被取代则转为 SUPERSEDED 而不发布
        db.save_analysis_result(revision, result)
        after = db.get_analysis_row(revision)
        logger.info("修订 %s 重算结束，最终状态 %s", revision, after["status"])
    except Exception as exc:  # noqa: BLE001 - 后台线程需吞掉异常并落 FAILED
        logger.exception("修订 %s 重算失败", revision)
        try:
            db.mark_analysis_failed(revision, f"{type(exc).__name__}: {exc}")
        except Exception:  # pragma: no cover - 落库失败仅记录日志
            logger.exception("修订 %s 失败状态落库也失败了", revision)
    finally:
        _running_revision = None


def _worker_loop() -> None:
    """后台守护线程循环：只要还有 PENDING 修订就逐个重算。"""
    logger.info("分析工作线程启动")
    while not _stop_event.is_set():
        pending = db.pending_revisions()
        if not pending:
            break
        revision = pending[0]
        _run_revision(revision)
    with _worker_lock:
        global _worker_thread
        _worker_thread = None
    logger.info("分析工作线程退出")


def ensure_worker() -> bool:
    """确保有一个后台工作线程在处理 PENDING 修订。返回是否新启动了线程。"""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return False
        _stop_event.clear()
        t = threading.Thread(target=_worker_loop, name="analysis-worker", daemon=True)
        _worker_thread = t
        t.start()
        return True


def recompute_sync(revision: int) -> None:
    """同步重算（供测试与启动恢复使用）。"""
    db.mark_analysis_pending(revision)
    _run_revision(revision)


def current_running_revision() -> Optional[int]:
    return _running_revision


def recover_on_startup() -> None:
    """服务启动时恢复：

    - 任何遗留的非终态分析都保持/重置为 PENDING（重算失败或重启后仍待重算）；
    - 启动后台线程逐个重算，期间查询只会看到 PENDING 而不会返回旧结论。
    """
    pending = db.pending_revisions()
    if pending:
        logger.info("启动恢复：待重算修订 %s", pending)
        ensure_worker()
