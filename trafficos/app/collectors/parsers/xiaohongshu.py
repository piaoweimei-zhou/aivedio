#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书视频解析器
支持小红书短链接和长链接，解析无水印视频地址和元数据
"""

import json
import logging
import re
from typing import Optional

import requests

from ..parser_base import BaseParser, ParseResult
from ..parser_base.exceptions import ParseError

logger = logging.getLogger(__name__)


class XiaohongshuParser(BaseParser):
    """
    小红书视频解析器

    支持的URL格式：
    - 短链接：https://xhslink.com/xxxxxx
    - 探索页：https://www.xiaohongshu.com/explore/xxxxxx
    - 发现页：https://www.xiaohongshu.com/discovery/item/xxxxxx
    - 笔记页：https://www.xiaohongshu.com/note/xxxxxx
    """

    PLATFORM_NAME = "xiaohongshu"
    PLATFORM_DISPLAY_NAME = "小红书"
    SUPPORTED_DOMAINS = [
        "xhslink.com",
        "www.xiaohongshu.com",
        "xiaohongshu.com",
        "xhslink",
    ]

    # 笔记ID提取正则（按优先级）
    VIDEO_ID_PATTERNS = [
        r"/explore/([a-f0-9]+)",  # /explore/xxx
        r"/discovery/item/([a-f0-9]+)",  # /discovery/item/xxx
        r"/note/([a-f0-9]+)",  # /note/xxx
        r"/item/([a-f0-9]+)",  # /item/xxx
        r"noteId=([a-f0-9]+)",  # ?noteId=xxx
        r"id=([a-f0-9]{24})",  # ?id=xxx (24位hex)
    ]

    DESKTOP_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.DESKTOP_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

    def parse(self, url: str) -> ParseResult:
        """解析小红书视频链接"""
        try:
            # 从混合文本中提取URL（支持小红书APP分享的完整文本）
            clean_url = self._extract_url_from_text(url)
            if not clean_url:
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="未找到有效的视频链接",
                    error_code=1001,
                )

            if not self.supports(clean_url):
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="不支持的小红书链接格式",
                    error_code=1002,
                )

            resolved_url = self._resolve_if_short(clean_url)
            note_id = self._extract_video_id(resolved_url, self.VIDEO_ID_PATTERNS)
            if not note_id:
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="无法从链接中提取笔记ID",
                    error_code=1003,
                )

            logger.info(f"小红书解析: note_id={note_id}")

            result = self._fetch_from_page(note_id, resolved_url, url)
            return result

        except ParseError as e:
            logger.error(f"小红书解析异常: {e}")
            return ParseResult.error_result(
                url=url, platform=self.PLATFORM_NAME, error=str(e), error_code=e.error_code
            )
        except Exception as e:
            logger.error(f"小红书解析未知异常: {e}", exc_info=True)
            return ParseResult.error_result(
                url=url, platform=self.PLATFORM_NAME, error=f"解析失败: {str(e)}", error_code=-1
            )

    def _resolve_if_short(self, url: str) -> str:
        """如果是短链接，解析为长链接"""
        if "xhslink.com" in url:
            logger.debug(f"解析小红书短链接: {url}")
            return self._resolve_short_url(url)
        # xhslink (no .com) short links also redirect to full URLs
        if re.search(r"xhslink\.(?!com)", url):
            logger.debug(f"解析小红书短链接: {url}")
            return self._resolve_short_url(url)
        return url

    def _fetch_from_page(self, note_id: str, page_url: str, original_url: str) -> ParseResult:
        """从笔记页面HTML中提取视频信息"""
        try:
            if not page_url or "xiaohongshu.com" not in page_url:
                page_url = f"https://www.xiaohongshu.com/explore/{note_id}"

            # 先访问首页种cookie（a1等），再请求笔记页，确保拿到完整INITIAL_STATE
            try:
                self.session.get("https://www.xiaohongshu.com/", timeout=10)
            except Exception as e:
                logger.debug(f"预热首页失败(忽略): {e}")

            response = self.session.get(page_url, timeout=20)

            if response.status_code != 200:
                return ParseResult.error_result(
                    url=original_url,
                    platform=self.PLATFORM_NAME,
                    error=f"页面请求失败: HTTP {response.status_code}",
                    error_code=1005,
                )

            html = response.text

            # 从HTML中提取嵌入的JSON数据（window.__INITIAL_STATE__）
            note_data = self._extract_note_from_html(html, note_id)

            if note_data:
                return self._parse_note(note_data, note_id, original_url)

            # 降级：从HTML中直接提取视频地址
            video_url = self._extract_video_url_from_html(html)
            if video_url:
                return ParseResult.success_result(
                    url=original_url,
                    platform=self.PLATFORM_NAME,
                    video_url=video_url,
                    video_id=note_id,
                    title=f"小红书笔记_{note_id}",
                    raw_data={"source": "page_html_direct"},
                )

            # 最终降级：Playwright headless render for pages that block non-browser clients
            return self._fetch_via_playwright(note_id, page_url, original_url)

        except Exception as e:
            logger.error(f"从页面提取失败: {e}", exc_info=True)
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error=f"页面解析失败: {str(e)}",
                error_code=-1,
            )

    def _fetch_via_playwright(self, note_id: str, page_url: str, original_url: str) -> ParseResult:
        """Fallback: use Playwright to render the page and extract INITIAL_STATE."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="无法从页面提取笔记信息（可能是图文笔记或需要登录）",
                error_code=1005,
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context(user_agent=self.DESKTOP_UA)
                page = context.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
                # Wait for INITIAL_STATE to populate
                for _ in range(15):
                    try:
                        state = page.evaluate("window.__INITIAL_STATE__")
                        if state:
                            note = self._find_note_in_state(state, note_id)
                            if note:
                                browser.close()
                                return self._parse_note(note, note_id, original_url)
                    except Exception:
                        pass
                    page.wait_for_timeout(1_000)
                html = page.content()
                browser.close()

            # Try to extract from the rendered HTML
            note_data = self._extract_note_from_html(html, note_id)
            if note_data:
                return self._parse_note(note_data, note_id, original_url)

            video_url = self._extract_video_url_from_html(html)
            if video_url:
                return ParseResult.success_result(
                    url=original_url,
                    platform=self.PLATFORM_NAME,
                    video_url=video_url,
                    video_id=note_id,
                    title=f"小红书笔记_{note_id}",
                    raw_data={"source": "playwright_html_direct"},
                )

            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="无法从页面提取笔记信息（可能是图文笔记或需要 login）",
                error_code=1005,
            )
        except Exception as e:
            logger.warning(f"Playwright fallback failed: {e}")
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="无法从页面提取笔记信息（可能是图文笔记或需要 login）",
                error_code=1005,
            )

    def _extract_note_from_html(self, html: str, note_id: str) -> Optional[dict]:
        """从HTML中提取笔记数据"""
        try:
            # 方法1: 从window.__INITIAL_STATE__提取
            initial_match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.DOTALL
            )
            if initial_match:
                try:
                    raw = initial_match.group(1)
                    # XHS嵌入的JSON里含有JS关键字undefined，需替换后才能json.loads
                    data = json.loads(re.sub(r"\bundefined\b", "null", raw))
                    note = self._find_note_in_state(data, note_id)
                    if note:
                        return note
                except (json.JSONDecodeError, ValueError):
                    pass

            # 方法2: 从__REDUX_STATE__提取
            redux_match = re.search(r"window\.__REDUX_STATE__\s*=\s*(\{.*?\});", html, re.DOTALL)
            if redux_match:
                try:
                    data = json.loads(redux_match.group(1))
                    note = self._find_note_in_state(data, note_id)
                    if note:
                        return note
                except (json.JSONDecodeError, ValueError):
                    pass

            # 方法3: 从RENDER_DATA提取
            render_match = re.search(
                r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.DOTALL
            )
            if render_match:
                try:
                    import urllib.parse

                    json_str = render_match.group(1)
                    if "%" in json_str[:20]:
                        json_str = urllib.parse.unquote(json_str)
                    data = json.loads(json_str)
                    note = self._find_note_in_state(data, note_id)
                    if note:
                        return note
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

            return None

        except Exception as e:
            logger.debug(f"从HTML提取note失败: {e}")
            return None

    def _find_note_in_state(self, data: dict, note_id: str) -> Optional[dict]:
        """从状态数据中查找笔记"""
        try:
            # 优先走标准路径：note.noteDetailMap.<id>.note
            detail_map = (data.get("note") or {}).get("noteDetailMap") or {}
            entry = detail_map.get(note_id)
            if isinstance(entry, dict):
                note = entry.get("note")
                if isinstance(note, dict) and note:
                    return note

            def find_note(obj):
                if isinstance(obj, dict):
                    if obj.get("id") == note_id or obj.get("noteId") == note_id:
                        return obj
                    for value in obj.values():
                        result = find_note(value)
                        if result:
                            return result
                elif isinstance(obj, list):
                    for item in obj:
                        result = find_note(item)
                        if result:
                            return result
                return None

            return find_note(data)
        except Exception:
            return None

    def _extract_video_url_from_html(self, html: str) -> str:
        """从HTML中直接提取视频地址"""
        patterns = [
            r'"(https?://[^"]+\.mp4[^"]*)"',
            r'(https?://[^"\'\\]+\.mp4[^"\'\\]*)',
            r'video["\s:]+["\']([^"\']+)',
            r'media["\s:]+["\']([^"\']*\.mp4[^"\']*)[\'\]]',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                url = match.group(1)
                if url and ".mp4" in url and "sns-video" in url:
                    return url
        # 如果没找到sns-video的，返回第一个mp4
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                url = match.group(1)
                if url and ".mp4" in url:
                    return url
        return ""

    def _parse_note(self, note: dict, note_id: str, original_url: str) -> ParseResult:
        """从笔记数据中解析视频信息"""
        # 基本信息
        nid = str(note.get("id", note.get("noteId", note_id)))
        title = note.get("title", "") or note.get("desc", "") or ""
        desc = note.get("desc", "") or note.get("description", "") or ""
        create_time = note.get("time", 0) or note.get("createTime", 0)

        # 笔记类型：normal=图文, video=视频
        note_type = note.get("type", "") or note.get("noteType", "")
        is_video = note_type == "video" or "video" in str(note_type).lower()

        # 作者信息
        user = note.get("user", {}) or note.get("author", {})
        author_name = user.get("nickname", "") or user.get("name", "") or ""
        author_id = str(user.get("userId", "") or user.get("id", ""))
        author_avatar = self._safe_get(user, "avatar", default="")
        if not author_avatar:
            author_avatar = user.get("image", "")

        # 视频信息
        video = note.get("video", {}) or {}
        duration = self._parse_int(video.get("duration", 0))
        width = self._parse_int(video.get("width", 0))
        height = self._parse_int(video.get("height", 0))

        # 视频地址（新版结构：video.media.stream.h264[*].masterUrl）
        video_url, video_url_list = self._extract_video_urls(video)

        # 封面图
        cover_url = self._safe_get(video, "cover", "url", default="")
        if not cover_url:
            cover_url = self._safe_get(video, "thumbnail", "url", default="")
        if not cover_url:
            cover_url = self._safe_get(note, "imageList", 0, "url", default="")

        # 背景音乐
        music = note.get("music", {}) or video.get("music", {})
        music_url = self._safe_get(music, "playUrl", default="")
        music_name = music.get("title", "") or music.get("name", "") or ""

        # 互动数据
        interect = note.get("interactInfo", {}) or {}
        likes = self._parse_int(
            interect.get("likedCount", 0) or note.get("likedCount", 0) or note.get("likeCount", 0)
        )
        comments = self._parse_int(interect.get("commentCount", 0) or note.get("commentCount", 0))
        shares = self._parse_int(interect.get("shareCount", 0) or note.get("shareCount", 0))
        collects = self._parse_int(
            interect.get("collectedCount", 0)
            or note.get("collectedCount", 0)
            or note.get("collectCount", 0)
        )

        # 标签/话题
        tags = []
        topics = []
        tag_list = note.get("tagList", [])
        if isinstance(tag_list, list):
            for tag in tag_list:
                if isinstance(tag, dict):
                    tag_name = tag.get("name", "")
                    if tag_name:
                        tags.append(tag_name)
                        topics.append(tag_name)

        # 质量判断
        quality = self._judge_quality(width, height)

        # 如果不是视频笔记，返回提示
        if not is_video and not video_url:
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="该笔记为图文笔记，不含视频",
                error_code=1006,
            )

        result = ParseResult.success_result(
            url=original_url,
            platform=self.PLATFORM_NAME,
            video_url=video_url,
            video_id=nid,
            title=title[:100] if title else f"小红书视频_{nid}",
            description=desc,
            author=author_name,
            author_id=author_id,
            author_avatar=author_avatar,
            video_url_list=video_url_list,
            cover_url=cover_url,
            music_url=music_url,
            music_name=music_name,
            duration=self._format_duration(duration),
            width=width,
            height=height,
            quality=quality,
            likes=likes,
            comments=comments,
            shares=shares,
            collects=collects,
            tags=tags,
            topics=topics,
            create_time=create_time,
            raw_data=note,
        )

        logger.info(f"小红书解析成功: id={nid}, title={result.title[:30]}..., likes={likes}")
        return result

    def _extract_video_urls(self, video: dict):
        """从新版video结构中抽取视频地址：(最佳地址, 全部候选)"""
        urls = []

        # 新结构：video.media.stream.{h264,h265,av1}[{masterUrl, backupUrls[]}]
        media = video.get("media") if isinstance(video, dict) else None
        stream = (media or {}).get("stream") or {}
        for codec in ("h264", "h265", "av1"):
            entries = stream.get(codec) or []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                master = entry.get("masterUrl") or ""
                if master and master not in urls:
                    urls.append(master)
                for b in entry.get("backupUrls") or []:
                    if b and b not in urls:
                        urls.append(b)

        # 旧/其它字段兜底
        for key_path in (("url",), ("videoUrl",)):
            v = self._safe_get(video, *key_path, default="")
            if v and v not in urls:
                urls.append(v)

        best = ""
        if urls:
            # 优先取带sns-video域名的地址（无水印CDN）
            for u in urls:
                if "sns-video" in u:
                    best = u
                    break
            if not best:
                best = urls[0]
        return best, urls

    def _judge_quality(self, width: int, height: int) -> str:
        """根据分辨率判断视频质量（用短边）"""
        min_side = min(width, height)
        if min_side >= 1440:
            return "ultra"
        elif min_side >= 1080:
            return "high"
        elif min_side >= 720:
            return "medium"
        elif min_side > 0:
            return "low"
        else:
            return "unknown"
