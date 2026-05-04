"""
龙虎榜分析服务

通过 StockAPI 获取龙虎榜数据，进行 AI 智能评分排名，
并提供 5 位 AI 分析师协同分析、推荐股票提取等功能。

从 aiagents-stock/longhubang_data.py 和 aiagents-stock/longhubang_db.py 迁移并重构为异步服务。
"""

import asyncio
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.database import get_mongo_db
from app.services.longhubang_scoring import ScoringEngine

logger = logging.getLogger(__name__)

# UTC 时区常量
_UTC = timezone.utc


class LonghubangDataResult:
    """龙虎榜数据获取结果"""

    def __init__(
        self,
        data_list: List[dict],
        summary: Dict[str, Any],
        saved_count: int = 0,
        date_range: str = "",
    ):
        self.data_list = data_list
        self.summary = summary
        self.saved_count = saved_count
        self.date_range = date_range


class LonghubangService:
    """龙虎榜分析服务"""

    # 默认配置常量（当 Config_Service 不可用时使用）
    DEFAULT_BASE_URL = "http://lhb-api.ws4.cn/v1"
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 2  # 秒
    DEFAULT_REQUEST_DELAY = 0.025  # 秒，40 次/秒 = 0.025 秒/次
    DEFAULT_REQUEST_TIMEOUT = 10  # 秒

    # 默认游资名单
    DEFAULT_TOP_YOUZI = [
        "赵老哥", "章盟主", "92科比", "炒股养家", "小鳄鱼",
        "佛山无影脚", "欢乐海岸", "金田路", "作手新一", "涅槃重生",
        "方新侠", "深南哥", "首板王", "八仙过海", "拉萨天团",
        "宁波桑田路", "成都帮", "上海超短帮",
    ]
    DEFAULT_FAMOUS_YOUZI = [
        "深股通", "中信证券", "华泰证券", "国泰君安", "招商证券",
        "中金公司", "海通证券", "广发证券", "申万宏源", "银河证券",
    ]
    DEFAULT_INSTITUTION_KEYWORDS = [
        "机构专用", "基金", "保险", "社保", "QFII", "信托",
    ]

    def __init__(self, config_service=None):
        """
        初始化龙虎榜服务。

        Args:
            config_service: ConfigService 实例，用于读取系统配置。
                           如果为 None，则使用默认参数。
        """
        self.config_service = config_service

    # ------------------------------------------------------------------
    # 公开方法：数据获取
    # ------------------------------------------------------------------

    async def fetch_data(
        self, date: Optional[str] = None, days: int = 1
    ) -> LonghubangDataResult:
        """
        获取龙虎榜数据。

        支持两种模式：
        - 指定日期模式：传入 date 参数（YYYY-MM-DD 格式）
        - 最近 N 天模式：传入 days 参数

        流程：
        1. 通过 StockAPI 获取数据
        2. 生成统计摘要
        3. 持久化到 longhubang_records（upsert 去重）
        4. 返回数据和摘要

        Args:
            date: 指定日期（YYYY-MM-DD 格式），为 None 时使用 days 模式
            days: 最近天数（默认 1 天）

        Returns:
            LonghubangDataResult 包含数据列表、统计摘要、保存数量和日期范围
        """
        # 1. 读取 API 配置
        api_config = await self._get_api_config()

        # 2. 确定日期列表
        date_list = self._compute_date_list(date, days)

        if not date_list:
            return LonghubangDataResult(
                data_list=[], summary={}, saved_count=0, date_range=""
            )

        # 3. 逐日获取数据
        all_data: List[dict] = []
        for target_date in date_list:
            day_data = await self._fetch_single_day(target_date, api_config)
            if day_data:
                all_data.extend(day_data)

        if not all_data:
            date_range = (
                date_list[0]
                if len(date_list) == 1
                else f"{date_list[0]} 至 {date_list[-1]}"
            )
            return LonghubangDataResult(
                data_list=[],
                summary={},
                saved_count=0,
                date_range=date_range,
            )

        # 4. 生成统计摘要（纯函数）
        summary = LonghubangService._analyze_data_summary(all_data)

        # 5. 持久化到 MongoDB
        saved_count = await self._persist_records(all_data)

        # 6. 构建日期范围字符串
        date_range = (
            date_list[0]
            if len(date_list) == 1
            else f"{date_list[0]} 至 {date_list[-1]}"
        )

        logger.info(
            f"[龙虎榜] 获取完成: {len(all_data)} 条记录, "
            f"保存 {saved_count} 条, 日期范围: {date_range}"
        )

        return LonghubangDataResult(
            data_list=all_data,
            summary=summary,
            saved_count=saved_count,
            date_range=date_range,
        )

    # ------------------------------------------------------------------
    # 公开方法：AI 智能评分
    # ------------------------------------------------------------------

    async def score_stocks(self, data_list: List[dict]) -> List[dict]:
        """
        对上榜股票进行 AI 智能评分。

        调用 ScoringEngine 对龙虎榜数据进行五维度评分排名。

        Args:
            data_list: 龙虎榜数据列表

        Returns:
            按综合评分降序排列的评分结果列表
        """
        if not data_list:
            return []

        # 从配置加载游资名单
        scoring_config = await self._get_scoring_config()

        engine = ScoringEngine(
            top_youzi=scoring_config["top_youzi"],
            famous_youzi=scoring_config["famous_youzi"],
            institution_keywords=scoring_config["institution_keywords"],
        )

        return engine.score_all_stocks(data_list)

    # ------------------------------------------------------------------
    # 公开方法：AI 分析任务提交与执行
    # ------------------------------------------------------------------

    async def submit_ai_analysis(
        self,
        user_id: str,
        data_summary: dict,
        scoring: List,
    ) -> str:
        """
        提交 AI 分析任务到队列，返回 task_id。

        通过 QueueService.enqueue_task() 将任务入队，
        task_type="longhubang_analysis"，由 AnalysisWorker 消费后
        路由到 execute_ai_analysis()。

        Args:
            user_id: 用户 ID
            data_summary: 龙虎榜数据统计摘要
            scoring: 评分排名列表

        Returns:
            task_id 字符串
        """
        import uuid
        from app.services.queue_service import get_queue_service
        from app.core.database import get_mongo_db

        # 先生成 task_id，确保 DB 记录、Redis 进度和队列任务使用同一个 ID
        task_id = str(uuid.uuid4())

        # 写入 analysis_tasks 集合，创建完整的任务记录（与主力选股一致）
        db = get_mongo_db()
        task_record = {
            "task_id": task_id,
            "user_id": user_id,
            "symbol": "longhubang_analysis",
            "status": "pending",
            "task_type": "longhubang_analysis",
            "parameters": {
                "date_range": data_summary.get("date_range", ""),
                "total_records": data_summary.get("total_records", 0),
                "total_stocks": data_summary.get("total_stocks", 0),
            },
            "progress": 0,
            "created_at": datetime.utcnow(),
        }
        await db.analysis_tasks.insert_one(task_record)

        # 构建队列参数
        queue_service = get_queue_service()
        queue_params: Dict[str, Any] = {
            "task_type": "longhubang_analysis",
            "data_summary": data_summary,
            "scoring": scoring,
            "user_id": user_id,
        }

        # 入队时传入预先生成的 task_id
        await queue_service.enqueue_task(
            user_id=user_id,
            symbol="longhubang_analysis",
            params=queue_params,
            task_id=task_id,
        )

        # 立即写入初始进度到 Redis
        await self._write_initial_progress(task_id, "longhubang_analysis", "龙虎榜 AI 分析任务已入队，等待执行...")

        logger.info(f"[龙虎榜] AI 分析任务已入队: {task_id}")
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
                logger.info(f"✅ [龙虎榜] 初始进度已写入 Redis: {task_id}")
                return

            # Redis 未启用时写文件
            os.makedirs("./data/progress", exist_ok=True)
            with open(f"./data/progress/{task_id}.json", "w", encoding="utf-8") as f:
                _json.dump(progress_data, f)
            logger.info(f"✅ [龙虎榜] 初始进度已写入文件: {task_id}")
        except Exception as e:
            logger.warning(f"⚠️ [龙虎榜] 写入初始进度失败: {task_id} - {e}")

    async def execute_ai_analysis(
        self,
        task_id: str,
        data_summary: dict,
        scoring: List,
        model_name: str = None,
    ) -> Dict[str, Any]:
        """
        执行 AI 分析（由 AnalysisWorker 调用）。

        流程：
        1. 初始化 RedisProgressTracker
        2. 调用游资行为分析师
        3. 调用个股潜力分析师
        4. 调用题材追踪分析师
        5. 调用风险控制专家
        6. 调用首席策略师综合
        7. 提取推荐股票
        8. 通过 RedisProgressTracker 更新进度

        Args:
            task_id: 任务 ID
            data_summary: 龙虎榜数据统计摘要
            scoring: 评分排名列表

        Returns:
            分析结果字典
        """
        from app.services.redis_progress_tracker import RedisProgressTracker

        # 初始化进度跟踪器
        progress_tracker = RedisProgressTracker(
            task_id=task_id,
            analysts=["youzi", "stock", "theme", "risk", "chief"],
            research_depth="标准",
            llm_provider="dashscope",
        )

        result: Dict[str, Any] = {
            "task_id": task_id,
            "success": False,
            "agents_analysis": {},
            "recommended_stocks": [],
            "error": None,
        }

        # 更新 analysis_tasks 状态为 processing
        try:
            db = get_mongo_db()
            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"status": "running", "progress": 0, "started_at": datetime.utcnow()}}
            )
        except Exception:
            pass

        try:
            # 获取 LLM 客户端
            llm_call = await self._get_llm_call_func(specified_model=model_name)

            # 准备数据文本
            data_text = self._prepare_data_text(data_summary, scoring)

            # ---- 步骤 1/5: 游资行为分析师 ----
            progress_tracker.update_progress({
                "progress_percentage": 5,
                "last_message": "🎯 游资行为分析师正在分析...",
            })

            youzi_report = await self._call_youzi_analyst(
                llm_call, data_text, data_summary
            )
            result["agents_analysis"]["youzi"] = {
                "agent_name": "游资行为分析师",
                "analysis": youzi_report,
            }

            progress_tracker.update_progress({
                "progress_percentage": 20,
                "last_message": "✅ 游资行为分析完成",
            })
            await self._update_task_progress(task_id, 20)

            # ---- 步骤 2/5: 个股潜力分析师 ----
            progress_tracker.update_progress({
                "progress_percentage": 22,
                "last_message": "📈 个股潜力分析师正在分析...",
            })

            stock_report = await self._call_stock_potential_analyst(
                llm_call, data_text, data_summary
            )
            result["agents_analysis"]["stock"] = {
                "agent_name": "个股潜力分析师",
                "analysis": stock_report,
            }

            progress_tracker.update_progress({
                "progress_percentage": 40,
                "last_message": "✅ 个股潜力分析完成",
            })
            await self._update_task_progress(task_id, 40)

            # ---- 步骤 3/5: 题材追踪分析师 ----
            progress_tracker.update_progress({
                "progress_percentage": 42,
                "last_message": "🔥 题材追踪分析师正在分析...",
            })

            theme_report = await self._call_theme_tracker_analyst(
                llm_call, data_text, data_summary
            )
            result["agents_analysis"]["theme"] = {
                "agent_name": "题材追踪分析师",
                "analysis": theme_report,
            }

            progress_tracker.update_progress({
                "progress_percentage": 58,
                "last_message": "✅ 题材追踪分析完成",
            })
            await self._update_task_progress(task_id, 58)

            # ---- 步骤 4/5: 风险控制专家 ----
            progress_tracker.update_progress({
                "progress_percentage": 60,
                "last_message": "⚠️ 风险控制专家正在分析...",
            })

            risk_report = await self._call_risk_control_specialist(
                llm_call, data_text, data_summary
            )
            result["agents_analysis"]["risk"] = {
                "agent_name": "风险控制专家",
                "analysis": risk_report,
            }

            progress_tracker.update_progress({
                "progress_percentage": 75,
                "last_message": "✅ 风险控制分析完成",
            })
            await self._update_task_progress(task_id, 75)

            # ---- 步骤 5/5: 首席策略师 ----
            progress_tracker.update_progress({
                "progress_percentage": 78,
                "last_message": "👔 首席策略师正在综合分析...",
            })

            all_analyses = [
                result["agents_analysis"]["youzi"],
                result["agents_analysis"]["stock"],
                result["agents_analysis"]["theme"],
                result["agents_analysis"]["risk"],
            ]
            chief_report = await self._call_chief_strategist(
                llm_call, all_analyses
            )
            result["agents_analysis"]["chief"] = {
                "agent_name": "首席策略师",
                "analysis": chief_report,
            }

            progress_tracker.update_progress({
                "progress_percentage": 92,
                "last_message": "✅ 首席策略师综合分析完成",
            })
            await self._update_task_progress(task_id, 92)

            # ---- 提取推荐股票 ----
            recommended_stocks = self._extract_recommended_stocks(chief_report)
            result["recommended_stocks"] = recommended_stocks

            # ---- 持久化到 MongoDB ----
            progress_tracker.update_progress({
                "progress_percentage": 95,
                "last_message": "💾 保存分析结果...",
            })

            result["success"] = True
            await self._save_analysis_result(task_id, data_summary, scoring, result)

            # 标记完成（注意：success 已在持久化前设置）
            progress_tracker.mark_completed()
            logger.info(f"[龙虎榜] AI 分析完成: {task_id}")

        except Exception as e:
            error_msg = f"龙虎榜 AI 分析失败: {e}"
            logger.error(error_msg)
            result["error"] = error_msg
            progress_tracker.mark_failed(error_msg)

        return result

    # ------------------------------------------------------------------
    # 内部方法：LLM 调用
    # ------------------------------------------------------------------

    async def _get_llm_call_func(self, specified_model: str = None):
        """
        获取 LLM 调用函数。

        通过 unified_llm_service 统一获取模型配置。

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
        logger.info(f"[龙虎榜 LLM] specified_model={specified_model}")
        if specified_model:
            try:
                merged_config = await unified_llm_service.get_model_config(specified_model)
                if merged_config and merged_config.api_key:
                    logger.info(f"[龙虎榜 LLM] ✅ 使用用户指定模型: model={merged_config.model_name}, base={merged_config.api_base}")
                else:
                    logger.warning(f"[龙虎榜 LLM] 用户指定模型 {specified_model} 无有效配置，尝试其他方式")
                    merged_config = None
            except Exception as e:
                logger.warning(f"[龙虎榜 LLM] 查找用户指定模型 {specified_model} 失败: {e}")

        # 方式 2：通过 unified_llm_service.recommend_model 推荐模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.recommend_model(
                    task_type="analysis", depth="标准"
                )
                if merged_config:
                    logger.info(f"[龙虎榜 LLM] ✅ 使用推荐模型: model={merged_config.model_name}, base={merged_config.api_base}, source=unified_llm_service.recommend_model")
            except Exception as e:
                logger.warning(f"[龙虎榜 LLM] unified_llm_service.recommend_model 获取失败: {e}")

        # 方式 3：通过 unified_llm_service.get_default_model 获取默认模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.get_default_model()
                if merged_config:
                    logger.info(f"[龙虎榜 LLM] ✅ 使用默认模型: model={merged_config.model_name}, base={merged_config.api_base}, source=unified_llm_service.get_default_model")
            except Exception as e:
                logger.warning(f"[龙虎榜 LLM] unified_llm_service.get_default_model 获取失败: {e}")

        # 从 MergedModelConfig 提取参数
        if merged_config:
            model_name = merged_config.model_name
            api_key = merged_config.api_key
            api_base = merged_config.api_base
            temperature = merged_config.temperature or 0.7
            max_tokens_default = merged_config.max_tokens or 4000

        # 方式 3：回退到环境变量
        if not api_key:
            import os
            api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
            api_base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            model_name = model_name or os.getenv("DEFAULT_MODEL", "qwen-plus")
            logger.info(f"[龙虎榜 LLM] 回退到环境变量: model={model_name}, base={api_base}")

        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        logger.info(f"[龙虎榜 LLM] 最终配置: model={model_name}, base={api_base}")

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
    # 内部方法：数据准备
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_data_text(
        data_summary: dict, scoring: List
    ) -> str:
        """
        将数据摘要和评分排名格式化为文本，供分析师 prompt 使用。

        Args:
            data_summary: 统计摘要字典
            scoring: 评分排名列表

        Returns:
            格式化的数据文本
        """
        lines: List[str] = []

        # 评分排名 TOP 信息
        if scoring:
            lines.append("【AI 智能评分 TOP 排名】")
            for item in scoring[:15]:
                name = item.get("stock_name", "")
                code = item.get("stock_code", "")
                total = item.get("total_score", 0)
                net = item.get("net_inflow", 0)
                lines.append(
                    f"  {name}({code}): 综合评分 {total:.1f}, "
                    f"净流入 {net:,.0f} 元"
                )
            lines.append("")

        # 活跃游资
        top_youzi = data_summary.get("top_youzi", [])
        if top_youzi:
            lines.append("【活跃游资 TOP10】")
            if isinstance(top_youzi, list):
                for idx, item in enumerate(top_youzi[:10], 1):
                    name = item.get("youzi_name", "")
                    net = item.get("total_net_inflow", 0)
                    count = item.get("trade_count", 0)
                    lines.append(f"  {idx}. {name}: 净流入 {net:,.2f} 元, 交易 {count} 次")
            elif isinstance(top_youzi, dict):
                for idx, (name, amount) in enumerate(list(top_youzi.items())[:10], 1):
                    lines.append(f"  {idx}. {name}: 净流入 {amount:,.2f} 元")
            lines.append("")

        # 热门股票
        top_stocks = data_summary.get("top_stocks", [])
        if top_stocks:
            lines.append("【资金净流入 TOP20 股票】")
            for idx, stock in enumerate(top_stocks[:20], 1):
                lines.append(
                    f"  {idx}. {stock['name']}({stock['code']}): "
                    f"净流入 {stock['net_inflow']:,.2f} 元"
                )
            lines.append("")

        # 热门概念
        hot_concepts = data_summary.get("hot_concepts", {})
        if hot_concepts:
            lines.append("【热门概念 TOP20】")
            for idx, (concept, count) in enumerate(
                list(hot_concepts.items())[:20], 1
            ):
                lines.append(f"  {idx}. {concept}: 出现 {count} 次")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部方法：五位分析师
    # ------------------------------------------------------------------

    async def _call_youzi_analyst(
        self,
        llm_call,
        data_text: str,
        summary: dict,
    ) -> str:
        """调用游资行为分析师"""
        prompt = f"""你是一名资深的游资研究专家，拥有10年以上的龙虎榜数据分析经验，深谙各路游资的操作风格和盈利模式。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个
