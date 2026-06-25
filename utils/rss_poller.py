#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 后台轮询器
定时通过公众号后台 API 拉取订阅号的最新文章列表并缓存到 SQLite。
仅获取标题、摘要、封面等元数据，不访问文章页面，零风控风险。
"""

import asyncio
import json
import logging
import os
from typing import List, Dict, Optional

import httpx

from utils.auth_manager import auth_manager
from utils import rss_store
from utils.helpers import extract_article_info, parse_article_url, is_image_text_message, has_article_content, is_article_unavailable, get_unavailable_reason
from utils.http_client import fetch_page

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("RSS_POLL_INTERVAL", "3600"))
ARTICLES_PER_POLL = int(os.getenv("ARTICLES_PER_POLL", "10"))
FETCH_FULL_CONTENT = os.getenv("RSS_FETCH_FULL_CONTENT", "true").lower() == "true"


class WechatInvalidFakeidError(Exception):
    """
    [2026-05-18] 公众号在微信侧已失效（已注销/改名/重新注册）

    触发条件：appmsgpublish 接口返回 ret=200002 且 err_msg="invalid args"
    实测：任何 token+cookie 都无法访问，需要标记为永久失效
    """
    pass


class RSSPoller:
    """后台轮询单例"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("RSS poller started (interval=%ds)", POLL_INTERVAL)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RSS poller stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _loop(self):
        while self._running:
            try:
                await self._poll_all()
            except Exception as e:
                logger.error("RSS poll cycle error: %s", e, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_all(self):
        fakeids = rss_store.get_all_fakeids()
        if not fakeids:
            return

        creds = auth_manager.get_credentials()
        if not creds or not creds.get("token") or not creds.get("cookie"):
            logger.warning("RSS poll skipped: not logged in")
            return

        # 获取活跃黑名单
        blacklisted = set(rss_store.get_active_blacklist_fakeids())
        
        # 过滤掉黑名单中的公众号
        active_fakeids = [f for f in fakeids if f not in blacklisted]
        skipped = len(fakeids) - len(active_fakeids)
        
        if skipped > 0:
            logger.info("RSS poll: %d subscriptions (%d blacklisted, skipped)",
                       len(fakeids), skipped)
        else:
            logger.info("RSS poll: checking %d subscriptions", len(fakeids))

        # 每个 poll cycle 起一个全新 client：避免长连接池在网络抖动后卡死不自愈。
        # 与 remote_login / webhook / routes/* 等模块保持一致（短生命周期）。
        # poll 频率本就低（默认 1h 一次），多一次 DNS+TLS 握手开销可忽略。
        async with httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        ) as client:
            for fakeid in active_fakeids:
                try:
                    articles = await self._fetch_article_list(fakeid, creds, client)
                    if articles and FETCH_FULL_CONTENT:
                        # 获取完整文章内容
                        articles = await self._enrich_articles_content(fakeid, articles)

                    if articles:
                        # 轮询器拉取的文章标记为 'poll'
                        new_count = rss_store.save_articles(fakeid, articles, source='poll')
                        if new_count > 0:
                            logger.info("RSS: %d new articles for %s", new_count, fakeid[:8])
                    rss_store.update_last_poll(fakeid)
                except WechatInvalidFakeidError as e:
                    # [2026-05-18] 同步 SaaS 修复：fakeid 在微信侧已失效，自动加入黑名单
                    # 取该 fakeid 的 nickname（如果数据库里有）便于后续运维查看
                    sub = rss_store.get_subscription(fakeid)
                    nickname = sub.get("nickname", "") if sub else ""
                    logger.warning("Fakeid %s (%s) is invalid on WeChat, adding to blacklist", fakeid[:8], nickname)
                    try:
                        rss_store.add_to_blacklist(
                            fakeid, nickname=nickname, reason="invalid_fakeid",
                            note="[2026-05-18] 微信侧返回 invalid args，fakeid 已失效（注销/改名/重新注册）",
                        )
                    except Exception as bl_err:
                        logger.warning("Failed to blacklist invalid fakeid %s: %s", fakeid[:8], bl_err)
                except Exception as e:
                    # %r 拿异常类型 + repr；exc_info=True 打完整 Traceback
                    # 避免某些 httpx transport error str(e) 为空时日志变成 "RSS poll error for X: "
                    logger.error("RSS poll error for %s: %r", fakeid[:8], e, exc_info=True)
                await asyncio.sleep(3)

    async def _fetch_article_list(self, fakeid: str, creds: Dict, client: httpx.AsyncClient) -> List[Dict]:
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": 0,
            "count": ARTICLES_PER_POLL,
            "query": "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": creds["token"],
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mp.weixin.qq.com/",
            "Cookie": creds["cookie"],
        }

        resp = await client.get(
            "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()

        base_resp = result.get("base_resp", {})
        if base_resp.get("ret") != 0:
            ret_code = base_resp.get("ret")
            err_msg = base_resp.get("err_msg", "")
            logger.warning("WeChat API error for %s: ret=%s err_msg=%r",
                           fakeid[:8], ret_code, err_msg)
            # [2026-05-18] 同步 SaaS 修复：ret=200002 + "invalid args" → fakeid 已失效
            # 老代码统一返回空 → 静默失败，用户感受不到该号已注销
            # 现在：抛 WechatInvalidFakeidError 让调用方加入黑名单
            if ret_code == 200002 and "invalid arg" in err_msg.lower():
                raise WechatInvalidFakeidError(
                    f"fakeid {fakeid[:8]} 已失效（注销/改名）: {err_msg}"
                )
            return []

        publish_page = result.get("publish_page", {})
        if isinstance(publish_page, str):
            try:
                publish_page = json.loads(publish_page)
            except (json.JSONDecodeError, ValueError):
                return []

        if not isinstance(publish_page, dict):
            return []

        articles = []
        for item in publish_page.get("publish_list", []):
            publish_info = item.get("publish_info", {})
            if isinstance(publish_info, str):
                try:
                    publish_info = json.loads(publish_info)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(publish_info, dict):
                continue
            for a in publish_info.get("appmsgex", []):
                articles.append({
                    "aid": a.get("aid", ""),
                    "title": a.get("title", ""),
                    "link": a.get("link", ""),
                    "digest": a.get("digest", ""),
                    "cover": a.get("cover", ""),
                    "author": a.get("author", ""),
                    "publish_time": a.get("update_time", 0),
                })
        return articles

    async def poll_now(self):
        """手动触发一次轮询"""
        await self._poll_all()
    
    async def _enrich_articles_content(self, fakeid: str, articles: List[Dict]) -> List[Dict]:
        """
        批量获取文章完整内容（并发版）
        
        限制：最多获取 20 篇文章的完整内容（避免大量文章导致轮询过久）
        
        Args:
            articles: 文章列表（包含基本信息）
            
        Returns:
            enriched_articles: 包含完整内容的文章列表
        """
        from utils.article_fetcher import fetch_articles_batch
        from utils.content_processor import process_article_content
        
        # 提取所有文章链接
        article_links = [a.get("link", "") for a in articles if a.get("link")]
        
        if not article_links:
            return articles
        
        # 限制最多获取 20 篇（5个批次可能返回100+篇）
        max_fetch = 20
        if len(article_links) > max_fetch:
            logger.info("文章数 %d 篇超过限制，仅获取最近 %d 篇的完整内容", 
                       len(article_links), max_fetch)
            article_links = article_links[:max_fetch]
            articles = articles[:max_fetch]
        
        logger.info("开始批量获取 %d 篇文章的完整内容", len(article_links))
        
        # 获取微信凭证（从环境变量读取）
        wechat_token = os.getenv("WECHAT_TOKEN", "")
        wechat_cookie = os.getenv("WECHAT_COOKIE", "")
        
        results = await fetch_articles_batch(
            article_links, 
            max_concurrency=3, 
            timeout=60,
            wechat_token=wechat_token,
            wechat_cookie=wechat_cookie
        )
        
        # 处理结果并合并到原文章数据
        enriched = []
        for article in articles:
            link = article.get("link", "")
            if not link:
                enriched.append(article)
                continue
            
            html = results.get(link)
            if not html:
                logger.warning("Empty HTML: %s", link[:80])
                enriched.append(article)
                continue
            
            # [2026-05-18] 精确化验证码检测（之前用 "验证" 二字误伤大量正文含此字的文章）
            # 微信风控页特有标记：
            #   1. verifycode 出现在 URL/form/script 中（最强信号）
            #   2. "请输入图片中的字符" — 微信原版验证码提示文案
            #   3. "环境异常" — 微信明确风控提示（保留原检测）
            # 移除单纯的"验证"二字判断 — 文章正文里出现概率高，会导致 content 丢失
            html_lower = html.lower()
            verification_markers = (
                "verifycode" in html_lower
                or "请输入图片中的字符" in html
                or "环境异常" in html
            )
            if verification_markers:
                sub = rss_store.get_subscription(fakeid)
                nickname = sub.get("nickname", "") if sub else ""
                count = rss_store.increment_verification_count(fakeid, nickname)
                logger.warning("Verification triggered for %s (count=%d): %s",
                             fakeid[:8], count, link[:60])
                enriched.append(article)
                continue
            
            if is_article_unavailable(html):
                reason = get_unavailable_reason(html) or "unknown"
                logger.warning("Article permanently unavailable (%s): %s", reason, link[:80])
                article["content"] = f"<p>[unavailable] {reason}</p>"
                article["plain_content"] = f"[unavailable] {reason}"
                enriched.append(article)
                continue
            if not has_article_content(html):
                logger.warning("No content in HTML: %s", link[:80])
                enriched.append(article)
                continue
            
            try:
                # 使用 content_processor 处理文章内容（完美保持图文顺序）
                # 从环境变量读取网站URL,入库时代理图片(与SaaS版策略一致)
                site_url = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
                result = process_article_content(html, proxy_base_url=site_url)
                
                # 合并到原文章数据
                article["content"] = result.get("content", "")
                article["plain_content"] = result.get("plain_content", "")
                
                # 如果原始数据没有作者，从 HTML 中提取
                if not article.get("author"):
                    from utils.helpers import extract_article_info, parse_article_url
                    article_info = extract_article_info(html, parse_article_url(link))
                    article["author"] = article_info.get("author", "")
                
                logger.info("Content fetched: %s... (%d chars, %d images)",
                           link[:50],
                           len(article["content"]), 
                           len(result.get("images", [])))
            except Exception as e:
                logger.error("Failed to process content for %s: %s", link[:80], str(e))
            
            enriched.append(article)
        
        return enriched


rss_poller = RSSPoller()
