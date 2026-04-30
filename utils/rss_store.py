#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 数据存储 — SQLite
管理订阅列表和文章缓存
"""

import sqlite3
import time
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Database path: configurable via env var, defaults to ./data/rss.db
_default_db = Path(__file__).parent.parent / "data" / "rss.db"
DB_PATH = Path(os.getenv("RSS_DB_PATH", str(_default_db)))


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表（幂等）"""
    conn = _get_conn()
    
    # 先创建不依赖其他表的基础表
    conn.executescript("""
        -- 分类表（先创建，因为 subscriptions 依赖它）
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            color       TEXT NOT NULL DEFAULT 'blue',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL
        );
        
        -- 黑名单表
        CREATE TABLE IF NOT EXISTS fakeid_blacklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid      TEXT NOT NULL UNIQUE,
            nickname    TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT 'manual',
            verification_count INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            blacklisted_at INTEGER NOT NULL,
            unblacklisted_at INTEGER DEFAULT NULL,
            note        TEXT NOT NULL DEFAULT ''
        );
        
        CREATE INDEX IF NOT EXISTS idx_blacklist_active ON fakeid_blacklist(is_active);
    """)
    conn.commit()
    
    # 检查 subscriptions 表是否存在
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subscriptions'"
    )
    table_exists = cursor.fetchone() is not None
    
    if table_exists:
        # 表已存在，检查是否有 category_id 列
        cursor = conn.execute("PRAGMA table_info(subscriptions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "category_id" not in columns:
            # 添加 category_id 列
            conn.execute("ALTER TABLE subscriptions ADD COLUMN category_id INTEGER DEFAULT NULL")
            conn.commit()
            logger.info("Added category_id column to subscriptions table")
    else:
        # 表不存在，创建新表
        conn.executescript("""
            CREATE TABLE subscriptions (
                fakeid      TEXT PRIMARY KEY,
                nickname    TEXT NOT NULL DEFAULT '',
                alias       TEXT NOT NULL DEFAULT '',
                head_img    TEXT NOT NULL DEFAULT '',
                category_id INTEGER DEFAULT NULL,
                created_at  INTEGER NOT NULL,
                last_poll   INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            );
        """)
        conn.commit()
    
    # 创建 articles 表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid      TEXT NOT NULL,
            aid         TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            link        TEXT NOT NULL DEFAULT '',
            digest      TEXT NOT NULL DEFAULT '',
            cover       TEXT NOT NULL DEFAULT '',
            author      TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            plain_content TEXT NOT NULL DEFAULT '',
            publish_time INTEGER NOT NULL DEFAULT 0,
            fetched_at  INTEGER NOT NULL,
            UNIQUE(fakeid, link),
            FOREIGN KEY (fakeid) REFERENCES subscriptions(fakeid) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_articles_fakeid_time
            ON articles(fakeid, publish_time DESC);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_category ON subscriptions(category_id);
    """)
    conn.commit()
    conn.close()
    logger.info("RSS database initialized: %s", DB_PATH)


# ── 订阅管理 ─────────────────────────────────────────────

def add_subscription(fakeid: str, nickname: str = "",
                     alias: str = "", head_img: str = "") -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions "
            "(fakeid, nickname, alias, head_img, created_at) VALUES (?,?,?,?,?)",
            (fakeid, nickname, alias, head_img, int(time.time())),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_subscription(fakeid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM subscriptions WHERE fakeid=?", (fakeid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_subscriptions() -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, c.name AS category_name, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s "
            "LEFT JOIN categories c ON s.category_id = c.id "
            "ORDER BY s.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscription(fakeid: str) -> Optional[Dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE fakeid=?", (fakeid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_last_poll(fakeid: str):
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET last_poll=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        conn.commit()
    finally:
        conn.close()


# ── 文章缓存 ─────────────────────────────────────────────

def save_articles(fakeid: str, articles: List[Dict]) -> int:
    """
    批量保存文章，返回新增数量。
    If an article already exists but has empty content, update it with new content.
    """
    conn = _get_conn()
    inserted = 0
    try:
        for a in articles:
            content = a.get("content", "")
            plain_content = a.get("plain_content", "")
            try:
                cursor = conn.execute(
                    "INSERT INTO articles "
                    "(fakeid, aid, title, link, digest, cover, author, "
                    "content, plain_content, publish_time, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(fakeid, link) DO UPDATE SET "
                    "content = CASE WHEN excluded.content != '' AND articles.content = '' "
                    "  THEN excluded.content ELSE articles.content END, "
                    "plain_content = CASE WHEN excluded.plain_content != '' AND articles.plain_content = '' "
                    "  THEN excluded.plain_content ELSE articles.plain_content END, "
                    "author = CASE WHEN excluded.author != '' AND articles.author = '' "
                    "  THEN excluded.author ELSE articles.author END",
                    (
                        fakeid,
                        a.get("aid", ""),
                        a.get("title", ""),
                        a.get("link", ""),
                        a.get("digest", ""),
                        a.get("cover", ""),
                        a.get("author", ""),
                        content,
                        plain_content,
                        a.get("publish_time", 0),
                        int(time.time()),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        return inserted
    finally:
        conn.close()


def get_articles(fakeid: str, limit: int = 20) -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fakeid=? "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_fakeids() -> List[str]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT fakeid FROM subscriptions").fetchall()
        return [r["fakeid"] for r in rows]
    finally:
        conn.close()


def get_all_articles(limit: int = 50) -> List[Dict]:
    """Get latest articles across all subscriptions, sorted by publish_time desc."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY publish_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 黑名单管理 ─────────────────────────────────────────────

def add_to_blacklist(fakeid: str, nickname: str = "", reason: str = "manual",
                     verification_count: int = 0, note: str = "") -> bool:
    """添加公众号到黑名单"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO fakeid_blacklist "
            "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
            "VALUES (?,?,?,?,1,?,?)",
            (fakeid, nickname, reason, verification_count, int(time.time()), note),
        )
        conn.commit()
        logger.info("Added %s to blacklist: %s", fakeid[:8], reason)
        return True
    finally:
        conn.close()


def remove_from_blacklist(fakeid: str) -> bool:
    """从黑名单移除（标记为非活跃）"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE fakeid_blacklist SET is_active=0, unblacklisted_at=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_blacklist_record(blacklist_id: int) -> bool:
    """永久删除黑名单记录"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM fakeid_blacklist WHERE id=? AND is_active=0", (blacklist_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def is_blacklisted(fakeid: str) -> bool:
    """检查公众号是否在黑名单中"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM fakeid_blacklist WHERE fakeid=? AND is_active=1",
            (fakeid,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_blacklist() -> List[Dict]:
    """获取黑名单列表"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM fakeid_blacklist ORDER BY blacklisted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_blacklist_fakeids() -> List[str]:
    """获取活跃黑名单的 fakeid 列表"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT fakeid FROM fakeid_blacklist WHERE is_active=1"
        ).fetchall()
        return [r["fakeid"] for r in rows]
    finally:
        conn.close()


def increment_verification_count(fakeid: str, nickname: str = "") -> int:
    """
    增加验证码触发次数，自动加入黑名单（超过阈值时）
    返回当前触发次数
    """
    threshold = 5  # 触发5次自动加入黑名单
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fakeid_blacklist WHERE fakeid=?", (fakeid,)
        ).fetchone()
        
        if row:
            new_count = row["verification_count"] + 1
            conn.execute(
                "UPDATE fakeid_blacklist SET verification_count=?, is_active=1, "
                "blacklisted_at=? WHERE fakeid=?",
                (new_count, int(time.time()), fakeid),
            )
        else:
            new_count = 1
            conn.execute(
                "INSERT INTO fakeid_blacklist "
                "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (fakeid, nickname, "high_verification", new_count,
                 1 if new_count >= threshold else 0,
                 int(time.time()),
                 f"自动记录: 触发验证码 {new_count} 次"),
            )
        
        conn.commit()
        
        if new_count >= threshold:
            logger.warning("Auto-blacklisted %s after %d verification triggers", 
                          fakeid[:8], new_count)
        
        return new_count
    finally:
        conn.close()