总买入金额: {summary.get('total_buy_amount', 0):,.2f} 元
总卖出金额: {summary.get('total_sell_amount', 0):,.2f} 元
净流入金额: {summary.get('total_net_inflow', 0):,.2f} 元

{data_text[:8000]}

请基于以上龙虎榜数据，进行深入的游资行为分析：

1. **活跃游资识别与画像** ⭐ 核心
   - 识别当前最活跃的5-8个游资席位
   - 分析每个游资的操作风格（激进型/稳健型/超短型/波段型）
   - 识别知名"牛散"和"游资大佬"

2. **游资操作特征分析**
   - 分析游资的买入特征（追高/低吸/打板/潜伏）
   - 识别游资的联合操作和接力特征
   - 判断游资是否存在抱团现象

3. **游资目标股票分析**
   - 分析游资重点关注的股票（前10只）
   - 识别游资集体看好的股票（多席位介入）
   - 评估游资介入股票的后续爆发力

4. **游资进出节奏**
   - 判断游资整体是进攻还是防守状态
   - 识别游资撤退的信号和板块

5. **游资与题材的匹配**
   - 分析游资偏好的题材和概念
   - 预判下一个游资可能关注的题材

6. **风险与机会提示**
   - 识别游资可能设置的"陷阱"股票
   - 发现游资刚开始介入的潜力股

