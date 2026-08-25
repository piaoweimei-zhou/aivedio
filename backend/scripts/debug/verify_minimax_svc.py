"""MiniMax H3 真机 smoke：走生产路径 ComfyUIService.generate_minimax_h3
真正生成一条短视频并回读 mp4 文件，验证整条本地链路（构建→提交→轮询→gifs 解析）。
用法: python verify_minimax_svc.py [duration_seconds]
"""
import asyncio  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

os.environ.setdefault("COMFYUI_DIR", r"D:\1\2\ComfyUI_windows_portable\ComfyUI")
os.environ.setdefault("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
sys.path.insert(0, r"D:\1\2\director\backend")

from services.comfyui_service import get_comfyui_service  # noqa: E402

prompt = "夏日傍晚的湖边，一只白鹭从水面掠起，平静倒影随涟漪轻荡"


async def main():
    svc = get_comfyui_service()
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    print(f"[smoke] prompt={prompt[:40]}... duration={duration}s")

    def prog(pct_desc, pct):
        print(f"[smoke] 进度 | {pct_desc} | {pct}%", flush=True)

    try:
        result = await svc.generate_minimax_h3(
            prompt=prompt,
            width=480, height=864,
            duration_seconds=duration,
            seed=7,
            audio_mode="native",
            filename_prefix="mmh3_smoke",
            progress_callback=prog,
        )
        print("[smoke] RESULT:")
        print(f"  prompt_id = {result.prompt_id}")
        print(f"  filename  = {result.filename}")
        print(f"  url       = {result.image_url}")
        print(f"  elapsed   = {result.elapsed_ms}ms")
        print("[smoke] PASS" if result.filename else "[smoke] FAIL: 无输出文件")
    except Exception as e:
        print(f"[smoke] FAIL: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
