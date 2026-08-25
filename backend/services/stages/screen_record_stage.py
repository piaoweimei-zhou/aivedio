"""
录屏阶段

支持两种模式：
  - mode="record": 调用 ffmpeg gdigrab（Windows）/ x11grab（Linux）录制指定窗口/区域
  - mode="upload": 接收用户上传的录屏文件，仅做资产注册（最简单稳定）

支持平台：
  - Windows: ffmpeg -f gdigrab -i title="窗口标题"
  - Linux:   ffmpeg -f x11grab -i :0.0+x,y
  - macOS:   ffmpeg -f avfoundation -i "1:0"

输出：video 资产
"""

import asyncio
import logging
import os
import platform
import time
import uuid
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.stage_service import StageDef, StagePlugin
from services.stages.ffmpeg_utils import _ffmpeg_bin

logger = logging.getLogger(__name__)


class ScreenRecordStage(StagePlugin):
    """录屏阶段（record + upload 双模式）"""

    stage_def = StageDef(
        stage_id="screen_record",
        name="屏幕录制",
        input_types=[],  # 录屏不需要输入资产（upload 模式由前端上传后传入 url）
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="屏幕录制（支持 ffmpeg gdigrab/x11grab/avfoundation 和上传两种模式）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        start = time.time()
        asset_svc = get_asset_service()

        mode = params.get("mode", "record")  # record / upload
        duration = float(params.get("duration", 30))
        fps = int(params.get("fps", 15))
        output_name = params.get("name", f"录屏_{int(start)}")

        logger.info(
            f"[ScreenRecordStage] mode={mode} | duration={duration}s | fps={fps} | name={output_name}"  # noqa: E501
        )

        try:
            if mode == "upload":
                # upload 模式：input_assets 第一个资产的 URL 即为上传的录屏
                if not input_assets or not input_assets[0].urls:
                    return self._error_result(
                        "upload 模式需要传入上传的录屏文件 URL（input_assets[0].urls[0]）"
                    )
                result_url = input_assets[0].urls[0]
                logger.info(f"[ScreenRecordStage] upload 模式 | url={result_url}")

            elif mode == "record":
                # record 模式：调用 ffmpeg 抓屏
                result_url = await self._record_screen(
                    window_title=params.get("window_title", ""),
                    region=params.get("region", ""),  # "x,y,w,h"
                    display=params.get("display", ""),  # Linux X11 display
                    duration=duration,
                    fps=fps,
                    extra_ffmpeg_args=params.get("ffmpeg_args", []),
                )
            else:
                return self._error_result(f"不支持的 mode: {mode}，可选: record / upload")

            elapsed = int((time.time() - start) * 1000)
            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="video",
                name=output_name,
                urls=[result_url],
                input_assets=input_assets if mode == "upload" else None,
                extra_metadata={
                    "mode": mode,
                    "duration": duration,
                    "fps": fps,
                    "window_title": params.get("window_title", ""),
                    "region": params.get("region", ""),
                    "video_url": result_url,
                    "source": "screen_record",
                    "elapsed_ms": elapsed,
                },
                content_type="",
            )

            logger.info(
                f"[ScreenRecordStage] 录屏完成 | id={new_asset.asset_id} | "
                f"url={result_url} | elapsed={elapsed}ms"
            )
            return AssetProduceResult(
                asset=new_asset,
                success=True,
                elapsed_ms=elapsed,
            )

        except Exception as e:
            logger.error(f"[ScreenRecordStage] 录屏失败: {e}")
            return self._error_result(str(e))

    # ──────────────────────────────────────────────────────────
    # ffmpeg 录屏实现
    # ──────────────────────────────────────────────────────────
    async def _record_screen(
        self,
        window_title: str = "",
        region: str = "",
        display: str = "",
        duration: float = 30,
        fps: int = 15,
        extra_ffmpeg_args: List[str] = None,
    ) -> str:
        """调用 ffmpeg 录屏

        Args:
            window_title: Windows 窗口标题（含此关键字匹配）。空=全屏
            region: "x,y,w,h" 录制区域。空=全屏
            display: Linux X11 display（如 ":0.0"），空=自动
            duration: 录制时长（秒）
            fps: 帧率
            extra_ffmpeg_args: 额外 ffmpeg 输入参数

        Returns:
            视频文件 URL
        """
        from services.providers.provider_utils import output_path_for, output_url_for

        ffmpeg = _ffmpeg_bin()
        await self._check_ffmpeg(ffmpeg)

        system = platform.system()
        output_file = output_path_for(
            f"screen_{uuid.uuid4().hex[:8]}_{int(time.time())}.mp4",
            "output",
        )

        args: List[str] = [ffmpeg, "-y"]

        # ── 输入参数（按平台） ───────────────────────────────
        if system == "Windows":
            args += ["-f", "gdigrab", "-framerate", str(fps)]
            if window_title:
                args += ["-i", f'title="{window_title}"']
            else:
                args += ["-i", "desktop"]
                # gdigrab 全屏录制 + 区域裁剪（通过 crop filter）
                if region:
                    x, y, w, h = self._parse_region(region)
                    # crop=w:h:x:y
                    args += ["-filter:v", f"crop={w}:{h}:{x}:{y}"]
        elif system == "Linux":
            args += ["-f", "x11grab", "-framerate", str(fps)]
            if region:
                x, y, w, h = self._parse_region(region)
                args += ["-video_size", f"{w}x{h}", "-i", f"{display or ':0.0'}+{x},{y}"]
            else:
                args += ["-i", display or ":0.0"]
        elif system == "Darwin":
            # macOS avfoundation: "1:0" 表示屏幕索引1 + 音频0
            args += ["-f", "avfoundation", "-framerate", str(fps)]
            args += ["-i", display or "1:0"]
            if region:
                x, y, w, h = self._parse_region(region)
                args += ["-filter:v", f"crop={w}:{h}:{x}:{y}"]
        else:
            raise RuntimeError(f"不支持的平台: {system}")

        if extra_ffmpeg_args:
            args += extra_ffmpeg_args

        # ── 时长 + 输出编码 ────────────────────────────────
        args += [
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",  # 录屏实时性优先
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            output_file,
        ]

        logger.info(f"[ScreenRecordStage] ffmpeg 命令: {' '.join(args)}")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 录制会阻塞 duration 秒，需要等待
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ffmpeg 录屏失败 (code={proc.returncode}): {err_msg}")

        if not os.path.exists(output_file):
            raise RuntimeError(f"ffmpeg 录屏输出文件未生成: {output_file}")

        url = output_url_for(os.path.basename(output_file), "output")
        logger.info(
            f"[ScreenRecordStage] 录屏文件已保存 | path={output_file} | size={os.path.getsize(output_file)} bytes"  # noqa: E501
        )  # noqa: E501
        return url

    async def _check_ffmpeg(self, ffmpeg: str):
        """检查 ffmpeg 是否可用"""
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg 返回非零状态")
        except FileNotFoundError:
            raise RuntimeError(
                f"ffmpeg 未安装或路径无效: {ffmpeg}，请安装 ffmpeg 或设置 FFMPEG_PATH 环境变量"
            )

    def _parse_region(self, region: str) -> tuple:
        """解析 "x,y,w,h" 字符串"""
        try:
            parts = [int(p.strip()) for p in region.split(",")]
            if len(parts) != 4:
                raise ValueError("region 格式应为 'x,y,w,h'")
            return tuple(parts)
        except (ValueError, TypeError) as e:
            raise ValueError(f"region 参数无效 '{region}': {e}")


# ============================================================
# 辅助：列出当前可录制的窗口（Windows gdigrab）
# ============================================================
async def list_windows_async() -> List[Dict[str, str]]:
    """列出当前可录制的窗口标题（Windows 专用）

    通过 PowerShell 调用 Get-Process 获取可见窗口列表。
    Linux/macOS 暂不支持枚举，返回空列表。
    """
    if platform.system() != "Windows":
        return []

    try:
        # 通过 PowerShell 列出所有可见的窗口标题
        ps_script = (
            "Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | "
            "Select-Object MainWindowTitle, ProcessName | ConvertTo-Json"
        )
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-Command",
            ps_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return []

        import json

        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [
            {"title": item.get("MainWindowTitle", ""), "process": item.get("ProcessName", "")}
            for item in data
            if item.get("MainWindowTitle")
        ]
    except Exception as e:
        logger.warning(f"[ScreenRecordStage] 列出窗口失败: {e}")
        return []
