"""
RSS流式生成工具（开源版）
[2026-05-08] 使用纯字符串拼接，降低内存占用
"""

import logging
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Iterator

from utils.image_proxy import proxy_content_images, proxy_image_url

logger = logging.getLogger(__name__)


def _escape_xml(text: str) -> str:
    """转义XML特殊字符"""
    if not text:
        return ""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;"))


def _rfc822(timestamp: int) -> str:
    """Convert Unix timestamp to RFC 822 format"""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _build_item_xml(article: dict, base_url: str) -> str:
    """
    构建单个RSS item的XML字符串（纯字符串拼接）
    """
    title = article.get("title", "")
    link = article.get("link", "")
    pub_date = _rfc822(article["publish_time"]) if article.get("publish_time") else ""
    author = article.get("author", "")
    
    # 构建内容
    cover = proxy_image_url(article.get("cover", ""), base_url)
    digest = html_escape(article.get("digest", "")) if article.get("digest") else ""
    author_escaped = html_escape(author) if author else ""
    title_escaped = html_escape(title)
    
    content_html = article.get("content", "")
    if content_html:
        content_html = proxy_content_images(content_html, base_url)
    html_parts = []
    
    if content_html:
        html_parts.append(
            f'<div style="font-size:16px;line-height:1.8;color:#333">'
            f'{content_html}</div>'
        )
        if author:
            html_parts.append(
                f'<hr style="margin:24px 0;border:none;border-top:1px solid #eee" />'
                f'<p style="color:#888;font-size:13px;margin:0">作者: {author_escaped}</p>'
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
                f'作者: {author_escaped}</p>'
            )
        html_parts.append(
            f'<p style="margin:0"><a href="{html_escape(link)}" '
            f'style="color:#1890ff;text-decoration:none;font-size:14px">'
            f'阅读原文 &rarr;</a></p>'
        )
    
    description = "\n".join(html_parts)
    
    # 拼接XML
    xml_parts = ['<item>\n']
    xml_parts.append(f'  <title>{_escape_xml(title)}</title>\n')
    xml_parts.append(f'  <link>{_escape_xml(link)}</link>\n')
    xml_parts.append(f'  <guid isPermaLink="true">{_escape_xml(link)}</guid>\n')
    
    if pub_date:
        xml_parts.append(f'  <pubDate>{pub_date}</pubDate>\n')
    
    if author:
        xml_parts.append(f'  <author>{_escape_xml(author)}</author>\n')
    
    # description和content:encoded用CDATA包裹
    xml_parts.append(f'  <description><![CDATA[{description}]]></description>\n')
    xml_parts.append(f'  <content:encoded><![CDATA[{description}]]></content:encoded>\n')
    xml_parts.append('</item>\n')
    
    return "".join(xml_parts)


def generate_single_rss_stream(
    fakeid: str,
    sub: dict,
    articles: list,
    base_url: str
) -> Iterator[bytes]:
    """
    流式生成单个公众号RSS
    """
    nickname = sub.get("nickname", fakeid)
    
    # ==================== RSS 头部 ====================
    yield b'<?xml version="1.0" encoding="UTF-8"?>\n'
    yield b'<rss version="2.0" '
    yield b'xmlns:atom="http://www.w3.org/2005/Atom" '
    yield b'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
    yield b'<channel>\n'
    
    # Channel 元数据
    yield f'<title>{_escape_xml(nickname)}</title>\n'.encode('utf-8')
    yield b'<link>https://mp.weixin.qq.com</link>\n'
    yield f'<description>{_escape_xml(nickname)} - WeChat RSS</description>\n'.encode('utf-8')
    yield b'<language>zh-CN</language>\n'
    
    last_build = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    yield f'<lastBuildDate>{last_build}</lastBuildDate>\n'.encode('utf-8')
    yield b'<generator>WeChat RSS</generator>\n'
    
    # atom:link
    atom_link = f'<atom:link href="{_escape_xml(base_url)}/api/rss/{_escape_xml(fakeid)}" rel="self" type="application/rss+xml"/>\n'
    yield atom_link.encode('utf-8')
    
    # 公众号头像
    if sub.get("head_img"):
        yield b'<image>\n'
        img_url = proxy_image_url(sub["head_img"], base_url)
        yield f'  <url>{_escape_xml(img_url)}</url>\n'.encode('utf-8')
        yield f'  <title>{_escape_xml(nickname)}</title>\n'.encode('utf-8')
        yield b'  <link>https://mp.weixin.qq.com</link>\n'
        yield b'</image>\n'
    
    # ==================== 文章列表 ====================
    for article in articles:
        item_xml = _build_item_xml(article, base_url)
        yield item_xml.encode('utf-8')
    
    # ==================== RSS 尾部 ====================
    yield b'</channel>\n'
    yield b'</rss>\n'
    
    logger.info(f"[RSS Stream] Generated {len(articles)} articles for fakeid {fakeid}")


