"""端到端 API 测试：上传、幂等、拒收、修订交错、重启恢复。"""
from __future__ import annotations

import threading
import time

import pytest


def _csv(rows, header="subject_id,day,outcome"):
    body = header + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n"
    return body.encode("utf-8")


def _upload(client, revision, rows, filename="c.csv"):
    return client.post(
        "/api/revisions/upload",
        data={"revision": str(revision)},
        files={"file": (filename, _csv(rows), "text/csv")},
    )


def _wait_status(client, revision, expected, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/analyses/{revision}")
        if resp.status_code == 200 and resp.json()["analysis_status"] == expected:
            return resp
        last = resp
        time.sleep(0.02)
    raise AssertionError(f"修订 {revision} 未在 {timeout}s 内达到 {expected}，最后响应={getattr(last, 'content', b'')!r}")


def test_upload_and_daily_result(app_client):
    client, db, services = app_client
    rows = [
        ("S1", 1, "READMISSION"), ("S2", 1, "DEATH"),
        ("S3", 2, "READMISSION"), ("S4", 30, "CENSORED"),
    ]
    resp = _upload(client, 1, rows)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True and body["idempotent"] is False
    assert body["analysis_status"] == "PENDING"

    done = _wait_status(client, 1, "COMPLETED")
    data = done.json()
    assert data["is_current"] is True
    assert data["summary"]["n_subjects"] == 4
    d1 = next(d for d in data["daily"] if d["day"] == 1)
    assert d1["at_risk"] == 4 and d1["readmission_count"] == 1 and d1["death_count"] == 1
    cur = client.get("/api/analyses/current").json()
    assert cur["revision"] == 1
    assert len(cur["daily"]) == 30


def test_idempotent_same_revision_same_content(app_client):
    client, db, services = app_client
    rows = [("S1", 1, "READMISSION"), ("S2", 2, "CENSORED")]
    r1 = _upload(client, 1, rows)
    assert r1.json()["accepted"] is True
    _wait_status(client, 1, "COMPLETED")

    # 同版同内容重送：幂等，不改变任何结果
    r2 = _upload(client, 1, rows)
    assert r2.status_code == 200
    assert r2.json()["accepted"] is False and r2.json()["idempotent"] is True

    before = client.get("/api/analyses/1").json()
    time.sleep(0.1)
    after = client.get("/api/analyses/1").json()
    assert before == after

    # 内容字节不同但语义相同（多一个空行）也算异内容 -> 仍按 SHA 拒收
    different = _csv(rows) + b"\n"
    r3 = client.post(
        "/api/revisions/upload",
        data={"revision": "1"},
        files={"file": ("c.csv", different, "text/csv")},
    )
    assert r3.status_code == 409
    assert r3.json()["error"] == "REVISION_CONFLICT"


def test_same_revision_different_content_rejected(app_client):
    client, db, services = app_client
    assert _upload(client, 1, [("S1", 1, "READMISSION")]).status_code == 200
    _wait_status(client, 1, "COMPLETED")
    resp = _upload(client, 1, [("S1", 1, "DEATH"), ("S2", 2, "CENSORED")])
    assert resp.status_code == 409
    assert resp.json()["error"] == "REVISION_CONFLICT"
    # 旧结论不受影响
    assert client.get("/api/analyses/1").status_code == 200


def test_revision_must_increase(app_client):
    client, db, services = app_client
    assert _upload(client, 1, [("S1", 1, "READMISSION")]).status_code == 200
    # 跳号
    resp = _upload(client, 3, [("S1", 1, "READMISSION")])
    assert resp.status_code == 409
    # 正确递增
    assert _upload(client, 2, [("S1", 1, "DEATH")]).status_code == 200
    # 旧修订被取代
    revs = {r["revision"]: r for r in client.get("/api/revisions").json()}
    assert revs[1]["status"] == "SUPERSEDED"
    assert revs[2]["status"] == "CURRENT"


def test_csv_validation_errors(app_client):
    client, db, services = app_client
    # 缺列
    bad_header = client.post(
        "/api/revisions/upload", data={"revision": "1"},
        files={"file": ("c.csv", b"subject_id,day\nS1,1\n", "text/csv")},
    )
    assert bad_header.status_code == 400
    # 重复编号
    dup = _upload(client, 1, [("S1", 1, "READMISSION"), ("S1", 2, "DEATH")])
    assert dup.status_code == 400
    # day 越界
    bad_day = _upload(client, 1, [("S1", 31, "READMISSION")])
    assert bad_day.status_code == 400
    # 非法结局
    bad_out = _upload(client, 1, [("S1", 1, "WALKED")])
    assert bad_out.status_code == 400


def test_chinese_outcome_aliases(app_client):
    client, db, services = app_client
    body = "subject_id,day,outcome\nS1,1,再入院\nS2,1,死亡\nS3,2,随访截止\n".encode()
    resp = client.post(
        "/api/revisions/upload", data={"revision": "1"},
        files={"file": ("c.csv", body, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = _wait_status(client, 1, "COMPLETED").json()
    d1 = next(d for d in data["daily"] if d["day"] == 1)
    assert d1["readmission_count"] == 1 and d1["death_count"] == 1
    assert d1["censored_count"] == 0


def test_interleaving_superseded_revision_never_published(app_client, monkeypatch):
    """修订交错：rev1 计算期间 rev2 受理，rev1 结论绝不发布，rev2 正常完成。"""
    client, db, services = app_client
    entered = threading.Event()
    release = threading.Event()

    real_load = db.load_records

    def slow_load(revision):
        if revision == 1:
            entered.set()
            assert release.wait(timeout=5), "rev2 未在 rev1 计算期间被受理"
        return real_load(revision)

    monkeypatch.setattr(services, "db", db)  # 确保引用同一模块
    monkeypatch.setattr(db, "load_records", slow_load)

    t1 = threading.Thread(
        target=lambda: _upload(client, 1, [("S1", 1, "READMISSION"), ("S2", 2, "DEATH")]),
        daemon=True,
    )
    t1.start()
    assert entered.wait(timeout=5), "rev1 计算未启动"

    # rev1 计算中：现行结论不可得，且不返回任何旧结论（此时尚无完成结论）
    cur = client.get("/api/analyses/current")
    assert cur.status_code == 409
    assert cur.json()["error"] == "ANALYSIS_PENDING"

    # rev2 受理，立即成为现行；rev1 被取代
    up2 = _upload(client, 2, [("S9", 3, "CENSORED")])
    assert up2.status_code == 200
    assert up2.json()["current_revision"] == 2

    # 放行 rev1 的计算
    release.set()
    t1.join(timeout=5)

    # rev1 永远不会变成 COMPLETED
    deadline = time.time() + 5
    while time.time() < deadline:
        row = db.get_analysis_row(1)
        if row["status"] in ("SUPERSEDED", "FAILED"):
            break
        time.sleep(0.02)
    assert db.get_analysis_row(1)["status"] == "SUPERSEDED"

    # rev2 完成并可经 current 接口取得
    data = _wait_status(client, 2, "COMPLETED").json()
    assert data["is_current"] is True
    assert data["summary"]["n_subjects"] == 1

    # rev1 查询不发布结论
    resp1 = client.get("/api/analyses/1")
    assert resp1.status_code == 409
    assert resp1.json()["error"] in ("ANALYSIS_SUPERSEDED",)

    # current 永远指向 rev2
    assert client.get("/api/analyses/current").json()["revision"] == 2


def test_restart_recovery_pending_and_failed(app_client, monkeypatch):
    """重启恢复：PENDING/FAILED 在新进程视角仍待重算，不返回旧结论且能自动完成。"""
    client, db, services = app_client
    rows = [("S1", 1, "READMISSION"), ("S2", 1, "DEATH"), ("S3", 5, "CENSORED")]
    assert _upload(client, 1, rows).status_code == 200
    _wait_status(client, 1, "COMPLETED")

    # 门控：让重算在读取记录处阻塞，模拟"受理后、重算完成前进程仍在运行/崩溃"
    entered = threading.Event()
    release = threading.Event()
    real_load = db.load_records

    def gated_load(revision):
        entered.set()
        assert release.wait(timeout=5), f"修订 {revision} 的重算未被放行"
        return real_load(revision)

    monkeypatch.setattr(db, "load_records", gated_load)

    # rev2 受理后工作线程在重算中阻塞 -> 确定性处于 PENDING
    assert _upload(client, 2, [("S1", 2, "DEATH")]).status_code == 200
    assert entered.wait(timeout=5)
    assert db.get_analysis_row(2)["status"] == "PENDING"

    # 此时查询不得返回旧（rev1 的）结论
    cur = client.get("/api/analyses/current")
    assert cur.status_code == 409 and cur.json()["error"] == "ANALYSIS_PENDING"

    # 模拟服务重启：恢复逻辑看到 PENDING 仍在（工作线程已存在则不重复拉起）
    services.recover_on_startup()
    assert db.get_analysis_row(2)["status"] == "PENDING"

    # 放行后重算完成
    release.set()
    data = _wait_status(client, 2, "COMPLETED").json()
    assert data["is_current"] is True and data["summary"]["n_subjects"] == 1

    # FAILED 也必须仍可重算：手动置 FAILED，再调 recompute
    db.mark_analysis_failed(2, "boom")
    assert client.get("/api/analyses/current").status_code == 409
    rc = client.post("/api/analyses/recompute")
    assert rc.status_code == 200 and rc.json()["analysis_status"] == "PENDING"
    assert _wait_status(client, 2, "COMPLETED").status_code == 200


def test_failed_computation_does_not_publish_stale_result(app_client, monkeypatch):
    client, db, services = app_client
    # rev1 先成功
    assert _upload(client, 1, [("S1", 1, "READMISSION")]).status_code == 200
    _wait_status(client, 1, "COMPLETED")

    # rev2 计算失败：落库 FAILED，current 查询不能退回 rev1 旧结论
    def boom(_revision):
        raise RuntimeError("simulated compute failure")

    monkeypatch.setattr(services, "compute_aalen_johansen", boom)
    assert _upload(client, 2, [("S2", 2, "DEATH")]).status_code == 200

    deadline = time.time() + 5
    while time.time() < deadline:
        if db.get_analysis_row(2)["status"] == "FAILED":
            break
        time.sleep(0.02)
    assert db.get_analysis_row(2)["status"] == "FAILED"

    cur = client.get("/api/analyses/current")
    assert cur.status_code == 409
    assert cur.json()["error"] == "ANALYSIS_FAILED"

    # 恢复计算后可以成功
    monkeypatch.undo()
    client.post("/api/analyses/recompute")
    assert _wait_status(client, 2, "COMPLETED").status_code == 200


def test_health_and_listing(app_client):
    client, db, services = app_client
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/revisions").json() == []
    assert client.get("/api/analyses/current").status_code == 404
