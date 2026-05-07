"""
龙虎榜分析相关数据模型

定义龙虎榜分析功能的请求/响应模型和 MongoDB 文档模型。
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from bson import ObjectId
from .user import PyObjectId
from app.utils.timezone import now_tz


# ============================================================
# 请求/响应模型
# ============================================================


class LonghubangAnalyzeRequest(BaseModel):
    """龙虎榜分析请求"""
    mode: str = Field(default="date", description="分析模式: date(指定日期) / days(最近N天)")
    date: Optional[str] = Field(default=None, description="指定日期 YYYY-MM-DD（mode=date 时使用）")
    days: int = Field(default=1, ge=1, le=10, description="最近天数（mode=days 时使用）")
    model_name: Optional[str] = Field(default=None, description="用户指定的 LLM 模型名称（留空则自动选择）")


class LonghubangRecord(BaseModel):
    """龙虎榜记录"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票名称")
    youzi_name: str = Field(..., description="游资名称")
    yingye_bu: str = Field(..., description="营业部名称")
    buy_amount: float = Field(..., description="买入金额(元)")
    sell_amount: float = Field(..., description="卖出金额(元)")
    net_inflow: float = Field(..., description="净流入金额(元)")
    concepts: Optional[str] = Field(default=None, description="相关概念")


class ScoringResult(BaseModel):
    """评分结果"""
    rank: int = Field(..., description="排名")
    stock_name: str = Field(..., description="股票名称")
    stock_code: str = Field(..., description="股票代码")
    total_score: float = Field(..., description="综合评分(0-100)")
    capital_quality: float = Field(..., description="买入资金含金量(0-30)")
    net_inflow_score: float = Field(..., description="净买入额评分(0-25)")
    sell_pressure: float = Field(..., description="卖出压力评分(0-20)")
    institution_score: float = Field(..., description="机构共振评分(0-15)")
    bonus: float = Field(..., description="其他加分项(0-10)")
    top_youzi_count: int = Field(..., description="顶级游资数量")
    buy_seats: int = Field(..., description="买方席位数")
    has_institution: bool = Field(..., description="是否有机构参与")
    net_inflow: float = Field(..., description="净流入金额(元)")


class LonghubangAnalyzeResponse(BaseModel):
    """龙虎榜分析响应"""
    task_id: str = Field(..., description="分析任务 ID")
    data_info: dict = Field(default_factory=dict, description="数据概况信息")
    scoring_ranking: List[ScoringResult] = Field(default_factory=list, description="评分排名列表")
    message: str = Field(default="", description="响应消息")


class LonghubangRecommendedStock(BaseModel):
    """龙虎榜推荐股票"""
    rank: int = Field(..., description="推荐排名")
    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    net_inflow: float = Field(..., description="净流入金额(元)")
    reason: str = Field(default="", description="推荐理由")
    confidence: str = Field(default="", description="确定性: 高/中/低")
    hold_period: str = Field(default="", description="持有周期: 短线/中线/长线")


class LonghubangStatistics(BaseModel):
    """龙虎榜数据库统计信息"""
    total_records: int = Field(default=0, description="总记录数")
    total_stocks: int = Field(default=0, description="涉及股票数")
    total_youzi: int = Field(default=0, description="涉及游资数")
    total_reports: int = Field(default=0, description="分析报告数")
    date_range: Dict[str, Optional[str]] = Field(
        default_factory=lambda: {"start": None, "end": None},
        description="数据日期范围"
    )


# ============================================================
# MongoDB 文档模型
# ============================================================


class AgentAnalysis(BaseModel):
    """单个分析师的分析结果"""
    agent_name: str = Field(..., description="分析师名称")
    analysis: str = Field(default="", description="分析内容")


class LonghubangAnalysis(BaseModel):
    """龙虎榜分析报告 - 对应 longhubang_analysis 集合"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    analysis_date: datetime = Field(default_factory=now_tz, description="分析日期")
    data_date_range: str = Field(default="", description="数据日期范围，如 '2024-01-15 至 2024-01-19'")
    data_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="数据概况: total_records, total_stocks, total_youzi 等"
    )
    scoring_ranking: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="评分排名列表"
    )
    agents_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="各分析师报告: youzi, stock, theme, risk, chief"
    )
    recommended_stocks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="推荐股票列表"
    )
    summary: str = Field(default="", description="分析摘要")
    task_id: Optional[str] = Field(default=None, description="关联的任务 ID")
    created_at: datetime = Field(default_factory=now_tz, description="创建时间")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True
    )

    @field_serializer('analysis_date', 'created_at')
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """序列化 datetime 为 ISO 8601 格式，保留时区信息"""
        if dt:
            return dt.isoformat()
        return None
