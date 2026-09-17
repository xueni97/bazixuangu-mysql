"""渊海子平命理选股 Flask 后端（独立部署版 · MySQL 存储）。

职责：
1. 数据同步：全市场行情快照（data_sync.py，多数据源 fallback，启动自动检测 + 手动触发）
2. API：股票搜索 / 全市场命理扫描 / 同步状态 / 五行分布
3. 页面托管：serve dist/（电脑浏览器大屏入口，监听 0.0.0.0）

运行方式:
  cd bazi-stock-app
  pip install -r requirements.txt
  python server/app.py
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Flask, jsonify, request, send_from_directory

# server/ 在以 `python server/app.py` 启动时会自动加入 sys.path，
# 命理引擎已内置到 server/metaphysics/，不再依赖外部项目。
from metaphysics import (  # noqa: E402
    StockElementAnalyzer,
    YuanhaiDecisionModel,
)

from data_sync import (  # noqa: E402
    get_spot_count,
    get_state,
    near_ma,
    sync_ma_async,
    sync_spot_async,
)
from db import DB_NAME, get_conn, init_db  # noqa: E402

app = Flask(__name__, static_folder=str(Path(__file__).parent.parent / "dist"), static_url_path="")

# ── CORS（手机浏览器调试 / 局域网联用） ──────────────────────────


@app.after_request
def after_request(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ── API 蓝图 ────────────────────────────────────────────────

api = Blueprint("api", __name__, url_prefix="/api")

_sectors_cache: dict = {"date": "", "source": "", "data": {}}


def _json_err(message: str, code: int):
    return jsonify({"error": message}), code


def _get_universe(conn):
    """扫描 universe：优先全市场快照（含价格），降级全量名称表。

    返回 (rows, source)，rows 元素为 (symbol, name, price, change_pct, market)。
    """
    n = get_spot_count()
    if n > 0:
        rows = conn.execute(
            "SELECT symbol, name, price, change_pct, market FROM stock_spot ORDER BY symbol"
        ).fetchall()
        source = "spot"
    else:
        rows = conn.execute(
            "SELECT symbol, name, NULL, NULL, '' FROM stock_names ORDER BY symbol"
        ).fetchall()
        source = "names"
    return rows, source


@api.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "db": DB_NAME,
        "spot_count": get_spot_count(),
    })


@api.route("/stock-names")
def stock_names():
    """获取股票名称列表（全量）。"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT symbol, name FROM stock_names ORDER BY symbol").fetchall()
        return jsonify([{"symbol": r[0], "name": r[1]} for r in rows])
    finally:
        conn.close()


