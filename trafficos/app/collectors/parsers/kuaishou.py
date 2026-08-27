#!/usrbin/python3
# -*- coding: utf-8 -*-
"""快手视频解析器（Playwright + requests 双通道版）

Primary path: plain HTTP GET with iPhone UA → parse embedded JSON (srcMap/caption).
Fallback: Playwright headless Chromium for pages that block non-browser clients.
User does NOT need to supply any cookies or tokens.
"""

import json
import logging
import re
from typing import List, Optional

from ..parser_base import BaseParser, ParseResult
from ..parser_base.exceptions import (
    NetworkError,
    ParseError,
    VideoNotFoundError,
)

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


class KuaishouParser(BaseParser):
    """快手视频解析器"""

    PLATFORM_NAME = "kuaishou"
    PLATFORM_DISPLAY_NAME = "快手"
    SUPPORTED_DOMAINS = [
        "v.kuaishou.com",
        "www.kuaishou.com",
        "m.gifshow.com",
        "gifshow.com",
        "kuaishou.com",
        "chenzhongtech.com",  # mobile redirect target
    ]

    VIDEO_ID_PATTERNS = [
        r"/short-video/([a-zA-Z0-9_-]+)",
        r"/fw/photo/([a-zA-Z0-9_-]+)",
        r"/photo/([a-zA-Z0-9_-]+)",
        r"/video/([a-zA-Z0-9_-]+)",
        # Short-form ID in share URLs (e.g. v.kuaishou.com/nP98yxEz → /fw/photo/3x4fgqfs94emn4e)
        r"photoId=([a-zA-Z0-9_-]{8,})",
        r"photo_id=([a-zA-Z0-9_-]{8,})",
    ]

    DESKTOP_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Mobile Safari/604.1"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.headers = {**self.DEFAULT_HEADERS}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def parse(self, url: str) -> ParseResult:
        try:
            # 从混合文本中提取URL（支持快手APP分享的完整文本）
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
                    error="不支持的快手链接格式",
                    error_code=1002,
                )

            video_id = self._extract_video_id(clean_url, self.VIDEO_ID_PATTERNS)
            resolved_url = clean_url
            if not video_id:
                # v.kuaishou.com short link — resolve via HTTP redirect
                resolved_url = self._resolve_short_link(clean_url)
                video_id = self._extract_video_id(resolved_url, self.VIDEO_ID_PATTERNS)
                if not video_id:
                    return ParseResult.error_result(
                        url=url,
                        platform=self.PLATFORM_NAME,
                        error="无法从链接中提取视频ID",
                        error_code=1003,
                    )

            logger.info(f"快手解析: video_id={video_id}, url={resolved_url}")

            # Primary: plain HTTP with mobile UA (fast, no browser needed)
            detail = self._fetch_via_requests(resolved_url, video_id)

            # Fallback: Playwright for pages that block non-browser clients
            if not detail or not (detail.get("photo") or {}).get("mainMvUrls"):
                logger.info("快手 requests 通道未拿到视频，尝试 Playwright…")
                detail = self._fetch_via_playwright(video_id, resolved_url)

            return self._build_result(detail, video_id, url)

        except ParseError as e:
            logger.error(f"快手解析异常: {e}")
            return ParseResult.error_result(
                url=url,
                platform=self.PLATFORM_NAME,
                error=str(e),
                error_code=e.error_code,
            )
        except Exception as e:
            logger.exception("快手解析未知异常")
            return ParseResult.error_result(
                url=url,
                platform=self.PLATFORM_NAME,
                error=f"解析失败: {str(e)}",
                error_code=-1,
            )

    # ------------------------------------------------------------------
    # Short-link resolution (plain HTTP)
    # ------------------------------------------------------------------
    def _resolve_short_link(self, url: str) -> str:
        """Resolve v.kuaishou.com short link to the full mobile page URL.

        The redirect target looks like:
          https://v.m.chenzhongtech.com/fw/photo/3x4fgqfs94emn4e?cc=share_copylink&...
        We extract the photo ID from the path and build a canonical URL.
        """
        try:
            resp = self._make_request(url, method="GET", allow_redirects=True)
            final_url = getattr(resp, "url", None) or ""
            if final_url and final_url != url:
                logger.debug(f"快手短链解析: {url} -> {final_url}")
                # Extract photo ID from the redirect target
                m = re.search(r"/fw/photo/([a-zA-Z0-9_-]+)", final_url)
                if m:
                    return f"https://v.m.chenzhongtech.com/fw/photo/{m.group(1)}"
                m = re.search(r"photoId=([a-zA-Z0-9_-]{8,})", final_url)
                if m:
                    return f"https://v.m.chenzhongtech.com/fw/photo/{m.group(1)}"
                return final_url
        except NetworkError as e:
            logger.warning(f"快手短链解析失败: {e}")
        return url

    # ------------------------------------------------------------------
    # Primary fetch: plain HTTP with mobile UA
    # ------------------------------------------------------------------
    def _fetch_via_requests(self, page_url: str, video_id: str) -> dict:
        """Fetch the Kuaishou page with a mobile UA and parse embedded JSON."""
        try:
            resp = self._make_request(
                page_url,
                method="GET",
                allow_redirects=True,
                headers={"User-Agent": self.MOBILE_UA},
            )
            html = getattr(resp, "text", "") or ""
            if not html:
                return {}

            photo = self._extract_photo_from_html(html, video_id)
            if photo and (photo.get("mainMvUrls") or photo.get("playUrl")):
                logger.info(f"快手 requests 通道成功: id={video_id}")
                return {"photo": photo}

            # Last resort in this channel: direct mp4 link in HTML
            video_url = self._extract_direct_mp4(html)
            if video_url:
                return {
                    "photo": {
                        "photoId": video_id,
                        "playUrl": video_url,
                        "caption": f"快手视频_{video_id}",
                    }
                }
        except Exception as e:
            logger.warning(f"快手 requests 通道异常: {e}")
        return {}

    # ------------------------------------------------------------------
    # Fallback fetch: Playwright headless Chromium
    # ------------------------------------------------------------------
    def _fetch_via_playwright(self, video_id: str, page_url: str) -> dict:
        if not PLAYWRIGHT_AVAILABLE:
            raise ParseError(
                message="Playwright 未安装，无法解析快手视频",
                error_code=1010,
            )

        normalized = f"https://www.kuaishou.com/short-video/{video_id}"
        if page_url and "kuaishou.com" in page_url:
            m = re.search(r"(https?://[a-z0-9.-]*kuaishou\.com/\S+)", page_url)
            if m:
                normalized = m.group(1).split("?")[0]

        payload: dict = {}
        html_body = ""

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(user_agent=self.DESKTOP_UA)
                page = context.new_page()

                def _on_response(resp):
                    nonlocal payload
                    try:
                        ct = (resp.headers or {}).get("content-type", "")
                        if "json" not in ct.lower():
                            return
                        u = resp.url
                        if any(h in u for h in ("graphql", "/rest/n/photo/info")):
                            body = resp.text()
                            parsed = json.loads(body)
                            photo = self._find_photo_in_apollo(parsed, video_id)
                            if photo and not payload:
                                payload = {"photo": photo}
                    except Exception as e:
                        logger.debug(f"快手 XHR 解析失败: {e}")

                page.on("response", _on_response)
                try:
                    page.goto(normalized, wait_until="domcontentloaded", timeout=45_000)
                    for _ in range(20):
                        if payload:
                            break
                        page.wait_for_timeout(1_000)
                except Exception as e:
                    logger.warning(f"快手页面加载异常: {e}")

                try:
                    html_body = page.content() or ""
                except Exception:
                    pass
            finally:
                browser.close()

        if payload.get("photo"):
            return payload

        photo = self._extract_photo_from_html(html_body, video_id)
        if photo:
            return {"photo": photo}

        video_url = self._extract_direct_mp4(html_body)
        if video_url:
            return {
                "photo": {
                    "photoId": video_id,
                    "playUrl": video_url,
                    "caption": f"快手视频_{video_id}",
                }
            }

        raise VideoNotFoundError(video_id=video_id)

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------
    def _find_photo_in_apollo(self, data: dict, video_id: str) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        for key, value in data.items():
            if isinstance(key, str) and key.startswith("Photo:"):
                pid = key.split(":", 1)[-1]
                if pid == video_id or (video_id and video_id in key):
                    return self._normalize_photo(value, video_id)
        for k in ("data", "result"):
            if isinstance(data.get(k), dict):
                found = self._find_photo_in_apollo(data[k], video_id)
                if found:
                    return found
        return None

    def _normalize_photo(self, raw: dict, video_id: str) -> Optional[dict]:
        if not isinstance(raw, dict):
            return None
        candidate = raw.get("photo") if isinstance(raw.get("photo"), dict) else raw
        if not (
            candidate.get("mainMvUrls")
            or candidate.get("playUrl")
            or candidate.get("caption")
            or candidate.get("photoId")
        ):
            return None
        photo_id = str(candidate.get("photoId") or video_id)
        author = candidate.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        # mainMvUrls may be a list of dicts ({url,width,height,...}) or strings
        mv_urls = candidate.get("mainMvUrls", []) or []
        normalized_mvs = []
        for item in mv_urls:
            if isinstance(item, dict):
                u = item.get("url") or item.get("src") or ""
                if u:
                    normalized_mvs.append(u)
            elif isinstance(item, str) and item:
                normalized_mvs.append(item)

        return {
            "photoId": photo_id,
            "caption": candidate.get("caption", "") or "",
            "mainMvUrls": normalized_mvs,
            "playUrl": candidate.get("playUrl", "") or "",
            "width": self._parse_int(candidate.get("width", 0)),
            "height": self._parse_int(candidate.get("height", 0)),
            "duration": self._parse_int(candidate.get("duration", 0)) * 1000,
            "timestamp": candidate.get("timestamp", 0) or 0,
            "authorName": author.get("name", "") or "",
            "authorId": str(author.get("id", "") or ""),
            "likeCount": self._parse_int(candidate.get("likeCount", 0)),
            "commentCount": self._parse_int(candidate.get("commentCount", 0)),
            "shareCount": self._parse_int(candidate.get("shareCount", 0)),
            "collectCount": self._parse_int(candidate.get("collectCount", 0)),
            "viewCount": self._parse_int(candidate.get("viewCount", 0)),
            "coverUrl": candidate.get("coverUrl", "") or "",
        }

    def _extract_photo_from_html(self, html: str, video_id: str) -> Optional[dict]:
        if not html:
            return None

        # 1. RENDER_DATA (URL-encoded JSON inside <script id="RENDER_DATA">)
        m = re.search(
            r'<script[^>]*id=["\']?RENDER_DATA["\']?[^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            import urllib.parse

            raw = m.group(1).strip()
            if "%" in raw[:50]:
                raw = urllib.parse.unquote(raw)
            try:
                data = json.loads(raw)
                found = self._deep_find_photo(data, video_id)
                if found:
                    return found
            except (json.JSONDecodeError, ValueError):
                pass

        # 2. window.__INITIAL_STATE__ / window.INIT_DATA / inline JSON blobs
        for pattern in [
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>",
            r"window\.INIT_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>",
            r'"photoId"\s*:\s*"%s".*?"mainMvUrls"' % re.escape(video_id),
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1) if m.lastindex else m.group(0)
                try:
                    data = json.loads(raw)
                    found = self._deep_find_photo(data, video_id)
                    if found:
                        return found
                except (json.JSONDecodeError, ValueError):
                    pass

        # 3. Generic deep scan: look for any dict with photoId + mainMvUrls/playUrl
        #    This handles the mobile-page case where data is embedded in a large
        #    inline JSON without a well-known wrapper key (e.g. window.INIT_STATE).
        try:
            scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
            for s in sorted(scripts, key=len, reverse=True)[:3]:
                s = s.strip()
                if not s or len(s) < 100:
                    continue
                # Try to extract JSON object from the script content.
                # The data may be JS-prefixed (window.X = {...}) and/or
                # brace-UNbalanced when it is an HTML entity — so instead of
                # relying on a single balanced-brace walk, scan candidate
                # '{' positions with a lenient JSON decoder (raw_decode stops
                # at the first complete object, ignoring trailing junk).
                for m in re.finditer(r"\{", s):
                    pos = m.start()
                    try:
                        data, _end = json.JSONDecoder().raw_decode(s[pos:])
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    found = self._deep_find_photo(data, video_id)
                    if found:
                        return found
        except Exception as e:
            logger.debug(f"快手 HTML 深度扫描异常: {e}")

        return None

    def _deep_find_photo(self, obj, video_id: str) -> Optional[dict]:
        if isinstance(obj, dict):
            pid = obj.get("photoId") or obj.get("id")
            # The mobile page embeds the numeric photoId while share URLs use a
            # short-form ID. We don't know which one was in the input URL, so
            # accept any dict that has a plausible video payload.
            if pid and (obj.get("mainMvUrls") or obj.get("playUrl") or obj.get("caption")):
                return self._normalize_photo(obj, str(pid))
            for value in obj.values():
                found = self._deep_find_photo(value, video_id)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._deep_find_photo(item, video_id)
                if found:
                    return found
        return None

    def _extract_direct_mp4(self, html: str) -> str:
        """Extract the highest-quality mp4 URL from embedded JSON in HTML.

        The mobile page embeds a large JSON blob with srcMap containing
        multiple quality variants (1080p, 720p, etc). We want the best one.
        """
        if not html:
            return ""
        # Look for photo-video-mz URLs (these are the actual video files)
        mp4s = re.findall(r'"(https?://[^"]+photo-video-mz/[^"]+\.mp4[^"]*)"', html)
        if mp4s:
            # Prefer the one with highest resolution hint in filename
            for u in reversed(mp4s):  # later entries tend to be higher quality
                return u
        # Fallback: any kwaicdn mp4
        mp4s = re.findall(r'"(https?://[^"]+\.mp4[^"]*)"', html)
        for u in mp4s:
            if "kwaicdn.com" in u or "oskwai.com" in u:
                return u
        return ""

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------
    def _build_result(self, detail: dict, video_id: str, original_url: str) -> ParseResult:
        photo = detail.get("photo", {}) or {}
        caption = (photo.get("caption") or "").strip()

        mv_urls = photo.get("mainMvUrls") or []
        url_list: List[str] = [u for u in mv_urls if isinstance(u, str) and u]
        play_url = photo.get("playUrl", "") or ""
        video_url = url_list[0] if url_list else play_url

        width = self._parse_int(photo.get("width", 0))
        height = self._parse_int(photo.get("height", 0))
        quality = self._judge_quality(width, height)

        result = ParseResult.success_result(
            url=original_url,
            platform=self.PLATFORM_NAME,
            video_url=video_url,
            video_id=str(photo.get("photoId") or video_id),
            title=caption[:100] if caption else f"快手视频_{video_id}",
            description=caption,
            author=photo.get("authorName", ""),
            author_id=photo.get("authorId", ""),
            video_url_list=url_list,
            cover_url=photo.get("coverUrl", ""),
            width=width,
            height=height,
            quality=quality,
            duration=self._format_duration(photo.get("duration", 0)),
            likes=self._parse_int(photo.get("likeCount", 0)),
            comments=self._parse_int(photo.get("commentCount", 0)),
            shares=self._parse_int(photo.get("shareCount", 0)),
            collects=self._parse_int(photo.get("collectCount", 0)),
            plays=self._parse_int(photo.get("viewCount", 0)),
            create_time=photo.get("timestamp", 0) or None,
            raw_data={"source": "requests" if not detail.get("_pw") else "playwright"},
        )
        logger.info(f"快手解析成功: id={video_id}, title={result.title[:30]}")
        return result

    def _judge_quality(self, width: int, height: int) -> str:
        min_side = min(width or 0, height or 0)
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
