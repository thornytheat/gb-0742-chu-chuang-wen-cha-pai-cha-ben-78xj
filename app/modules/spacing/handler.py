from fastapi import APIRouter, Request, Response

from app.dependencies import Archive
from app.modules.spacing.model import RemeasureRequest
from app.modules.spacing.service import SpacingService

router = APIRouter(prefix="/api/runs/{run_id}/spacing")


def spacing_service(request: Request) -> SpacingService:
    return request.app.state.spacing


@router.get("/reviews")
def review_history(run_id: str, request: Request):
    """列出该归卷下全部复核版本（含失败留痕），旧版不被抹掉。"""
    return spacing_service(request).history(run_id)


@router.get("/effective")
def effective_dimensions(archive: Archive, run_id: str, request: Request):
    """返回当前生效的尺寸版及其来源（原始建档或最新正式复核结论）。"""
    return spacing_service(request).effective(run_id)


@router.post("/reviews", status_code=201)
def submit_review(run_id: str, body: RemeasureRequest, request: Request, response: Response):
    """提交闭馆后复尺登记，生成间距复核结论。

    证据不完整或只有单层尺寸的展柜不能进入正式结论，失败保留旧版并留痕；
    同一复尺单重跑不新增版本。
    """
    result = spacing_service(request).review(run_id, body)
    if result["reused"]:
        response.status_code = 200
    elif result["status"] == "REJECTED":
        response.status_code = 422
    return {"review_id": result["review_id"], "status": result["status"],
            "reused": result["reused"], "report": result["report"]}


@router.get("/reviews/{review_id}")
def fetch_review(review_id: str, request: Request):
    return spacing_service(request).spacing_store.fetch(review_id)


@router.post("/reviews/{review_id}/verify")
def verify_review(review_id: str, request: Request):
    """按固定原档复算正式复核结论，验证结果摘要，不新增版本。"""
    return spacing_service(request).verify(review_id)


@router.get("/reviews/{review_id}/export")
def export_review(review_id: str, request: Request):
    payload = spacing_service(request).export_review(review_id)
    return Response(payload, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="spacing-{review_id}.json"'})
