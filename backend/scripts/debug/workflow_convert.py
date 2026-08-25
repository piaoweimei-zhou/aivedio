"""ComfyUI UI 工作流(json) → API prompt(json) 转换
标准 litegraph 保存格式。复用 ComfyUI 前端 getPrompt 的装配逻辑：
  有 link 的 input → 用量化节点引用 [origin_id, origin_slot_output_name]
  无 link 的 widget input → 从 widgets_values 顺序弹出
"""
import json
import sys


def ui_to_api(ui):
    if "nodes" not in ui or "links" not in ui:
        raise ValueError("不是 litegraph 保存格式（缺 nodes/links）")

    nodes = {n["id"]: n for n in ui["nodes"]}
    links = {}
    for link in ui["links"]:
        # [link_id, origin_id, origin_slot, target_id, target_slot, type]
        links[link[0]] = link

    SKIP_TYPES = {
        "Note", "MarkdownNote", "Fast Groups Bypasser (rgthree)", "output",
        "PrimitiveFloat", "PrimitiveStringMultiline",
    }
    prompt = {}
    for n in ui["nodes"]:
        ct = n["type"]
        if ct in SKIP_TYPES:
            continue
        if ct == "input":
            continue
        inputs = {}
        widgets = list(n.get("widgets_values", []) or [])
        w = 0
        for inp in n.get("inputs", []) or []:
            name = inp.get("name")
            if inp.get("link") is not None:
                lk = links.get(inp["link"])
                if not lk:
                    continue
                origin_id, origin_slot = lk[1], lk[2]
                oname = ""
                outs = nodes.get(origin_id, {}).get("outputs", []) or []
                if origin_slot < len(outs):
                    oname = outs[origin_slot].get("name", "")
                inputs[name] = [origin_id, oname]
            else:
                # 数值/字符串 widget 值
                if w < len(widgets):
                    inputs[name] = widgets[w]
                    w += 1
                else:
                    # 可能 input 自带 value
                    inputs[name] = inp.get("value", None)
        meta = {}
        if n.get("title") and n.get("title") != ct:
            meta["title"] = n["title"]
        prompt[n["id"]] = {"class_type": ct, "inputs": inputs, "_meta": meta}
    return prompt


def main():
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    with open(src, "r", encoding="utf-8") as f:
        ui = json.load(f)
    prompt = ui_to_api(ui)
    print(f"[convert] 节点数: {len(prompt)}")
    for nid in sorted(prompt.keys(), key=int):
        print(f"  {nid}  {prompt[nid]['class_type']}")
    if dst:
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(prompt, f, ensure_ascii=False, indent=2)
        print(f"[convert] 已写入 {dst}")


if __name__ == "__main__":
    main()
