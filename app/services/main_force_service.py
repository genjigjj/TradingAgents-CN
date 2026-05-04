"""
主力选股服务

通过 pywencai 获取主力资金净流入排名数据，进行智能筛选，
并提供 AI 分析师团队整体分析、精选推荐等功能。

从 aiagents-stock/main_force_selector.py 迁移并重构为异步服务。
"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from app.models.main_force import (
    MainForceScreenParams,
    MainForceCandidate,
    MainForceScreenResult,
)

logger = logging.getLogger(__name__)


class MainForceService:
    """主力选股服务"""

    def __init__(self, config_service=None):
        """
        初始化主力选股服务。

        Args:
            config_service: ConfigService 实例，用于读取系统配置。
                           如果为 None，则使用默认参数。
        """
        self.config_service = config_service

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def screen_stocks(
        self, params: MainForceScreenParams
    ) -> MainForceScreenResult:
        """
        获取主力资金数据并筛选。

        流程：
        1. 从 Config_Service 读取默认筛选参数（如果 params 使用默认值）
        2. 通过 pywencai 获取主力资金净流入 TOP100
        3. 应用筛选条件（涨跌幅、市值、排除 ST / 科创板）
        4. 返回筛选后的候选股票列表

        Args:
            params: 筛选参数

        Returns:
            MainForceScreenResult 包含原始获取数量、筛选后数量和候选列表
        """
        # 1. 合并配置参数
        effective_params = await self._merge_config_params(params)

        # 2. 计算起始日期
        start_date = self._compute_start_date(effective_params)

        # 3. 构建查询方案
        queries = self._build_queries(
            start_date,
            effective_params.min_market_cap,
            effective_params.max_market_cap,
        )

        # 4. 通过 pywencai 获取数据（同步库，需要 to_thread）
        raw_df = await self._fetch_with_fallback(queries)

        if raw_df is None or raw_df.empty:
            return MainForceScreenResult(
                total_fetched=0,
                total_filtered=0,
                candidates=[],
            )

        total_fetched = len(raw_df)

        # 5. 将 DataFrame 转换为候选列表
        candidates = self._dataframe_to_candidates(raw_df)

        # 6. 应用筛选条件（纯函数）
        filtered = self._filter_candidates(
            candidates,
            max_change_pct=effective_params.max_change_pct,
            min_market_cap=effective_params.min_market_cap,
            max_market_cap=effective_params.max_market_cap,
        )

        return MainForceScreenResult(
            total_fetched=total_fetched,
            total_filtered=len(filtered),
            candidates=filtered,
        )

    # ------------------------------------------------------------------
    # 纯函数：筛选逻辑（静态方法，方便属性测试）
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_candidates(
        candidates: List[MainForceCandidate],
        max_change_pct: float = 30.0,
        min_market_cap: float = 50.0,
        max_market_cap: float = 5000.0,
    ) -> List[MainForceCandidate]:
        """
        对候选股票列表应用筛选条件。

        筛选规则：
        (a) 股票代码不以 "688" 开头（排除科创板）
        (b) 股票名称不含 "ST"（排除 ST 股票）
        (c) |涨跌幅| ≤ max_change_pct
        (d) min_market_cap ≤ 市值 ≤ max_market_cap

        这是一个纯函数，不依赖任何外部状态，方便属性测试。

        Args:
            candidates: 候选股票列表
            max_change_pct: 最大涨跌幅限制（%）
            min_market_cap: 最小市值（亿）
            max_market_cap: 最大市值（亿）

        Returns:
            筛选后的候选股票列表
        """
        result = []
        for c in candidates:
            # (a) 排除科创板
            if c.code.startswith("688"):
                continue
            # (b) 排除 ST 股票
            if "ST" in c.name.upper():
                continue
            # (c) 涨跌幅限制
            if abs(c.change_pct) > max_change_pct:
                continue
            # (d) 市值范围
            if c.market_cap < min_market_cap or c.market_cap > max_market_cap:
                continue
            result.append(c)
        return result

    # ------------------------------------------------------------------
    # 公开方法：AI 整体分析
    # ------------------------------------------------------------------

    async def submit_overview_analysis(
        self,
        user_id: str,
        candidates: List[Dict[str, Any]],
        params: dict,
    ) -> str:
        """
        提交整体分析任务到队列，返回 task_id。

        改造点：
        1. 先生成 task_id，向 analysis_tasks 集合插入完整任务记录
        2. 再将任务入队（传入相同的 task_id）
        3. 写入 Redis 初始进度（使用相同的 task_id）

        通过 QueueService.enqueue_task() 将任务入队，
        task_type="main_force_overview"，由 AnalysisWorker 消费后
        路由到 execute_overview_analysis()。

        Args:
            user_id: 用户 ID
            candidates: 候选股票列表（字典格式）
            params: 分析参数（包含 top_n 等）

        Returns:
            task_id 字符串
        """
        from app.services.queue_service import get_queue_service
        from app.core.database import get_mongo_db

        # 先生成 task_id，确保 DB 记录、Redis 进度和队列任务使用同一个 ID
        task_id = str(uuid.uuid4())

        # 写入 analysis_tasks 集合，创建完整的任务记录
        db = get_mongo_db()
        task_record = {
            "task_id": task_id,
            "user_id": user_id,
            "symbol": "main_force_overview",
            "status": "pending",
            "task_type": "main_force_overview",
            "parameters": {
                "candidates_count": len(candidates),
                "top_n": params.get("top_n", 5),
                "model_name": params.get("model_name"),
                **params,
            },
            "progress": 0,
            "created_at": datetime.utcnow(),
        }
        await db.analysis_tasks.insert_one(task_record)

        # 构建队列参数
        queue_service = get_queue_service()
        queue_params: Dict[str, Any] = {
            "task_type": "main_force_overview",
            "candidates": candidates,
            "analysis_params": params,
            "user_id": user_id,
        }

        # 入队时传入预先生成的 task_id，保证一致性
        await queue_service.enqueue_task(
            user_id=user_id,
            symbol="main_force_overview",
            params=queue_params,
            task_id=task_id,
        )

        # 立即写入初始进度到 Redis，避免前端轮询时找不到任务
        await self._write_initial_progress(task_id, "main_force_overview", "主力选股分析任务已入队，等待执行...")

        logger.info(f"主力选股整体分析任务已入队: {task_id}")
        return task_id

    @staticmethod
    async def _write_initial_progress(task_id: str, task_type: str, message: str) -> None:
        """
        入队后立即写入初始进度到 Redis / 文件，
        让前端轮询能立刻感知到任务存在（状态 = pending）。
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
                    host=redis_host, port=redis_port,
                    password=redis_password or None, db=redis_db,
                    decode_responses=True,
                )
                key = f"progress:{task_id}"
                rc.set(key, _json.dumps(progress_data))
                rc.expire(key, 3600)
                logger.info(f"✅ 初始进度已写入 Redis: {task_id}")
                return

            # Redis 未启用时写文件
            os.makedirs("./data/progress", exist_ok=True)
            with open(f"./data/progress/{task_id}.json", "w", encoding="utf-8") as f:
                _json.dump(progress_data, f)
            logger.info(f"✅ 初始进度已写入文件: {task_id}")
        except Exception as e:
            logger.warning(f"⚠️ 写入初始进度失败: {task_id} - {e}")

    async def execute_overview_analysis(
        self,
        task_id: str,
        candidates: List[Dict[str, Any]],
        params: dict,
    ) -> Dict[str, Any]:
        """
        执行整体分析（由 AnalysisWorker 调用）。

        流程：
        1. 初始化 RedisProgressTracker
        2. 调用资金流向分析师
        3. 调用行业板块分析师
        4. 调用财务基本面分析师
        5. 调用综合研究员精选推荐
        6. 持久化分析结果到 MongoDB
        7. 通过 RedisProgressTracker 更新进度

        Args:
            task_id: 任务 ID
            candidates: 候选股票列表
            params: 分析参数

        Returns:
            分析结果字典
        """
        from app.services.redis_progress_tracker import RedisProgressTracker
        from app.core.database import get_mongo_db

        top_n = params.get("top_n", 5)

        # 初始化进度跟踪器
        progress_tracker = RedisProgressTracker(
            task_id=task_id,
            analysts=["fund_flow", "industry", "fundamental"],
            research_depth="标准",
            llm_provider="dashscope",
        )

        # 同步更新 DB 状态为 processing（任务 4.1）
        await self._sync_task_status(task_id, "running", 0, "开始整体分析")

        result: Dict[str, Any] = {
            "task_id": task_id,
            "success": False,
            "overview_analysis": {},
            "recommended_stocks": [],
            "error": None,
        }

        # 跟踪当前步骤名称和进度，用于失败时构建结构化错误信息
        current_step = "初始化"
        current_progress = 0

        try:
            # 获取 LLM 客户端（优先使用用户指定的模型）
            specified_model = params.get("model_name")
            logger.info(f"[主力选股] execute_overview_analysis params keys={list(params.keys())}, model_name={specified_model}")
            llm_call = await self._get_llm_call_func(specified_model=specified_model)

            # 准备整体数据摘要
            data_summary = self._prepare_candidates_summary(candidates)
            data_table = self._prepare_candidates_table(candidates)

            # ---- 步骤 1/4: 资金流向分析师 ----
            current_step = "资金流向分析师"
            current_progress = 10
            progress_tracker.update_progress({
                "progress_percentage": 10,
                "last_message": "💰 资金流向分析师整体分析中...",
            })

            fund_flow_report = await self._call_fund_flow_analyst(
                llm_call, data_summary, data_table, len(candidates)
            )
            result["overview_analysis"]["fund_flow_analyst"] = fund_flow_report

            current_progress = 30
            progress_tracker.update_progress({
                "progress_percentage": 30,
                "last_message": "✅ 资金流向分析完成",
            })
            # 同步更新 DB 进度（任务 4.2）
            await self._sync_task_status(task_id, "running", 30, "资金流向分析完成")

            # ---- 步骤 2/4: 行业板块分析师 ----
            current_step = "行业板块分析师"
            current_progress = 35
            progress_tracker.update_progress({
                "progress_percentage": 35,
                "last_message": "📊 行业板块分析师整体分析中...",
            })

            industry_report = await self._call_industry_analyst(
                llm_call, data_summary, data_table, len(candidates)
            )
            result["overview_analysis"]["industry_analyst"] = industry_report

            current_progress = 55
            progress_tracker.update_progress({
                "progress_percentage": 55,
                "last_message": "✅ 行业板块分析完成",
            })
            # 同步更新 DB 进度（任务 4.2）
            await self._sync_task_status(task_id, "running", 55, "行业板块分析完成")

            # ---- 步骤 3/4: 财务基本面分析师 ----
            current_step = "财务基本面分析师"
            current_progress = 60
            progress_tracker.update_progress({
                "progress_percentage": 60,
                "last_message": "📈 财务基本面分析师整体分析中...",
            })

            fundamental_report = await self._call_fundamental_analyst(
                llm_call, data_summary, data_table, len(candidates)
            )
            result["overview_analysis"]["fundamental_analyst"] = fundamental_report

            current_progress = 75
            progress_tracker.update_progress({
                "progress_percentage": 75,
                "last_message": "✅ 财务基本面分析完成",
            })
            # 同步更新 DB 进度（任务 4.2）
            await self._sync_task_status(task_id, "running", 75, "财务基本面分析完成")

            # ---- 步骤 4/4: 综合研究员精选推荐 ----
            current_step = "综合研究员"
            current_progress = 80
            progress_tracker.update_progress({
                "progress_percentage": 80,
                "last_message": "👔 综合研究员精选推荐中...",
            })

            comprehensive_report, recommended_stocks = await self._call_comprehensive_researcher(
                llm_call,
                data_table,
                fund_flow_report,
                industry_report,
                fundamental_report,
                len(candidates),
                top_n,
            )
            result["overview_analysis"]["comprehensive_researcher"] = comprehensive_report
            result["recommended_stocks"] = recommended_stocks

            current_progress = 90
            # 同步更新 DB 进度（任务 4.2）
            await self._sync_task_status(task_id, "running", 90, "综合研究员分析完成")

            # ---- 持久化到 MongoDB ----
            current_step = "持久化"
            current_progress = 95
            progress_tracker.update_progress({
                "progress_percentage": 95,
                "last_message": "💾 保存分析结果...",
            })

            try:
                await self._save_overview_result(task_id, candidates, params, result)
            except Exception as save_err:
                # 数据库持久化失败场景（任务 4.4）
                error_msg = f"[持久化] 保存分析结果失败: {save_err}"
                logger.error(error_msg)
                result["error"] = error_msg
                progress_tracker.mark_failed(error_msg)
                await self._sync_task_status(task_id, "failed", current_progress, error=error_msg)
                return result

            # 同步更新 DB 进度（任务 4.2）
            await self._sync_task_status(task_id, "running", 95, "分析结果已保存")

            result["success"] = True

            # 标记完成
            progress_tracker.mark_completed()
            logger.info(f"主力选股整体分析完成: {task_id}")

            # 同步更新 DB 状态为 completed（任务 4.3）
            await self._sync_task_status(task_id, "completed", 100, result=result)

        except json.JSONDecodeError as je:
            # JSON 解析失败场景（任务 4.4）
            response_text = str(je.doc) if hasattr(je, 'doc') and je.doc else ""
            error_msg = f"[{current_step}] JSON 解析失败: {je}; 原始响应片段: {response_text[:200]}"
            logger.error(error_msg)
            result["error"] = error_msg
            progress_tracker.mark_failed(error_msg)
            await self._sync_task_status(task_id, "failed", current_progress, error=error_msg)

        except Exception as e:
            # LLM 调用失败等通用异常场景（任务 4.4）
            error_msg = f"[{current_step}] LLM 调用失败: {e}"
            logger.error(error_msg)
            result["error"] = error_msg
            progress_tracker.mark_failed(error_msg)
            await self._sync_task_status(task_id, "failed", current_progress, error=error_msg)

        return result

    # ------------------------------------------------------------------
    # 内部方法：LLM 调用
    # ------------------------------------------------------------------

    async def _get_llm_call_func(self, specified_model: str = None):
        """
        获取 LLM 调用函数。

        通过 unified_llm_service 统一获取模型配置（Requirements: 4.7）。

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

        # 方式 1：用户在前端指定了模型 → 通过 unified_llm_service 获取配置
        logger.info(f"[主力选股 LLM] specified_model={specified_model}")
        if specified_model:
            try:
                merged_config = await unified_llm_service.get_model_config(specified_model)
                if merged_config and merged_config.api_key:
                    key_hint = f"{merged_config.api_key[:6]}...{merged_config.api_key[-4:]}" if len(merged_config.api_key) > 10 else "***"
                    logger.info(f"[主力选股 LLM] ✅ 使用用户指定模型: model={merged_config.model_name}, base={merged_config.api_base}, key={key_hint}, source=unified_llm_service")
                else:
                    logger.warning(f"[主力选股 LLM] 用户指定模型 {specified_model} 无有效配置，尝试其他方式")
                    merged_config = None
            except Exception as e:
                logger.warning(f"[主力选股 LLM] 通过 unified_llm_service 查找用户指定模型 {specified_model} 失败: {e}")

        # 方式 2：通过 unified_llm_service.recommend_model 推荐模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.recommend_model(
                    task_type="screening", depth="标准"
                )
                if merged_config:
                    logger.info(f"[主力选股 LLM] ✅ 使用推荐模型: model={merged_config.model_name}, base={merged_config.api_base}, source=unified_llm_service.recommend_model")
            except Exception as e:
                logger.warning(f"[主力选股 LLM] unified_llm_service.recommend_model 获取失败: {e}")

        # 方式 3：通过 unified_llm_service.get_default_model 获取默认模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.get_default_model()
                if merged_config:
                    logger.info(f"[主力选股 LLM] ✅ 使用默认模型: model={merged_config.model_name}, base={merged_config.api_base}, source=unified_llm_service.get_default_model")
            except Exception as e:
                logger.warning(f"[主力选股 LLM] unified_llm_service.get_default_model 获取失败: {e}")

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
            api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
            api_base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            model_name = model_name or os.getenv("DEFAULT_MODEL", "qwen-plus")
            logger.info(f"[主力选股 LLM] 回退到环境变量: model={model_name}, base={api_base}")

        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        logger.info(f"[主力选股 LLM] 最终配置: model={model_name}, base={api_base}")

        async def llm_call(messages: List[Dict[str, str]], max_tokens: int = None) -> str:
            """调用 LLM 模型"""
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens or max_tokens_default,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}")
                raise

        return llm_call

    # ------------------------------------------------------------------
    # 内部方法：四位分析师
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_candidates_summary(candidates: List[Dict[str, Any]]) -> str:
        """准备候选股票的整体数据摘要"""
        lines = [f"候选股票总数: {len(candidates)}只"]

        # 主力资金统计
        inflows = [c.get("net_inflow", 0) for c in candidates if c.get("net_inflow")]
        if inflows:
            total = sum(inflows)
            avg = total / len(inflows)
            lines.append(f"主力资金总净流入: {total / 1e8:.2f}亿")
            lines.append(f"平均主力资金净流入: {avg / 1e8:.2f}亿")

        # 涨跌幅统计
        changes = [c.get("change_pct", 0) for c in candidates if c.get("change_pct") is not None]
        if changes:
            lines.append(f"平均涨跌幅: {sum(changes) / len(changes):.2f}%")
            lines.append(f"涨跌幅范围: {min(changes):.2f}% ~ {max(changes):.2f}%")

        # 行业分布
        industries: Dict[str, int] = {}
        for c in candidates:
            ind = c.get("industry", "未知")
            industries[ind] = industries.get(ind, 0) + 1
        if industries:
            sorted_ind = sorted(industries.items(), key=lambda x: x[1], reverse=True)[:10]
            lines.append("\n主要行业分布:")
            for ind, cnt in sorted_ind:
                lines.append(f"  - {ind}: {cnt}只")

        return "\n".join(lines)

    @staticmethod
    def _prepare_candidates_table(candidates: List[Dict[str, Any]], max_rows: int = 50) -> str:
        """准备候选股票的数据表格（文本格式）"""
        header = f"{'代码':<8} {'名称':<10} {'行业':<12} {'主力净流入(亿)':<14} {'涨跌幅(%)':<10} {'市值(亿)':<10} {'市盈率':<8} {'市净率':<8}"
        lines = [header, "-" * 90]

        for c in candidates[:max_rows]:
            code = c.get("code", "N/A")
            name = str(c.get("name", "N/A"))[:8]
            industry = str(c.get("industry", "N/A"))[:10]
            inflow = c.get("net_inflow", 0)
            inflow_str = f"{inflow / 1e8:.2f}" if inflow else "N/A"
            change = c.get("change_pct")
            change_str = f"{change:.2f}" if change is not None else "N/A"
            mcap = c.get("market_cap")
            mcap_str = f"{mcap:.1f}" if mcap is not None else "N/A"
            pe = c.get("pe_ratio")
            pe_str = f"{pe:.1f}" if pe is not None else "N/A"
            pb = c.get("pb_ratio")
            pb_str = f"{pb:.2f}" if pb is not None else "N/A"
            lines.append(f"{code:<8} {name:<10} {industry:<12} {inflow_str:<14} {change_str:<10} {mcap_str:<10} {pe_str:<8} {pb_str:<8}")

        if len(candidates) > max_rows:
            lines.append(f"... 还有 {len(candidates) - max_rows} 只股票未显示")

        return "\n".join(lines)

    async def _call_fund_flow_analyst(
        self,
        llm_call,
        summary: str,
        data_table: str,
        count: int,
    ) -> str:
        """调用资金流向分析师"""
        prompt = f"""你是一名资深的资金面分析师，现在需要你从整体角度分析这批主力资金净流入的股票。

