from datetime import datetime, timezone

from app.errors import FieldError
from app.manifest import canonical, json_sha, sha
from app.modules.archive.proof import verify
from app.modules.spacing.model import EVIDENCE_COMPLETE, SINGLE_LAYER, UNKNOWN_CABINET
from app.modules.spacing.review import build_revision, verify_revision


class SpacingService:
    """间距复核服务。

    复核基于归卷固定的原始图档与契约，复尺只改尺寸不改读数。失败保留旧版并留痕，
    成功生成新版本；同一复尺单重跑直接返回既有记录，不新增版本。
    """

    def __init__(self, archive_store, spacing_store):
        self.archive_store = archive_store
        self.spacing_store = spacing_store

    def _baseline(self, run_id):
        archive = self.archive_store.fetch(run_id)
        package = verify(archive)  # 校验原档、契约与结果摘要，防止带病复核
        manifest, evidence, _source = archive
        return package, manifest, evidence

    def review(self, run_id, request):
        package, manifest, baseline_evidence = self._baseline(run_id)
        fingerprint = json_sha(canonical(request.model_dump(mode="json")))

        # 同一复尺单重跑：直接取回既有结论，不新增版本。
        for existing in self.spacing_store.history(run_id):
            if existing["fingerprint"] == fingerprint:
                return {"report": existing["report"], "reused": True,
                        "status": existing["status"], "review_id": existing["id"]}

        conclusion, checks = build_revision(package, request, baseline_evidence)
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if conclusion is None:
            # 证据不完整、只有单层、柜号未知或全部无变化：生成失败留痕，旧版保持不动。
            reasons = sorted({item["status"] for item in checks
                              if item["status"] != EVIDENCE_COMPLETE})
            report = {
                "measure_no": request.measure_no,
                "status": "REJECTED",
                "rejected_reasons": reasons,
                "evidence_checks": checks,
                "message": self._reject_message(reasons),
                "baseline_result_sha256": manifest["result_sha256"],
                "note": "复核未通过，保留旧版尺寸与原读数，旧证未改动。",
            }
            review_id = self._identity(run_id, fingerprint, "REJECTED")
            record = {"id": review_id, "run_id": run_id, "measure_no": request.measure_no,
                      "status": "REJECTED", "fingerprint": fingerprint,
                      "baseline_id": self._current_baseline(run_id), "report": report,
                      "created": created}
            saved, _inserted = self.spacing_store.insert(record)
            return {"report": saved, "reused": False, "status": "REJECTED", "review_id": review_id}

        # 正式结论：记录基线（当前生效版本）与新版尺寸，旧版与原读数继续留存。
        baseline_id = self._current_baseline(run_id)
        conclusion["status"] = "PUBLISHED"
        conclusion["baseline_run_id"] = run_id
        conclusion["baseline_review_id"] = baseline_id
        conclusion["note"] = "判级、工单与归卷自本版起统一使用复核尺寸；原始读数与旧尺寸留存可溯。"
        review_id = self._identity(run_id, fingerprint, "PUBLISHED")
        record = {"id": review_id, "run_id": run_id, "measure_no": request.measure_no,
                  "status": "PUBLISHED", "fingerprint": fingerprint,
                  "baseline_id": baseline_id, "report": conclusion, "created": created}
        saved, _inserted = self.spacing_store.insert(record)
        return {"report": saved, "reused": False, "status": "PUBLISHED", "review_id": review_id}

    def history(self, run_id):
        # 确认归卷存在，未知归卷直接 404。
        self.archive_store.fetch(run_id)
        return self.spacing_store.history(run_id)

    def effective(self, run_id):
        """返回当前生效的尺寸版：有正式复核结论用最新一版，否则用原始建档值。"""
        package, manifest, evidence = self._baseline(run_id)
        latest = self.spacing_store.latest_published(run_id)
        if latest is None:
            return {"run_id": run_id, "source": "ARCHIVE", "review_id": None,
                    "cabinets": manifest["cabinets"], "result_sha256": manifest["result_sha256"]}
        report = latest["report"]
        return {"run_id": run_id, "source": "SPACING_REVIEW", "review_id": latest["id"],
                "measure_no": report["measure_no"],
                "cabinets": report["revised_spec"]["cabinet_hours"],
                "result_sha256": report["revised_result_sha256"]}

    def export_review(self, review_id):
        record = self.spacing_store.fetch(review_id)
        return canonical({"format": "museum-spacing-review-v1", **record}).encode("utf-8")

    def verify(self, review_id):
        """按固定原档复算正式复核结论，确认判级、工单、归卷仍与该版尺寸一致。"""
        record = self.spacing_store.fetch(review_id)
        if record["status"] != "PUBLISHED":
            raise FieldError("REVIEW_NOT_PUBLISHED", "失败登记无可复算的正式结论", 409)
        archive = self.archive_store.fetch(record["run_id"])
        verify_revision(archive, record["report"])
        return {"review_id": review_id, "verified": True,
                "result_sha256": record["report"]["revised_result_sha256"]}

    def _current_baseline(self, run_id):
        latest = self.spacing_store.latest_published(run_id)
        return latest["id"] if latest else None

    @staticmethod
    def _identity(run_id, fingerprint, status):
        return "SR-" + sha(canonical([run_id, fingerprint, status]).encode("utf-8"))[:24]

    @staticmethod
    def _reject_message(reasons):
        phrases = {
            SINGLE_LAYER: "存在只有单层尺寸的展柜",
            "INCOMPLETE": "存在复尺证据不完整的展柜",
            UNKNOWN_CABINET: "存在未在本归卷建档的展柜",
        }
        detail = "、".join(phrases.get(code, code) for code in reasons) or "无有效复尺尺寸"
        return f"复核未通过：{detail}，不能进入正式结论，保留旧版。"
