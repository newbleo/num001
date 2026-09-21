"""SQLite storage layer for 이거사죠."""
import os
import secrets
import sqlite3
import time

RESERVE_TTL_SECONDS = 30 * 60  # 예약(선물하는 중) 유지 시간

SCHEMA = """
CREATE TABLE IF NOT EXISTS wishlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    owner_name  TEXT NOT NULL,
    intro       TEXT NOT NULL DEFAULT '',
    edit_token  TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    wishlist_id    INTEGER NOT NULL REFERENCES wishlists(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL DEFAULT '',
    price          INTEGER NOT NULL DEFAULT 0,
    category       TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    image_url      TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open',
    reserve_token  TEXT NOT NULL DEFAULT '',
    reserved_at    INTEGER,
    gifter_name    TEXT NOT NULL DEFAULT '',
    gifter_message TEXT NOT NULL DEFAULT '',
    gifter_display TEXT NOT NULL DEFAULT 'name',
    gifted_at      INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_wishlist ON items(wishlist_id);
"""


def default_path():
    return os.environ.get("IGEOSAJO_DB", os.path.join(os.path.dirname(__file__), "..", "igeosajo.sqlite3"))


def connect(path=None):
    conn = sqlite3.connect(path or default_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def now():
    return int(time.time())


def new_token():
    return secrets.token_hex(16)


def new_slug():
    # 사람이 주고받기 쉬운 짧은 링크
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(7))


def expire_reservations(conn, wishlist_id=None):
    """오래된 '선물하는 중' 상태를 자동으로 풀어준다."""
    cutoff = now() - RESERVE_TTL_SECONDS
    sql = "UPDATE items SET status='open', reserve_token='', reserved_at=NULL " \
          "WHERE status='reserved' AND reserved_at IS NOT NULL AND reserved_at < ?"
    params = [cutoff]
    if wishlist_id is not None:
        sql += " AND wishlist_id = ?"
        params.append(wishlist_id)
    conn.execute(sql, params)
    conn.commit()
