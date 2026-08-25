"""重新生成三幕 TTS 配音（正确中文编码）"""
import sys  # noqa: E402
sys.path.insert(0, r"d:\1\2\director\backend\output\temp")
from api_client import submit_stage, wait_task  # noqa: E402

texts = [
    "家人们谁懂啊！明天要交100页的部门汇报PPT，我熬到凌晨三点半，一个字都憋不出来！",
    "本来我都准备写辞职信了，结果同事甩给我个AI工具，我随便输了个需求，居然3秒就生成好了？",
    "整整100页啊！排版内容数据全给我弄好了，我直接给这AI跪了好吗！想要的家人们评论区扣1，我私发你链接！",
]

task_ids = []
for i, t in enumerate(texts, 1):
    r = submit_stage("tts", [], "comfyui", {
        "text": t,
        "mode": "voice_design",
        "voice_description": "年轻女声，情绪饱满，语速稍快，适合短视频",
        "language": "Auto",
    })
    task_ids.append(r["task_id"])
    print(f"act{i}: {r['task_id']}")

for i, tid in enumerate(task_ids, 1):
    t = wait_task(tid, timeout_s=300)
    print(f"act{i}: status={t['status']} success={t.get('success')} err={t.get('error')}")
    if t.get("result") and t["result"].get("asset"):
        a = t["result"]["asset"]
        print(f"  asset_id={a['asset_id']} urls={a['urls']}")
        print(f"  text={a['metadata'].get('text', '')[:40]}")
