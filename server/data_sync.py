"""全市场股票快照同步模块（多数据源 fallback 链）。

背景：东财 push2 主域在部分网络下连接被重置（RemoteDisconnected），导致
akshare stock_zh_a_spot_em 长期拉取失败。经实测验证的可用源链：

1. eastmoney-delay  push2delay.eastmoney.com（东财延时域，全市场含北交所，~10 次分页请求，最快）
2. tencent          qt.gtimg.cn 批量行情（按 stock_names 代码表批量拉，含北交所 bj 前缀）
3. sina             vip.stock.finance.sina.com.cn 列表接口（沪深A+北交所 920 段，80 条/页）

状态机 idle → syncing → done/failed，线程安全（后台线程同步，不阻塞 API）。
stock_spot 表每次同步整表替换，sync_meta 记录最近成功日期与使用的数据源。
"""

from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pymysql
import requests

from db import create_tables as ensure_tables  # noqa: F401  保留旧函数名，调用方无需改
from db import get_conn

MAX_RETRIES_PER_SOURCE = 2
RETRY_INTERVAL = 3  # 秒
REQUEST_TIMEOUT = 15  # 秒
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_lock = threading.Lock()
_state = {
    "status": "idle",       # idle / syncing / failed
    "phase": "",            # 阶段描述
    "last_error": "",
    "started_at": "",
    "finished_at": "",
}


def get_state() -> dict:
    """当前同步状态（供 API 查询，含快照与均线两套状态）。"""
    with _lock:
        st = dict(_state)
    last = get_last_success()
    st["last_success_date"] = last
    st["today_synced"] = (last == datetime.now().strftime("%Y-%m-%d"))
    st.update(get_ma_state())
    return st


def get_last_success() -> str | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM sync_meta WHERE meta_key = 'last_success_date'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_spot_count() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM stock_spot").fetchone()[0]
    except pymysql.MySQLError:
        return 0
    finally:
        conn.close()


def classify_market(symbol: str) -> str:
    """按代码前缀划分市场。"""
    if symbol.startswith(("60", "68")):
        return "沪"
    if symbol.startswith(("00", "30")):
        return "深"
    return "北交所"


def _to_num(v):
    """行情值规范化：'-'、NaN、0、None → None。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f == 0:
        return None
    return f


# ── 数据源 1：东财 push2delay（分页 JSON，全市场含北交所） ──────────

_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_EM_PAGE_SIZE = 100  # push2delay 域单页上限 100 条


def _parse_em_payload(payload: dict) -> list[tuple]:
    """解析东财 clist JSON → 规范化行。纯函数，便于测试。"""
    data = (payload or {}).get("data") or {}
    diffs = data.get("diff") or []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for d in diffs:
        symbol = str(d.get("f12", "")).strip()
        if not symbol:
            continue
        name = str(d.get("f14", "") or "").strip()
        rows.append((
            symbol, name,
            _to_num(d.get("f2")),
            _to_num(d.get("f3")),
            classify_market(symbol), now,
        ))
    return rows


def _fetch_eastmoney() -> list[tuple]:
    """东财延时域全市场快照。"""
    session = requests.Session()
    session.headers.update(_UA)
    all_rows: dict[str, tuple] = {}
    page = 1
    while True:
        url = (
            "https://push2delay.eastmoney.com/api/qt/clist/get?"
            f"pn={page}&pz={_EM_PAGE_SIZE}&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
            f"&fltt=2&invt=2&fid=f12&fs={_EM_FS}&fields=f12,f13,f14,f2,f3"
        )
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        rows = _parse_em_payload(payload)
        if not rows:
            break
        for row in rows:
            all_rows[row[0]] = row
        total = ((payload or {}).get("data") or {}).get("total") or 0
        if page * _EM_PAGE_SIZE >= total or len(rows) < _EM_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.2)
    return list(all_rows.values())


# ── 数据源 2：腾讯批量行情（按本地代码表，含北交所 bj 前缀） ────────


def _parse_tencent_text(text: str) -> list[tuple]:
    """解析腾讯 qt.gtimg.cn 批量响应。纯函数，便于测试。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        parts = line.split("=", 1)[1].strip().strip('"').split("~")
        if len(parts) < 33:
            continue
        code_field = parts[2].strip()
        symbol = code_field.lower().replace("sh", "").replace("sz", "").replace("bj", "") \
            if not code_field[:1].isdigit() else code_field
        if len(symbol) != 6 or not symbol.isdigit():
            symbol = parts[2][-6:] if parts[2][-6:].isdigit() else ""
        if not symbol:
            continue
        name = parts[1].strip()
        rows.append((
            symbol, name,
            _to_num(parts[3]),
            _to_num(parts[32]),
            classify_market(symbol), now,
        ))
    return rows


