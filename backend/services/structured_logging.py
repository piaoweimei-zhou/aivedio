"""
结构化日志服务
支持 JSON 格式输出、日志级别管理、请求追踪、日志轮转、trace_id 链路追踪
"""

import logging
import json
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from logging.handlers import TimedRotatingFileHandler
from contextvars import ContextVar
from services.paths import LOGS_DIR


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Windows 兼容的时间轮转 handler。

    TimedRotatingFileHandler 在 Windows 上轮转时若目标文件被其他进程占用
    （多 uvicorn 实例共存等），rename 抛 PermissionError，导致每次 emit 都刷
    "Logging error"。此处捕获 PermissionError 降级为"继续追加写当前文件"，
    避免日志系统刷屏，待占用解除后自动恢复轮转。
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # 目标被占用（Windows 常见）：关闭旧流后重新打开当前文件继续追加
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass
            self.stream = self._open()


# 日志级别定义
class LogLevel:
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


# 全局 trace_id 上下文变量
_trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def get_trace_id() -> str:
    """获取当前请求的 trace_id"""
    tid = _trace_id_ctx.get()
    if tid is None:
        tid = str(uuid.uuid4())
        _trace_id_ctx.set(tid)
    return tid


def set_trace_id(trace_id: str):
    """设置当前请求的 trace_id"""
    _trace_id_ctx.set(trace_id)


def clear_trace_id():
    """清除当前请求的 trace_id"""
    _trace_id_ctx.set(None)


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器"""

    def __init__(self, ensure_ascii=False):
        """
        Args:
            ensure_ascii: 控制台输出时设为 True（避免 Windows GBK 乱码），
                         文件输出时设为 False（文件用 UTF-8 编码，中文可读）
        """
        super().__init__()
        self.ensure_ascii = ensure_ascii

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录"""
        # 如果已经是 JSON 格式，直接返回
        if record.msg and isinstance(record.msg, str) and record.msg.startswith("{"):
            return record.msg

        # 构建结构化日志记录
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(record.levelno),
            "message": record.getMessage(),
            "logger": record.name,
            "trace_id": get_trace_id(),
        }

        # 添加额外属性
        if hasattr(record, "request"):
            log_record["request"] = record.request
        if hasattr(record, "pipeline"):
            log_record["pipeline"] = record.pipeline
        if hasattr(record, "error"):
            log_record["error"] = record.error
        if hasattr(record, "extra"):
            log_record.update(record.extra)

        return json.dumps(log_record, ensure_ascii=self.ensure_ascii)


