"""实跑全市场快照同步（验证多源 fallback 链真实可用）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_sync

start = time.time()
result = data_sync.sync_spot()
elapsed = time.time() - start
print(f"同步结果: {result}")
print(f"耗时: {elapsed:.1f}s")
print(f"状态: {data_sync.get_state()}")
print(f"快照数量: {data_sync.get_spot_count()}")

# 抽查几个各市场代表股
conn = data_sync.get_conn()
for code in ("600519", "000001", "300750", "688981", "833533", "920000"):
    row = conn.execute(
        "SELECT symbol, name, price, change_pct, market FROM stock_spot WHERE symbol = %s", (code,)
    ).fetchone()
    print("抽查:", row if row else f"{code} 不在快照中")

# 市场分布
dist = conn.execute("SELECT market, COUNT(*) FROM stock_spot GROUP BY market").fetchall()
print("市场分布:", [(r[0], r[1]) for r in dist])
conn.close()

