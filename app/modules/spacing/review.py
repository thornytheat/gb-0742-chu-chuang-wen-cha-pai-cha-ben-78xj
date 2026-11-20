"""间距复核引擎。

以原归卷固定的图档与契约为输入，仅替换复尺确认过的上下探头高度，复算整条
判级—持续异常—工单勾稽链路，并逐柜、逐柜时、逐工单给出新旧差异，保证判级、
工单和归卷使用同一版尺寸。
"""

from dataclasses import dataclass

from app.errors import FieldError
from app.manifest import canonical, json_sha
from app.modules.archive.pipeline import evaluate
from app.modules.archive.proof import verify as verify_archive
from app.modules.audit.reconcile import reconcile_tickets
from app.modules.intake.contract import Package, Shift
from app.modules.spacing.model import (
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    SINGLE_LAYER,
    UNKNOWN_CABINET,
)


@dataclass(frozen=True)
class CabinetMeasure:
    cabinet: str
    upper_mm: int
    lower_mm: int
    evidence: str
    measurer: str
    gap_mm: int


def _slot_index(grades):
    return {(slot["cabinet"], slot["hour"]): slot for slot in grades}


def _grade_changes(before, after):
    """逐柜时比较判级，只返回判级实际变化的柜时。"""
    old, new = _slot_index(before), _slot_index(after)
    changes = []
    for key in sorted(old.keys() | new.keys()):
        first, second = old.get(key), new.get(key)
        if first is None or second is None or first["state"] == second["state"]:
            continue
        changes.append({
            "cabinet": key[0],
            "hour": second["start"],
            "before_state": first["state"],
            "after_state": second["state"],
            "before_gap_mm": first["gap_mm"],
            "after_gap_mm": second["gap_mm"],
            "before_temperature": first["temperature"],
            "after_temperature": second["temperature"],
            "before_humidity": first["humidity"],
            "after_humidity": second["humidity"],
            "upper": second["upper"],
            "lower": second["lower"],
        })
    return changes


def _episode_changes(before, after):
    """比较持续/瞬时异常段：新增、消失、变更（时长、类别或判级指标变化）。

    段号是每柜按出现顺序编排的，判级变化会引起段号漂移，因此按“柜号＋起止时刻”
    定位同一段，再比对时长、类别与指标，避免把段号重排误报成新增/消失。
    """
    def index(items):
        return {(item["cabinet"], item["start"], item["end"]): item for item in items}

    old, new = index(before), index(after)
    created, removed, updated = [], [], []
    for key in sorted(new.keys() - old.keys()):
        created.append(_episode_brief(new[key]))
    for key in sorted(old.keys() - new.keys()):
        removed.append(_episode_brief(old[key]))
    for key in sorted(old.keys() & new.keys()):
        first, second = old[key], new[key]
        if (first["hours"], first["kind"], first["axes"]) != (
                second["hours"], second["kind"], second["axes"]):
            updated.append({"id": second["id"], "cabinet": second["cabinet"],
                            "before": _episode_brief(first), "after": _episode_brief(second)})
    return {"created": created, "removed": removed, "updated": updated}


def _episode_brief(item):
    return {"id": item["id"], "cabinet": item["cabinet"], "start": item["start"], "end": item["end"],
            "hours": item["hours"], "kind": item["kind"], "axes": item["axes"]}


def _ticket_changes(spec, before_grades, before_episodes, after_grades, after_episodes):
    """按同一套工单重新勾稽，列出核对结论变化的工单及未报持续段差异。"""
    old_reviews, old_unreported = reconcile_tickets(spec, before_grades, before_episodes)
    new_reviews, new_unreported = reconcile_tickets(spec, after_grades, after_episodes)
    old, new = ({item["id"]: item for item in group} for group in (old_reviews, new_reviews))
    changed = []
    for ticket_id in sorted(old.keys() | new.keys()):
        first, second = old.get(ticket_id), new.get(ticket_id)
        if first is None or second is None:
            continue
        if (first["code"], first["episode"]) != (second["code"], second["episode"]):
            changed.append({"ticket": ticket_id, "cabinet": second["cabinet"], "at_utc": second["at_utc"],
                            "before_code": first["code"], "after_code": second["code"],
                            "before_episode": first["episode"], "after_episode": second["episode"],
                            "evidence": second["evidence"]})
    return {"changed": changed,
            "before_unreported": old_unreported, "after_unreported": new_unreported,
            "unreported_added": sorted(set(new_unreported) - set(old_unreported)),
            "unreported_cleared": sorted(set(old_unreported) - set(new_unreported))}


def _hours_by_cabinet(grades):
    tally = {}
    for slot in grades:
        bucket = tally.setdefault(slot["cabinet"], {"stable": 0, "abnormal": 0, "missing": 0})
        key = {"STABLE": "stable", "ABNORMAL": "abnormal", "MISSING": "missing"}[slot["state"]]
        bucket[key] += 1
    return tally


