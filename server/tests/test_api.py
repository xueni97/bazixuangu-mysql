"""八字选股后端测试（MySQL 版）。

运行:
  # 先在 .env 或环境变量中配置好 DB_HOST/DB_USER/DB_PASSWORD
  cd bazi-stock-app
  python -m pytest server/tests -v

测试使用独立库 bazixuangu_test（可用环境变量 TEST_DB_NAME 覆盖），
本机没有可用 MySQL 时，需要连库的用例会自动 skip。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

# 必须在 import db / data_sync 之前指定测试库
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "bazixuangu_test")

import data_sync  # noqa: E402
import db  # noqa: E402


# ── 测试数据与辅助 ─────────────────────────────────────────


NAMES = [
    ("600519", "贵州茅台"),
    ("000001", "平安银行"),
    ("300750", "宁德时代"),
    ("688981", "中芯国际"),
    ("832000", "测试北交"),
]


def _seed_names(rows=NAMES):
    conn = db.get_conn()
    try:
        with conn:
            conn.executemany(
                "REPLACE INTO stock_names (symbol, name, industry) VALUES (%s, %s, '')",
                rows,
            )
    finally:
        conn.close()


def _seed_spot(rows):
    conn = db.get_conn()
    try:
        with conn:
            conn.executemany(
                "REPLACE INTO stock_spot "
                "(symbol, name, price, change_pct, market, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )
    finally:
        conn.close()


@pytest.fixture()
def db_ready():
    """确保 MySQL 测试库可用，不可用则跳过本用例。"""
    try:
        db.init_db(retries=1, delay=1)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL 不可用：{exc}")

    conn = db.get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM stock_spot")
            conn.execute("DELETE FROM stock_ma")
            conn.execute("DELETE FROM stock_names")
            conn.execute("DELETE FROM sync_meta")
    finally:
        conn.close()

    # 重置内存状态机
    data_sync._state.update(
        status="idle", phase="", last_error="", started_at="", finished_at="")
    data_sync._ma_state.update(
        status="idle", phase="", last_error="", done=0, total=0,
        started_at="", finished_at="")

    _seed_names()
    yield


@pytest.fixture()
def client(db_ready):
    import app as app_module

    app_module._sectors_cache.update(date="", source="", data={})
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ── data_sync 纯函数单元（不连库） ──────────────────────────


def test_classify_market():
    assert data_sync.classify_market("600519") == "沪"
    assert data_sync.classify_market("688981") == "沪"
    assert data_sync.classify_market("000001") == "深"
    assert data_sync.classify_market("300750") == "深"
    assert data_sync.classify_market("832000") == "北交所"


def test_parse_eastmoney_payload():
    """东财 delay JSON 解析：跳过空代码，'-' 价格 → None。"""
    payload = {
        "data": {
            "total": 2,
            "diff": [
                {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f2": 1500.0, "f3": 2.5},
                {"f12": "000001", "f13": 0, "f14": "平安银行", "f2": "-", "f3": "-"},
            ],
        }
    }
    rows = data_sync._parse_em_payload(payload)
    assert rows[0] == ("600519", "贵州茅台", 1500.0, 2.5, "沪", rows[0][5])
    assert rows[1][2] is None and rows[1][3] is None


def test_parse_tencent_text():
    """腾讯批量响应解析：bj 前缀归属北交所，市场字段正确。"""
    text = (
        'v_sh600519="1~贵州茅台~600519~1275.16~1285.13~1285.15~a~b~c~d~e~f~g~h~i~j~k~l~m~n~o~'
        "p~q~r~s~t~u~v~w~x~y~z~aa~20260912160000~0.55~2.51~1290.00~1270.00"
        '~9.80~44308~4430841~1.10~20.5~0~0~0~0~0~0~0~0~0~0";'
        'v_bj833533="1~骏创科技~833533~12.86~12.86~12.86~a~b~c~d~e~f~g~h~i~j~k~l~m~n~o~'
        "p~q~r~s~t~u~v~w~x~y~z~aa~20260912160000~0.00~0.00~13.00~12.50"
        '~9.80~443~4430~1.10~20.5~0~0~0~0~0~0~0~0~0~0";'
    )
    rows = data_sync._parse_tencent_text(text)
    assert len(rows) == 2
    assert rows[0][0] == "600519" and rows[0][4] == "沪"
    assert rows[0][2] == 1275.16
    assert rows[1][0] == "833533" and rows[1][4] == "北交所"


def test_parse_sina_list():
    """新浪列表解析：bj920 段归属北交所。"""
    items = [
        {"symbol": "bj920000", "name": "安徽凤凰", "trade": "13.550", "changepercent": "-2.448"},
        {"symbol": "sz000001", "name": "平安银行", "trade": "12.00", "changepercent": "1.0"},
        {"symbol": "bad", "name": "坏行", "trade": "1", "changepercent": "0"},
    ]
    rows = data_sync._parse_sina_list(items)
    assert len(rows) == 2
    assert rows[0][0] == "920000" and rows[0][4] == "北交所"
    assert rows[1][2] == 12.0


def test_fetch_chain_fallback(db_ready, monkeypatch):
    """东财与腾讯都失败时，自动切到新浪源成功。"""
    def em_fail():
        raise RuntimeError("em down")

    def tx_fail():
        raise RuntimeError("tx down")

    sina_rows = [("600519", "贵州茅台", 10.0, 1.0, "沪", "t")]
    monkeypatch.setattr(data_sync, "_fetch_eastmoney", em_fail)
    monkeypatch.setattr(data_sync, "_fetch_tencent", tx_fail)
    monkeypatch.setattr(data_sync, "_fetch_sina", lambda: sina_rows)
    monkeypatch.setattr(data_sync.time, "sleep", lambda s: None)

    rows, source = data_sync._fetch_snapshot()
    assert source == "sina"
    assert rows == sina_rows


def test_sync_state_machine(db_ready, monkeypatch):
    """成功路径: idle → syncing → idle，且记录 last_success 与来源。"""
    good_rows = [("600519", "贵州茅台", 10.0, 1.0, "沪", "2026-09-12 10:00:00")]
    monkeypatch.setattr(
        data_sync, "_fetch_snapshot", lambda: (good_rows, "eastmoney")
    )

    result = data_sync.sync_spot()
    assert result["ok"] is True
    assert "eastmoney" in result["message"]
    assert data_sync.get_state()["status"] == "idle"
    assert data_sync.get_last_success() is not None
    assert data_sync.get_spot_count() == 1


def test_sync_failure_keeps_state(db_ready, monkeypatch):
    """所有数据源持续失败: 状态 failed，不写库。"""
    def boom():
        raise RuntimeError("网络超时")

    monkeypatch.setattr(data_sync, "_fetch_snapshot", boom)
    monkeypatch.setattr(data_sync.time, "sleep", lambda s: None)

    result = data_sync.sync_spot()
    assert result["ok"] is False
    st = data_sync.get_state()
    assert st["status"] == "failed"
    assert "网络超时" in st["last_error"]
    assert data_sync.get_spot_count() == 0
    assert data_sync.get_last_success() is None


def test_sync_idempotent_when_syncing(db_ready, monkeypatch):
    """同步进行中再次触发 → 拒绝。"""
    import threading

    release = threading.Event()

    def slow_fetch():
        release.wait(timeout=5)
        return []

    monkeypatch.setattr(data_sync, "_fetch_snapshot", slow_fetch)
    t = threading.Thread(target=data_sync.sync_spot, daemon=True)
    t.start()
    try:
        import time
        time.sleep(0.2)  # 等线程进入 syncing
        result = data_sync.sync_spot()
        assert result["ok"] is False
        assert "进行中" in result["message"]
    finally:
        release.set()
        t.join(timeout=5)


# ── API 契约 ────────────────────────────────────────────────


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"


def test_scan_fallback_to_names(client):
    """无快照时降级 stock_names，dataSource=names，price 为 null。"""
    resp = client.get("/api/scan?min_score=-100&limit=10")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["dataSource"] == "names"
    assert body["totalScanned"] == 5
    assert all(r["price"] is None for r in body["results"])
    # JSON 输出为 camelCase
    for key in ("dayMaster", "useGods", "avoidGods", "totalScanned"):
        assert key in body


def test_scan_with_spot(client):
    """有快照时使用全市场 universe，结果带价格。"""
    _seed_spot([
        ("600519", "贵州茅台", 1500.0, 2.5, "沪", "2026-09-12"),
        ("000001", "平安银行", 12.0, -1.0, "深", "2026-09-12"),
    ])

    resp = client.get("/api/scan?min_score=-100&limit=10")
    body = resp.get_json()
    assert body["dataSource"] == "spot"
    assert body["totalScanned"] == 2
    by_symbol = {r["symbol"]: r for r in body["results"]}
    assert by_symbol["600519"]["price"] == 1500.0
    assert by_symbol["600519"]["changePct"] == 2.5


def test_search(client):
    resp = client.get("/api/search?q=茅台")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body[0]["symbol"] == "600519"
    assert body[0]["price"] is None  # 无快照降级

    _seed_spot([("600519", "贵州茅台", 1500.0, 2.5, "沪", "2026-09-12")])
    body = client.get("/api/search?q=600519").get_json()
    assert body[0]["price"] == 1500.0


def test_sectors_counts_all(client):
    resp = client.get("/api/sectors")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"木", "火", "土", "金", "水", "未知"}
    assert sum(body.values()) == 5  # 全 universe 计数


def test_scan_multi_period_meta_and_scores(client):
    """勾选月/周/日：返回三个周期元信息，每只股票带分项分，综合分在[-100,100]。"""
    resp = client.get("/api/scan?min_score=-100&limit=10&periods=monthly,weekly,daily")
    body = resp.get_json()
    keys = [p["key"] for p in body["periods"]]
    assert keys == ["monthly", "weekly", "daily"]
    weights = [p["weight"] for p in body["periods"]]
    assert weights == [0.5, 0.3, 0.2]
    for r in body["results"]:
        assert set(r["periodScores"].keys()) == {"monthly", "weekly", "daily"}
        assert -100 <= r["score"] <= 100


def test_scan_period_default_and_subset(client):
    """默认仅日周期；周+日子集不包含月。"""
    body = client.get("/api/scan?min_score=-100").get_json()
    assert [p["key"] for p in body["periods"]] == ["daily"]
    body2 = client.get("/api/scan?min_score=-100&periods=weekly,daily,xxx").get_json()
    assert [p["key"] for p in body2["periods"]] == ["weekly", "daily"]


def test_scan_element_filter(client):
    """五行筛选：命中结果的属性必须全部属于勾选集合。"""
    resp = client.get("/api/scan?min_score=-100&periods=monthly,weekly,daily&elements=火,水")
    body = resp.get_json()
    assert body["results"], "当前盘面下火/水至少应有命中（阈值-100）"
    assert all(r["element"] in {"火", "水"} for r in body["results"])


def test_scan_market_and_price_filter(client):
    """市场与价格区间硬过滤。"""
    _seed_spot([
        ("600519", "贵州茅台", 1500.0, 2.5, "沪", "2026-09-14"),
        ("000001", "平安银行", 12.0, -1.0, "深", "2026-09-14"),
        ("832000", "测试北交", 5.0, 0.0, "北交所", "2026-09-14"),
    ])

    body = client.get("/api/scan?min_score=-100&markets=沪").get_json()
    assert {r["symbol"] for r in body["results"]} <= {"600519"}

    body = client.get("/api/scan?min_score=-100&markets=深,北交所").get_json()
    assert {r["symbol"] for r in body["results"]} <= {"000001", "832000"}

    body = client.get("/api/scan?min_score=-100&min_price=10&max_price=100").get_json()
    assert {r["symbol"] for r in body["results"]} <= {"000001"}

    body = client.get("/api/scan?min_score=-100&min_price=1000").get_json()
    assert {r["symbol"] for r in body["results"]} <= {"600519"}


def test_composite_score_weighted_and_resonance():
    """综合评分 = 归一化加权 + 同向共振封顶；单周期退化为原日评分。"""
    from datetime import datetime
    from metaphysics import YuanhaiDecisionModel

    pd = YuanhaiDecisionModel.period_analyses(datetime(2026, 9, 14, 10))
    daily_only = YuanhaiDecisionModel.composite_score("火", pd, ["daily"], stock_name="测试")
    daily_raw = YuanhaiDecisionModel.stock_score(
        "火", pd["daily"]["analysis"], stock_name="测试")
    assert daily_only["score"] == daily_raw["score"]
    assert "periodScores" in daily_only

    full = YuanhaiDecisionModel.composite_score(
        "火", pd, ["monthly", "weekly", "daily"], stock_name="测试")
    raws = [v["score"] for v in full["periodScores"].values()]
    weighted = round(0.5 * raws[0] + 0.3 * raws[1] + 0.2 * raws[2])
    expected = min(100, weighted + (15 if min(raws) >= 45 else 8 if min(raws) >= 15 else 0))
    assert full["score"] == expected


# ── 144/288 均线指标 ────────────────────────────────────────


def test_parse_em_klines():
    """东财K线字符串解析：date,open,close 取第3列，'-'/坏行跳过。"""
    payload = {"data": {"klines": [
        "2026-09-10,10.00,10.50,11.00,9.50,100,1000,0.5",
        "bad,1",
        "2026-09-11,10.50,-,11,10,200,2000,0",
    ]}}
    rows = data_sync._parse_em_klines(payload)
    assert rows == [("2026-09-10", 10.5)]


def test_parse_tx_klines():
    """腾讯 qfqday/day 两种键都能解析。"""
    payload = {"data": {"sh600519": {"qfqday": [
        ["2026-09-10", "10.00", "10.50", "11.00", "9.50", "100"],
    ]}}}
    assert data_sync._parse_tx_klines(payload, "sh600519") == [("2026-09-10", 10.5)]
    payload2 = {"data": {"sz000001": {"day": [["2026-09-11", "12", "12.1", "12.2", "11.9", "0"]]}}}
    assert data_sync._parse_tx_klines(payload2, "sz000001") == [("2026-09-11", 12.1)]


def test_parse_sina_klines():
    """新浪 getKLineData 数组解析。"""
    payload = [
        {"day": "2026-09-10", "open": "10.0", "high": "11", "low": "9.5",
         "close": "10.50", "volume": "100"},
        {"day": "2026-09-11", "open": "10.5", "high": "10.8", "low": "10.2",
         "close": "10.7", "volume": "80"},
    ]
    assert data_sync._parse_sina_klines(payload) == [
        ("2026-09-10", 10.5), ("2026-09-11", 10.7)]
    assert data_sync._parse_sina_klines([]) == []


def test_compute_ma_snapshot():
    """上市不足144日→None；200日有ma144无ma288；300日均线与high20正确。"""
    kl = lambda closes: [(f"d{i:03d}", c) for i, c in enumerate(closes)]

    assert data_sync.compute_ma_snapshot("x", kl([10.0] * 100)) is None

    snap200 = data_sync.compute_ma_snapshot("x", kl([10.0] * 200))
    assert snap200["ma144"] == 10.0 and snap200["ma288"] is None
    assert snap200["high20"] == 10.0 and snap200["bars"] == 200

    closes = [10.0] * 280 + [12.0] * 19 + [10.0]  # 共300根
    snap = data_sync.compute_ma_snapshot("x", kl(closes))
    assert snap["ma288"] == round((269 * 10 + 19 * 12) / 288, 4)
    # 不含当日的最近20根：1根10 + 19根12 → 最高12
    assert snap["high20"] == 12.0
    assert snap["close"] == 10.0 and snap["trade_date"] == "d299"


def test_near_ma():
    """回踩均线：带内容差 + 此前20日曾站上上沿，两条件同时满足。"""
    hit, dist = data_sync.near_ma(10.0, 10.0, 10.5, 0.03)
    assert hit and dist == 0.0
    # 贴着均线但此前一直在线附近徘徊（无回踩）→ 不命中
    assert data_sync.near_ma(10.0, 10.0, 10.2, 0.03)[0] is False
    # 偏离超过容差 → 不命中，但返回距离
    hit, dist = data_sync.near_ma(10.5, 10.0, 10.5, 0.03)
    assert hit is False and dist == 0.05
    # -3% 边界（含）仍算附近
    assert data_sync.near_ma(9.7, 10.0, 10.5, 0.03)[0] is True
    # 缺数据
    assert data_sync.near_ma(None, 10.0, 10.5, 0.03) == (False, None)
    assert data_sync.near_ma(10.0, None, 10.5, 0.03) == (False, None)


def test_scan_ma_requires_sync(client):
    """勾选均线但 stock_ma 从未同步 → 400 并提示先同步。"""
    resp = client.get("/api/scan?min_score=-100&ma=144")
    assert resp.status_code == 400
    assert "均线" in resp.get_json()["error"]


def test_scan_ma_filter(client):
    """ma=144 仅保留回踩144日线的标的；结果带均线与距离字段。"""
    _seed_spot([
        ("600519", "贵州茅台", 10.0, 0.0, "沪", "2026-09-12"),
        ("000001", "平安银行", 10.0, 0.0, "深", "2026-09-12"),
    ])
    conn = db.get_conn()
    try:
        with conn:
            conn.execute(
                "REPLACE INTO sync_meta (meta_key, value) VALUES ('ma_trade_date', '2026-09-11')")
            conn.executemany(
                "REPLACE INTO stock_ma (symbol, trade_date, close, high20, bars, ma144, ma288, "
                "updated_at, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [("600519", "2026-09-11", 10.0, 11.0, 300, 10.0, 9.9, "t", "em"),  # 回踩
                 ("000001", "2026-09-11", 10.0, 12.5, 300, 12.0, 12.0, "t", "em")],  # 偏离
            )
    finally:
        conn.close()

    body = client.get("/api/scan?min_score=-100&ma=144").get_json()
    assert body["maFilter"] == [144] and body["maTradeDate"] == "2026-09-11"
    assert {r["symbol"] for r in body["results"]} == {"600519"}
    row = body["results"][0]
    assert row["ma144"] == 10.0 and row["dist144"] == 0.0
    assert row["ma288"] == 9.9

    # 双均线共振：288 数据缺失者同样被剔除
    body2 = client.get("/api/scan?min_score=-100&ma=144,288&ma_tol=0.05").get_json()
    assert {r["symbol"] for r in body2["results"]} == {"600519"}

    # 不勾选均线：均线字段仍随结果下发（供展示距离标签）
    body3 = client.get("/api/scan?min_score=-100").get_json()
    by = {r["symbol"]: r for r in body3["results"]}
    assert by["600519"]["dist144"] == 0.0
    assert by["000001"]["dist144"] == round(10 / 12 - 1, 4)


def test_sync_endpoints(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "sync_spot_async",
                        lambda: {"ok": True, "message": "已启动后台同步"})
    monkeypatch.setattr(app_module, "sync_ma_async",
                        lambda force=False: {"ok": True, "message": "已启动均线后台同步"})
    resp = client.post("/api/sync")
    assert resp.status_code == 200

    resp = client.post("/api/sync/ma")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True

    resp = client.get("/api/sync/status")
    body = resp.get_json()
    assert "status" in body and "last_success_date" in body
    for k in ("ma_status", "ma_phase", "ma_trade_date", "ma_count"):
        assert k in body


def test_sync_ma_pipeline(db_ready, monkeypatch):
    """均线同步状态机：并发拉取→计算→落 stock_ma + meta（全程不联网）。"""
    # 名称库 5 只标的，每只 300 根收盘 10 的日K（末日=目标交易日）
    klines = [(f"2025-{i // 30 + 1:02d}-{i % 28 + 1:02d}", 10.0) for i in range(300)]
    klines[-1] = ("2026-09-12", 10.0)
    monkeypatch.setattr(data_sync, "_latest_trade_date", lambda s: "2026-09-12")
    monkeypatch.setattr(data_sync, "fetch_symbol_klines_ex",
                        lambda sym: (list(klines), "em"))

    result = data_sync.sync_ma(force=True)
    assert result["ok"] is True and result["count"] == 5
    assert result["trade_date"] == "2026-09-12"

    st = data_sync.get_ma_state()
    assert st["ma_status"] == "idle" and st["ma_count"] == 5
    assert st["ma_trade_date"] == "2026-09-12"

    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT ma144, ma288, bars, source FROM stock_ma WHERE symbol='600519'"
        ).fetchone()
    finally:
        conn.close()
    assert row == (10.0, 10.0, 300, "em")

    # 同一交易日再跑 → 跳过
    again = data_sync.sync_ma()
    assert again.get("skipped") is True


def test_index_spa_fallback(client):
    """根路径与未知路径返回 index.html（hash 路由 SPA）。"""
    import app as app_module

    index_file = Path(app_module.app.static_folder) / "index.html"
    if not index_file.exists():
        pytest.skip("dist 未构建")
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"app" in resp.data
