"""渊海子平命理选股模型。

基于天干地支四柱排盘与《渊海子平》旺衰用神之法，
结合股票名称/行业五行属性，给出周/月/日方向与买点建议。
"""

from .bazi import BaziEngine, Pillar, FourPillars
from .elements import (
    STEMS,
    BRANCHES,
    STEM_ELEMENT,
    BRANCH_ELEMENT,
    BRANCH_HIDDEN_STEMS,
    GENERATE,
    RESTRAIN,
    TEN_GODS,
    element_of_stem,
    element_of_branch,
)
from .stock_element import StockElementAnalyzer
from .model import YuanhaiDecisionModel
from .report import YuanhaiReport, run_report

__all__ = [
    "BaziEngine",
    "Pillar",
    "FourPillars",
    "STEMS",
    "BRANCHES",
    "STEM_ELEMENT",
    "BRANCH_ELEMENT",
    "BRANCH_HIDDEN_STEMS",
    "GENERATE",
    "RESTRAIN",
    "TEN_GODS",
    "element_of_stem",
    "element_of_branch",
    "StockElementAnalyzer",
    "YuanhaiDecisionModel",
    "YuanhaiReport",
    "run_report",
]