def _fetch_tencent(conn) -> list[tuple]:
    """按 stock_names 全表代码，腾讯批量行情拉取。"""
    names = conn.execute("SELECT symbol FROM stock_names ORDER BY symbol").fetchall()
    symbols = [r[0] for r in names]
    if not symbols:
        raise RuntimeError("stock_names 为空，腾讯源不可用")

    session = requests.Session()
    session.headers.update(_UA)
    rows = []
    batch = 80
    for i in range(0, len(symbols), batch):
        codes = ",".join(
            ("sh" if s.startswith(("6",)) else "bj" if s.startswith(("4", "8", "92")) else "sz") + s
            for s in symbols[i:i + batch]
        )
        r = session.get(f"https://qt.gtimg.cn/q={codes}", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        r.encoding = "gbk"
        rows.extend(_parse_tencent_text(r.text))
        time.sleep(0.1)
    if not rows:
        raise RuntimeError("腾讯源返回空数据")
    return rows


# ── 数据源 3：新浪列表接口（沪深A + 北交所920段） ──────────────────


def _parse_sina_list(items: list) -> list[tuple]:
    """解析新浪 Market_Center.getHQNodeData 列表。纯函数，便于测试。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for d in items:
        raw = str(d.get("symbol", "")).strip()  # 如 sh600519 / bj920000
        symbol = raw[-6:]
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        rows.append((
            symbol,
            str(d.get("name", "") or "").strip(),
            _to_num(d.get("trade")),
            _to_num(d.get("changepercent")),
            classify_market(symbol), now,
        ))
    return rows


def _fetch_sina() -> list[tuple]:
    """新浪 hs_a 节点分页列表。"""
    session = requests.Session()
    session.headers.update({**_UA, "Referer": "https://finance.sina.com.cn"})
    all_rows: dict[str, tuple] = {}
    page, num = 1, 80
    while True:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?"
            f"page={page}&num={num}&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page"
        )
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        items = json.loads(r.text)
        rows = _parse_sina_list(items)
        if not rows:
            break
        for row in rows:
            all_rows[row[0]] = row
        if len(items) < num:
            break
        page += 1
        time.sleep(0.2)
    if not all_rows:
        raise RuntimeError("新浪源返回空数据")
    return list(all_rows.values())


# ── 长周期均线(144/288 日线)指标同步 ──────────────────────────
#
# 用途：扫描时筛选"股价回踩至 144/288 日均线附近"的标的。
# 需要近 ~288 个交易日的前复权日K收盘价，无法在扫描时实时拉取（5900+ 只），
# 因此独立于快照任务：每日盘后/手动触发一次，并发拉取后只把均线结果落 stock_ma。
#
# 数据源：东财 push2his（沪深北全市场，0./1. secid，镜像轮换+退避重试）为主，
#         腾讯 ifzq 前复权K线（仅沪深）、新浪日K（仅沪深）依次兜底。
# 注意：东财对高并发会按 IP 临时断连限流，故 MA_WORKERS=4 且失败补拉。

MA_WINDOWS = (144, 288)
MA_KLINE_LMT = 320          # 288 均线 + 20 回踩窗口 + 余量
MA_WORKERS = 4              # 并发线程（过高会触发东财 IP 限流断连）
MA_RETRY = 3                # 单源网络错误重试次数（退避递增）
MA_RETRY_PAUSE = (0.8, 1.8, 4.0)
# 分源限速：东财可承受较高并发；新浪/腾讯约 2 req/s 才不会被限流
_SOURCE_INTERVAL = {"em": 0.15, "tx": 0.3, "sina": 0.5}
MA_CB_FAILS = 5             # 连续失败 N 次熔断该源
MA_CB_COOLDOWN = 120        # 熔断冷却秒数（半开试探）
# 东财 push2his 镜像（主源，洪峰期 IP 会被临时断连；逐主机轮换 + 退避重试）
_EM_HOSTS = (
    "push2his.eastmoney.com",
    "1.push2his.eastmoney.com",
    "7.push2his.eastmoney.com",
)
_RETRY_STATUS = {429, 500, 501, 502, 503, 504}

# ── 分源限速 + 数据源熔断（线程间共享，按 IP 限流时自动让位给备用源）──
_rate_lock = threading.Lock()
_last_request_at: dict[str, float] = {}
_cb_lock = threading.Lock()
_cb_state: dict[str, dict] = {}  # source -> {"streak": n, "open_until": ts}


def _pace(source: str = "em") -> None:
    """分源令牌间隔：把该源外发请求压到最小间隔以上（线程共享）。"""
    interval = _SOURCE_INTERVAL.get(source, 0.3)
    key = source
    while True:
        with _rate_lock:
            now = time.monotonic()
            last = _last_request_at.get(key, 0.0)
            wait = interval - (now - last)
            if wait <= 0:
                _last_request_at[key] = now
                return
        time.sleep(wait)


def _cb_allow(source: str) -> bool:
    """熔断半开：冷却期内直接跳过该源，冷却后放行一次试探。"""
    with _cb_lock:
        st = _cb_state.setdefault(
            source, {"streak": 0, "open_until": 0.0})
        return time.monotonic() >= st["open_until"]


def _cb_record(source: str, ok: bool) -> None:
    with _cb_lock:
        st = _cb_state.setdefault(
            source, {"streak": 0, "open_until": 0.0})
        if ok:
            st["streak"] = 0
            st["open_until"] = 0.0
        else:
            st["streak"] += 1
            if st["streak"] >= MA_CB_FAILS:
                st["open_until"] = time.monotonic() + MA_CB_COOLDOWN

_ma_lock = threading.Lock()
_ma_state = {
    "status": "idle",       # idle / syncing / failed
    "phase": "",
    "last_error": "",
    "done": 0,
    "total": 0,
    "started_at": "",
    "finished_at": "",
}
_tls = threading.local()


def _http_session() -> requests.Session:
    """每线程复用一个 Session。"""
    if not hasattr(_tls, "session"):
        s = requests.Session()
        s.headers.update(_UA)
        _tls.session = s
    return _tls.session


def _em_secid(symbol: str) -> str:
    """东财 secid：60/68 开头为沪市 1.，其余（含北交所）为 0.。"""
    return ("1." if symbol.startswith(("60", "68")) else "0.") + symbol


def _parse_em_klines(payload: dict) -> list[tuple[str, float]]:
    """解析东财 kline JSON → [(date, close)]。纯函数，便于测试。"""
    klines = ((payload or {}).get("data") or {}).get("klines") or []
    out = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        close = _to_num(parts[2])
        if close:
            out.append((parts[0], close))
    return out


def _parse_tx_klines(payload: dict, code: str) -> list[tuple[str, float]]:
    """解析腾讯 fqkline JSON → [(date, close)]（qfqday/day 两种键）。纯函数。"""
    node = ((payload or {}).get("data") or {}).get(code) or {}
    raw = node.get("qfqday") or node.get("day") or []
    out = []
    for parts in raw:
        if len(parts) < 3:
            continue
        close = _to_num(parts[2])
        if close:
            out.append((parts[0], close))
    return out


def _http_get_json(session: requests.Session, url: str,
                   tries: int = MA_RETRY, source: str = "em") -> dict:
    """GET 并解析 JSON；连接错误/超时/限流状态码按退避重试（按源限速）。

    东财洪峰期会直接断连（RemoteDisconnected）或间歇性 5xx，
    立即重试通常仍失败，故退避递增。
    """
    last_err: Exception | None = None
    for i in range(tries):
        try:
            _pace(source)
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code in _RETRY_STATUS:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout,
                requests.HTTPError, json.JSONDecodeError) as e:
            last_err = e
            if i < tries - 1:
                time.sleep(MA_RETRY_PAUSE[min(i, len(MA_RETRY_PAUSE) - 1)])
    raise last_err if last_err else RuntimeError("请求失败")


def _em_kline_url(host: str, secid: str, lmt: int) -> str:
    return (
        f"https://{host}/api/qt/stock/kline/get?"
        f"secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&klt=101&fqt=1&end=20500101&lmt={lmt}"
    )


def _fetch_kline_em(session: requests.Session, secid: str,
                    lmt: int = MA_KLINE_LMT) -> list[tuple[str, float]]:
    """东财日K：镜像主机轮换，每台主机内部退避重试。

    熔断冷却期/所有镜像均不可用时抛 RuntimeError（让调用方立刻转备用源）；
    主机正常响应但无数据（代码无效）返回 []。
    """
    responded = False
    for host in _EM_HOSTS:
        if not _cb_allow("em"):
            continue
        try:
            payload = _http_get_json(
                session, _em_kline_url(host, secid, lmt), tries=2,
                source="em")
            _cb_record("em", True)
            responded = True
            rows = _parse_em_klines(payload)
            if rows:
                return rows
        except Exception:  # noqa: BLE001 - 换下一台镜像
            _cb_record("em", False)
            continue
    if responded:
        return []  # 服务正常，仅此标的无K线
    raise RuntimeError("东财K线源不可用（熔断/限流中）")


def _tx_code(symbol: str) -> str | None:
    """腾讯行情代码（仅沪深，腾讯无北交所日K）。"""
    if symbol.startswith(("60", "68", "90")):  # 90=沪B（名单内一般没有）
        return "sh" + symbol
    if symbol.startswith(("00", "20", "30")):  # 20=深B
        return "sz" + symbol
    return None


def _sina_code(symbol: str) -> str | None:
    """新浪行情代码（沪深 + 北交所 bj 前缀）。"""
    tx = _tx_code(symbol)
    if tx:
        return tx
    if symbol[:1] in ("4", "8", "9"):  # 北交所 4xx/8xx/920
        return "bj" + symbol
    return None


def _fetch_kline_tx(session: requests.Session, code: str,
                    lmt: int = MA_KLINE_LMT) -> list[tuple[str, float]]:
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={code},day,,,{lmt},qfq"
    )
    payload = _http_get_json(session, url, tries=1, source="tx")
    _cb_record("tx", True)
    return _parse_tx_klines(payload, code)


def _parse_sina_klines(payload: list[dict]) -> list[tuple[str, float]]:
    """解析新浪 getKLineData JSON 数组 → [(date, close)]。纯函数。"""
    out = []
    for it in payload or []:
        close = _to_num(str(it.get("close", "")))
        day = it.get("day") or it.get("date")
        if day and close:
            out.append((day, close))
    return out


def _fetch_kline_sina(session: requests.Session, code: str,
                      lmt: int = MA_KLINE_LMT) -> list[tuple[str, float]]:
    """新浪不复权日K（沪深+北交所，兜底源；均线对小幅除权偏差不敏感）。"""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={lmt}"
    )
    payload = _http_get_json(session, url, tries=2, source="sina")
    _cb_record("sina", True)
    return _parse_sina_klines(payload)


def fetch_symbol_klines_ex(symbol: str) -> tuple[list[tuple[str, float]], str]:
    """日K兜底链：东财(前复权,全市场) → 腾讯(前复权,沪深) → 新浪(不复权,含北交所)。

    返回 (klines, source)；source ∈ em/tx/sina；全失败为 ([], "")。
    熔断中的源直接跳过，不在单只标的上烧重试等待。
    """
    session = _http_session()
    try:
        rows = _fetch_kline_em(session, _em_secid(symbol))
        if rows:
            return rows, "em"
    except Exception:  # noqa: BLE001
        pass
    tx = _tx_code(symbol)
    if tx and _cb_allow("tx"):
        try:
            rows = _fetch_kline_tx(session, tx)
            if rows:
                return rows, "tx"
        except Exception:  # noqa: BLE001
            _cb_record("tx", False)
    sc = _sina_code(symbol)
    if sc and _cb_allow("sina"):
        try:
            rows = _fetch_kline_sina(session, sc)
            if rows:
                return rows, "sina"
        except Exception:  # noqa: BLE001
            _cb_record("sina", False)
    return [], ""


def fetch_symbol_klines(symbol: str) -> list[tuple[str, float]]:
    """单只股票日K（不带来源标记，兼容旧调用/测试）。"""
    return fetch_symbol_klines_ex(symbol)[0]


def compute_ma_snapshot(symbol: str, klines: list[tuple[str, float]]) -> dict | None:
    """日K (date, close) → stock_ma 行。上市不足 144 个交易日返回 None。

    high20：不含当日的最近 20 个交易日最高收盘，用于"回踩"判定。
    """
    closes = [c for _, c in klines if c]
    n = len(closes)
    if n < 144:
        return None
    return {
        "symbol": symbol,
        "trade_date": klines[-1][0],
        "close": round(closes[-1], 4),
        "high20": round(max(closes[-21:-1]), 4) if n >= 21 else None,
        "bars": n,
        "ma144": round(sum(closes[-144:]) / 144, 4),
        "ma288": round(sum(closes[-288:]) / 288, 4) if n >= 288 else None,
    }


def near_ma(price, ma, high20, tol: float) -> tuple[bool, float | None]:
    """是否"下跌至均线附近"。

    判定（两条同时成立）：
    1. 附近：|price/ma - 1| <= tol（默认 3%）；
    2. 回踩：此前 20 个交易日内最高收盘曾站上均线上沿 ma*(1+tol)，
       即股价是从上方跌回均线，而非一直在线下徘徊。

    返回 (是否命中, 距离比例 price/ma-1)；缺数据返回 (False, None)。
    """
    if not price or not ma or price <= 0:
        return False, None
    dist = price / ma - 1.0
    if abs(dist) > tol + 1e-9:  # 容差边界含等号（消除浮点误差）
        return False, round(dist, 4)
    if not high20 or high20 <= ma * (1 + tol):
        return False, round(dist, 4)
    return True, round(dist, 4)


def _get_meta(meta_key: str) -> str | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM sync_meta WHERE meta_key=%s", (meta_key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_ma_state() -> dict:
    """均线同步状态（内存态 + 库内元信息）。"""
    with _ma_lock:
        st = dict(_ma_state)
    out = {
        "ma_status": st["status"],
        "ma_phase": st["phase"],
        "ma_last_error": st["last_error"],
        "ma_done": st["done"],
        "ma_total": st["total"],
        "ma_trade_date": _get_meta("ma_trade_date"),
        "ma_updated_at": _get_meta("ma_updated_at"),
    }
    out["ma_count"] = get_ma_count()
    return out


def get_ma_count() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM stock_ma").fetchone()[0]
    except pymysql.MySQLError:
        return 0
    finally:
        conn.close()


def _latest_trade_date(session: requests.Session) -> str | None:
    """取上证指数最新交易日，作为"今日是否已同步"的判断基准。"""
    try:
        rows = _fetch_kline_em(session, "1.000001", lmt=2)
        return rows[-1][0] if rows else None
    except Exception:  # noqa: BLE001
        return None


def _universe_symbols(conn) -> list[str]:
    """均线同步标的：快照表优先，空则降级名称表。"""
    try:
        rows = conn.execute("SELECT symbol FROM stock_spot ORDER BY symbol").fetchall()
        if rows:
            return [r[0] for r in rows]
    except pymysql.MySQLError:
        pass
    rows = conn.execute("SELECT symbol FROM stock_names ORDER BY symbol").fetchall()
    return [r[0] for r in rows]


def sync_ma(force: bool = False) -> dict:
    """并发拉取全市场日K并落均线结果（幂等：进行中直接返回）。"""
    with _ma_lock:
        if _ma_state["status"] == "syncing":
            return {"ok": False, "message": "均线同步进行中"}
        _ma_state.update(status="syncing", phase="准备中", last_error="",
                         done=0, total=0,
                         started_at=datetime.now().strftime("%H:%M:%S"), finished_at="")

    try:
        session = _http_session()
        trade_date = _latest_trade_date(session)
        if not trade_date:
            # 东财被限流时，降级用上次成功的交易日，保证增量逻辑仍成立
            trade_date = _get_meta("ma_trade_date")

        conn = get_conn()
        try:
            ensure_tables(conn)
            symbols = _universe_symbols(conn)
            # 增量：已是目标交易日的行不再重拉（限流期可安全中断后续跑）
            existing = dict(conn.execute(
                "SELECT symbol, trade_date FROM stock_ma").fetchall())
        finally:
            conn.close()
        if not symbols:
            raise RuntimeError("股票标的为空，请先同步全市场快照")

        # force=True 强制全量重拉（用于备用源数据归一为东财前复权）
        if force:
            todo = list(symbols)
        else:
            todo = [s for s in symbols
                    if not trade_date or existing.get(s) != trade_date]
        skipped_have = len(symbols) - len(todo)

        # 已是目标交易日且覆盖率 >=99% 才视为"今日已完成"（补缺失标的不误跳过）
        if not force and trade_date and _get_meta("ma_trade_date") == trade_date:
            coverage = 1 - len(todo) / len(symbols)
            if coverage >= 0.99:
                with _ma_lock:
                    _ma_state.update(status="idle", phase="",
                                     finished_at=datetime.now().strftime("%H:%M:%S"))
                return {"ok": True, "skipped": True,
                        "message": f"均线数据已为最新（{trade_date}，{len(symbols)}只）"}

        def _run_round(targets, phase, workers):
            """并发拉取一轮，返回 (新均线行, 仍失败标的)。"""
            records, failed_syms = [], []
            with _ma_lock:
                _ma_state.update(done=0, total=len(targets))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fetch_symbol_klines_ex, s): s
                           for s in targets}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        klines, source = fut.result()
                        snap = compute_ma_snapshot(sym, klines)
                        if snap:
                            snap["source"] = source or "em"
                            records.append(snap)
                        else:
                            failed_syms.append(sym)  # 次新股 <144 根，不算错误
                    except Exception:  # noqa: BLE001
                        failed_syms.append(sym)
                    with _ma_lock:
                        _ma_state["done"] += 1
                        n = _ma_state["done"]
                        if n % 100 == 0:
                            _ma_state["phase"] = f"{phase} {n}/{_ma_state['total']}"
            return records, failed_syms

        with _ma_lock:
            _ma_state.update(phase="拉取日K中", total=len(symbols),
                             done=skipped_have)

        records, failed_syms = _run_round(todo, "拉取日K中", MA_WORKERS)

        def _persist(batch, mark_meta: bool):
            """把一轮结果落库（逐轮持久化，限流中断也不丢进度）。"""
            if not batch:
                return
            with _ma_lock:
                _ma_state.update(phase=f"写库中({len(batch)}只)")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            eff = trade_date or max(r["trade_date"] for r in batch)
            conn = get_conn()
            try:
                ensure_tables(conn)
                with conn:
                    conn.executemany(
                        "REPLACE INTO stock_ma "
                        "(symbol, trade_date, close, high20, bars, ma144, ma288, "
                        "updated_at, source) "
                        "VALUES (%(symbol)s, %(trade_date)s, %(close)s, %(high20)s, %(bars)s, "
                        "%(ma144)s, %(ma288)s, %(updated_at)s, %(source)s)",
                        [{**r, "updated_at": now} for r in batch],
                    )
                    if mark_meta:
                        conn.execute(
                            "REPLACE INTO sync_meta (meta_key, value) "
                            "VALUES ('ma_trade_date', %s)", (eff,))
                        conn.execute(
                            "REPLACE INTO sync_meta (meta_key, value) "
                            "VALUES ('ma_updated_at', %s)", (now,))
            finally:
                conn.close()

        # 首轮结果先落库（含交易日 meta，扫描即可用；覆盖不全时下次自动补）
        _persist(records, bool(records))

        # 限流导致的失败：长冷却后降速补拉三轮（东财单 IP 配额窗口约数分钟，
        # 次新股/退市无数据会自然残留；逐轮落库，中途无新增即收尾）
        for rnd, wait_s in ((1, 120), (2, 300), (3, 600)):
            got = {r["symbol"] for r in records}
            still_missing = [s for s in failed_syms if s not in got]
            if not still_missing:
                break
            with _ma_lock:
                _ma_state["phase"] = (
                    f"冷却{wait_s}s后补拉重试（{len(still_missing)}只）")
            time.sleep(wait_s)
            more, failed_syms = _run_round(
                still_missing, f"补拉重试{rnd}轮", max(2, MA_WORKERS // 2))
            records.extend(more)
            _persist(more, False)

        if not records:
            raise RuntimeError("未获取到任何有效日K（数据源全部失败/限流中，请稍后重试）")

        effective_date = trade_date or max(r["trade_date"] for r in records)
        total_have = get_ma_count()
        with _ma_lock:
            _ma_state.update(status="idle", phase="", done=total_have,
                             finished_at=datetime.now().strftime("%H:%M:%S"))
        msg = f"均线同步完成：累计{total_have}只（交易日 {effective_date}）"
        if failed_syms:
            msg += f"，{len(failed_syms)}只无有效日K（次新股/退市/源失败，可再点更新补齐）"
        return {"ok": True, "message": msg, "count": total_have,
                "trade_date": effective_date,
                "updated": len(records), "failed": len(failed_syms)}

    except Exception as e:  # noqa: BLE001
        with _ma_lock:
            _ma_state.update(status="failed", phase="", last_error=str(e),
                             finished_at=datetime.now().strftime("%H:%M:%S"))
        return {"ok": False, "message": f"均线同步失败：{e}"}


def sync_ma_async(force: bool = False) -> dict:
    """后台线程触发均线同步。"""
    with _ma_lock:
        running = _ma_state["status"] == "syncing"
    if running:
        return {"ok": False, "message": "均线同步进行中"}
    threading.Thread(target=sync_ma, kwargs={"force": force}, daemon=True).start()
    return {"ok": True, "message": "已启动均线后台同步"}


# ── 同步状态机 ──────────────────────────────────────────────


def _write_rows(rows: list[tuple], source: str) -> None:
    conn = get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM stock_spot")
            conn.executemany(
                "REPLACE INTO stock_spot "
                "(symbol, name, price, change_pct, market, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )
            # 快照同时维护名称库（独立部署后不再依赖 baostock / 父项目数据库）
            conn.executemany(
                "REPLACE INTO stock_names (symbol, name) VALUES (%s, %s)",
                [(r[0], r[1]) for r in rows if r[1]],
            )
            conn.execute(
                "REPLACE INTO sync_meta (meta_key, value) VALUES ('last_success_date', %s)",
                (datetime.now().strftime("%Y-%m-%d"),),
            )
            conn.execute(
                "REPLACE INTO sync_meta (meta_key, value) VALUES ('last_source', %s)",
                (source,),
            )
    finally:
        conn.close()


def _fetch_snapshot() -> tuple[list[tuple], str]:
    """多源 fallback 链：东财delay → 腾讯 → 新浪。

    返回 (rows, source_name)。全部失败抛出最后异常。
    """
    conn = get_conn()
    try:
        sources = [
            ("eastmoney", lambda: _fetch_eastmoney()),
            ("tencent", lambda: _fetch_tencent(conn)),
            ("sina", _fetch_sina),
        ]
    finally:
        conn.close()

    last_err = None
    for source_name, fetch in sources:
        for attempt in range(MAX_RETRIES_PER_SOURCE + 1):
            try:
                rows = fetch()
                if rows:
                    return rows, source_name
                last_err = RuntimeError(f"{source_name} 源返回空数据")
            except Exception as e:  # noqa: BLE001 - 网络源需逐个兜底
                last_err = e
            if attempt < MAX_RETRIES_PER_SOURCE:
                time.sleep(RETRY_INTERVAL)
        with _lock:
            _state["phase"] = f"{source_name} 源不可用，切换下一源"
    raise last_err or RuntimeError("所有数据源均失败")


def sync_spot() -> dict:
    """同步全市场快照（幂等：进行中直接返回）。"""
    with _lock:
        if _state["status"] == "syncing":
            return {"ok": False, "message": "同步进行中"}
        _state.update(status="syncing", phase="拉取中", last_error="",
                      started_at=datetime.now().strftime("%H:%M:%S"), finished_at="")

    try:
        rows, source = _fetch_snapshot()

        with _lock:
            _state["phase"] = f"写库中({len(rows)}只)"
        _write_rows(rows, source)
        with _lock:
            _state.update(status="idle", phase="", finished_at=datetime.now().strftime("%H:%M:%S"))
        return {"ok": True, "message": f"同步成功({source})，共{len(rows)}只"}

    except Exception as e:  # noqa: BLE001
        with _lock:
            _state.update(status="failed", phase="", last_error=str(e),
                          finished_at=datetime.now().strftime("%H:%M:%S"))
        return {"ok": False, "message": f"同步失败：{e}"}


def sync_spot_async() -> dict:
    """后台线程触发同步（启动时自动检测 / 手动按钮共用）。"""
    st = get_state()
    if st["status"] == "syncing":
        return {"ok": False, "message": "同步进行中"}
    if st["today_synced"]:
        return {"ok": True, "message": "今日快照已是最新", "skipped": True}

    threading.Thread(target=sync_spot, daemon=True).start()
    return {"ok": True, "message": "已启动后台同步"}
