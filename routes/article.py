#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
文章路由 - FastAPI版本
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, List
import re
from utils.auth_manager import auth_manager
from utils.helpers import extract_article_info, parse_article_url
from utils.rate_limiter import rate_limiter
from utils.webhook import webhook
from utils.http_client import fetch_page

router = APIRouter()

class ArticleRequest(BaseModel):
    """文章请求"""
    url: str = Field(..., description="微信文章链接，如 https://mp.weixin.qq.com/s/xxxxx")

class ArticleData(BaseModel):
    """文章数据"""
    title: str = Field(..., description="文章标题")
    content: str = Field(..., description="文章 HTML 正文（保留原始排版）")
    plain_content: str = Field("", description="纯文本正文（去除所有 HTML 标签，适合直接阅读或 AI 处理）")
    images: List[str] = Field(default_factory=list, description="文章内图片 URL 列表")
    author: str = Field("", description="作者")
    publish_time: int = Field(0, description="发布时间戳（秒）")
    publish_time_str: Optional[str] = Field(None, description="可读发布时间，如 2026-02-24 09:00:00")

class ArticleResponse(BaseModel):
    """文章响应"""
    success: bool = Field(..., description="是否成功")
    data: Optional[ArticleData] = Field(None, description="文章数据，失败时为 null")
    error: Optional[str] = Field(None, description="错误信息，成功时为 null")

@router.post("/article", response_model=ArticleResponse, summary="获取文章内容")
async def get_article(article_request: ArticleRequest, request: Request):
    """
    解析微信公众号文章，返回标题、正文、图片等结构化数据。

    **请求体参数：**
    - **url** (必填): 微信文章链接，支持 `https://mp.weixin.qq.com/s/xxxxx` 格式

    **返回字段：**
    - `title`: 文章标题
    - `content`: HTML 正文（保留原始排版）
    - `plain_content`: 纯文本正文（去除所有 HTML 标签，适合直接阅读或 AI 处理）
    - `author`: 作者
    - `publish_time`: 发布时间戳
    - `images`: 文章内的图片列表
    """
    client_ip = request.client.host if request.client else "unknown"
    allowed, error_msg = rate_limiter.check_rate_limit(client_ip, "/api/article")
    if not allowed:
        return {"success": False, "error": f"⏱️ {error_msg}"}

    credentials = auth_manager.get_credentials()
    if not credentials:
        return {"success": False, "error": "服务器未登录，请先访问管理页面扫码登录"}

    try:
        html = await fetch_page(
            article_request.url,
            extra_headers={"Referer": "https://mp.weixin.qq.com/"},
            timeout=120  # WeChat 大文章可能超时，延长至 120 秒
        )

        if "js_content" not in html:
            if "verify" in html or "验证" in html or "环境异常" in html:
                await webhook.notify('verification_required', {
                    'url': article_request.url,
                    'ip': client_ip
                })
                return {
                    "success": False,
                    "error": "触发微信安全验证。解决方法：1) 在浏览器中打开文章URL完成验证 "
                             "2) 等待30分钟后重试 3) 降低请求频率"
                }
            if "请登录" in html:
                await webhook.notify('login_expired', {
                    'account': credentials.get('nickname', ''),
                    'url': article_request.url
                })
                return {"success": False, "error": "登录已失效，请重新扫码登录"}
            return {
                "success": False,
                "error": "无法获取文章内容。可能原因：文章被删除、访问受限或需要验证。"
            }

        params = parse_article_url(article_request.url)

        if not params or not params.get('__biz'):
            location_match = re.search(r'var\s+msg_link\s*=\s*"([^"]+)"', html)
            if location_match:
                real_url = location_match.group(1).replace('&amp;', '&')
                params = parse_article_url(real_url)

        if not params or not params.get('__biz'):
            href_match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', html)
            if href_match:
                real_url = href_match.group(1).replace('&amp;', '&')
                params = parse_article_url(real_url)

        if not params or not params.get('__biz'):
            biz_match = re.search(r'var\s+__biz\s*=\s*"([^"]+)"', html)
            mid_match = re.search(r'var\s+mid\s*=\s*"([^"]+)"', html)
            idx_match = re.search(r'var\s+idx\s*=\s*"([^"]+)"', html)
            sn_match = re.search(r'var\s+sn\s*=\s*"([^"]+)"', html)

            if all([biz_match, mid_match, idx_match, sn_match]):
                params = {
                    '__biz': biz_match.group(1),
                    'mid': mid_match.group(1),
                    'idx': idx_match.group(1),
                    'sn': sn_match.group(1)
                }

        if not params or not params.get('__biz'):
            params = None

        article_data = extract_article_info(html, params)

        return {"success": True, "data": article_data}

    except Exception as e:
        error_str = str(e)
        if "timeout" in error_str.lower():
            return {"success": False, "error": "请求超时，请稍后重试"}
        return {"success": False, "error": f"处理请求时发生错误: {error_str}"}
