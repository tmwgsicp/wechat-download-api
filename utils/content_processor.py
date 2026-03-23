#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图文内容处理器 - 完美还原微信文章的图文混合内容
"""

import re
import logging
from typing import Dict, List
from urllib.parse import quote

logger = logging.getLogger(__name__)


def process_article_content(html: str, proxy_base_url: str = None) -> Dict:
    """
    处理文章内容，保持图文顺序并代理图片
    
    Args:
        html: 原始 HTML
        proxy_base_url: 图片代理基础 URL（例如：https://你的域名.com）
        
    Returns:
        {
            'content': '处理后的 HTML（图片已代理）',
            'plain_content': '纯文本',
            'images': ['图片URL列表'],
            'has_images': True/False
        }
    """
    
    # 1. 提取正文内容（保持原始 HTML 结构）
    content = extract_content(html)
    
    if not content:
        return {
            'content': '',
            'plain_content': '',
            'images': [],
            'has_images': False
        }
    
    # 2. 提取所有图片 URL（按顺序）
    images = extract_images_in_order(content)
    
    # 3. 代理图片 URL（保持 HTML 中的图片顺序）
    if proxy_base_url:
        content = proxy_all_images(content, proxy_base_url)
    
    # 4. 清理和优化 HTML
    content = clean_html(content)
    
    # 5. 生成纯文本
    plain_content = html_to_text(content)
    
    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
        'has_images': len(images) > 0
    }


def _extract_div_inner(html: str, open_tag_pattern: str) -> str:
    """
    Extract the inner HTML of a <div> matched by open_tag_pattern,
    correctly handling nested <div> tags by counting open/close depth.
    """
    m = re.search(open_tag_pattern, html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""

    start = m.end()
    depth = 1
    pos = start
    open_re = re.compile(r'<div[\s>/]', re.IGNORECASE)
    close_re = re.compile(r'</div\s*>', re.IGNORECASE)

    while depth > 0 and pos < len(html):
        next_open = open_re.search(html, pos)
        next_close = close_re.search(html, pos)

        if next_close is None:
            break

        if next_open and next_open.start() < next_close.start():
            depth += 1
            pos = next_open.end()
        else:
            depth -= 1
            if depth == 0:
                return html[start:next_close.start()].strip()
            pos = next_close.end()

    return html[start:].strip()


def extract_content(html: str) -> str:
    """
    Extract article body, trying multiple container patterns.
    Different WeChat account types (government, media, personal) use
    different HTML structures. We try them in order of specificity.
    For image-text messages (item_show_type=8) and short posts (item_show_type=10),
    delegates to helpers.
    """
    from utils.helpers import (
        is_image_text_message, _extract_image_text_content,
        is_short_content_message, _extract_short_content,
        is_audio_message, _extract_audio_content,
    )

    if is_image_text_message(html):
        result = _extract_image_text_content(html)
        return result.get('content', '')

    if is_short_content_message(html):
        result = _extract_short_content(html)
        return result.get('content', '')

    if is_audio_message(html):
        result = _extract_audio_content(html)
        return result.get('content', '')

    # Pattern 1: id="js_content" (most common)
    content = _extract_div_inner(html, r'<div[^>]*\bid=["\']js_content["\'][^>]*>')
    if content:
        return content

    # Pattern 2: class contains rich_media_content
    content = _extract_div_inner(html, r'<div[^>]*\bclass=["\'][^"\']*rich_media_content[^"\']*["\'][^>]*>')
    if content:
        return content

    # Pattern 3: id="page-content" (government/institutional accounts)
    content = _extract_div_inner(html, r'<div[^>]*\bid=["\']page-content["\'][^>]*>')
    if content:
        return content

    # Pattern 4: class contains rich_media_area_primary_inner
    content = _extract_div_inner(html, r'<div[^>]*\bclass=["\'][^"\']*rich_media_area_primary_inner[^"\']*["\'][^>]*>')
    if content:
        return content

    # Pattern 5: id="js_article" (alternative article container)
    content = _extract_div_inner(html, r'<div[^>]*\bid=["\']js_article["\'][^>]*>')
    if content:
        return content

    logger.warning("Failed to extract article body from any known container")
    return ""


def extract_images_in_order(content: str) -> List[str]:
    """
    按顺序提取所有图片 URL
    
    微信文章的图片有两种属性：
    1. data-src（主要）- 懒加载图片
    2. src（备用）- 直接加载图片
    """
    images = []
    
    # 提取所有 <img> 标签（按 HTML 中的顺序）
    img_pattern = re.compile(r'<img[^>]*>', re.IGNORECASE)
    
    for img_tag in img_pattern.finditer(content):
        img_html = img_tag.group(0)
        
        # 优先提取 data-src
        data_src_match = re.search(r'data-src="([^"]+)"', img_html)
        if data_src_match:
            img_url = data_src_match.group(1)
            if is_valid_image_url(img_url) and img_url not in images:
                images.append(img_url)
            continue
        
        # 备用：提取 src
        src_match = re.search(r'src="([^"]+)"', img_html)
        if src_match:
            img_url = src_match.group(1)
            if is_valid_image_url(img_url) and img_url not in images:
                images.append(img_url)
    
    logger.info(f"提取到 {len(images)} 张图片（按顺序）")
    return images


def proxy_all_images(content: str, proxy_base_url: str) -> str:
    """
    代理所有图片 URL（保持 HTML 中的图片顺序）
    
    替换策略：
    1. 提取图片URL（data-src 或 src）
    2. 替换为代理URL
    3. 确保同时有 data-src 和 src 属性（RSS阅读器需要src）
    
    重要: RSS 阅读器需要 src 属性才能显示图片!
    """
    
    def replace_img_tag(match):
        """替换单个 <img> 标签"""
        img_html = match.group(0)
        
        # 提取原始图片 URL（优先data-src，其次src）
        data_src_match = re.search(r'data-src="([^"]+)"', img_html, re.IGNORECASE)
        src_match = re.search(r'\ssrc="([^"]+)"', img_html, re.IGNORECASE)
        
        original_url = None
        if data_src_match:
            original_url = data_src_match.group(1)
        elif src_match:
            original_url = src_match.group(1)
        
        if not original_url or not is_valid_image_url(original_url):
            return img_html
        
        # 生成代理 URL
        proxy_url = f"{proxy_base_url}/api/image?url={quote(original_url, safe='')}"
        
        new_html = img_html
        
        # 第一步：替换 data-src（如果有）
        if data_src_match:
            new_html = re.sub(
                r'data-src="[^"]+"',
                f'data-src="{proxy_url}"',
                new_html,
                count=1,
                flags=re.IGNORECASE
            )
        
        # 第二步：处理 src 属性
        if src_match:
            # 已有 src，直接替换
            new_html = re.sub(
                r'\ssrc="[^"]+"',
                f' src="{proxy_url}"',
                new_html,
                count=1,
                flags=re.IGNORECASE
            )
        else:
            # 没有 src，必须添加（使用最简单可靠的方法）
            new_html = new_html.replace('<img', f'<img src="{proxy_url}"', 1)
            # 处理大写
            if 'src=' not in new_html:
                new_html = new_html.replace('<IMG', f'<IMG src="{proxy_url}"', 1)
        
        return new_html
    
    # 替换所有 <img> 标签
    content = re.sub(
        r'<img[^>]*>',
        replace_img_tag,
        content,
        flags=re.IGNORECASE
    )
    
    logger.info("图片 URL 已代理")
    return content


def is_valid_image_url(url: str) -> bool:
    """判断是否为有效的图片 URL"""
    if not url:
        return False
    
    # 排除 base64 和无效 URL
    if url.startswith('data:'):
        return False
    
    # 只保留微信 CDN 图片
    wechat_cdn_domains = [
        'mmbiz.qpic.cn',
        'mmbiz.qlogo.cn',
        'wx.qlogo.cn'
    ]
    
    return any(domain in url for domain in wechat_cdn_domains)


def clean_html(content: str) -> str:
    """
    清理和优化 HTML
    
    1. 移除 script 标签
    2. 移除 style 标签（可选）
    3. 移除空白标签
    """
    
    # 移除 <script> 标签
    content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 <style> 标签（可选，保留可以保持样式）
    # content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除空段落
    content = re.sub(r'<p[^>]*>\s*</p>', '', content, flags=re.IGNORECASE)
    
    # 移除多余空白
    content = re.sub(r'\n\s*\n', '\n', content)
    
    return content.strip()


def html_to_text(html: str) -> str:
    """将 HTML 转为纯文本（移除图片，只保留文字）"""
    import html as html_module
    
    # 移除图片标签
    text = re.sub(r'<img[^>]*>', '', html, flags=re.IGNORECASE)
    
    # 移除其他标签
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|section|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    
    # HTML 实体解码
    text = html_module.unescape(text)
    
    # 清理空白
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


# ==================== 使用示例 ====================

def example_usage():
    """使用示例"""
    
    # 假设这是从微信获取的原始 HTML
    original_html = """
    <html>
    <body>
    <div id="js_content">
        <p>这是第一段文字</p>
        <p><img data-src="https://mmbiz.qpic.cn/image1.jpg" /></p>
        <p>这是第二段文字</p>
        <p><img data-src="https://mmbiz.qpic.cn/image2.jpg" /></p>
        <p>这是第三段文字</p>
    </div>
    </body>
    </html>
    """
    
    # 处理内容
    result = process_article_content(
        html=original_html,
        proxy_base_url="https://wechatrss.waytomaster.com"
    )
    
    print("处理后的 HTML:")
    print(result['content'])
    print("\n图片列表（按顺序）:")
    for i, img in enumerate(result['images'], 1):
        print(f"  {i}. {img}")
    
    print("\n纯文本:")
    print(result['plain_content'])


if __name__ == "__main__":
    example_usage()
