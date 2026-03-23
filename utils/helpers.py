#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
辅助函数模块
提供各种工具函数
"""

import re
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

def html_to_text(html: str) -> str:
    """将 HTML 转为可读纯文本"""
    import html as html_module
    text = re.sub(r'<br\s*/?\s*>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|section|h[1-6]|tr|li|blockquote)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr[^>]*>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_module.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_article_url(url: str) -> Optional[Dict[str, str]]:
    """
    解析微信文章URL，提取参数
    
    Args:
        url: 微信文章URL
        
    Returns:
        包含__biz, mid, idx, sn的字典，如果解析失败返回None
    """
    try:
        # 确保是微信文章URL
        if not url or 'mp.weixin.qq.com/s' not in url:
            return None
        
        parsed = urlparse(str(url))  # 确保url是字符串
        params = parse_qs(parsed.query)
        
        __biz = params.get('__biz', [''])[0]
        mid = params.get('mid', [''])[0]
        idx = params.get('idx', [''])[0]
        sn = params.get('sn', [''])[0]
        
        # 必须有这4个参数才返回
        if not all([__biz, mid, idx, sn]):
            return None
        
        return {
            '__biz': __biz,
            'mid': mid,
            'idx': idx,
            'sn': sn
        }
    except Exception:
        return None

def get_item_show_type(html: str) -> Optional[str]:
    """提取 item_show_type 值"""
    m = re.search(r"window\.item_show_type\s*=\s*'(\d+)'", html)
    return m.group(1) if m else None


def is_image_text_message(html: str) -> bool:
    """检测是否为图文消息（item_show_type=8，类似小红书多图+文字）"""
    return get_item_show_type(html) == '8'


def is_short_content_message(html: str) -> bool:
    """检测是否为短内容/转发消息（item_show_type=10，纯文字无 js_content div）"""
    return get_item_show_type(html) == '10'


def is_audio_message(html: str) -> bool:
    """
    Detect audio articles (voice messages embedded via mpvoice / mp-common-mpaudio).
    检测是否为音频文章（包含 mpvoice 标签或音频播放器组件）。
    """
    return ('voice_encode_fileid' in html or
            '<mpvoice' in html or
            'mp-common-mpaudio' in html or
            'js_editor_audio' in html)


def _extract_image_text_content(html: str) -> Dict:
    """
    提取图文消息的内容（item_show_type=8）

    图文消息的结构与普通文章完全不同：
    - 图片在 picture_page_info_list 的 JsDecode() 中
    - 文字在 meta description 或 content_desc 中
    - 没有 #js_content div
    """
    import html as html_module

    # 提取图片 URL（从 picture_page_info_list 中的 cdn_url）
    # 页面中有两种格式:
    #   1. picture_page_info_list: [ { cdn_url: JsDecode('...'), ... } ]  (带JsDecode)
    #   2. picture_page_info_list = [ { width:..., height:..., cdn_url: '...' } ]  (简单格式)
    # 每个 item 中第一个 cdn_url 是主图，watermark_info 内的是水印，需要跳过
    images = []

    # 优先使用简单格式（第二种），更易解析且包含所有图片
    simple_list_pos = html.find('picture_page_info_list = [')
    if simple_list_pos >= 0:
        bracket_start = html.find('[', simple_list_pos)
        depth = 0
        end = bracket_start
        for end in range(bracket_start, min(bracket_start + 20000, len(html))):
            if html[end] == '[':
                depth += 1
            elif html[end] == ']':
                depth -= 1
                if depth == 0:
                    break
        block = html[bracket_start:end + 1]
        # 按顶层 { 分割，每个 item 取第一个 cdn_url（主图）
        items = re.split(r'\n\s{4,10}\{', block)
        for item in items:
            m = re.search(r"cdn_url:\s*'([^']+)'", item)
            if m:
                url = m.group(1)
                if url not in images and ('mmbiz.qpic.cn' in url or 'mmbiz.qlogo.cn' in url):
                    images.append(url)

    # 降级: 使用 JsDecode 格式
    if not images:
        jsdecode_list_match = re.search(
            r'picture_page_info_list:\s*\[', html
        )
        if jsdecode_list_match:
            block_start = jsdecode_list_match.end() - 1
            depth = 0
            end = block_start
            for end in range(block_start, min(block_start + 20000, len(html))):
                if html[end] == '[':
                    depth += 1
                elif html[end] == ']':
                    depth -= 1
                    if depth == 0:
                        break
            block = html[block_start:end + 1]
            # 按顶层 { 分割
            items = re.split(r'\n\s{10,30}\{(?=\s*\n\s*cdn_url)', block)
            for item in items:
                m = re.search(r"cdn_url:\s*JsDecode\('([^']+)'\)", item)
                if m:
                    url = m.group(1).replace('\\x26amp;', '&').replace('\\x26', '&')
                    if url not in images and ('mmbiz.qpic.cn' in url or 'mmbiz.qlogo.cn' in url):
                        images.append(url)

    # 提取文字描述
    desc = ''
    # 方法1: meta description
    desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
    if desc_match:
        desc = desc_match.group(1)
        # 处理 \x26 编码（微信的双重编码：\x26lt; -> &lt; -> <）
        desc = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), desc)
        desc = html_module.unescape(desc)
        # 二次 unescape 处理双重编码
        desc = html_module.unescape(desc)
        # 清理 HTML 标签残留
        desc = re.sub(r'<[^>]+>', '', desc)
        desc = desc.replace('\\x0a', '\n').replace('\\n', '\n')

    # 方法2: content_desc
    if not desc:
        desc_match2 = re.search(r"content_desc:\s*JsDecode\('([^']*)'\)", html)
        if desc_match2:
            desc = desc_match2.group(1)
            desc = html_module.unescape(desc)

    # 构建 HTML 内容：竖向画廊 + 文字（RSS 兼容）
    html_parts = []

    # 竖向画廊：每张图限宽，紧凑排列，兼容主流 RSS 阅读器
    if images:
        gallery_imgs = []
        for i, img_url in enumerate(images):
            gallery_imgs.append(
                f'<p style="text-align:center;margin:0 0 6px">'
                f'<img src="{img_url}" data-src="{img_url}" '
                f'style="max-width:480px;width:100%;height:auto;border-radius:4px" />'
                f'</p>'
            )
        gallery_imgs.append(
            f'<p style="text-align:center;color:#999;font-size:12px;margin:4px 0 0">'
            f'{len(images)} images'
            f'</p>'
        )
        html_parts.append('\n'.join(gallery_imgs))

    # 文字描述区域
    if desc:
        text_lines = []
        for line in desc.split('\n'):
            line = line.strip()
            if line:
                text_lines.append(
                    f'<p style="margin:0 0 8px;line-height:1.8;font-size:15px;color:#333">{line}</p>'
                )
        html_parts.append('\n'.join(text_lines))

    content = '\n'.join(html_parts)
    plain_content = desc if desc else ''

    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
    }


def _jsdecode_unescape(s: str) -> str:
    """Unescape JsDecode \\xNN sequences and HTML entities."""
    import html as html_module
    s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
    s = html_module.unescape(s)
    s = html_module.unescape(s)
    return s


def _extract_short_content(html: str) -> Dict:
    """
    Extract content from item_show_type=10 (short posts / reposts).

    Type-10 articles have no js_content div; text and metadata are inside
    JsDecode() calls in <script> tags.
    """
    import html as html_module

    # content / content_noencode (prefer content_noencode for unescaped text)
    text = ''
    for key in ('content_noencode', 'content'):
        m = re.search(rf"{key}:\s*JsDecode\('([^']*)'\)", html)
        if m and len(m.group(1)) > 10:
            text = _jsdecode_unescape(m.group(1))
            break

    # Cover / head image
    images = []
    img_m = re.search(r"round_head_img:\s*JsDecode\('([^']+)'\)", html)
    if img_m:
        img_url = _jsdecode_unescape(img_m.group(1))
        if 'mmbiz.qpic.cn' in img_url or 'wx.qlogo.cn' in img_url:
            images.append(img_url)

    # Build HTML: simple paragraphs
    html_parts = []
    if text:
        for line in text.replace('\\x0a', '\n').replace('\\n', '\n').split('\n'):
            line = line.strip()
            if line:
                safe = html_module.escape(line)
                html_parts.append(
                    f'<p style="margin:0 0 8px;line-height:1.8;font-size:15px;color:#333">{safe}</p>'
                )

    content = '\n'.join(html_parts)
    plain_content = text.replace('\\x0a', '\n').replace('\\n', '\n') if text else ''

    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
    }


def _extract_audio_content(html: str) -> Dict:
    """
    Extract audio content from WeChat voice articles.
    音频文章使用 mpvoice / mp-common-mpaudio 标签嵌入语音，
    通过 voice_encode_fileid 构造下载链接。

    Also extracts any surrounding text content from js_content.
    """
    import html as html_module
    from bs4 import BeautifulSoup

    audio_items = []

    # Pattern 1: <mpvoice voice_encode_fileid="..." name="..." .../>
    for m in re.finditer(
        r'<mpvoice[^>]*voice_encode_fileid=["\']([^"\']+)["\'][^>]*/?>',
        html, re.IGNORECASE
    ):
        fileid = m.group(1)
        name_m = re.search(r'name=["\']([^"\']*)["\']', m.group(0))
        name = html_module.unescape(name_m.group(1)) if name_m else ''
        play_length_m = re.search(r'play_length=["\'](\d+)["\']', m.group(0))
        duration = int(play_length_m.group(1)) if play_length_m else 0
        audio_url = f"https://res.wx.qq.com/voice/getvoice?mediaid={fileid}"
        audio_items.append({'name': name, 'url': audio_url, 'duration': duration})

    # Pattern 2: mp-common-mpaudio with voice_encode_fileid in data or attributes
    if not audio_items:
        for m in re.finditer(
            r'<mp-common-mpaudio[^>]*voice_encode_fileid=["\']([^"\']+)["\'][^>]*>',
            html, re.IGNORECASE
        ):
            fileid = m.group(1)
            name_m = re.search(r'name=["\']([^"\']*)["\']', m.group(0))
            name = html_module.unescape(name_m.group(1)) if name_m else ''
            play_length_m = re.search(r'play_length=["\'](\d+)["\']', m.group(0))
            duration = int(play_length_m.group(1)) if play_length_m else 0
            audio_url = f"https://res.wx.qq.com/voice/getvoice?mediaid={fileid}"
            audio_items.append({'name': name, 'url': audio_url, 'duration': duration})

    # Build HTML content
    html_parts = []

    # Extract surrounding text from js_content (some audio articles have text too)
    text_content = ''
    js_match = re.search(
        r'<div[^>]*id=["\']js_content["\'][^>]*>([\s\S]*?)</div>\s*(?:<script|<div[^>]*class=["\']rich_media_tool)',
        html, re.IGNORECASE
    )
    if js_match:
        try:
            soup = BeautifulSoup(js_match.group(1), 'html.parser')
            for tag in soup.find_all(['mpvoice', 'mp-common-mpaudio']):
                tag.decompose()
            text_content = soup.get_text(separator='\n', strip=True)
        except Exception:
            pass

    if text_content:
        for line in text_content.split('\n'):
            line = line.strip()
            if line:
                html_parts.append(f'<p style="margin:0 0 8px;line-height:1.8">{html_module.escape(line)}</p>')

    for i, audio in enumerate(audio_items):
        dur_str = ''
        if audio['duration'] > 0:
            minutes = audio['duration'] // 60
            seconds = audio['duration'] % 60
            dur_str = f' ({minutes}:{seconds:02d})'

        display_name = audio['name'] or f'Audio {i + 1}'
        html_parts.append(
            f'<div style="margin:12px 0;padding:12px 16px;background:#f6f6f6;border-radius:8px">'
            f'<p style="margin:0 0 4px;font-size:15px;font-weight:500">'
            f'{html_module.escape(display_name)}{dur_str}</p>'
            f'<a href="{audio["url"]}" style="color:#1890ff;font-size:14px">'
            f'[Play Audio / Click to Listen]</a>'
            f'</div>'
        )

    content = '\n'.join(html_parts) if html_parts else ''

    plain_parts = []
    if text_content:
        plain_parts.append(text_content)
    for i, audio in enumerate(audio_items):
        display_name = audio['name'] or f'Audio {i + 1}'
        plain_parts.append(f"[Audio] {display_name} - {audio['url']}")

    return {
        'content': content,
        'plain_content': '\n'.join(plain_parts),
        'images': [],
        'audios': audio_items,
    }


def extract_article_info(html: str, params: Optional[Dict] = None) -> Dict:
    """
    从HTML中提取文章信息

    Args:
        html: 文章HTML内容
        params: URL参数（可选，用于返回__biz等信息）

    Returns:
        文章信息字典
    """

    title = ''
    # 图文消息的标题通常在 window.msg_title 中
    title_match = (
        re.search(r'<h1[^>]*class=[^>]*rich_media_title[^>]*>([\s\S]*?)</h1>', html, re.IGNORECASE) or
        re.search(r'<h2[^>]*class=[^>]*rich_media_title[^>]*>([\s\S]*?)</h2>', html, re.IGNORECASE) or
        re.search(r"var\s+msg_title\s*=\s*'([^']+)'\.html\(false\)", html) or
        re.search(r"window\.msg_title\s*=\s*window\.title\s*=\s*'([^']*)'", html) or
        re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html) or
        re.search(r"msg_title:\s*JsDecode\('([^']+)'\)", html)
    )

    if title_match:
        title = title_match.group(1)
        title = _jsdecode_unescape(title)
        title = re.sub(r'<[^>]+>', '', title)
        title = title.replace('&quot;', '"').replace('&amp;', '&').strip()

    author = ''
    author_match = (
        re.search(r'<a[^>]*id="js_name"[^>]*>([\s\S]*?)</a>', html, re.IGNORECASE) or
        re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html) or
        re.search(r'<meta\s+property="og:article:author"\s+content="([^"]+)"', html) or
        re.search(r'<a[^>]*class=[^>]*rich_media_meta_nickname[^>]*>([^<]+)</a>', html, re.IGNORECASE)
    )

    if author_match:
        author = author_match.group(1)
        author = re.sub(r'<[^>]+>', '', author).strip()

    publish_time = 0
    time_match = (
        re.search(r'var\s+publish_time\s*=\s*"(\d+)"', html) or
        re.search(r'var\s+ct\s*=\s*"(\d+)"', html) or
        re.search(r"var\s+ct\s*=\s*'(\d+)'", html) or
        re.search(r'<em[^>]*id="publish_time"[^>]*>([^<]+)</em>', html)
    )

    if time_match:
        try:
            publish_time = int(time_match.group(1))
        except (ValueError, TypeError):
            pass

    # 检测特殊内容类型
    if is_image_text_message(html):
        img_text_data = _extract_image_text_content(html)
        content = img_text_data['content']
        images = img_text_data['images']
        plain_content = img_text_data['plain_content']
    elif is_short_content_message(html):
        short_data = _extract_short_content(html)
        content = short_data['content']
        images = short_data['images']
        plain_content = short_data['plain_content']
    elif is_audio_message(html):
        audio_data = _extract_audio_content(html)
        content = audio_data['content']
        images = audio_data['images']
        plain_content = audio_data['plain_content']
    else:
        content = ''
        images = []

        # 方法1: 匹配 id="js_content"
        content_match = re.search(r'<div[^>]*id="js_content"[^>]*>([\s\S]*?)<script[^>]*>[\s\S]*?</script>', html, re.IGNORECASE)

        if not content_match:
            # 方法2: 匹配 class包含rich_media_content
            content_match = re.search(r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>([\s\S]*?)</div>', html, re.IGNORECASE)

        if content_match and content_match.group(1):
            content = content_match.group(1).strip()
        else:
            # 方法3: 手动截取
            js_content_pos = html.find('id="js_content"')
            if js_content_pos > 0:
                start = html.find('>', js_content_pos) + 1
                script_pos = html.find('<script', start)
                if script_pos > start:
                    content = html[start:script_pos].strip()
        if content:
            # 提取data-src属性
            img_regex = re.compile(r'<img[^>]+data-src="([^"]+)"')
            for img_match in img_regex.finditer(content):
                img_url = img_match.group(1)
                if img_url not in images:
                    images.append(img_url)

            # 提取src属性
            img_regex2 = re.compile(r'<img[^>]+src="([^"]+)"')
            for img_match in img_regex2.finditer(content):
                img_url = img_match.group(1)
                if not img_url.startswith('data:') and img_url not in images:
                    images.append(img_url)

        content = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
        plain_content = html_to_text(content) if content else ''

    __biz = params.get('__biz', 'unknown') if params else 'unknown'
    publish_time_str = ''
    if publish_time > 0:
        from datetime import datetime
        dt = datetime.fromtimestamp(publish_time)
        publish_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')

    return {
        'title': title,
        'content': content,
        'plain_content': plain_content,
        'images': images,
        'author': author,
        'publish_time': publish_time,
        'publish_time_str': publish_time_str,
        '__biz': __biz
    }

def has_article_content(html: str) -> bool:
    """
    Check whether the fetched HTML likely contains article content.
    Different WeChat account types use different content containers.

    Must match actual HTML elements (id/class attributes), not random JS strings,
    to avoid false positives on WeChat verification pages (~1.9MB) that contain
    "js_content" references in their JavaScript code.
    """
    element_markers = [
        'id="js_content"',
        'class="rich_media_content',
        'class="rich_media_area_primary',
        'id="page-content"',
        'id="page_content"',
    ]
    if any(marker in html for marker in element_markers):
        return True
    if is_image_text_message(html) or is_short_content_message(html) or is_audio_message(html):
        return True
    return False


def get_client_ip(request) -> str:
    """
    Extract real client IP from request, respecting reverse proxy headers.
    Priority: X-Forwarded-For > X-Real-IP > request.client.host
    """
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def is_article_deleted(html: str) -> bool:
    """检查文章是否被删除"""
    return '已删除' in html or 'deleted' in html.lower()


def is_article_unavailable(html: str) -> bool:
    """
    Check if the article is permanently unavailable (deleted / censored / restricted).
    检查文章是否永久不可获取（删除/违规/限制）。
    """
    return get_unavailable_reason(html) is not None


def get_unavailable_reason(html: str) -> Optional[str]:
    """
    Return human-readable reason if article is permanently unavailable, else None.
    返回文章不可用的原因，如果文章正常则返回 None。
    """
    markers = [
        ("该内容已被发布者删除", "已被发布者删除"),
        ("内容已删除", "已被发布者删除"),
        ("此内容因违规无法查看", "因违规无法查看"),
        ("涉嫌违反相关法律法规和政策", "涉嫌违规被限制"),
        ("此内容发送失败无法查看", "发送失败无法查看"),
        ("该内容暂时无法查看", "暂时无法查看"),
        ("根据作者隐私设置，无法查看该内容", "作者隐私设置不可见"),
        ("接相关投诉，此内容违反", "因投诉违规被限制"),
        ("该文章已被第三方辟谣", "已被第三方辟谣"),
    ]
    for keyword, reason in markers:
        if keyword in html:
            return reason
    return None


def is_need_verification(html: str) -> bool:
    """检查是否需要验证"""
    return ('verify' in html.lower() or
            '验证' in html or
            '环境异常' in html)

def is_login_required(html: str) -> bool:
    """检查是否需要登录"""
    return '请登录' in html or 'login' in html.lower()

def time_str_to_microseconds(time_str: str) -> int:
    """
    将时间字符串转换为微秒
    
    支持格式：
    - "5s" -> 5秒
    - "1m30s" -> 1分30秒
    - "1h30m" -> 1小时30分
    - "00:01:30" -> 1分30秒
    - 直接数字 -> 微秒
    """
    if isinstance(time_str, int):
        return time_str
    
    # 尝试解析为整数（已经是微秒）
    try:
        return int(time_str)
    except ValueError:
        pass
    
    # 解析时间字符串
    total_seconds = 0
    
    # 格式：HH:MM:SS 或 MM:SS
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 3:
            total_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            total_seconds = int(parts[0]) * 60 + int(parts[1])
    else:
        # 格式：1h30m45s
        hours = re.search(r'(\d+)h', time_str)
        minutes = re.search(r'(\d+)m', time_str)
        seconds = re.search(r'(\d+)s', time_str)
        
        if hours:
            total_seconds += int(hours.group(1)) * 3600
        if minutes:
            total_seconds += int(minutes.group(1)) * 60
        if seconds:
            total_seconds += int(seconds.group(1))
    
    return total_seconds * 1000000  # 转换为微秒