【整体数据摘要】
{summary}

【候选股票详细数据】（共{count}只）
{data_table}

【分析任务】
请从资金流向的整体角度进行分析，重点关注：

1. **资金流向特征**
   - 哪些板块/行业资金流入最集中？
   - 主力资金的整体行为特征（大规模建仓/试探性进场/板块轮动）
   - 资金流向与涨跌幅的配合情况

2. **优质标的识别**
   - 从资金面角度，哪些股票最值得关注？
   - 主力资金流入大但涨幅不高的潜力股
   - 资金持续流入且趋势明确的股票

3. **板块热点判断**
   - 当前资金最看好哪些板块？
   - 是否有板块轮动迹象？
   - 新兴热点 vs 传统强势板块

4. **投资建议**
   - 从资金面角度，建议重点关注哪3-5只股票？
   - 理由和风险提示

请给出专业、系统的资金面整体分析报告。"""

        messages = [
            {"role": "system", "content": "你是资金面分析专家，擅长从整体资金流向中发现投资机会。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_industry_analyst(
        self,
        llm_call,
        summary: str,
        data_table: str,
        count: int,
    ) -> str:
        """调用行业板块分析师"""
        prompt = f"""你是一名资深的行业板块分析师，现在需要你从行业热点和板块轮动角度分析这批股票。