def generate_historical_rss_stream(
    fakeid: str,
    sub: dict,
    articles: list,
    base_url: str,
    page: int,
    total_pages: int,
    total_count: int
) -> Iterator[bytes]:
    """
    流式生成历史文章RSS
    """
    nickname = sub.get("nickname", fakeid)
    
    # ==================== RSS 头部 ====================
    yield b'<?xml version="1.0" encoding="UTF-8"?>\n'
    yield b'<rss version="2.0" '
    yield b'xmlns:atom="http://www.w3.org/2005/Atom" '
    yield b'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
    yield b'<channel>\n'
    
    # Channel 元数据
    title_text = f"{nickname} - 历史文章"
    if total_pages > 1:
        title_text += f" (第{page}页/共{total_pages}页)"
    
    yield f'<title>{_escape_xml(title_text)}</title>\n'.encode('utf-8')
    yield b'<link>https://mp.weixin.qq.com</link>\n'
    
    description = f"{nickname} 历史文章归档 - 共{total_count}篇"
    yield f'<description>{_escape_xml(description)}</description>\n'.encode('utf-8')
    yield b'<language>zh-CN</language>\n'
    
    last_build = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    yield f'<lastBuildDate>{last_build}</lastBuildDate>\n'.encode('utf-8')
    yield b'<generator>WeChat RSS Historical</generator>\n'
    
    # atom:link
    self_url = f"{_escape_xml(base_url)}/api/rss/{_escape_xml(fakeid)}/history?page={page}"
    yield f'<atom:link href="{self_url}" rel="self" type="application/rss+xml"/>\n'.encode('utf-8')
    
    # pagination links
    if page > 1:
        prev_url = f"{_escape_xml(base_url)}/api/rss/{_escape_xml(fakeid)}/history?page={page-1}"
        yield f'<atom:link href="{prev_url}" rel="previous" type="application/rss+xml"/>\n'.encode('utf-8')
    
    if page < total_pages:
        next_url = f"{_escape_xml(base_url)}/api/rss/{_escape_xml(fakeid)}/history?page={page+1}"
        yield f'<atom:link href="{next_url}" rel="next" type="application/rss+xml"/>\n'.encode('utf-8')
    
    # 公众号头像
    if sub.get("head_img"):
        yield b'<image>\n'
        img_url = proxy_image_url(sub["head_img"], base_url)
        yield f'  <url>{_escape_xml(img_url)}</url>\n'.encode('utf-8')
        yield f'  <title>{_escape_xml(nickname)}</title>\n'.encode('utf-8')
        yield b'  <link>https://mp.weixin.qq.com</link>\n'
        yield b'</image>\n'
    
    # ==================== 文章列表 ====================
    for article in articles:
        item_xml = _build_item_xml(article, base_url)
        yield item_xml.encode('utf-8')
    
    # ==================== RSS 尾部 ====================
    yield b'</channel>\n'
    yield b'</rss>\n'
    
    logger.info(f"[RSS Stream] Generated {len(articles)} historical articles for fakeid {fakeid} (page {page}/{total_pages})")


