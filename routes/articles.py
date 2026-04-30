#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
文章列表API
获取公众号的文章列表
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
import httpx
from utils.auth_manager import auth_manager

router = APIRouter()


class ArticleItem(BaseModel):
    """文章列表项"""
    aid: str
    title: str
    link: str
    update_time: int
    create_time: int
    digest: Optional[str] = None
    cover: Optional[str] = None
    author: Optional[str] = None


class ArticlesResponse(BaseModel):
    """文章列表响应"""
    success: bool
    data: Optional[Dict] = None
    error: Optional[str] = None


@router.get("/articles", response_model=ArticlesResponse, summary="获取文章列表")
async def get_articles(
    fakeid: str = Query(..., description="目标公众号的 FakeID（通过搜索接口获取）"),
    begin: int = Query(0, description="偏移量，从第几条开始", ge=0, alias="begin"),
    count: int = Query(10, description="获取数量，最大 100", ge=1, le=100),
    keyword: Optional[str] = Query(None, description="在该公众号内搜索关键词（可选）")
):
    """
    获取指定公众号的文章列表，支持分页。

    **使用流程：**
    1. 先调用 `GET /api/public/searchbiz` 搜索目标公众号
    2. 从搜索结果中获取目标公众号的 `fakeid`
    3. 使用 `fakeid` 调用本接口获取文章列表

    **查询参数：**
    - **fakeid** (必填): 目标公众号的 FakeID
    - **begin** (可选): 偏移量，默认 0
    - **count** (可选): 获取数量，默认 10，最大 100
    - **keyword** (可选): 在该公众号内搜索关键词
    """
    try:
        print(f"[INFO] get article list: fakeid={fakeid[:8]}...")
        
        # 获取认证信息（用于请求微信API）
        creds = auth_manager.get_credentials()
        
        if not creds or not isinstance(creds, dict):
            raise HTTPException(
                status_code=401,
                detail="未登录或认证信息格式错误"
            )
        
        token = creds.get("token", "")
        cookie = creds.get("cookie", "")
        
        if not token or not cookie:
            raise HTTPException(
                status_code=401,
                detail="登录信息不完整，请重新登录"
            )
        
        # 构建请求参数
        is_searching = bool(keyword)
        params = {
            "sub": "search" if is_searching else "list",
            "search_field": "7" if is_searching else "null",
            "begin": begin,
            "count": count,
            "query": keyword or "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        
        # 请求微信API
        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mp.weixin.qq.com/",
            "Cookie": cookie
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            result = response.json()
        
        # 检查返回结果
        base_resp = result.get("base_resp", {})
        if base_resp.get("ret") != 0:
            error_msg = base_resp.get("err_msg", "未知错误")
            ret_code = base_resp.get("ret")
            
            print(f"[ERROR] WeChat API error: ret={ret_code}, msg={error_msg}")
            
            # 检查是否需要重新登录
            if "login" in error_msg.lower() or ret_code == 200003:
                return ArticlesResponse(
                    success=False,
                    error="登录已过期，请重新登录"
                )
            
            return ArticlesResponse(
                success=False,
                error=f"获取文章列表失败: ret={ret_code}, msg={error_msg}"
            )
        
        # 解析文章列表
        publish_page = result.get("publish_page", {})
        
        if isinstance(publish_page, str):
            try:
                publish_page = json.loads(publish_page)
            except (json.JSONDecodeError, ValueError):
                return ArticlesResponse(
                    success=False,
                    error="数据格式错误: publish_page 无法解析"
                )
        if not isinstance(publish_page, dict):
            return ArticlesResponse(
                success=False,
                error=f"数据格式错误: publish_page 类型为 {type(publish_page).__name__}"
            )
        
        publish_list = publish_page.get("publish_list", [])
        
        articles = []
        for item in publish_list:
            publish_info = item.get("publish_info", {})
            
            # publish_info可能是字符串JSON，需要解析
            if isinstance(publish_info, str):
                try:
                    publish_info = json.loads(publish_info)
                except (json.JSONDecodeError, ValueError):
                    continue
            
            if not isinstance(publish_info, dict):
                continue  # 跳过非字典类型
            
            appmsgex = publish_info.get("appmsgex", [])
            
            # 处理每篇文章
            for article in appmsgex:
                articles.append({
                    "aid": article.get("aid", ""),
                    "title": article.get("title", ""),
                    "link": article.get("link", ""),
                    "update_time": article.get("update_time", 0),
                    "create_time": article.get("create_time", 0),
                    "digest": article.get("digest", ""),
                    "cover": article.get("cover", ""),
                    "author": article.get("author", "")
                })
        
        return ArticlesResponse(
            success=True,
            data={
                "articles": articles,
                "total": publish_page.get("total_count", 0),
                "begin": begin,
                "count": len(articles),
                "keyword": keyword
            }
        )
        
    except httpx.HTTPStatusError as e:
        print(f"[ERROR] HTTP error: {e.response.status_code}")
        return ArticlesResponse(
            success=False,
            error=f"请求失败: HTTP {e.response.status_code}"
        )
    except httpx.RequestError as e:
        print(f"[ERROR] request error: {e}")
        return ArticlesResponse(
            success=False,
            error=f"网络请求失败: {str(e)}"
        )
    except Exception as e:
        import traceback
        print(f"[ERROR] unknown error: {e}")
        traceback.print_exc()
        return ArticlesResponse(
            success=False,
            error=f"服务器内部错误，请稍后重试"
        )


@router.get("/articles/search", response_model=ArticlesResponse, summary="搜索公众号文章")
async def search_articles(
    fakeid: str = Query(..., description="目标公众号的 FakeID"),
    query: str = Query(..., description="搜索关键词", alias="query"),
    begin: int = Query(0, description="偏移量，默认 0", ge=0, alias="begin"),
    count: int = Query(10, description="获取数量，默认 10，最大 100", ge=1, le=100)
):
    """
    在指定公众号内按关键词搜索文章。

    **查询参数：**
    - **fakeid** (必填): 目标公众号的 FakeID
    - **query** (必填): 搜索关键词
    - **begin** (可选): 偏移量，默认 0
    - **count** (可选): 获取数量，默认 10，最大 100
    """
    return await get_articles(
        fakeid=fakeid,
        keyword=query,
        begin=begin,
        count=count
    )

