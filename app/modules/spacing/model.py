from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.modules.intake.contract import IDENT


class RemeasuredCabinet(BaseModel):
    """闭馆后复尺登记的单个展柜。

    两层高度允许缺一层：只有单层尺寸的登记不会被接口直接丢弃，而是在证据核验阶段
    判为 SINGLE_LAYER，不得进入正式结论。
    """

    cabinet: IDENT
    upper_mm: Optional[int] = Field(default=None, ge=150, le=5000)
    lower_mm: Optional[int] = Field(default=None, ge=0, le=4500)
    # 证据编号与测量人允许传空：空值在证据核验阶段判为 INCOMPLETE，不进入正式结论。
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    measurer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] = ""

    @model_validator(mode="after")
    def valid_separation(self):
        if self.upper_mm is not None and self.lower_mm is not None:
            if self.upper_mm - self.lower_mm < 100:
                raise ValueError("复尺上下探头须相隔至少 100 mm")
        return self


class RemeasureRequest(BaseModel):
    """一次复尺登记。每个展柜须带证据与测量人，单层或证据不全者整柜剔除。"""

    measure_no: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    cabinets: list[RemeasuredCabinet] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_cabinets(self):
        ids = [item.cabinet for item in self.cabinets]
        if len(set(ids)) != len(ids):
            raise ValueError("同一复尺单内展柜重复登记")
        return self


# 复尺证据核验结果
EVIDENCE_COMPLETE = "COMPLETE"          # 两层尺寸齐全且证据完整
EVIDENCE_INCOMPLETE = "INCOMPLETE"      # 证据不完整（缺证据编号或测量人）
SINGLE_LAYER = "SINGLE_LAYER"           # 只有单层尺寸
UNKNOWN_CABINET = "UNKNOWN_CABINET"     # 复尺柜未在本归卷建档
UNCHANGED = "UNCHANGED"                 # 复尺间距与建档值相同，不产生新版
