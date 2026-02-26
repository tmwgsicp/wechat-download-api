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
from utils.helpers import extract_article_info, parse_article_url
from utils.http_client import fetch_page

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("RSS_POLL_INTERVAL", "3600"))
ARTICLES_PER_POLL = 10
FETCH_FULL_CONTENT = os.getenv("RSS_FETCH_FULL_CONTENT", "true").lower() == "true"


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

        logger.info("RSS poll: checking %d subscriptions", len(fakeids))

        for fakeid in fakeids:
            try:
                articles = await self._fetch_article_list(fakeid, creds)
                if articles and FETCH_FULL_CONTENT:
                    # 获取完整文章内容
                    articles = await self._enrich_articles_content(articles)
                
                if articles:
                    new_count = rss_store.save_articles(fakeid, articles)
                    if new_count > 0:
                        logger.info("RSS: %d new articles for %s", new_count, fakeid[:8])
                rss_store.update_last_poll(fakeid)
            except Exception as e:
                logger.error("RSS poll error for %s: %s", fakeid[:8], e)
            await asyncio.sleep(3)

    async def _fetch_article_list(self, fakeid: str, creds: Dict) -> List[Dict]:
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()

        base_resp = result.get("base_resp", {})
        if base_resp.get("ret") != 0:
            logger.warning("WeChat API error for %s: ret=%s",
                           fakeid[:8], base_resp.get("ret"))
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
    
    async def _enrich_articles_content(self, articles: List[Dict]) -> List[Dict]:
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
        
        # 批量并发获取（max_concurrency=5，传递微信凭证）
        results = await fetch_articles_batch(
            article_links, 
            max_concurrency=5, 
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
            if not html or "js_content" not in html:
                logger.warning("❌ No content in HTML: %s", link[:80])
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
                
                logger.info("✅ Content fetched: %s... (%d chars, %d images)", 
                           link[:50],
                           len(article["content"]), 
                           len(result.get("images", [])))
            except Exception as e:
                logger.error("Failed to process content for %s: %s", link[:80], str(e))
            
            enriched.append(article)
        
        return enriched


rss_poller = RSSPoller()
