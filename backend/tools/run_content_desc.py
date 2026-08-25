"""用本地 Qwen3-VL-8B 生成视频内容描述（画面/人物/情节/字幕/风格）"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from services.qc.qc_service import _extract_frames  # noqa: E402

VIDEO = r"D:\1\2\director\backend\output\output\export_85cbf5da.mp4"
SERVER = "http://127.0.0.1:8082/v1/chat/completions"
OUT = r"D:\1\2\director\backend\data\generated\qc\content_desc.json"

PROMPT = (
    "请详细描述这段视频的内容，用中文分点输出 JSON：\n"
    '{"scene":"画面场景（环境/地点/背景）","characters":"出现的人物（外貌/服装/动作）",'
    '"plot":"情节与叙事（发生了什么，按时间顺序）","subtitle":"视频中的字幕/文案文字（如有，逐条列出）",'
    '"style":"整体风格与氛围（色调/节奏/情绪）"}\n'
    "只输出 JSON，不要其他文字。"
)


def main():
    frames = _extract_frames(VIDEO, max_frames=6, target_width=768)
    if not frames:
        print("[FAIL] 抽帧失败")
        return 1
    content = []
    for b64 in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": PROMPT})
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": "你是专业的视频内容分析员。"},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    print("调用本地模型生成内容描述...")
    r = httpx.post(SERVER, json=payload, timeout=300)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    print("原始输出:", text[:500])
    # 抽取 JSON
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        print("[FAIL] 未解析到 JSON")
        return 1
    data = json.loads(m.group(0))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {OUT}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
