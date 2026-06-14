#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
服务端远程登录流程

绕过浏览器，由服务端自己持有 cookie jar 串起整个扫码登录流程：
startlogin → getqrcode → 推企微 → 轮询 scan → bizlogin → 保存凭证。

触发方式：
- LoginReminder 检测到 critical/expired 时自动触发
- 管理面板按钮主动触发（POST /api/login/remote/start）
"""

import asyncio
import base64
import logging
import re
import time
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qs

import httpx

from utils.auth_manager import auth_manager
from utils.webhook import webhook

logger = logging.getLogger(__name__)

MP_BASE_URL = "https://mp.weixin.qq.com"
QR_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/scanloginqrcode"
BIZ_LOGIN_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/bizlogin"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 二维码有效期约 2-3 分钟，留 30s 缓冲给 bizlogin
QR_VALID_SECONDS = 150
# 轮询间隔
POLL_INTERVAL = 2.0


class RemoteLogin:
    """单飞（single-flight）的远程登录协调器。

    同一时刻只允许一个登录流程在跑，避免 cookie jar / 通知互相污染。
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active_session_id: Optional[str] = None
        self._active_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    @property
    def session_id(self) -> Optional[str]:
        return self._active_session_id

    async def start(self) -> Dict:
        """触发一次远程登录。

        返回:
            {"status": "started"|"already_running", "session_id": str}
        """
        async with self._lock:
            if self.is_running:
                logger.info("Remote login already running: %s", self._active_session_id)
                return {"status": "already_running", "session_id": self._active_session_id}

            session_id = f"remote_{int(time.time())}"
            self._active_session_id = session_id
            self._active_task = asyncio.create_task(self._run(session_id))
            logger.info("Remote login started: %s", session_id)
            return {"status": "started", "session_id": session_id}

    async def _run(self, session_id: str) -> None:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            await self._do_login(client, session_id)
        except Exception as e:
            logger.error("Remote login failed [%s]: %s", session_id, e, exc_info=True)
            await webhook.notify("login_qrcode_expired", {
                "session_id": session_id,
                "message": f"远程登录失败: {e}",
            })
        finally:
            await client.aclose()
            async with self._lock:
                if self._active_session_id == session_id:
                    self._active_session_id = None
                    self._active_task = None

    async def _do_login(self, client: httpx.AsyncClient, session_id: str) -> None:
        headers = {
            "User-Agent": UA,
            "Referer": f"{MP_BASE_URL}/",
            "Origin": MP_BASE_URL,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        # 1) startlogin —— 初始化会话，必须先于 getqrcode
        start_body = {
            "userlang": "zh_CN",
            "redirect_url": "",
            "login_type": 3,
            "sessionid": session_id,
            "token": "",
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        resp = await client.post(
            BIZ_LOGIN_ENDPOINT,
            params={"action": "startlogin"},
            data=start_body,
            headers=headers,
        )
        resp.raise_for_status()
        logger.info("[%s] startlogin ok, cookies=%d", session_id, len(client.cookies.jar))

        # 2) getqrcode —— 取二维码图片（同时会 set-cookie）
        qr_resp = await client.get(
            QR_ENDPOINT,
            params={"action": "getqrcode", "random": int(time.time() * 1000)},
            headers=headers,
        )
        qr_resp.raise_for_status()

        content = qr_resp.content
        if not (content.startswith(b"\x89PNG") or content.startswith(b"\xff\xd8\xff")):
            logger.error("[%s] getqrcode returned non-image: %s",
                         session_id, qr_resp.headers.get("content-type"))
            raise RuntimeError("获取二维码失败，响应非图片")

        # 3) 推送二维码到企微
        ok = await webhook.notify_image("login_qrcode_ready", content, {
            "session_id": session_id,
            "message": "请使用微信扫描二维码登录",
        })
        if not ok:
            logger.warning("[%s] webhook not configured or send failed; "
                           "polling will continue anyway", session_id)

        # 4) 轮询扫码状态
        deadline = time.time() + QR_VALID_SECONDS
        confirmed = False
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            scan = await client.get(
                QR_ENDPOINT,
                params={
                    "action": "ask",
                    "token": "",
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": 1,
                },
                headers=headers,
            )
            scan.raise_for_status()
            data = scan.json()

            ret = (data.get("base_resp") or {}).get("ret", -1)
            if ret != 0:
                logger.warning("[%s] scan ask ret=%s body=%s", session_id, ret, data)
                continue

            status = data.get("status", 0)
            if status == 1:  # 用户在手机上确认了
                confirmed = True
                logger.info("[%s] scan confirmed", session_id)
                break
            if status == 2:  # 二维码过期
                logger.info("[%s] qrcode expired", session_id)
                await webhook.notify("login_qrcode_expired", {
                    "session_id": session_id,
                    "message": "二维码已过期，请重新触发",
                })
                return
            # status 4/6 = 已扫码待确认；其他 = 继续等

        if not confirmed:
            logger.info("[%s] polling timed out", session_id)
            await webhook.notify("login_qrcode_expired", {
                "session_id": session_id,
                "message": "等待扫码超时，请重新触发",
            })
            return

        # 5) bizlogin —— 完成登录
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
            "ajax": 1,
        }
        login_resp = await client.post(
            BIZ_LOGIN_ENDPOINT,
            params={"action": "login"},
            data=login_data,
            headers=headers,
        )
        login_resp.raise_for_status()
        result = login_resp.json()

        if (result.get("base_resp") or {}).get("ret") != 0:
            err = (result.get("base_resp") or {}).get("err_msg", "登录失败")
            raise RuntimeError(f"bizlogin 失败: {err}")

        redirect_url = result.get("redirect_url", "")
        token = parse_qs(urlparse(f"http://localhost{redirect_url}").query).get("token", [""])[0]
        if not token:
            raise RuntimeError("bizlogin 未返回 token")

        # 6) 收集 cookie + 拉公众号信息
        cookie_str = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
        nickname, fakeid = await self._fetch_account_info(client, token, cookie_str)

        expire_time = int((time.time() + 4 * 24 * 3600) * 1000)
        auth_manager.save_credentials(
            token=token,
            cookie=cookie_str,
            fakeid=fakeid,
            nickname=nickname,
            expire_time=expire_time,
        )
        logger.info("[%s] remote login success: %s (fakeid=%s)",
                    session_id, nickname, fakeid)

        await webhook.notify("login_success", {
            "nickname": nickname,
            "fakeid": fakeid,
            "source": "remote",
        })

    async def _fetch_account_info(self, client: httpx.AsyncClient,
                                  token: str, cookie_str: str) -> tuple:
        common = {"Cookie": cookie_str, "User-Agent": UA}
        nickname = "公众号"
        fakeid = ""

        info_resp = await client.get(
            f"{MP_BASE_URL}/cgi-bin/home",
            params={"t": "home/index", "token": token, "lang": "zh_CN"},
            headers=common,
        )
        nick_match = re.search(r'nick_name\s*[:=]\s*["\']([^"\']+)["\']', info_resp.text)
        if nick_match:
            nickname = nick_match.group(1)

        try:
            search_resp = await client.get(
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
                    "count": 5,
                },
                headers=common,
            )
            result = search_resp.json()
            if (result.get("base_resp") or {}).get("ret") == 0:
                for account in result.get("list", []):
                    if account.get("nickname") == nickname:
                        fakeid = account.get("fakeid", "")
                        break
                if not fakeid and result.get("list"):
                    fakeid = result["list"][0].get("fakeid", "")
        except Exception as e:
            logger.warning("fetch fakeid failed: %s", e)

        return nickname, fakeid


remote_login = RemoteLogin()
