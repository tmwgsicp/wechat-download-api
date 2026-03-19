#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 订阅路由
订阅管理 + RSS XML 输出
"""

import csv
import io
import time
import logging
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Optional
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from utils import rss_store
from utils.rss_poller import rss_poller, POLL_INTERVAL
from utils.image_proxy import proxy_image_url

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────

class SubscribeRequest(BaseModel):
    fakeid: str = Field(..., description="公众号 FakeID")
    nickname: str = Field("", description="公众号名称")
    alias: str = Field("", description="公众号微信号")
    head_img: str = Field("", description="头像 URL")


class SubscribeResponse(BaseModel):
    success: bool
    message: str = ""


class SubscriptionItem(BaseModel):
    fakeid: str
    nickname: str
    alias: str
    head_img: str
    created_at: int
    last_poll: int
    article_count: int = 0
    rss_url: str = ""


class SubscriptionListResponse(BaseModel):
    success: bool
    data: list = []


class PollerStatusResponse(BaseModel):
    success: bool
    data: dict = {}


# ── 订阅管理 ─────────────────────────────────────────────

@router.post("/rss/subscribe", response_model=SubscribeResponse, summary="添加 RSS 订阅")
async def subscribe(req: SubscribeRequest, request: Request):
    """
    添加一个公众号到 RSS 订阅列表。

    添加后，后台轮询器会定时拉取该公众号的最新文章。

    **请求体参数：**
    - **fakeid** (必填): 公众号 FakeID，通过搜索接口获取
    - **nickname** (可选): 公众号名称
    - **alias** (可选): 公众号微信号
    - **head_img** (可选): 公众号头像 URL
    """
    added = rss_store.add_subscription(
        fakeid=req.fakeid,
        nickname=req.nickname,
        alias=req.alias,
        head_img=req.head_img,
    )
    if added:
        logger.info("RSS subscription added: %s (%s)", req.nickname, req.fakeid[:8])
        return SubscribeResponse(success=True, message="订阅成功")
    return SubscribeResponse(success=True, message="已订阅，无需重复添加")


@router.delete("/rss/subscribe/{fakeid}", response_model=SubscribeResponse,
               summary="取消 RSS 订阅")
async def unsubscribe(fakeid: str):
    """
    取消订阅一个公众号，同时删除该公众号的缓存文章。

    **路径参数：**
    - **fakeid**: 公众号 FakeID
    """
    removed = rss_store.remove_subscription(fakeid)
    if removed:
        logger.info("RSS subscription removed: %s", fakeid[:8])
        return SubscribeResponse(success=True, message="已取消订阅")
    return SubscribeResponse(success=False, message="未找到该订阅")


@router.get("/rss/subscriptions", response_model=SubscriptionListResponse,
            summary="获取订阅列表")
async def get_subscriptions(request: Request):
    """
    获取当前所有 RSS 订阅的公众号列表。

    返回每个订阅的基本信息、缓存文章数和 RSS 地址。
    """
    subs = rss_store.list_subscriptions()
    base_url = str(request.base_url).rstrip("/")

    items = []
    for s in subs:
        # 将头像 URL 转换为代理链接
        head_img = proxy_image_url(s.get("head_img", ""), base_url)
        items.append({
            **s,
            "head_img": head_img,
            "rss_url": f"{base_url}/api/rss/{s['fakeid']}",
        })

    return SubscriptionListResponse(success=True, data=items)


@router.post("/rss/poll", response_model=PollerStatusResponse,
             summary="手动触发轮询")
async def trigger_poll():
    """
    手动触发一次轮询，立即拉取所有订阅公众号的最新文章。

    通常用于首次订阅后立即获取文章，无需等待下一个轮询周期。
    """
    if not rss_poller.is_running:
        return PollerStatusResponse(
            success=False,
            data={"message": "轮询器未启动"}
        )
    try:
        await rss_poller.poll_now()
        return PollerStatusResponse(
            success=True,
            data={"message": "轮询完成"}
        )
    except Exception as e:
        return PollerStatusResponse(
            success=False,
            data={"message": f"轮询出错: {str(e)}"}
        )


@router.get("/rss/status", response_model=PollerStatusResponse,
            summary="轮询器状态")
async def poller_status():
    """
    获取 RSS 轮询器运行状态。
    """
    subs = rss_store.list_subscriptions()
    return PollerStatusResponse(
        success=True,
        data={
            "running": rss_poller.is_running,
            "poll_interval": POLL_INTERVAL,
            "subscription_count": len(subs),
        },
    )


# ── 聚合 RSS ─────────────────────────────────────────────

@router.get("/rss/all", summary="聚合 RSS 订阅源",
            response_class=Response)
async def get_aggregated_rss_feed(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="文章数量上限"),
):
    """
    获取所有订阅公众号的聚合 RSS 2.0 订阅源。

    将此地址添加到 RSS 阅读器，即可在一个订阅源中查看所有公众号文章。
    订阅增减后自动生效，无需更换链接。
    """
    subs = rss_store.list_subscriptions()
    nickname_map = {s["fakeid"]: s.get("nickname") or s["fakeid"] for s in subs}

    articles = rss_store.get_all_articles(limit=limit) if subs else []

    base_url = str(request.base_url).rstrip("/")
    xml = _build_aggregated_rss_xml(articles, nickname_map, base_url)
    return Response(
        content=xml,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


# ── 导出 ─────────────────────────────────────────────────

@router.get("/rss/export", summary="导出订阅列表")
async def export_subscriptions(
    request: Request,
    format: str = Query("csv", regex="^(csv|opml)$", description="导出格式: csv 或 opml"),
):
    """
    导出当前订阅列表。

    - **csv**: 包含公众号名称、FakeID、RSS 地址、文章数、订阅时间
    - **opml**: 标准 OPML 格式，可直接导入 RSS 阅读器
    """
    subs = rss_store.list_subscriptions()
    base_url = str(request.base_url).rstrip("/")

    if format == "opml":
        return _build_opml_response(subs, base_url)
    return _build_csv_response(subs, base_url)


def _build_csv_response(subs: list, base_url: str) -> Response:
    buf = io.StringIO()
    buf.write('\ufeff')
    writer = csv.writer(buf)
    writer.writerow(["Name", "FakeID", "RSS URL", "Articles", "Subscribed At"])
    for s in subs:
        rss_url = f"{base_url}/api/rss/{s['fakeid']}"
        sub_date = datetime.fromtimestamp(
            s.get("created_at", 0), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        writer.writerow([
            s.get("nickname") or s["fakeid"],
            s["fakeid"],
            rss_url,
            s.get("article_count", 0),
            sub_date,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="wechat_rss_subscriptions.csv"'},
    )


def _build_opml_response(subs: list, base_url: str) -> Response:
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "WeChat RSS Subscriptions"
    ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    body = ET.SubElement(opml, "body")
    group = ET.SubElement(body, "outline", text="WeChat RSS", title="WeChat RSS")

    for s in subs:
        name = s.get("nickname") or s["fakeid"]
        rss_url = f"{base_url}/api/rss/{s['fakeid']}"
        ET.SubElement(group, "outline", **{
            "type": "rss",
            "text": name,
            "title": name,
            "xmlUrl": rss_url,
            "htmlUrl": "https://mp.weixin.qq.com",
            "description": f"{name} - WeChat RSS",
        })

    xml_str = ET.tostring(opml, encoding="unicode", xml_declaration=False)
    content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="wechat_rss_subscriptions.opml"'},
    )


# ── RSS XML 输出 ──────────────────────────────────────────

def _rfc822(ts: int) -> str:
    """Unix 时间戳 → RFC 822 日期字符串"""
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _build_rss_xml(fakeid: str, sub: dict, articles: list,
                   base_url: str) -> str:
    """
    构建 RSS XML，使用 CDATA 包裹 HTML 内容
    """
    from xml.dom import minidom
    
    # 创建 XML 文档
    doc = minidom.Document()
    
    # 创建根元素
    rss = doc.createElement("rss")
    rss.setAttribute("version", "2.0")
    rss.setAttribute("xmlns:atom", "http://www.w3.org/2005/Atom")
    doc.appendChild(rss)
    
    # 创建 channel
    channel = doc.createElement("channel")
    rss.appendChild(channel)
    
    # Channel 基本信息
    def add_text_element(parent, tag, text):
        elem = doc.createElement(tag)
        elem.appendChild(doc.createTextNode(str(text)))
        parent.appendChild(elem)
        return elem
    
    add_text_element(channel, "title", sub.get("nickname") or fakeid)
    add_text_element(channel, "link", "https://mp.weixin.qq.com")
    add_text_element(channel, "description", 
                     f'{sub.get("nickname", "")} 的微信公众号文章 RSS 订阅')
    add_text_element(channel, "language", "zh-CN")
    add_text_element(channel, "lastBuildDate", _rfc822(int(time.time())))
    add_text_element(channel, "generator", "WeChat Download API")
    
    # atom:link
    atom_link = doc.createElement("atom:link")
    atom_link.setAttribute("href", f"{base_url}/api/rss/{fakeid}")
    atom_link.setAttribute("rel", "self")
    atom_link.setAttribute("type", "application/rss+xml")
    channel.appendChild(atom_link)
    
    # Channel 图片
    if sub.get("head_img"):
        image = doc.createElement("image")
        head_img_proxied = proxy_image_url(sub["head_img"], base_url)
        add_text_element(image, "url", head_img_proxied)
        add_text_element(image, "title", sub.get("nickname", ""))
        add_text_element(image, "link", "https://mp.weixin.qq.com")
        channel.appendChild(image)
    
    # 文章列表
    for a in articles:
        item = doc.createElement("item")
        
        add_text_element(item, "title", a.get("title", ""))
        
        link = a.get("link", "")
        add_text_element(item, "link", link)
        
        guid = doc.createElement("guid")
        guid.setAttribute("isPermaLink", "true")
        guid.appendChild(doc.createTextNode(link))
        item.appendChild(guid)
        
        if a.get("publish_time"):
            add_text_element(item, "pubDate", _rfc822(a["publish_time"]))
        
        if a.get("author"):
            add_text_element(item, "author", a["author"])
        
        # 构建 description HTML
        cover = proxy_image_url(a.get("cover", ""), base_url)
        digest = html_escape(a.get("digest", "")) if a.get("digest") else ""
        author = html_escape(a.get("author", "")) if a.get("author") else ""
        title_escaped = html_escape(a.get("title", ""))
        
        content_html = a.get("content", "")
        html_parts = []
        
        if content_html:
            # 统一策略:入库时已代理(见utils/rss_poller.py:236),RSS输出时直接使用
            html_parts.append(
                f'<div style="font-size:16px;line-height:1.8;color:#333">'
                f'{content_html}'
                f'</div>'
            )
            if author:
                html_parts.append(
                    f'<hr style="margin:24px 0;border:none;border-top:1px solid #eee" />'
                    f'<p style="color:#888;font-size:13px;margin:0">作者: {author}</p>'
                )
        else:
            if cover:
                html_parts.append(
                    f'<div style="margin-bottom:12px">'
                    f'<a href="{html_escape(link)}">'
                    f'<img src="{html_escape(cover)}" alt="{title_escaped}" '
                    f'style="max-width:100%;height:auto;border-radius:8px" />'
                    f'</a></div>'
                )
            if digest:
                html_parts.append(
                    f'<p style="color:#333;font-size:15px;line-height:1.8;'
                    f'margin:0 0 16px">{digest}</p>'
                )
            if author:
                html_parts.append(
                    f'<p style="color:#888;font-size:13px;margin:0 0 12px">'
                    f'作者: {author}</p>'
                )
            html_parts.append(
                f'<p style="margin:0"><a href="{html_escape(link)}" '
                f'style="color:#1890ff;text-decoration:none;font-size:14px">'
                f'阅读原文 &rarr;</a></p>'
            )
        
        # 使用 CDATA 包裹 HTML 内容
        description = doc.createElement("description")
        cdata = doc.createCDATASection("\n".join(html_parts))
        description.appendChild(cdata)
        item.appendChild(description)
        
        channel.appendChild(item)
    
    # 生成 XML 字符串
    xml_str = doc.toprettyxml(indent="  ", encoding=None)
    
    # 移除多余的空行和 XML 声明（我们自己添加）
    lines = [line for line in xml_str.split('\n') if line.strip()]
    xml_str = '\n'.join(lines[1:])  # 跳过默认的 XML 声明
    
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str


@router.get("/rss/{fakeid}", summary="获取 RSS 订阅源",
            response_class=Response)
async def get_rss_feed(fakeid: str, request: Request,
                       limit: int = Query(20, ge=1, le=100,
                                          description="文章数量上限")):
    """
    获取指定公众号的 RSS 2.0 订阅源（XML 格式）。

    将此地址添加到任何 RSS 阅读器即可订阅公众号文章。

    **路径参数：**
    - **fakeid**: 公众号 FakeID

    **查询参数：**
    - **limit** (可选): 返回文章数量上限，默认 20
    """
    sub = rss_store.get_subscription(fakeid)
    if not sub:
        raise HTTPException(status_code=404, detail="未找到该订阅，请先添加订阅")

    articles = rss_store.get_articles(fakeid, limit=limit)
    base_url = str(request.base_url).rstrip("/")
    xml = _build_rss_xml(fakeid, sub, articles, base_url)

    return Response(
        content=xml,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )


# ── 聚合 RSS XML 构建 ────────────────────────────────────

def _build_aggregated_rss_xml(articles: list, nickname_map: dict,
                               base_url: str) -> str:
    """Build aggregated RSS XML across all subscriptions."""
    from xml.dom import minidom

    doc = minidom.Document()
    rss = doc.createElement("rss")
    rss.setAttribute("version", "2.0")
    rss.setAttribute("xmlns:atom", "http://www.w3.org/2005/Atom")
    doc.appendChild(rss)

    channel = doc.createElement("channel")
    rss.appendChild(channel)

    def add_text(parent, tag, text):
        elem = doc.createElement(tag)
        elem.appendChild(doc.createTextNode(str(text)))
        parent.appendChild(elem)
        return elem

    add_text(channel, "title", "WeChat RSS - All Subscriptions")
    add_text(channel, "link", base_url)
    add_text(channel, "description", "Aggregated feed of all subscribed WeChat accounts")
    add_text(channel, "language", "zh-CN")
    add_text(channel, "lastBuildDate", _rfc822(int(time.time())))
    add_text(channel, "generator", "WeChat Download API")

    atom_link = doc.createElement("atom:link")
    atom_link.setAttribute("href", f"{base_url}/api/rss/all")
    atom_link.setAttribute("rel", "self")
    atom_link.setAttribute("type", "application/rss+xml")
    channel.appendChild(atom_link)

    for a in articles:
        item = doc.createElement("item")
        source_name = nickname_map.get(a.get("fakeid", ""), "")
        title_text = a.get("title", "")
        if source_name:
            title_text = f"[{source_name}] {title_text}"

        add_text(item, "title", title_text)

        link = a.get("link", "")
        add_text(item, "link", link)

        guid = doc.createElement("guid")
        guid.setAttribute("isPermaLink", "true")
        guid.appendChild(doc.createTextNode(link))
        item.appendChild(guid)

        if a.get("publish_time"):
            add_text(item, "pubDate", _rfc822(a["publish_time"]))

        if a.get("author"):
            add_text(item, "author", a["author"])

        cover = proxy_image_url(a.get("cover", ""), base_url)
        digest = html_escape(a.get("digest", "")) if a.get("digest") else ""
        author = html_escape(a.get("author", "")) if a.get("author") else ""
        title_escaped = html_escape(a.get("title", ""))
        content_html = a.get("content", "")
        html_parts = []

        if content_html:
            html_parts.append(
                f'<div style="font-size:16px;line-height:1.8;color:#333">'
                f'{content_html}</div>'
            )
            if author:
                html_parts.append(
                    f'<hr style="margin:24px 0;border:none;border-top:1px solid #eee" />'
                    f'<p style="color:#888;font-size:13px;margin:0">author: {author}</p>'
                )
        else:
            if cover:
                html_parts.append(
                    f'<div style="margin-bottom:12px">'
                    f'<a href="{html_escape(link)}">'
                    f'<img src="{html_escape(cover)}" alt="{title_escaped}" '
                    f'style="max-width:100%;height:auto;border-radius:8px" /></a></div>'
                )
            if digest:
                html_parts.append(
                    f'<p style="color:#333;font-size:15px;line-height:1.8;'
                    f'margin:0 0 16px">{digest}</p>'
                )
            if author:
                html_parts.append(
                    f'<p style="color:#888;font-size:13px;margin:0 0 12px">'
                    f'author: {author}</p>'
                )
            html_parts.append(
                f'<p style="margin:0"><a href="{html_escape(link)}" '
                f'style="color:#1890ff;text-decoration:none;font-size:14px">'
                f'Read &rarr;</a></p>'
            )

        description = doc.createElement("description")
        cdata = doc.createCDATASection("\n".join(html_parts))
        description.appendChild(cdata)
        item.appendChild(description)

        channel.appendChild(item)

    xml_str = doc.toprettyxml(indent="  ", encoding=None)
    lines = [line for line in xml_str.split('\n') if line.strip()]
    xml_str = '\n'.join(lines[1:])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