@api.route("/search")
def search():
    """搜索股票（按代码或名称，快照优先带价格）。"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    conn = get_conn()
    try:
        like = f"%{q}%"
        if get_spot_count() > 0:
            rows = conn.execute(
                "SELECT symbol, name, price, change_pct FROM stock_spot "
                "WHERE symbol LIKE %s OR name LIKE %s LIMIT 20",
                (like, like),
            ).fetchall()
            return jsonify([
                {"symbol": r[0], "name": r[1], "price": r[2], "changePct": r[3]}
                for r in rows
            ])
        rows = conn.execute(
            "SELECT symbol, name FROM stock_names WHERE symbol LIKE %s OR name LIKE %s LIMIT 20",
            (like, like),
        ).fetchall()
        return jsonify([{"symbol": r[0], "name": r[1], "price": None, "changePct": None}
                        for r in rows])
    finally:
        conn.close()


@api.route("/scan")
def scan():
    """全市场命理扫描，按多周期综合评分排序。

    参数:
      year/month/day/hour（默认当前）
      periods: 逗号分隔，可选 monthly,weekly,daily（默认 daily）
      elements: 逗号分隔五行筛选（木火土金水，空=全部）
      markets: 逗号分隔市场筛选（沪,深,北交所，空=全部）
      min_price/max_price: 价格区间（仅快照有价格时生效）
      ma: 逗号分隔均线条件，可选 144,288（回踩至均线附近，多选为同时满足）
      ma_tol: 均线附近容差（默认0.03=±3%，范围0.5%~10%）
      min_score（默认10）、limit（默认100）
    """
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    day = int(request.args.get("day", now.day))
    hour = int(request.args.get("hour", now.hour))
    min_score = int(request.args.get("min_score", 10))
    limit = int(request.args.get("limit", 100))

    # 周期勾选（保持月→周→日顺序）
    raw_periods = request.args.get("periods", "daily")
    selected = [p for p in ("monthly", "weekly", "daily")
                if p in raw_periods.split(",")] or ["daily"]

    # 均线附加指标勾选（144/288 日线，多选为"同时回踩"共振）
    ma_windows = []
    for w in request.args.get("ma", "").split(","):
        w = w.strip()
        if w in ("144", "288") and int(w) not in ma_windows:
            ma_windows.append(int(w))
    try:
        ma_tol = float(request.args.get("ma_tol", 0.03))
    except (TypeError, ValueError):
        ma_tol = 0.03
    ma_tol = min(0.10, max(0.005, ma_tol))

    # 属性/市场/价格筛选
    elem_filter = {e for e in request.args.get("elements", "").split(",") if e}
    valid_elems = {"木", "火", "土", "金", "水"}
    elem_filter &= valid_elems
    market_filter = {m for m in request.args.get("markets", "").split(",") if m}

    def _opt_float(name):
        v = request.args.get(name)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    min_price = _opt_float("min_price")
    max_price = _opt_float("max_price")

    dt = datetime(year, month, day, hour)
    period_data = YuanhaiDecisionModel.period_analyses(dt)
    daily_analysis = period_data["daily"]["analysis"]

    # 各周期元信息（日期/四柱/用神/权重）
    period_meta = []
    for p in ("monthly", "weekly", "daily"):
        if p not in selected:
            continue
        pd = period_data[p]
        an = pd["analysis"]
        period_meta.append({
            "key": p,
            "label": YuanhaiDecisionModel.PERIOD_LABELS[p],
            "weight": YuanhaiDecisionModel.PERIOD_WEIGHTS[p],
            "date": pd["date"],
            "pillars": str(pd["pillars"]),
            "dayMaster": an.day_master,
            "dayMasterStrength": an.day_master_strength,
            "useGods": an.use_gods,
            "avoidGods": an.avoid_gods,
        })

    conn = get_conn()
    try:
        rows, source = _get_universe(conn)
        # 均线表（可能尚未同步：表为空）
        ma_rows = conn.execute(
            "SELECT symbol, trade_date, close, high20, ma144, ma288 FROM stock_ma"
        ).fetchall()
        ma_meta = conn.execute(
            "SELECT value FROM sync_meta WHERE meta_key='ma_trade_date'"
        ).fetchone()
    finally:
        conn.close()

    ma_map = {r[0]: r for r in ma_rows}
    ma_trade_date = ma_meta[0] if ma_meta else None
    if ma_windows and not ma_trade_date:
        return _json_err(
            "均线指标尚未同步：请先点右侧「更新均线(144/288)」完成日K同步（首次约3~5分钟）",
            400,
        )

    results = []
    for row in rows:
        symbol, name = row[0], row[1] or row[0]
        price, change_pct, market = row[2], row[3], row[4]

        # ── 硬过滤：市场 / 价格 ──
        if market_filter and market not in market_filter:
            continue
        if price is not None:
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue

        elem = StockElementAnalyzer.combined_element(name)
        if elem is None:
            continue
        if elem_filter and elem not in elem_filter:
            continue

        # ── 硬过滤：回踩 144/288 日均线附近 ──
        # 无快照价时退用日K最新收盘（名称库场景）
        mrow = ma_map.get(symbol)
        ref_price = price
        ma144 = ma288 = high20 = kclose = ma_date = None
        if mrow:
            ma_date, kclose, high20, ma144, ma288 = (
                mrow[1], mrow[2], mrow[3], mrow[4], mrow[5])
            if ref_price is None:
                ref_price = kclose
        if ma_windows:
            if not mrow:
                continue
            ma_hit = True
            for w in ma_windows:
                ma_val = ma144 if w == 144 else ma288
                hit, _ = near_ma(ref_price, ma_val, high20, ma_tol)
                if not hit:
                    ma_hit = False
                    break
            if not ma_hit:
                continue

        info = YuanhaiDecisionModel.composite_score(
            elem, period_data, selected, stock_name=name)
        if info["score"] < min_score:
            continue

        item = {
            "symbol": symbol,
            "name": name,
            "element": elem,
            "score": info["score"],
            "level": info["level"],
            "reason": info["reason"],
            "periodScores": {
                k: {"score": v["score"], "level": v["level"]}
                for k, v in info["periodScores"].items()
            },
            "price": price,
            "changePct": change_pct,
            "market": market,
        }
        # 均线附加信息（有则带，供前端展示距离标签，不依赖勾选）
        if mrow:
            item["maDate"] = ma_date
            if ma144:
                _, d144 = near_ma(ref_price, ma144, high20, 1.0)
                item["ma144"] = ma144
                item["dist144"] = d144
            if ma288:
                _, d288 = near_ma(ref_price, ma288, high20, 1.0)
                item["ma288"] = ma288
                item["dist288"] = d288
        results.append(item)

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    return jsonify({
        "date": dt.strftime("%Y-%m-%d %H:%M"),
        "pillars": str(period_data["daily"]["pillars"]),
        "dayMaster": daily_analysis.day_master,
        "dayMasterElement": daily_analysis.day_master_element,
        "dayMasterStrength": daily_analysis.day_master_strength,
        "useGods": daily_analysis.use_gods,
        "avoidGods": daily_analysis.avoid_gods,
        "toneGod": daily_analysis.tone_god,
        "periods": period_meta,
        "maFilter": ma_windows,
        "maTol": ma_tol,
        "maTradeDate": ma_trade_date,
        "dataSource": source,
        "totalScanned": len(rows),
        "totalMatched": len(results),
        "results": results,
    })


@api.route("/sectors")
def sectors():
    """全 universe 按五行分组计数（大屏分布图用，按日缓存）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if _sectors_cache["date"] == today and _sectors_cache["data"]:
        return jsonify(_sectors_cache["data"])

    conn = get_conn()
    try:
        rows, source = _get_universe(conn)
    finally:
        conn.close()

    counts = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0, "未知": 0}
    for row in rows:
        elem = StockElementAnalyzer.combined_element(row[1] or row[0])
        counts[elem if elem else "未知"] += 1

    _sectors_cache.update(date=today, source=source, data=counts)
    return jsonify(counts)


