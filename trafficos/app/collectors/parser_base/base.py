"""
解析器基类
所有平台解析器继承此基类，实现统一的接口
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import requests

from .exceptions import NetworkError
from .result import ParseResult

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """
    解析器基类

    所有平台解析器继承此类，必须实现：
    - parse(url) -> ParseResult：解析视频链接
    - supports(url) -> bool：判断是否支持该URL

    可选实现：
    - batch_parse(urls) -> List[ParseResult]：批量解析（默认循环调用parse）
    - get_platform_name() -> str：获取平台名称
    """

    # 子类必须设置的类属性
    PLATFORM_NAME: str = ""  # 平台名称（如"douyin"）
    PLATFORM_DISPLAY_NAME: str = ""  # 平台显示名称（如"抖音"）
    SUPPORTED_DOMAINS: List[str] = []  # 支持的域名列表

    # 默认请求头
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    def __init__(
        self,
        headers: dict = None,
        proxies: dict = None,
        timeout: int = 15,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """
        初始化解析器

        Args:
            headers: 自定义请求头（不传用默认）
            proxies: 代理设置
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.headers = {**self.DEFAULT_HEADERS, **(headers or {})}
        self.proxies = proxies
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        logger.info(f"初始化解析器: {self.PLATFORM_DISPLAY_NAME or self.PLATFORM_NAME}")

    @abstractmethod
    def parse(self, url: str) -> ParseResult:
        """
        解析视频链接

        Args:
            url: 视频分享链接

        Returns:
            ParseResult对象（包含视频信息和无水印地址）
        """
        pass

    def supports(self, url: str) -> bool:
        """
        判断是否支持该URL

        默认实现：检查URL是否包含支持的域名
        子类可以重写此方法实现更复杂的判断

        Args:
            url: 视频链接

        Returns:
            是否支持
        """
        if not url:
            return False
        url_lower = url.lower()
        for domain in self.SUPPORTED_DOMAINS:
            if domain.lower() in url_lower:
                return True
        return False

    def batch_parse(self, urls: List[str]) -> List[ParseResult]:
        """
        批量解析视频链接

        默认实现：循环调用parse，子类可以重写为并发解析

        Args:
            urls: 视频链接列表

        Returns:
            ParseResult列表
        """
        results = []
        for url in urls:
            try:
                result = self.parse(url)
            except Exception as e:
                logger.error(f"批量解析异常: {url}, 错误: {e}")
                result = ParseResult.error_result(
                    url=url, platform=self.PLATFORM_NAME, error=str(e)
                )
            results.append(result)
        return results

    def get_platform_name(self) -> str:
        """获取平台名称"""
        return self.PLATFORM_NAME

    def get_platform_display_name(self) -> str:
        """获取平台显示名称"""
        return self.PLATFORM_DISPLAY_NAME or self.PLATFORM_NAME

    # ============================================================
    # 工具方法（子类可直接使用）
    # ============================================================

    def _make_request(
        self,
        url: str,
        method: str = "GET",
        headers: dict = None,
        params: dict = None,
        data: dict = None,
        json: dict = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """
        发送HTTP请求（带重试机制）

        Args:
            url: 请求URL
            method: 请求方法（GET/POST）
            headers: 自定义请求头
            params: URL参数
            data: 表单数据
            json: JSON数据
            allow_redirects: 是否允许重定向

        Returns:
            requests.Response对象

        Raises:
            NetworkError: 网络请求失败
        """
        request_headers = {**self.headers, **(headers or {})}
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    data=data,
                    json=json,
                    proxies=self.proxies,
                    timeout=self.timeout,
                    allow_redirects=allow_redirects,
                )
                return response
            except requests.exceptions.Timeout as e:
                last_error = e
                logger.warning(f"请求超时（第{attempt+1}次）: {url}")
            except requests.exceptions.ConnectionError as e:
                last_error = e
                logger.warning(f"连接错误（第{attempt+1}次）: {url}")
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(f"请求异常（第{attempt+1}次）: {url}, 错误: {e}")

            if attempt < self.max_retries - 1:
                delay = self.retry_delay * (2**attempt)  # 指数退避
                time.sleep(delay)

        raise NetworkError(
            message=f"请求失败（重试{self.max_retries}次）: {url}, 最后错误: {last_error}", url=url
        )

    def _extract_video_id(self, url: str, patterns: List[str]) -> Optional[str]:
        """
        从URL中提取视频ID

        Args:
            url: 视频链接
            patterns: 正则表达式列表（按优先级尝试）

        Returns:
            视频ID，提取失败返回None
        """
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                logger.debug(f"提取视频ID: {video_id} (pattern: {pattern})")
                return video_id
        logger.warning(f"无法提取视频ID: {url}")
        return None

    def _resolve_short_url(self, url: str) -> str:
        """
        解析短链接，获取重定向后的长链接

        Args:
            url: 短链接

        Returns:
            长链接（如果解析失败返回原URL）
        """
        try:
            response = self._make_request(url, method="HEAD", allow_redirects=True)
            final_url = response.url
            if final_url and final_url != url:
                logger.debug(f"短链接解析: {url} -> {final_url}")
                return final_url
        except NetworkError as e:
            logger.warning(f"短链接解析失败: {url}, 错误: {e}")

        # HEAD请求失败，尝试GET
        try:
            response = self._make_request(url, method="GET", allow_redirects=True)
            return response.url
        except NetworkError:
            return url

    @staticmethod
    def _extract_url_from_text(text: str) -> Optional[str]:
        """从混合文本中提取URL（支持各平台APP分享的完整文本）。

        示例输入：
        "2.84 复制打开抖音，看看【包上恩的作品】蹦蹦跳跳 https://v.douyin.com/7aexlpKY5vc/ 07/13"

        输出：
        "https://v.douyin.com/7aexlpKY5vc/"
        """
        if not text:
            return None

        # 匹配 http:// 或 https:// 开头的URL
        url_pattern = r"https?://[^\s，。、；！？\)\]]+"
        match = re.search(url_pattern, text)
        if match:
            url = match.group(0).rstrip(".,;:!?，。；：！？")
            return url

        # 如果没有找到URL，检查输入本身是否就是URL
        stripped = text.strip()
        if stripped.startswith(("http://", "https://")):
            return stripped

        return None

    def _safe_get(self, data: dict, *keys, default=None):
        """
        安全地从嵌套字典中获取值

        Args:
            data: 字典
            *keys: 键路径（如 "aweme", "video", "play_addr"）
            default: 默认值

        Returns:
            值，不存在返回default
        """
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            elif isinstance(current, list) and isinstance(key, int) and key < len(current):
                current = current[key]
            else:
                return default
        return current

    def _parse_int(self, value, default: int = 0) -> int:
        """
        安全地解析整数（处理字符串、None等）

        Args:
            value: 待解析的值
            default: 默认值

        Returns:
            整数值
        """
        if value is None:
            return default
        try:
            if isinstance(value, str):
                # 处理带单位的字符串（如 "1.2万"）
                value = value.replace(",", "").replace("，", "")
                if "万" in value:
                    return int(float(value.replace("万", "")) * 10000)
                if "亿" in value:
                    return int(float(value.replace("亿", "")) * 100000000)
            return int(value)
        except (ValueError, TypeError):
            return default

    def _format_duration(self, duration_ms: int) -> int:
        """
        格式化时长（毫秒转秒）

        Args:
            duration_ms: 毫秒数

        Returns:
            秒数
        """
        if duration_ms <= 0:
            return 0
        return round(duration_ms / 1000)