def validate_request(request, spec):
    """核验复尺证据。两层高度本就由契约强约束，这里核验柜号、证据与测量人。

    返回 (可进入正式结论的测量列表, 每柜核验结果)。证据不完整或只有单层尺寸的
    展柜被剔除，不得进入正式结论。
    """
    registered = {item.id: item for item in spec.cabinet_hours}
    accepted, checks = [], []
    for item in request.cabinets:
        gap = (item.upper_mm - item.lower_mm
               if item.upper_mm is not None and item.lower_mm is not None else None)
        record = {"cabinet": item.cabinet, "upper_mm": item.upper_mm, "lower_mm": item.lower_mm,
                  "gap_mm": gap, "evidence": item.evidence,
                  "measurer": item.measurer}
        if item.cabinet not in registered:
            record["status"] = UNKNOWN_CABINET
        elif item.upper_mm is None or item.lower_mm is None:
            record["status"] = SINGLE_LAYER
        elif not item.evidence or not item.measurer:
            record["status"] = EVIDENCE_INCOMPLETE
        else:
            record["status"] = EVIDENCE_COMPLETE
            accepted.append(CabinetMeasure(
                item.cabinet, item.upper_mm, item.lower_mm, item.evidence, item.measurer,
                item.upper_mm - item.lower_mm))
        checks.append(record)
    return accepted, checks


def build_revision(package, request, baseline_evidence=None):
    """执行一次复尺复核。

    全部展柜都无法进入正式结论（或无任何间距变化）时返回 None，调用方保留旧版。
    成功时返回完整复核结论，原始读数与旧尺寸均在结论中留存。
    """
    spec = package.spec
    accepted, checks = validate_request(request, spec)
    if not accepted:
        return None, checks

    registered = {item.id: item for item in spec.cabinet_hours}
    # 仅对“证据完整且间距确实变化”的展柜换新尺寸；间距相同的展柜维持建档值。
    updates = {}
    for measure in accepted:
        original = registered[measure.cabinet]
        if measure.gap_mm != original.upper_mm - original.lower_mm:
            updates[measure.cabinet] = measure

    if not updates:
        # 证据完整但没有任何间距变化，不产生新版本。
        return None, checks

    # 以固定原档复制契约，只替换复核通过的展柜尺寸，其余输入原样保留。
    new_cabinets = []
    for cabinet in spec.cabinet_hours:
        if cabinet.id in updates:
            measure = updates[cabinet.id]
            new_cabinets.append(cabinet.model_copy(
                update={"upper_mm": measure.upper_mm, "lower_mm": measure.lower_mm}))
        else:
            new_cabinets.append(cabinet)
    revised_spec = spec.model_copy(update={"cabinet_hours": new_cabinets})
    revised_package = Package(revised_spec, package.blob)

    before = baseline_evidence if baseline_evidence is not None else evaluate(package)
    after = evaluate(revised_package)

    grade_changes = _grade_changes(before["grades"], after["grades"])
    episode_changes = _episode_changes(before["episodes"], after["episodes"])
    ticket_changes = _ticket_changes(spec, before["grades"], before["episodes"],
                                     after["grades"], after["episodes"])

    affected = sorted(updates.keys())
    per_cabinet = []
    for cabinet_id in affected:
        original = registered[cabinet_id]
        measure = updates[cabinet_id]
        slot_changes = [item for item in grade_changes if item["cabinet"] == cabinet_id]
        per_cabinet.append({
            "cabinet": cabinet_id,
            "label": original.label,
            "before_gap_mm": original.upper_mm - original.lower_mm,
            "after_gap_mm": measure.gap_mm,
            "before_dimensions": {"upper_mm": original.upper_mm, "lower_mm": original.lower_mm},
            "after_dimensions": {"upper_mm": measure.upper_mm, "lower_mm": measure.lower_mm},
            "evidence": measure.evidence,
            "measurer": measure.measurer,
            "grade_changes": slot_changes,
            "changed_hours": len(slot_changes),
            "hours_before": _hours_by_cabinet(before["grades"]).get(
                cabinet_id, {"stable": 0, "abnormal": 0, "missing": 0}),
            "hours_after": _hours_by_cabinet(after["grades"]).get(
                cabinet_id, {"stable": 0, "abnormal": 0, "missing": 0}),
        })

    conclusion = {
        "measure_no": request.measure_no,
        "cabinets": per_cabinet,
        "evidence_checks": checks,
        "grade_changes": grade_changes,
        "episodes": episode_changes,
        "tickets": ticket_changes,
        "hours_before": before["audit"]["hours"],
        "hours_after": after["audit"]["hours"],
        "baseline_result_sha256": json_sha(before),
        "revised_result_sha256": json_sha(after),
        "revised_contract_sha256": json_sha(revised_spec.model_dump(mode="json")),
        "revised_evidence": after,
        "revised_spec": revised_spec.model_dump(mode="json"),
    }
    conclusion["request_fingerprint"] = json_sha(canonical(request.model_dump(mode="json")))
    return conclusion, checks


def verify_revision(archive, report):
    """复算正式复核结论，验证新版结果摘要与固定原档一致。

    以归卷固定的原始图档重建复核契约（只改尺寸），重跑整条链路并比对结果
    SHA-256；契约被篡改到无法解析或任一摘要不符即判漂移，杜绝尺寸版与判级、
    工单、归卷脱节。
    """
    package = verify_archive(archive)
    try:
        revised_spec = Shift.model_validate(report["revised_spec"])
        recomputed = evaluate(Package(revised_spec, package.blob))
    except (ValueError, TypeError, KeyError) as exc:
        raise FieldError("REVIEW_DRIFT", "复核契约已损坏，无法复算", 409) from exc
    if json_sha(recomputed) != report["revised_result_sha256"]:
        raise FieldError("REVIEW_DRIFT", "复核复算结果摘要不一致", 409)
    if json_sha(revised_spec.model_dump(mode="json")) != report["revised_contract_sha256"]:
        raise FieldError("REVIEW_DRIFT", "复核契约摘要不一致", 409)
    return True