7. **投资策略建议**
   - 推荐3-5只游资看好的潜力股票
   - 提示2-3只游资可能出货的风险股票

请给出专业、实战性强的游资行为分析报告。"""

        messages = [
            {"role": "system", "content": "你是一名资深的游资研究专家，擅长从龙虎榜数据中洞察游资意图和操作手法。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_stock_potential_analyst(
        self,
        llm_call,
        data_text: str,
        summary: dict,
    ) -> str:
        """调用个股潜力分析师"""
        prompt = f"""你是一名资深的个股研究专家和短线交易高手，精通技术分析和资金分析，擅长从龙虎榜中挖掘短期爆发股。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个

{data_text[:8000]}

请基于以上龙虎榜数据，进行深入的个股潜力分析：

1. **次日大概率上涨股票挖掘** ⭐⭐⭐ 最核心
   - 识别5-8只次日大概率上涨的股票
   - 详细分析每只股票的上涨逻辑（资金面、技术面、题材面）
   - 评估每只股票的上涨空间和确定性（高/中/低）
   - 给出具体的买入价位和止损位

2. **资金流向强度分析**
   - 识别主力资金大幅流入的股票（净买入前10）
   - 分析资金流入的集中度和持续性
   - 识别多席位联合买入的股票（强烈看好信号）

3. **技术形态评估**
   - 分析上榜股票的技术位置（突破/回调/整理）
   - 识别处于启动阶段的股票

4. **题材与概念分析**
   - 识别当前最热门的题材和概念
   - 找出题材龙头和低位补涨股

5. **风险股票识别**
   - 识别3-5只高风险股票（游资可能出货）
   - 分析卖出金额大于买入金额的股票

6. **操作策略建议**
   - 推荐5-8只次日重点关注的股票（按优先级排序）
   - 给出每只股票的买入逻辑、买入价位、目标价位、止损价位
   - 提供仓位分配建议和持有周期建议

