"""structured_logging 单元测试：trace_id 上下文、结构化格式化器、日志方法"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.structured_logging import (  # noqa: E402
    get_trace_id,
    set_trace_id,
    clear_trace_id,
    StructuredFormatter,
    StructuredLogger,
    _trace_id_ctx,
)


class TestTraceId:
    def test_auto_generate(self):
        clear_trace_id()
        tid = get_trace_id()
        assert tid and len(tid) > 10

    def test_set_get_clear(self):
        set_trace_id("my-trace-123")
        assert get_trace_id() == "my-trace-123"
        clear_trace_id()
        assert _trace_id_ctx.get() is None


class TestStructuredFormatter:
    def test_format_json(self):
        set_trace_id("t-1")
        fmt = StructuredFormatter(ensure_ascii=False)
        record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, "hello %s", ("world",), None)  # noqa: E501
        out = fmt.format(record)
        data = json.loads(out)
        assert data["level"] == "INFO"
        assert data["message"] == "hello world"
        assert data["logger"] == "test.logger"
        assert data["trace_id"] == "t-1"

    def test_format_extra_attrs(self):
        fmt = StructuredFormatter(ensure_ascii=False)
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
        record.request = {"method": "GET"}
        record.pipeline = {"stage": "s"}
        record.error = {"type": "E"}
        record.extra = {"k": "v"}
        data = json.loads(fmt.format(record))
        assert data["request"]["method"] == "GET"
        assert data["pipeline"]["stage"] == "s"
        assert data["error"]["type"] == "E"
        assert data["k"] == "v"

    def test_already_json(self):
        fmt = StructuredFormatter()
        record = logging.LogRecord("x", logging.INFO, __file__, 1, '{"already": true}', (), None)
        assert fmt.format(record) == '{"already": true}'

    def test_ensure_ascii(self):
        fmt = StructuredFormatter(ensure_ascii=True)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "中文", (), None)
        out = fmt.format(record)
        assert "\\u" in out


class TestStructuredLogger:
    def _make(self):
        return StructuredLogger("test.slf")

    def test_build_record(self):
        lg = self._make()
        rec = lg._build_log_record("INFO", "msg", foo="bar")
        data = json.loads(rec)
        assert data["level"] == "INFO" and data["message"] == "msg" and data["foo"] == "bar"

    def test_level_methods(self):
        lg = self._make()
        lg.debug("d", a=1)
        lg.info("i", a=1)
        lg.warning("w", a=1)
        lg.error("e", a=1)
        lg.critical("c", a=1)

    def test_log_request(self):
        lg = self._make()
        lg.log_request("POST", "/api/x", 200, 0.123)

    def test_log_pipeline_failed(self):
        lg = self._make()
        lg.log_pipeline("gen", "failed")
        lg.log_pipeline("gen", "ok")

    def test_log_error(self):
        lg = self._make()
        try:
            raise ValueError("bad")
        except ValueError as e:
            lg.log_error(e, context="ctx")
