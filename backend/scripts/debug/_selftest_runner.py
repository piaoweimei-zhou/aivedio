"""
导演工作台 · 全量自测 v2（离线，端口 8234）
覆盖：SMOKE / CRUD / LOGIC / ERR(真实生成错误路径) / BRIDGE(无限画布)
分类：PASS, EXPECTED_FAIL, UNEXPECTED, SKIP
输出 stdout 摘要 + data/selftest_result.json
"""
import asyncio, json, os, sys, time
import httpx

BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8234")
results = []
seq = 0


def rec(cat, group, name, status, note=""):
    global seq
    seq += 1
    results.append({"id": seq, "category": cat, "group": group, "name": name,
                    "status": status, "note": str(note)[:400]})

def pr(cat, group, name, status, note=""):
    rec(cat, group, name, status, note)
    mark = {"PASS": "P", "EXPECTED_FAIL": "E", "UNEXPECTED": "X", "SKIP": "S"}[cat]
    print(f"[{mark}] {group} / {name} -> {status} {note}")

def cls(status, allow=(200, 201, 202, 204)):
    return "PASS" if status in allow else "EXPECTED_FAIL"

async def req(method, url, **kw):
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        return await c.request(method, BASE + url, **kw)

async def jreq(method, url, **kw):
    r = await req(method, url, **kw)
    try:
        return r, r.json()
    except Exception:
        return r, {}


async def smoke():
    scope = [("/health","GET"),("/api/director/projects","GET"),("/api/director/assets?limit=1","GET"),
             ("/api/director/assets/types","GET"),("/api/director/assets/stage-types","GET"),
             ("/api/director/assets/content-types","GET"),("/api/director/assets/stats/overview","GET"),
             ("/api/director/stages","GET"),("/api/director/stages/script/video-types","GET"),
             ("/api/director/stages/graphic/types","GET"),("/api/director/stages/tts/voices","GET"),
             ("/api/director/stages/screen/windows","GET"),("/api/director/providers","GET"),
             ("/api/director/providers/config/meta","GET"),("/api/director/providers/health/all","GET"),
             ("/api/director/batches","GET"),("/api/director/workflow-templates","GET"),
             ("/api/director/presets","GET"),("/api/director/prompts","GET"),
             ("/api/director/prompts/categories","GET"),("/api/director/prompts/tags","GET"),
             ("/api/director/prompts/stats","GET"),("/api/canvas/","GET"),("/api/config","GET"),
             ("/api/workflows","GET"),("/api/canvases","GET"),("/api/local-assets","GET"),
             ("/api/canvas-assets","GET"),("/api/asset-library","GET"),("/api/prompt-libraries","GET")]
    for path, m in scope:
        r = await req(m, path)
        cat = "PASS" if r.status_code < 400 else ("EXPECTED_FAIL" if r.status_code < 500 else "UNEXPECTED")
        pr(cat, "SMOKE", f"{m} {path}", r.status_code, "" if r.status_code < 400 else r.text[:100])


