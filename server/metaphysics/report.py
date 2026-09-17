"""渊海子平命理选股报告生成器。

输出周/月/日方向与买点建议，结合当日四柱用神与股票名称五行评分。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .bazi import BaziEngine
from .elements import STEMS, BRANCHES, STEM_ELEMENT, BRANCH_ELEMENT
from .model import YuanhaiDecisionModel
from .stock_element import StockElementAnalyzer


class YuanhaiReport:
    """命理选股报告。"""

    ELEMENT_EMOJI = {"木": "[木]", "火": "[火]", "土": "[土]", "金": "[金]", "水": "[水]"}
    ELEMENT_COLOR = {
        "木": "青", "火": "赤", "土": "黄", "金": "白", "水": "黑",
    }
    ACTION_EMOJI = {
        "买入": "[买]", "轻仓试探": "[试]", "观望": "[观]",
        "减仓": "[减]", "卖出": "[卖]",
    }

    def __init__(self, dt: datetime | None = None) -> None:
        self.dt = dt or datetime.now()
        self.model = YuanhaiDecisionModel()

    def generate(self) -> str:
        """生成完整报告。"""
        lines: list[str] = []
        lines.append(self._header())
        lines.append(self._daily_section())
        lines.append(self._weekly_section())
        lines.append(self._monthly_section())
        lines.append(self._buy_points_section())
        lines.append(self._stock_scoring_section())
        lines.append(self._disclaimer())
        return "\n".join(lines)

    def _header(self) -> str:
        pillars = BaziEngine.from_datetime(self.dt)
        sep = "=" * 60
        return (
            sep + "\n"
            + "    渊海子平 · 命理选股决策报告\n"
            + sep + "\n"
            + f"  生成时间：{self.dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
            + f"  当值四柱：{pillars}\n"
            + f"  日主：{pillars.day.stem}（{STEM_ELEMENT[pillars.day.stem]}）\n"
            + f"  月令：{pillars.month.branch}（{BRANCH_ELEMENT[pillars.month.branch]}）\n"
            + "-" * 60
        )

    def _daily_section(self) -> str:
        daily = self.model.daily_direction(self.dt)
        use = daily["recommended_elements"]
        avoid = daily["avoid_elements"]

        use_str = " ".join(
            f"{self.ELEMENT_EMOJI[e]}{e}({self.ELEMENT_COLOR[e]})" for e in use
        )
        avoid_str = " ".join(
            f"{self.ELEMENT_EMOJI[e]}{e}" for e in avoid
        )

        return (
            "\n【一、今日方向】\n"
            f"  日主旺衰：{daily['day_master_strength']}\n"
            f"  调候用神：{daily['tone_god']}\n"
            f"  推荐五行（用神）：{use_str}\n"
            f"  回避五行（忌神）：{avoid_str}\n"
            f"  五行旺衰：{daily['elements_strength']}\n"
        )

    def _weekly_section(self) -> str:
        weekly = self.model.weekly_direction(self.dt)
        lines = ["\n【二、本周方向】"]
        lines.append(f"  周用神：{' '.join(weekly['week_use_gods'])}")
        lines.append(f"  周忌神：{' '.join(weekly['week_avoid_gods'])}")
        lines.append("  每日分解：")
        for d in weekly["daily_breakdown"]:
            lines.append(
                f"    {d['date']} {d['weekday']:3s} "
                f"日柱:{d['pillar']:2s} "
                f"用神:{','.join(d['use_gods']):4s} "
                f"旺衰:{d['day_master_strength']}"
            )
        lines.append(
            f"  最佳交易日：{weekly['best_day']['date']} "
            f"({weekly['best_day']['weekday']})"
        )
        return "\n".join(lines)

    def _monthly_section(self) -> str:
        monthly = self.model.monthly_direction(self.dt)
        lines = ["\n【三、本月方向】"]
        lines.append(f"  月柱：{monthly['month_pillar']}")
        lines.append(f"  月干五行：{monthly['month_stem_element']}")
        lines.append(f"  月支五行：{monthly['month_branch_element']}")
        lines.append(
            f"  月用神：{' '.join(monthly['month_use_gods'])}"
        )
        lines.append(f"  月忌神：{' '.join(monthly['month_avoid_gods'])}")
        lines.append(f"  调候用神：{monthly['tone_god']}")
        lines.append("  推荐板块：")
        for elem, sectors in monthly["recommended_sectors"].items():
            emoji = self.ELEMENT_EMOJI.get(elem, "")
            lines.append(f"    {emoji}{elem}：{'、'.join(sectors)}")
        return "\n".join(lines)

    def _buy_points_section(self) -> str:
        lines = ["\n【四、近七日买点信号】"]
        for i in range(7):
            d = self.dt + timedelta(days=i)
            bp = self.model.buy_point_signal(d)
            emoji = self.ACTION_EMOJI.get(bp["action"], "")
            signals_str = "；".join(bp["signals"]) if bp["signals"] else "无明显信号"
            lines.append(
                f"  {d.strftime('%m-%d %a')} "
                f"{emoji}{bp['action']:6s} "
                f"(信号分:{bp['signal_score']:+3d}) "
                f"| {signals_str}"
            )
        return "\n".join(lines)

    def _stock_scoring_section(self) -> str:
        """对一批代表性股票按用神评分。"""
        daily = self.model.daily_direction(self.dt)
        use_gods = daily["recommended_elements"]

        # 代表性股票池（可扩展为全市场扫描）
        sample_stocks = [
            ("贵州茅台", "食品饮料"),
            ("宁德时代", "电力设备"),
            ("比亚迪", "汽车"),
            ("招商银行", "银行"),
            ("中国平安", "非银金融"),
            ("紫金矿业", "有色金属"),
            ("药明康德", "医药生物"),
            ("隆基绿能", "电力设备"),
            ("五粮液", "食品饮料"),
            ("中交设计", "建筑装饰"),
            ("中国中免", "商贸零售"),
            ("长江电力", "公用事业"),
        ]

        lines = ["\n【五、标的五行评分（用神匹配度）】"]
        lines.append(f"  （当前用神：{' '.join(use_gods)}）")
        lines.append("  " + "-" * 56)
        lines.append(
            f"  {'股票名称':10s} {'行业':8s} {'五行':4s} "
            f"{'匹配分':>6s} {'评级':8s} {'理由'}"
        )
        lines.append("  " + "-" * 56)

        results = []
        for name, industry in sample_stocks:
            elem = StockElementAnalyzer.combined_element(name, industry)
            analysis = self.model.analyze(BaziEngine.from_datetime(self.dt))
            score_info = self.model.stock_score(elem, analysis, stock_name=name)
            results.append((name, industry, elem, score_info))

        results.sort(key=lambda x: x[3]["score"], reverse=True)

        for name, industry, elem, info in results:
            emoji = self.ELEMENT_EMOJI.get(elem or "土", "❓")
            lines.append(
                f"  {name:10s} {industry:8s} {emoji}{(elem or '?'):3s} "
                f"{info['score']:>+6.1f} {info['level']:8s} {info['reason']}"
            )

        # 操作建议
        top = results[0]
        lines.append("  " + "-" * 56)
        lines.append(
            f"  [首选] {top[0]}（{top[3]['level']}，匹配分 {top[3]['score']:+.1f}）"
        )
        return "\n".join(lines)

    def _disclaimer(self) -> str:
        return (
            "\n" + "-" * 60 + "\n"
            "[!] 免责声明：\n"
            "  本报告基于《渊海子平》传统命理推演，仅供国学研究与娱乐参考，\n"
            "  不构成任何投资建议。股市有风险，投资需谨慎，决策以理性判断为准。\n"
            + "=" * 60
        )


def run_report(dt: datetime | None = None) -> str:
    """运行并返回报告字符串。"""
    report = YuanhaiReport(dt)
    return report.generate()
