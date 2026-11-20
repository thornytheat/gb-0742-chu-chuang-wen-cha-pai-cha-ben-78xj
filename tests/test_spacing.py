import pytest

from app.manifest import canonical, json_sha
from app.modules.intake.catalog import load_sample
from app.modules.spacing.model import RemeasureRequest
from app.modules.spacing.review import build_revision, validate_request


@pytest.fixture(scope="module")
def package():
    return load_sample("shift")


@pytest.fixture(scope="module")
def baseline(package):
    from app.modules.archive.pipeline import evaluate
    return evaluate(package)


def request(payload):
    return RemeasureRequest.model_validate(payload)


def test_complete_two_layer_evidence_is_accepted(package, baseline):
    body = request({"measure_no": "RM-1", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": "周保管"}]})
    conclusion, checks = build_revision(package, body, baseline)
    assert [item["status"] for item in checks] == ["COMPLETE"]
    assert conclusion is not None
    assert conclusion["cabinets"][0]["before_gap_mm"] == 1000
    assert conclusion["cabinets"][0]["after_gap_mm"] == 1250


def test_single_layer_cabinet_cannot_enter_conclusion(package, baseline):
    body = request({"measure_no": "RM-SINGLE", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": None, "evidence": "RC-17", "measurer": "周保管"}]})
    conclusion, checks = build_revision(package, body, baseline)
    assert conclusion is None
    assert checks[0]["status"] == "SINGLE_LAYER"


def test_missing_evidence_or_measurer_is_incomplete(package, baseline):
    for payload in (
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "", "measurer": "周保管"},
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": ""},
    ):
        body = request({"measure_no": "RM-INCOMPLETE", "cabinets": [payload]})
        conclusion, checks = build_revision(package, body, baseline)
        assert conclusion is None and checks[0]["status"] == "INCOMPLETE"


def test_unknown_cabinet_is_rejected_and_known_one_still_evaluates(package, baseline):
    body = request({"measure_no": "RM-MIX", "cabinets": [
        {"cabinet": "CV-99", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-X", "measurer": "周"},
        {"cabinet": "CV-17", "upper_mm": 1350, "lower_mm": 550, "evidence": "RC-17", "measurer": "周"}]})
    accepted, checks = validate_request(body, package.spec)
    assert {item["cabinet"]: item["status"] for item in checks} == {
        "CV-99": "UNKNOWN_CABINET", "CV-17": "COMPLETE"}
    conclusion, _ = build_revision(package, body, baseline)
    assert [item["cabinet"] for item in conclusion["cabinets"]] == ["CV-17"]


def test_batch_with_no_valid_cabinet_returns_no_conclusion(package, baseline):
    body = request({"measure_no": "RM-ALLBAD", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": None, "lower_mm": 550, "evidence": "RC", "measurer": "周"}]})
    assert build_revision(package, body, baseline) == (None, build_revision(package, body, baseline)[1])


def test_unchanged_gap_does_not_create_version(package, baseline):
    body = request({"measure_no": "RM-SAME", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1550, "lower_mm": 550, "evidence": "RC", "measurer": "周"}]})
    conclusion, checks = build_revision(package, body, baseline)
    assert conclusion is None and checks[0]["status"] == "COMPLETE"


def test_grade_changes_list_only_changed_hours_with_old_and_new_values(package, baseline):
    body = request({"measure_no": "RM-2", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": "周保管"}]})
    conclusion, _ = build_revision(package, body, baseline)
    changed = conclusion["grade_changes"]
    assert len(changed) == 1
    slot = changed[0]
    assert (slot["cabinet"], slot["before_state"], slot["after_state"]) == (
        "CV-17", "MISSING", "STABLE")
    assert (slot["before_gap_mm"], slot["after_gap_mm"]) == (1000, 1250)
    # 原读数行号继续保留在差异中
    assert slot["upper"] and slot["lower"]
    # 未变化的柜时不在差异清单
    assert slot["hour"] == "2026-11-20T03:00:00Z"


def test_episode_and_ticket_reconciliation_follow_the_same_dimensions(package, baseline):
    body = request({"measure_no": "RM-3", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1350, "lower_mm": 550, "evidence": "RC-17", "measurer": "吴保管"}]})
    conclusion, _ = build_revision(package, body, baseline)
    # 按柜号＋起止时刻定位：原 01–03 持续段与 04–05 瞬时段消失，合并为 01–05 的新持续段
    removed_spans = {(item["cabinet"], item["start"], item["end"]) for item in conclusion["episodes"]["removed"]}
    assert ("CV-17", "2026-11-20T01:00:00Z", "2026-11-20T03:00:00Z") in removed_spans
    assert ("CV-17", "2026-11-20T04:00:00Z", "2026-11-20T05:00:00Z") in removed_spans
    created = conclusion["episodes"]["created"]
    assert len(created) == 1 and created[0]["hours"] == 4 and created[0]["kind"] == "SUSTAINED"
    ticket_codes = {item["ticket"]: (item["before_code"], item["after_code"])
                    for item in conclusion["tickets"]["changed"]}
    assert ticket_codes["AL-0703"] == ("MISSING_EVIDENCE", "DUPLICATE_ALARM")
    assert ticket_codes["AL-0704"] == ("TRANSIENT_ONLY", "DUPLICATE_ALARM")
    assert conclusion["tickets"]["unreported_cleared"] == ["CV-17-A03"]
    assert conclusion["tickets"]["unreported_added"] == ["CV-17-A02"]


def test_revised_pipeline_recomputes_conservation_tally(package, baseline):
    body = request({"measure_no": "RM-4", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": "周保管"}]})
    conclusion, _ = build_revision(package, body, baseline)
    before, after = conclusion["hours_before"], conclusion["hours_after"]
    assert (before["stable"], before["missing"]) == (11, 4)
    assert (after["stable"], after["missing"]) == (12, 3)
    assert after["balanced"] and after["expected"] == before["expected"]


def test_baseline_evidence_and_raw_readings_are_preserved(package, baseline):
    body = request({"measure_no": "RM-5", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": "周保管"}]})
    conclusion, _ = build_revision(package, body, baseline)
    # 基线结果摘要固定，新摘要不同；复核证据里保留完整重算结果与原始行数
    assert conclusion["baseline_result_sha256"] == json_sha(baseline)
    assert conclusion["revised_result_sha256"] != conclusion["baseline_result_sha256"]
    assert len(conclusion["revised_evidence"]["rows"]) == len(baseline["rows"])
    # 契约里只有尺寸变化，阈值等其他输入不变
    revised = conclusion["revised_spec"]
    original = package.spec.model_dump(mode="json")
    assert revised["tolerance_seconds"] == original["tolerance_seconds"]
    assert revised["deployments"] == original["deployments"]
    assert revised["csv"] == original["csv"]


def test_request_fingerprint_is_stable_for_identical_rerun(package, baseline):
    payload = {"measure_no": "RM-6", "cabinets": [
        {"cabinet": "CV-17", "upper_mm": 1800, "lower_mm": 550, "evidence": "RC-17", "measurer": "周保管"}]}
    first = request(payload)
    second = request(payload)
    assert json_sha(canonical(first.model_dump(mode="json"))) == json_sha(
        canonical(second.model_dump(mode="json")))


def test_separation_below_100mm_is_rejected_by_contract():
    with pytest.raises(ValueError):
        request({"measure_no": "RM-7", "cabinets": [
            {"cabinet": "CV-17", "upper_mm": 600, "lower_mm": 550, "evidence": "RC", "measurer": "周"}]})
