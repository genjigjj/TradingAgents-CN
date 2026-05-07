"""
龙虎榜分析 API 路由

提供龙虎榜数据获取、AI 分析、报告管理、统计查询等 REST API 端点。
所有端点使用 JWT 认证保护，API 前缀 /api/longhubang。
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from app.routers.auth_db import get_current_user
from app.core.response import ok, fail
from app.models.longhubang import LonghubangAnalyzeRequest
from app.services.longhubang_service import LonghubangService
from app.services.config_service import ConfigService

router = APIRouter(tags=["longhubang"])
logger = logging.getLogger("webapi")

# 服务实例（注入 ConfigService 以读取 LLM 和 API 配置）
_service = LonghubangService(config_service=ConfigService())


@router.post("/analyze")
async def analyze(
    request: LonghubangAnalyzeRequest,
    user: dict = Depends(get_current_user),
):
    """
    触发龙虎榜分析

    获取龙虎榜数据 → AI 智能评分 → 提交 AI 分析任务到队列。
    同步返回数据概况和评分排名，AI 分析师报告通过任务 ID 异步获取。
    """
    try:
        user_id = user.get("id", "")
        logger.info(
            f"[龙虎榜] 用户 {user_id} 发起分析请求: mode={request.mode}, "
            f"date={request.date}, days={request.days}"
        )

        # 1. 获取龙虎榜数据
        if request.mode == "date" and request.date:
            data_result = await _service.fetch_data(date=request.date)
        else:
            data_result = await _service.fetch_data(days=request.days)

        if not data_result.data_list:
            return ok(
                data={
                    "task_id": None,
                    "data_info": data_result.summary,
                    "scoring_ranking": [],
                    "message": "未获取到龙虎榜数据，请检查日期或稍后重试",
                },
                message="未获取到龙虎榜数据",
            )

        # 2. AI 智能评分
        scoring = await _service.score_stocks(data_result.data_list)

        # 3. 提交 AI 分析任务到队列
        task_id = await _service.submit_ai_analysis(
            user_id=user_id,
            data_summary=data_result.summary,
            scoring=scoring,
        )

        # 立即在后台启动 AI 分析（不依赖独立 Worker 进程）
        import asyncio
        specified_model = request.model_name

        async def _run_ai_analysis():
            try:
                await _service.execute_ai_analysis(
                    task_id=task_id,
                    data_summary=data_result.summary,
                    scoring=scoring,
                    model_name=specified_model,
                )
            except Exception as bg_err:
                logger.error(f"[龙虎榜] 后台 AI 分析执行失败: {bg_err}", exc_info=True)

        asyncio.create_task(_run_ai_analysis())

        return ok(
            data={
                "task_id": task_id,
                "data_info": data_result.summary,
                "scoring_ranking": scoring,
                "date_range": data_result.date_range,
                "saved_count": data_result.saved_count,
            },
            message="龙虎榜分析已启动",
        )
    except Exception as e:
        logger.error(f"[龙虎榜] 分析请求失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"龙虎榜分析失败: {str(e)}")


@router.get("/reports")
async def get_reports(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    user: dict = Depends(get_current_user),
):
    """
    查询历史报告列表

    支持分页查询，按创建时间降序排列，返回报告概要信息。
    """
    try:
        result = await _service.get_reports(page=page, page_size=page_size)
        return ok(data=result, message="查询成功")
    except Exception as e:
        logger.error(f"[龙虎榜] 查询报告列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询报告列表失败: {str(e)}")


@router.get("/reports/{report_id}")
async def get_report_detail(
    report_id: str,
    user: dict = Depends(get_current_user),
):
    """
    查询报告详情

    返回单个报告的完整内容，包含 AI 分析师报告全文、评分排名、推荐股票等。
    """
    try:
        detail = await _service.get_report_detail(report_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return ok(data=detail, message="查询成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[龙虎榜] 查询报告详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询报告详情失败: {str(e)}")


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: dict = Depends(get_current_user),
):
    """
    删除指定的历史报告
    """
    try:
        deleted = await _service.delete_report(report_id)
        if deleted:
            return ok(message="删除成功")
        else:
            raise HTTPException(status_code=404, detail="报告不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[龙虎榜] 删除报告失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除报告失败: {str(e)}")


@router.get("/statistics")
async def get_statistics(
    user: dict = Depends(get_current_user),
):
    """
    数据库统计信息

    返回龙虎榜数据库的统计概览：总记录数、涉及股票数、涉及游资数、
    分析报告数、数据日期范围。
    """
    try:
        stats = await _service.get_statistics()
        return ok(data=stats, message="查询成功")
    except Exception as e:
        logger.error(f"[龙虎榜] 查询统计信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询统计信息失败: {str(e)}")


@router.get("/records")
async def get_records(
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    stock_code: Optional[str] = Query(default=None, description="股票代码（可选）"),
    user: dict = Depends(get_current_user),
):
    """
    查询龙虎榜历史数据

    支持按日期范围和股票代码筛选，按日期降序和净流入降序排列。
    """
    try:
        records = await _service.get_records(
            start_date=start_date,
            end_date=end_date,
            stock_code=stock_code,
        )
        return ok(data=records, message="查询成功")
    except Exception as e:
        logger.error(f"[龙虎榜] 查询历史数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询历史数据失败: {str(e)}")


@router.get("/top-youzi")
async def get_top_youzi(
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量"),
    user: dict = Depends(get_current_user),
):
    """
    活跃游资排名

    按日期范围筛选，返回游资名称、交易次数、总买入、总卖出、总净流入。
    """
    try:
        ranking = await _service.get_top_youzi(
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return ok(data=ranking, message="查询成功")
    except Exception as e:
        logger.error(f"[龙虎榜] 查询活跃游资排名失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询活跃游资排名失败: {str(e)}")


@router.get("/top-stocks")
async def get_top_stocks(
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量"),
    user: dict = Depends(get_current_user),
):
    """
    热门股票排名

    按日期范围筛选，返回股票代码、名称、游资关注数、总买入、总卖出、
    总净流入、相关概念。
    """
    try:
        ranking = await _service.get_top_stocks(
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return ok(data=ranking, message="查询成功")
    except Exception as e:
        logger.error(f"[龙虎榜] 查询热门股票排名失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询热门股票排名失败: {str(e)}")
