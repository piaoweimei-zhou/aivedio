# -*- coding: utf-8 -*-
"""契约 OpenAPI 防漂移测试：openapi-contract.yaml 必须与真实实现一致。

单一事实源校验：契约文档由 tools/export_contract_openapi.py 从 app.openapi()
自动导出。本测试在 CI 强制"文档路径/方法/schema 字段"与实现零漂移——
契约违约（实现改了文档没更，或反之）即失败。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_PROCESS_MANAGEMENT", "true")

from main import app  # noqa: E402

OPENAPI_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "04_工程化", "openapi-contract.yaml",
)

CORE_SCHEMAS = ["ContentSpec", "ProduceResponse", "TaskDetail", "Capabilities"]


@pytest.fixture(scope="module")
def doc_openapi():
    import yaml
    assert os.path.exists(OPENAPI_YAML), f"契约文档缺失: {OPENAPI_YAML}"
    with open(OPENAPI_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def impl_openapi():
    schema = app.openapi()
    paths = {
        k: v for k, v in schema.get("paths", {}).items()
        if k.startswith("/contract")
    }
    comps = schema.get("components", {}).get("schemas", {})
    return {"paths": paths, "schemas": comps}


def _methods(path_item):
    return set(m.upper() for m in path_item if m in ("get", "post", "put", "delete", "patch"))


def test_contract_paths_match(doc_openapi, impl_openapi):
    doc_paths = set(doc_openapi["paths"])
    impl_paths = set(impl_openapi["paths"])
    assert doc_paths == impl_paths, (
        f"契约 path 集合漂移\n  文档独有: {doc_paths - impl_paths}\n  实现独有: {impl_paths - doc_paths}"
    )


def test_contract_methods_match(doc_openapi, impl_openapi):
    for path in doc_openapi["paths"]:
        doc_methods = _methods(doc_openapi["paths"][path])
        impl_methods = _methods(impl_openapi["paths"][path])
        assert doc_methods == impl_methods, f"{path} 方法漂移: 文档{doc_methods} vs 实现{impl_methods}"


def test_core_schema_fields_match(doc_openapi, impl_openapi):
    for name in CORE_SCHEMAS:
        doc_schema = doc_openapi["components"]["schemas"].get(name)
        impl_schema = impl_openapi["schemas"].get(name)
        assert doc_schema, f"文档缺 schema: {name}"
        assert impl_schema, f"实现缺 schema: {name}"
        doc_fields = set(doc_schema.get("properties", {}).keys())
        impl_fields = set(impl_schema.get("properties", {}).keys())
        assert doc_fields == impl_fields, (
            f"{name} 字段漂移\n  文档独有: {doc_fields - impl_fields}\n  实现独有: {impl_fields - doc_fields}"
        )


def test_doc_has_security_scheme(doc_openapi):
    schemes = doc_openapi.get("components", {}).get("securitySchemes", {})
    assert "X-API-Key" in schemes, "契约文档必须声明 X-API-Key 鉴权（与 require_api_key 一致）"
