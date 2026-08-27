# -*- coding: utf-8 -*-
"""从 FastAPI app.openapi() 生成契约 OpenAPI YAML（单一事实源，防漂移）。

契约端点（/api/contract/*）的 OpenAPI 定义由真实代码自动导出，
避免手写与实现漂移。落盘 docs/04_工程化/openapi-contract.yaml。
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))  # backend 根目录（main 所在）
os.environ.setdefault("DISABLE_PROCESS_MANAGEMENT", "true")

from main import app  # noqa: E402

CONTRACT_SCHEMA_KEYWORDS = [
    "ContentSpec", "Produce", "TaskDetail", "AssetInfo", "ErrorPayload",
    "Capabilities", "Cancel", "Claim", "Start", "Tasks", "TaskSummary",
]


def main():
    schema = app.openapi()
    paths = schema.get("paths", {})
    contract_paths = {
        k: v for k, v in paths.items() if k.startswith("/contract")
    }
    comps = schema.get("components", {}).get("schemas", {})
    contract_schemas = {
        k: v for k, v in comps.items()
        if any(w in k for w in CONTRACT_SCHEMA_KEYWORDS)
    }
    # 附带所有被契约 schema 引用的 component schema（如 AnyOf 引用）
    refs = set()

    def collect_refs(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                refs.add(obj["$ref"].split("/")[-1])
            for v in obj.values():
                collect_refs(v)
        elif isinstance(obj, list):
            for v in obj:
                collect_refs(v)
    for s in contract_schemas.values():
        collect_refs(s)
    for p in contract_paths.values():
        collect_refs(p)
    extra = {r: comps[r] for r in refs if r in comps and r not in contract_schemas}

    out = {
        "openapi": schema.get("openapi", "3.1.0"),
        "info": {
            "title": "Director 生产契约 API (TrafficOS ↔ Director)",
            "version": "1.0.0",
            "description": (
                "流量侧与导演侧的唯一契约事实源。由 backend/api/contract_api.py "
                "的真实模型自动导出（生成器: backend/tools/export_contract_openapi.py）。"
                "任何实现若与本文档不一致即为契约违约，CI test_openapi_contract.py 强制校验。"
            ),
        },
        "servers": [{"url": "http://127.0.0.1:8000", "description": "本地导演侧"}],
        "security": [{"X-API-Key": []}],
        "components": {
            "schemas": {**contract_schemas, **extra},
            "securitySchemes": {
                "X-API-Key": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "导演侧 API key（与契约层 require_api_key 一致）",
                }
            },
        },
        "paths": contract_paths,
    }

    out_path = os.path.normpath(
        os.path.join(_HERE, "..", "docs", "04_工程化", "openapi-contract.yaml")
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    import yaml
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)
    print(f"已生成: {out_path}")
    print(f"  契约 paths: {len(contract_paths)} 个")
    print(f"  contract schemas: {len(contract_schemas)} 个 + 引用 {len(extra)} 个")
    for p in sorted(contract_paths):
        methods = ",".join(m.upper() for m in contract_paths[p])
        print(f"    {p}  [{methods}]")


if __name__ == "__main__":
    main()
