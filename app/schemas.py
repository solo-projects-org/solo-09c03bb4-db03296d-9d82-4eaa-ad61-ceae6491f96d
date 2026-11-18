"""Pydantic 响应模型（Pydantic v2）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

AnalysisStatus = Literal["PENDING", "COMPLETED", "FAILED", "SUPERSEDED"]
RevisionStatus = Literal["RECEIVED", "CURRENT", "SUPERSEDED"]
OutcomeCode = Literal["READMISSION", "DEATH", "CENSORED"]


class UploadResponse(BaseModel):
    revision: int = Field(..., description="修订版本号（递增整数）")
    accepted: bool = Field(..., description="本次请求是否受理了新版本")
    idempotent: bool = Field(..., description="是否为同版同内容的重复送达（未产生任何改动）")
    content_sha256: str = Field(..., description="CSV 原始字节的 SHA-256")
    n_records: int = Field(..., description="记录人数")
    revision_status: RevisionStatus
    analysis_status: AnalysisStatus
    current_revision: Optional[int] = Field(None, description="当前现行修订版本号")
    message: str


class RevisionOut(BaseModel):
    revision: int
    content_sha256: str
    status: RevisionStatus
    n_records: int
    uploaded_at: str
    superseded_at: Optional[str] = None
    analysis_status: AnalysisStatus
    analysis_error: Optional[str] = None


class DayRow(BaseModel):
    day: int = Field(..., description="随访日（1~30）")
    at_risk: int = Field(..., description="日初在险人数（同日两类事件共用此人数）")
    readmission_count: int = Field(..., description="当日再入院事件数")
    death_count: int = Field(..., description="当日死亡事件数（竞争结局）")
    censored_count: int = Field(..., description="当日随访截止（删失）人数，事件后退出")
    readmission_cif: float = Field(..., description="截至当日（含当日）再入院累计发生概率")
    death_cif: float = Field(..., description="截至当日（含当日）死亡累计发生概率")
    survival: float = Field(..., description="当日结束时仍在险的生存概率")


class AnalysisSummary(BaseModel):
    n_subjects: int
    n_readmission: int
    n_death: int
    n_censored: int
    cumulative_readmission: float = Field(..., description="30 天再入院累计发生概率（Aalen-Johansen CIF）")
    cumulative_death: float
    last_activity_day: int


class AnalysisResponse(BaseModel):
    revision: int
    is_current: bool
    analysis_status: AnalysisStatus
    error: Optional[str] = None
    summary: Optional[AnalysisSummary] = None
    daily: list[DayRow] = Field(default_factory=list)


class RecomputeResponse(BaseModel):
    revision: int
    triggered: bool
    already_running: bool
    analysis_status: AnalysisStatus
    message: str


class ErrorDetail(BaseModel):
    line: Optional[int] = None
    message: str


class ErrorResponse(BaseModel):
    error: str
    details: list[ErrorDetail] = Field(default_factory=list)
    current_revision: Optional[int] = None
