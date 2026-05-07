"""
持仓分析服务

对模拟交易持仓执行 AI 多维度分析（评级、信心度、目标价、进出场位、止盈止损等）。
直接以 paper_positions 为数据源，通过 Unified_LLM_Service 调用 AI 进行分析。

从 aiagents-stock/ 的持仓分析模块迁移并重构为异步服务。
"""

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

# 预定义的评级列表
VALID_RATINGS = ["强烈买入", "买入", "增持", "持有", "观望", "减持", "卖出", "强烈卖出"]


class PortfolioAnalysisService:
    """持仓分析服务 — 对模拟交易持仓执行 AI 多维度分析"""

    def __init__(self, config_service=None):
        """
        初始化持仓分析服务。

        Args:
            config_service: ConfigService 实例，用于读取系统配置。
                           如果为 None，则使用默认参数。
        """
        self.config_service = config_service

    # ------------------------------------------------------------------
    # 公开方法：持仓数据读取
    # ------------------------------------------------------------------

    async def get_positions_for_analysis(
        self, user_id: str, market: str = None
    ) -> List[Dict[str, Any]]:
        """
        从 paper_positions 读取用户持仓数据。

        可选按 market 过滤 (CN/HK/US)。

        Args:
            user_id: 用户 ID
            market: 市场类型过滤（可选），如 "CN"、"HK"、"US"

        Returns:
            持仓数据列表，每条包含 code、quantity、avg_cost、market、currency 等字段
        """
        db = get_mongo_db()

        query: Dict[str, Any] = {"user_id": user_id}
        if market:
            query["market"] = market.upper()

        positions = await db["paper_positions"].find(query).to_list(None)

        result = []
        for pos in positions:
            result.append({
                "code": pos.get("code", ""),
                "quantity": int(pos.get("quantity", 0)),
                "avg_cost": float(pos.get("avg_cost", 0.0)),
                "market": pos.get("market", "CN"),
                "currency": pos.get("currency", "CNY"),
                "available_qty": int(pos.get("available_qty", 0)),
                "frozen_qty": int(pos.get("frozen_qty", 0)),
            })

        logger.info(
            f"[持仓分析] 获取用户 {user_id} 持仓: {len(result)} 条"
            + (f" (market={market})" if market else "")
        )
        return result

    # ------------------------------------------------------------------
    # 公开方法：单只股票分析
    # ------------------------------------------------------------------

    async def analyze_single(
        self, user_id: str, code: str, market: str = "CN",
        model_name: str = None
    ) -> Dict[str, Any]:
        """
        对单只持仓股票执行 AI 分析。

        流程: 获取行情 → 获取技术指标 → 构建 prompt → 调用 LLM → 解析结果 → 持久化

        Args:
            user_id: 用户 ID
            code: 股票代码
            market: 市场类型 (CN/HK/US)，默认 CN
            model_name: 用户指定的模型名称（可选）

        Returns:
            {"success": True, "data": analysis_result} 或
            {"success": False, "error": "错误信息"}
        """
        from app.services import market_data_helper

        # 1. 获取实时行情数据
        try:
            market_data = await market_data_helper.get_market_data(code, market)
        except Exception as e:
            logger.warning(f"[持仓分析] 行情获取失败: {code} ({market}): {e}")
            return {"success": False, "error": f"行情获取失败: {e}"}

        # 2. 获取技术指标（失败时降级处理，使用空指标继续分析）
        try:
            indicators = await market_data_helper.get_technical_indicators(code, market)
        except Exception as e:
            logger.warning(
                f"[持仓分析] 技术指标获取失败，降级处理: {code} ({market}): {e}"
            )
            indicators = {}

        # 3. 获取持仓信息（用于 prompt 中的成本计算）
        db = get_mongo_db()
        position_doc = await db["paper_positions"].find_one(
            {"user_id": user_id, "code": code}
        )
        position_info = {
            "quantity": int(position_doc.get("quantity", 0)) if position_doc else 0,
            "avg_cost": float(position_doc.get("avg_cost", 0.0)) if position_doc else 0.0,
            "market": market,
            "currency": position_doc.get("currency", "CNY") if position_doc else "CNY",
        }

        # 4. 构建分析 prompt
        messages = self._build_analysis_prompt(code, market_data, indicators, position_info)

        # 5. 调用 LLM
        try:
            llm_call = await self._get_llm_call_func(model_name)
            llm_response = await llm_call(messages)
        except Exception as e:
            logger.error(f"[持仓分析] LLM 调用失败: {code}: {e}")
            return {"success": False, "error": f"AI 分析服务调用失败: {e}"}

        # 6. 解析 LLM 结果
        analysis_result = self._parse_analysis_result(llm_response, code, market_data)

        # 7. 持久化到 portfolio_analysis_history
        now = datetime.now()
        history_doc = {
            "user_id": user_id,
            "position_code": code,
            "market": market.upper(),
            "analysis_time": now,
            # 分析结果字段
            "rating": analysis_result["rating"],
            "confidence": analysis_result["confidence"],
            "current_price": analysis_result["current_price"],
            "target_price": analysis_result["target_price"],
            "entry_min": analysis_result["entry_range"]["min"],
            "entry_max": analysis_result["entry_range"]["max"],
            "take_profit": analysis_result["take_profit"],
            "stop_loss": analysis_result["stop_loss"],
            "summary": analysis_result["summary"],
            # 元数据
            "model_name": model_name or "default",
            "raw_response": llm_response,
            "full_report": analysis_result.get("full_report", ""),
            "market_data_snapshot": market_data,
            "indicators_snapshot": indicators,
            "created_at": now,
        }

        try:
            await db["portfolio_analysis_history"].insert_one(history_doc)
            logger.info(
                f"[持仓分析] ✅ 分析完成并持久化: {code} ({market}), "
                f"评级={analysis_result['rating']}, 信心度={analysis_result['confidence']}"
            )
        except Exception as e:
            # MongoDB 写入失败不中断主流程，仅记录错误
            logger.error(f"[持仓分析] 持久化失败: {code}: {e}")

        # 确保返回的 data 中包含 position_code（前端展示需要）
        analysis_result["position_code"] = code
        analysis_result["market"] = market.upper()

        return {"success": True, "data": analysis_result}

    # ------------------------------------------------------------------
    # 公开方法：批量分析
    # ------------------------------------------------------------------

    async def submit_batch_analysis(
        self, user_id: str, codes: List[str] = None,
        model_name: str = None
    ) -> str:
        """
        提交批量分析任务。codes 为空时分析所有持仓。

        流程：
        1. codes 为空时从 paper_positions 获取所有持仓代码
        2. 生成 UUID 作为 task_id
        3. 写入 analysis_tasks 集合（status=pending, task_type="portfolio_batch"）
        4. 通过 QueueService 入队
        5. 写入 Redis 初始进度（0%）
        6. 返回 task_id

        Args:
            user_id: 用户 ID
            codes: 股票代码列表（可选），为空时分析所有持仓
            model_name: 用户指定的模型名称（可选）

        Returns:
            task_id 字符串
        """
        from app.services.queue_service import get_queue_service

        db = get_mongo_db()

        # 1. codes 为空时从 paper_positions 获取所有持仓代码
        if not codes:
            positions = await self.get_positions_for_analysis(user_id)
            codes = [pos["code"] for pos in positions if pos.get("code")]

        if not codes:
            raise ValueError("没有可分析的持仓股票")

        # 2. 生成 UUID 作为 task_id
        task_id = str(uuid.uuid4())

        # 3. 写入 analysis_tasks 集合
        task_record = {
            "task_id": task_id,
            "user_id": user_id,
            "symbol": "portfolio_batch",
            "status": "pending",
            "task_type": "portfolio_batch",
            "parameters": {
                "codes": codes,
                "codes_count": len(codes),
                "model_name": model_name,
            },
            "progress": 0,
            "created_at": datetime.utcnow(),
        }
        await db.analysis_tasks.insert_one(task_record)

        # 4. 通过 QueueService 入队
        queue_service = get_queue_service()
        queue_params: Dict[str, Any] = {
            "task_type": "portfolio_batch",
            "codes": codes,
            "model_name": model_name,
            "user_id": user_id,
        }
        await queue_service.enqueue_task(
            user_id=user_id,
            symbol="portfolio_batch",
            params=queue_params,
            task_id=task_id,
        )

        # 5. 写入 Redis 初始进度（0%）
        await self._write_initial_progress(
            task_id, "portfolio_batch",
            f"持仓批量分析任务已入队，共 {len(codes)} 只股票等待分析..."
        )

        logger.info(
            f"[持仓分析] 批量分析任务已提交: {task_id}, "
            f"共 {len(codes)} 只股票"
        )
        return task_id

    async def execute_batch_analysis(
        self, task_id: str, user_id: str, codes: List[str],
        model_name: str = None
    ) -> Dict[str, Any]:
        """
        执行批量分析（由 Worker 调用）。

        逐只调用 analyze_single，失败时跳过并记录错误，
        通过 RedisProgressTracker 更新进度。

        Args:
            task_id: 任务 ID
            user_id: 用户 ID
            codes: 股票代码列表
            model_name: 用户指定的模型名称（可选）

        Returns:
            {"total": n, "success": m, "failed": k, "errors": [...], "results": [...]}
        """
        from app.services.redis_progress_tracker import RedisProgressTracker

        db = get_mongo_db()
        total = len(codes)

        # 初始化进度跟踪器
        progress_tracker = RedisProgressTracker(
            task_id=task_id,
            analysts=["portfolio_analysis"],
            research_depth="标准",
            llm_provider="dashscope",
        )

        # 更新 DB 和 Redis 状态为 running（让前端立即感知到任务已开始执行）
        progress_tracker.update_progress({
            "progress_percentage": 2,
            "last_message": f"🚀 Worker 已接收任务，准备分析 {total} 只股票...",
        })
        await self._sync_task_status(task_id, "running", 2, f"Worker 已接收任务，准备分析 {total} 只股票")

        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        success_count = 0
        failed_count = 0

        try:
            for i, code in enumerate(codes):
                # 计算当前进度百分比（5% ~ 95%）
                base_pct = 5 + int((i / total) * 90)

                # 阶段 1: 获取行情
                progress_tracker.update_progress({
                    "progress_percentage": base_pct,
                    "last_message": f"📈 [{i + 1}/{total}] {code} - 获取实时行情数据...",
                })
                await self._sync_task_status(
                    task_id, "running", base_pct,
                    f"[{i + 1}/{total}] {code} - 获取行情"
                )

                try:
                    result = await self.analyze_single(
                        user_id=user_id,
                        code=code,
                        market="CN",
                        model_name=model_name,
                    )

                    if result.get("success"):
                        success_count += 1
                        # 阶段完成提示
                        step_pct = 5 + int(((i + 1) / total) * 90)
                        progress_tracker.update_progress({
                            "progress_percentage": step_pct,
                            "last_message": f"✅ [{i + 1}/{total}] {code} - 分析完成 (评级: {result.get('data', {}).get('rating', '-')})",
                        })
                        results.append({
                            "code": code,
                            "success": True,
                            "data": result.get("data"),
                        })
                    else:
                        # analyze_single 返回了失败响应（如行情获取失败）
                        failed_count += 1
                        error_msg = result.get("error", "分析失败")
                        errors.append({"code": code, "error": error_msg})
                        results.append({
                            "code": code,
                            "success": False,
                            "error": error_msg,
                        })
                        step_pct = 5 + int(((i + 1) / total) * 90)
                        progress_tracker.update_progress({
                            "progress_percentage": step_pct,
                            "last_message": f"⚠️ [{i + 1}/{total}] {code} - 跳过: {error_msg[:50]}",
                        })
                        logger.warning(
                            f"[持仓分析] 批量分析跳过 {code}: {error_msg}"
                        )

                except Exception as e:
                    # 异常时跳过并记录错误
                    failed_count += 1
                    error_msg = str(e)
                    errors.append({"code": code, "error": error_msg})
                    results.append({
                        "code": code,
                        "success": False,
                        "error": error_msg,
                    })
                    step_pct = 5 + int(((i + 1) / total) * 90)
                    progress_tracker.update_progress({
                        "progress_percentage": step_pct,
                        "last_message": f"❌ [{i + 1}/{total}] {code} - 异常: {error_msg[:50]}",
                    })
                    logger.error(
                        f"[持仓分析] 批量分析异常 {code}: {e}"
                    )

            # 全部完成
            batch_result = {
                "total": total,
                "success": success_count,
                "failed": failed_count,
                "errors": errors,
                "results": results,
            }

            # 标记进度完成
            progress_tracker.update_progress({
                "progress_percentage": 100,
                "last_message": f"✅ 批量分析完成: 成功 {success_count}/{total}",
            })
            progress_tracker.mark_completed()

            # 更新 analysis_tasks 状态为 completed
            await self._sync_task_status(
                task_id, "completed", 100,
                f"批量分析完成: 成功 {success_count}/{total}",
                result=batch_result,
            )

            logger.info(
                f"[持仓分析] ✅ 批量分析完成: {task_id}, "
                f"成功={success_count}, 失败={failed_count}, 总计={total}"
            )
            return batch_result

        except Exception as e:
            # 整体异常（不应发生，但作为安全网）
            error_msg = f"批量分析执行异常: {e}"
            logger.error(f"[持仓分析] {error_msg}")
            progress_tracker.mark_failed(error_msg)
            await self._sync_task_status(
                task_id, "failed", 0, error=error_msg
            )
            return {
                "total": total,
                "success": success_count,
                "failed": failed_count + (total - success_count - failed_count),
                "errors": errors + [{"code": "batch", "error": error_msg}],
                "results": results,
            }

    # ------------------------------------------------------------------
    # 公开方法：查询分析历史
    # ------------------------------------------------------------------

    async def get_analysis_history(
        self, user_id: str, code: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        查询单只股票的分析历史。

        从 portfolio_analysis_history 集合按 analysis_time 倒序查询，
        支持 limit 参数分页。

        Args:
            user_id: 用户 ID
            code: 股票代码
            limit: 返回记录数上限，默认 20

        Returns:
            分析历史列表，每条记录的 _id 已转为字符串
        """
        db = get_mongo_db()
        cursor = (
            db["portfolio_analysis_history"]
            .find({"user_id": user_id, "position_code": code})
            .sort("analysis_time", -1)
            .limit(limit)
        )
        results = await cursor.to_list(length=limit)

        # 将 MongoDB _id 转为字符串
        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])

        logger.info(
            f"[持仓分析] 查询分析历史: user={user_id}, code={code}, "
            f"limit={limit}, 返回 {len(results)} 条"
        )
        return results

    async def get_latest_analyses(
        self, user_id: str
    ) -> List[Dict[str, Any]]:
        """
        查询所有持仓的最新分析结果。

        使用 MongoDB 聚合管道，按 position_code 分组，
        取每组最新的一条记录，按 analysis_time 倒序排列。

        Args:
            user_id: 用户 ID

        Returns:
            每只持仓最新分析结果列表，每条记录的 _id 已转为字符串
        """
        db = get_mongo_db()

        pipeline = [
            # 1. 筛选当前用户的记录
            {"$match": {"user_id": user_id}},
            # 2. 按 analysis_time 倒序排列（确保 $first 取到最新的）
            {"$sort": {"analysis_time": -1}},
            # 3. 按 position_code 分组，取每组第一条（即最新的）
            {
                "$group": {
                    "_id": "$position_code",
                    "doc": {"$first": "$$ROOT"},
                }
            },
            # 4. 将分组结果展开为原始文档结构
            {"$replaceRoot": {"newRoot": "$doc"}},
            # 5. 按 analysis_time 倒序排列最终结果
            {"$sort": {"analysis_time": -1}},
        ]

        results = await db["portfolio_analysis_history"].aggregate(pipeline).to_list(None)

        # 将 MongoDB _id 转为字符串
        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])

        logger.info(
            f"[持仓分析] 查询最新分析汇总: user={user_id}, 返回 {len(results)} 条"
        )
        return results

    # ------------------------------------------------------------------
    # 公开方法：同步分析结果到实时监测
    # ------------------------------------------------------------------

    async def sync_to_monitor(
        self, user_id: str, code: str, analysis_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        将分析结果同步到实时监测配置（stock_monitor_configs）。

        如果 analysis_result 为 None，从 portfolio_analysis_history 获取最新分析结果。
        已存在配置则更新，不存在则创建新配置（默认 notification_enabled=true、
        trading_hours_only=true、enabled=true、check_interval=60）。

        Args:
            user_id: 用户 ID
            code: 股票代码
            analysis_result: 分析结果字典（可选），包含 entry_range、take_profit、
                            stop_loss、rating 等字段。为 None 时自动查询最新分析。

        Returns:
            {"success": True, "action": "created"/"updated", "config_id": str} 或
            {"success": False, "error": "错误信息"}
        """
        db = get_mongo_db()

        # 如果未传入 analysis_result，从历史记录获取最新分析
        if analysis_result is None:
            latest = await db["portfolio_analysis_history"].find_one(
                {"user_id": user_id, "position_code": code},
                sort=[("analysis_time", -1)],
            )
            if not latest:
                return {"success": False, "error": f"未找到 {code} 的分析记录"}

            # 从历史记录构建 analysis_result
            analysis_result = {
                "entry_range": {
                    "min": latest.get("entry_min", 0.0),
                    "max": latest.get("entry_max", 0.0),
                },
                "take_profit": latest.get("take_profit", 0.0),
                "stop_loss": latest.get("stop_loss", 0.0),
                "rating": latest.get("rating", "持有"),
            }

        now = datetime.now()

        # 构建更新字段
        entry_range = analysis_result.get("entry_range", {})
        update_fields = {
            "entry_range": {
                "min": float(entry_range.get("min", 0.0)),
                "max": float(entry_range.get("max", 0.0)),
            },
            "take_profit": float(analysis_result.get("take_profit", 0.0)),
            "stop_loss": float(analysis_result.get("stop_loss", 0.0)),
            "rating": analysis_result.get("rating", "持有"),
            "source": "analysis_sync",
            "updated_at": now,
        }

        # 新建时额外设置的默认字段
        set_on_insert_fields = {
            "user_id": user_id,
            "symbol": code,
            "notification_enabled": True,
            "trading_hours_only": True,
            "enabled": True,
            "check_interval": 60,
            "created_at": now,
        }

        try:
            result = await db["stock_monitor_configs"].update_one(
                {"user_id": user_id, "symbol": code},
                {
                    "$set": update_fields,
                    "$setOnInsert": set_on_insert_fields,
                },
                upsert=True,
            )

            # 判断是创建还是更新
            if result.upserted_id:
                action = "created"
                config_id = str(result.upserted_id)
            else:
                action = "updated"
                # 获取已有配置的 _id
                existing = await db["stock_monitor_configs"].find_one(
                    {"user_id": user_id, "symbol": code}
                )
                config_id = str(existing["_id"]) if existing else ""

            logger.info(
                f"[持仓分析] ✅ 同步到监测配置: {code}, action={action}, "
                f"config_id={config_id}"
            )
            return {"success": True, "action": action, "config_id": config_id}

        except Exception as e:
            logger.error(f"[持仓分析] 同步到监测配置失败: {code}: {e}")
            return {"success": False, "error": f"同步失败: {e}"}

    # ------------------------------------------------------------------
    # 内部方法：进度与状态同步
    # ------------------------------------------------------------------

    @staticmethod
    async def _write_initial_progress(
        task_id: str, task_type: str, message: str
    ) -> None:
        """
        入队后立即写入初始进度到 Redis / 文件，
        让前端轮询能立刻感知到任务存在（状态 = pending）。

        与 main_force_service._write_initial_progress 模式一致。
        """
        import json as _json
        import os
        import time as _time

        progress_data = {
            "task_id": task_id,
            "task_type": task_type,
            "status": "pending",
            "progress_percentage": 0,
            "last_message": message,
            "start_time": _time.time(),
            "completed": False,
        }

        try:
            redis_enabled = os.getenv("REDIS_ENABLED", "false").lower() == "true"
            if redis_enabled:
                import redis
                redis_host = os.getenv("REDIS_HOST", "localhost")
                redis_port = int(os.getenv("REDIS_PORT", 6379))
                redis_password = os.getenv("REDIS_PASSWORD", None)
                redis_db = int(os.getenv("REDIS_DB", 0))
                rc = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    password=redis_password or None,
                    db=redis_db,
                    decode_responses=True,
                )
                key = f"progress:{task_id}"
                rc.set(key, _json.dumps(progress_data))
                rc.expire(key, 3600)
                logger.info(f"✅ 初始进度已写入 Redis: {task_id}")
                return

            # Redis 未启用时写文件
            os.makedirs("./data/progress", exist_ok=True)
            with open(
                f"./data/progress/{task_id}.json", "w", encoding="utf-8"
            ) as f:
                _json.dump(progress_data, f)
            logger.info(f"✅ 初始进度已写入文件: {task_id}")
        except Exception as e:
            logger.warning(f"⚠️ 写入初始进度失败: {task_id} - {e}")

    async def _sync_task_status(
        self,
        task_id: str,
        status: str,
        progress: int,
        message: str = "",
        result=None,
        error=None,
    ) -> None:
        """
        同步更新 analysis_tasks 集合中的任务状态。

        与 main_force_service._sync_task_status 模式一致。

        Args:
            task_id: 任务 ID
            status: 任务状态（pending / running / completed / failed）
            progress: 进度百分比（0-100）
            message: 当前步骤描述信息
            result: 任务结果（成功完成时传入）
            error: 错误信息（失败时传入）
        """
        try:
            db = get_mongo_db()

            update_data: Dict[str, Any] = {
                "status": status,
                "progress": progress,
            }

            if status in ("processing", "running"):
                update_data["started_at"] = datetime.utcnow()

            if status in ("completed", "failed"):
                update_data["completed_at"] = datetime.utcnow()

            if result is not None:
                update_data["result"] = result

            if error is not None:
                update_data["last_error"] = error

            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data},
            )
        except Exception as e:
            logger.error(
                f"同步任务状态到 MongoDB 失败: task_id={task_id}, "
                f"status={status}, error={e}"
            )

    # ------------------------------------------------------------------
    # 内部方法：LLM 调用
    # ------------------------------------------------------------------

    async def _get_llm_call_func(self, specified_model: str = None):
        """
        获取 LLM 调用函数。

        通过 unified_llm_service 统一获取模型配置，与 longhubang_service 模式一致。

        模型选择优先级：
        1. 用户在前端指定的模型（specified_model）→ unified_llm_service.get_model_config
        2. unified_llm_service.recommend_model 推荐模型
        3. unified_llm_service.get_default_model 获取默认模型
        4. 回退到环境变量

        Args:
            specified_model: 用户指定的模型名称（可选）

        Returns:
            异步调用函数 async def llm_call(messages, max_tokens) -> str
        """
        from openai import AsyncOpenAI
        from app.services.unified_llm_service import unified_llm_service

        model_name = None
        api_key = None
        api_base = None
        temperature = 0.7
        max_tokens_default = 4000

        merged_config = None

        # 方式 1：用户在前端指定了模型
        logger.info(f"[持仓分析 LLM] specified_model={specified_model}")
        if specified_model:
            try:
                merged_config = await unified_llm_service.get_model_config(specified_model)
                if merged_config and merged_config.api_key:
                    logger.info(
                        f"[持仓分析 LLM] ✅ 使用用户指定模型: "
                        f"model={merged_config.model_name}, base={merged_config.api_base}"
                    )
                else:
                    logger.warning(
                        f"[持仓分析 LLM] 用户指定模型 {specified_model} 无有效配置，尝试其他方式"
                    )
                    merged_config = None
            except Exception as e:
                logger.warning(
                    f"[持仓分析 LLM] 查找用户指定模型 {specified_model} 失败: {e}"
                )

        # 方式 2：通过 unified_llm_service.recommend_model 推荐模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.recommend_model(
                    task_type="analysis", depth="标准"
                )
                if merged_config:
                    logger.info(
                        f"[持仓分析 LLM] ✅ 使用推荐模型: "
                        f"model={merged_config.model_name}, base={merged_config.api_base}"
                    )
            except Exception as e:
                logger.warning(
                    f"[持仓分析 LLM] unified_llm_service.recommend_model 获取失败: {e}"
                )

        # 方式 3：通过 unified_llm_service.get_default_model 获取默认模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.get_default_model()
                if merged_config:
                    logger.info(
                        f"[持仓分析 LLM] ✅ 使用默认模型: "
                        f"model={merged_config.model_name}, base={merged_config.api_base}"
                    )
            except Exception as e:
                logger.warning(
                    f"[持仓分析 LLM] unified_llm_service.get_default_model 获取失败: {e}"
                )

        # 从 MergedModelConfig 提取参数
        if merged_config:
            model_name = merged_config.model_name
            api_key = merged_config.api_key
            api_base = merged_config.api_base
            temperature = merged_config.temperature or 0.7
            max_tokens_default = merged_config.max_tokens or 4000

        # 方式 4：回退到环境变量
        if not api_key:
            import os

            # 根据模型名称选择对应的环境变量
            if model_name and "deepseek" in model_name.lower():
                # DeepSeek 模型优先使用 DeepSeek 环境变量
                api_key = os.getenv("DEEPSEEK_API_KEY", "")
                api_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                if not api_key:
                    # DeepSeek key 不存在，尝试聚合渠道
                    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
                    api_base = os.getenv(
                        "DASHSCOPE_BASE_URL",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    )
                model_name = model_name or "deepseek-chat"
            else:
                # 默认使用阿里百炼/OpenAI 环境变量
                api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
                api_base = os.getenv(
                    "DASHSCOPE_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
                model_name = model_name or os.getenv("DEFAULT_MODEL", "qwen-plus")

            # 最终兜底：如果仍然没有 api_key，尝试所有可能的环境变量
            if not api_key:
                api_key = (
                    os.getenv("DEEPSEEK_API_KEY")
                    or os.getenv("DASHSCOPE_API_KEY")
                    or os.getenv("OPENAI_API_KEY", "")
                )
                if os.getenv("DEEPSEEK_API_KEY"):
                    api_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                    model_name = model_name or "deepseek-chat"

            logger.info(
                f"[持仓分析 LLM] 回退到环境变量: model={model_name}, base={api_base}"
            )

        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        logger.info(f"[持仓分析 LLM] 最终配置: model={model_name}, base={api_base}")

        async def llm_call(
            messages: List[Dict[str, str]], max_tokens: int = None
        ) -> str:
            """调用 LLM 模型"""
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens or max_tokens_default,
                )
                msg = response.choices[0].message
                content = msg.content or ""
                # DeepSeek V4 等推理模型可能将内容放在 reasoning_content 中
                reasoning_content = getattr(msg, "reasoning_content", "") or ""
                result = content if content.strip() else reasoning_content
                if not result.strip():
                    logger.warning(
                        f"[持仓分析 LLM] 模型返回空内容: "
                        f"content='{content}', reasoning_content='{reasoning_content[:50]}...'"
                    )
                return result
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}")
                raise

        return llm_call

    # ------------------------------------------------------------------
    # 纯函数：构建分析 Prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_analysis_prompt(
        code: str,
        market_data: Dict[str, Any],
        indicators: Dict[str, Any],
        position_info: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """
        构建持仓分析 prompt，包含行情数据、技术指标和持仓成本信息。

        这是一个纯函数（静态方法），不依赖任何外部状态，方便测试。

        Args:
            code: 股票代码
            market_data: 实时行情数据 {price, change_pct, volume, high, low, open, prev_close}
            indicators: 技术指标 {ma5, ma20, ma60, macd, rsi, kdj, boll}
            position_info: 持仓信息 {quantity, avg_cost, market, currency}

        Returns:
            LLM messages 列表 [{"role": "system", ...}, {"role": "user", ...}]
        """
        price = market_data.get("price", 0)
        change_pct = market_data.get("change_pct", 0)
        volume = market_data.get("volume", 0)
        high = market_data.get("high", 0)
        low = market_data.get("low", 0)
        open_price = market_data.get("open", 0)
        prev_close = market_data.get("prev_close", 0)

        avg_cost = position_info.get("avg_cost", 0)
        quantity = position_info.get("quantity", 0)
        market = position_info.get("market", "CN")
        currency = position_info.get("currency", "CNY")

        # 计算持仓盈亏
        if avg_cost > 0 and price > 0:
            pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)
            pnl_amount = round((price - avg_cost) * quantity, 2)
        else:
            pnl_pct = 0
            pnl_amount = 0

        # 技术指标文本
        ma5 = indicators.get("ma5", "N/A")
        ma20 = indicators.get("ma20", "N/A")
        ma60 = indicators.get("ma60", "N/A")

        macd = indicators.get("macd", {})
        macd_dif = macd.get("dif", "N/A") if isinstance(macd, dict) else "N/A"
        macd_dea = macd.get("dea", "N/A") if isinstance(macd, dict) else "N/A"
        macd_hist = macd.get("macd", "N/A") if isinstance(macd, dict) else "N/A"

        rsi = indicators.get("rsi", "N/A")

        kdj = indicators.get("kdj", {})
        kdj_k = kdj.get("k", "N/A") if isinstance(kdj, dict) else "N/A"
        kdj_d = kdj.get("d", "N/A") if isinstance(kdj, dict) else "N/A"
        kdj_j = kdj.get("j", "N/A") if isinstance(kdj, dict) else "N/A"

        boll = indicators.get("boll", {})
        boll_upper = boll.get("upper", "N/A") if isinstance(boll, dict) else "N/A"
        boll_middle = boll.get("middle", "N/A") if isinstance(boll, dict) else "N/A"
        boll_lower = boll.get("lower", "N/A") if isinstance(boll, dict) else "N/A"

        prompt = f"""你是一名资深的股票投资分析师，拥有 20 年以上的投资研究经验。
请对以下持仓股票进行全面的 AI 分析，给出投资评级和操作建议。

【股票信息】
- 股票代码: {code}
- 市场: {market}
- 货币: {currency}

【实时行情】
- 当前价格: {price}
- 涨跌幅: {change_pct}%
- 成交量: {volume}
- 最高价: {high}
- 最低价: {low}
- 开盘价: {open_price}
- 昨收价: {prev_close}

【持仓信息】
- 持仓数量: {quantity} 股
- 持仓成本: {avg_cost} {currency}
- 当前盈亏: {pnl_pct}% ({pnl_amount} {currency})

【技术指标】
- 均线: MA5={ma5}, MA20={ma20}, MA60={ma60}
- MACD: DIF={macd_dif}, DEA={macd_dea}, MACD柱={macd_hist}
- RSI(14): {rsi}
- KDJ: K={kdj_k}, D={kdj_d}, J={kdj_j}
- 布林带: 上轨={boll_upper}, 中轨={boll_middle}, 下轨={boll_lower}

请基于以上数据进行综合分析。

**请按以下格式输出（先输出详细分析报告，再输出 JSON 结构化数据）：**

---

首先，请输出一份详细的 Markdown 格式分析报告，包含以下内容：
1. **技术面分析**：均线系统、MACD、RSI、KDJ、布林带的综合研判
2. **趋势判断**：当前趋势方向、支撑位和阻力位
3. **持仓建议**：基于当前持仓成本的操作建议
4. **风险提示**：需要关注的风险因素
5. **操作策略**：具体的进出场策略

---

然后，在报告末尾输出以下 JSON 结构化数据（用 ```json``` 包裹）：

```json
{{
    "rating": "评级（强烈买入/买入/增持/持有/观望/减持/卖出/强烈卖出）",
    "confidence": 85,
    "current_price": {price},
    "target_price": 0.0,
    "entry_range": {{
        "min": 0.0,
        "max": 0.0
    }},
    "take_profit": 0.0,
    "stop_loss": 0.0,
    "summary": "分析摘要（200字以内，包含核心逻辑和操作建议）"
}}
```

分析要求：
1. **rating**: 基于技术面、资金面和持仓成本综合给出评级
2. **confidence**: 信心度 0-100，反映分析的确定性
3. **target_price**: 目标价位，基于技术分析和基本面给出合理目标
4. **entry_range**: 建议进场区间（min 和 max），适合加仓或建仓的价格范围
5. **take_profit**: 止盈价位，建议获利了结的价格
6. **stop_loss**: 止损价位，建议止损的价格
7. **summary**: 简洁的分析摘要，包含核心逻辑和操作建议

请确保所有价格字段为数值类型，confidence 为 0-100 的整数。"""

        system_message = (
            "你是一名资深的股票投资分析师，拥有 20 年以上的投资研究经验，擅长技术分析和持仓管理。"
            "请先输出详细的 Markdown 格式分析报告，然后在末尾附上 JSON 结构化数据。"
            "分析报告要专业、详细、有逻辑性。"
        )

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ]

    # ------------------------------------------------------------------
    # 纯函数：解析 LLM 分析结果
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_analysis_result(
        llm_response: str,
        code: str,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        解析 LLM 响应为 Analysis_Result。

        这是一个纯函数（静态方法），不依赖任何外部状态，方便属性测试。
        能处理各种 LLM 响应格式：纯 JSON、markdown 包裹的 JSON、
        带有额外文本的 JSON 等。

        Args:
            llm_response: LLM 原始响应文本
            code: 股票代码
            market_data: 行情数据（用于填充默认值）

        Returns:
            Analysis_Result 字典，包含:
            rating, confidence, current_price, target_price,
            entry_range (min/max), take_profit, stop_loss, summary
        """
        current_price = market_data.get("price", 0.0)

        # 默认结果（确保所有必需字段都存在）
        default_result = {
            "rating": "持有",
            "confidence": 50,
            "current_price": current_price,
            "target_price": current_price,
            "entry_range": {
                "min": current_price * 0.95 if current_price > 0 else 0.0,
                "max": current_price * 1.02 if current_price > 0 else 0.0,
            },
            "take_profit": current_price * 1.10 if current_price > 0 else 0.0,
            "stop_loss": current_price * 0.92 if current_price > 0 else 0.0,
            "summary": "分析结果解析异常，使用默认值。",
        }

        if not llm_response or not llm_response.strip():
            return default_result

        # 提取完整分析报告（JSON 之前的 Markdown 文本）
        full_report = _extract_report_text(llm_response)

        # 尝试从响应中提取 JSON
        parsed_json = _extract_json_from_response(llm_response)

        if parsed_json is None:
            # JSON 提取失败，返回默认结果但保留 LLM 文本作为 summary 和 full_report
            default_result["summary"] = llm_response.strip()[:500]
            default_result["full_report"] = llm_response.strip()
            return default_result

        # 从解析的 JSON 中提取字段，使用默认值兜底
        result = {}

        # rating: 必须是预定义评级之一
        raw_rating = str(parsed_json.get("rating", "持有")).strip()
        result["rating"] = raw_rating if raw_rating in VALID_RATINGS else "持有"

        # confidence: 0-100 的数值
        raw_confidence = parsed_json.get("confidence", 50)
        try:
            confidence = int(float(raw_confidence))
            result["confidence"] = max(0, min(100, confidence))
        except (ValueError, TypeError):
            result["confidence"] = 50

        # current_price: 优先使用行情数据中的真实价格
        result["current_price"] = _safe_positive_float(
            parsed_json.get("current_price"), current_price
        )

        # target_price
        result["target_price"] = _safe_positive_float(
            parsed_json.get("target_price"), current_price
        )

        # entry_range
        entry_range = parsed_json.get("entry_range", {})
        if isinstance(entry_range, dict):
            entry_min = _safe_positive_float(
                entry_range.get("min"), default_result["entry_range"]["min"]
            )
            entry_max = _safe_positive_float(
                entry_range.get("max"), default_result["entry_range"]["max"]
            )
        else:
            entry_min = default_result["entry_range"]["min"]
            entry_max = default_result["entry_range"]["max"]

        # 确保 min <= max
        if entry_min > entry_max:
            entry_min, entry_max = entry_max, entry_min

        result["entry_range"] = {"min": entry_min, "max": entry_max}

        # take_profit
        result["take_profit"] = _safe_positive_float(
            parsed_json.get("take_profit"), default_result["take_profit"]
        )

        # stop_loss
        result["stop_loss"] = _safe_positive_float(
            parsed_json.get("stop_loss"), default_result["stop_loss"]
        )

        # summary
        raw_summary = parsed_json.get("summary", "")
        result["summary"] = str(raw_summary).strip() if raw_summary else "AI 分析完成。"

        # full_report: Markdown 格式的完整分析报告
        result["full_report"] = full_report or result["summary"]

        return result


# ======================================================================
# 模块级纯函数（辅助 _parse_analysis_result）
# ======================================================================


def _extract_report_text(text: str) -> str:
    """
    从 LLM 响应中提取 Markdown 分析报告文本（JSON 代码块之前的部分）。

    LLM 的响应格式为：先输出 Markdown 报告，然后在末尾附上 ```json ... ``` 代码块。
    本函数提取 JSON 代码块之前的所有文本作为完整分析报告。

    Args:
        text: LLM 完整响应文本

    Returns:
        Markdown 格式的分析报告文本，如果无法提取则返回空字符串
    """
    if not text or not text.strip():
        return ""

    # 查找 ```json 代码块的位置，取其之前的文本作为报告
    import re
    json_block_match = re.search(r"```json\s*\n", text)
    if json_block_match:
        report = text[:json_block_match.start()].strip()
        if report:
            return report

    # 查找 ``` 代码块（可能没有 json 标记）
    code_block_match = re.search(r"```\s*\n\s*\{", text)
    if code_block_match:
        report = text[:code_block_match.start()].strip()
        if report:
            return report

    # 查找独立的 JSON 对象（{ 开头），取其之前的文本
    brace_match = re.search(r"\n\s*\{", text)
    if brace_match and brace_match.start() > 100:  # 确保前面有足够的报告文本
        report = text[:brace_match.start()].strip()
        if report:
            return report

    # 如果整个响应都不包含 JSON，则整体作为报告
    return text.strip()


def _extract_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    """
    从 LLM 响应文本中提取 JSON 对象。

    支持多种格式：
    1. ```json ... ``` 包裹的 JSON
    2. ``` ... ``` 包裹的 JSON
    3. 纯 JSON 文本
    4. 文本中嵌入的 JSON 对象

    Args:
        text: LLM 响应文本

    Returns:
        解析后的字典，提取失败返回 None
    """
    if not text or not text.strip():
        return None

    # 策略 1：尝试 ```json ... ``` 包裹
    json_block_match = re.search(
        r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL
    )
    if json_block_match:
        try:
            parsed = json.loads(json_block_match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 策略 2：尝试 ``` ... ``` 包裹（无 json 标记）
    code_block_match = re.search(r"```\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if code_block_match:
        try:
            parsed = json.loads(code_block_match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 策略 3：尝试直接解析整个文本为 JSON
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 策略 4：尝试查找文本中的 JSON 对象（花括号匹配）
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _safe_positive_float(value: Any, default: float = 0.0) -> float:
    """
    安全转换为正浮点数。

    Args:
        value: 待转换的值
        default: 转换失败时的默认值

    Returns:
        正浮点数（>= 0），转换失败返回 default
    """
    if value is None:
        return default
    try:
        result = float(value)
        return result if result >= 0 else default
    except (ValueError, TypeError):
        return default
