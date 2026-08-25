"""端到端确认：真实调用 ScriptStage 用 DeepSeek 生成一篇剧本并落盘。"""

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from services.stages.script_stage import ScriptStage  # noqa: E402


async def main():
    stage = ScriptStage()
    params = {
        "topic": "打工人熬夜做 PPT，发现用 AI 排版工具能一键出稿",
        "video_type": "full_ai_short",
        "acts": 3,
        "duration_seconds": 30,
        "characters": ["打工人小李", "领导王总"],
        "target_audience": "职场新人",
        "tone_extra": "职场穿越反转，结尾钩子",
        "temperature": 0.85,
        "max_tokens": 4000,
    }
    t = time.time()
    res = await stage.execute([], "openai_compat", params)
    elapsed = time.time() - t
    print(f"[e2e] elapsed_s={round(elapsed, 1)}")

    print("[e2e] success =", getattr(res, "success", None))
    print("[e2e] error =", getattr(res, "error", None))
    asset = getattr(res, "asset", None)
    print("[e2e] asset =", asset)
    if asset:
        print("[e2e] asset.id =", getattr(asset, "id", None))
        print(
            "[e2e] asset fields =",
            {
                k: getattr(asset, k, None)
                for k in ("name", "url", "file_path", "path", "asset_type", "type")
            },
        )
    s = json.dumps(res, ensure_ascii=False, default=str)
    print("[e2e] res_json =", s[:300])

    # 兜底：全局找最近生成的 script json
    import glob

    hits = glob.glob(os.path.join(os.getcwd(), "**", "*.json"), recursive=True)
    hits = [h for h in hits if "script" in h.lower()]
    hits.sort(key=os.path.getmtime, reverse=True)
    if hits:
        newest = hits[0]
        print("[e2e] newest_json =", newest)
        print("[e2e] newest_json_age_s =", int(time.time() - os.path.getmtime(newest)))


def _run():
    import logging

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    asyncio.run(main())


_run()


asyncio.run(main())
