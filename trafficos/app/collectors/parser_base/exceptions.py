"""
解析异常定义
所有平台解析器抛出的异常都继承自ParseError
"""


class ParseError(Exception):
    """解析异常基类"""

    def __init__(self, message: str = "解析失败", error_code: int = -1):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class NetworkError(ParseError):
    """网络请求异常"""

    def __init__(self, message: str = "网络请求失败", url: str = ""):
        self.url = url
        super().__init__(message, error_code=1001)


class UnsupportedURLError(ParseError):
    """不支持的URL异常"""

    def __init__(self, url: str = "", platform: str = ""):
        self.url = url
        self.platform = platform
        super().__init__(f"不支持的URL: {url}", error_code=1002)


class InvalidVideoIdError(ParseError):
    """无效的视频ID异常"""

    def __init__(self, video_id: str = ""):
        self.video_id = video_id
        super().__init__(f"无效的视频ID: {video_id}", error_code=1003)


class APIRateLimitError(ParseError):
    """API限流异常"""

    def __init__(self, message: str = "API请求过于频繁，请稍后重试"):
        super().__init__(message, error_code=1004)


class APIResponseError(ParseError):
    """API响应异常（返回数据格式错误或包含错误信息）"""

    def __init__(self, message: str = "API响应异常", status_code: int = 0, response: str = ""):
        self.status_code = status_code
        self.response = response
        super().__init__(message, error_code=1005)


class VideoNotFoundError(ParseError):
    """视频不存在或已删除异常"""

    def __init__(self, video_id: str = ""):
        self.video_id = video_id
        super().__init__(f"视频不存在或已删除: {video_id}", error_code=1006)


class PrivateVideoError(ParseError):
    """私密视频异常（无法访问）"""

    def __init__(self, message: str = "视频为私密视频，无法访问"):
        super().__init__(message, error_code=1007)


class SignatureError(ParseError):
    """签名验证异常"""

    def __init__(self, message: str = "签名验证失败"):
        super().__init__(message, error_code=1008)


class ParserNotImplementedError(ParseError):
    """解析器未实现异常"""

    def __init__(self, platform: str = ""):
        self.platform = platform
        super().__init__(f"平台解析器未实现: {platform}", error_code=1009)
