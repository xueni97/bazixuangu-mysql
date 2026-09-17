"""MySQL 数据访问层（替代原 SQLite）。

连接配置来自项目根目录 .env（也可用真实环境变量覆盖）：
  DB_HOST=127.0.0.1
  DB_PORT=3306
  DB_USER=bazi
  DB_PASSWORD=你的密码
  DB_NAME=bazixuangu

服务启动时调用 init_db()：自动建库 + 建表（幂等），
并在 MySQL 尚未就绪时重试等待（适配 Docker / 开机自启场景）。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv

# server/db.py → 项目根目录 bazi-stock-app/
APP_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(APP_ROOT / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "bazixuangu")

_CONNECT_KWARGS = dict(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    charset="utf8mb4",
    connect_timeout=10,
    autocommit=False,
)

_init_lock = threading.Lock()


class Connection:
    """PyMySQL 连接薄封装。

    补齐两类 sqlite3 习惯用法，让迁移后的业务代码几乎不用改：
    1. conn.execute()/conn.executemany() 快捷方法（PyMySQL 原生只有 cursor 才有）；
    2. with conn: 成功自动 commit / 异常自动 rollback，且退出时【不】关闭连接
       （与 sqlite3 一致；连接仍由调用方在 finally 中 close）。
    """

    def __init__(self, **kwargs):
        self._c = pymysql.connections.Connection(**kwargs)

    def cursor(self, *args, **kwargs):
        return self._c.cursor(*args, **kwargs)

    def execute(self, sql, args=None):
        cur = self._c.cursor()
        cur.execute(sql, args)
        return cur

    def executemany(self, sql, args):
        cur = self._c.cursor()
        cur.executemany(sql, args)
        return cur

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()

    def ping(self, reconnect=True):
        self._c.ping(reconnect=reconnect)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        return False  # 不吞异常


def _server_connection() -> pymysql.connections.Connection:
    """连 MySQL 服务（不指定数据库），用于 CREATE DATABASE。"""
    return pymysql.connect(**_CONNECT_KWARGS)


def get_conn() -> Connection:
    """获取业务库连接（tuple 游标，与原 sqlite3.Row 的下标访问保持兼容）。"""
    return Connection(database=DB_NAME, **_CONNECT_KWARGS)


# ── 建表 DDL（与原 SQLite 表结构一一对应；sync_meta 的 key 是 MySQL 保留字，改名 meta_key）──

_CREATE_STOCK_NAMES = """
CREATE TABLE IF NOT EXISTS stock_names (
    symbol   VARCHAR(10) NOT NULL PRIMARY KEY,
    name     VARCHAR(32) NOT NULL,
    industry VARCHAR(32) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_STOCK_SPOT = """
CREATE TABLE IF NOT EXISTS stock_spot (
    symbol     VARCHAR(10) NOT NULL PRIMARY KEY,
    name       VARCHAR(32) DEFAULT NULL,
    price      DOUBLE DEFAULT NULL,
    change_pct DOUBLE DEFAULT NULL,
    market     VARCHAR(8) DEFAULT NULL,
    updated_at VARCHAR(19) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_STOCK_MA = """
CREATE TABLE IF NOT EXISTS stock_ma (
    symbol     VARCHAR(10) NOT NULL PRIMARY KEY,
    trade_date VARCHAR(10) DEFAULT NULL,
    close      DOUBLE DEFAULT NULL,
    high20     DOUBLE DEFAULT NULL,
    bars       INT DEFAULT NULL,
    ma144      DOUBLE DEFAULT NULL,
    ma288      DOUBLE DEFAULT NULL,
    updated_at VARCHAR(19) DEFAULT NULL,
    source     VARCHAR(8) NOT NULL DEFAULT 'em'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_SYNC_META = """
CREATE TABLE IF NOT EXISTS sync_meta (
    meta_key VARCHAR(40) NOT NULL PRIMARY KEY,
    value    VARCHAR(64) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_STATEMENTS = (
    _CREATE_STOCK_NAMES,
    _CREATE_STOCK_SPOT,
    _CREATE_STOCK_MA,
    _CREATE_SYNC_META,
)


def create_tables(conn: pymysql.connections.Connection) -> None:
    """在业务库中创建全部表（幂等）。"""
    with conn.cursor() as cur:
        for ddl in DDL_STATEMENTS:
            cur.execute(ddl)
    conn.commit()


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """建库 + 建表。MySQL 未就绪时按间隔重试。

    进程内只真正执行一次；重复调用直接返回。
    """
    with _init_lock:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                conn = _server_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                        )
                    conn.commit()
                finally:
                    conn.close()

                conn = get_conn()
                try:
                    create_tables(conn)
                finally:
                    conn.close()
                return
            except pymysql.MySQLError as exc:  # 服务还没起来 / 认证失败等
                last_error = exc
                if attempt == 1 or attempt % 5 == 0:
                    print(f"[db] 等待 MySQL 就绪（{attempt}/{retries}）：{exc}")
                time.sleep(delay)
        raise RuntimeError(f"MySQL 连接失败，已重试 {retries} 次：{last_error}")
