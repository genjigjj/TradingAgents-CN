"""
主力选股相关数据模型

定义主力选股功能的请求/响应模型和 MongoDB 文档模型。
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


class MainForceScreenParams(BaseModel):
    """主力选股筛选参数"""
    time_range: str = Field(default="3m", description="时间区间: 3m/6m/1y/custom")
    start_date: Optional[str] = Field(default=None, description="自定义开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="自定义结束日期 YYYY-MM-DD")
    top_n: int = Field(default=5, ge=3, le=10, description="精选推荐数量")
    max_change_pct: float = Field(default=30.0, description="最大涨跌幅限制(%)")
    min_market_cap: float = Field(default=50.0, description="最小市值(亿)")
    max_market_cap: float = Field(default=5000.0, description="最大市值(亿)")


class MainForceCandidate(BaseModel):
    """候选股票"""
    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    industry: str = Field(..., description="所属行业")
    net_inflow: float = Field(..., description="主力资金净流入(元)")
    change_pct: float = Field(..., description="区间涨跌幅(%)")
    market_cap: float = Field(..., description="总市值(亿)")
    pe_ratio: Optional[float] = Field(default=None, description="市盈率")
    pb_ratio: Optional[float] = Field(default=None, description="市净率")


class MainForceScreenResult(BaseModel):
    """筛选结果"""
    total_fetched: int = Field(..., description="获取的原始股票数量")
    total_filtered: int = Field(..., description="筛选后的股票数量")
    candidates: List[MainForceCandidate] = Field(default_factory=list, description="候选股票列表")


class MainForceAnalyzeRequest(BaseModel):
    """主力选股分析请求"""
    params: MainForceScreenParams = Field(default_factory=MainForceScreenParams, description="筛选参数")
    analysis_mode: str = Field(default="overview", description="分析模式: overview/batch")
    batch_count: Optional[int] = Field(default=None, ge=10, le=50, description="批量分析数量(10/20/30/50)")
    research_depth: str = Field(default="标准", description="研究深度")
    model_name: Optional[str] = Field(default=None, description="指定 LLM 模型名称（为空时自动选择）")
    selected_analysts: List[str] = Field(
        default_factory=lambda: ["fund_flow", "industry", "fundamental"],
        description="选择的分析师"
    )


class RecommendedStock(BaseModel):
    """推荐股票"""
    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    reason: str = Field(default="", description="推荐理由")
    highlights: List[str] = Field(default_factory=list, description="投资亮点")
    risks: List[str] = Field(default_factory=list, description="风险提示")
    position: str = Field(default="", description="建议仓位")
    period: str = Field(default="", description="投资周期")


class BatchResultItem(BaseModel):
    """批量分析单项结果"""
    code: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")
    status: str = Field(default="pending", description="分析状态")
    rating: Optional[str] = Field(default=None, description="投资评级")
    confidence: Optional[float] = Field(default=None, description="信心度")
    entry_range: Optional[str] = Field(default=None, description="进场区间")
    take_profit: Optional[str] = Field(default=None, description="止盈位")
    stop_loss: Optional[str] = Field(default=None, description="止损位")
    target_price: Optional[str] = Field(default=None, description="目标价")
    advice: Optional[str] = Field(default=None, description="投资建议")
    error_message: Optional[str] = Field(default=None, description="错误信息")


# ============================================================
# MongoDB 文档模型
# ============================================================


class MainForceBatchHistory(BaseModel):
    """主力选股批量分析历史 - 对应 main_force_batch_history 集合"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    analysis_date: datetime = Field(default_factory=now_tz, description="分析日期")
    params: Dict[str, Any] = Field(default_factory=dict, description="筛选参数")
    candidates_count: int = Field(default=0, description="候选股票数量")
    filtered_count: int = Field(default=0, description="筛选后数量")
    recommended_count: int = Field(default=0, description="推荐股票数量")
    overview_analysis: Dict[str, str] = Field(
        default_factory=dict,
        description="各分析师报告: fund_flow_analyst, industry_analyst, fundamental_analyst, comprehensive_researcher"
    )
    recommended_stocks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="推荐股票列表"
    )
    batch_results: Dict[str, Any] = Field(
        default_factory=dict,
        description="批量分析结果: total, success, failed, duration_seconds, results"
    )
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