【整体数据摘要】
{summary}

【候选股票详细数据】（共{count}只）
{data_table}

【分析任务】
请从行业板块的整体角度进行分析，重点关注：

1. **热点板块识别**
   - 哪些行业/板块最受资金青睐？
   - 热点板块的持续性如何？
   - 是否有新兴热点正在形成？

2. **板块特征分析**
   - 各板块的涨幅与资金流入匹配度
   - 哪些板块处于启动阶段（资金流入但涨幅不大）
   - 哪些板块可能过热（涨幅高但资金流入减弱）

3. **行业前景评估**
   - 主力资金集中的行业，基本面支撑如何？
   - 政策面、产业面是否有催化因素？
   - 行业竞争格局和龙头地位

4. **优质标的推荐**
   - 从行业板块角度，推荐3-5只最具潜力的股票
   - 推荐理由（行业地位、成长空间、催化因素）

请给出专业、深入的行业板块分析报告。"""

        messages = [
            {"role": "system", "content": "你是行业板块分析专家，擅长发现市场热点和板块机会。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_fundamental_analyst(
        self,
        llm_call,
        summary: str,
        data_table: str,
        count: int,
    ) -> str:
        """调用财务基本面分析师"""
        prompt = f"""你是一名资深的基本面分析师，现在需要你从财务质量和基本面角度分析这批股票。

