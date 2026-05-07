"""
持仓分析 API 路由

提供持仓分析相关的 REST API 端点：
- 单只股票分析
- 批量分析（异步任务）
- 分析历史查询
- 最新分析汇总
- 同步分析结果到实时监测
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.response import ok, fail
from app.routers.auth_db import get_current_user
from app.services.portfolio_analysis_service import PortfolioAnalysisService

router = APIRouter(prefix="/api/portfolio", tags=["持仓分析"])
logger = logging.getLogger("webapi")

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class SingleAnalyzeRequest(BaseModel):
    """单只股票分析请求"""
    market: str = Field(default="CN", description="市场类型 (CN/HK/US)")
    model_name: Optional[str] = Field(default=None, description="指定 LLM 模型名称")


class QuickAnalyzeRequest(BaseModel):
    """快速分析请求（不依赖持仓，直接输入代码即时分析）"""
    codes: List[str] = Field(..., min_length=1, max_length=10, description="股票代码列表（1-10只）")
    market: str = Field(default="CN", description="市场类型 (CN/HK/US)")
    model_name: Optional[str] = Field(default=None, description="指定 LLM 模型名称")


class BatchAnalyzeRequest(BaseModel):
    """批量分析请求"""
    codes: Optional[List[str]] = Field(
        default=None,
        description="股票代码列表，为空时分析所有持仓",
    )
    model_name: Optional[str] = Field(default=None, description="指定 LLM 模型名称")


# ---------------------------------------------------------------------------
# 服务实例获取
# ---------------------------------------------------------------------------


def _get_service() -> PortfolioAnalysisService:
    """获取 PortfolioAnalysisService 实例"""
    return PortfolioAnalysisService()


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/analyze/{code}", response_model=Dict[str, Any])
async def analyze_single(
    code: str,
    body: SingleAnalyzeRequest = None,
    user: dict = Depends(get_current_user),
):
    """
    对单只持仓股票执行 AI 分析。

    - **code**: 股票代码（路径参数）
    - **market**: 市场类型，默认 CN
    - **model_name**: 可选，指定 LLM 模型
    """
    try:
        if not code or not code.strip():
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        # 处理 body 为 None 的情况（客户端未发送请求体）
        market = body.market if body else "CN"
        model_name = body.model_name if body else None

        service = _get_service()
        result = await service.analyze_single(
            user_id=user["id"],
            code=code.strip(),
            market=market,
            model_name=model_name,
        )

        if result.get("success"):
            return ok(result.get("data"), message="分析完成")
        else:
            # 业务层返回的失败（如行情获取失败）
            error_msg = result.get("error", "分析失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[持仓分析路由] 单只分析异常: code={code}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析服务异常: {e}")


@router.post("/analyze-batch", response_model=Dict[str, Any])
async def analyze_batch(
    body: BatchAnalyzeRequest = None,
    user: dict = Depends(get_current_user),
):
    """
    提交批量持仓分析任务。

    - **codes**: 股票代码列表（可选），为空时分析所有持仓
    - **model_name**: 可选，指定 LLM 模型

    直接在后台异步执行（不依赖 Worker 队列），通过进度查询接口跟踪状态。
    """
    import asyncio

    try:
        codes = body.codes if body else None
        model_name = body.model_name if body else None

        service = _get_service()
        task_id = await service.submit_batch_analysis(
            user_id=user["id"],
            codes=codes,
            model_name=model_name,
        )

        # 直接在后台异步执行批量分析（不依赖 Worker 队列消费）
        # 这样即使 Worker 没有运行，批量分析也能正常工作
        async def _run_batch():
            try:
                await service.execute_batch_analysis(
                    task_id=task_id,
                    user_id=user["id"],
                    codes=codes or [],
                    model_name=model_name,
                )
            except Exception as exc:
                logger.error(f"[持仓分析] 后台批量分析异常: {task_id}: {exc}")

        # 如果 codes 为空，需要重新获取（submit_batch_analysis 内部已获取但没返回）
        if not codes:
            positions = await service.get_positions_for_analysis(user["id"])
            codes = [pos["code"] for pos in positions if pos.get("code")]

        asyncio.create_task(_run_batch())

        return ok({"task_id": task_id}, message="批量分析任务已提交")

    except ValueError as e:
        # submit_batch_analysis 在没有可分析持仓时抛出 ValueError
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[持仓分析路由] 批量分析提交异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量分析服务异常: {e}")


@router.get("/analysis-history/{code}", response_model=Dict[str, Any])
async def get_analysis_history(
    code: str,
    limit: int = Query(default=20, ge=1, le=200, description="返回记录数上限"),
    user: dict = Depends(get_current_user),
):
    """
    查询单只股票的分析历史。

    - **code**: 股票代码（路径参数）
    - **limit**: 返回记录数上限，默认 20，最大 200
    """
    try:
        if not code or not code.strip():
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        service = _get_service()
        history = await service.get_analysis_history(
            user_id=user["id"],
            code=code.strip(),
            limit=limit,
        )

        return ok({"items": history, "total": len(history)}, message="查询成功")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[持仓分析路由] 查询分析历史异常: code={code}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询分析历史异常: {e}")


@router.get("/analysis-latest", response_model=Dict[str, Any])
async def get_latest_analyses(
    user: dict = Depends(get_current_user),
):
    """
    查询所有持仓的最新分析结果汇总。

    返回每只持仓最新的一条分析记录。
    """
    try:
        service = _get_service()
        analyses = await service.get_latest_analyses(user_id=user["id"])

        return ok(
            {"items": analyses, "total": len(analyses)},
            message="查询成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[持仓分析路由] 查询最新分析汇总异常: error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"查询最新分析汇总异常: {e}")


@router.post("/sync-to-monitor/{code}", response_model=Dict[str, Any])
async def sync_to_monitor(
    code: str,
    user: dict = Depends(get_current_user),
):
    """
    将分析结果同步到实时监测配置。

    自动获取该股票最新的分析结果，将 entry_range、take_profit、stop_loss、rating
    同步到 stock_monitor_configs 集合。已存在配置则更新，不存在则创建。

    - **code**: 股票代码（路径参数）
    """
    try:
        if not code or not code.strip():
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        service = _get_service()
        result = await service.sync_to_monitor(
            user_id=user["id"],
            code=code.strip(),
        )

        if result.get("success"):
            action = result.get("action", "unknown")
            config_id = result.get("config_id", "")
            return ok(
                {"action": action, "config_id": config_id},
                message=f"同步成功（{action}）",
            )
        else:
            error_msg = result.get("error", "同步失败")
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[持仓分析路由] 同步到监测异常: code={code}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"同步到监测服务异常: {e}")


@router.post("/quick-analyze", response_model=Dict[str, Any])
async def quick_analyze(
    body: QuickAnalyzeRequest,
    user: dict = Depends(get_current_user),
):
    """
    快速实时分析（不依赖持仓，直接输入代码即时分析）。

    输入 1-10 只股票代码，系统立即获取当前行情和技术指标，
    通过 AI 分析后给出买入/持有/卖出建议。

    - **codes**: 股票代码列表（1-10只）
    - **market**: 市场类型，默认 CN
    - **model_name**: 可选，指定 LLM 模型
    """
    try:
        if not body.codes:
            raise HTTPException(status_code=400, detail="请输入至少一只股票代码")

        service = _get_service()
        results = []

        for code in body.codes:
            code = code.strip()
            if not code:
                continue

            # 直接调用 analyze_single（不需要持仓数据，position_info 会使用默认值）
            result = await service.analyze_single(
                user_id=user["id"],
                code=code,
                market=body.market,
                model_name=body.model_name,
            )
            results.append({
                "code": code,
                **result,
            })

        # 统计
        success_count = sum(1 for r in results if r.get("success"))
        failed_count = len(results) - success_count

        return ok(
            {
                "results": results,
                "total": len(results),
                "success": success_count,
                "failed": failed_count,
            },
            message=f"分析完成: {success_count}/{len(results)} 成功",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[持仓分析路由] 快速分析异常: error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"快速分析服务异常: {e}")