请给出专业、实战、具有可操作性的个股潜力分析报告。务必重点分析次日大概率上涨的股票！"""

        messages = [
            {"role": "system", "content": "你是一名资深的个股研究专家和短线交易高手，擅长从龙虎榜中挖掘短期爆发股。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_theme_tracker_analyst(
        self,
        llm_call,
        data_text: str,
        summary: dict,
    ) -> str:
        """调用题材追踪分析师"""
        prompt = f"""你是一名资深的题材研究专家，拥有敏锐的市场嗅觉，擅长从龙虎榜数据中捕捉题材热点和板块轮动机会。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只

{data_text[:8000]}

请基于以上龙虎榜数据，进行深入的题材追踪分析：

1. **热点题材识别** ⭐ 核心
   - 识别当前最热门的5-8个题材/概念
   - 分析每个题材的核心逻辑和催化剂
   - 判断题材是主流还是伪题材

2. **题材炒作周期分析**
   - 判断每个题材所处的炒作周期（萌芽期/爆发期/高潮期/退潮期）
   - 识别即将启动的新题材（萌芽期）
   - 提示即将退潮的老题材（高潮期）

3. **题材龙头与梯队**
   - 识别每个题材的龙头股（1-2只）
   - 找出题材的跟风股和补涨股

4. **游资对题材的态度**
   - 分析游资重点炒作的题材
   - 识别游资集体进攻的题材（强势题材）
   - 发现游资开始撤离的题材（弱势题材）

5. **题材轮动特征**
   - 分析题材之间的轮动关系
   - 预判下一个可能启动的题材

6. **题材风险评估**
   - 识别过度炒作的题材（泡沫风险）
   - 提示游资分歧加大的题材

7. **投资策略建议**
   - 推荐3-5个值得关注的强势题材
   - 每个题材推荐1-2只最优标的
   - 给出题材仓位和持有周期建议

请给出专业、前瞻性强的题材追踪分析报告。"""

        messages = [
            {"role": "system", "content": "你是一名资深的题材研究专家，擅长从龙虎榜数据中捕捉题材热点和投资机会。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_risk_control_specialist(
        self,
        llm_call,
        data_text: str,
        summary: dict,
    ) -> str:
        """调用风险控制专家"""
        prompt = f"""你是一名资深的风险控制专家和反向思维大师，拥有20年的市场风险管理经验，擅长识别龙虎榜中的风险信号和资金陷阱。

【龙虎榜数据概况】
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个
总买入金额: {summary.get('total_buy_amount', 0):,.2f} 元
总卖出金额: {summary.get('total_sell_amount', 0):,.2f} 元
净流入金额: {summary.get('total_net_inflow', 0):,.2f} 元

{data_text[:8000]}

请基于以上龙虎榜数据，进行全面的风险分析：

1. **高风险股票识别** ⭐ 核心
   - 识别5-8只高风险股票（次日大概率下跌）
   - 分析每只股票的风险点（游资出货/技术破位/题材退潮）
   - 评估每只股票的风险等级（高/中/低）

2. **游资出货信号识别**
   - 识别卖出金额远大于买入金额的股票
   - 分析游资"一日游"后撤离的股票
   - 识别游资集体出货的股票（多席位卖出）

3. **资金陷阱识别**
   - 识别"虚假放量"的股票
   - 分析"高位放量滞涨"的股票
   - 提示"击鼓传花"的末期信号

4. **题材风险评估**
   - 识别过度炒作的题材（泡沫严重）
   - 提示题材退潮的信号

5. **情绪风险评估**
   - 识别市场情绪过热的信号
   - 分析游资一致性过高的风险（易崩盘）

6. **系统性风险提示**
   - 分析整体龙虎榜数据反映的市场风险
   - 评估游资整体是进攻还是防守

7. **风险管理建议**
   - 提供仓位控制建议（重仓/轻仓/空仓）
   - 给出止损止盈的纪律要求
   - 建议规避的板块和题材

请给出专业、严谨、保守的风险控制报告，宁可错过，不可做错。"""

        messages = [
            {"role": "system", "content": "你是一名资深的风险控制专家，擅长识别龙虎榜中的风险信号和资金陷阱。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=4000)

    async def _call_chief_strategist(
        self,
        llm_call,
        all_analyses: List[Dict],
    ) -> str:
        """调用首席策略师综合分析"""
        # 整合所有分析师的分析结果
        analyses_text = ""
        for analysis in all_analyses:
            analyses_text += f"\n{'=' * 60}\n"
            analyses_text += f"【{analysis['agent_name']}】分析报告\n"
            analyses_text += f"{'=' * 60}\n"
            analyses_text += analysis["analysis"] + "\n"

        from datetime import date as _date
        today_str = _date.today().strftime("%Y年%m月%d日")

        prompt = f"""你是一名资深的首席投资策略师，拥有CFA、FRM等专业资格，具有25年的市场实战经验和卓越的综合分析能力。

**当前日期：{today_str}**（请在报告中使用此日期，不要使用其他日期）

你的团队包含4位专业分析师，他们已经从不同维度完成了龙虎榜数据分析：
1. 游资行为分析师 - 分析游资操作特征和意图
2. 个股潜力分析师 - 挖掘次日大概率上涨的股票
3. 题材追踪分析师 - 识别热点题材和轮动机会
4. 风险控制专家 - 识别高风险股票和市场陷阱

以下是各位分析师的详细分析报告：

{analyses_text[:15000]}

请作为首席策略师，综合以上所有分析，给出最终的投资策略报告：

1. **市场总体研判**
   - 综合评估当前龙虎榜反映的市场状态
   - 判断游资整体的进攻或防守态度
   - 给出市场情绪和热度评分（0-100分）

2. **次日重点推荐股票（TOP5-8）** ⭐⭐⭐ 最核心
   - 综合4位分析师的意见，筛选出5-8只次日最有潜力的股票
   - 每只股票必须包含：
     * 股票名称和代码
     * 推荐理由（多维度综合）
     * 确定性评级（高/中/低）
     * 持有周期建议
   - 按推荐优先级排序

请按以下JSON格式在报告末尾输出推荐股票（用```json```包裹）：
```json
{{
  "recommended_stocks": [
    {{
      "rank": 1,
      "code": "股票代码",
      "name": "股票名称",
      "net_inflow": 0,
      "reason": "推荐理由",
      "confidence": "高/中/低",
      "hold_period": "短线/中线"
    }}
  ]
}}
```

3. **高风险警示股票（TOP3-5）**
   - 综合识别3-5只高风险股票
   - 说明风险原因

4. **热点题材总结**
   - 总结当前2-3个最强势题材
   - 每个题材推荐1-2只最优标的

5. **操作策略建议**
   - 仓位管理建议（进攻/平衡/防守）
   - 选股思路和方向
   - 风险控制要求

