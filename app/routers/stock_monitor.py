"""
实时监测 API 路由

提供实时监测相关的 REST API 端点：
- 创建/批量同步监测配置
- 列出监测配置
- 更新监测配置
- 删除监测配置
- 通知历史查询（支持按股票代码和通知类型过滤）
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.response import ok, fail
from app.routers.auth_db import get_current_user
from app.services.stock_monitor_service import StockMonitorService

router = APIRouter(prefix="/api/monitor", tags=["实时监测"])
logger = logging.getLogger("webapi")

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class EntryRangeModel(BaseModel):
    """进场区间"""
    min: float = Field(default=0, description="进场区间下限")
    max: float = Field(default=0, description="进场区间上限")


class CreateConfigRequest(BaseModel):
    """创建/同步监测配置请求"""
    symbol: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")
    market: str = Field(default="CN", description="市场类型 (CN/HK/US)")
    rating: str = Field(default="", description="评级")
    entry_range: Optional[EntryRangeModel] = Field(default=None, description="进场区间")
    take_profit: float = Field(default=0, description="止盈价")
    stop_loss: float = Field(default=0, description="止损价")
    current_price: float = Field(default=0, description="当前价格")
    check_interval: int = Field(default=60, ge=10, le=3600, description="检查间隔（秒）")
    notification_enabled: bool = Field(default=True, description="是否启用通知")
    trading_hours_only: bool = Field(default=True, description="是否仅交易时段监测")
    enabled: bool = Field(default=True, description="是否启用")
    source: str = Field(default="manual", description="来源 (manual/analysis_sync)")


class UpdateConfigRequest(BaseModel):
    """更新监测配置请求"""
    name: Optional[str] = Field(default=None, description="股票名称")
    rating: Optional[str] = Field(default=None, description="评级")
    entry_range: Optional[EntryRangeModel] = Field(default=None, description="进场区间")
    take_profit: Optional[float] = Field(default=None, description="止盈价")
    stop_loss: Optional[float] = Field(default=None, description="止损价")
    check_interval: Optional[int] = Field(default=None, ge=10, le=3600, description="检查间隔（秒）")
    notification_enabled: Optional[bool] = Field(default=None, description="是否启用通知")
    trading_hours_only: Optional[bool] = Field(default=None, description="是否仅交易时段监测")
    enabled: Optional[bool] = Field(default=None, description="是否启用")


# ---------------------------------------------------------------------------
# 服务实例获取
# ---------------------------------------------------------------------------


def _get_service() -> StockMonitorService:
    """获取 StockMonitorService 实例"""
    return StockMonitorService()


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/stocks", response_model=Dict[str, Any])
async def create_config(
    body: CreateConfigRequest,
    user: dict = Depends(get_current_user),
):
    """
    创建或同步监测配置（upsert on user_id + symbol）。

    用于手动创建和分析结果同步。
    """
    try:
        if not body.symbol or not body.symbol.strip():
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        config_data: Dict[str, Any] = {
            "symbol": body.symbol.strip(),
            "name": body.name,
            "market": body.market,
            "rating": body.rating,
            "take_profit": body.take_profit,
            "stop_loss": body.stop_loss,
            "current_price": body.current_price,
            "check_interval": body.check_interval,
            "notification_enabled": body.notification_enabled,
            "trading_hours_only": body.trading_hours_only,
            "enabled": body.enabled,
            "source": body.source,
        }

        if body.entry_range:
            config_data["entry_range"] = {
                "min": body.entry_range.min,
                "max": body.entry_range.max,
            }

        service = _get_service()
        result = await service.create_or_update_config(
            user_id=user["id"],
            config=config_data,
        )

        if result.get("success"):
            return ok(result.get("config"), message="监测配置保存成功")
        else:
            error_msg = result.get("error", "保存失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[实时监测路由] 创建监测配置异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建监测配置异常: {e}")


@router.get("/stocks", response_model=Dict[str, Any])
async def list_configs(
    user: dict = Depends(get_current_user),
):
    """
    列出当前用户的所有监测配置。
    """
    try:
        service = _get_service()
        configs = await service.list_configs(user_id=user["id"])

        return ok({"items": configs, "total": len(configs)}, message="查询成功")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[实时监测路由] 查询监测配置异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询监测配置异常: {e}")


@router.put("/stocks/{config_id}", response_model=Dict[str, Any])
async def update_config(
    config_id: str,
    body: UpdateConfigRequest,
    user: dict = Depends(get_current_user),
):
    """
    更新监测配置。

    - **config_id**: 配置 ID（MongoDB _id 字符串）
    """
    try:
        if not config_id or not config_id.strip():
            raise HTTPException(status_code=400, detail="配置 ID 不能为空")

        # 构建更新字典，仅包含非 None 的字段
        updates: Dict[str, Any] = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.rating is not None:
            updates["rating"] = body.rating
        if body.entry_range is not None:
            updates["entry_range"] = {
                "min": body.entry_range.min,
                "max": body.entry_range.max,
            }
        if body.take_profit is not None:
            updates["take_profit"] = body.take_profit
        if body.stop_loss is not None:
            updates["stop_loss"] = body.stop_loss
        if body.check_interval is not None:
            updates["check_interval"] = body.check_interval
        if body.notification_enabled is not None:
            updates["notification_enabled"] = body.notification_enabled
        if body.trading_hours_only is not None:
            updates["trading_hours_only"] = body.trading_hours_only
        if body.enabled is not None:
            updates["enabled"] = body.enabled

        if not updates:
            raise HTTPException(status_code=400, detail="没有有效的更新字段")

        service = _get_service()
        result = await service.update_config(
            user_id=user["id"],
            config_id=config_id.strip(),
            updates=updates,
        )

        if result.get("success"):
            return ok(result.get("config"), message="监测配置更新成功")
        else:
            error_msg = result.get("error", "更新失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[实时监测路由] 更新监测配置异常: config_id={config_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"更新监测配置异常: {e}")


@router.delete("/stocks/{config_id}", response_model=Dict[str, Any])
async def delete_config(
    config_id: str,
    user: dict = Depends(get_current_user),
):
    """
    删除监测配置。

    - **config_id**: 配置 ID（MongoDB _id 字符串）
    """
    try:
        if not config_id or not config_id.strip():
            raise HTTPException(status_code=400, detail="配置 ID 不能为空")

        service = _get_service()
        deleted = await service.delete_config(
            user_id=user["id"],
            config_id=config_id.strip(),
        )

        if deleted:
            return ok(None, message="监测配置已删除")
        else:
            raise HTTPException(status_code=404, detail="配置不存在或无权限")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[实时监测路由] 删除监测配置异常: config_id={config_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"删除监测配置异常: {e}")


@router.get("/notifications", response_model=Dict[str, Any])
async def get_notifications(
    symbol: Optional[str] = Query(default=None, description="股票代码（可选）"),
    notification_type: Optional[str] = Query(
        default=None,
        description="通知类型（可选，entry/take_profit/stop_loss）",
    ),
    limit: int = Query(default=50, ge=1, le=500, description="返回记录数上限"),
    user: dict = Depends(get_current_user),
):
    """
    查询通知历史。

    支持按股票代码和通知类型过滤。

    - **symbol**: 股票代码（可选，不传则查询所有）
    - **notification_type**: 通知类型（可选，entry/take_profit/stop_loss）
    - **limit**: 返回记录数上限，默认 50，最大 500
    """
    try:
        service = _get_service()
        notifications = await service.get_notifications(
            user_id=user["id"],
            symbol=symbol,
            notification_type=notification_type,
            limit=limit,
        )

        return ok(
            {"items": notifications, "total": len(notifications)},
            message="查询成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[实时监测路由] 查询通知历史异常: error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询通知历史异常: {e}")