class StructuredLogger:
    """结构化日志记录器"""

    def __init__(self, name: str, level: int = LogLevel.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False

        # 只有在没有 handler 时才添加（避免重复添加）
        if not self.logger.handlers:
            self._add_handlers()

    def _add_handlers(self):
        """添加日志处理器"""
        # 控制台处理器 — iconv-lite 在 Node.js 侧处理编码转换，Python 直接输出 UTF-8 中文
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(StructuredFormatter(ensure_ascii=False))
        self.logger.addHandler(console_handler)

        # 文件处理器（按时间轮转）— 文件用 UTF-8 编码，中文可读
        log_dir = Path(LOGS_DIR)
        log_dir.mkdir(exist_ok=True)

        file_handler = SafeTimedRotatingFileHandler(
            log_dir / "app.log",
            when="D",  # 按天轮转
            interval=1,  # 每天
            backupCount=30,  # 保留30天
            encoding="utf-8",
        )
        file_handler.setFormatter(StructuredFormatter(ensure_ascii=False))
        self.logger.addHandler(file_handler)

        # 错误日志单独记录
        error_handler = SafeTimedRotatingFileHandler(
            log_dir / "error.log", when="D", interval=1, backupCount=30, encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(StructuredFormatter(ensure_ascii=False))
        self.logger.addHandler(error_handler)

    def _build_log_record(self, level: str, message: str, **kwargs) -> str:
        """构建结构化日志记录"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "logger": self.logger.name,
            "trace_id": get_trace_id(),
        }

        # 添加额外字段
        if kwargs:
            record.update(kwargs)

        return json.dumps(record, ensure_ascii=False)

    def debug(self, message: str, **kwargs):
        """DEBUG 级别日志"""
        if self.logger.isEnabledFor(LogLevel.DEBUG):
            record = self._build_log_record("DEBUG", message, **kwargs)
            self.logger.debug(record)

    def info(self, message: str, **kwargs):
        """INFO 级别日志"""
        record = self._build_log_record("INFO", message, **kwargs)
        self.logger.info(record)

    def warning(self, message: str, **kwargs):
        """WARNING 级别日志"""
        record = self._build_log_record("WARNING", message, **kwargs)
        self.logger.warning(record)

    def error(self, message: str, **kwargs):
        """ERROR 级别日志"""
        record = self._build_log_record("ERROR", message, **kwargs)
        self.logger.error(record)

    def critical(self, message: str, **kwargs):
        """CRITICAL 级别日志"""
        record = self._build_log_record("CRITICAL", message, **kwargs)
        self.logger.critical(record)

    def log_request(self, method: str, path: str, status_code: int, duration: float, **kwargs):
        """记录请求日志"""
        self.info(
            "Request completed",
            request={
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            },
            **kwargs,
        )

    def log_pipeline(self, stage: str, status: str, **kwargs):
        """记录管线执行日志"""
        if status.lower() in ["failed", "error", "exception"]:
            self.error(
                f"Pipeline stage {stage} {status}",
                pipeline={"stage": stage, "status": status},
                **kwargs,
            )
        else:
            self.info(
                f"Pipeline stage {stage} {status}",
                pipeline={"stage": stage, "status": status},
                **kwargs,
            )

    def log_error(self, error: Exception, context: str = "", **kwargs):
        """记录异常日志"""
        self.error(
            str(error),
            error={
                "type": type(error).__name__,
                "message": str(error),
                "context": context,
                "traceback": traceback.format_exc(),
            },
            **kwargs,
        )


def _configure_root_logger():
    """配置根 logger，让所有 logging.info() 自动输出 JSON"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除默认 handler
    root_logger.handlers.clear()

    # 添加结构化 handler — Node.js 侧 iconv-lite 处理编码，Python 直接输出 UTF-8
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(StructuredFormatter(ensure_ascii=False))
    root_logger.addHandler(console_handler)

    # 文件 handler — 文件用 UTF-8 编码，中文可读
    log_dir = Path(LOGS_DIR)
    log_dir.mkdir(exist_ok=True)

    file_handler = SafeTimedRotatingFileHandler(
        log_dir / "root.log", when="D", interval=1, backupCount=30, encoding="utf-8"
    )
    file_handler.setFormatter(StructuredFormatter(ensure_ascii=False))
    root_logger.addHandler(file_handler)


# 请求日志装饰器
def log_request(func):
    """装饰器：记录请求日志（同步版本）"""

    def wrapper(*args, **kwargs):
        import time

        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            # 尝试从请求对象获取 method 和 path
            method = "GET"
            path = "/unknown"
            if args:
                arg0 = args[0]
                if hasattr(arg0, "method"):
                    method = arg0.method
                if hasattr(arg0, "url"):
                    path = str(arg0.url)

            logger.log_request(method=method, path=path, status_code=200, duration=duration)
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.log_error(e, context="request_handler")
            # 记录失败请求
            logger.log_request(
                method="UNKNOWN", path="/unknown", status_code=500, duration=duration
            )  # noqa: E501
            raise

    return wrapper


# async 版本请求日志装饰器
def log_request_async(func):
    """装饰器：记录请求日志（异步版本）"""

    async def wrapper(*args, **kwargs):
        import time

        start_time = time.time()

        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            method = "GET"
            path = "/unknown"
            if args:
                arg0 = args[0]
                if hasattr(arg0, "method"):
                    method = arg0.method
                if hasattr(arg0, "url"):
                    path = str(arg0.url)

            logger.log_request(method=method, path=path, status_code=200, duration=duration)
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.log_error(e, context="request_handler")
            logger.log_request(
                method="UNKNOWN", path="/unknown", status_code=500, duration=duration
            )  # noqa: E501
            raise

    return wrapper


# 获取结构化日志记录器
def get_logger(name: str) -> StructuredLogger:
    """获取结构化日志记录器"""
    return StructuredLogger(name)


# 通用日志记录器
logger = get_logger("app")


def init_logging(level: int = LogLevel.INFO):
    """初始化日志系统"""
    # 设置根日志级别
    logging.root.setLevel(level)

    # 配置根 logger，让所有 logging.info() 输出 JSON
    _configure_root_logger()

    # 确保日志目录存在
    log_dir = Path(LOGS_DIR)
    log_dir.mkdir(exist_ok=True)

    # 使用标准 logging 打印初始化消息（此时根 logger 已配置为 JSON 格式）
    logging.info(f"Structured logging system initialized, level={logging.getLevelName(level)}")