请给出专业、全面、可执行的首席策略师综合报告。报告要有明确的结论和可操作性！"""

        messages = [
            {"role": "system", "content": "你是一名资深的首席投资策略师，擅长综合多维度分析，给出最优投资决策。请严格按照要求在报告末尾输出JSON格式的推荐股票。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_call(messages, max_tokens=5000)

    # ------------------------------------------------------------------
    # 内部方法：提取推荐股票
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_recommended_stocks(chief_report: str) -> List[Dict[str, Any]]:
        """
        从首席策略师的分析报告中提取推荐股票列表。

        尝试从报告中解析 JSON 格式的推荐股票数据。
        如果 JSON 解析失败，返回空列表。

        Args:
            chief_report: 首席策略师的分析报告文本

        Returns:
            推荐股票列表，每项包含 rank、code、name、net_inflow、
            reason、confidence、hold_period
        """
        if not chief_report:
            return []

        try:
            # 尝试提取 JSON 块（```json ... ```）
            json_match = re.search(
                r"```json\s*(\{.*?\})\s*```", chief_report, re.DOTALL
            )
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接查找包含 recommended_stocks 的 JSON 对象
                json_match = re.search(
                    r"\{[\s\S]*\"recommended_stocks\"[\s\S]*\}",
                    chief_report,
                )
                if json_match:
                    json_str = json_match.group(0)
                else:
                    logger.warning("[龙虎榜] 未能从首席策略师报告中提取推荐股票 JSON")
                    return []

            data = json.loads(json_str)
            raw_stocks = data.get("recommended_stocks", [])

            # 标准化字段
            result: List[Dict[str, Any]] = []
            for idx, stock in enumerate(raw_stocks, 1):
                item: Dict[str, Any] = {
                    "rank": stock.get("rank", idx),
                    "code": str(stock.get("code", "")),
                    "name": str(stock.get("name", "")),
                    "net_inflow": _safe_float(stock.get("net_inflow")),
                    "reason": str(stock.get("reason", "")),
                    "confidence": str(stock.get("confidence", "")),
                    "hold_period": str(stock.get("hold_period", "")),
                }
                result.append(item)

            logger.info(f"[龙虎榜] 提取到 {len(result)} 只推荐股票")
            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[龙虎榜] 解析推荐股票 JSON 失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 内部方法：分析结果持久化
    # ------------------------------------------------------------------

    async def _update_task_progress(self, task_id: str, progress: int) -> None:
        """同步更新 analysis_tasks 中的进度值"""
        try:
            db = get_mongo_db()
            await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"progress": progress}}
            )
        except Exception:
            pass

    async def _save_analysis_result(
        self,
        task_id: str,
        data_summary: dict,
        scoring: List,
        result: Dict[str, Any],
    ) -> None:
        """
        将 AI 分析结果持久化到 MongoDB。

        同时更新 analysis_tasks 集合和 longhubang_analysis 集合。

        Args:
            task_id: 任务 ID
            data_summary: 数据统计摘要
            scoring: 评分排名列表
            result: 分析结果字典
        """
        try:
            from app.utils.timezone import now_tz

            db = get_mongo_db()

            # 更新 analysis_tasks 集合中的任务记录
            update_result = await db.analysis_tasks.update_one(
                {"task_id": task_id},
                {
                    "$set": {
                        "status": "completed" if result.get("success") else "failed",
                        "task_type": "longhubang_analysis",
                        "result": {
                            "agents_analysis": result.get("agents_analysis", {}),
                            "recommended_stocks": result.get("recommended_stocks", []),
                        },
                        "completed_at": now_tz(),
                        "progress": 100,
                        "started_at": now_tz(),
                        "error": result.get("error"),
                    }
                },
                upsert=True,
            )
            logger.info(f"[龙虎榜] analysis_tasks 更新: task_id={task_id}, matched={update_result.matched_count}, modified={update_result.modified_count}, success={result.get('success')}")

            # 同时保存到 longhubang_analysis 集合
            analysis_doc = {
                "analysis_date": now_tz(),
                "data_date_range": data_summary.get("date_range", ""),
                "data_info": {
                    "total_records": data_summary.get("total_records", 0),
                    "total_stocks": data_summary.get("total_stocks", 0),
                    "total_youzi": data_summary.get("total_youzi", 0),
                },
                "scoring_ranking": scoring,
                "agents_analysis": result.get("agents_analysis", {}),
                "recommended_stocks": result.get("recommended_stocks", []),
                "summary": f"本次分析共涵盖 {data_summary.get('total_records', 0)} 条龙虎榜记录，"
                           f"涉及 {data_summary.get('total_stocks', 0)} 只股票，"
                           f"{data_summary.get('total_youzi', 0)} 个游资席位。",
                "task_id": task_id,
                "created_at": now_tz(),
            }
            await db.longhubang_analysis.insert_one(analysis_doc)

            # 保存到 analysis_reports 集合（与分析报告页面统一展示）
            agents = result.get("agents_analysis", {})
            reports_map = {}
            agent_label_map = {
                "chief": "首席策略师",
                "youzi": "游资行为分析师",
                "stock": "个股潜力分析师",
                "theme": "题材追踪分析师",
                "risk": "风险控制专家",
            }
            for key, val in agents.items():
                content = val.get("analysis", "") if isinstance(val, dict) else str(val)
                if content.strip():
                    reports_map[key] = content

            recommended = result.get("recommended_stocks", [])
            summary_text = (
                f"本次分析共涵盖 {data_summary.get('total_records', 0)} 条龙虎榜记录，"
                f"涉及 {data_summary.get('total_stocks', 0)} 只股票，"
                f"{data_summary.get('total_youzi', 0)} 个游资席位。"
            )

            report_doc = {
                "analysis_id": f"lhb_{task_id}",
                "stock_symbol": "longhubang_analysis",
                "stock_name": "龙虎榜分析",
                "market_type": "A股",
                "model_info": "Unknown",
                "analysis_date": now_tz().strftime('%Y-%m-%d') if hasattr(now_tz(), 'strftime') else str(now_tz())[:10],
                "timestamp": now_tz(),
                "status": "completed",
                "source": "longhubang",
                "summary": summary_text,
                "analysts": list(agents.keys()),
                "research_depth": "标准",
                "reports": reports_map,
                "decision": {},
                "created_at": now_tz(),
                "updated_at": now_tz(),
                "task_id": task_id,
                "task_type": "longhubang_analysis",
                "recommended_stocks": recommended,
                "recommendation": f"推荐 {len(recommended)} 只股票" if recommended else "暂无推荐",
                "confidence_score": 0.0,
                "risk_level": "中等",
                "key_points": [f"{s.get('name', '')}({s.get('code', '')})" for s in recommended[:5]],
                "execution_time": 0,
                "tokens_used": 0,
            }
            await db.analysis_reports.insert_one(report_doc)
            logger.info(f"[龙虎榜] 报告已保存到 analysis_reports: {task_id}")

            logger.info(f"[龙虎榜] 分析结果已持久化: {task_id}")

        except Exception as e:
            logger.error(f"[龙虎榜] 持久化分析结果失败: {e}")


    # ------------------------------------------------------------------
    # 纯函数：统计摘要（静态方法，方便属性测试）
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_data_summary(data_list: List[dict]) -> Dict[str, Any]:
        """
        分析龙虎榜数据，生成统计摘要。

        这是一个纯函数，不依赖任何外部状态，方便属性测试。

        统计内容：
        - 记录总数
        - 涉及股票数（唯一股票代码数）
        - 涉及游资数（唯一游资名称数）
        - 总买入金额
        - 总卖出金额
        - 总净流入金额
        - 活跃游资 TOP10（按净流入排序）
        - 资金净流入 TOP20 股票
        - 热门概念 TOP20

        Args:
            data_list: 龙虎榜数据列表，每条记录包含以下字段（支持中文和拼音两种键名）：
                - 股票代码/gpdm, 股票名称/gpmc, 游资名称/yzmc
                - 买入金额/mrje, 卖出金额/mcje, 净流入金额/jlrje
                - 概念/gl

        Returns:
            统计摘要字典
        """
        if not data_list:
            return {}

        # 提取字段值的辅助函数
        def _get(record: dict, *keys: str, default=None):
            for key in keys:
                val = record.get(key)
                if val is not None:
                    return val
            return default

        # 基础统计
        stock_codes = set()
        youzi_names = set()
        total_buy = 0.0
        total_sell = 0.0
        total_net = 0.0

        # 用于 TOP 排名的聚合
        youzi_net_inflow: Dict[str, Any] = {}
        stock_info: Dict[str, Dict[str, Any]] = {}  # code -> {name, net, youzi_set, concepts_set}
        all_concepts: List[str] = []

        for record in data_list:
            code = str(_get(record, "股票代码", "gpdm", default=""))
            name = str(_get(record, "股票名称", "gpmc", default=""))
            youzi = str(_get(record, "游资名称", "yzmc", default=""))
            buy = _safe_float(_get(record, "买入金额", "mrje"))
            sell = _safe_float(_get(record, "卖出金额", "mcje"))
            net = _safe_float(_get(record, "净流入金额", "jlrje"))
            concepts = str(_get(record, "概念", "gl", default=""))

            if code:
                stock_codes.add(code)
            if youzi:
                youzi_names.add(youzi)

            total_buy += buy
            total_sell += sell
            total_net += net

            # 游资净流入聚合（同时统计买入、卖出、交易次数）
            if youzi:
                prev = youzi_net_inflow.get(youzi, {"net": 0.0, "buy": 0.0, "sell": 0.0, "count": 0})
                if isinstance(prev, (int, float)):
                    # 兼容旧格式
                    prev = {"net": prev, "buy": 0.0, "sell": 0.0, "count": 0}
                youzi_net_inflow[youzi] = {
                    "net": prev["net"] + net,
                    "buy": prev["buy"] + buy,
                    "sell": prev["sell"] + sell,
                    "count": prev["count"] + 1,
                }

            # 股票信息聚合（净流入、游资数量、概念）
            if code:
                info = stock_info.get(code, {"name": name, "net": 0.0, "youzi_set": set(), "concepts_set": set()})
                if not info["name"] and name:
                    info["name"] = name
                info["net"] += net
                if youzi:
                    info["youzi_set"].add(youzi)
                if concepts:
                    for c in concepts.split(","):
                        c = c.strip()
                        if c:
                            info["concepts_set"].add(c)
                stock_info[code] = info

            # 概念收集
            if concepts:
                for c in concepts.split(","):
                    c = c.strip()
                    if c:
                        all_concepts.append(c)

        summary: Dict[str, Any] = {
            "total_records": len(data_list),
            "total_stocks": len(stock_codes),
            "total_youzi": len(youzi_names),
            "total_buy_amount": total_buy,
            "total_sell_amount": total_sell,
            "total_net_inflow": total_net,
        }

        # TOP 游资排名（按净流入降序，取前 10）
        sorted_youzi = sorted(
            youzi_net_inflow.items(), key=lambda x: x[1]["net"] if isinstance(x[1], dict) else x[1], reverse=True
        )
        summary["top_youzi"] = [
            {
                "youzi_name": name,
                "total_net_inflow": stats["net"] if isinstance(stats, dict) else stats,
                "total_buy": stats.get("buy", 0) if isinstance(stats, dict) else 0,
                "total_sell": stats.get("sell", 0) if isinstance(stats, dict) else 0,
                "trade_count": stats.get("count", 0) if isinstance(stats, dict) else 0,
            }
            for name, stats in sorted_youzi[:10]
        ]

        # TOP 股票排名（按净流入降序，取前 20）
        sorted_stocks = sorted(
            stock_info.items(), key=lambda x: x[1]["net"], reverse=True
        )
        summary["top_stocks"] = [
            {
                "stock_code": code,
                "stock_name": info["name"],
                "total_net_inflow": info["net"],
                "youzi_count": len(info["youzi_set"]),
                "concepts": ", ".join(sorted(info["concepts_set"])[:5]) if info["concepts_set"] else "",
                # 兼容旧字段名
                "code": code,
                "name": info["name"],
                "net_inflow": info["net"],
            }
            for code, info in sorted_stocks[:20]
        ]

        # 热门概念 TOP20
        concept_counter = Counter(all_concepts)
        summary["hot_concepts"] = dict(concept_counter.most_common(20))

        return summary

    # ------------------------------------------------------------------
    # 内部方法：API 配置读取
    # ------------------------------------------------------------------

    async def _get_api_config(self) -> Dict[str, Any]:
        """
        从 Config_Service 读取龙虎榜 API 配置。

        配置键（在 system_settings 中）：
        - longhubang_api_base_url: API 基础 URL
        - longhubang_rate_limit: 每秒请求次数限制
        - longhubang_max_retries: 最大重试次数
        - longhubang_request_timeout: 请求超时（秒）

        Returns:
            API 配置字典
        """
        config = {
            "base_url": self.DEFAULT_BASE_URL,
            "max_retries": self.DEFAULT_MAX_RETRIES,
            "retry_delay": self.DEFAULT_RETRY_DELAY,
            "request_delay": self.DEFAULT_REQUEST_DELAY,
            "request_timeout": self.DEFAULT_REQUEST_TIMEOUT,
        }

        if self.config_service is None:
            return config

        try:
            settings = await self.config_service.get_system_settings()
            if not settings:
                return config

            if "longhubang_api_base_url" in settings:
                config["base_url"] = settings["longhubang_api_base_url"]
            if "longhubang_max_retries" in settings:
                config["max_retries"] = int(settings["longhubang_max_retries"])
            if "longhubang_request_timeout" in settings:
                config["request_timeout"] = int(settings["longhubang_request_timeout"])
            if "longhubang_rate_limit" in settings:
                rate_limit = int(settings["longhubang_rate_limit"])
                if rate_limit > 0:
                    config["request_delay"] = 1.0 / rate_limit

        except Exception as e:
            logger.warning(f"读取龙虎榜 API 配置失败，使用默认值: {e}")

        return config

    async def _get_scoring_config(self) -> Dict[str, List[str]]:
        """
        从 Config_Service 读取评分参数（游资名单、机构关键词）。

        配置键（在 system_settings 中）：
        - longhubang_top_youzi: 顶级游资名单（逗号分隔字符串或列表）
        - longhubang_famous_youzi: 知名游资名单
        - longhubang_institution_keywords: 机构关键词列表

        Returns:
            评分配置字典
        """
        config = {
            "top_youzi": list(self.DEFAULT_TOP_YOUZI),
            "famous_youzi": list(self.DEFAULT_FAMOUS_YOUZI),
            "institution_keywords": list(self.DEFAULT_INSTITUTION_KEYWORDS),
        }

        if self.config_service is None:
            return config

        try:
            settings = await self.config_service.get_system_settings()
            if not settings:
                return config

            for key, config_key in [
                ("longhubang_top_youzi", "top_youzi"),
                ("longhubang_famous_youzi", "famous_youzi"),
                ("longhubang_institution_keywords", "institution_keywords"),
            ]:
                if key in settings:
                    val = settings[key]
                    if isinstance(val, list):
                        config[config_key] = val
                    elif isinstance(val, str):
                        config[config_key] = [
                            v.strip() for v in val.split(",") if v.strip()
                        ]

        except Exception as e:
            logger.warning(f"读取评分配置失败，使用默认值: {e}")

        return config

    # ------------------------------------------------------------------
    # 内部方法：日期计算
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_date_list(
        date: Optional[str] = None, days: int = 1
    ) -> List[str]:
        """
        计算需要获取数据的日期列表。

        - 指定日期模式：返回单个日期
        - 最近 N 天模式：返回最近 N 个工作日（跳过周末）

        Args:
            date: 指定日期（YYYY-MM-DD 格式）
            days: 最近天数

        Returns:
            日期字符串列表（YYYY-MM-DD 格式），按日期升序排列
        """
        if date:
            return [date]

        # 最近 N 天模式：从今天往前推，跳过周末
        result = []
        current = datetime.now()
        # 乘以 2 以确保包含足够的交易日
        max_lookback = days * 3
        checked = 0

        while len(result) < days and checked < max_lookback:
            check_date = current - timedelta(days=checked)
            # 跳过周末（0=周一, 5=周六, 6=周日）
            if check_date.weekday() < 5:
                result.append(check_date.strftime("%Y-%m-%d"))
            checked += 1

        # 按日期升序排列
        result.reverse()
        return result

    # ------------------------------------------------------------------
    # 内部方法：单日数据获取（含重试）
    # ------------------------------------------------------------------

    async def _fetch_single_day(
        self, date: str, api_config: Dict[str, Any]
    ) -> Optional[List[dict]]:
        """
        获取指定日期的龙虎榜数据，包含重试机制和频率限制。

        Args:
            date: 日期（YYYY-MM-DD 格式）
            api_config: API 配置

        Returns:
            龙虎榜数据列表，或 None（获取失败）
        """
        base_url = api_config["base_url"]
        max_retries = api_config["max_retries"]
        retry_delay = api_config["retry_delay"]
        request_delay = api_config["request_delay"]
        request_timeout = api_config["request_timeout"]

        url = f"{base_url}/youzi/all"
        params = {"date": date}

        logger.info(f"[龙虎榜] 获取 {date} 的龙虎榜数据...")

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    response = await client.get(url, params=params)

                # 频率限制：请求后等待
                await asyncio.sleep(request_delay)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == 20000 and data.get("data"):
                        records = data["data"]
                        logger.info(
                            f"[龙虎榜] ✓ {date}: 获取 {len(records)} 条记录"
                        )
                        return records
                    else:
                        msg = data.get("msg", "未知错误")
                        logger.warning(f"[龙虎榜] {date}: API 返回错误: {msg}")
                        return None
                else:
                    logger.warning(
                        f"[龙虎榜] {date}: HTTP 错误 {response.status_code}"
                    )

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"[龙虎榜] {date}: 请求失败，{retry_delay}秒后重试... "
                        f"(尝试 {attempt + 1}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        f"[龙虎榜] {date}: 请求失败，已达最大重试次数: {e}"
                    )
                    return None

        return None

    # ------------------------------------------------------------------
    # 内部方法：数据持久化
    # ------------------------------------------------------------------

    async def _persist_records(self, data_list: List[dict]) -> int:
        """
        将龙虎榜数据持久化到 MongoDB longhubang_records 集合。

        使用 update_one with upsert=True 实现去重，
        以 {date, stock_code, youzi_name, yingye_bu} 为唯一键。

        Args:
            data_list: 龙虎榜数据列表

        Returns:
            成功保存/更新的记录数
        """
        if not data_list:
            return 0

        try:
            db = get_mongo_db()
            collection = db.longhubang_records
            saved_count = 0

            for record in data_list:
                try:
                    doc = self._record_to_document(record)
                    if not doc:
                        continue

                    # 构建唯一键过滤条件
                    filter_key = {
                        "date": doc["date"],
                        "stock_code": doc["stock_code"],
                        "youzi_name": doc["youzi_name"],
                        "yingye_bu": doc["yingye_bu"],
                    }

                    # upsert 操作
                    await collection.update_one(
                        filter_key,
                        {"$set": doc},
                        upsert=True,
                    )
                    saved_count += 1

                except Exception as e:
                    logger.warning(f"[龙虎榜] 保存记录失败: {e}")
                    continue

            logger.info(f"[龙虎榜] 成功保存/更新 {saved_count} 条记录")
            return saved_count

        except Exception as e:
            logger.error(f"[龙虎榜] 数据持久化失败: {e}")
            return 0

    # ------------------------------------------------------------------
    # 公开方法：报告持久化与查询
    # ------------------------------------------------------------------

    async def save_report(self, analysis_result: dict) -> str:
        """
        保存分析报告到 longhubang_analysis 集合。

        这是一个独立的公开方法，与 _save_analysis_result 不同：
        - _save_analysis_result 在 execute_ai_analysis 流程中自动调用
        - save_report 供外部（如路由层）直接调用，用于手动保存报告

        Args:
            analysis_result: 分析结果字典，应包含以下字段：
                - data_date_range: 数据日期范围
                - data_info: 数据概况
                - scoring_ranking: 评分排名列表
                - agents_analysis: 各分析师报告
                - recommended_stocks: 推荐股票列表
                - summary: 分析摘要（可选）
                - task_id: 关联的任务 ID（可选）

        Returns:
            插入文档的 ID 字符串
        """
        from app.utils.timezone import now_tz

        db = get_mongo_db()

        doc = {
            "analysis_date": now_tz(),
            "data_date_range": analysis_result.get("data_date_range", ""),
            "data_info": analysis_result.get("data_info", {}),
            "scoring_ranking": analysis_result.get("scoring_ranking", []),
            "agents_analysis": analysis_result.get("agents_analysis", {}),
            "recommended_stocks": analysis_result.get("recommended_stocks", []),
            "summary": analysis_result.get("summary", ""),
            "task_id": analysis_result.get("task_id"),
            "created_at": now_tz(),
        }

        result = await db.longhubang_analysis.insert_one(doc)
        report_id = str(result.inserted_id)
        logger.info(f"[龙虎榜] 分析报告已保存: {report_id}")
        return report_id

    async def get_reports(self, page: int, page_size: int) -> dict:
        """
        查询历史分析报告列表，支持分页和按时间排序。

        Args:
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            包含 total、page、page_size、items 的字典
        """
        db = get_mongo_db()
        collection = db.longhubang_analysis

        # 计算总数
        total = await collection.count_documents({})

        # 分页查询，按创建时间降序
        skip = (page - 1) * page_size
        cursor = collection.find(
            {},
            {
                # 返回概要字段，不返回大体积的分析师报告全文
                "analysis_date": 1,
                "data_date_range": 1,
                "data_info": 1,
                "summary": 1,
                "task_id": 1,
                "created_at": 1,
            },
        ).sort("created_at", -1).skip(skip).limit(page_size)

        items = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            items.append(doc)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }

    async def get_report_detail(self, report_id: str) -> Optional[dict]:
        """
        查询单个报告的完整详情。

        Args:
            report_id: 报告 ID（MongoDB ObjectId 字符串）

        Returns:
            报告详情字典，不存在时返回 None
        """
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            oid = ObjectId(report_id)
        except (InvalidId, Exception):
            logger.warning(f"[龙虎榜] 无效的报告 ID: {report_id}")
            return None

        db = get_mongo_db()
        doc = await db.longhubang_analysis.find_one({"_id": oid})

        if doc:
            doc["_id"] = str(doc["_id"])

        return doc

    async def delete_report(self, report_id: str) -> bool:
        """
        删除指定的历史报告。

        Args:
            report_id: 报告 ID（MongoDB ObjectId 字符串）

        Returns:
            删除成功返回 True，否则返回 False
        """
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            oid = ObjectId(report_id)
        except (InvalidId, Exception):
            logger.warning(f"[龙虎榜] 无效的报告 ID: {report_id}")
            return False

        db = get_mongo_db()
        result = await db.longhubang_analysis.delete_one({"_id": oid})

        deleted = result.deleted_count > 0
        if deleted:
            logger.info(f"[龙虎榜] 报告已删除: {report_id}")
        else:
            logger.warning(f"[龙虎榜] 报告不存在: {report_id}")

        return deleted

    async def get_statistics(self) -> dict:
        """
        获取数据库统计信息。

        返回内容：
        - total_records: longhubang_records 集合总记录数
        - total_stocks: 涉及的唯一股票数
        - total_youzi: 涉及的唯一游资数
        - total_reports: longhubang_analysis 集合报告数
        - date_range: 数据日期范围 {start, end}

        Returns:
            统计信息字典
        """
        db = get_mongo_db()
        records_col = db.longhubang_records
        analysis_col = db.longhubang_analysis

        # 总记录数
        total_records = await records_col.count_documents({})

        # 涉及股票数（唯一 stock_code）
        stock_codes = await records_col.distinct("stock_code")
        total_stocks = len(stock_codes)

        # 涉及游资数（唯一 youzi_name）
        youzi_names = await records_col.distinct("youzi_name")
        total_youzi = len(youzi_names)

        # 分析报告数
        total_reports = await analysis_col.count_documents({})

        # 数据日期范围
        date_range: Dict[str, Optional[str]] = {"start": None, "end": None}
        if total_records > 0:
            # 最早日期
            earliest = await records_col.find_one(
                {}, {"date": 1}, sort=[("date", 1)]
            )
            if earliest:
                date_range["start"] = earliest.get("date")

            # 最晚日期
            latest = await records_col.find_one(
                {}, {"date": 1}, sort=[("date", -1)]
            )
            if latest:
                date_range["end"] = latest.get("date")

        return {
            "total_records": total_records,
            "total_stocks": total_stocks,
            "total_youzi": total_youzi,
            "total_reports": total_reports,
            "date_range": date_range,
        }

    async def get_records(
        self,
        start_date: str,
        end_date: str,
        stock_code: Optional[str] = None,
    ) -> List[dict]:
        """
        查询龙虎榜历史数据。

        支持按日期范围和股票代码筛选，按日期降序和净流入降序排列。

        Args:
            start_date: 开始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            stock_code: 股票代码（可选）

        Returns:
            记录列表
        """
        db = get_mongo_db()
        collection = db.longhubang_records

        query: Dict[str, Any] = {
            "date": {"$gte": start_date, "$lte": end_date},
        }
        if stock_code:
            query["stock_code"] = stock_code

        cursor = collection.find(query).sort(
            [("date", -1), ("net_inflow", -1)]
        )

        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)

        return results

    async def get_top_youzi(
        self, start_date: str, end_date: str, limit: int = 20
    ) -> List[dict]:
        """
        查询活跃游资排名。

        使用 MongoDB 聚合管道，按日期范围筛选后，
        按游资名称分组统计交易次数、总买入、总卖出、总净流入，
        按总净流入降序排列。

        Args:
            start_date: 开始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            limit: 返回数量限制（默认 20）

        Returns:
            游资排名列表，每项包含 youzi_name、trade_count、
            total_buy、total_sell、total_net_inflow
        """
        db = get_mongo_db()
        collection = db.longhubang_records

        pipeline = [
            # 按日期范围筛选
            {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
            # 按游资名称分组
            {
                "$group": {
                    "_id": "$youzi_name",
                    "trade_count": {"$sum": 1},
                    "total_buy": {"$sum": "$buy_amount"},
                    "total_sell": {"$sum": "$sell_amount"},
                    "total_net_inflow": {"$sum": "$net_inflow"},
                }
            },
            # 按总净流入降序排列
            {"$sort": {"total_net_inflow": -1}},
            # 限制返回数量
            {"$limit": limit},
            # 重命名字段
            {
                "$project": {
                    "_id": 0,
                    "youzi_name": "$_id",
                    "trade_count": 1,
                    "total_buy": 1,
                    "total_sell": 1,
                    "total_net_inflow": 1,
                }
            },
        ]

        results = []
        async for doc in collection.aggregate(pipeline):
            results.append(doc)

        return results

    async def get_top_stocks(
        self, start_date: str, end_date: str, limit: int = 20
    ) -> List[dict]:
        """
        查询热门股票排名。

        使用 MongoDB 聚合管道，按日期范围筛选后，
        按股票代码分组统计游资关注数、总买入、总卖出、总净流入，
        按总净流入降序排列。

        Args:
            start_date: 开始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            limit: 返回数量限制（默认 20）

        Returns:
            股票排名列表，每项包含 stock_code、stock_name、
            youzi_count、total_buy、total_sell、total_net_inflow、concepts
        """
        db = get_mongo_db()
        collection = db.longhubang_records

        pipeline = [
            # 按日期范围筛选
            {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
            # 按股票代码分组
            {
                "$group": {
                    "_id": "$stock_code",
                    "stock_name": {"$first": "$stock_name"},
                    "youzi_count": {"$addToSet": "$youzi_name"},
                    "total_buy": {"$sum": "$buy_amount"},
                    "total_sell": {"$sum": "$sell_amount"},
                    "total_net_inflow": {"$sum": "$net_inflow"},
                    "concepts": {"$first": "$concepts"},
                }
            },
            # 按总净流入降序排列
            {"$sort": {"total_net_inflow": -1}},
            # 限制返回数量
            {"$limit": limit},
            # 重命名字段，将 youzi_count 从数组转为数量
            {
                "$project": {
                    "_id": 0,
                    "stock_code": "$_id",
                    "stock_name": 1,
                    "youzi_count": {"$size": "$youzi_count"},
                    "total_buy": 1,
                    "total_sell": 1,
                    "total_net_inflow": 1,
                    "concepts": 1,
                }
            },
        ]

        results = []
        async for doc in collection.aggregate(pipeline):
            results.append(doc)

        return results

    # ------------------------------------------------------------------
    # 内部方法：记录转换
    # ------------------------------------------------------------------

    @staticmethod
    def _record_to_document(record: dict) -> Optional[dict]:
        """
        将 API 返回的原始记录转换为 MongoDB 文档格式。

        支持中文键名和拼音键名两种格式。

        Args:
            record: API 返回的原始记录

        Returns:
            MongoDB 文档字典，或 None（数据无效）
        """
        date = record.get("rq") or record.get("日期") or ""
        stock_code = record.get("gpdm") or record.get("股票代码") or ""
        youzi_name = record.get("yzmc") or record.get("游资名称") or ""
        yingye_bu = record.get("yyb") or record.get("营业部") or ""

        # 必须有日期和股票代码
        if not date or not stock_code:
            return None

        return {
            "date": str(date),
            "stock_code": str(stock_code),
            "stock_name": str(record.get("gpmc") or record.get("股票名称") or ""),
            "youzi_name": str(youzi_name),
            "yingye_bu": str(yingye_bu),
            "buy_amount": _safe_float(record.get("mrje") or record.get("买入金额")),
            "sell_amount": _safe_float(record.get("mcje") or record.get("卖出金额")),
            "net_inflow": _safe_float(record.get("jlrje") or record.get("净流入金额")),
            "concepts": str(record.get("gl") or record.get("概念") or ""),
            "board_type": str(record.get("sblx") or record.get("榜单类型") or ""),
            "updated_at": datetime.now(tz=_UTC),
        }


# ======================================================================
# 模块级辅助函数
# ======================================================================


def _safe_float(value: Any) -> float:
    """安全地将值转换为 float，无法转换时返回 0.0"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
