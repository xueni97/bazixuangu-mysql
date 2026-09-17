"""五行基础数据：天干、地支、藏干、十神、生克关系。

参考《渊海子平》《三命通会》基础定义。
"""

from __future__ import annotations

# ── 天干地支 ──────────────────────────────────────────────
STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 天干五行（阴阳）
STEM_ELEMENT = {
    "甲": "木", "乙": "木",
    "丙": "火", "丁": "火",
    "戊": "土", "己": "土",
    "庚": "金", "辛": "金",
    "壬": "水", "癸": "水",
}

# 天干阴阳
STEM_YINYANG = {
    "甲": "阳", "乙": "阴",
    "丙": "阳", "丁": "阴",
    "戊": "阳", "己": "阴",
    "庚": "阳", "辛": "阴",
    "壬": "阳", "癸": "阴",
}

# 地支五行
BRANCH_ELEMENT = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木",
    "辰": "土", "巳": "火", "午": "火", "未": "土",
    "申": "金", "酉": "金", "戌": "土", "亥": "水",
}

# 地支藏干（本气、中气、余气）
BRANCH_HIDDEN_STEMS = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "戊", "庚"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

# 地支主气（用于月令判断）
BRANCH_MAIN_QI = {
    "子": "癸", "丑": "己", "寅": "甲", "卯": "乙",
    "辰": "戊", "巳": "丙", "午": "丁", "未": "己",
    "申": "庚", "酉": "辛", "戌": "戊", "亥": "壬",
}

# ── 五行生克 ──────────────────────────────────────────────
# 相生：木→火→土→金→水→木
GENERATE = {
    "木": "火", "火": "土", "土": "金", "金": "水", "水": "木",
}

# 相克：木→土→水→火→金→木
RESTRAIN = {
    "木": "土", "土": "水", "水": "火", "火": "金", "金": "木",
}

# 反生（谁生我）
GENERATED_BY = {v: k for k, v in GENERATE.items()}
# 反克（谁克我）
RESTRAINED_BY = {v: k for k, v in RESTRAIN.items()}


def element_of_stem(stem: str) -> str:
    """天干五行。"""
    return STEM_ELEMENT[stem]


def element_of_branch(branch: str) -> str:
    """地支五行。"""
    return BRANCH_ELEMENT[branch]


def is_same_element(a: str, b: str) -> bool:
    return a == b


def generates(parent: str, child: str) -> bool:
    """parent 生 child。"""
    return GENERATE.get(parent) == child


def restrains(a: str, b: str) -> bool:
    """a 克 b。"""
    return RESTRAIN.get(a) == b


# ── 十神 ─────────────────────────────────────────────────
# 以日主为中心，根据五行关系与阴阳异同确定十神
# 同我者：比肩（同阴阳）、劫财（异阴阳）
# 我生者：食神（同阴阳）、伤官（异阴阳）
# 我克者：偏财（同阴阳）、正财（异阴阳）
# 克我者：七杀（同阴阳）、正官（异阴阳）
# 生我者：偏印（同阴阳）、正印（异阴阳）

def ten_god(day_stem: str, other_stem: str) -> str:
    """根据日主天干与另一干的关系，返回十神名称。"""
    day_elem = STEM_ELEMENT[day_stem]
    other_elem = STEM_ELEMENT[other_stem]
    day_yinyang = STEM_YINYANG[day_stem]
    other_yinyang = STEM_YINYANG[other_stem]
    same_yinyang = day_yinyang == other_yinyang

    if other_elem == day_elem:
        return "比肩" if same_yinyang else "劫财"
    elif GENERATE[day_elem] == other_elem:  # 我生
        return "食神" if same_yinyang else "伤官"
    elif RESTRAIN[day_elem] == other_elem:  # 我克
        return "偏财" if same_yinyang else "正财"
    elif RESTRAIN[other_elem] == day_elem:  # 克我
        return "七杀" if same_yinyang else "正官"
    elif GENERATE[other_elem] == day_elem:  # 生我
        return "偏印" if same_yinyang else "正印"
    else:
        return "未知"


