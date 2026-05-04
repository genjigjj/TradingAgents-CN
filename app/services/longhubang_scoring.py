"""
龙虎榜 AI 智能评分引擎

对龙虎榜上榜股票进行综合评分排名，基于五个维度：
- 买入资金含金量 (0-30)
- 净买入额评分 (0-25)
- 卖出压力评分 (0-20)
- 机构共振评分 (0-15)
- 其他加分项 (0-10)

纯函数设计，不依赖数据库或外部服务，方便单元测试。
从 aiagents-stock/longhubang_scoring.py 迁移并重构。
"""

from typing import List, Dict, Any
from collections import Counter


class ScoringEngine:
    """龙虎榜 AI 智能评分引擎"""

    def __init__(
        self,
        top_youzi: List[str],
        famous_youzi: List[str],
        institution_keywords: List[str],
    ):
        """
        初始化评分引擎。

        Args:
            top_youzi: 顶级游资名单（如赵老哥、章盟主等）
            famous_youzi: 知名游资名单（如深股通、中信证券等）
            institution_keywords: 机构关键词列表（如机构专用、基金等）
        """
        self.top_youzi = list(top_youzi)
        self.famous_youzi = list(famous_youzi)
        self.institution_keywords = list(institution_keywords)

    def score_all_stocks(self, data_list: List[dict]) -> List[dict]:
        """
        对所有上榜股票进行评分排名。

        按股票代码分组，计算每只股票的综合评分，
        返回按综合评分降序排列的结果列表。

        Args:
            data_list: 龙虎榜数据列表，每条记录包含股票代码、名称、
                       游资名称、营业部、买入金额、卖出金额、净流入金额等字段

        Returns:
            按综合评分降序排列的评分结果列表
        """
        if not data_list:
            return []

        # 按股票代码分组
        stocks_dict: Dict[str, Dict[str, Any]] = {}
        for record in data_list:
            code = record.get("股票代码") or record.get("gpdm") or ""
            name = record.get("股票名称") or record.get("gpmc") or ""

            if not code:
                continue

            if code not in stocks_dict:
                stocks_dict[code] = {
                    "code": code,
                    "name": name,
                    "records": [],
                }

            stocks_dict[code]["records"].append(record)

        # 计算每只股票的评分
        results = []
        for code, stock_info in stocks_dict.items():
            score_result = self.calculate_stock_score(stock_info["records"])
            score_result["stock_code"] = code
            score_result["stock_name"] = stock_info["name"]
            results.append(score_result)

        # 按综合评分降序排列
        results.sort(key=lambda x: x["total_score"], reverse=True)

        # 填充排名
        for i, result in enumerate(results):
            result["rank"] = i + 1

        return results

    def calculate_stock_score(self, stock_records: List[dict]) -> dict:
        """
        计算单只股票的综合评分。

        Args:
            stock_records: 该股票的所有龙虎榜记录

        Returns:
            包含各维度评分和汇总数据的字典：
            {
                total_score, capital_quality, net_inflow_score,
                sell_pressure, institution_score, bonus,
                top_youzi_count, buy_seats, has_institution, net_inflow
            }
        """
        if not stock_records:
            return self._empty_score_result()

        # 计算五个维度评分
        capital_quality = self._calculate_capital_quality(stock_records)
        net_inflow_score = self._calculate_net_inflow_score(stock_records)
        sell_pressure = self._calculate_sell_pressure_score(stock_records)
        institution_score = self._calculate_institution_score(stock_records)
        bonus = self._calculate_bonus_score(stock_records)

        total_score = round(
            capital_quality + net_inflow_score + sell_pressure + institution_score + bonus,
            1,
        )

        # 汇总统计数据
        total_net = 0.0
        buy_seats = 0
        for r in stock_records:
            total_net += _safe_float(r.get("净流入金额") or r.get("jlrje"))
            if _safe_float(r.get("买入金额") or r.get("mrje")) > 0:
                buy_seats += 1

        top_youzi_count = self._count_top_youzi(stock_records)
        has_institution = self._has_institution_buyer(stock_records)

        return {
            "total_score": total_score,
            "capital_quality": round(capital_quality, 1),
            "net_inflow_score": round(net_inflow_score, 1),
            "sell_pressure": round(sell_pressure, 1),
            "institution_score": round(institution_score, 1),
            "bonus": round(bonus, 1),
            "top_youzi_count": top_youzi_count,
            "buy_seats": buy_seats,
            "has_institution": has_institution,
            "net_inflow": round(total_net, 2),
        }

    def _calculate_capital_quality(self, records: List[dict]) -> float:
        """
        买入资金含金量评分 (0-30)。

        评分规则：
        - 顶级游资：每位 +10 分
        - 知名游资：每位 +5 分
        - 普通游资：每位 +1.5 分
        - 上限 30 分
        """
        max_score = 30.0
        score = 0.0

        # 提取所有买方
        buyers = []
        for record in records:
            buy_amount = _safe_float(record.get("买入金额") or record.get("mrje"))
            if buy_amount > 0:
                buyers.append({
                    "name": str(record.get("游资名称") or record.get("yzmc") or ""),
                    "yingye_bu": str(record.get("营业部") or record.get("yyb") or ""),
                })

        if not buyers:
            return 0.0

        # 顶级游资：每个 +10 分
        top_youzi_count = 0
        for buyer in buyers:
            if self._is_top_youzi(buyer["name"], buyer["yingye_bu"]):
                top_youzi_count += 1
                score += 10.0

        # 知名游资：每个 +5 分（排除已计为顶级的）
        famous_youzi_count = 0
        for buyer in buyers:
            if self._is_top_youzi(buyer["name"], buyer["yingye_bu"]):
                continue
            if self._is_famous_youzi(buyer["name"], buyer["yingye_bu"]):
                famous_youzi_count += 1
                score += 5.0

        # 普通游资：每个 +1.5 分
        ordinary_count = len(buyers) - top_youzi_count - famous_youzi_count
        score += ordinary_count * 1.5

        return min(score, max_score)

    def _calculate_net_inflow_score(self, records: List[dict]) -> float:
        """
        净买入额评分 (0-25)。

        评分规则（按净流入金额分段）：
        - 1000 万以下：0-10 分
        - 1000-5000 万：10-18 分
        - 5000 万-1 亿：18-22 分
        - 1 亿以上：22-25 分
        """
        max_score = 25.0

        # 计算总净流入
        total_net_inflow = 0.0
        for record in records:
            total_net_inflow += _safe_float(record.get("净流入金额") or record.get("jlrje"))

        if total_net_inflow <= 0:
            return 0.0

        # 转换为万元
        net_inflow_wan = total_net_inflow / 10000

        # 分段评分
        if net_inflow_wan < 1000:
            score = (net_inflow_wan / 1000) * 10
        elif net_inflow_wan < 5000:
            score = 10 + ((net_inflow_wan - 1000) / 4000) * 8
        elif net_inflow_wan < 10000:
            score = 18 + ((net_inflow_wan - 5000) / 5000) * 4
        else:
            score = 22 + min((net_inflow_wan - 10000) / 10000, 1) * 3

        return min(score, max_score)

    def _calculate_sell_pressure_score(self, records: List[dict]) -> float:
        """
        卖出压力评分 (0-20)。

        卖出压力越小分数越高：
        - 卖出比例 0-10%：20 分
        - 卖出比例 10-30%：15-20 分
        - 卖出比例 30-50%：10-15 分
        - 卖出比例 50-80%：5-10 分
        - 卖出比例 80% 以上：0-5 分
        """
        max_score = 20.0

        total_buy = 0.0
        total_sell = 0.0

        for record in records:
            total_buy += _safe_float(record.get("买入金额") or record.get("mrje"))
            total_sell += _safe_float(record.get("卖出金额") or record.get("mcje"))

        if total_buy == 0:
            return 0.0

        # 计算卖出比例
        sell_ratio = total_sell / total_buy

        # 分段评分
        if sell_ratio < 0.1:
            score = 20.0
        elif sell_ratio < 0.3:
            score = 20.0 - (sell_ratio - 0.1) / 0.2 * 5
        elif sell_ratio < 0.5:
            score = 15.0 - (sell_ratio - 0.3) / 0.2 * 5
        elif sell_ratio < 0.8:
            score = 10.0 - (sell_ratio - 0.5) / 0.3 * 5
        else:
            score = 5.0 - min(sell_ratio - 0.8, 0.2) / 0.2 * 5

        return max(0.0, min(score, max_score))

    def _calculate_institution_score(self, records: List[dict]) -> float:
        """
        机构共振评分 (0-15)。

        评分规则：
        - 机构 + 游资共振：15 分
        - 仅机构买入：8-12 分
        - 仅游资买入：5-10 分
        """
        max_score = 15.0

        has_institution = False
        has_youzi = False
        institution_count = 0
        youzi_count = 0

        for record in records:
            buy_amount = _safe_float(record.get("买入金额") or record.get("mrje"))
            if buy_amount <= 0:
                continue

            youzi_name = str(record.get("游资名称") or record.get("yzmc") or "")
            yingye_bu = str(record.get("营业部") or record.get("yyb") or "")

            if self._is_institution(youzi_name, yingye_bu):
                has_institution = True
                institution_count += 1
            else:
                has_youzi = True
                youzi_count += 1

        # 评分逻辑
        if has_institution and has_youzi:
            score = 15.0
        elif has_institution:
            score = min(8 + institution_count * 2, 12)
        elif has_youzi:
            score = min(5 + youzi_count * 1, 10)
        else:
            score = 0.0

        return min(float(score), max_score)

    def _calculate_bonus_score(self, records: List[dict]) -> float:
        """
        其他加分项 (0-10)。

        包含四个子项：
        1. 主力集中度 (0-3)：席位越少越集中
        2. 热门概念 (0-3)：匹配热门关键词
        3. 连续上榜 (0-2)：多条记录加分
        4. 买卖比例优秀 (0-2)：买入远大于卖出
        """
        max_score = 10.0
        score = 0.0

        if not records:
            return 0.0

        # 1. 主力集中度加分 (0-3)
        seat_count = len(records)
        if seat_count == 1:
            score += 3.0
        elif seat_count == 2:
            score += 2.5
        elif seat_count == 3:
            score += 2.0
        elif seat_count <= 5:
            score += 1.5
        else:
            score += 1.0

        # 2. 热门概念加分 (0-3)
        hot_keywords = [
            "人工智能", "AI", "ChatGPT", "算力", "新能源", "芯片", "半导体",
            "军工", "医药", "消费", "5G", "新材料", "量子", "光伏",
            "储能", "锂电池", "汽车", "游戏", "传媒", "元宇宙",
        ]

        all_concepts: List[str] = []
        for record in records:
            concepts = record.get("概念") or record.get("gl") or ""
            if concepts:
                all_concepts.extend([c.strip() for c in str(concepts).split(",") if c.strip()])

        concept_score = 0.0
        for concept in all_concepts:
            if any(keyword in concept for keyword in hot_keywords):
                concept_score += 0.3

        score += min(concept_score, 3.0)

        # 3. 连续上榜加分 (0-2)
        if len(records) >= 3:
            score += 2.0
        elif len(records) == 2:
            score += 1.0

        # 4. 买卖比例优秀加分 (0-2)
        total_buy = 0.0
        total_sell = 0.0
        for r in records:
            total_buy += _safe_float(r.get("买入金额") or r.get("mrje"))
            total_sell += _safe_float(r.get("卖出金额") or r.get("mcje"))

        if total_buy > 0:
            buy_sell_ratio = total_buy / (total_sell + 1)
            if buy_sell_ratio >= 10:
                score += 2.0
            elif buy_sell_ratio >= 5:
                score += 1.5
            elif buy_sell_ratio >= 3:
                score += 1.0

        return min(score, max_score)

    # ========== 辅助方法 ==========

    def _is_top_youzi(self, name: str, yingye_bu: str) -> bool:
        """判断是否为顶级游资"""
        return any(top in name or top in yingye_bu for top in self.top_youzi)

    def _is_famous_youzi(self, name: str, yingye_bu: str) -> bool:
        """判断是否为知名游资"""
        return any(famous in name or famous in yingye_bu for famous in self.famous_youzi)

    def _is_institution(self, name: str, yingye_bu: str) -> bool:
        """判断是否为机构"""
        return any(kw in name or kw in yingye_bu for kw in self.institution_keywords)

    def _count_top_youzi(self, records: List[dict]) -> int:
        """统计买方中的顶级游资数量"""
        count = 0
        for record in records:
            buy_amount = _safe_float(record.get("买入金额") or record.get("mrje"))
            if buy_amount <= 0:
                continue

            youzi_name = str(record.get("游资名称") or record.get("yzmc") or "")
            yingye_bu = str(record.get("营业部") or record.get("yyb") or "")

            if self._is_top_youzi(youzi_name, yingye_bu):
                count += 1

        return count

    def _has_institution_buyer(self, records: List[dict]) -> bool:
        """检查是否有机构买入"""
        for record in records:
            buy_amount = _safe_float(record.get("买入金额") or record.get("mrje"))
            if buy_amount <= 0:
                continue

            youzi_name = str(record.get("游资名称") or record.get("yzmc") or "")
            yingye_bu = str(record.get("营业部") or record.get("yyb") or "")

            if self._is_institution(youzi_name, yingye_bu):
                return True

        return False

    @staticmethod
    def _empty_score_result() -> dict:
        """返回空评分结果"""
        return {
            "total_score": 0.0,
            "capital_quality": 0.0,
            "net_inflow_score": 0.0,
            "sell_pressure": 0.0,
            "institution_score": 0.0,
            "bonus": 0.0,
            "top_youzi_count": 0,
            "buy_seats": 0,
            "has_institution": False,
            "net_inflow": 0.0,
        }


def _safe_float(value: Any) -> float:
    """安全地将值转换为 float，无法转换时返回 0.0"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
