"""ffmpeg/ffprobe 本地处理工具（字幕烧录、钩子叠加等后处理阶段共用）"""
import asyncio
import logging
import os
import uuid
from typing import List

logger = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    p = os.getenv("FFMPEG_PATH", "ffmpeg")
    # 兼容配置为 bin 目录的情况（系统变量可能只指向目录）
    if os.path.isdir(p):
        return os.path.join(p, "ffmpeg.exe")
    return p


def _ffprobe_bin() -> str:
    p = os.getenv("FFPROBE_PATH", "ffprobe")
    if os.path.isdir(p):
        return os.path.join(p, "ffprobe.exe")
    return p


async def check_ffmpeg() -> None:
    """检查 ffmpeg 是否可用，不可用抛 RuntimeError"""
    try:
        proc = await asyncio.create_subprocess_exec(
            _ffmpeg_bin(), "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg 不可用")
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 未安装，请安装 ffmpeg 或设置 FFMPEG_PATH")


async def run_ffmpeg(args: List[str]) -> None:
    """执行 ffmpeg，失败抛 RuntimeError"""
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg_bin(), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 执行失败: {stderr.decode('utf-8', errors='replace')[:800]}")


async def get_video_duration(path: str) -> float:
    """获取视频时长（秒）"""
    proc = await asyncio.create_subprocess_exec(
        _ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 获取时长失败: {stderr.decode('utf-8', errors='replace')[:300]}")
    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise RuntimeError(f"无法解析视频时长: {stdout.decode().strip()}")


async def get_video_size(path: str):
    """获取视频宽高 (width, height)"""
    proc = await asyncio.create_subprocess_exec(
        _ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 获取尺寸失败: {stderr.decode('utf-8', errors='replace')[:300]}")
    try:
        w, h = stdout.decode().strip().split("x")
        return int(w), int(h)
    except Exception:
        raise RuntimeError(f"无法解析视频尺寸: {stdout.decode().strip()}")


async def resolve_local_video(url: str) -> str:
    """把视频 URL 解析为本地文件路径（远程则下载）"""
    from services.providers.provider_utils import output_file_from_url, output_path_for

    if url.startswith(("http://", "https://")):
        import httpx
        temp_path = output_path_for(f"temp_{uuid.uuid4().hex[:8]}.mp4", "temp")
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=20.0, read=300.0)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            with open(temp_path, "wb") as f:
                f.write(resp.content)
        return temp_path
    local = output_file_from_url(url)
    if local and os.path.exists(local):
        return local
    raise RuntimeError(f"视频文件不存在: {url}")
