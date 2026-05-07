"""
实时监测服务

监控股票行情关键价位（进场区间、止盈位、止损位），触发预警通知。
直接以 stock_monitor_configs 为配置源，通过 market_data_helper 获取行情数据。

从 aiagents-stock/ 的实时监测模块迁移并重构为异步服务。
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)

# 有效的通知类型
VALID_NOTIFICATION_TYPES = ["entry", "take_profit", "stop_loss"]

# 通知去重窗口（分钟）
DEDUP_WINDOW_MINUTES = 60


class StockMonitorService:
    """实时监测服务 — 监控股票行情关键价位，触发预警通知"""

    def __init__(self):
        # 去重缓存: key = f"{symbol}:{type}", value = last_sent_time
        self._notification_cache: Dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # 监测配置 CRUD
    # ------------------------------------------------------------------

    async def create_or_update_config(
        self, user_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        创建或更新监测配置（upsert on user_id + symbol）。

        用于手动创建和分析结果同步。

        Args:
            user_id: 用户 ID
            config: 监测配置字典，必须包含 symbol

        Returns:
            {"success": True, "config": {...}} 或
            {"success": False, "error": "错误信息"}
        """
        db = get_mongo_db()

        symbol = config.get("symbol", "").strip()
        if not symbol:
            return {"success": False, "error": "缺少 symbol 字段"}

        now = datetime.now()

        # 构建 upsert 文档
        entry_range = config.get("entry_range", {})
        update_fields = {
            "user_id": user_id,
            "symbol": symbol,
            "name": config.get("name", ""),
            "market": config.get("market", "CN"),
            "rating": config.get("rating", ""),
            "entry_range": {
                "min": float(entry_range.get("min", 0)) if entry_range else 0,
                "max": float(entry_range.get("max", 0)) if entry_range else 0,
            },
            "take_profit": float(config.get("take_profit", 0)),
            "stop_loss": float(config.get("stop_loss", 0)),
            "current_price": float(config.get("current_price", 0)),
            "last_checked": config.get("last_checked"),
            "check_interval": int(config.get("check_interval", 60)),
            "notification_enabled": config.get("notification_enabled", True),
            "trading_hours_only": config.get("trading_hours_only", True),
            "enabled": config.get("enabled", True),
            "source": config.get("source", "manual"),
            "updated_at": now,
        }

        try:
            result = await db["stock_monitor_configs"].update_one(
                {"user_id": user_id, "symbol": symbol},
                {
                    "$set": update_fields,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

            # 获取更新后的文档
            doc = await db["stock_monitor_configs"].find_one(
                {"user_id": user_id, "symbol": symbol}
            )
            if doc and "_id" in doc:
                doc["_id"] = str(doc["_id"])

            action = "创建" if result.upserted_id else "更新"
            logger.info(
                f"[实时监测] ✅ {action}监测配置: user={user_id}, symbol={symbol}"
            )
            return {"success": True, "config": doc}

        except Exception as e:
            logger.error(f"[实时监测] 创建/更新监测配置失败: {e}")
            return {"success": False, "error": f"操作失败: {e}"}

    async def list_configs(self, user_id: str) -> List[Dict[str, Any]]:
        """
        列出用户的所有监测配置。

        Args:
            user_id: 用户 ID

        Returns:
            监测配置列表
        """
        db = get_mongo_db()
        cursor = db["stock_monitor_configs"].find({"user_id": user_id})
        configs = await cursor.to_list(None)

        for config in configs:
            if "_id" in config:
                config["_id"] = str(config["_id"])

        logger.info(
            f"[实时监测] 查询监测配置: user={user_id}, 返回 {len(configs)} 条"
        )
        return configs

    async def update_config(
        self, user_id: str, config_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        更新监测配置。

        Args:
            user_id: 用户 ID
            config_id: 配置 ID（MongoDB _id 字符串）
            updates: 更新字段字典

        Returns:
            {"success": True, "config": {...}} 或
            {"success": False, "error": "错误信息"}
        """
        from bson import ObjectId

        db = get_mongo_db()

        # 允许更新的字段白名单
        allowed_fields = {
            "name", "rating", "entry_range", "take_profit", "stop_loss",
            "check_interval", "notification_enabled", "trading_hours_only",
            "enabled",
        }
        filtered_updates = {
            k: v for k, v in updates.items() if k in allowed_fields
        }

        if not filtered_updates:
            return {"success": False, "error": "没有有效的更新字段"}

        filtered_updates["updated_at"] = datetime.now()

        try:
            result = await db["stock_monitor_configs"].update_one(
                {"_id": ObjectId(config_id), "user_id": user_id},
                {"$set": filtered_updates},
            )
            if result.matched_count == 0:
                return {"success": False, "error": "配置不存在或无权限"}

            # 返回更新后的文档
            updated_doc = await db["stock_monitor_configs"].find_one(
                {"_id": ObjectId(config_id)}
            )
            if updated_doc and "_id" in updated_doc:
                updated_doc["_id"] = str(updated_doc["_id"])

            logger.info(
                f"[实时监测] ✅ 更新监测配置: config_id={config_id}, "
                f"updates={list(filtered_updates.keys())}"
            )
            return {"success": True, "config": updated_doc}

        except Exception as e:
            logger.error(f"[实时监测] 更新监测配置失败: {e}")
            return {"success": False, "error": f"更新失败: {e}"}

    async def delete_config(self, user_id: str, config_id: str) -> bool:
        """
        删除监测配置。

        Args:
            user_id: 用户 ID
            config_id: 配置 ID（MongoDB _id 字符串）

        Returns:
            是否删除成功
        """
        from bson import ObjectId

        db = get_mongo_db()

        try:
            result = await db["stock_monitor_configs"].delete_one(
                {"_id": ObjectId(config_id), "user_id": user_id}
            )
            if result.deleted_count > 0:
                logger.info(
                    f"[实时监测] ✅ 删除监测配置: config_id={config_id}"
                )
                return True
            else:
                logger.warning(
                    f"[实时监测] 删除监测配置失败: config_id={config_id} 不存在或无权限"
                )
                return False
        except Exception as e:
            logger.error(f"[实时监测] 删除监测配置异常: {e}")
            return False

    # ------------------------------------------------------------------
    # 价格触发判断（纯函数）
    # ------------------------------------------------------------------

    @staticmethod
    def check_price_triggers(
        current_price: float, config: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """
        检查价格是否触发预警条件。

        纯函数（静态方法），方便属性测试。

        Args:
            current_price: 当前价格
            config: 监测配置字典，需包含 entry_range、take_profit、stop_loss、
                    symbol、name

        Returns:
            触发的通知列表，每个通知包含 type 和 message:
            - type="entry": entry_min <= price <= entry_max
            - type="take_profit": price >= take_profit
            - type="stop_loss": price <= stop_loss
        """
        triggers = []

        symbol = config.get("symbol", "")
        name = config.get("name", "")

        entry_range = config.get("entry_range", {})
        entry_min = float(entry_range.get("min", 0)) if entry_range else 0
        entry_max = float(entry_range.get("max", 0)) if entry_range else 0
        take_profit = float(config.get("take_profit", 0))
        stop_loss = float(config.get("stop_loss", 0))

        # 进场区间触发: entry_min <= price <= entry_max
        if entry_min > 0 and entry_max > 0 and entry_min <= current_price <= entry_max:
            triggers.append({
                "type": "entry",
                "message": (
                    f"{name}({symbol}) 当前价格 {current_price:.2f} "
                    f"进入进场区间 [{entry_min:.2f}, {entry_max:.2f}]"
                ),
            })

        # 止盈触发: price >= take_profit
        if take_profit > 0 and current_price >= take_profit:
            triggers.append({
                "type": "take_profit",
                "message": (
                    f"{name}({symbol}) 当前价格 {current_price:.2f} "
                    f"已达止盈位 {take_profit:.2f}"
                ),
            })

        # 止损触发: price <= stop_loss
        if stop_loss > 0 and current_price <= stop_loss:
            triggers.append({
                "type": "stop_loss",
                "message": (
                    f"{name}({symbol}) 当前价格 {current_price:.2f} "
                    f"已触止损位 {stop_loss:.2f}"
                ),
            })

        return triggers

    # ------------------------------------------------------------------
    # 通知去重机制
    # ------------------------------------------------------------------

    def should_send_notification(
        self, symbol: str, notification_type: str, now: datetime = None
    ) -> bool:
        """
        通知去重判断。

        给定 now 参数时为纯函数，方便属性测试。
        60 分钟内同一股票同一类型仅发送一次。

        Args:
            symbol: 股票代码
            notification_type: 通知类型（entry/take_profit/stop_loss）
            now: 当前时间，默认使用 datetime.now()

        Returns:
            是否应发送通知
        """
        if now is None:
            now = datetime.now()

        cache_key = f"{symbol}:{notification_type}"
        last_sent = self._notification_cache.get(cache_key)

        if last_sent is not None:
            elapsed = now - last_sent
            if elapsed < timedelta(minutes=DEDUP_WINDOW_MINUTES):
                return False

        # 更新缓存
        self._notification_cache[cache_key] = now
        return True

    # ------------------------------------------------------------------
    # 价格轮询与通知推送
    # ------------------------------------------------------------------

    async def check_all_prices(self) -> None:
        """
        轮询所有启用的监测配置，获取最新价格并检查触发条件。

        由 APScheduler 周期性调用。
        遍历所有启用的监测配置，获取最新价格并检查触发条件。
        当 trading_hours_only=true 时仅在交易时段内执行。
        触发通知时写入 stock_monitor_notifications 并通过 WebSocket 推送。
        """
        from app.services import market_data_helper
        from app.services.smart_monitor_service import SmartMonitorService

        db = get_mongo_db()
        now = datetime.now()

        try:
            configs = await db["stock_monitor_configs"].find(
                {"enabled": True, "notification_enabled": True}
            ).to_list(None)
        except Exception as e:
            logger.error(f"[实时监测] 查询监测配置失败: {e}")
            return

        logger.info(f"[实时监测] 价格轮询: 共 {len(configs)} 个启用的配置")

        for config in configs:
            try:
                symbol = config.get("symbol", "")
                user_id = config.get("user_id", "")
                market = config.get("market", "CN")
                trading_hours_only = config.get("trading_hours_only", True)

                # 检查交易时段
                if trading_hours_only and not SmartMonitorService.is_trading_session(now):
                    logger.debug(
                        f"[实时监测] 跳过 {symbol}: 非交易时段"
                    )
                    continue

                # 获取最新价格
                try:
                    market_data = await market_data_helper.get_market_data(
                        symbol, market
                    )
                    current_price = market_data.get("price", 0)
                except Exception as e:
                    logger.warning(
                        f"[实时监测] 行情获取失败: {symbol}: {e}"
                    )
                    continue

                if current_price <= 0:
                    continue

                # 更新当前价格和检查时间
                try:
                    config_id = config.get("_id")
                    await db["stock_monitor_configs"].update_one(
                        {"_id": config_id},
                        {
                            "$set": {
                                "current_price": current_price,
                                "last_checked": now,
                            }
                        },
                    )
                except Exception as e:
                    logger.warning(
                        f"[实时监测] 更新价格失败: {symbol}: {e}"
                    )

                # 检查触发条件
                triggers = self.check_price_triggers(current_price, config)

                for trigger in triggers:
                    notification_type = trigger["type"]

                    # 去重判断
                    if not self.should_send_notification(
                        symbol, notification_type, now
                    ):
                        logger.debug(
                            f"[实时监测] 去重跳过: {symbol} {notification_type}"
                        )
                        continue

                    # 写入通知记录
                    notification_doc = {
                        "user_id": user_id,
                        "config_id": str(config.get("_id", "")),
                        "symbol": symbol,
                        "name": config.get("name", ""),
                        "type": notification_type,
                        "message": trigger["message"],
                        "current_price": current_price,
                        "trigger_value": current_price,
                        "triggered_at": now,
                        "sent": False,
                        "read": False,
                        "source": "stock_monitor",
                        "created_at": now,
                    }

                    try:
                        await db["stock_monitor_notifications"].insert_one(
                            notification_doc
                        )
                        logger.info(
                            f"[实时监测] ✅ 通知已记录: {symbol} {notification_type}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[实时监测] 通知写入 DB 失败: {e}"
                        )

                    # 通过 WebSocket 推送
                    await self._send_price_alert(user_id, notification_doc)

            except Exception as e:
                logger.error(
                    f"[实时监测] 配置处理异常: {config.get('symbol', '?')}: {e}"
                )
                # 单个配置失败不影响其他配置
                continue

    # ------------------------------------------------------------------
    # 通知历史查询
    # ------------------------------------------------------------------

    async def get_notifications(
        self,
        user_id: str,
        symbol: str = None,
        notification_type: str = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询通知历史。

        Args:
            user_id: 用户 ID
            symbol: 股票代码（可选）
            notification_type: 通知类型（可选，entry/take_profit/stop_loss）
            limit: 返回记录数上限，默认 50

        Returns:
            通知历史列表
        """
        db = get_mongo_db()

        query: Dict[str, Any] = {"user_id": user_id}

        if symbol:
            query["symbol"] = symbol

        if notification_type:
            query["type"] = notification_type

        cursor = (
            db["stock_monitor_notifications"]
            .find(query)
            .sort("triggered_at", -1)
            .limit(limit)
        )
        results = await cursor.to_list(length=limit)

        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])

        logger.info(
            f"[实时监测] 查询通知历史: user={user_id}, "
            f"symbol={symbol or 'all'}, type={notification_type or 'all'}, "
            f"返回 {len(results)} 条"
        )
        return results

    # ------------------------------------------------------------------
    # 内部方法：WebSocket 推送
    # ------------------------------------------------------------------

    async def _send_price_alert(
        self, user_id: str, notification: Dict[str, Any]
    ) -> None:
        """
        通过统一通知系统推送价格预警通知。

        同时写入 notifications 集合并通过 WebSocket 推送。

        Args:
            user_id: 用户 ID
            notification: 通知文档字典
        """
        try:
            from app.services.notifications_service import get_notifications_service
            from app.models.notification import NotificationCreate

            alert_type = notification.get("type", "")
            symbol = notification.get("symbol", "")
            name = notification.get("name", "")
            message = notification.get("message", "")

            # 根据通知类型设置标题和严重级别
            type_labels = {
                "entry": ("进场区间", "success"),
                "take_profit": ("止盈触发", "warning"),
                "stop_loss": ("止损触发", "error"),
            }
            label, severity = type_labels.get(alert_type, ("价格预警", "info"))
            title = f"{label} - {name}({symbol})"

            notif_svc = get_notifications_service()
            await notif_svc.create_and_publish(NotificationCreate(
                user_id=user_id,
                type="alert",
                title=title,
                content=message,
                source="stock_monitor",
                severity=severity,
                metadata={
                    "symbol": symbol,
                    "name": name,
                    "alert_type": alert_type,
                    "current_price": notification.get("current_price", 0),
                },
            ))

            # 更新 stock_monitor_notifications 的 sent 状态
            db = get_mongo_db()
            if notification.get("_id"):
                await db["stock_monitor_notifications"].update_one(
                    {"_id": notification["_id"]},
                    {"$set": {"sent": True}},
                )

            logger.info(
                f"[实时监测] ✅ 通知已推送: "
                f"{symbol} {alert_type}"
            )
        except Exception as e:
            # 推送失败不影响主流程，通知已写入 DB 供后续拉取
            logger.warning(f"[实时监测] 通知推送失败: {e}")
