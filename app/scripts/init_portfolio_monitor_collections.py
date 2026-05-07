#!/usr/bin/env python3
"""
持仓分析 · AI盯盘 · 实时监测 — MongoDB 集合初始化脚本

创建以下 5 个集合并建立索引：
- portfolio_analysis_history  持仓分析历史
- smart_monitor_tasks         AI 盯盘任务
- smart_monitor_decisions     AI 决策记录
- stock_monitor_configs       实时监测配置
- stock_monitor_notifications 监测通知记录

可在应用启动时通过 ensure_indexes() 调用，也可作为独立脚本运行。
"""

import asyncio
import sys
import os
import logging

# 作为独立脚本运行时，添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# 集合名称常量
PORTFOLIO_ANALYSIS_HISTORY = "portfolio_analysis_history"
SMART_MONITOR_TASKS = "smart_monitor_tasks"
SMART_MONITOR_DECISIONS = "smart_monitor_decisions"
STOCK_MONITOR_CONFIGS = "stock_monitor_configs"
STOCK_MONITOR_NOTIFICATIONS = "stock_monitor_notifications"


async def ensure_indexes() -> None:
    """
    确保持仓分析/AI盯盘/实时监测相关集合的索引存在。

    幂等操作：如果索引已存在则跳过，不会重复创建。
    可在 app 启动时（lifespan）安全调用。
    """
    from app.core.database import get_mongo_db

    db = get_mongo_db()

    try:
        # ── portfolio_analysis_history ──────────────────────────────
        # 需求 7.1: 按用户和股票查询
        collection = db[PORTFOLIO_ANALYSIS_HISTORY]
        await collection.create_index(
            [("user_id", ASCENDING), ("position_code", ASCENDING)],
            name="idx_user_position",
            background=True,
        )
        # 需求 7.1: 按分析时间倒序排序
        await collection.create_index(
            [("analysis_time", DESCENDING)],
            name="idx_analysis_time",
            background=True,
        )

        # ── smart_monitor_tasks ────────────────────────────────────
        # 需求 7.2: 用户+股票唯一索引
        collection = db[SMART_MONITOR_TASKS]
        await collection.create_index(
            [("user_id", ASCENDING), ("stock_code", ASCENDING)],
            name="idx_user_stock_unique",
            unique=True,
            background=True,
        )
        # 需求 7.2: 定时任务查询（启用状态 + 上次检查时间）
        await collection.create_index(
            [("enabled", ASCENDING), ("last_check_time", ASCENDING)],
            name="idx_enabled_last_check",
            background=True,
        )

        # ── smart_monitor_decisions ────────────────────────────────
        # 需求 7.3: 按用户、股票和决策时间查询
        collection = db[SMART_MONITOR_DECISIONS]
        await collection.create_index(
            [("user_id", ASCENDING), ("stock_code", ASCENDING), ("decision_time", DESCENDING)],
            name="idx_user_stock_decision_time",
            background=True,
        )

        # ── stock_monitor_configs ──────────────────────────────────
        # 需求 7.4: 用户+股票代码唯一索引
        collection = db[STOCK_MONITOR_CONFIGS]
        await collection.create_index(
            [("user_id", ASCENDING), ("symbol", ASCENDING)],
            name="idx_user_symbol_unique",
            unique=True,
            background=True,
        )

        # ── stock_monitor_notifications ────────────────────────────
        # 需求 7.5: 去重和查询索引
        collection = db[STOCK_MONITOR_NOTIFICATIONS]
        await collection.create_index(
            [("user_id", ASCENDING), ("symbol", ASCENDING), ("type", ASCENDING), ("triggered_at", DESCENDING)],
            name="idx_user_symbol_type_triggered",
            background=True,
        )
        # 需求 7.5: 未读通知查询索引
        await collection.create_index(
            [("user_id", ASCENDING), ("read", ASCENDING), ("created_at", DESCENDING)],
            name="idx_user_read_created",
            background=True,
        )

        logger.info("✅ 持仓分析/AI盯盘/实时监测集合索引创建完成")

    except Exception as e:
        logger.warning(f"⚠️ 持仓监测集合索引创建失败: {e}")
        raise


async def main():
    """独立运行入口：初始化数据库连接后创建索引"""
    from app.core.database import init_db

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("🚀 开始初始化持仓分析/AI盯盘/实时监测集合索引...")
    await init_db()
    await ensure_indexes()
    print("🎉 集合索引初始化完成")


if __name__ == "__main__":
    asyncio.run(main())