@api.route("/sync", methods=["POST"])
def trigger_sync():
    """手动触发全市场快照同步。"""
    result = sync_spot_async()
    if not result["ok"] and "进行中" in result["message"]:
        return jsonify(result), 409
    return jsonify(result)


@api.route("/sync/ma", methods=["POST"])
def trigger_ma_sync():
    """手动触发 144/288 日均线日K同步（force=1 可强制重跑）。"""
    force = request.args.get("force") in ("1", "true")
    result = sync_ma_async(force=force)
    if not result["ok"] and "进行中" in result["message"]:
        return jsonify(result), 409
    return jsonify(result)


@api.route("/sync/status")
def sync_status():
    """同步状态查询。"""
    return jsonify(get_state())


app.register_blueprint(api)

# gunicorn（server.app:app）启动时同样确保库表就绪；测试可设 SKIP_DB_INIT=1 跳过。
if os.getenv("SKIP_DB_INIT") != "1":
    init_db()


# ── 静态页面托管（电脑大屏入口） ──────────────────────────────


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def not_found(_e):
    # hash 路由 SPA：非 /api 路径回退到 index.html
    if request.path.startswith("/api"):
        return _json_err("接口不存在", 404)
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    import sys
    import webbrowser

    # 1. 确保数据库与表就绪（MySQL 未启动时会自动等待重试）
    print(f"[i] 连接 MySQL 并初始化数据库 {DB_NAME} ...")
    init_db()
    print("[i] 数据库就绪")

    # 2. 启动时自动检测：当日无快照则后台同步（不阻塞服务）
    print(f"[i] 同步检测: {sync_spot_async()['message']}")

    # 3. 打印访问地址
    port = 5175
    try:
        hostname = socket.gethostname()
        lan_ip = socket.gethostbyname(hostname)
        print(f"[i] 本机访问: http://127.0.0.1:{port}")
        print(f"[i] 局域网/外网: http://{lan_ip}:{port}")
    except OSError:
        pass

    # 4. 仅在本机桌面环境自动打开浏览器（云服务器无桌面，跳过）
    if sys.platform.startswith("win") or sys.platform == "darwin":
        threading.Timer(2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    # 生产环境建议用 gunicorn，见 deploy/ 目录
    app.run(host="0.0.0.0", port=port, debug=False)
