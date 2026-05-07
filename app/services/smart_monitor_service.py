"""
AI 盯盘服务

基于 AI 决策引擎对持仓股票进行智能监控，生成 BUY/SELL/HOLD 决策建议。
直接以 paper_positions 为数据源，通过 Unified_LLM_Service 调用 AI 进行决策。

从 aiagents-stock/ 的智能盯盘模块迁移并重构为异步服务。
"""

import json
import logging
import re
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

# 有效的决策动作
VALID_ACTIONS = ["BUY", "SELL", "HOLD"]

# 有效的风险等级
VALID_RISK_LEVELS = ["low", "medium", "high"]

# 有效的任务状态
VALID_TASK_STATUSES = ["running", "stopped", "position_cleared"]


class SmartMonitorService:
    """AI 盯盘服务 — 基于 AI 决策引擎对持仓股票进行智能监控"""

    def __init__(self, config_service=None):
        """
        初始化 AI 盯盘服务。

        Args:
            config_service: ConfigService 实例，用于读取系统配置。
                           如果为 None，则使用默认参数。
        """
        self.config_service = config_service

    # ------------------------------------------------------------------
    # 盯盘任务 CRUD
    # ------------------------------------------------------------------

    async def create_task(
        self,
        user_id: str,
        stock_code: str,
        stock_name: str = "",
        check_interval: int = 300,
        auto_notify: bool = True,
        trading_hours_only: bool = True,
    ) -> Dict[str, Any]:
        """
        创建盯盘任务。

        自动从 paper_positions 读取持仓成本和数量填充到任务配置。
        在 (user_id, stock_code) 上保持唯一，已存在则返回错误。

        Args:
            user_id: 用户 ID
            stock_code: 股票代码
            stock_name: 股票名称（可选）
            check_interval: 检查间隔（秒），默认 300
            auto_notify: 是否自动通知，默认 True
            trading_hours_only: 是否仅交易时段检查，默认 True

        Returns:
            {"success": True, "task": {...}} 或
            {"success": False, "error": "错误信息"}
        """
        db = get_mongo_db()

        # 检查是否已存在同一用户同一股票的盯盘任务
        existing = await db["smart_monitor_tasks"].find_one(
            {"user_id": user_id, "stock_code": stock_code}
        )
        if existing:
            return {
                "success": False,
                "error": f"盯盘任务已存在: {stock_code}",
            }

        # 从 paper_positions 读取持仓信息
        position_doc = await db["paper_positions"].find_one(
            {"user_id": user_id, "code": stock_code}
        )
        position_info = {
            "quantity": int(position_doc.get("quantity", 0)) if position_doc else 0,
            "avg_cost": float(position_doc.get("avg_cost", 0.0)) if position_doc else 0.0,
            "currency": position_doc.get("currency", "CNY") if position_doc else "CNY",
        }

        now = datetime.now()
        task_doc = {
            "user_id": user_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "CN",
            "enabled": True,
            "status": "running",
            "check_interval": check_interval,
            "auto_notify": auto_notify,
            "trading_hours_only": trading_hours_only,
            "position_info": position_info,
            "last_check_time": None,
            "last_decision": None,
            "created_at": now,
            "updated_at": now,
        }

        try:
            result = await db["smart_monitor_tasks"].insert_one(task_doc)
            task_doc["_id"] = str(result.inserted_id)
            logger.info(
                f"[AI盯盘] ✅ 创建盯盘任务: user={user_id}, "
                f"stock={stock_code}, interval={check_interval}s"
            )
            return {"success": True, "task": task_doc}
        except Exception as e:
            # 处理唯一索引冲突
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                return {
                    "success": False,
                    "error": f"盯盘任务已存在: {stock_code}",
                }
            logger.error(f"[AI盯盘] 创建盯盘任务失败: {e}")
            return {"success": False, "error": f"创建失败: {e}"}

    async def list_tasks(self, user_id: str) -> List[Dict[str, Any]]:
        """
        列出用户的所有盯盘任务。

        Args:
            user_id: 用户 ID

        Returns:
            盯盘任务列表
        """
        db = get_mongo_db()
        cursor = db["smart_monitor_tasks"].find({"user_id": user_id})
        tasks = await cursor.to_list(None)

        for task in tasks:
            if "_id" in task:
                task["_id"] = str(task["_id"])

        logger.info(f"[AI盯盘] 查询盯盘任务: user={user_id}, 返回 {len(tasks)} 条")
        return tasks

    async def update_task(
        self, user_id: str, task_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        更新盯盘任务配置（启停、间隔等）。

        Args:
            user_id: 用户 ID
            task_id: 任务 ID（MongoDB _id 字符串）
            updates: 更新字段字典

        Returns:
            {"success": True, "task": {...}} 或
            {"success": False, "error": "错误信息"}
        """
        from bson import ObjectId

        db = get_mongo_db()

        # 只允许更新的字段白名单
        allowed_fields = {
            "enabled", "status", "check_interval", "auto_notify",
            "trading_hours_only", "stock_name",
        }
        filtered_updates = {
            k: v for k, v in updates.items() if k in allowed_fields
        }

        if not filtered_updates:
            return {"success": False, "error": "没有有效的更新字段"}

        # 如果更新了 enabled 字段，同步更新 status
        if "enabled" in filtered_updates:
            if filtered_updates["enabled"]:
                filtered_updates["status"] = "running"
            else:
                filtered_updates["status"] = "stopped"

        filtered_updates["updated_at"] = datetime.now()

        try:
            result = await db["smart_monitor_tasks"].update_one(
                {"_id": ObjectId(task_id), "user_id": user_id},
                {"$set": filtered_updates},
            )
            if result.matched_count == 0:
                return {"success": False, "error": "任务不存在或无权限"}

            # 返回更新后的任务
            updated_task = await db["smart_monitor_tasks"].find_one(
                {"_id": ObjectId(task_id)}
            )
            if updated_task and "_id" in updated_task:
                updated_task["_id"] = str(updated_task["_id"])

            logger.info(
                f"[AI盯盘] ✅ 更新盯盘任务: task_id={task_id}, "
                f"updates={list(filtered_updates.keys())}"
            )
            return {"success": True, "task": updated_task}
        except Exception as e:
            logger.error(f"[AI盯盘] 更新盯盘任务失败: {e}")
            return {"success": False, "error": f"更新失败: {e}"}

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """
        删除盯盘任务。

        Args:
            user_id: 用户 ID
            task_id: 任务 ID（MongoDB _id 字符串）

        Returns:
            是否删除成功
        """
        from bson import ObjectId

        db = get_mongo_db()

        try:
            result = await db["smart_monitor_tasks"].delete_one(
                {"_id": ObjectId(task_id), "user_id": user_id}
            )
            if result.deleted_count > 0:
                logger.info(f"[AI盯盘] ✅ 删除盯盘任务: task_id={task_id}")
                return True
            else:
                logger.warning(
                    f"[AI盯盘] 删除盯盘任务失败: task_id={task_id} 不存在或无权限"
                )
                return False
        except Exception as e:
            logger.error(f"[AI盯盘] 删除盯盘任务异常: {e}")
            return False

    # ------------------------------------------------------------------
    # AI 决策引擎
    # ------------------------------------------------------------------

    async def execute_ai_decision(
        self,
        user_id: str,
        stock_code: str,
        market: str = "CN",
        model_name: str = None,
    ) -> Dict[str, Any]:
        """
        执行单次 AI 决策分析。

        流程: 获取行情+技术指标 → 读取持仓信息 → 构建 prompt(含T+1规则) → LLM 决策
        → 持久化到 smart_monitor_decisions → 触发通知（BUY/SELL 时）

        Args:
            user_id: 用户 ID
            stock_code: 股票代码
            market: 市场类型 (CN/HK/US)，默认 CN
            model_name: 用户指定的模型名称（可选）

        Returns:
            {"success": True, "decision": {...}} 或
            {"success": False, "error": "错误信息"}
        """
        from app.services import market_data_helper

        db = get_mongo_db()

        # 1. 获取实时行情数据
        try:
            market_data = await market_data_helper.get_market_data(stock_code, market)
        except Exception as e:
            logger.warning(f"[AI盯盘] 行情获取失败: {stock_code} ({market}): {e}")
            return {"success": False, "error": f"行情获取失败: {e}"}

        # 2. 获取技术指标（失败时降级处理）
        try:
            indicators = await market_data_helper.get_technical_indicators(
                stock_code, market
            )
        except Exception as e:
            logger.warning(
                f"[AI盯盘] 技术指标获取失败，降级处理: {stock_code}: {e}"
            )
            indicators = {}

        # 3. 获取持仓信息
        position_doc = await db["paper_positions"].find_one(
            {"user_id": user_id, "code": stock_code}
        )
        position_info = {
            "quantity": int(position_doc.get("quantity", 0)) if position_doc else 0,
            "avg_cost": float(position_doc.get("avg_cost", 0.0)) if position_doc else 0.0,
            "currency": position_doc.get("currency", "CNY") if position_doc else "CNY",
        }

        # 4. 构建决策 prompt
        messages = self._build_decision_prompt(
            stock_code, market_data, indicators, position_info, market
        )

        # 5. 调用 LLM
        try:
            llm_call = await self._get_llm_call_func(model_name)
            llm_response = await llm_call(messages)
        except Exception as e:
            logger.error(f"[AI盯盘] LLM 调用失败: {stock_code}: {e}")
            return {"success": False, "error": f"AI 决策服务调用失败: {e}"}

        # 6. 解析决策结果
        decision = self._parse_decision_result(llm_response)

        # 7. 确定交易时段
        now = datetime.now()
        current_time = now.time()
        if time(9, 30) <= current_time <= time(11, 30):
            trading_session = "morning"
        elif time(13, 0) <= current_time <= time(15, 0):
            trading_session = "afternoon"
        else:
            trading_session = "off_hours"

        # 8. 获取盯盘任务中的 stock_name
        task_doc = await db["smart_monitor_tasks"].find_one(
            {"user_id": user_id, "stock_code": stock_code}
        )
        stock_name = task_doc.get("stock_name", "") if task_doc else ""

        # 9. 持久化到 smart_monitor_decisions
        decision_doc = {
            "user_id": user_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "decision_time": now,
            "trading_session": trading_session,
            "action": decision["action"],
            "confidence": decision["confidence"],
            "reasoning": decision["reasoning"],
            "risk_level": decision["risk_level"],
            "key_price_levels": decision["key_price_levels"],
            "position_size_pct": decision["position_size_pct"],
            "stop_loss_pct": decision["stop_loss_pct"],
            "take_profit_pct": decision["take_profit_pct"],
            "market_data_snapshot": {
                **market_data,
                **{f"indicator_{k}": v for k, v in indicators.items()},
            },
            "model_name": model_name or "default",
            "created_at": now,
        }

        try:
            await db["smart_monitor_decisions"].insert_one(decision_doc)
            logger.info(
                f"[AI盯盘] ✅ 决策完成: {stock_code}, "
                f"action={decision['action']}, confidence={decision['confidence']}"
            )
        except Exception as e:
            logger.error(f"[AI盯盘] 决策持久化失败: {stock_code}: {e}")

        # 10. 更新盯盘任务的 last_check_time 和 last_decision
        try:
            if task_doc:
                await db["smart_monitor_tasks"].update_one(
                    {"user_id": user_id, "stock_code": stock_code},
                    {
                        "$set": {
                            "last_check_time": now,
                            "last_decision": {
                                "action": decision["action"],
                                "confidence": decision["confidence"],
                                "time": now,
                            },
                            "updated_at": now,
                        }
                    },
                )
        except Exception as e:
            logger.warning(f"[AI盯盘] 更新任务状态失败: {e}")

        # 11. 触发通知（BUY/SELL 时）
        if decision["action"] in ("BUY", "SELL"):
            decision_with_meta = {
                **decision,
                "stock_code": stock_code,
                "stock_name": stock_name,
            }
            try:
                await self._send_decision_notification(user_id, decision_with_meta)
            except Exception as e:
                logger.warning(f"[AI盯盘] 通知推送失败: {e}")

        return {"success": True, "decision": decision}

    # ------------------------------------------------------------------
    # 纯函数：构建决策 Prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_decision_prompt(
        stock_code: str,
        market_data: Dict[str, Any],
        indicators: Dict[str, Any],
        position_info: Dict[str, Any],
        market: str = "CN",
    ) -> List[Dict[str, str]]:
        """
        构建 AI 决策 prompt。

        A 股市场包含 T+1 规则约束:
        - 买入当天不可卖出
        - 单只股票建议仓位不超过 30%
        - 止损位建议 -5%，止盈位建议 +8% 至 +15%

        纯函数（静态方法），方便属性测试。

        Args:
            stock_code: 股票代码
            market_data: 实时行情数据
            indicators: 技术指标
            position_info: 持仓信息
            market: 市场类型

        Returns:
            LLM messages 列表
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
        currency = position_info.get("currency", "CNY")

        # 计算持仓盈亏
        if avg_cost > 0 and price > 0:
            pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)
            pnl_amount = round((price - avg_cost) * quantity, 2)
        else:
            pnl_pct = 0
            pnl_amount = 0

        has_position = quantity > 0 and avg_cost > 0

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

        # 构建 A 股 T+1 规则约束文本
        t1_rules = ""
        if market.upper() == "CN":
            t1_rules = """
【A股 T+1 交易规则约束】
⚠️ 买入当天不可卖出（T+1 规则），必须等到下一个交易日才能卖出。
⚠️ 单只股票建议仓位不超过 30%（因 T+1 风险较大）。
⚠️ 止损位建议 -5%（次日开盘执行）。
⚠️ 止盈位建议 +8% 至 +15%（分批止盈）。
⚠️ 只能做多，不能做空。
⚠️ 涨跌停限制：主板 ±10%，创业板/科创板 ±20%，ST ±5%。
"""

        # 持仓状态文本
        if has_position:
            position_text = f"""
【当前持仓】
- 持仓数量: {quantity} 股
- 持仓成本: {avg_cost} {currency}
- 当前盈亏: {pnl_pct}% ({pnl_amount} {currency})
- 持仓状态: 已持仓
"""
        else:
            position_text = """
【当前持仓】
- 持仓状态: 未持仓
- 可考虑买入，但需确保技术面强势
"""

        prompt = f"""请对以下股票进行实时决策分析，给出 BUY/SELL/HOLD 建议。

【股票信息】
- 股票代码: {stock_code}
- 市场: {market}

【实时行情】
- 当前价格: {price}
- 涨跌幅: {change_pct}%
- 成交量: {volume}
- 最高价: {high}
- 最低价: {low}
- 开盘价: {open_price}
- 昨收价: {prev_close}
{position_text}
【技术指标】
- 均线: MA5={ma5}, MA20={ma20}, MA60={ma60}
- MACD: DIF={macd_dif}, DEA={macd_dea}, MACD柱={macd_hist}
- RSI(14): {rsi}
- KDJ: K={kdj_k}, D={kdj_d}, J={kdj_j}
- 布林带: 上轨={boll_upper}, 中轨={boll_middle}, 下轨={boll_lower}
{t1_rules}
请严格按照以下 JSON 格式输出决策结果（用 ```json``` 包裹）：

```json
{{
    "action": "BUY/SELL/HOLD",
    "confidence": 0-100,
    "reasoning": "详细的决策理由（200-300字）",
    "position_size_pct": 10-30,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "risk_level": "low/medium/high",
    "key_price_levels": {{
        "support": 0.0,
        "resistance": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0
    }}
}}
```

决策要求：
1. action: BUY（买入）、SELL（卖出）或 HOLD（持有/观望）
2. confidence: 信心度 0-100
3. reasoning: 包含技术分析、风险评估的详细理由
4. risk_level: 风险等级 low/medium/high
5. key_price_levels: 关键价位（支撑位、阻力位、止损位、止盈位）"""

        system_message = (
            "你是一位资深的A股量化交易专家，擅长技术分析和风险管理。"
            "请严格按照要求的 JSON 格式输出决策结果。"
            "决策必须考虑 T+1 交易规则和仓位管理。"
        )

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ]

    # ------------------------------------------------------------------
    # 纯函数：解析决策结果
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_decision_result(llm_response: str) -> Dict[str, Any]:
        """
        解析 LLM 响应为 AI_Decision。

        纯函数（静态方法），方便属性测试。
        能处理各种 LLM 响应格式：纯 JSON、markdown 包裹的 JSON、
        带有额外文本的 JSON 等。

        Args:
            llm_response: LLM 原始响应文本

        Returns:
            AI_Decision 字典，包含:
            action, confidence, reasoning, risk_level,
            key_price_levels, position_size_pct, stop_loss_pct, take_profit_pct
        """
        # 默认保守决策
        default_decision = {
            "action": "HOLD",
            "confidence": 0,
            "reasoning": "AI 响应解析失败，默认持有观望。",
            "risk_level": "high",
            "key_price_levels": {
                "support": 0.0,
                "resistance": 0.0,
                "stop_loss": 0.0,
                "take_profit": 0.0,
            },
            "position_size_pct": 0,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        }

        if not llm_response or not llm_response.strip():
            return default_decision

        # 尝试从响应中提取 JSON
        parsed_json = _extract_json_from_response(llm_response)

        if parsed_json is None:
            default_decision["reasoning"] = llm_response.strip()[:500]
            return default_decision

        result = {}

        # action: 必须是 BUY/SELL/HOLD 之一
        raw_action = str(parsed_json.get("action", "HOLD")).strip().upper()
        result["action"] = raw_action if raw_action in VALID_ACTIONS else "HOLD"

        # confidence: 0-100 的数值
        raw_confidence = parsed_json.get("confidence", 0)
        try:
            confidence = int(float(raw_confidence))
            result["confidence"] = max(0, min(100, confidence))
        except (ValueError, TypeError):
            result["confidence"] = 0

        # reasoning
        raw_reasoning = parsed_json.get("reasoning", "")
        result["reasoning"] = (
            str(raw_reasoning).strip() if raw_reasoning else "AI 决策完成。"
        )

        # risk_level
        raw_risk = str(parsed_json.get("risk_level", "medium")).strip().lower()
        result["risk_level"] = raw_risk if raw_risk in VALID_RISK_LEVELS else "medium"

        # key_price_levels
        raw_levels = parsed_json.get("key_price_levels", {})
        if isinstance(raw_levels, dict):
            result["key_price_levels"] = {
                "support": _safe_positive_float(raw_levels.get("support"), 0.0),
                "resistance": _safe_positive_float(raw_levels.get("resistance"), 0.0),
                "stop_loss": _safe_positive_float(raw_levels.get("stop_loss"), 0.0),
                "take_profit": _safe_positive_float(raw_levels.get("take_profit"), 0.0),
            }
        else:
            result["key_price_levels"] = default_decision["key_price_levels"]

        # position_size_pct
        raw_psp = parsed_json.get("position_size_pct", 20)
        try:
            psp = int(float(raw_psp))
            result["position_size_pct"] = max(0, min(100, psp))
        except (ValueError, TypeError):
            result["position_size_pct"] = 20

        # stop_loss_pct
        raw_sl = parsed_json.get("stop_loss_pct", 5.0)
        try:
            result["stop_loss_pct"] = max(0.0, float(raw_sl))
        except (ValueError, TypeError):
            result["stop_loss_pct"] = 5.0

        # take_profit_pct
        raw_tp = parsed_json.get("take_profit_pct", 10.0)
        try:
            result["take_profit_pct"] = max(0.0, float(raw_tp))
        except (ValueError, TypeError):
            result["take_profit_pct"] = 10.0

        return result

    # ------------------------------------------------------------------
    # 交易时段判断（纯函数）
    # ------------------------------------------------------------------

    @staticmethod
    def is_trading_session(dt: datetime = None) -> bool:
        """
        判断给定时间是否在 A 股交易时段内。

        上午盘: 9:30-11:30, 下午盘: 13:00-15:00
        周末（周六、周日）始终返回 False。

        纯函数（静态方法），方便属性测试。

        Args:
            dt: 日期时间，默认使用当前时间

        Returns:
            是否在交易时段内
        """
        if dt is None:
            dt = datetime.now()

        # 周末返回 False（weekday: 0=周一, ..., 5=周六, 6=周日）
        if dt.weekday() >= 5:
            return False

        current_time = dt.time()

        # 上午盘: 9:30-11:30
        morning_start = time(9, 30)
        morning_end = time(11, 30)

        # 下午盘: 13:00-15:00
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)

        return (
            (morning_start <= current_time <= morning_end)
            or (afternoon_start <= current_time <= afternoon_end)
        )

    # ------------------------------------------------------------------
    # 定时触发检查
    # ------------------------------------------------------------------

    async def trigger_check(self) -> None:
        """
        定时触发检查（由 APScheduler 调用）。

        遍历所有 enabled=true 的任务，按 check_interval 和交易时段过滤后执行。
        单个任务失败不影响其他任务。
        """
        db = get_mongo_db()
        now = datetime.now()

        try:
            tasks = await db["smart_monitor_tasks"].find(
                {"enabled": True}
            ).to_list(None)
        except Exception as e:
            logger.error(f"[AI盯盘] 查询盯盘任务失败: {e}")
            return

        logger.info(f"[AI盯盘] 定时检查: 共 {len(tasks)} 个启用的任务")

        for task in tasks:
            try:
                stock_code = task.get("stock_code", "")
                user_id = task.get("user_id", "")
                check_interval = task.get("check_interval", 300)
                trading_hours_only = task.get("trading_hours_only", True)

                # 检查交易时段
                if trading_hours_only and not self.is_trading_session(now):
                    logger.debug(
                        f"[AI盯盘] 跳过 {stock_code}: 非交易时段"
                    )
                    continue

                # 检查 check_interval（上次检查时间 + 间隔 > 当前时间则跳过）
                last_check = task.get("last_check_time")
                if last_check:
                    if isinstance(last_check, datetime):
                        elapsed = (now - last_check).total_seconds()
                    else:
                        elapsed = float("inf")

                    if elapsed < check_interval:
                        logger.debug(
                            f"[AI盯盘] 跳过 {stock_code}: "
                            f"距上次检查仅 {elapsed:.0f}s < {check_interval}s"
                        )
                        continue

                # 执行 AI 决策
                logger.info(f"[AI盯盘] 执行决策: {stock_code} (user={user_id})")
                await self.execute_ai_decision(
                    user_id=user_id,
                    stock_code=stock_code,
                    market=task.get("market", "CN"),
                )

            except Exception as e:
                logger.error(
                    f"[AI盯盘] 任务执行异常: {task.get('stock_code', '?')}: {e}"
                )
                # 单个任务失败不影响其他任务
                continue

    # ------------------------------------------------------------------
    # 决策历史查询
    # ------------------------------------------------------------------

    async def get_decisions(
        self,
        user_id: str,
        stock_code: str = None,
        start_time: str = None,
        end_time: str = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询 AI 决策历史。

        Args:
            user_id: 用户 ID
            stock_code: 股票代码（可选，不传则查询所有）
            start_time: 开始时间 ISO 格式（可选）
            end_time: 结束时间 ISO 格式（可选）
            limit: 返回记录数上限，默认 50

        Returns:
            决策历史列表
        """
        db = get_mongo_db()

        query: Dict[str, Any] = {"user_id": user_id}

        if stock_code:
            query["stock_code"] = stock_code

        # 时间范围过滤
        time_filter: Dict[str, Any] = {}
        if start_time:
            try:
                time_filter["$gte"] = datetime.fromisoformat(start_time)
            except (ValueError, TypeError):
                pass
        if end_time:
            try:
                time_filter["$lte"] = datetime.fromisoformat(end_time)
            except (ValueError, TypeError):
                pass
        if time_filter:
            query["decision_time"] = time_filter

        cursor = (
            db["smart_monitor_decisions"]
            .find(query)
            .sort("decision_time", -1)
            .limit(limit)
        )
        results = await cursor.to_list(length=limit)

        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])

        logger.info(
            f"[AI盯盘] 查询决策历史: user={user_id}, "
            f"stock={stock_code or 'all'}, 返回 {len(results)} 条"
        )
        return results

    # ------------------------------------------------------------------
    # 持仓状态检查
    # ------------------------------------------------------------------

    async def check_position_status(self, user_id: str) -> None:
        """
        检查盯盘任务对应的持仓状态。

        如果持仓已清空（quantity=0 或记录不存在），
        标记任务为 'position_cleared'，但保留任务配置。

        Args:
            user_id: 用户 ID
        """
        db = get_mongo_db()

        # 获取用户所有启用的盯盘任务
        tasks = await db["smart_monitor_tasks"].find(
            {"user_id": user_id, "status": {"$ne": "position_cleared"}}
        ).to_list(None)

        for task in tasks:
            stock_code = task.get("stock_code", "")

            try:
                # 检查 paper_positions 中的持仓
                position = await db["paper_positions"].find_one(
                    {"user_id": user_id, "code": stock_code}
                )

                quantity = int(position.get("quantity", 0)) if position else 0

                if quantity == 0:
                    # 持仓已清空，标记任务状态
                    await db["smart_monitor_tasks"].update_one(
                        {"_id": task["_id"]},
                        {
                            "$set": {
                                "status": "position_cleared",
                                "enabled": False,
                                "updated_at": datetime.now(),
                            }
                        },
                    )
                    logger.info(
                        f"[AI盯盘] 持仓已清空，标记任务: "
                        f"user={user_id}, stock={stock_code}"
                    )

            except Exception as e:
                logger.error(
                    f"[AI盯盘] 检查持仓状态异常: {stock_code}: {e}"
                )
                continue

    # ------------------------------------------------------------------
    # 决策通知推送
    # ------------------------------------------------------------------

    async def _send_decision_notification(
        self, user_id: str, decision: Dict[str, Any]
    ) -> None:
        """
        当 action 为 BUY 或 SELL 时，推送决策通知。

        通知内容包含股票代码、股票名称、决策方向、信心度和核心理由摘要。
        同时写入 stock_monitor_notifications 集合，并通过统一通知系统推送。

        Args:
            user_id: 用户 ID
            decision: 决策结果字典，需包含 stock_code, stock_name, action,
                     confidence, reasoning
        """
        action = decision.get("action", "HOLD")

        # 仅在 BUY 或 SELL 时推送通知
        if action not in ("BUY", "SELL"):
            return

        stock_code = decision.get("stock_code", "")
        stock_name = decision.get("stock_name", "")
        confidence = decision.get("confidence", 0)
        reasoning = decision.get("reasoning", "")

        # 构建通知类型
        notification_type = "ai_buy" if action == "BUY" else "ai_sell"

        # 构建通知消息
        action_text = "🟢 买入" if action == "BUY" else "🔴 卖出"
        reasoning_summary = (
            reasoning[:150] + "..." if len(reasoning) > 150 else reasoning
        )
        message = (
            f"{action_text}信号 - {stock_name}({stock_code}) "
            f"| 信心度: {confidence}% | {reasoning_summary}"
        )

        now = datetime.now()

        # 写入 stock_monitor_notifications 集合
        db = get_mongo_db()
        notification_doc = {
            "user_id": user_id,
            "symbol": stock_code,
            "name": stock_name,
            "type": notification_type,
            "message": message,
            "current_price": 0.0,
            "trigger_value": 0.0,
            "triggered_at": now,
            "sent": False,
            "read": False,
            "source": "smart_monitor",
            "created_at": now,
        }

        try:
            await db["stock_monitor_notifications"].insert_one(notification_doc)
        except Exception as e:
            logger.warning(f"[AI盯盘] 通知写入 DB 失败: {e}")

        # 通过统一通知系统推送（notifications 集合 + WebSocket）
        try:
            from app.services.notifications_service import get_notifications_service
            from app.models.notification import NotificationCreate

            notif_svc = get_notifications_service()
            title = f"AI盯盘 {action_text} - {stock_name}({stock_code})"
            await notif_svc.create_and_publish(NotificationCreate(
                user_id=user_id,
                type="alert",
                title=title,
                content=message,
                source="smart_monitor",
                severity="warning" if action == "SELL" else "success",
                metadata={
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "action": action,
                    "confidence": confidence,
                },
            ))

            # 更新 stock_monitor_notifications 的 sent 状态
            await db["stock_monitor_notifications"].update_one(
                {"user_id": user_id, "symbol": stock_code, "triggered_at": now},
                {"$set": {"sent": True}},
            )
            logger.info(
                f"[AI盯盘] ✅ 通知已推送: {stock_code} {action_text}"
            )
        except Exception as e:
            # 推送失败不影响主流程，通知已写入 DB 供后续拉取
            logger.warning(f"[AI盯盘] 通知推送失败: {e}")

    # ------------------------------------------------------------------
    # 内部方法：LLM 调用
    # ------------------------------------------------------------------

    async def _get_llm_call_func(self, specified_model: str = None):
        """
        获取 LLM 调用函数。

        通过 unified_llm_service 统一获取模型配置，
        与 portfolio_analysis_service._get_llm_call_func 模式一致。

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
        temperature = 0.3  # 决策场景使用较低温度
        max_tokens_default = 2000

        merged_config = None

        # 方式 1：用户指定模型
        if specified_model:
            try:
                merged_config = await unified_llm_service.get_model_config(
                    specified_model
                )
                if merged_config and merged_config.api_key:
                    logger.info(
                        f"[AI盯盘 LLM] ✅ 使用用户指定模型: "
                        f"model={merged_config.model_name}"
                    )
                else:
                    merged_config = None
            except Exception as e:
                logger.warning(
                    f"[AI盯盘 LLM] 查找用户指定模型失败: {e}"
                )

        # 方式 2：推荐模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.recommend_model(
                    task_type="analysis", depth="标准"
                )
                if merged_config:
                    logger.info(
                        f"[AI盯盘 LLM] ✅ 使用推荐模型: "
                        f"model={merged_config.model_name}"
                    )
            except Exception as e:
                logger.warning(
                    f"[AI盯盘 LLM] recommend_model 获取失败: {e}"
                )

        # 方式 3：默认模型
        if not merged_config:
            try:
                merged_config = await unified_llm_service.get_default_model()
                if merged_config:
                    logger.info(
                        f"[AI盯盘 LLM] ✅ 使用默认模型: "
                        f"model={merged_config.model_name}"
                    )
            except Exception as e:
                logger.warning(
                    f"[AI盯盘 LLM] get_default_model 获取失败: {e}"
                )

        # 从 MergedModelConfig 提取参数
        if merged_config:
            model_name = merged_config.model_name
            api_key = merged_config.api_key
            api_base = merged_config.api_base
            temperature = merged_config.temperature or 0.3
            max_tokens_default = merged_config.max_tokens or 2000

        # 方式 4：回退到环境变量
        if not api_key:
            import os

            # 根据模型名称选择对应的环境变量
            if model_name and "deepseek" in model_name.lower():
                api_key = os.getenv("DEEPSEEK_API_KEY", "")
                api_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                if not api_key:
                    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
                    api_base = os.getenv(
                        "DASHSCOPE_BASE_URL",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    )
                model_name = model_name or "deepseek-chat"
            else:
                api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv(
                    "OPENAI_API_KEY", ""
                )
                api_base = os.getenv(
                    "DASHSCOPE_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
                model_name = model_name or os.getenv("DEFAULT_MODEL", "qwen-plus")

            # 最终兜底
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
                f"[AI盯盘 LLM] 回退到环境变量: model={model_name}, base={api_base}"
            )

        client = AsyncOpenAI(api_key=api_key, base_url=api_base)

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
                return result
            except Exception as e:
                logger.error(f"[AI盯盘] LLM 调用失败: {e}")
                raise

        return llm_call


# ======================================================================
# 模块级纯函数
# ======================================================================


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

    # 策略 2：尝试 ``` ... ``` 包裹
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

    # 策略 4：尝试查找文本中的 JSON 对象
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
