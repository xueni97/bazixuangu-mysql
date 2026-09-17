"""四柱排盘引擎。

根据公历日期时间计算年柱、月柱、日柱、时柱。
- 年柱：以立春为界
- 月柱：以十二节为界（寅月起于立春）
- 日柱：以零点为界，用儒略日计算
- 时柱：以时辰为界（23点起为子时，属次日）

节气日期采用近似值（平均日期±1日），满足股票决策的精度需求。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .elements import (
    STEMS,
    BRANCHES,
    BRANCH_HIDDEN_STEMS,
    BRANCH_MAIN_QI,
    STEM_ELEMENT,
    element_of_stem,
    element_of_branch,
    ten_god,
)


# ── 节气近似日期（月-日） ─────────────────────────────────
# 十二节定义月支边界，采用常年平均日期
SOLAR_TERMS_MONTH_BOUNDARY = [
    # (month, day, branch)  -- 此日起进入该月支
    (1, 6, "丑"),    # 小寒
    (2, 4, "寅"),    # 立春
    (3, 6, "卯"),    # 惊蛰
    (4, 5, "辰"),    # 清明
    (5, 6, "巳"),    # 立夏
    (6, 6, "午"),    # 芒种
    (7, 7, "未"),    # 小暑
    (8, 8, "申"),    # 立秋
    (9, 8, "酉"),    # 白露
    (10, 8, "戌"),   # 寒露
    (11, 7, "亥"),   # 立冬
    (12, 7, "子"),   # 大雪
]


def _month_branch(month: int, day: int) -> str:
    """根据公历月日确定月支。"""
    for m, d, branch in SOLAR_TERMS_MONTH_BOUNDARY:
        if (month, day) >= (m, d):
            result = branch
        else:
            break
    else:
        result = "子"
    # 1月6日前属于上一年的丑月（小寒前仍为子月）
    if (month, day) < (1, 6):
        return "子"
    return result


# ── 五虎遁：年干推月干 ──────────────────────────────────
# 甲己之年丙作首，乙庚之年戊为头，
# 丙辛必定寻庚起，丁壬壬位顺行流，
# 戊癸之年何方发，甲寅之上好追求。
TIGER_ESCAPE = {
    "甲": "丙", "己": "丙",
    "乙": "戊", "庚": "戊",
    "丙": "庚", "辛": "庚",
    "丁": "壬", "壬": "壬",
    "戊": "甲", "癸": "甲",
}


def _month_stem(year_stem: str, month_branch: str) -> str:
    """五虎遁求月干。"""
    start_stem = TIGER_ESCAPE[year_stem]
    start_idx = STEMS.index(start_stem)
    # 寅月为正月，月支索引：寅=2
    branch_idx = BRANCHES.index(month_branch)
    offset = (branch_idx - 2) % 12
    return STEMS[(start_idx + offset) % 10]


# ── 五鼠遁：日干推时干 ──────────────────────────────────
# 甲己还加甲，乙庚丙作初，
# 丙辛从戊起，丁壬庚子居，
# 戊癸何方发，壬子是真途。
RAT_ESCAPE = {
    "甲": "甲", "己": "甲",
    "乙": "丙", "庚": "丙",
    "丙": "戊", "辛": "戊",
    "丁": "庚", "壬": "庚",
    "戊": "壬", "癸": "壬",
}


def _hour_branch(hour: int, minute: int = 0) -> str:
    """根据小时确定时支。23点起为子时（次日）。"""
    if hour == 23 or hour < 1:
        return "子"
    idx = (hour + 1) // 2
    return BRANCHES[idx]


def _hour_stem(day_stem: str, hour_branch: str) -> str:
    """五鼠遁求时干。"""
    start_stem = RAT_ESCAPE[day_stem]
    start_idx = STEMS.index(start_stem)
    branch_idx = BRANCHES.index(hour_branch)
    return STEMS[(start_idx + branch_idx) % 10]


# ── 日柱计算（儒略日） ──────────────────────────────────
def _julian_day(year: int, month: int, day: int) -> int:
    """计算儒略日数。"""
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def _day_pillar(year: int, month: int, day: int) -> tuple[str, str]:
    """根据公历日期计算日柱。"""
    jdn = _julian_day(year, month, day)
    idx = (jdn + 49) % 60
    stem = STEMS[idx % 10]
    branch = BRANCHES[idx % 12]
    return stem, branch


# ── 年柱计算（立春为界） ─────────────────────────────────
def _year_pillar(year: int, month: int, day: int) -> tuple[str, str]:
    """根据公历日期计算年柱，以立春为界。"""
    # 立春前属上一年
    if (month, day) < (2, 4):
        year -= 1
    stem_idx = (year - 4) % 10
    branch_idx = (year - 4) % 12
    return STEMS[stem_idx], BRANCHES[branch_idx]


@dataclass
class Pillar:
    """单柱：天干 + 地支。"""
    stem: str
    branch: str

    @property
    def ganzhi(self) -> str:
        return self.stem + self.branch

    @property
    def hidden_stems(self) -> list[str]:
        return BRANCH_HIDDEN_STEMS[self.branch]

    @property
    def main_qi(self) -> str:
        return BRANCH_MAIN_QI[self.branch]

    def __str__(self) -> str:
        return self.ganzhi


@dataclass
class FourPillars:
    """四柱：年月日时。"""
    year: Pillar
    month: Pillar
    day: Pillar
    hour: Pillar

    @property
    def day_master(self) -> str:
        return self.day.stem

    def pillars(self) -> list[Pillar]:
        return [self.year, self.month, self.day, self.hour]

    def __str__(self) -> str:
        return f"{self.year} {self.month} {self.day} {self.hour}"


class BaziEngine:
    """四柱排盘引擎。"""

    @staticmethod
    def from_datetime(dt: datetime) -> FourPillars:
        """根据 datetime 计算四柱。"""
        year, month, day = dt.year, dt.month, dt.day
        hour, minute = dt.hour, dt.minute

        # 处理夜子时（23:00-23:59 属于次日）
        if hour >= 23:
            from datetime import timedelta
            dt_next = dt + timedelta(days=1)
            year, month, day = dt_next.year, dt_next.month, dt_next.day

        y_stem, y_branch = _year_pillar(year, month, day)
        m_branch = _month_branch(month, day)
        m_stem = _month_stem(y_stem, m_branch)
        d_stem, d_branch = _day_pillar(year, month, day)
        h_branch = _hour_branch(hour, minute)
        h_stem = _hour_stem(d_stem, h_branch)

        return FourPillars(
            year=Pillar(y_stem, y_branch),
            month=Pillar(m_stem, m_branch),
            day=Pillar(d_stem, d_branch),
            hour=Pillar(h_stem, h_branch),
        )

    @staticmethod
    def from_ymd(year: int, month: int, day: int, hour: int = 12) -> FourPillars:
        """根据年月日时（整数）计算四柱。"""
        return BaziEngine.from_datetime(datetime(year, month, day, hour))

    @staticmethod
    def ten_god_of(day_stem: str, other_stem: str) -> str:
        """计算十神。"""
        return ten_god(day_stem, other_stem)
