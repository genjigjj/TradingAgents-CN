"""
AI 盯盘 API 路由

提供 AI 盯盘相关的 REST API 端点：
- 创建盯盘任务
- 列出盯盘任务
- 更新盯盘任务
- 删除盯盘任务
- AI 决策历史查询
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.response import ok, fail
from app.routers.auth_db import get_current_user
from app.services.smart_monitor_service import SmartMonitorService

router = APIRouter(prefix="/api/monitor/smart", tags=["AI盯盘"])
logger = logging.getLogger("webapi")

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """创建盯盘任务请求"""
    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    check_interval: int = Field(default=300, ge=10, le=3600, description="检查间隔（秒）")
    auto_notify: bool = Field(default=True, description="是否自动通知")
    trading_hours_only: bool = Field(default=True, description="是否仅交易时段检查")


class UpdateTaskRequest(BaseModel):
    """更新盯盘任务请求"""
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    check_interval: Optional[int] = Field(default=None, ge=10, le=3600, description="检查间隔（秒）")
    auto_notify: Optional[bool] = Field(default=None, description="是否自动通知")
    trading_hours_only: Optional[bool] = Field(default=None, description="是否仅交易时段检查")
    stock_name: Optional[str] = Field(default=None, description="股票名称")


# ---------------------------------------------------------------------------
# 服务实例获取
# ---------------------------------------------------------------------------


def _get_service() -> SmartMonitorService:
    """获取 SmartMonitorService 实例"""
    return SmartMonitorService()


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=Dict[str, Any])
async def create_task(
    body: CreateTaskRequest,
    user: dict = Depends(get_current_user),
):
    """
    创建盯盘任务。

    自动从 paper_positions 读取持仓成本和数量填充到任务配置。
    在 (user_id, stock_code) 上保持唯一。
    """
    try:
        if not body.stock_code or not body.stock_code.strip():
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        service = _get_service()
        result = await service.create_task(
            user_id=user["id"],
            stock_code=body.stock_code.strip(),
            stock_name=body.stock_name,
            check_interval=body.check_interval,
            auto_notify=body.auto_notify,
            trading_hours_only=body.trading_hours_only,
        )

        if result.get("success"):
            return ok(result.get("task"), message="盯盘任务创建成功")
        else:
            error_msg = result.get("error", "创建失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI盯盘路由] 创建盯盘任务异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建盯盘任务异常: {e}")


@router.get("/tasks", response_model=Dict[str, Any])
async def list_tasks(
    user: dict = Depends(get_current_user),
):
    """
    列出当前用户的所有盯盘任务。
    """
    try:
        service = _get_service()
        tasks = await service.list_tasks(user_id=user["id"])

        return ok({"items": tasks, "total": len(tasks)}, message="查询成功")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI盯盘路由] 查询盯盘任务异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询盯盘任务异常: {e}")


@router.put("/tasks/{task_id}", response_model=Dict[str, Any])
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    user: dict = Depends(get_current_user),
):
    """
    更新盯盘任务配置（启停、间隔等）。

    - **task_id**: 任务 ID（MongoDB _id 字符串）
    """
    try:
        if not task_id or not task_id.strip():
            raise HTTPException(status_code=400, detail="任务 ID 不能为空")

        # 构建更新字典，仅包含非 None 的字段
        updates = {}
        if body.enabled is not None:
            updates["enabled"] = body.enabled
        if body.check_interval is not None:
            updates["check_interval"] = body.check_interval
        if body.auto_notify is not None:
            updates["auto_notify"] = body.auto_notify
        if body.trading_hours_only is not None:
            updates["trading_hours_only"] = body.trading_hours_only
        if body.stock_name is not None:
            updates["stock_name"] = body.stock_name

        if not updates:
            raise HTTPException(status_code=400, detail="没有有效的更新字段")

        service = _get_service()
        result = await service.update_task(
            user_id=user["id"],
            task_id=task_id.strip(),
            updates=updates,
        )

        if result.get("success"):
            return ok(result.get("task"), message="盯盘任务更新成功")
        else:
            error_msg = result.get("error", "更新失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[AI盯盘路由] 更新盯盘任务异常: task_id={task_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"更新盯盘任务异常: {e}")


@router.delete("/tasks/{task_id}", response_model=Dict[str, Any])
async def delete_task(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """
    删除盯盘任务。

    - **task_id**: 任务 ID（MongoDB _id 字符串）
    """
    try:
        if not task_id or not task_id.strip():
            raise HTTPException(status_code=400, detail="任务 ID 不能为空")

        service = _get_service()
        deleted = await service.delete_task(
            user_id=user["id"],
            task_id=task_id.strip(),
        )

        if deleted:
            return ok(None, message="盯盘任务已删除")
        else:
            raise HTTPException(status_code=404, detail="任务不存在或无权限")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[AI盯盘路由] 删除盯盘任务异常: task_id={task_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"删除盯盘任务异常: {e}")


@router.get("/decisions", response_model=Dict[str, Any])
async def get_decisions(
    stock_code: Optional[str] = Query(default=None, description="股票代码（可选）"),
    start_time: Optional[str] = Query(default=None, description="开始时间 ISO 格式（可选）"),
    end_time: Optional[str] = Query(default=None, description="结束时间 ISO 格式（可选）"),
    limit: int = Query(default=50, ge=1, le=500, description="返回记录数上限"),
    user: dict = Depends(get_current_user),
):
    """
    查询 AI 决策历史。

    支持按股票代码和时间范围过滤。

    - **stock_code**: 股票代码（可选，不传则查询所有）
    - **start_time**: 开始时间 ISO 格式（可选）
    - **end_time**: 结束时间 ISO 格式（可选）
    - **limit**: 返回记录数上限，默认 50，最大 500
    """
    try:
        service = _get_service()
        decisions = await service.get_decisions(
            user_id=user["id"],
            stock_code=stock_code,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        return ok(
            {"items": decisions, "total": len(decisions)},
            message="查询成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[AI盯盘路由] 查询决策历史异常: error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询决策历史异常: {e}")
