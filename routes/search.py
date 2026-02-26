#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
搜索路由 - FastAPI版本
"""

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from typing import Optional, List
import time
import httpx
from utils.auth_manager import auth_manager
from utils.image_proxy import proxy_image_url

router = APIRouter()

class Account(BaseModel):
    """公众号模型"""
    id: str
    name: str
    round_head_img: str

class SearchResponse(BaseModel):
    """搜索响应模型"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None

@router.get("/searchbiz", response_model=SearchResponse, summary="搜索公众号")
async def search_accounts(query: str = Query(..., description="公众号名称或关键词", alias="query"), request: Request = None):
    """
    按关键词搜索微信公众号，获取 FakeID。

    **查询参数：**
    - **query** (必填): 搜索关键词（公众号名称）

    **返回字段：**
    - `list[]`: 匹配的公众号列表，每项包含 `fakeid`、`nickname`、`alias`、`round_head_img`
    - `total`: 匹配数量
    """
    credentials = auth_manager.get_credentials()
    if not credentials:
        return SearchResponse(
            success=False,
            error="服务器未登录，请先访问管理页面扫码登录"
        )
    
    token = credentials.get("token")
    cookie = credentials.get("cookie")
    
    try:
        # 调用微信搜索公众号API
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://mp.weixin.qq.com/cgi-bin/searchbiz",
                params={
                    "action": "search_biz",
                    "token": token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": 1,
                    "random": time.time(),
                    "query": query,
                    "begin": 0,
                    "count": 5
                },
                headers={
                    "Cookie": cookie,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            
            result = response.json()
            
            if result.get("base_resp", {}).get("ret") == 0:
                accounts = result.get("list", [])
                
                # 获取 base_url 用于图片代理
                base_url = str(request.base_url).rstrip("/") if request else ""
                
                # 格式化返回数据
                formatted_accounts = []
                for acc in accounts:
                    # 将头像 URL 转换为代理链接
                    round_head_img = proxy_image_url(acc.get("round_head_img", ""), base_url)
                    formatted_accounts.append({
                        "fakeid": acc.get("fakeid", ""),
                        "nickname": acc.get("nickname", ""),
                        "alias": acc.get("alias", ""),
                        "round_head_img": round_head_img,
                        "service_type": acc.get("service_type", 0)
                    })
                
                return SearchResponse(
                    success=True,
                    data={
                        "list": formatted_accounts,
                        "total": len(formatted_accounts)
                    }
                )
            else:
                return SearchResponse(
                    success=False,
                    error=f"搜索失败: {result.get('base_resp', {}).get('err_msg', '未知错误')}"
                )
                
    except Exception as e:
        print(f"❌ 搜索公众号失败: {str(e)}")
        return SearchResponse(
            success=False,
            error=f"搜索请求失败: {str(e)}"
        )
