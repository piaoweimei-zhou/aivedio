"""Seedance 视频模型真实端到端验证脚本（非 CI，需 ARK_API_KEY）

用法: backend/.venv-test/Scripts/python.exe scripts/debug/seedance_live_check.py
"""

import asyncio
import os
import sys
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from services.providers.volcengine_provider import VolcEngineProvider  # noqa: E402


async def main():
    provider = VolcEngineProvider()
    if not provider.is_available():
        print("FAIL: ARK_API_KEY 未配置")
        return 1

    model = provider._get_video_model()
    print(f"[Seedance] model={model} | base={provider._get_base_url()}")

    # 用之前火山引擎生成的橘猫窗台图作为首帧
    first_frame = "/output/output/volc_b6f365d613.jpg"
    if not os.path.exists(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output",
            "output",
            "volc_b6f365d613.jpg",
        )
    ):  # noqa: E501
        print("WARN: 首帧图片不存在，将退化为文生视频")
        first_frame = None

    start = time.time()
    try:
        result = await provider.generate_video(
            prompt="橘猫趴在窗台上，阳光洒落，猫咪轻轻转头看向镜头，耳朵微动，画面温馨",
            images=[first_frame] if first_frame else None,
            model=model,
            duration=5,
            aspect_ratio="16:9",
            resolution="480p",
        )
        elapsed = int(time.time() - start)
        print(
            f"[Seedance] 成功 | video_url={result.video_url} | elapsed={elapsed}s | status={result.status}"  # noqa: E501
        )  # noqa: E501
        return 0
    except Exception as e:
        elapsed = int(time.time() - start)
        print(f"[Seedance] 失败 | elapsed={elapsed}s | error={e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
