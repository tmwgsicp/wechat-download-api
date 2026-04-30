#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
登录路由 - FastAPI版本
实现真实的微信公众号登录流程
"""

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict
import httpx
import time
from utils.auth_manager import auth_manager
from utils.webhook import webhook

router = APIRouter()

# 微信登录API端点
MP_BASE_URL = "https://mp.weixin.qq.com"
QR_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/scanloginqrcode"
BIZ_LOGIN_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/bizlogin"

# 全局session状态存储
_sessions = {}

async def proxy_wx_request(request: Request, url: str, params: dict = None, method: str = "GET", data: dict = None):
    """
    代理微信请求,转发浏览器cookies
    
    这个函数类似Node.js版本的proxyMpRequest:
    1. 从浏览器请求中提取cookies
    2. 转发给微信API
    3. 把微信的Set-Cookie响应转发回浏览器
    """
    # 从浏览器请求中提取cookies
    cookie_header = request.headers.get("cookie", "")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://mp.weixin.qq.com/",
        "Origin": "https://mp.weixin.qq.com",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie_header  # 转发浏览器的cookies
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        if method == "GET":
            response = await client.get(url, params=params, headers=headers)
        else:
            response = await client.post(url, params=params, data=data, headers=headers)
        
        return response

class LoginRequest(BaseModel):
    """手动登录请求模型"""
    token: str
    cookie: str
    fakeid: str
    nickname: str
    expire_time: int

class LoginResponse(BaseModel):
    """登录响应模型"""
    success: bool
    message: str

@router.post("/session/{sessionid}", summary="初始化登录会话", include_in_schema=True)
async def create_session(sessionid: str, request: Request):
    """
    初始化登录会话，必须在获取二维码之前调用。

    **路径参数：**
    - **sessionid**: 会话标识，由前端生成
    """
    try:
        # [SEARCH] 调试：输出请求信息
        cookie_header = request.headers.get("cookie", "")
        print(f"[DEBUG] 创建Session - Cookie: {cookie_header[:100]}..." if len(cookie_header) > 100 else f"[DEBUG] 创建Session - Cookie: {cookie_header}")
        
        # [*] 关键：调用bizlogin而不是scanloginqrcode！
        body = {
            "userlang": "zh_CN",
            "redirect_url": "",
            "login_type": 3,
            "sessionid": sessionid,
            "token": "",
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1
        }
        
        response = await proxy_wx_request(
            request,
            BIZ_LOGIN_ENDPOINT,  # [*] 使用bizlogin
            params={"action": "startlogin"},
            method="POST",
            data=body  # [*] 传递body
        )
        
        # 存储session
        _sessions[sessionid] = {
            "created_at": time.time(),
            "status": "created"
        }
        
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"base_resp": {"ret": 0}}
        
        # [SEARCH] 调试：输出响应详情
        print(f"[DEBUG] Session响应状态码: {response.status_code}")
        print(f"[DEBUG] Session响应数据: {data}")
        print(f"[DEBUG] Session响应 Set-Cookie 数量: {len(response.headers.get_list('set-cookie'))}")
        for i, cookie in enumerate(response.headers.get_list("set-cookie")):
            print(f"[DEBUG] Cookie [{i}]: {cookie[:150]}..." if len(cookie) > 150 else f"[DEBUG] Cookie [{i}]: {cookie}")
        
        # 转发Set-Cookie（智能处理Secure标志）
        response_obj = JSONResponse(content=data)
        
        # [SEARCH] 检测是否使用 HTTPS（支持反向代理）
        is_https = (
            request.url.scheme == "https" or 
            request.headers.get("x-forwarded-proto") == "https" or
            request.headers.get("x-forwarded-ssl") == "on"
        )
        
        if is_https:
            print(f"[HTTPS] 检测到HTTPS环境，Cookie将保留Secure标志（安全传输）")
        else:
            print(f"[WARN] 检测到HTTP环境，Cookie将移除Secure标志（兼容模式，生产环境建议使用HTTPS）")
        
        for cookie_str in response.headers.get_list("set-cookie"):
            if not is_https:
                # [FIX] HTTP模式：移除Secure标志以支持HTTP传输
                modified_cookie = cookie_str.replace("; Secure", "")
                response_obj.headers.append("Set-Cookie", modified_cookie)
            else:
                # [HTTPS] HTTPS模式：保留Secure标志，保持安全性
                response_obj.headers.append("Set-Cookie", cookie_str)
        
        print(f"[OK] 创建session: {sessionid}, 响应: {data}")
        return response_obj
        
    except Exception as e:
        print(f"[ERROR] session failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"base_resp": {"ret": 0}})  # 返回成功避免前端报错

@router.get("/getqrcode", summary="获取登录二维码", include_in_schema=True)
async def get_qrcode(request: Request):
    """
    获取微信公众号登录二维码图片，用微信扫码登录。

    **返回：** 二维码图片（PNG/JPEG 格式）
    """
    try:
        # [SEARCH] 调试：输出请求信息
        cookie_header = request.headers.get("cookie", "")
        print(f"[DEBUG] 二维码请求 Cookie: {cookie_header[:100]}..." if len(cookie_header) > 100 else f"[DEBUG] 二维码请求 Cookie: {cookie_header}")
        
        # 代理请求到微信
        response = await proxy_wx_request(
            request,
            QR_ENDPOINT,
            params={
                "action": "getqrcode",
                "random": int(time.time() * 1000)
            }
        )
        
        # [SEARCH] 调试：输出响应信息
        print(f"[DEBUG] 微信响应状态码: {response.status_code}")
        print(f"[DEBUG] 微信响应 Content-Type: {response.headers.get('content-type', 'N/A')}")
        print(f"[DEBUG] 微信响应内容长度: {len(response.content)} 字节")
        print(f"[DEBUG] 微信响应 Set-Cookie: {response.headers.get('set-cookie', 'N/A')}")
        
        # 检查响应类型
        content_type = response.headers.get("content-type", "")
        content = response.content
        
        # 检查是否是图片格式
        is_png = content.startswith(b'\x89PNG')
        is_jpeg = content.startswith(b'\xff\xd8\xff') or b'JFIF' in content[:20]
        is_image = "image" in content_type or is_png or is_jpeg
        
        # 如果返回的是JSON或者不是图片，说明出错了
        if not is_image:
            try:
                error_data = response.json()
                print(f"[WARN] 二维码接口返回JSON: {error_data}")
                return JSONResponse(
                    status_code=400,
                    content={"error": "需要先调用 /session/{sessionid} 创建会话", "detail": error_data}
                )
            except:
                print(f"[WARN] 二维码接口返回非图片内容: {content_type}")
                print(f"响应内容前20字节: {content[:20]}")
                return JSONResponse(
                    status_code=400,
                    content={"error": "获取二维码失败，响应格式异常"}
                )
        
        # 确定正确的媒体类型
        if is_png:
            media_type = "image/png"
            print(f"[OK] 获取到PNG格式二维码")
        elif is_jpeg:
            media_type = "image/jpeg"
            print(f"[OK] 获取到JPEG格式二维码")
        else:
            # 使用响应头中的类型
            media_type = content_type if "image" in content_type else "image/png"
            print(f"[OK] 获取到二维码，类型: {media_type}")
        
        # 可选：保存二维码到本地（用于调试）
        import os
        qrcode_dir = "static/qrcodes"
        if not os.path.exists(qrcode_dir):
            os.makedirs(qrcode_dir)
        
        # 根据格式确定文件扩展名
        ext = "png" if is_png else "jpg"
        qrcode_path = f"{qrcode_dir}/login_qrcode.{ext}"
        
        with open(qrcode_path, "wb") as f:
            f.write(content)
        print(f"[SAVE] 二维码已保存到: {qrcode_path}")
        
        # 构建响应,转发Set-Cookie
        response_obj = Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
        # 转发Set-Cookie到浏览器（智能处理Secure标志）
        is_https = (
            request.url.scheme == "https" or 
            request.headers.get("x-forwarded-proto") == "https" or
            request.headers.get("x-forwarded-ssl") == "on"
        )
        
        for cookie_str in response.headers.get_list("set-cookie"):
            if not is_https:
                # [FIX] HTTP模式：移除Secure标志
                modified_cookie = cookie_str.replace("; Secure", "")
                response_obj.headers.append("Set-Cookie", modified_cookie)
            else:
                # [HTTPS] HTTPS模式：保留Secure标志
                response_obj.headers.append("Set-Cookie", cookie_str)
        
        return response_obj
    
    except httpx.HTTPStatusError as e:
        print(f"[ERROR] HTTP: {e.response.status_code}, content: {e.response.text[:200]}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"获取二维码失败: {e.response.status_code}"
        )
    except Exception as e:
        print(f"[ERROR] QR code error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取二维码失败: {str(e)}")

@router.get("/scan", summary="检查扫码状态", include_in_schema=True)
async def check_scan_status(request: Request):
    """
    轮询检查二维码扫描状态。

    **返回状态：** 等待扫码 / 已扫码待确认 / 确认成功 / 二维码过期
    """
    try:
        # 代理请求到微信
        response = await proxy_wx_request(
            request,
            QR_ENDPOINT,
            params={
                "action": "ask",
                "token": "",
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1
            }
        )
        response.raise_for_status()
        
        # 返回微信的响应
        data = response.json()
        
        # 记录状态用于调试
        if data.get("base_resp", {}).get("ret") != 0:
            print(f"[WARN] 扫码状态检查失败: ret={data.get('base_resp', {}).get('ret')}")
        else:
            status = data.get("status", 0)
            if status == 1:  # 登录成功
                print(f"[SUCCESS] 用户已确认登录! status=1")
            elif status in [4, 6]:  # 已扫码
                acct_size = data.get("acct_size", 0)
                print(f"[OK] 用户已扫码, status={status}, acct_size={acct_size}")
        
        # 转发Set-Cookie到浏览器（智能处理Secure标志）
        response_obj = JSONResponse(content=data)
        
        is_https = (
            request.url.scheme == "https" or 
            request.headers.get("x-forwarded-proto") == "https" or
            request.headers.get("x-forwarded-ssl") == "on"
        )
        
        for cookie_str in response.headers.get_list("set-cookie"):
            if not is_https:
                # [FIX] HTTP模式：移除Secure标志
                modified_cookie = cookie_str.replace("; Secure", "")
                response_obj.headers.append("Set-Cookie", modified_cookie)
            else:
                # [HTTPS] HTTPS模式：保留Secure标志
                response_obj.headers.append("Set-Cookie", cookie_str)
        
        return response_obj
    
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"检查扫码状态失败: {e.response.status_code}"
        )
    except Exception as e:
        print(f"[ERROR] scan status error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"检查扫码状态失败: {str(e)}")

@router.post("/bizlogin", summary="完成登录", include_in_schema=True)
async def biz_login(request: Request):
    """
    扫码确认后调用此接口完成登录，成功后凭证自动保存到 `.env`。

    **返回：** Token、Cookie、FakeID、昵称等登录凭证
    """
    try:
        # 准备登录请求数据
        login_data = {
            "userlang": "zh_CN",
            "redirect_url": "",
            "cookie_forbidden": 0,
            "cookie_cleaned": 0,
            "plugin_used": 0,
            "login_type": 3,
            "token": "",
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1
        }
        
        # 发起登录请求
        response = await proxy_wx_request(
            request,
            BIZ_LOGIN_ENDPOINT,
            params={"action": "login"},
            method="POST",
            data=login_data  # [*] 修复变量名
        )
        response.raise_for_status()
        
        # 解析响应
        result = response.json()
        
        print(f"[INFO] Bizlogin响应: base_resp.ret={result.get('base_resp', {}).get('ret')}")
        
        # 检查登录是否成功
        if result.get("base_resp", {}).get("ret") != 0:
            error_msg = result.get("base_resp", {}).get("err_msg", "登录失败")
            print(f"[ERROR] WeChat error: {error_msg}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": error_msg}
            )
        
        # 获取redirect_url中的token
        redirect_url = result.get("redirect_url", "")
        if not redirect_url:
            print(f"[ERROR] no redirect_url, response: {result}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "未获取到登录凭证"}
            )
        
        # 从URL中提取token
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(f"http://localhost{redirect_url}")
        token = parse_qs(parsed.query).get("token", [""])[0]
        
        if not token:
            print(f"[ERROR] no Token, redirect_url: {redirect_url}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "未获取到Token"}
            )
        
        # 获取Cookie：合并浏览器已有的cookie和bizlogin响应新设的cookie
        cookies = {}
        
        # 先解析浏览器在整个登录流程中累积的cookie
        browser_cookie = request.headers.get("cookie", "")
        for part in browser_cookie.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, value = part.partition("=")
                cookies[key.strip()] = value.strip()
        
        # 再用bizlogin响应中新设的cookie覆盖（这些是最新的）
        for cookie in response.cookies.jar:
            cookies[cookie.name] = cookie.value
        
        cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        
        # 获取公众号信息和FakeID（使用同一个客户端）
        common_headers = {
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        nickname = "公众号"
        fakeid = ""
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 第一步：获取公众号昵称
            info_response = await client.get(
                f"{MP_BASE_URL}/cgi-bin/home",
                params={"t": "home/index", "token": token, "lang": "zh_CN"},
                headers=common_headers
            )
            
            html = info_response.text
            import re
            nick_match = re.search(r'nick_name\s*[:=]\s*["\']([^"\']+)["\']', html)
            if nick_match:
                nickname = nick_match.group(1)
            
            # 第二步：通过昵称搜索获取FakeID
            print(f"[SEARCH] 开始获取FakeID，昵称: {nickname}")
            
            try:
                search_response = await client.get(
                    f"{MP_BASE_URL}/cgi-bin/searchbiz",
                    params={
                        "action": "search_biz",
                        "token": token,
                        "lang": "zh_CN",
                        "f": "json",
                        "ajax": 1,
                        "random": time.time(),
                        "query": nickname,
                        "begin": 0,
                        "count": 5
                    },
                    headers=common_headers
                )
                
                print(f"[API] 搜索API响应状态: {search_response.status_code}")
                search_result = search_response.json()
                print(f"[API] 搜索结果: {search_result}")
                
                if search_result.get("base_resp", {}).get("ret") == 0:
                    accounts = search_result.get("list", [])
                    print(f"[LIST] 找到 {len(accounts)} 个公众号")
                    
                    for account in accounts:
                        acc_nickname = account.get("nickname", "")
                        acc_fakeid = account.get("fakeid", "")
                        print(f"   - {acc_nickname} (fakeid: {acc_fakeid})")
                        
                        if acc_nickname == nickname:
                            fakeid = acc_fakeid
                            print(f"[OK] 匹配成功，FakeID: {fakeid}")
                            break
                    
                    if not fakeid:
                        print(f"[WARN] 未找到完全匹配的公众号，尝试使用第一个结果")
                        if accounts:
                            fakeid = accounts[0].get("fakeid", "")
                            print(f"[NOTE] 使用第一个公众号的FakeID: {fakeid}")
                else:
                    ret = search_result.get("base_resp", {}).get("ret")
                    err_msg = search_result.get("base_resp", {}).get("err_msg", "未知错误")
                    print(f"[ERROR] Search API error: ret={ret}, err_msg={err_msg}")
                    
            except Exception as e:
                print(f"[ERROR] FakeID error: {str(e)}")
                import traceback
                traceback.print_exc()
        
        # 计算过期时间（4天后，与微信实际有效期一致）
        expire_time = int((time.time() + 4 * 24 * 3600) * 1000)
        
        # 保存凭证
        auth_manager.save_credentials(
            token=token,
            cookie=cookie_str,
            fakeid=fakeid,
            nickname=nickname,
            expire_time=expire_time
        )
        
        print(f"[OK] 登录成功: {nickname} (fakeid: {fakeid})")
        print(f"   Token: {token[:20]}...")
        print(f"   Cookie已保存到.env")
        
        await webhook.notify('login_success', {
            'nickname': nickname,
            'fakeid': fakeid,
        })
        
        return {
            "success": True,
            "message": "登录成功",
            "data": {
                "nickname": nickname,
                "fakeid": fakeid,
                "token": token,
                "expire_time": expire_time
            }
        }
    
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            status_code=e.response.status_code,
            content={"success": False, "error": f"登录请求失败: {e.response.status_code}"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"登录失败: {str(e)}"}
        )

@router.post("/manual", response_model=LoginResponse, summary="手动配置登录凭证")
async def manual_login(request: LoginRequest):
    """
    手动提交登录凭证（适用于已通过其他方式获取凭证的场景）。

    **请求体参数：**
    - **token** (必填): 微信 Token
    - **cookie** (必填): 微信 Cookie
    - **fakeid** (可选): 公众号 FakeID
    - **nickname** (可选): 公众号昵称
    - **expire_time** (可选): 过期时间戳
    """
    try:
        success = auth_manager.save_credentials(
            token=request.token,
            cookie=request.cookie,
            fakeid=request.fakeid,
            nickname=request.nickname,
            expire_time=request.expire_time
        )
        
        if success:
            await webhook.notify('login_success', {
                'nickname': request.nickname or '',
                'fakeid': request.fakeid or '',
            })
            return {
                "success": True,
                "message": "登录凭证已保存"
            }
        else:
            return {
                "success": False,
                "message": "保存登录凭证失败"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")

@router.get("/info", summary="获取登录信息")
async def get_login_info():
    """
    获取当前登录用户的昵称、FakeID、过期时间等信息。
    """
    credentials = auth_manager.get_credentials()
    if credentials:
        return {
            "success": True,
            "data": {
                "nickname": credentials.get("nickname"),
                "fakeid": credentials.get("fakeid"),
                "expire_time": credentials.get("expire_time")
            }
        }
    return {
        "success": False,
        "error": "未登录"
    }
