#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文章内容获取器 - SOCKS5 代理方案
使用 curl_cffi 模拟真实浏览器 TLS 指纹，支持代理池轮转
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_article_content(
    article_url: str, 
    timeout: int = 60,
    wechat_token: Optional[str] = None,
    wechat_cookie: Optional[str] = None
) -> Optional[str]:
    """
    获取文章内容
    
    请求策略：
    1. SOCKS5 代理池轮转
    2. 直连兜底
    
    Args:
        article_url: 文章 URL
        timeout: 超时时间（秒）
        wechat_token: 微信 token（用于鉴权）
        wechat_cookie: 微信 Cookie（用于鉴权）
        
    Returns:
        文章 HTML 内容，失败返回 None
    """
    # 使用代理池获取文章
    html = await _fetch_via_proxy(article_url, timeout, wechat_cookie, wechat_token)
    return html


async def _fetch_via_proxy(
    article_url: str, 
    timeout: int,
    wechat_cookie: Optional[str] = None,
    wechat_token: Optional[str] = None,
    max_retries: int = 2
) -> Optional[str]:
    """
    通过 SOCKS5 代理或直连获取文章

    Args:
        article_url: 文章 URL
        timeout: 超时时间
        wechat_cookie: 微信 Cookie
        wechat_token: 微信 Token
        max_retries: 内容验证失败时的最大重试次数(每次会尝试不同代理)
    """
    try:
        from utils.http_client import fetch_page
        
        logger.info("[Fetch] %s", article_url[:80])
        
        full_url = article_url
        if wechat_token:
            separator = '&' if '?' in article_url else '?'
            full_url = f"{article_url}{separator}token={wechat_token}"
        
        extra_headers = {"Referer": "https://mp.weixin.qq.com/"}
        if wechat_cookie:
            extra_headers["Cookie"] = wechat_cookie
        
        for attempt in range(max_retries + 1):
            try:
                html = await fetch_page(
                    full_url,
                    extra_headers=extra_headers,
                    timeout=timeout
                )
                
                from utils.helpers import has_article_content, is_article_unavailable

                if is_article_unavailable(html):
                    logger.warning("[Fetch] permanently unavailable (attempt %d/%d) %s",
                                 attempt + 1, max_retries + 1, article_url[:60])
                    return html

                if has_article_content(html):
                    logger.info("[Fetch] len=%d (attempt %d/%d)",
                               len(html), attempt + 1, max_retries + 1)
                    return html
                else:
                    hint = "unknown"
                    if "验证" in html or "verify" in html.lower() or "环境异常" in html:
                        hint = "wechat_verification"
                    elif "请登录" in html or "login" in html.lower():
                        hint = "login_required"
                    elif "location.replace" in html or "location.href" in html:
                        hint = "redirect_page"
                    elif len(html) < 1000:
                        hint = "empty_or_blocked"
                    
                    logger.warning(
                        "[Fetch] invalid (len=%d, hint=%s, attempt %d/%d) %s",
                        len(html), hint, attempt + 1, max_retries + 1,
                        article_url[:60]
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(1)
                        continue
                    
            except Exception as e:
                logger.warning("[Fetch] request error: %s (attempt %d/%d)", 
                             str(e)[:100], attempt + 1, max_retries + 1)
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
        
        return None
        
    except Exception as e:
        logger.error("[Fetch] fatal error: %s", str(e)[:100])
        return None


async def fetch_articles_batch(
    article_urls: list, 
    max_concurrency: int = 5, 
    timeout: int = 60,
    wechat_token: Optional[str] = None,
    wechat_cookie: Optional[str] = None
) -> dict:
    """
    批量获取文章内容（并发版）
    
    Args:
        article_urls: 文章 URL 列表
        max_concurrency: 最大并发数
        timeout: 单个请求超时时间
        wechat_token: 微信 token（用于鉴权）
        wechat_cookie: 微信 Cookie（用于鉴权）
        
    Returns:
        {url: html} 字典，失败的 URL 对应 None
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    results = {}
    
    async def fetch_one(url):
        async with semaphore:
            html = await fetch_article_content(url, timeout, wechat_token, wechat_cookie)
            results[url] = html
            await asyncio.sleep(1)
    
    logger.info("[Batch] 开始批量获取 %d 篇文章", len(article_urls))
    
    await asyncio.gather(
        *[fetch_one(url) for url in article_urls],
        return_exceptions=True
    )
    
    success_count = sum(1 for html in results.values() if html)
    fail_count = len(results) - success_count
    
    logger.info("[Batch] 完成: 成功=%d, 失败=%d", success_count, fail_count)
    
    return results
