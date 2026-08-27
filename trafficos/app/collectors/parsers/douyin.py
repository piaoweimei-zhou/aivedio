#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# flake8: noqa: E402  (playwright 须在设置浏览器路径后导入，import 无法置于顶部)
"""
抖音解析器（Playwright 版）

用 headless Chromium 打开视频页，拦截其内部 XHR
`/aweme/v1/web/aweme/detail/?aweme_id=...` 拿完整 JSON（含无水印 CDN 直链）。
不需要 user cookies or tokens — the browser establishes a fresh session.
"""

import json
import logging
import os
import sys


# ============================================================
# 关键：必须在 import playwright 之前设置浏览器路径！
# playwright 在导入时就会确定浏览器位置，之后设置环境变量无效。
# ============================================================
def _setup_playwright_browser_path():
    """在导入playwright之前设置浏览器路径。"""
    # 1. 优先使用打包目录中的内置浏览器（exe同目录的browser文件夹）
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        bundled_browser = os.path.join(exe_dir, "browser")
        if os.path.exists(bundled_browser):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled_browser
            return

    # 2. 开发环境或未内置浏览器时，使用系统默认路径
    default_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright")
    if os.path.exists(default_path):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", default_path)


_setup_playwright_browser_path()

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False

from ..parser_base import BaseParser, ParseResult
from ..parser_base.exceptions import (
    APIResponseError,
    ParseError,
)

logger = logging.getLogger(__name__)