【整体数据摘要】
{summary}

【候选股票详细数据】（共{count}只）
{data_table}

【分析任务】
请从财务基本面的整体角度进行分析，重点关注：

1. **财务质量评估**
   - 整体财务指标健康度如何？
   - 哪些股票盈利能力、成长性突出？
   - 是否存在财务风险较大的股票？

2. **估值水平分析**
   - 市盈率、市净率的整体分布
   - 哪些股票估值合理且有成长空间？
   - 高估值是否有业绩支撑？

3. **成长性评估**
   - 营收、净利润增长情况
   - 哪些股票成长性最好？

4. **优质标的筛选**
   - 从基本面角度，推荐3-5只最优质的股票
   - 推荐理由（财务健康、估值合理、成长性好）

请给出专业、详实的基本面分析报告。"""

        messages = [
            {"role": "system", "content": "你是基本面分析专家，擅长从财务角度评估投资价值。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_comprehensive_researcher(
        self,
        llm_call,
        data_table: str,
        fund_analysis: str,
        industry_analysis: str,
        fundamental_analysis: str,
        count: int,
        top_n: int,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        调用综合研究员精选推荐。

        Returns:
            (综合报告文本, 推荐股票列表)
        """
        prompt = f"""你是一名资深股票研究员，具有20年以上的投资研究经验。现在需要你综合三位分析师的意见，
从{count}只候选股票中精选出{top_n}只最具投资价值的优质标的。

【候选股票数据】
{data_table}

【资金流向分析师观点】
{fund_analysis}

【行业板块分析师观点】
{industry_analysis}

【财务基本面分析师观点】
{fundamental_analysis}

【筛选标准】
1. **主力资金**: 主力资金净流入较多，显示机构看好
2. **涨幅适中**: 区间涨跌幅不是很高（避免追高），还有上涨空间
3. **行业热点**: 所属行业有发展前景，是市场热点
4. **基本面良好**: 财务指标健康，盈利能力强
5. **综合平衡**: 资金、行业、基本面三方面都不错

【任务要求】
综合三位分析师的观点，精选出{top_n}只最优标的。

对于每只精选股票，请提供：
1. **股票代码和名称**
2. **核心推荐理由**（3-5条，综合资金、行业、基本面）
3. **投资亮点**（最突出的优势）
4. **风险提示**（需要注意的风险）
5. **建议仓位**（如20-30%）
6. **投资周期**（短期/中期/长期）

请按以下JSON格式输出（只输出JSON，不要其他内容）：
```json
{{
  "summary": "综合分析总结（200字以内）",
  "recommendations": [
    {{
      "rank": 1,
      "code": "股票代码",
      "name": "股票名称",
      "reason": "核心推荐理由",
      "highlights": ["亮点1", "亮点2"],
      "risks": ["风险1", "风险2"],
      "position": "建议仓位",
      "period": "投资周期"
    }}
  ]
}}
```

注意：
- 必须严格按照JSON格式输出
- 推荐数量为{top_n}只
- 按投资价值从高到低排序
"""

        messages = [
            {"role": "system", "content": "你是资深股票研究员，擅长综合多维度分析做出投资决策。请严格按照要求的JSON格式输出。"},
            {"role": "user", "content": prompt},
        ]

        response_text = await llm_call(messages, max_tokens=4000)

        # 解析推荐股票
        recommended_stocks = self._parse_recommendations(response_text)

        return response_text, recommended_stocks

    @staticmethod
    def _parse_recommendations(response_text: str) -> List[Dict[str, Any]]:
        """从综合研究员的响应中解析推荐股票列表"""
        try:
            # 尝试提取 JSON 块
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接查找 JSON 对象
                json_match = re.search(r"\{[\s\S]*\"recommendations\"[\s\S]*\}", response_text)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    logger.warning("未能从综合研究员响应中提取 JSON")
                    return []

            data = json.loads(json_str)
            recommendations = data.get("recommendations", [])

            # 标准化字段
            result = []
            for rec in recommendations:
                item = {
                    "code": rec.get("code", rec.get("symbol", "")),
                    "name": rec.get("name", ""),
                    "reason": rec.get("reason", ""),
                    "highlights": rec.get("highlights", []),
                    "risks": rec.get("risks", []),
                    "position": rec.get("position", ""),
                    "period": rec.get("period", rec.get("investment_period", "")),
                }
                # 兼容 reasons 列表格式
                if not item["reason"] and "reasons" in rec:
                    reasons = rec["reasons"]
                    if isinstance(reasons, list):
                        item["reason"] = "；".join(reasons)
                # 兼容 highlights/risks 为字符串
                if isinstance(item["highlights"], str):
                    item["highlights"] = [item["highlights"]]
                if isinstance(item["risks"], str):
                    item["risks"] = [item["risks"]]
                result.append(item)

            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"解析推荐股票 JSON 失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 内部方法：持久化
    # ------------------------------------------------------------------

    async def _save_overview_result(
        self,
        task_id: str,
        candidates: List[Dict[str, Any]],
        params: dict,
        result: Dict[str, Any],
    ) -> None:
        """将整体分析结果持久化到 MongoDB"""
        try:
            from app.core.database import get_mongo_db
            from app.utils.timezone import now_tz

            db = get_mongo_db()
            timestamp = now_tz()

            # 更新 analysis_tasks 集合中的任务记录
            # 注意：status、completed_at 由 _sync_task_status 统一管理，
            # 这里只写入持久化相关的补充字段，避免状态覆盖竞态
            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {
                    "$set": {
                        "task_type": "main_force_overview",
                        "candidates_count": len(candidates),
                        "params": params,
                    }
                },
                upsert=True,
            )

            # 保存到 main_force_batch_history 集合
            history_doc = {
                "analysis_date": timestamp,
                "params": params,
                "candidates_count": len(candidates),
                "filtered_count": len(candidates),
                "recommended_count": len(result.get("recommended_stocks", [])),
                "overview_analysis": result.get("overview_analysis", {}),
                "recommended_stocks": result.get("recommended_stocks", []),
                "batch_results": {},
                "task_id": task_id,
                "created_at": timestamp,
            }
            await db.main_force_batch_history.insert_one(history_doc)

            # 保存到 analysis_reports 集合（与单股分析保持一致，便于在报告列表页查询）
            from app.routers.reports import _format_comprehensive_researcher_report

            overview = result.get("overview_analysis", {})
            recommended = result.get("recommended_stocks", [])

            # 将各分析师报告映射为 reports 字段
            reports_map = {}
            for key, content in overview.items():
                if isinstance(content, str) and content.strip():
                    if key == "comprehensive_researcher":
                        reports_map[key] = _format_comprehensive_researcher_report(content, recommended)
                    else:
                        reports_map[key] = content

            # 将推荐股票列表格式化为 Markdown
            if recommended:
                rec_lines = ["# 精选推荐\n"]
                for i, stock in enumerate(recommended, 1):
                    rec_lines.append(f"## {i}. {stock.get('name', '')} ({stock.get('code', '')})")
                    if stock.get("reason"):
                        rec_lines.append(f"**推荐理由：** {stock['reason']}")
                    if stock.get("highlights"):
                        highlights = stock["highlights"] if isinstance(stock["highlights"], list) else [stock["highlights"]]
                        rec_lines.append("**投资亮点：**")
                        for h in highlights:
                            rec_lines.append(f"- {h}")
                    if stock.get("risks"):
                        risks = stock["risks"] if isinstance(stock["risks"], list) else [stock["risks"]]
                        rec_lines.append("**风险提示：**")
                        for risk in risks:
                            rec_lines.append(f"- {risk}")
                    rec_lines.append(f"- 建议仓位：{stock.get('position', '-')}")
                    rec_lines.append(f"- 投资周期：{stock.get('period', '-')}")
                    rec_lines.append("")
                reports_map["recommended_stocks"] = "\n".join(rec_lines)

            # 生成摘要
            # 生成摘要：将综合研究员的 JSON 转换为可读文本
            raw_summary = overview.get("comprehensive_researcher", "")
            if raw_summary:
                formatted_summary = _format_comprehensive_researcher_report(raw_summary, recommended)
                summary = formatted_summary
            else:
                summary = "主力选股整体分析报告"

            report_doc = {
                "analysis_id": f"mf_{task_id}",
                "stock_symbol": "main_force_overview",
                "stock_name": "主力选股整体分析",
                "market_type": "A股",
                "model_info": params.get("model_name", "Unknown"),
                "analysis_date": timestamp.strftime('%Y-%m-%d') if hasattr(timestamp, 'strftime') else str(timestamp)[:10],
                "timestamp": timestamp,
                "status": "completed",
                "source": "main_force",
                "summary": summary,
                "analysts": list(overview.keys()),
                "research_depth": "标准",
                "reports": reports_map,
                "decision": {},
                "created_at": timestamp,
                "updated_at": timestamp,
                "task_id": task_id,
                "task_type": "main_force_overview",
                "recommended_stocks": recommended,
                "recommendation": f"精选推荐 {len(recommended)} 只股票" if recommended else "暂无推荐",
                "confidence_score": 0.0,
                "risk_level": "中等",
                "key_points": [f"推荐 {s.get('name', '')}({s.get('code', '')})" for s in recommended[:5]],
                "execution_time": 0,
                "tokens_used": 0,
            }
            await db.analysis_reports.insert_one(report_doc)
            logger.info(f"✅ 主力选股报告已保存到 analysis_reports: {task_id}")

            logger.info(f"主力选股分析结果已持久化: {task_id}")

        except Exception as e:
            logger.error(f"持久化主力选股分析结果失败: {e}")

    # ------------------------------------------------------------------
    # 内部方法：同步任务状态到 MongoDB
    # ------------------------------------------------------------------

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

        在每个关键节点调用，将任务的最新状态、进度、结果或错误信息
        写入 MongoDB，供任务中心前端查询。

        Args:
            task_id: 任务 ID
            status: 任务状态（pending / processing / completed / failed）
            progress: 进度百分比（0-100）
            message: 当前步骤描述信息
            result: 任务结果（成功完成时传入）
            error: 错误信息（失败时传入）
        """
        try:
            from app.core.database import get_mongo_db

            db = get_mongo_db()

            # 构建更新字段
            update_data: Dict[str, Any] = {
                "status": status,
                "progress": progress,
            }

            # 开始执行时记录 started_at
            if status in ("processing", "running"):
                update_data["started_at"] = datetime.utcnow()

            # 完成或失败时记录 completed_at
            if status in ("completed", "failed"):
                update_data["completed_at"] = datetime.utcnow()

            # 写入结果
            if result is not None:
                update_data["result"] = result

            # 写入错误信息
            if error is not None:
                update_data["last_error"] = error

            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": update_data},
            )

            if status == "completed" and result is not None:
                logger.info(f"✅ 任务状态同步成功（含 result）: task_id={task_id}, result_keys={list(result.keys()) if isinstance(result, dict) else 'non-dict'}")

        except Exception as e:
            logger.error(f"同步任务状态到 MongoDB 失败: task_id={task_id}, status={status}, error={e}")

    # ------------------------------------------------------------------
    # 内部方法：配置合并
    # ------------------------------------------------------------------

    async def _merge_config_params(
        self, params: MainForceScreenParams
    ) -> MainForceScreenParams:
        """
        从 Config_Service 读取默认筛选参数并与用户参数合并。

        Config_Service 的 system_settings 中可配置：
        - main_force_default_time_range: 默认时间区间（如 "3m"）
        - main_force_default_top_n: 默认精选数量
        - main_force_default_max_change_pct: 默认最大涨跌幅
        - main_force_default_min_market_cap: 默认最小市值
        - main_force_default_max_market_cap: 默认最大市值

        用户显式传入的参数优先于配置默认值。
        """
        if self.config_service is None:
            return params

        try:
            settings = await self.config_service.get_system_settings()
            if not settings:
                return params

            # 仅在用户使用默认值时才用配置覆盖
            merged = params.model_copy()

            # 时间区间
            if params.time_range == "3m" and "main_force_default_time_range" in settings:
                merged.time_range = settings["main_force_default_time_range"]

            # 精选数量
            if params.top_n == 5 and "main_force_default_top_n" in settings:
                merged.top_n = int(settings["main_force_default_top_n"])

            # 最大涨跌幅
            if params.max_change_pct == 30.0 and "main_force_default_max_change_pct" in settings:
                merged.max_change_pct = float(settings["main_force_default_max_change_pct"])

            # 最小市值
            if params.min_market_cap == 50.0 and "main_force_default_min_market_cap" in settings:
                merged.min_market_cap = float(settings["main_force_default_min_market_cap"])

            # 最大市值
            if params.max_market_cap == 5000.0 and "main_force_default_max_market_cap" in settings:
                merged.max_market_cap = float(settings["main_force_default_max_market_cap"])

            return merged
        except Exception as e:
            logger.warning(f"读取配置参数失败，使用用户传入参数: {e}")
            return params

    # ------------------------------------------------------------------
    # 内部方法：日期计算
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_start_date(params: MainForceScreenParams) -> str:
        """
        根据参数计算起始日期，返回中文格式（如 "2025年1月1日"）。

        支持的 time_range 值：
        - "3m": 最近 3 个月
        - "6m": 最近 6 个月
        - "1y": 最近 1 年
        - "custom": 使用 start_date 字段
        """
        if params.time_range == "custom" and params.start_date:
            # 将 YYYY-MM-DD 转换为中文格式
            try:
                dt = datetime.strptime(params.start_date, "%Y-%m-%d")
                return f"{dt.year}年{dt.month}月{dt.day}日"
            except ValueError:
                pass

        # 根据 time_range 计算天数
        days_map = {"3m": 90, "6m": 180, "1y": 365}
        days = days_map.get(params.time_range, 90)
        dt = datetime.now() - timedelta(days=days)
        return f"{dt.year}年{dt.month}月{dt.day}日"

    # ------------------------------------------------------------------
    # 内部方法：构建查询方案
    # ------------------------------------------------------------------

    @staticmethod
    def _build_queries(
        start_date: str,
        min_market_cap: float,
        max_market_cap: float,
    ) -> List[str]:
        """
        构建 pywencai 查询方案列表（最多 4 个备选方案）。

        方案按优先级排列：
        1. 完整查询（包含评分字段）
        2. 简化查询（包含基本财务字段）
        3. 基础查询（仅行业和市值）
        4. 最简查询（最小字段集）

        Args:
            start_date: 中文格式起始日期
            min_market_cap: 最小市值（亿）
            max_market_cap: 最大市值（亿）

        Returns:
            查询语句列表
        """
        return [
            # 方案1: 完整查询（最优）
            (
                f"{start_date}以来主力资金净流入排名，并计算区间涨跌幅，"
                f"市值{min_market_cap}-{max_market_cap}亿之间，非科创非st，"
                f"所属同花顺行业，总市值，净利润，营收，市盈率，市净率，"
                f"盈利能力评分，成长能力评分，营运能力评分，偿债能力评分，"
                f"现金流评分，资产质量评分，流动性评分，资本充足性评分"
            ),
            # 方案2: 简化查询
            (
                f"{start_date}以来主力资金净流入，并计算区间涨跌幅，"
                f"市值{min_market_cap}-{max_market_cap}亿，非科创非st，"
                f"所属同花顺行业，总市值，净利润，营收，市盈率，市净率"
            ),
            # 方案3: 基础查询
            (
                f"{start_date}以来主力资金净流入排名，并计算区间涨跌幅，"
                f"市值{min_market_cap}-{max_market_cap}亿，非科创非st，"
                f"所属行业，总市值"
            ),
            # 方案4: 最简查询
            (
                f"{start_date}以来主力资金净流入前100名，并计算区间涨跌幅，"
                f"市值{min_market_cap}-{max_market_cap}亿，非st非科创板，"
                f"所属行业，总市值"
            ),
        ]

    # ------------------------------------------------------------------
    # 公开方法：批量深度分析
    # ------------------------------------------------------------------

    async def submit_batch_analysis(
        self,
        user_id: str,
        symbols: List[str],
        params: dict,
    ) -> str:
        """
        提交批量深度分析任务，返回 batch_id。

        复用 AnalysisService.submit_batch_analysis() 的流程：
        1. 生成 batch_id
        2. 为每只股票创建 AnalysisTask 并入队
        3. 任务参数中标记 task_type="main_force_batch"

        Args:
            user_id: 用户 ID
            symbols: 股票代码列表
            params: 分析参数（包含 research_depth、selected_analysts 等）

        Returns:
            batch_id 字符串
        """
        from app.models.analysis import (
            AnalysisTask,
            AnalysisBatch,
            AnalysisParameters,
            AnalysisStatus,
            BatchStatus,
            BatchAnalysisRequest,
        )
        from app.services.analysis_service import AnalysisService

        # 构建 BatchAnalysisRequest 并委托给 AnalysisService
        analysis_service = AnalysisService()

        # 构建分析参数
        analysis_params = AnalysisParameters(
            research_depth=params.get("research_depth", "标准"),
            selected_analysts=params.get("selected_analysts", ["market", "fundamentals"]),
        )

        batch_request = BatchAnalysisRequest(
            title=params.get("title", f"主力选股批量深度分析 - {len(symbols)}只"),
            description=params.get("description", "主力选股批量深度分析"),
            symbols=symbols,
            parameters=analysis_params,
        )

        # 调用 AnalysisService 的批量分析方法
        result = await analysis_service.submit_batch_analysis(
            user_id=user_id,
            request=batch_request,
        )

        batch_id = result.get("batch_id", "")

        # 更新队列中每个任务的 task_type 为 main_force_batch
        # 通过 Redis 直接更新任务参数中的 task_type
        try:
            from app.core.database import get_redis_client
            import json as _json

            redis = get_redis_client()
            batch_tasks_key = f"batch_tasks:{batch_id}"
            task_ids = await redis.smembers(batch_tasks_key)

            for task_id in task_ids:
                task_key = f"task:{task_id}"
                raw_params = await redis.hget(task_key, "params")
                if raw_params:
                    task_params = _json.loads(raw_params)
                    task_params["task_type"] = "main_force_batch"
                    # 传递主力选股的额外参数
                    task_params["main_force_params"] = params
                    await redis.hset(task_key, "params", _json.dumps(task_params))
        except Exception as e:
            logger.warning(f"更新批量任务 task_type 失败: {e}")

        logger.info(f"主力选股批量深度分析已提交: batch_id={batch_id}, 共 {len(symbols)} 只股票")
        return batch_id

    async def execute_single_deep_analysis(
        self,
        task_id: str,
        symbol: str,
        params: dict,
    ) -> Dict[str, Any]:
        """
        执行单股深度分析（由 AnalysisWorker 调用）。

        流程：
        1. 初始化 RedisProgressTracker
        2. 调用 LLM 进行深度分析
        3. 提取投资评级、信心度、关键价位
        4. 更新进度并返回结果

        Args:
            task_id: 任务 ID
            symbol: 股票代码
            params: 分析参数

        Returns:
            分析结果字典，包含 rating、confidence、entry_range、
            take_profit、stop_loss、target_price、advice 等
        """
        from app.services.redis_progress_tracker import RedisProgressTracker

        # 初始化进度跟踪器
        progress_tracker = RedisProgressTracker(
            task_id=task_id,
            analysts=["deep_analysis"],
            research_depth="深度",
            llm_provider="dashscope",
        )

        # 同步更新 DB 状态为 processing（任务 5.1）
        await self._sync_task_status(task_id, "running", 10, f"开始深度分析 {symbol}")

        result: Dict[str, Any] = {
            "task_id": task_id,
            "symbol": symbol,
            "success": False,
            "rating": None,
            "confidence": None,
            "entry_range": None,
            "take_profit": None,
            "stop_loss": None,
            "target_price": None,
            "advice": None,
            "error": None,
        }

        # 跟踪当前进度，用于失败时记录
        current_progress = 10

        try:
            # 获取 LLM 客户端
            llm_call = await self._get_llm_call_func()

            # ---- 步骤 1: 深度分析 ----
            progress_tracker.update_progress({
                "progress_percentage": 10,
                "last_message": f"🔍 正在深度分析 {symbol}...",
            })

            deep_report = await self._call_deep_analysis(llm_call, symbol, params)

            current_progress = 70
            progress_tracker.update_progress({
                "progress_percentage": 70,
                "last_message": f"📊 提取 {symbol} 关键信息...",
            })

            # ---- 步骤 2: 提取关键信息 ----
            extracted = self._extract_deep_analysis_info(deep_report)

            result.update(extracted)
            result["raw_report"] = deep_report
            result["success"] = True

            # ---- 步骤 3: 持久化结果到 analysis_tasks ----
            current_progress = 90
            progress_tracker.update_progress({
                "progress_percentage": 90,
                "last_message": f"💾 保存 {symbol} 分析结果...",
            })

            # 构建结果数据，包含所有关键字段（任务 5.2）
            result_data = {
                "rating": result.get("rating"),
                "confidence": result.get("confidence"),
                "entry_range": result.get("entry_range"),
                "take_profit": result.get("take_profit"),
                "stop_loss": result.get("stop_loss"),
                "target_price": result.get("target_price"),
                "advice": result.get("advice"),
                "raw_report": deep_report,
            }

            # 使用 _sync_task_status 替代直接 db.analysis_tasks.update_one（任务 5.2）
            await self._sync_task_status(task_id, "completed", 100, result=result_data)

            # 标记完成
            progress_tracker.mark_completed()
            logger.info(f"主力选股单股深度分析完成: {task_id} - {symbol}")

        except Exception as e:
            # 构建结构化错误信息（任务 5.3）
            error_msg = f"[深度分析] {symbol} 分析失败: {e}"
            logger.error(error_msg)
            result["error"] = error_msg
            progress_tracker.mark_failed(error_msg)
            await self._sync_task_status(task_id, "failed", current_progress, error=error_msg)

        return result

    async def get_batch_history(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        查询批量分析历史记录。

        从 main_force_batch_history 集合中分页查询，
        按 analysis_date 降序排列。

        Args:
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            包含 total、page、page_size、items 的字典
        """
        from app.core.database import get_mongo_db

        db = get_mongo_db()
        collection = db.main_force_batch_history

        # 计算总数
        total = await collection.count_documents({})

        # 分页查询
        skip = (page - 1) * page_size
        cursor = collection.find({}).sort("analysis_date", -1).skip(skip).limit(page_size)
        items = []
        async for doc in cursor:
            # 将 ObjectId 转为字符串
            doc["_id"] = str(doc["_id"])
            items.append(doc)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }

    async def delete_batch_history(self, history_id: str) -> bool:
        """
        删除指定的批量分析历史记录。

        Args:
            history_id: 历史记录的 _id（字符串格式的 ObjectId）

        Returns:
            是否删除成功
        """
        from app.core.database import get_mongo_db
        from bson import ObjectId

        db = get_mongo_db()
        collection = db.main_force_batch_history

        try:
            result = await collection.delete_one({"_id": ObjectId(history_id)})
            if result.deleted_count > 0:
                logger.info(f"已删除主力选股批量分析历史: {history_id}")
                return True
            else:
                logger.warning(f"未找到要删除的历史记录: {history_id}")
                return False
        except Exception as e:
            logger.error(f"删除主力选股批量分析历史失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 内部方法：深度分析 LLM 调用
    # ------------------------------------------------------------------

    async def _call_deep_analysis(
        self,
        llm_call,
        symbol: str,
        params: dict,
    ) -> str:
        """
        调用 LLM 对单只股票进行深度分析。

        Args:
            llm_call: LLM 调用函数
            symbol: 股票代码
            params: 分析参数

        Returns:
            深度分析报告文本
        """
        prompt = f"""你是一名资深的股票投资分析师，现在需要你对股票 {symbol} 进行深度分析。

【分析要求】
请从以下维度进行全面深度分析：

1. **投资评级**：给出明确的投资评级（强烈推荐/推荐/中性/谨慎/回避）
2. **信心度**：给出 0-100 的信心度评分
3. **关键价位分析**：
   - 进场区间（建议买入的价格范围）
   - 止盈位（建议卖出获利的价格）
   - 止损位（建议止损的价格）
   - 目标价（中期目标价格）
4. **投资建议**：给出具体的操作建议

请按以下 JSON 格式输出（只输出 JSON，不要其他内容）：
```json
{{
  "rating": "投资评级（强烈推荐/推荐/中性/谨慎/回避）",
  "confidence": 85,
  "entry_range": "进场区间（如 15.50-16.20）",
  "take_profit": "止盈位（如 18.50）",
  "stop_loss": "止损位（如 14.80）",
  "target_price": "目标价（如 20.00）",
  "advice": "具体投资建议（200字以内）"
}}
```

注意：
- 必须严格按照 JSON 格式输出
- 信心度为 0-100 的整数
- 价格使用具体数字
- 投资建议简明扼要"""

        messages = [
            {"role": "system", "content": "你是资深股票投资分析师，擅长深度分析个股并给出精准的投资建议。请严格按照要求的 JSON 格式输出。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=2000)

    @staticmethod
    def _extract_deep_analysis_info(report_text: str) -> Dict[str, Any]:
        """
        从深度分析报告中提取关键信息。

        尝试从 LLM 输出中解析 JSON 格式的结构化数据。

        Args:
            report_text: LLM 返回的分析报告文本

        Returns:
            包含 rating、confidence、entry_range 等字段的字典
        """
        result: Dict[str, Any] = {
            "rating": None,
            "confidence": None,
            "entry_range": None,
            "take_profit": None,
            "stop_loss": None,
            "target_price": None,
            "advice": None,
        }

        try:
            # 尝试提取 JSON 块
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", report_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接查找 JSON 对象
                json_match = re.search(r"\{[\s\S]*\"rating\"[\s\S]*\}", report_text)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    logger.warning("未能从深度分析报告中提取 JSON")
                    return result

            data = json.loads(json_str)

            result["rating"] = data.get("rating")
            result["confidence"] = data.get("confidence")
            result["entry_range"] = data.get("entry_range")
            result["take_profit"] = data.get("take_profit")
            result["stop_loss"] = data.get("stop_loss")
            result["target_price"] = data.get("target_price")
            result["advice"] = data.get("advice")

            # 确保 confidence 是数值
            if result["confidence"] is not None:
                try:
                    result["confidence"] = float(result["confidence"])
                except (ValueError, TypeError):
                    result["confidence"] = None

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"解析深度分析 JSON 失败: {e}")

        return result

    # ------------------------------------------------------------------
    # 内部方法：pywencai 数据获取（含 fallback）
    # ------------------------------------------------------------------

    async def _fetch_with_fallback(
        self, queries: List[str]
    ) -> Optional[pd.DataFrame]:
        """
        依次尝试多个查询方案获取数据。

        pywencai 是同步库，使用 asyncio.to_thread() 包装。
        每个方案失败后等待 2 秒再尝试下一个。

        Args:
            queries: 查询方案列表

        Returns:
            成功获取的 DataFrame，或 None（所有方案均失败）
        """
        for i, query in enumerate(queries, 1):
            logger.info(f"尝试 pywencai 查询方案 {i}/{len(queries)}...")
            try:
                result = await asyncio.to_thread(self._call_pywencai, query)

                if result is None:
                    logger.warning(f"方案 {i} 返回 None，尝试下一个方案")
                    continue

                df = self._convert_to_dataframe(result)

                if df is None or df.empty:
                    logger.warning(f"方案 {i} 数据为空，尝试下一个方案")
                    continue

                logger.info(f"方案 {i} 成功，获取到 {len(df)} 只股票")
                return df

            except Exception as e:
                logger.error(f"方案 {i} 失败: {e}")
                if i < len(queries):
                    await asyncio.sleep(2)
                continue

        logger.error("所有 pywencai 查询方案均失败")
        return None

    @staticmethod
    def _call_pywencai(query: str) -> Any:
        """
        调用 pywencai.get() 获取数据（同步方法）。

        独立为静态方法，方便在测试中 mock。

        Args:
            query: 问财查询语句

        Returns:
            pywencai 返回的原始结果
        """
        import pywencai

        return pywencai.get(query=query, loop=True)

    @staticmethod
    def _convert_to_dataframe(result: Any) -> Optional[pd.DataFrame]:
        """
        将 pywencai 返回结果转换为 DataFrame。

        pywencai 可能返回 DataFrame、dict 或 list，需要统一处理。

        Args:
            result: pywencai 返回的原始结果

        Returns:
            转换后的 DataFrame，或 None
        """
        try:
            if isinstance(result, pd.DataFrame):
                return result
            elif isinstance(result, dict):
                if "tableV1" in result:
                    table_data = result["tableV1"]
                    if isinstance(table_data, pd.DataFrame):
                        return table_data
                    elif isinstance(table_data, list):
                        return pd.DataFrame(table_data)
                return pd.DataFrame([result])
            elif isinstance(result, list):
                return pd.DataFrame(result)
            else:
                return None
        except Exception as e:
            logger.warning(f"转换 DataFrame 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 内部方法：DataFrame → 候选列表转换
    # ------------------------------------------------------------------

    @staticmethod
    def _dataframe_to_candidates(
        df: pd.DataFrame,
    ) -> List[MainForceCandidate]:
        """
        将 pywencai 返回的 DataFrame 转换为 MainForceCandidate 列表。

        pywencai 返回的列名不固定，需要智能匹配。

        Args:
            df: pywencai 返回的 DataFrame

        Returns:
            候选股票列表
        """
        candidates = []
        columns = list(df.columns)

        # 智能匹配列名
        code_col = _find_column(columns, ["股票代码"])
        name_col = _find_column(columns, ["股票简称"])
        industry_col = _find_column(
            columns, ["所属同花顺行业", "所属行业"]
        )
        net_inflow_col = _find_column(
            columns,
            [
                "区间主力资金流向",
                "区间主力资金净流入",
                "主力资金流向",
                "主力资金净流入",
                "主力净流入",
            ],
        )
        change_pct_col = _find_column(
            columns,
            [
                "区间涨跌幅:前复权",
                "区间涨跌幅:前复权(%)",
                "区间涨跌幅(%)",
                "区间涨跌幅",
                "涨跌幅:前复权",
                "涨跌幅:前复权(%)",
                "涨跌幅(%)",
                "涨跌幅",
            ],
        )
        market_cap_col = _find_column(columns, ["总市值"])
        pe_col = _find_column(columns, ["市盈率"])
        pb_col = _find_column(columns, ["市净率"])

        for _, row in df.iterrows():
            try:
                code = _safe_str(row, code_col, "")
                if not code:
                    continue

                name = _safe_str(row, name_col, "未知")
                industry = _safe_str(row, industry_col, "未知")

                net_inflow = _safe_float(row, net_inflow_col, 0.0)
                change_pct = _safe_float(row, change_pct_col, 0.0)
                market_cap = _safe_float(row, market_cap_col, 0.0)

                # 市值单位处理：如果值很大（>100000），认为单位是元，转换为亿
                if market_cap > 100000:
                    market_cap = market_cap / 1e8

                pe_ratio = _safe_float_or_none(row, pe_col)
                pb_ratio = _safe_float_or_none(row, pb_col)

                candidates.append(
                    MainForceCandidate(
                        code=code,
                        name=name,
                        industry=industry,
                        net_inflow=net_inflow,
                        change_pct=change_pct,
                        market_cap=market_cap,
                        pe_ratio=pe_ratio,
                        pb_ratio=pb_ratio,
                    )
                )
            except Exception as e:
                logger.warning(f"解析股票数据行失败: {e}")
                continue

        return candidates


# ======================================================================
# 模块级辅助函数
# ======================================================================


def _find_column(columns: List[str], patterns: List[str]) -> Optional[str]:
    """
    在列名列表中按优先级查找匹配的列名。

    先尝试精确匹配，再尝试包含匹配。

    Args:
        columns: DataFrame 的列名列表
        patterns: 按优先级排列的列名模式

    Returns:
        匹配到的列名，或 None
    """
    # 精确匹配
    for pattern in patterns:
        if pattern in columns:
            return pattern

    # 包含匹配
    for pattern in patterns:
        for col in columns:
            if pattern in col:
                return col

    return None


def _safe_str(row: pd.Series, col: Optional[str], default: str = "") -> str:
    """安全获取字符串值"""
    if col is None:
        return default
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()


def _safe_float(row: pd.Series, col: Optional[str], default: float = 0.0) -> float:
    """安全获取浮点数值"""
    if col is None:
        return default
    val = row.get(col)
    if val is None:
        return default
    try:
        result = float(val)
        if pd.isna(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def _safe_float_or_none(row: pd.Series, col: Optional[str]) -> Optional[float]:
    """安全获取浮点数值，无效时返回 None"""
    if col is None:
        return None
    val = row.get(col)
    if val is None:
        return None
    try:
        result = float(val)
        if pd.isna(result):
            return None
        return result
    except (ValueError, TypeError):
        return None