# ── 分类管理 ─────────────────────────────────────────────

def create_category(name: str, description: str = "", color: str = "blue") -> Optional[int]:
    """创建分类，返回新分类 ID"""
    conn = _get_conn()
    try:
        # 获取最大 sort_order
        row = conn.execute("SELECT MAX(sort_order) as max_order FROM categories").fetchone()
        max_order = row["max_order"] or 0
        
        cursor = conn.execute(
            "INSERT INTO categories (name, description, color, sort_order, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, description, color, max_order + 1, int(time.time())),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_category(category_id: int, name: str = None, 
                    description: str = None, color: str = None) -> bool:
    """更新分类"""
    conn = _get_conn()
    try:
        updates = []
        params = []
        if name is not None:
            updates.append("name=?")
            params.append(name)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if color is not None:
            updates.append("color=?")
            params.append(color)
        
        if not updates:
            return False
        
        params.append(category_id)
        conn.execute(
            f"UPDATE categories SET {', '.join(updates)} WHERE id=?",
            params,
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_category(category_id: int) -> bool:
    """删除分类（订阅会自动解除关联）"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_categories() -> List[Dict]:
    """获取所有分类及其订阅数"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT c.*, 
                   (SELECT COUNT(*) FROM subscriptions s WHERE s.category_id=c.id) AS subscription_count
            FROM categories c 
            ORDER BY c.sort_order, c.created_at
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_category(category_id: int) -> Optional[Dict]:
    """获取单个分类"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM categories WHERE id=?", (category_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_subscription_category(fakeid: str, category_id: Optional[int]) -> bool:
    """设置订阅的分类"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET category_id=? WHERE fakeid=?",
            (category_id, fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_subscriptions_by_category(category_id: int) -> List[Dict]:
    """获取分类下的所有订阅"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s WHERE s.category_id=? ORDER BY s.created_at DESC",
            (category_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_articles_by_category(category_id: int, limit: int = 50) -> List[Dict]:
    """获取分类下所有订阅的文章"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT a.* FROM articles a
            JOIN subscriptions s ON a.fakeid = s.fakeid
            WHERE s.category_id = ?
            ORDER BY a.publish_time DESC
            LIMIT ?
        """, (category_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


