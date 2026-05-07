"""
主力选股 API 路由

提供主力资金选股的筛选、分析、批量历史查询等 REST API 端点。
所有端点使用 JWT 认证保护，API 前缀 /api/main-force。
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Query
from app.routers.auth_db import get_current_user
from app.core.response import ok, fail
from app.models.main_force import (
    MainForceScreenParams,
    MainForceAnalyzeRequest,
)
from app.services.main_force_service import MainForceService
from app.services.config_service import ConfigService

router = APIRouter(tags=["main-force"])
logger = logging.getLogger("webapi")

# 服务实例（注入 ConfigService 以读取 LLM 配置）
_service = MainForceService(config_service=ConfigService())


@router.post("/screen")
async def screen_stocks(
    params: MainForceScreenParams,
    user: dict = Depends(get_current_user),
):
    """
    筛选主力资金股票

    接收筛选参数（时间区间、精选数量、涨跌幅限制、市值范围），
    通过 pywencai 获取主力资金净流入排名数据并进行智能筛选，
    返回筛选后的候选股票列表。
    """
    try:
        logger.info(f"[主力选股] 用户 {user.get('id')} 发起筛选请求: {params}")
        result = await _service.screen_stocks(params)
        return ok(
            data={
                "total_fetched": result.total_fetched,
                "total_filtered": result.total_filtered,
                "candidates": [c.model_dump() for c in result.candidates],
            },
            message="筛选完成",
        )
    except Exception as e:
        logger.error(f"[主力选股] 筛选失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"主力选股筛选失败: {str(e)}")


@router.post("/analyze")
async def analyze(
    request: MainForceAnalyzeRequest,
    user: dict = Depends(get_current_user),
):
    """
    触发完整分析流程

    先执行筛选获取候选股票，然后将整体分析任务提交到任务队列，
    返回任务 ID 供前端轮询进度和获取结果。
    """
    try:
        user_id = user.get("id", "")
        logger.info(f"[主力选股] 用户 {user_id} 发起分析请求: mode={request.analysis_mode}")

        # 1. 先执行筛选
        screen_result = await _service.screen_stocks(request.params)
        candidates = [c.model_dump() for c in screen_result.candidates]

        if not candidates:
            return ok(
                data={"task_id": None, "candidates_count": 0},
                message="未筛选到符合条件的候选股票，请调整筛选参数",
            )

        # 2. 提交整体分析任务到队列
        analysis_params = {
            "top_n": request.params.top_n,
            "analysis_mode": request.analysis_mode,
            "research_depth": request.research_depth,
            "selected_analysts": request.selected_analysts,
            "model_name": request.model_name,
        }

        task_id = await _service.submit_overview_analysis(
            user_id=user_id,
            candidates=candidates,
            params=analysis_params,
        )

        # 立即在后台启动分析（不依赖独立 Worker 进程）
        import asyncio

        async def _run_overview():
            logger.info(f"[主力选股] 🚀 后台分析任务开始执行: {task_id}, model_name={analysis_params.get('model_name')}")
            try:
                await _service.execute_overview_analysis(
                    task_id=task_id,
                    candidates=candidates,
                    params=analysis_params,
                )
                logger.info(f"[主力选股] ✅ 后台分析任务完成: {task_id}")
            except Exception as bg_err:
                logger.error(f"[主力选股] ❌ 后台分析执行失败: {bg_err}", exc_info=True)

        logger.info(f"[主力选股] 创建后台任务: task_id={task_id}, analysis_params={list(analysis_params.keys())}")
        asyncio.create_task(_run_overview())

        # 3. 如果是批量模式，额外提交批量深度分析
        batch_id = None
        if request.analysis_mode == "batch" and request.batch_count:
            # 取前 batch_count 只股票进行批量深度分析
            batch_symbols = [c["code"] for c in candidates[: request.batch_count]]
            batch_id = await _service.submit_batch_analysis(
                user_id=user_id,
                symbols=batch_symbols,
                params=analysis_params,
            )

        return ok(
            data={
                "task_id": task_id,
                "batch_id": batch_id,
                "candidates_count": len(candidates),
                "total_fetched": screen_result.total_fetched,
                "total_filtered": screen_result.total_filtered,
            },
            message="分析任务已提交",
        )
    except Exception as e:
        logger.error(f"[主力选股] 分析请求失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"主力选股分析失败: {str(e)}")


@router.get("/batch-history")
async def get_batch_history(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    user: dict = Depends(get_current_user),
):
    """
    查询批量分析历史

    支持分页查询，按分析日期降序排列。
    """
    try:
        result = await _service.get_batch_history(page=page, page_size=page_size)
        return ok(data=result, message="查询成功")
    except Exception as e:
        logger.error(f"[主力选股] 查询批量历史失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询批量历史失败: {str(e)}")


@router.delete("/batch-history/{history_id}")
async def delete_batch_history(
    history_id: str,
    user: dict = Depends(get_current_user),
):
    """
    删除指定的批量分析历史记录
    """
    try:
        deleted = await _service.delete_batch_history(history_id)
        if deleted:
            return ok(message="删除成功")
        else:
            raise HTTPException(status_code=404, detail="历史记录不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[主力选股] 删除历史记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除历史记录失败: {str(e)}")