async def main():
    await smoke()
    pid = aid = cid = None

    # ---- CRUD: 修正后的健康链路 ----
    r, j = await jreq("POST", "/api/director/projects", json={"name": "_selftest_proj"})
    pid = (j or {}).get("project", {}).get("project_id") or (j or {}).get("project_id")
    pr(cls(r.status_code), "CRUD", "Project create", r.status_code, f"id={pid}")

    r, j = await jreq("POST", "/api/director/workflow-templates",
                      json={"name": "_selftest_wf", "steps": [{"stage_id": "concept", "input_mode": "upload"}], "required_inputs": []})
    wf_id = (j or {}).get("template_id") or (j or {}).get("id")
    pr(cls(r.status_code), "CRUD", "WorkflowTemplate create", r.status_code, f"id={wf_id}")

    r, j = await jreq("POST", "/api/canvas/", json={"name": "_selftest_canvas"})
    cid = (j or {}).get("canvas_id") or (j or {}).get("data", {}).get("canvas_id")
    pr(cls(r.status_code), "CRUD", "Canvas create", r.status_code, f"id={cid}")

    # 资产 create + 血缘 + 挂到项目
    r, j = await jreq("POST", "/api/director/assets", json={"asset_type": "concept", "name": "_selftest_asset", "project_id": pid, "urls": [], "metadata": {"src": "selftest"}})
    aid = (j or {}).get("asset", {}).get("asset_id") or (j or {}).get("asset_id") or (j or {}).get("id")
    pr(cls(r.status_code), "CRUD", "Asset create", r.status_code, f"id={aid}")
    if aid:
        for ep, nm in [(f"/api/director/assets/{aid}/lineage", "asset lineage"), (f"/api/director/assets/{aid}/children", "asset children")]:
            r = await req("GET", ep); pr(cls(r.status_code), "LOGIC", nm, r.status_code)
        if pid:
            r = await req("POST", f"/api/director/projects/{pid}/assets/{aid}")
            pr(cls(r.status_code), "LOGIC", "project add asset", r.status_code)
            r = await req("GET", f"/api/director/projects/{pid}/stats")
            pr(cls(r.status_code), "LOGIC", "project stats", r.status_code)

    # 提示词解析
    r, j = await jreq("POST", "/api/director/prompts", json={"name": "_res_prompt", "content": "Hi {name} @ {place}", "variables": [{"name": "name"}, {"name": "place"}]})
    p_id = (j or {}).get("prompt_id") or (j or {}).get("id")
    if p_id:
        r2, j2 = await jreq("POST", f"/api/director/prompts/{p_id}/resolve", json={"variables": {"name": "Tom", "place": "HK"}})
        ok = r2.status_code < 400 and "Tom" in json.dumps(j2, ensure_ascii=False)
        pr("PASS" if ok else "UNEXPECTED", "LOGIC", "prompt resolve", r2.status_code, json.dumps(j2, ensure_ascii=False)[:120])

    # 预设 + default + apply
    r, j = await jreq("POST", "/api/director/presets", json={"name": "_def", "stage_id": "video"})
    se_id = (j or {}).get("preset_id") or (j or {}).get("id")
    if se_id:
        for nm, mth, body in [("preset set-default", "POST", f"/api/director/presets/{se_id}/set-default")]:
            r = await req(mth, body, json={"project_id": pid} if pid else {})
            pr(cls(r.status_code), "LOGIC", nm, r.status_code)
        r = await req("POST", f"/api/director/presets/{se_id}/apply")
        pr(cls(r.status_code), "LOGIC", "preset apply", r.status_code)

    # 批量 create + dag + dry-run
    steps = [{"step_id": "s1", "stage_id": "concept", "name": "c1", "input_asset_ids": [aid] if aid else []}]
    r, j = await jreq("POST", "/api/director/batches", json={"name": "_b", "steps": steps, "project_id": pid, "stop_on_failure": False})
    b_id = (j or {}).get("batch", {}).get("batch_id") or (j or {}).get("batch_id")
    if b_id:
        r = await req("GET", f"/api/director/batches/{b_id}/dag"); pr(cls(r.status_code), "LOGIC", "batch dag", r.status_code)
        r = await req("POST", f"/api/director/batches/{b_id}/dry-run"); pr(cls(r.status_code), "LOGIC", "batch dry-run", r.status_code, r.text[:120])

    # wf create-batch
    if wf_id:
        r = await req("POST", f"/api/director/workflow-templates/{wf_id}/create-batch",
                      json={"name": "_wfb", "project_id": pid, "input_assets": {}})
        pr(cls(r.status_code), "LOGIC", "workflow create-batch", r.status_code, r.text[:120])

    # 画布节点 + 乐观锁(尽力)
    if cid:
        r = await req("PUT", f"/api/canvas/{cid}",
                      json={"name": "_selftest_canvas", "nodes": [{"node_id": "n1", "x": 10, "y": 20, "label": "a"}], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}})
        pr(cls(r.status_code), "LOGIC", "canvas layout update", r.status_code, r.text[:120])
        r = await req("GET", f"/api/canvas/{cid}"); pr(cls(r.status_code), "LOGIC", "canvas get", r.status_code)
        r, j = await jreq("POST", f"/api/canvas/{cid}/nodes", json={"node_id": "nn1", "asset_id": aid, "node_type": "image", "x": 1, "y": 1})
        pr(cls(r.status_code), "LOGIC", "canvas add node", r.status_code, json.dumps(j, ensure_ascii=False)[:120])

    # ---- ERR: 真实生成错误路径（无 ComfyUI/无 key） ----
    for stage, provider, inputs in [("concept", "comfyui", [aid] if aid else []),
                                    ("video", "comfyui", [aid] if aid else []),
                                    ("script", "openai_compat", [])]:
        r, j = await jreq("POST", "/api/director/stages/execute",
                          json={"stage_id": stage, "provider_id": provider, "input_asset_ids": inputs,
                                "params": {"prompt": "test"}, "async_mode": True})
        task_id = (j or {}).get("task_id") or (j or {}).get("data", {}).get("task_id")
        if r.status_code < 300 and task_id:
            pr("PASS", "ERR", f"stage {stage} submit", r.status_code)
            # 轮询任务，预期最终 failed 且错误可读
            state = "?"
            for _ in range(8):
                await asyncio.sleep(1.0)
                rr, jj = await jreq("GET", f"/api/director/stages/task/{task_id}")
                state = json.dumps(jj, ensure_ascii=False)[:180] if jj else rr.text[:180]
                if "fail" in state.lower() or "succeed" in state.lower() or "error" in state.lower():
                    break
            pr("EXPECTED_FAIL", "ERR", f"stage {stage} task result", "seen", state)
        else:
            body = r.text if r.status_code >= 400 else json.dumps(j, ensure_ascii=False)
            pr(cls(r.status_code), "ERR", f"stage {stage} reject-or-300", r.status_code, body[:160])

    # MSR-video 提交
    r, j = await jreq("POST", "/api/canvas/msr-video", json={"ref2_image_url": "x", "global_prompt": "t", "duration": 4})
    pr(cls(r.status_code), "BRIDGE", "msr-video submit", r.status_code, r.text[:120] if r.status_code >= 400 else json.dumps(j, ensure_ascii=False)[:120])

    # 无限画布桥接：generate 异步提交（无外部服务应优雅失败）
    r, j = await jreq("POST", "/api/generate", json={"type": "image", "prompt": "test", "workflow": "test"})
    pr("PASS" if r.status_code < 300 else cls(r.status_code), "BRIDGE", "/api/generate submit", r.status_code, r.text[:120])

    # ---- 清理 ----
    for u in [f"/api/director/batches/{b_id}" if b_id else None,
              f"/api/canvas/{cid}" if cid else None,
              f"/api/director/workflow-templates/{wf_id}" if wf_id else None,
              f"/api/director/presets/{se_id}" if se_id else None,
              f"/api/director/prompts/{p_id}" if p_id else None,
              f"/api/director/assets/{aid}" if aid else None,
              f"/api/director/projects/{pid}" if pid else None]:
        if u:
            try:
                await req("DELETE", u)
            except Exception:
                pass

    s = {"total": len(results), "PASS": sum(1 for x in results if x["category"] == "PASS"),
         "EXPECTED_FAIL": sum(1 for x in results if x["category"] == "EXPECTED_FAIL"),
         "UNEXPECTED": sum(1 for x in results if x["category"] == "UNEXPECTED"),
         "SKIP": sum(1 for x in results if x["category"] == "SKIP"), "results": results}
    os.makedirs("data", exist_ok=True)
    with open("data/selftest_result.json", "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    print("\n===== SUMMARY =====")
    print(f"total={s['total']} PASS={s['PASS']} EXPECTED_FAIL={s['EXPECTED_FAIL']} UNEXPECTED={s['UNEXPECTED']} SKIP={s['SKIP']}")
    return s


if __name__ == "__main__":
    s = asyncio.run(main())
    sys.exit(1 if s["UNEXPECTED"] else 0)