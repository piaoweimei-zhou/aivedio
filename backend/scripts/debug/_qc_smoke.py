"""临时 QC 链路验证脚本：生成测试视频 -> 跑 run_qc_async（cv2 + 8082 语义）-> 打印结果。"""
import asyncio
import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.qc.qc_service import run_qc_async  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "qc_smoke_test.mp4")


def make_test_video(path: str):
    w, h, fps, n = 640, 360, 25, 75  # 3 秒
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(path, fourcc, fps, (w, h))
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        # 彩色渐变 + 移动方块（避免黑屏/模糊）
        grad = np.linspace(0, 255, w, dtype=np.uint8)
        frame[:] = cv2.cvtColor(grad, cv2.COLOR_GRAY2BGR)
        x = int((i / n) * (w - 80))
        cv2.rectangle(frame, (x, 140), (x + 80, 220), (0, 200, 255), -1)
        cv2.putText(frame, f"QC TEST {i}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        vw.write(frame)
    vw.release()


async def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    make_test_video(OUT)
    print(f"[smoke] test video -> {OUT} ({os.path.getsize(OUT)} bytes)")

    # 含 medium 级敏感词，验证本地关键词兜底 + 语义分
    caption = "关注领取福利，加私信获取更多，今天聊聊AI视频制作"
    res = await run_qc_async(OUT, caption=caption, threshold=60.0, use_semantic=True, manage_server=False)  # noqa: E501
    d = res.to_dict()
    print("=== QC RESULT ===")
    print("total_score   :", d["total_score"])
    print("passed        :", d["passed"])
    print("blocked       :", d["blocked"], d["blocked_reasons"])
    print("model_used    :", d["model_used"])
    print("dimensions    :")
    for dim in d["dimensions_list"]:
        print(f"   - {dim['name']:14s} {dim['score']}")
    print("compliance_hits :", d["compliance_hits"])
    print("compliance_detail:", d["compliance_detail"])
    print("copyright_hits :", d["copyright_hits"])
    print("notes         :", d["notes"])
    print("summary       :", d["summary"])


if __name__ == "__main__":
    asyncio.run(main())
