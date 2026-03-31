#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
登录过期提醒（开源版）
定期检查本地微信登录凭证过期状态，提前 webhook 通知。
"""

import asyncio
import logging
import time
from typing import Optional
from utils.webhook import webhook

logger = logging.getLogger(__name__)


class LoginReminder:
    """登录过期提醒管理器（开源版单账号架构）"""
    
    def __init__(self):
        self.check_interval = 6 * 3600  # 每 6 小时检查一次
        self.warning_threshold = 24 * 3600  # 提前 24 小时预警
        self.critical_threshold = 6 * 3600  # 提前 6 小时严重警告
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_warning_level = None  # 记录最后一次警告级别，避免重复

    async def start(self):
        """启动提醒服务"""
        if self._running:
            logger.warning("登录提醒服务已在运行")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("登录提醒服务已启动，检查间隔: %d 秒", self.check_interval)

    async def stop(self):
        """停止提醒服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("登录提醒服务已停止")

    async def _run(self):
        """后台任务循环"""
        while self._running:
            try:
                await self._check_login_status()
            except Exception as e:
                logger.error("检查登录状态失败: %s", e, exc_info=True)
            
            await asyncio.sleep(self.check_interval)

    async def _check_login_status(self):
        """检查本地登录凭证的过期状态"""
        from utils.auth_manager import auth_manager
        
        # 获取凭证信息
        creds = auth_manager.get_credentials()
        if not creds or not creds.get("token"):
            logger.debug("无登录凭证，跳过检查")
            return
        
        expire_time = creds.get("expire_time", 0)
        if expire_time <= 0:
            logger.debug("凭证无过期时间，跳过检查")
            return
        
        nickname = creds.get("nickname", "未知账号")
        now = int(time.time() * 1000)  # 毫秒时间戳
        time_left_ms = expire_time - now
        time_left_sec = time_left_ms / 1000
        
        # 已过期
        if time_left_sec <= 0:
            if self._last_warning_level != 'expired':
                await self._notify_expired(nickname)
                self._last_warning_level = 'expired'
            return
        
        # 严重警告（6 小时内过期）
        if time_left_sec <= self.critical_threshold:
            if self._last_warning_level not in ['critical', 'expired']:
                await self._notify_critical(nickname, time_left_sec)
                self._last_warning_level = 'critical'
            return
        
        # 一般警告（24 小时内过期）
        if time_left_sec <= self.warning_threshold:
            if self._last_warning_level not in ['warning', 'critical', 'expired']:
                await self._notify_warning(nickname, time_left_sec)
                self._last_warning_level = 'warning'
            return
        
        # 状态正常，重置警告级别
        if self._last_warning_level is not None:
            self._last_warning_level = None
            logger.info("登录状态已恢复正常: %s", nickname)

    async def _notify_warning(self, nickname: str, time_left: float):
        """发送一般警告通知"""
        hours = time_left / 3600
        logger.warning(
            "登录凭证即将过期 [%s] - 剩余 %.1f 小时",
            nickname, hours
        )
        
        await webhook.notify('login_expiring_soon', {
            'nickname': nickname,
            'hours_left': round(hours, 1),
            'level': 'warning',
            'message': f'登录凭证将在 {round(hours, 1)} 小时后过期，请及时重新登录',
        })

    async def _notify_critical(self, nickname: str, time_left: float):
        """发送严重警告通知"""
        hours = time_left / 3600
        logger.error(
            "登录凭证即将过期（紧急）[%s] - 剩余 %.1f 小时",
            nickname, hours
        )
        
        await webhook.notify('login_expiring_critical', {
            'nickname': nickname,
            'hours_left': round(hours, 1),
            'level': 'critical',
            'message': f'登录凭证将在 {round(hours, 1)} 小时后过期（紧急），请立即重新登录',
        })

    async def _notify_expired(self, nickname: str):
        """发送已过期通知"""
        logger.error("登录凭证已过期 [%s]", nickname)
        
        await webhook.notify('login_expired', {
            'nickname': nickname,
            'message': '登录凭证已过期，API 功能将受限，请重新登录',
        })


# 全局单例
login_reminder = LoginReminder()
