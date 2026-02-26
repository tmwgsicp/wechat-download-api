#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
HTTP 客户端封装
优先使用 curl_cffi（模拟 Chrome TLS 指纹），不可用时自动降级到 httpx。
支持代理池轮转：当前代理失败 → 尝试下一个 → 全部失败 → 直连兜底。

注意：
1. curl_cffi 的 AsyncSession 在部分环境下 SOCKS5 代理不工作，
   因此代理场景使用同步 Session + 线程池来规避此问题。
2. 优先使用 SOCKS5 代理，避免被封禁。
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict

logger = logging.getLogger(__name__)

try:
    from curl_cffi.requests import Session as CurlSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

ENGINE_NAME = "curl_cffi (Chrome TLS)" if HAS_CURL_CFFI else "httpx (fallback)"
logger.info("HTTP engine: %s", ENGINE_NAME)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PROXY_RETRIES = 3
_executor = ThreadPoolExecutor(max_workers=4)


async def fetch_page(url: str, extra_headers: Optional[Dict] = None,
                     timeout: int = 30) -> str:
    """
    获取网页 HTML 内容。
    请求策略：代理1 → 代理2 → ... → 直连兜底。
    成功的代理会被标记为健康，失败的会被临时冷却。
    """
    from utils.proxy_pool import proxy_pool

    headers = {**BROWSER_HEADERS}
    if extra_headers:
        headers.update(extra_headers)

    tried_proxies = []
    for _ in range(min(MAX_PROXY_RETRIES, proxy_pool.count)):
        proxy = proxy_pool.next()
        if proxy is None or proxy in tried_proxies:
            break
        tried_proxies.append(proxy)

        logger.info("fetch_page: url=%s proxy=%s", url[:80], proxy)
        try:
            result = await _do_fetch(url, headers, timeout, proxy)
            proxy_pool.mark_ok(proxy)
            return result
        except Exception as e:
            logger.warning("Proxy %s failed: %s", proxy, e)
            proxy_pool.mark_failed(proxy)

    logger.info("fetch_page: url=%s proxy=direct (fallback)", url[:80])
    return await _do_fetch(url, headers, timeout, None)


async def _do_fetch(url: str, headers: Dict, timeout: int,
                    proxy: Optional[str]) -> str:
    """执行实际的HTTP请求"""
    # SOCKS5 代理或无代理：正常请求
    if HAS_CURL_CFFI:
        return await _fetch_curl_cffi(url, headers, timeout, proxy)
    return await _fetch_httpx(url, headers, timeout, proxy)


async def _fetch_curl_cffi(url: str, headers: Dict, timeout: int,
                           proxy: Optional[str]) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        _fetch_curl_cffi_sync, url, headers, timeout, proxy
    )


def _fetch_curl_cffi_sync(url: str, headers: Dict, timeout: int,
                          proxy: Optional[str]) -> str:
    """同步请求，在线程池中执行。规避 AsyncSession + SOCKS5 代理的兼容性问题。"""
    kwargs = {"timeout": timeout, "allow_redirects": True, "verify": False}  # 跳过 SSL 验证
    if proxy:
        kwargs["proxy"] = proxy
    with CurlSession(impersonate="chrome120") as session:
        resp = session.get(url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.text


async def _fetch_httpx(url: str, headers: Dict, timeout: int,
                       proxy: Optional[str]) -> str:
    import httpx
    transport_kwargs = {}
    if proxy:
        transport_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(timeout=float(timeout),
                                 follow_redirects=True,
                                 **transport_kwargs) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text
