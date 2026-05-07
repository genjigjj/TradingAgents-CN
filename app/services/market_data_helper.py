"""
公共行情数据获取与技术指标计算模块

提供统一的行情数据获取接口，支持 A 股、港股、美股三个市场。
- A 股：复用 paper.py 的 _get_last_price 逻辑，从 market_quotes / stock_basic_info 获取
- 港股/美股：通过 ForeignStockService 获取
- 技术指标：通过 akshare 获取历史 K 线，使用 pandas 计算 MA/MACD/RSI/KDJ/布林带
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import pandas as pd

from app.core.database import get_mongo_db

logger = logging.getLogger(__name__)


async def get_market_data(code: str, market: str = "CN") -> Dict[str, Any]:
    """
    获取股票实时行情数据（支持多市场）

    Args:
        code: 股票代码（A 股 6 位数字，港股 5 位数字，美股字母代码）
        market: 市场类型 (CN/HK/US)

    Returns:
        行情数据字典，包含:
        {price, change_pct, volume, high, low, open, prev_close}

    Raises:
        ValueError: 行情获取失败时抛出异常
    """
    market = market.upper()

    if market == "CN":
        return await _get_cn_market_data(code)
    elif market in ("HK", "US"):
        return await _get_foreign_market_data(code, market)
    else:
        raise ValueError(f"不支持的市场类型: {market}")


async def get_technical_indicators(code: str, market: str = "CN") -> Dict[str, Any]:
    """
    获取技术指标数据

    通过 akshare（A 股）或 ForeignStockService（港股/美股）获取历史 K 线，
    然后计算 MA5/MA20/MA60、MACD、RSI、KDJ、布林带。

    Args:
        code: 股票代码
        market: 市场类型 (CN/HK/US)

    Returns:
        技术指标字典，包含:
        {ma5, ma20, ma60, macd: {dif, dea, macd}, rsi, kdj: {k, d, j},
         boll: {upper, middle, lower}}

    Raises:
        ValueError: 历史数据获取失败或数据不足时抛出异常
    """
    market = market.upper()

    if market == "CN":
        df = await _get_cn_kline(code)
    elif market in ("HK", "US"):
        df = await _get_foreign_kline(code, market)
    else:
        raise ValueError(f"不支持的市场类型: {market}")

    if df is None or df.empty or len(df) < 60:
        raise ValueError(
            f"股票 {code} ({market}) 历史数据不足，"
            f"需要至少 60 条记录，当前 {len(df) if df is not None else 0} 条"
        )

    return calculate_indicators(df)


def calculate_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    根据历史 K 线数据计算技术指标（纯函数，方便测试）

    Args:
        df: 历史 K 线 DataFrame，必须包含 'close', 'high', 'low' 列

    Returns:
        技术指标字典
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 均线
    ma5 = close.rolling(window=5).mean()
    ma20 = close.rolling(window=20).mean()
    ma60 = close.rolling(window=60).mean()

    # MACD
    dif, dea, macd_hist = _calculate_macd(close)

    # RSI（默认使用 RSI14）
    rsi = _calculate_rsi(close, period=14)

    # KDJ
    k, d, j = _calculate_kdj(close, high, low)

    # 布林带
    boll_upper, boll_middle, boll_lower = _calculate_bollinger(close)

    # 取最后一行的值
    latest_idx = -1

    return {
        "ma5": _safe_round(ma5.iloc[latest_idx]),
        "ma20": _safe_round(ma20.iloc[latest_idx]),
        "ma60": _safe_round(ma60.iloc[latest_idx]),
        "macd": {
            "dif": _safe_round(dif.iloc[latest_idx]),
            "dea": _safe_round(dea.iloc[latest_idx]),
            "macd": _safe_round(macd_hist.iloc[latest_idx]),
        },
        "rsi": _safe_round(rsi.iloc[latest_idx]),
        "kdj": {
            "k": _safe_round(k.iloc[latest_idx]),
            "d": _safe_round(d.iloc[latest_idx]),
            "j": _safe_round(j.iloc[latest_idx]),
        },
        "boll": {
            "upper": _safe_round(boll_upper.iloc[latest_idx]),
            "middle": _safe_round(boll_middle.iloc[latest_idx]),
            "lower": _safe_round(boll_lower.iloc[latest_idx]),
        },
    }


# ========== A 股行情获取 ==========


async def _get_cn_market_data(code: str) -> Dict[str, Any]:
    """
    获取 A 股实时行情（复用 paper.py 的 _get_last_price 逻辑）

    优先从 market_quotes 获取完整行情数据，
    回退到 stock_basic_info 获取基础价格。
    """
    db = get_mongo_db()
    code = str(code).strip().zfill(6)

    # 1. 尝试从 market_quotes 获取完整行情
    q = await db["market_quotes"].find_one(
        {"$or": [{"code": code}, {"symbol": code}]},
    )
    if q:
        price = _safe_float(q.get("close"))
        if price and price > 0:
            logger.debug(f"✅ 从 market_quotes 获取 A 股行情: {code} = {price}")
            return {
                "price": price,
                "change_pct": _safe_float(q.get("pct_chg"), 0.0),
                "volume": _safe_float(q.get("volume"), 0.0),
                "high": _safe_float(q.get("high"), 0.0),
                "low": _safe_float(q.get("low"), 0.0),
                "open": _safe_float(q.get("open"), 0.0),
                "prev_close": _safe_float(q.get("pre_close"), 0.0),
            }

    # 2. 回退到 stock_basic_info
    basic_info = await db["stock_basic_info"].find_one(
        {"$or": [{"code": code}, {"symbol": code}]},
    )
    if basic_info:
        price = _safe_float(basic_info.get("current_price"))
        if price and price > 0:
            logger.debug(f"✅ 从 stock_basic_info 获取 A 股价格: {code} = {price}")
            return {
                "price": price,
                "change_pct": 0.0,
                "volume": 0.0,
                "high": price,
                "low": price,
                "open": price,
                "prev_close": price,
            }

    raise ValueError(f"无法获取 A 股 {code} 的行情数据")


async def _get_foreign_market_data(code: str, market: str) -> Dict[str, Any]:
    """
    获取港股/美股实时行情（通过 ForeignStockService）
    """
    from app.services.foreign_stock_service import ForeignStockService

    db = get_mongo_db()
    service = ForeignStockService(db=db)

    try:
        quote = await service.get_quote(market, code, force_refresh=False)
    except Exception as e:
        raise ValueError(f"获取 {market} 股票 {code} 行情失败: {e}")

    if not quote:
        raise ValueError(f"获取 {market} 股票 {code} 行情失败: 返回空数据")

    # 从 quote 中提取价格（兼容多种字段名）
    price = _safe_float(
        quote.get("price") or quote.get("current_price") or quote.get("close")
    )
    if not price or price <= 0:
        raise ValueError(f"获取 {market} 股票 {code} 行情失败: 无有效价格")

    return {
        "price": price,
        "change_pct": _safe_float(
            quote.get("change_percent") or quote.get("change_pct") or quote.get("pct_chg"),
            0.0,
        ),
        "volume": _safe_float(quote.get("volume"), 0.0),
        "high": _safe_float(quote.get("high"), price),
        "low": _safe_float(quote.get("low"), price),
        "open": _safe_float(quote.get("open"), price),
        "prev_close": _safe_float(
            quote.get("previous_close") or quote.get("pre_close"), price
        ),
    }


# ========== 历史 K 线获取 ==========


async def _get_cn_kline(code: str, days: int = 300) -> Optional[pd.DataFrame]:
    """
    获取 A 股历史 K 线数据（通过 akshare）

    Returns:
        标准化的 DataFrame，包含 close, high, low, open, volume 列
    """
    code = str(code).strip().zfill(6)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    try:
        import akshare as ak

        df = await asyncio.to_thread(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    except Exception as e:
        raise ValueError(f"获取 A 股 {code} 历史 K 线失败: {e}")

    if df is None or df.empty:
        raise ValueError(f"获取 A 股 {code} 历史 K 线失败: 返回空数据")

    # 标准化列名（akshare 返回中文列名）
    return _normalize_kline_columns(df)


async def _get_foreign_kline(
    code: str, market: str, days: int = 300
) -> Optional[pd.DataFrame]:
    """
    获取港股/美股历史 K 线数据（通过 ForeignStockService）

    Returns:
        标准化的 DataFrame，包含 close, high, low, open, volume 列
    """
    from app.services.foreign_stock_service import ForeignStockService

    db = get_mongo_db()
    service = ForeignStockService(db=db)

    try:
        kline_list = await service.get_kline(
            market, code, period="day", limit=days, force_refresh=False
        )
    except Exception as e:
        raise ValueError(f"获取 {market} 股票 {code} 历史 K 线失败: {e}")

    if not kline_list:
        raise ValueError(f"获取 {market} 股票 {code} 历史 K 线失败: 返回空数据")

    # 转换为 DataFrame
    df = pd.DataFrame(kline_list)
    return _normalize_kline_columns(df)


def _normalize_kline_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    标准化 K 线 DataFrame 的列名

    将中文列名或各种英文列名统一为: close, high, low, open, volume
    """
    # 列名映射（中文 → 英文）
    column_map = {
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "开盘": "open",
        "成交量": "volume",
        "成交额": "amount",
        "日期": "date",
        # 英文别名
        "Close": "close",
        "High": "high",
        "Low": "low",
        "Open": "open",
        "Volume": "volume",
        "Amount": "amount",
        "Date": "date",
        "time": "date",
    }

    df = df.rename(columns=column_map)

    # 确保必需列存在
    required = ["close", "high", "low"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"K 线数据缺少必需列: {missing}，当前列: {list(df.columns)}")

    # 确保数值类型
    for col in ["close", "high", "low", "open", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 按日期排序（升序）
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    return df


# ========== 技术指标计算（纯函数） ==========


def _calculate_macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple:
    """
    计算 MACD 指标

    Returns:
        (DIF, DEA, MACD柱) 三个 Series
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = (dif - dea) * 2

    return dif, dea, macd_hist


def _calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 RSI 指标
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _calculate_kdj(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple:
    """
    计算 KDJ 指标

    Returns:
        (K, D, J) 三个 Series
    """
    low_n = low.rolling(window=n).min()
    high_n = high.rolling(window=n).max()

    rsv = (close - low_n) / (high_n - low_n) * 100

    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    return k, d, j


def _calculate_bollinger(
    close: pd.Series, period: int = 20, std_num: int = 2
) -> tuple:
    """
    计算布林带

    Returns:
        (upper, middle, lower) 三个 Series
    """
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper = middle + std_num * std
    lower = middle - std_num * std

    return upper, middle, lower


# ========== 工具函数 ==========


def _safe_float(value, default=None) -> Optional[float]:
    """安全转换为浮点数"""
    if value is None or value == "" or value == "None":
        return default
    try:
        result = float(value)
        return result
    except (ValueError, TypeError):
        return default


def _safe_round(value, ndigits: int = 4) -> Optional[float]:
    """安全四舍五入"""
    if value is None or pd.isna(value):
        return None
    try:
        return round(float(value), ndigits)
    except (ValueError, TypeError):
        return None
