from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.spacing.model import RemeasureRequest


def _payload(**overrides):
    body = {"measure_no": "RM-API-1", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17-01", "measurer": "周保管"},
        {"cabinet": "CV-21", "upper_mm": 1450, "lower_mm": 450, "evidence": "RC-21-01", "measurer": "周保管"}]}
    body.update(overrides)
    return body


def test_published_review_creates_version_and_keeps_original_archive(client, base):
    reply = client.post(base + "/spacing/reviews", json=_payload())
    assert reply.status_code == 201
    data = reply.json()
    assert data["status"] == "PUBLISHED" and data["reused"] is False
    report = data["report"]
    assert {item["cabinet"] for item in report["cabinets"]} == {"CV-17", "CV-21"}
    # 原始归卷的建档尺寸未被改写
    manifest = client.get(base).json()
    cv17 = next(item for item in manifest["cabinets"] if item["id"] == "CV-17")
    assert (cv17["upper_mm"], cv17["lower_mm"]) == (1550, 550)
    # 生效尺寸切到复核版
    effective = client.get(base + "/spacing/effective").json()
    assert effective["source"] == "SPACING_REVIEW"
    assert effective["cabinets"][0]["upper_mm"] == 1800


def test_same_remeasure_rerun_returns_same_review_without_new_version(client, base):
    first = client.post(base + "/spacing/reviews", json=_payload())
    review_id = first.json()["review_id"]
    second = client.post(base + "/spacing/reviews", json=_payload())
    assert second.status_code == 200
    body = second.json()
    assert body["reused"] is True and body["review_id"] == review_id
    history = client.get(base + "/spacing/reviews").json()
    assert len(history) == 1 and history[0]["id"] == review_id


