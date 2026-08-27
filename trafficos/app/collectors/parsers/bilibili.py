#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站视频解析器
支持B站视频链接和短链接，解析视频地址和元数据
"""

import json
import logging
import re

from ..parser_base import BaseParser, ParseResult
from ..parser_base.exceptions import (
    APIResponseError,
    NetworkError,
    ParseError,
    VideoNotFoundError,
)

logger = logging.getLogger(__name__)


class BilibiliParser(BaseParser):
    """
    B站视频解析器

    支持的URL格式：
    - 视频页：https://www.bilibili.com/video/BVxxxxxx
    - 短链接：https://b23.tv/xxxxxx
    - 移动端：https://m.bilibili.com/video/BVxxxxxx
    - AV号：https://www.bilibili.com/video/avxxxxxx
    """

    PLATFORM_NAME = "bilibili"
    PLATFORM_DISPLAY_NAME = "B站"
    SUPPORTED_DOMAINS = [
        "www.bilibili.com",
        "m.bilibili.com",
        "bilibili.com",
        "b23.tv",
    ]

    # 视频ID提取正则（按优先级）
    VIDEO_ID_PATTERNS = [
        r"/video/(BV[a-zA-Z0-9]+)",  # /video/BVxxx
        r"/video/(av\d+)",  # /video/avxxx
        r"bvid=(BV[a-zA-Z0-9]+)",  # ?bvid=BVxxx
        r"aid=(\d+)",  # ?aid=xxx
    ]

    # B站API端点
    API_VIEW = "https://api.bilibili.com/x/web-interface/view"
    API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

    # 请求头
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.headers = {**self.headers, **self.HEADERS}

    def parse(self, url: str) -> ParseResult:
        """解析B站视频链接"""
        try:
            # 从混合文本中提取URL（支持B站APP分享的完整文本）
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
                    error="不支持的B站链接格式",
                    error_code=1002,
                )

            # 解析短链接
            resolved_url = self._resolve_if_short(clean_url)

            # 提取视频ID
            video_id = self._extract_video_id(resolved_url, self.VIDEO_ID_PATTERNS)
            if not video_id:
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="无法从链接中提取视频ID",
                    error_code=1003,
                )

            logger.info(f"B站解析: video_id={video_id}")

            # 调用API获取视频信息
            result = self._fetch_video_info(video_id, url)
            return result

        except ParseError as e:
            logger.error(f"B站解析异常: {e}")
            return ParseResult.error_result(
                url=url, platform=self.PLATFORM_NAME, error=str(e), error_code=e.error_code
            )
        except Exception as e:
            logger.error(f"B站解析未知异常: {e}", exc_info=True)
            return ParseResult.error_result(
                url=url, platform=self.PLATFORM_NAME, error=f"解析失败: {str(e)}", error_code=-1
            )

    def _resolve_if_short(self, url: str) -> str:
        """如果是短链接，解析为长链接"""
        if "b23.tv" in url:
            logger.debug(f"解析B站短链接: {url}")
            return self._resolve_short_url(url)
        return url

    def _fetch_video_info(self, video_id: str, original_url: str) -> ParseResult:
        """调用B站API获取视频信息"""
        try:
            # 构建参数
            if video_id.startswith("BV"):
                params = {"bvid": video_id}
            elif video_id.startswith("av"):
                params = {"aid": video_id[2:]}
            else:
                params = {"bvid": video_id}

            # 调用view API
            response = self._make_request(self.API_VIEW, method="GET", params=params)

            if response.status_code != 200:
                raise APIResponseError(
                    message=f"API返回状态码: {response.status_code}",
                    status_code=response.status_code,
                )

            try:
                data = response.json()
            except json.JSONDecodeError:
                raise APIResponseError(message="API返回数据不是有效的JSON")

            # 检查API返回状态
            code = data.get("code", -1)
            if code != 0:
                message = data.get("message", "未知错误")
                if code == -404:
                    raise VideoNotFoundError(video_id=video_id)
                raise APIResponseError(message=f"API返回错误: {message} (code={code})")

            video_data = data.get("data", {})
            if not video_data:
                raise VideoNotFoundError(video_id=video_id)

            # 获取视频播放地址
            bvid = video_data.get("bvid", video_id)
            cid = video_data.get("cid", 0)
            video_url, video_url_list = self._fetch_play_url(bvid, cid)

            # 解析视频信息
            return self._parse_video_data(video_data, video_url, video_url_list, original_url)

        except NetworkError as e:
            logger.warning(f"API请求失败，尝试从页面提取: {e}")
            return self._fetch_from_page(video_id, original_url)

    def _fetch_play_url(self, bvid: str, cid: int) -> tuple:
        """
        获取视频播放地址

        Args:
            bvid: BV号
            cid: 视频CID

        Returns:
            (video_url, video_url_list) 元组
        """
        video_url = ""
        video_url_list = []

        if not cid:
            return video_url, video_url_list

        try:
            params = {
                "bvid": bvid,
                "cid": cid,
                "qn": 80,  # 画质：80=1080P, 64=720P, 32=480P
                "fnval": 16,  # 返回DASH格式
                "fnver": 0,
                "fourk": 1,
            }

            response = self._make_request(self.API_PLAYURL, method="GET", params=params)

            if response.status_code != 200:
                return video_url, video_url_list

            data = response.json()
            if data.get("code") != 0:
                return video_url, video_url_list

            play_data = data.get("data", {})

            # DASH格式（多清晰度）
            dash = play_data.get("dash", {})
            if dash:
                video_streams = dash.get("video", [])
                if isinstance(video_streams, list):
                    for stream in video_streams:
                        base_url = stream.get("baseUrl", "") or stream.get("base_url", "")
                        if base_url:
                            video_url_list.append(base_url)
                            if not video_url:
                                video_url = base_url

                # 音频流（单独的音频地址，DASH格式音视频分离）
                # 注意：DASH格式音视频分离，需要合并，这里只返回视频流

            # 非DASH格式（直接返回视频地址列表）
            if not video_url:
                durl = play_data.get("durl", [])
                if isinstance(durl, list) and durl:
                    video_url = durl[0].get("url", "")
                    for item in durl:
                        url = item.get("url", "")
                        if url:
                            video_url_list.append(url)

            # 备用地址
            if not video_url and video_url_list:
                video_url = video_url_list[0]

        except Exception as e:
            logger.warning(f"获取播放地址失败: {e}")

        return video_url, video_url_list

    def _parse_video_data(
        self, data: dict, video_url: str, video_url_list: list, original_url: str
    ) -> ParseResult:
        """从API返回的视频数据中解析信息"""
        # 基本信息
        bvid = data.get("bvid", "")
        aid = data.get("aid", 0)
        title = data.get("title", "") or ""
        desc = data.get("desc", "") or ""
        pubdate = data.get("pubdate", 0)  # noqa: F841
        ctime = data.get("ctime", 0)

        # 作者信息
        owner = data.get("owner", {})
        author_name = owner.get("name", "") or ""
        author_id = str(owner.get("mid", ""))
        author_avatar = owner.get("face", "") or ""

        # 视频信息
        duration = self._parse_int(data.get("duration", 0))
        # B站duration是秒，不需要转换

        # 封面图
        cover_url = data.get("pic", "") or ""

        # 互动数据
        stat = data.get("stat", {})
        likes = self._parse_int(stat.get("like", 0))
        comments = self._parse_int(stat.get("reply", 0))
        shares = self._parse_int(stat.get("share", 0))
        collects = self._parse_int(stat.get("favorite", 0))
        plays = self._parse_int(stat.get("view", 0))
        coins = self._parse_int(stat.get("coin", 0))

        # 标签/话题
        tags = []
        topics = []
        # B站标签需要单独API获取，这里先留空
        tag_list = data.get("tag", [])
        if isinstance(tag_list, list):
            for tag in tag_list:
                if isinstance(tag, str):
                    tags.append(tag)
                    topics.append(tag)

        # 分P信息
        pages = data.get("pages", [])
        page_count = len(pages) if isinstance(pages, list) else 1

        # 质量判断（B站API不直接返回分辨率，从duration和其他信息推断）
        quality = "high"  # B站默认按高清处理

        # 构建结果
        result = ParseResult.success_result(
            url=original_url,
            platform=self.PLATFORM_NAME,
            video_url=video_url,
            video_id=bvid or str(aid),
            title=title[:100] if title else f"B站视频_{bvid}",
            description=desc,
            author=author_name,
            author_id=author_id,
            author_avatar=author_avatar,
            video_url_list=video_url_list,
            cover_url=cover_url,
            duration=duration,  # B站duration已经是秒
            quality=quality,
            likes=likes,
            comments=comments,
            shares=shares,
            collects=collects,
            plays=plays,
            tags=tags,
            topics=topics,
            create_time=ctime,
            raw_data=data,
        )

        # 添加额外字段
        result.raw_data["coins"] = coins
        result.raw_data["page_count"] = page_count
        result.raw_data["aid"] = aid

        logger.info(
            f"B站解析成功: id={bvid}, title={result.title[:30]}..., "
            f"plays={plays}, duration={duration}s"
        )
        return result

    def _fetch_from_page(self, video_id: str, original_url: str) -> ParseResult:
        """从视频页面HTML中提取视频信息（API失败时的降级方案）"""
        try:
            page_url = f"https://www.bilibili.com/video/{video_id}"
            response = self._make_request(page_url, method="GET")

            if response.status_code != 200:
                return ParseResult.error_result(
                    url=original_url,
                    platform=self.PLATFORM_NAME,
                    error=f"页面请求失败: HTTP {response.status_code}",
                    error_code=1005,
                )

            html = response.text

            # 从HTML中提取嵌入的JSON数据
            # B站页面在<script>标签中嵌入了window.__INITIAL_STATE__
            initial_match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html, re.DOTALL
            )

            if initial_match:
                try:
                    data = json.loads(initial_match.group(1))
                    video_data = data.get("videoData", {})
                    if video_data:
                        bvid = video_data.get("bvid", video_id)
                        cid = video_data.get("cid", 0)
                        video_url, video_url_list = self._fetch_play_url(bvid, cid)
                        return self._parse_video_data(
                            video_data, video_url, video_url_list, original_url
                        )
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(f"解析页面JSON失败: {e}")

            # 降级：从HTML中提取视频地址
            video_url = self._extract_video_url_from_html(html)
            if video_url:
                return ParseResult.success_result(
                    url=original_url,
                    platform=self.PLATFORM_NAME,
                    video_url=video_url,
                    video_id=video_id,
                    title=f"B站视频_{video_id}",
                    raw_data={"source": "page_html_direct"},
                )

            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="无法从页面提取视频信息",
                error_code=1005,
            )

        except Exception as e:
            logger.error(f"从页面提取失败: {e}", exc_info=True)
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error=f"页面解析失败: {str(e)}",
                error_code=-1,
            )

    def _extract_video_url_from_html(self, html: str) -> str:
        """从HTML中直接提取视频地址"""
        patterns = [
            r'"baseUrl":\s*"([^"]+)"',
            r'"url":\s*"(https?://[^"]+\.m4s[^"]*)"',
            r'(https?://[^"\']+\.mp4[^"\']*)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                url = match.group(1)
                if url and (".m4s" in url or ".mp4" in url):
                    return url
        return ""

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