# 十神与五行的对应（以日主为参照，十神代表的外来五行属性）
# 用于快速判断某十神对应的五行
TEN_GOD_ELEMENT = {
    "比肩": "同我", "劫财": "同我",
    "食神": "我生", "伤官": "我生",
    "偏财": "我克", "正财": "我克",
    "七杀": "克我", "正官": "克我",
    "偏印": "生我", "正印": "生我",
}

# 十神对应的五行（相对日主）
TEN_GODS = ["比肩", "劫财", "食神", "伤官", "偏财", "正财", "七杀", "正官", "偏印", "正印"]


def ten_god_element(day_stem: str, god: str) -> str | None:
    """返回某十神相对于日主的五行属性。"""
    day_elem = STEM_ELEMENT[day_stem]
    mapping = TEN_GOD_ELEMENT.get(god)
    if mapping == "同我":
        return day_elem
    elif mapping == "我生":
        return GENERATE[day_elem]
    elif mapping == "我克":
        return RESTRAIN[day_elem]
    elif mapping == "克我":
        return RESTRAINED_BY[day_elem]
    elif mapping == "生我":
        return GENERATED_BY[day_elem]
    return None


# ── 五行旺衰（得令） ──────────────────────────────────────
# 五行在各月令的旺相休囚死
# 当令者旺，我生者相，生我者休，克我者囚，我克者死
SEASON_ELEMENT = {
    "寅": "木", "卯": "木", "辰": "土",   # 春
    "巳": "火", "午": "火", "未": "土",   # 夏
    "申": "金", "酉": "金", "戌": "土",   # 秋
    "亥": "水", "子": "水", "丑": "土",   # 冬
}

# 四季月（辰戌丑未）属土，但余气有所属
# 旺衰表：[旺, 相, 休, 囚, 死]
# 春(寅卯): 木旺、火相、水休、金囚、土死
# 夏(巳午): 火旺、土相、木休、水囚、金死
# 秋(申酉): 金旺、水相、土休、火囚、木死
# 冬(亥子): 水旺、木相、金休、土囚、火死
# 四季(辰戌丑未): 土旺、金相、火休、木囚、水死

def element_strength_in_month(month_branch: str, element: str) -> str:
    """返回某五行在月令中的旺衰状态：旺/相/休/囚/死。"""
    season = SEASON_ELEMENT[month_branch]

    if element == season:
        return "旺"
    elif GENERATE[season] == element:  # 季生我
        return "相"
    elif GENERATE[element] == season:  # 我生季
        return "休"
    elif RESTRAIN[element] == season:  # 我克季
        return "囚"
    elif RESTRAIN[season] == element:  # 季克我
        return "死"
    return "休"


# 旺衰得分（量化）
STRENGTH_SCORE = {"旺": 3, "相": 2, "休": 1, "囚": 0.5, "死": 0}

# ── 六十甲子 ──────────────────────────────────────────────
GANZHI_60 = [
    "甲子", "乙丑", "丙寅", "丁卯", "戊辰", "己巳", "庚午", "辛未", "壬申", "癸酉",
    "甲戌", "乙亥", "丙子", "丁丑", "戊寅", "己卯", "庚辰", "辛巳", "壬午", "癸未",
    "甲申", "乙酉", "丙戌", "丁亥", "戊子", "己丑", "庚寅", "辛卯", "壬辰", "癸巳",
    "甲午", "乙未", "丙申", "丁酉", "戊戌", "己亥", "庚子", "辛丑", "壬寅", "癸卯",
    "甲辰", "乙巳", "丙午", "丁未", "戊申", "己酉", "庚戌", "辛亥", "壬子", "癸丑",
    "甲寅", "乙卯", "丙辰", "丁巳", "戊午", "己未", "庚申", "辛酉", "壬戌", "癸亥",
]
GANZHI_INDEX = {gz: i for i, gz in enumerate(GANZHI_60)}


def ganzhi_index(ganzhi: str) -> int:
    return GANZHI_INDEX[ganzhi]


def index_to_ganzhi(idx: int) -> str:
    return GANZHI_60[idx % 60]
