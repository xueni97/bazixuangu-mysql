"""渊海子平决策模型。

核心逻辑（参考《渊海子平》《子平真诠》）：
1. 以当日四柱为"市场命盘"，月令为提纲
2. 判断五行旺衰：得令、得地、得势
3. 定用神（扶抑 + 调候）：
   - 身旺（日主得令得地）→ 用神为克/泄/耗（官杀、食伤、财星）
   - 身弱（日主失令失地）→ 用神为生/助（印星、比劫）
   - 调候：冬月喜火暖，夏月喜水润
4. 评分模型：
   - 股票五行 = 用神 → 高分（强烈推荐）
   - 股票五行 生用神 → 次高分（通关助力）
   - 股票五行 = 忌神 → 负分（回避）
   - 股票五行 克用神 → 低分（谨慎）
5. 输出周/月/日方向与买点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .bazi import BaziEngine, FourPillars, Pillar
from .elements import (
    STEMS,
    BRANCHES,
    STEM_ELEMENT,
    BRANCH_ELEMENT,
    BRANCH_HIDDEN_STEMS,
    BRANCH_MAIN_QI,
    GENERATE,
    RESTRAIN,
    GENERATED_BY,
    RESTRAINED_BY,
    element_strength_in_month,
    STRENGTH_SCORE,
    ten_god,
    ten_god_element,
)


@dataclass
class ElementAnalysis:
    """五行旺衰分析结果。"""
    element: str
    strength: str  # 旺/相/休/囚/死
    score: float
    pillars_count: int  # 出现柱数
    is_use_god: bool = False
    is_avoid_god: bool = False


@dataclass
class BaziAnalysis:
    """八字分析结果。"""
    pillars: FourPillars
    day_master: str
    day_master_element: str
    month_branch: str
    month_command_element: str
    day_master_strength: str  # 旺/弱
    use_gods: list[str]  # 用神（有利五行）
    avoid_gods: list[str]  # 忌神（不利五行）
    element_scores: dict[str, float]
    elements_strength: dict[str, str]
    ten_gods_of_pillars: dict[str, str]  # 各柱十神
    tone_god: str | None = None  # 调候用神


class YuanhaiDecisionModel:
    """渊海子平决策模型。"""

    # 五行列表
    ELEMENTS = ["木", "火", "土", "金", "水"]

    @staticmethod
    def analyze(pillars: FourPillars) -> BaziAnalysis:
        """分析四柱八字，确定用神忌神。"""
        day_master = pillars.day.stem
        day_elem = STEM_ELEMENT[day_master]
        month_branch = pillars.month.branch

        # ── 1. 统计五行得分 ──
        element_scores: dict[str, float] = {e: 0.0 for e in YuanhaiDecisionModel.ELEMENTS}
        elements_strength: dict[str, str] = {}

        # 月令旺衰表
        for elem in YuanhaiDecisionModel.ELEMENTS:
            strength = element_strength_in_month(month_branch, elem)
            elements_strength[elem] = strength
            element_scores[elem] += STRENGTH_SCORE[strength]

        # 天干计分（权重 1.0）
        for p in pillars.pillars():
            e = STEM_ELEMENT[p.stem]
            element_scores[e] += 1.0

        # 地支藏干计分（本气 1.0，中气 0.5，余气 0.3）
        for p in pillars.pillars():
            hidden = BRANCH_HIDDEN_STEMS[p.branch]
            for i, stem in enumerate(hidden):
                e = STEM_ELEMENT[stem]
                weight = [1.0, 0.5, 0.3][i] if i < 3 else 0.2
                element_scores[e] += weight

        # ── 2. 判断日主旺衰 ──
        # 得令：日主五行在月令为旺/相
        dm_strength_in_month = elements_strength[day_elem]
        de_mingling = dm_strength_in_month in ("旺", "相")

        # 得地：日主五行在地支中有根（地支五行=日主五行，或藏干含日主五行）
        de_dedi = 0
        for p in pillars.pillars():
            if BRANCH_ELEMENT[p.branch] == day_elem:
                de_dedi += 1
            else:
                for h in BRANCH_HIDDEN_STEMS[p.branch]:
                    if STEM_ELEMENT[h] == day_elem:
                        de_dedi += 0.3
        de_dedi_bool = de_dedi >= 1.0

        # 得势：天干中有比劫帮身
        de_deshi = 0
        for p in pillars.pillars():
            if p is pillars.day:
                continue
            if STEM_ELEMENT[p.stem] == day_elem:
                de_deshi += 1
        de_deshi_bool = de_deshi >= 1

        # 综合判断：得令 + (得地 或 得势) → 旺；否则弱
        day_master_strength = "旺" if (de_mingling and (de_dedi_bool or de_deshi_bool)) else "弱"
        # 特殊：若得令且得地且得势 → 极旺
        if de_mingling and de_dedi_bool and de_deshi_bool:
            day_master_strength = "极旺"

        # ── 3. 定用神（扶抑用神） ──
        use_gods: list[str] = []
        avoid_gods: list[str] = []

        if day_master_strength in ("旺", "极旺"):
            # 身旺：用神为克我(官杀)、我生(食伤)、我克(财星)
            # 忌神为生我(印星)、同我(比劫)
            for god in ["正官", "七杀", "食神", "伤官", "正财", "偏财"]:
                ge = ten_god_element(day_master, god)
                if ge and ge not in use_gods:
                    use_gods.append(ge)
            for god in ["正印", "偏印", "比肩", "劫财"]:
                ge = ten_god_element(day_master, god)
                if ge and ge not in avoid_gods:
                    avoid_gods.append(ge)
        else:
            # 身弱：用神为生我(印星)、同我(比劫)
            # 忌神为克我(官杀)、我生(食伤)、我克(财星)
            for god in ["正印", "偏印", "比肩", "劫财"]:
                ge = ten_god_element(day_master, god)
                if ge and ge not in use_gods:
                    use_gods.append(ge)
            for god in ["正官", "七杀", "食神", "伤官", "正财", "偏财"]:
                ge = ten_god_element(day_master, god)
                if ge and ge not in avoid_gods:
                    avoid_gods.append(ge)

        # ── 4. 调候用神 ──
        # 参考《穷通宝鉴》调候原则：
        # 春木旺，喜金修剪；夏火旺，喜水滋润；
        # 秋金旺，喜火锻炼；冬水旺，喜土堤防 + 火暖。
        # 四季月（辰戌丑未）土旺，喜木疏土。
        tone_god = None
        if month_branch in ("亥", "子", "丑"):
            # 冬月水旺寒，调候用火暖局
            tone_god = "火"
            if "火" not in use_gods:
                use_gods.insert(0, "火")
        elif month_branch in ("巳", "午", "未"):
            # 夏月火旺燥，调候用水润燥
            tone_god = "水"
            if "水" not in use_gods:
                use_gods.insert(0, "水")
        elif month_branch in ("寅", "卯", "辰"):
            # 春月木旺，调候用金修剪（兼制木）
            tone_god = "金"
            if "金" not in use_gods:
                use_gods.insert(0, "金")
        elif month_branch in ("申", "酉", "戌"):
            # 秋月金旺，调候用火锻炼（兼制金）
            tone_god = "火"
            if "火" not in use_gods:
                use_gods.insert(0, "火")

        # ── 5. 各柱十神 ──
        ten_gods_of_pillars = {}
        for name, p in [("年", pillars.year), ("月", pillars.month),
                        ("日", pillars.day), ("时", pillars.hour)]:
            ten_gods_of_pillars[name] = ten_god(day_master, p.stem)

        return BaziAnalysis(
            pillars=pillars,
            day_master=day_master,
            day_master_element=day_elem,
            month_branch=month_branch,
            month_command_element=BRANCH_ELEMENT[month_branch],
            day_master_strength=day_master_strength,
            use_gods=use_gods,
            avoid_gods=avoid_gods,
            element_scores=element_scores,
            elements_strength=elements_strength,
            ten_gods_of_pillars=ten_gods_of_pillars,
            tone_god=tone_god,
        )

    @staticmethod
    def stock_score(stock_element: str | None, analysis: BaziAnalysis,
                    stock_name: str | None = None) -> dict:
        """
        根据股票五行与用神匹配度评分。

        返回: {score, level, reason}
        score: -100 ~ 100
        level: 强烈推荐/推荐/中性/谨慎/回避
        """
        from .stock_element import StockElementAnalyzer

        if stock_element is None:
            return {"score": 0, "level": "中性", "reason": "五行属性不明"}

        use_gods = analysis.use_gods
        avoid_gods = analysis.avoid_gods
        tone_god = analysis.tone_god

        score = 0
        reasons = []

        # 用神匹配
        if stock_element in use_gods:
            # 调候用神优先级最高
            if stock_element == tone_god:
                score += 40
                reasons.append(f"{stock_element}为调候用神")
            else:
                rank = use_gods.index(stock_element)
                score += max(30 - rank * 8, 12)
                reasons.append(f"{stock_element}为扶抑用神(第{rank+1}位)")
        elif stock_element in avoid_gods:
            rank = avoid_gods.index(stock_element)
            score -= max(30 - rank * 8, 12)
            reasons.append(f"{stock_element}为忌神(第{rank+1}位)")
        else:
            # 闲神：看与用神的关系
            if GENERATE.get(stock_element) in use_gods:
                score += 12
                reasons.append(f"{stock_element}生用神({GENERATE[stock_element]})")
            elif RESTRAIN.get(stock_element) in use_gods:
                score -= 18
                reasons.append(f"{stock_element}克用神({RESTRAIN[stock_element]})")
            elif GENERATED_BY.get(stock_element) in use_gods:
                score -= 8
                reasons.append(f"{stock_element}为用神所生")
            else:
                reasons.append(f"{stock_element}为闲神")

        # 名称字符级匹配度加成（精细化区分）
        if stock_name:
            for target in use_gods[:2]:
                char_score = StockElementAnalyzer.score_by_element(stock_name, target)
                if char_score > 0:
                    bonus = int(char_score * 30)
                    score += bonus
                    if bonus > 0:
                        reasons.append(f"名称含{target}气({char_score:.2f})")
                    break

        # 月令旺衰加成
        strength = analysis.elements_strength.get(stock_element, "休")
        if strength == "旺":
            score += 8
            reasons.append("得月令旺气")
        elif strength == "相":
            score += 4
        elif strength == "死":
            score -= 8
            reasons.append("月令处死地")

        score = max(-100, min(100, score))

        if score >= 45:
            level = "强烈推荐"
        elif score >= 15:
            level = "推荐"
        elif score >= -10:
            level = "中性"
        elif score >= -35:
            level = "谨慎"
        else:
            level = "回避"

        return {"score": score, "level": level, "reason": "；".join(reasons)}

    # ── 多周期叠加综合评分 ─────────────────────────────────────
    # 长期趋势权重高、短期日内权重低；实际使用按勾选项归一化
    PERIOD_WEIGHTS = {"monthly": 0.5, "weekly": 0.3, "daily": 0.2}
    PERIOD_LABELS = {"monthly": "月", "weekly": "周", "daily": "日"}

    @staticmethod
    def _score_level(score: int) -> str:
        if score >= 45:
            return "强烈推荐"
        if score >= 15:
            return "推荐"
        if score >= -10:
            return "中性"
        if score >= -35:
            return "谨慎"
        return "回避"

    @staticmethod
    def _period_datetime(dt: datetime, period: str) -> datetime:
        """各周期的代表时点：日=当时，周=本周一，月=当月1号。"""
        if period == "daily":
            return dt
        if period == "weekly":
            return (dt - timedelta(days=dt.weekday())).replace(
                hour=dt.hour, minute=0, second=0, microsecond=0)
        if period == "monthly":
            return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        raise ValueError(f"未知周期: {period}")

    @staticmethod
    def period_analyses(dt: datetime) -> dict:
        """返回日/周/月三个代表时点的四柱与用神分析（扫描时只算一次）。"""
        out = {}
        for key in ("monthly", "weekly", "daily"):
            pdt = YuanhaiDecisionModel._period_datetime(dt, key)
            pillars = BaziEngine.from_datetime(pdt)
            out[key] = {
                "date": pdt.strftime("%Y-%m-%d"),
                "pillars": pillars,
                "analysis": YuanhaiDecisionModel.analyze(pillars),
            }
        return out

    @staticmethod
    def composite_score(stock_element, period_data, selected,
                        stock_name=None, weights=None) -> dict:
        """多周期加权综合评分 + 同向共振加成（非线性封顶）。

        period_data: period_analyses() 的返回值
        selected: 勾选周期，如 ['monthly','weekly','daily']
        返回 {score, level, reason, periodScores}
        """
        weights = weights or YuanhaiDecisionModel.PERIOD_WEIGHTS
        chosen = [p for p in ("monthly", "weekly", "daily") if p in selected] or ["daily"]
        total_w = sum(weights[p] for p in chosen)

        period_scores = {}
        weighted = 0.0
        for p in chosen:
            info = YuanhaiDecisionModel.stock_score(
                stock_element, period_data[p]["analysis"], stock_name)
            period_scores[p] = info
            weighted += weights[p] * info["score"] / total_w

        score = weighted
        # 共振加成：所有勾选周期同向才给，封顶 ±15，防止多信号堆叠过度自信
        raws = [period_scores[p]["score"] for p in chosen]
        resonance = ""
        if len(chosen) > 1:
            label = "".join(YuanhaiDecisionModel.PERIOD_LABELS[p] for p in chosen)
            if min(raws) >= 45:
                score += 15
                resonance = f"{label}周期强烈共振"
            elif min(raws) >= 15:
                score += 8
                resonance = f"{label}周期共振看多"
            elif max(raws) <= -35:
                score -= 15
                resonance = f"{label}周期共振回避"
            elif max(raws) <= -15:
                score -= 8
                resonance = f"{label}周期共振走弱"

        score = int(max(-100, min(100, round(score))))
        # 综合理由：共振结论 + 权重最高周期的分项理由
        anchor = max(chosen, key=lambda p: weights[p])
        anchor_reason = period_scores[anchor]["reason"]
        reason = "；".join(x for x in (resonance, anchor_reason) if x)

        return {
            "score": score,
            "level": YuanhaiDecisionModel._score_level(score),
            "reason": reason,
            "periodScores": period_scores,
        }

    @staticmethod
    def daily_direction(dt: datetime) -> dict:
        """当日方向分析。"""
        pillars = BaziEngine.from_datetime(dt)
        analysis = YuanhaiDecisionModel.analyze(pillars)
        return {
            "date": dt.strftime("%Y-%m-%d"),
            "weekday": dt.strftime("%A"),
            "pillars": str(pillars),
            "day_master": analysis.day_master,
            "day_master_element": analysis.day_master_element,
            "month_command": analysis.month_branch,
            "day_master_strength": analysis.day_master_strength,
            "use_gods": analysis.use_gods,
            "avoid_gods": analysis.avoid_gods,
            "tone_god": analysis.tone_god,
            "elements_strength": analysis.elements_strength,
            "element_scores": analysis.element_scores,
            "recommended_elements": analysis.use_gods[:3],
            "avoid_elements": analysis.avoid_gods[:3],
            "ten_gods": analysis.ten_gods_of_pillars,
        }

    @staticmethod
    def weekly_direction(dt: datetime) -> dict:
        """本周方向分析（取周一为基准）。"""
        # 周一
        monday = dt - timedelta(days=dt.weekday())
        pillars = BaziEngine.from_datetime(monday)
        analysis = YuanhaiDecisionModel.analyze(pillars)

        # 统计本周各日五行倾向
        week_days = []
        for i in range(5):  # 周一到周五
            day = monday + timedelta(days=i)
            dp = BaziEngine.from_datetime(day)
            da = YuanhaiDecisionModel.analyze(dp)
            week_days.append({
                "date": day.strftime("%Y-%m-%d"),
                "weekday": day.strftime("%a"),
                "pillar": str(dp.day),
                "use_gods": da.use_gods[:2],
                "avoid_gods": da.avoid_gods[:2],
                "day_master_strength": da.day_master_strength,
            })

        # 统计本周用神频次
        use_god_counter: dict[str, int] = {}
        for wd in week_days:
            for g in wd["use_gods"]:
                use_god_counter[g] = use_god_counter.get(g, 0) + 1
        top_use = sorted(use_god_counter.items(), key=lambda x: -x[1])

        return {
            "week_start": monday.strftime("%Y-%m-%d"),
            "week_pillars": str(pillars),
            "day_master": analysis.day_master,
            "week_use_gods": [g for g, _ in top_use[:3]],
            "week_avoid_gods": analysis.avoid_gods[:3],
            "daily_breakdown": week_days,
            "best_day": max(week_days, key=lambda x: len(x["use_gods"])),
        }

    @staticmethod
    def monthly_direction(dt: datetime) -> dict:
        """本月方向分析（取月初为基准，以月柱为核心）。"""
        # 取当月1日
        first_day = dt.replace(day=1)
        pillars = BaziEngine.from_datetime(first_day)
        analysis = YuanhaiDecisionModel.analyze(pillars)

        # 月柱是核心
        month_pillar = pillars.month
        month_stem_elem = STEM_ELEMENT[month_pillar.stem]
        month_branch_elem = BRANCH_ELEMENT[month_pillar.branch]

        # 统计本月各周用神
        weekly_use_counter: dict[str, int] = {}
        weeks = []
        for week_offset in range(5):
            week_start = first_day + timedelta(days=week_offset * 7)
            if week_start.month != dt.month:
                break
            wp = BaziEngine.from_datetime(week_start)
            wa = YuanhaiDecisionModel.analyze(wp)
            for g in wa.use_gods[:2]:
                weekly_use_counter[g] = weekly_use_counter.get(g, 0) + 1
            weeks.append({
                "week_start": week_start.strftime("%Y-%m-%d"),
                "use_gods": wa.use_gods[:2],
                "avoid_gods": wa.avoid_gods[:2],
            })

        top_month_use = sorted(weekly_use_counter.items(), key=lambda x: -x[1])

        return {
            "year_month": dt.strftime("%Y-%m"),
            "month_pillar": str(month_pillar),
            "month_stem_element": month_stem_elem,
            "month_branch_element": month_branch_elem,
            "day_master": analysis.day_master,
            "day_master_strength": analysis.day_master_strength,
            "month_use_gods": [g for g, _ in top_month_use[:3]],
            "month_avoid_gods": analysis.avoid_gods[:3],
            "tone_god": analysis.tone_god,
            "weekly_breakdown": weeks,
            "recommended_sectors": YuanhaiDecisionModel._element_to_sectors(
                [g for g, _ in top_month_use[:3]]
            ),
        }

    @staticmethod
    def _element_to_sectors(elements: list[str]) -> dict[str, list[str]]:
        """五行→推荐行业板块。"""
        sector_map = {
            "木": ["农林牧渔", "医药生物", "纺织服饰", "教育", "造纸印刷"],
            "火": ["电子", "电力设备", "国防军工", "计算机", "传媒", "通信", "石油石化", "煤炭"],
            "土": ["房地产", "建筑装饰", "建筑材料", "钢铁", "有色金属", "基础化工"],
            "金": ["银行", "非银金融", "汽车", "机械设备", "家用电器", "食品饮料"],
            "水": ["交通运输", "商贸零售", "公用事业", "环保", "社会服务"],
        }
        return {e: sector_map.get(e, []) for e in elements}

    @staticmethod
    def buy_point_signal(dt: datetime) -> dict:
        """
        买点信号分析。

        基于当日四柱的十神组合判断买卖时机：
        - 财星旺+食伤生 → 买入信号（财源广进）
        - 官杀旺+印化 → 持有信号（贵人扶持）
        - 比劫旺夺财 → 卖出/观望信号（破财之象）
        - 印旺身强 → 观望信号（壅滞不动）
        """
        pillars = BaziEngine.from_datetime(dt)
        analysis = YuanhaiDecisionModel.analyze(pillars)
        dm = analysis.day_master

        # 统计十神出现频次
        god_counter: dict[str, int] = {}
        for name, p in [("年", pillars.year), ("月", pillars.month),
                        ("日", pillars.day), ("时", pillars.hour)]:
            god = ten_god(dm, p.stem)
            god_counter[god] = god_counter.get(god, 0) + 1
            # 地支藏干也算
            for h in BRANCH_HIDDEN_STEMS[p.branch]:
                god = ten_god(dm, h)
                god_counter[god] = god_counter.get(god, 0) + 0.5

        # 计算各类十神总分
        wealth = god_counter.get("正财", 0) + god_counter.get("偏财", 0)
        food = god_counter.get("食神", 0) + god_counter.get("伤官", 0)
        officer = god_counter.get("正官", 0) + god_counter.get("七杀", 0)
        seal = god_counter.get("正印", 0) + god_counter.get("偏印", 0)
        friend = god_counter.get("比肩", 0) + god_counter.get("劫财", 0)

        score = 0
        signals = []
        is_strong = analysis.day_master_strength in ("旺", "极旺")

        if is_strong:
            # ── 身旺：喜泄(食伤)、耗(财)、克(官杀)，忌生(印)、助(比劫) ──
            # 食伤生财 → 买入
            if food >= 1.5 and wealth >= 0.5:
                score += 30
                signals.append("身旺食伤生财，财源有源")
            # 财星有力 → 买入
            if wealth >= 1.5:
                score += 20
                signals.append("财星得用，求财有利")
            # 官杀制身 → 持有（有规矩约束）
            if officer >= 1:
                score += 10
                signals.append("官杀制身，稳健可持")
            # 比劫夺财 → 卖出
            if friend >= 2 and wealth >= 0.5:
                score -= 30
                signals.append("比劫夺财，防破财")
            # 印旺身壅 → 观望
            if seal >= 2:
                score -= 15
                signals.append("印旺助壅，宜静不宜动")
        else:
            # ── 身弱：喜生(印)、助(比劫)，忌泄(食伤)、耗(财)、克(官杀) ──
            # 印星生身 → 买入（贵人扶持）
            if seal >= 2:
                score += 30
                signals.append("身弱印旺生身，贵人扶持")
            # 比劫帮身 → 持有
            if friend >= 1.5:
                score += 15
                signals.append("比劫帮身，有支撑")
            # 食伤泄身 → 谨慎
            if food >= 1.5:
                score -= 15
                signals.append("食伤泄气，身弱不胜")
            # 财官杀旺 → 卖出/观望（身弱不胜财官）
            if wealth >= 1.5 or officer >= 1.5:
                score -= 25
                signals.append("身弱不胜财官，宜守不宜攻")
            # 印比全无 → 卖出
            if seal < 1 and friend < 1:
                score -= 20
                signals.append("身弱无助，孤立无援")

        # 调候用神到位加分
        if analysis.tone_god:
            tone_elem = analysis.tone_god
            tone_count = sum(
                v for k, v in god_counter.items()
                if ten_god_element(analysis.day_master, k) == tone_elem
            )
            if tone_count >= 1:
                score += 10
                signals.append(f"调候用神({tone_elem})到位")

        if score >= 20:
            action = "买入"
        elif score >= 5:
            action = "轻仓试探"
        elif score >= -10:
            action = "观望"
        elif score >= -25:
            action = "减仓"
        else:
            action = "卖出"

        return {
            "date": dt.strftime("%Y-%m-%d"),
            "pillars": str(pillars),
            "ten_god_distribution": god_counter,
            "wealth_score": wealth,
            "food_score": food,
            "officer_score": officer,
            "seal_score": seal,
            "friend_score": friend,
            "signal_score": score,
            "action": action,
            "signals": signals,
        }