def test_rejected_review_preserves_old_version_and_keeps_trace(client, base):
    good = client.post(base + "/spacing/reviews", json=_payload())
    assert good.status_code == 201
    effective_before = client.get(base + "/spacing/effective").json()["review_id"]

    bad = client.post(base + "/spacing/reviews", json={
        "measure_no": "RM-API-BAD", "cabinets": [
            {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": None, "evidence": "RC", "measurer": "周"}]})
    assert bad.status_code == 422
    failure = bad.json()
    assert failure["status"] == "REJECTED"
    assert failure["report"]["rejected_reasons"] == ["SINGLE_LAYER"]

    history = client.get(base + "/spacing/reviews").json()
    assert [item["status"] for item in history] == ["PUBLISHED", "REJECTED"]
    # 旧版尺寸仍是生效版，旧证未被抹掉
    assert client.get(base + "/spacing/effective").json()["review_id"] == effective_before
    assert client.get(base + "/spacing/reviews").json()[0]["status"] == "PUBLISHED"


def test_incomplete_evidence_batch_is_rejected_without_publishing(client, base):
    reply = client.post(base + "/spacing/reviews", json={
        "measure_no": "RM-API-INCOMPLETE", "cabinets": [
            {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "", "measurer": ""}]})
    assert reply.status_code == 422
    assert reply.json()["report"]["rejected_reasons"] == ["INCOMPLETE"]
    assert client.get(base + "/spacing/effective").json()["source"] == "ARCHIVE"


def test_history_and_export_are_self_describing(client, base):
    created = client.post(base + "/spacing/reviews", json=_payload()).json()
    review_id = created["review_id"]
    detail = client.get(base + f"/spacing/reviews/{review_id}").json()
    assert detail["status"] == "PUBLISHED" and detail["measure_no"] == "RM-API-1"
    export = client.get(base + f"/spacing/reviews/{review_id}/export")
    assert export.status_code == 200
    package = export.json()
    assert package["format"] == "museum-spacing-review-v1"
    assert package["report"]["revised_result_sha256"] == created["report"]["revised_result_sha256"]


def test_unknown_run_and_unknown_review_are_404(client, base):
    missing_run = client.post("/api/runs/nonexistent/spacing/reviews", json=_payload())
    assert missing_run.status_code == 404 and missing_run.json()["code"] == "UNKNOWN_RUN"
    missing_review = client.get(base + "/spacing/reviews/SR-nope")
    assert missing_review.status_code == 404 and missing_review.json()["code"] == "UNKNOWN_REVIEW"


def test_effective_falls_back_to_archive_dimensions(client, base):
    effective = client.get(base + "/spacing/effective").json()
    assert effective["source"] == "ARCHIVE" and effective["review_id"] is None
    assert {item["id"] for item in effective["cabinets"]} == {"CV-17", "CV-21"}


def test_second_published_review_chains_baseline_and_keeps_first(client, base):
    first = client.post(base + "/spacing/reviews", json=_payload()).json()
    second_payload = _payload(measure_no="RM-API-2", cabinets=[
        {"cabinet": "CV-17", "upper_mm": 1350, "lower_mm": 550, "evidence": "RC-17-02", "measurer": "吴保管"}])
    second = client.post(base + "/spacing/reviews", json=second_payload)
    assert second.status_code == 201 and second.json()["review_id"] != first["review_id"]
    history = client.get(base + "/spacing/reviews").json()
    assert [item["measure_no"] for item in history] == ["RM-API-1", "RM-API-2"]
    # 第二版以第一版为基线，第一版仍完整留存
    assert second.json()["report"]["baseline_review_id"] == first["review_id"]
    assert history[0]["report"]["revised_result_sha256"] == first["report"]["revised_result_sha256"]
    assert client.get(base + "/spacing/effective").json()["measure_no"] == "RM-API-2"


def test_review_request_validation_errors_are_422(client, base):
    reply = client.post(base + "/spacing/reviews", json={"measure_no": "RM-X", "cabinets": []})
    assert reply.status_code == 422


def test_review_versions_persist_across_app_restart(tmp_path):
    path = tmp_path / "spacing-persist.sqlite3"
    payload = _payload()
    with TestClient(create_app(path, seed=False)) as first:
        run_id = first.post("/api/runs", json={"sample": "shift"}).json()["manifest"]["id"]
        created = first.post(f"/api/runs/{run_id}/spacing/reviews", json=payload)
        assert created.status_code == 201
        review_id = created.json()["review_id"]
    with TestClient(create_app(path, seed=False)) as second:
        history = second.get(f"/api/runs/{run_id}/spacing/reviews").json()
        assert len(history) == 1 and history[0]["id"] == review_id
        # 重开后同一复尺单仍走幂等，不新增版本
        again = second.post(f"/api/runs/{run_id}/spacing/reviews", json=payload)
        assert again.status_code == 200 and again.json()["reused"] is True
        assert len(second.get(f"/api/runs/{run_id}/spacing/reviews").json()) == 1
        effective = second.get(f"/api/runs/{run_id}/spacing/effective").json()
        assert effective["review_id"] == review_id


def test_verify_recomputes_revised_evidence_and_detects_tampering(client, base):
    created = client.post(base + "/spacing/reviews", json=_payload()).json()
    review_id = created["review_id"]
    ok = client.post(base + f"/spacing/reviews/{review_id}/verify")
    assert ok.status_code == 200 and ok.json()["verified"] is True
    assert ok.json()["result_sha256"] == created["report"]["revised_result_sha256"]

    # 篡改复核尺寸后，复算摘要不符，拒绝通过
    store = client.app.state.spacing_store
    with store.connect() as db:
        db.execute(
            "UPDATE spacing_reviews SET report = json_set(report,"
            " '$.revised_spec.cabinet_hours[0].upper_mm', 9999) WHERE id = ?",
            (review_id,))
    tampered = client.post(base + f"/spacing/reviews/{review_id}/verify")
    assert tampered.status_code == 409 and tampered.json()["code"] == "REVIEW_DRIFT"
    # 原归卷仍可独立复算，未被复核污染
    assert client.post(base + "/rerun").json()["verified"] is True


def test_rejected_review_cannot_be_verified(client, base):
    reply = client.post(base + "/spacing/reviews", json={
        "measure_no": "RM-REJECT-VERIFY", "cabinets": [
            {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": None, "evidence": "RC", "measurer": "周"}]})
    review_id = reply.json()["review_id"]
    verify_reply = client.post(base + f"/spacing/reviews/{review_id}/verify")
    assert verify_reply.status_code == 409 and verify_reply.json()["code"] == "REVIEW_NOT_PUBLISHED"