class DouyinParser(BaseParser):
    """抖音视频解析器（headless-browser backed）"""

    PLATFORM_NAME = "douyin"
    PLATFORM_DISPLAY_NAME = "抖音"
    SUPPORTED_DOMAINS = [
        "www.douyin.com",
        "m.douyin.com",
        "douyin.com",
        "iesdouyin.com",
        "v.douyin.com",
    ]

    VIDEO_ID_PATTERNS = [
        r"/(?:video|note)/(\d{5,20})",
        r"/share/video/(\d+)",
        r"[?&]vid=(\d+)",
        r"modal_id=(\d+)",
        r"item_ids=([\d,]+)",
    ]

    DESKTOP_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    DETAIL_API_PREFIX = "/aweme/v1/web/aweme/detail/"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.headers = {**self.DEFAULT_HEADERS, **kwargs.get("headers", {})}

    # ------------------------------------------------------------------
    def parse(self, url: str) -> ParseResult:
        try:
            # 第一步：从混合文本中提取URL（支持抖音APP分享的完整文本）
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
                    error="不支持的抖音链接格式",
                    error_code=1002,
                )

            # 第二步：解析短链接（v.douyin.com/xxx -> www.douyin.com/video/xxx）
            if "v.douyin.com" in clean_url or "v.iesdouyin.com" in clean_url:
                logger.info(f"解析抖音短链接: {clean_url}")
                clean_url = self._resolve_short_url(clean_url)
                logger.info(f"短链接解析结果: {clean_url}")

            # 第三步：从完整URL中提取视频ID
            video_id = self._extract_video_id(clean_url, self.VIDEO_ID_PATTERNS)
            if not video_id:
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="无法从链接中提取视频ID",
                    error_code=1003,
                )

            logger.info(f"抖音解析: video_id={video_id}")

            # 检查playwright是否可用
            if not PLAYWRIGHT_AVAILABLE:
                return ParseResult.error_result(
                    url=url,
                    platform=self.PLATFORM_NAME,
                    error="抖音解析需要 playwright 支持，请运行: pip install playwright && "
                    "playwright install chromium",
                    error_code=1004,
                )

            detail = self._fetch_detail(video_id)
            return self._build_result(detail, video_id, clean_url)

        except ParseError as e:
            logger.error(f"抖音解析异常: {e}")
            return ParseResult.error_result(
                url=url,
                platform=self.PLATFORM_NAME,
                error=str(e),
                error_code=e.error_code,
            )
        except Exception as e:
            logger.error(f"抖音解析未知异常: {e}", exc_info=True)
            return ParseResult.error_result(
                url=url,
                platform=self.PLATFORM_NAME,
                error=f"解析失败: {str(e)}",
                error_code=-1,
            )

    # 类变量：标记是否已尝试安装chromium
    _chromium_installed = False

    # ------------------------------------------------------------------
    @staticmethod
    def _page_url_for(video_id: str) -> str:
        return f"https://www.douyin.com/video/{video_id}"

    @staticmethod
    def _ensure_playwright_browser_path():
        """确保playwright能找到浏览器路径。

        优先使用打包目录中的内置浏览器（browser/），搜索多个可能的位置：
        1. exe同目录的browser/
        2. exe上级目录的browser/
        3. exe同目录下所有子目录中的browser/（便携版目录）
        4. 开发环境的系统路径
        """
        import glob
        import os
        import sys

        # 开发环境：使用系统默认路径
        if not getattr(sys, "frozen", False):
            default_path = os.path.join(
                os.path.expanduser("~"), "AppData", "Local", "ms-playwright"
            )
            if os.path.exists(default_path):
                os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", default_path)
                logger.debug(f"使用系统浏览器: {default_path}")
            return

        # 打包环境：搜索多个位置的browser目录
        exe_dir = os.path.dirname(sys.executable)
        parent_dir = os.path.dirname(exe_dir)

        search_paths = [
            os.path.join(exe_dir, "browser"),  # 1. exe同目录
            os.path.join(parent_dir, "browser"),  # 2. exe上级目录
        ]

        # 3. exe同目录下所有子目录中的browser（便携版目录结构）
        try:
            for subdir in glob.glob(os.path.join(exe_dir, "*", "browser")):
                search_paths.append(subdir)
        except Exception:
            pass

        # 4. exe上级目录下所有子目录中的browser
        try:
            for subdir in glob.glob(os.path.join(parent_dir, "*", "browser")):
                search_paths.append(subdir)
        except Exception:
            pass

        # 找到第一个存在的browser目录
        for browser_path in search_paths:
            if os.path.exists(browser_path):
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_path
                logger.info(f"使用内置浏览器: {browser_path}")
                return

        # 找不到browser目录
        logger.warning(f"未找到内置浏览器目录。exe_dir={exe_dir}")
        logger.warning(f"搜索路径: {search_paths}")

    @staticmethod
    def _install_chromium() -> bool:
        """自动安装playwright chromium浏览器。

        Returns:
            是否安装成功
        """
        import os
        import shutil
        import subprocess
        import sys

        logger.info("正在自动安装 Chromium 浏览器（首次使用抖音解析需要，约150MB）...")
        try:
            # 查找playwright可执行文件
            playwright_exe = shutil.which("playwright")

            if not playwright_exe:
                # 尝试常见的Python Scripts目录
                common_paths = [
                    os.path.join(
                        os.path.expanduser("~"),
                        "AppData",
                        "Local",
                        "Programs",
                        "Python",
                        "Python313",
                        "Scripts",
                        "playwright.exe",
                    ),
                    os.path.join(
                        os.path.expanduser("~"),
                        "AppData",
                        "Local",
                        "Programs",
                        "Python",
                        "Python312",
                        "Scripts",
                        "playwright.exe",
                    ),
                    os.path.join("C:", "Python313", "Scripts", "playwright.exe"),
                    os.path.join("C:", "Python312", "Scripts", "playwright.exe"),
                ]
                for p in common_paths:
                    if os.path.exists(p):
                        playwright_exe = p
                        break

            if not playwright_exe:
                logger.error("未找到playwright可执行文件，无法自动安装Chromium")
                return False

            cmd = [playwright_exe, "install", "chromium"]
            logger.info(f"执行命令: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,  # 5分钟超时
            )

            if result.returncode == 0:
                logger.info("Chromium 安装成功")
                return True
            else:
                logger.error(f"Chromium 安装失败: {result.stderr[:500]}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("Chromium 安装超时（超过5分钟）")
            return False
        except Exception as e:
            logger.error(f"Chromium 安装异常: {e}")
            return False

    def _fetch_detail(self, video_id: str) -> dict:
        """Open the video page in headless Chromium and intercept the detail XHR.

        如果chromium未安装，开发环境自动下载安装；打包环境直接提示使用便携版。
        """
        import sys

        # 确保playwright能找到浏览器
        self._ensure_playwright_browser_path()

        # 打包环境下，如果找不到browser目录，直接给出清晰错误（不要尝试自动安装，肯定失败）
        if getattr(sys, "frozen", False):
            browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
            if not browser_path or not os.path.exists(browser_path):
                exe_dir = os.path.dirname(sys.executable)
                raise APIResponseError(
                    f"未找到Chromium浏览器目录。\n"
                    f"请使用便携版（含内置浏览器），或将browser目录放在exe同目录。\n"
                    f"当前exe目录: {exe_dir}"
                )

        page_url = self._page_url_for(video_id)

        # 最多重试2次（第一次可能触发chromium安装）
        for attempt in range(2):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    try:
                        ctx = browser.new_context(user_agent=self.DESKTOP_UA, locale="zh-CN")
                        page = ctx.new_page()
                        captured: list[dict] = []

                        def on_response(resp):
                            if self.DETAIL_API_PREFIX in resp.url and "aweme_id" in resp.url:
                                try:
                                    data = json.loads(resp.text())
                                    if "aweme_detail" in data:
                                        captured.append(data["aweme_detail"])
                                except Exception as e:
                                    logger.debug(f"detail XHR parse failed: {e}")

                        page.on("response", on_response)
                        page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)

                        # Wait up to 20s for the detail XHR to arrive
                        for _ in range(20):
                            if captured:
                                break
                            page.wait_for_timeout(1_000)

                    finally:
                        browser.close()

                if not captured:
                    raise APIResponseError(f"抖音接口未返回有效数据 (video_id={video_id})")
                return captured[0]

            except Exception as e:
                error_msg = str(e).lower()
                # 精确检测是否是chromium未安装的错误
                is_missing_browser = (
                    "executable doesn't exist" in error_msg
                    or "executable does not exist" in error_msg
                    or ("chromium" in error_msg and "not found" in error_msg)
                    or (
                        "browser" in error_msg
                        and "install" in error_msg
                        and "playwright" in error_msg
                    )
                )

                if attempt == 0 and not self._chromium_installed and is_missing_browser:
                    logger.info("检测到Chromium未安装，尝试自动安装...")
                    self._chromium_installed = True  # 标记已尝试，避免无限重试
                    if self._install_chromium():
                        logger.info("Chromium安装完成，重试解析...")
                        continue
                    else:
                        raise APIResponseError(
                            "Chromium浏览器安装失败，请手动运行: playwright install chromium"
                        ) from e
                else:
                    # 不是浏览器缺失错误，直接抛出
                    raise

        raise APIResponseError(f"抖音接口未返回有效数据 (video_id={video_id})")

    # ------------------------------------------------------------------
    def _build_result(self, data: dict, video_id: str, original_url: str) -> ParseResult:
        title = (data.get("desc") or "").strip()[:100]

        author = data.get("author") or {}
        author_name = author.get("nickname", "")
        author_id = str(author.get("unique_id") or author.get("id") or "")
        author_avatar = ""
        avatar_thumb = author.get("avatar_thumb")
        if isinstance(avatar_thumb, dict):
            urls = avatar_thumb.get("url_list") or []
            author_avatar = urls[0] if urls else ""

        video = data.get("video", {}) or {}
        width = self._parse_int(video.get("width"))
        height = self._parse_int(video.get("height"))
        duration_ms = self._parse_int(video.get("duration"))
        duration_s = round(duration_ms / 1000, 2) if duration_ms else None

        play_addr_list = ((video.get("play_addr") or {}).get("url_list")) or []
        download_addr_list = ((video.get("download_addr") or {}).get("url_list")) or []
        video_url = (
            play_addr_list[0]
            if play_addr_list
            else (download_addr_list[0] if download_addr_list else "")
        )

        cover = ""
        cover_obj = video.get("cover") or video.get("origin_cover") or {}
        if isinstance(cover_obj, dict):
            cover = (cover_obj.get("url_list") or [""])[0]
        if not cover:
            imgs = video.get("images") or []
            if imgs and isinstance(imgs[0], dict):
                cover = ((imgs[0].get("url_list")) or [""])[0]

        music = data.get("music") or {}
        music_url = ""
        play_url_obj = music.get("play_url")
        if isinstance(play_url_obj, dict):
            music_url = play_url_obj.get("uri", "")
        music_name = music.get("title", "")

        stats_block = data.get("statistics", {}) or {}
        likes = self._parse_int(stats_block.get("digg_count"))
        comments = self._parse_int(stats_block.get("comment_count"))
        shares = self._parse_int(stats_block.get("share_count"))
        collects = self._parse_int(stats_block.get("collect_count"))

        create_time = self._parse_int(data.get("create_time"))
        quality = self._judge_quality(width, height)

        if not video_url:
            return ParseResult.error_result(
                url=original_url,
                platform=self.PLATFORM_NAME,
                error="未找到可播放的视频地址（可能已删除或需要登录）",
                error_code=1005,
            )

        return ParseResult.success_result(
            url=original_url,
            platform=self.PLATFORM_NAME,
            video_url=video_url,
            video_id=video_id,
            title=title or f"抖音视频_{video_id}",
            description=data.get("desc", ""),
            author=author_name,
            author_id=author_id,
            author_avatar=author_avatar,
            cover_url=cover,
            duration=self._format_duration(duration_s),
            width=width,
            height=height,
            quality=quality,
            music_url=music_url,
            music_name=music_name,
            likes=likes,
            comments=comments,
            shares=shares,
            collects=collects,
            create_time=create_time,
            video_url_list=play_addr_list or download_addr_list,
        )

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