def generate_aggregated_rss_stream(
    articles: list,
    nickname_map: dict,
    base_url: str
) -> Iterator[bytes]:
    """
    流式生成聚合RSS（所有订阅）
    """
    
    # ==================== RSS 头部 ====================
    yield b'<?xml version="1.0" encoding="UTF-8"?>\n'
    yield b'<rss version="2.0" '
    yield b'xmlns:atom="http://www.w3.org/2005/Atom" '
    yield b'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
    yield b'<channel>\n'
    
    # Channel 元数据
    yield b'<title>WeChat RSS - \xe8\x81\x9a\xe5\x90\x88\xe8\xae\xa2\xe9\x98\x85</title>\n'  # 聚合订阅
    yield f'<link>{_escape_xml(base_url)}</link>\n'.encode('utf-8')
    yield b'<description>All subscribed WeChat articles</description>\n'
    yield b'<language>zh-CN</language>\n'
    
    last_build = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    yield f'<lastBuildDate>{last_build}</lastBuildDate>\n'.encode('utf-8')
    yield b'<generator>WeChat RSS</generator>\n'
    
    # atom:link
    atom_link = f'<atom:link href="{_escape_xml(base_url)}/api/rss/all" rel="self" type="application/rss+xml"/>\n'
    yield atom_link.encode('utf-8')
    
    # ==================== 文章列表 ====================
    for article in articles:
        fakeid = article.get("fakeid", "")
        source_name = nickname_map.get(fakeid, fakeid)
        
        # 修改标题添加来源
        article_with_source = article.copy()
        original_title = article.get("title", "")
        if source_name:
            article_with_source["title"] = f"[{source_name}] {original_title}"
        
        item_xml = _build_item_xml(article_with_source, base_url)
        yield item_xml.encode('utf-8')
    
    # ==================== RSS 尾部 ====================
    yield b'</channel>\n'
    yield b'</rss>\n'
    
    logger.info(f"[RSS Stream] Generated {len(articles)} articles for aggregated RSS")


def generate_category_rss_stream(
    category: dict,
    articles: list,
    nickname_map: dict,
    base_url: str
) -> Iterator[bytes]:
    """
    流式生成分类RSS
    """
    category_name = category.get("name", "分类")
    category_id = category.get("id", 0)
    
    # ==================== RSS 头部 ====================
    yield b'<?xml version="1.0" encoding="UTF-8"?>\n'
    yield b'<rss version="2.0" '
    yield b'xmlns:atom="http://www.w3.org/2005/Atom" '
    yield b'xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
    yield b'<channel>\n'
    
    # Channel 元数据
    title = f"WeChat RSS - {category_name}"
    yield f'<title>{_escape_xml(title)}</title>\n'.encode('utf-8')
    yield f'<link>{_escape_xml(base_url)}</link>\n'.encode('utf-8')
    yield f'<description>{_escape_xml(category_name)} RSS Feed</description>\n'.encode('utf-8')
    yield b'<language>zh-CN</language>\n'
    
    last_build = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    yield f'<lastBuildDate>{last_build}</lastBuildDate>\n'.encode('utf-8')
    yield b'<generator>WeChat RSS</generator>\n'
    
    # atom:link
    atom_link = f'<atom:link href="{_escape_xml(base_url)}/api/rss/category/{category_id}" rel="self" type="application/rss+xml"/>\n'
    yield atom_link.encode('utf-8')
    
    # ==================== 文章列表 ====================
    for article in articles:
        fakeid = article.get("fakeid", "")
        source_name = nickname_map.get(fakeid, fakeid)
        
        # 修改标题添加来源
        article_with_source = article.copy()
        original_title = article.get("title", "")
        if source_name:
            article_with_source["title"] = f"[{source_name}] {original_title}"
        
        item_xml = _build_item_xml(article_with_source, base_url)
        yield item_xml.encode('utf-8')
    
    # ==================== RSS 尾部 ====================
    yield b'</channel>\n'
    yield b'</rss>\n'
    
    logger.info(f"[RSS Stream] Generated {len(articles)} articles for category {category_id}")
