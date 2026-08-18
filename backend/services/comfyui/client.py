"""
ComfyUI HTTP 客户端 — 纯 HTTP 调用封装

职责：
- 管理 aiohttp session
- 提交工作流到 ComfyUI（_queue_prompt）
- 等待生成完成（_wait_for_completion）
- 查询队列进度（get_queue_progress）
- 健康检查（check_health）
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable

import aiohttp

logger = logging.getLogger(__name__)

# 任务超时配置（秒）
TASK_TIMEOUTS = {
    "generate": 300,
    "refine": 600,
    "standardize_3": 600,
    "standardize_6": 1200,
    "storyboard": 900,
    "storyboard_v2": 900,
    "batch_storyboard": 1800,
    "scene_multiangle": 600,
    "costume_change": 600,
    "panorama": 600,
    "pose_transfer": 600,
    "multi_frame": 600,
}
MAX_POLL_TIME = 600
POLL_INTERVAL = 0.5


class ComfyUIClient:
    """ComfyUI HTTP 客户端

    封装所有与 ComfyUI HTTP API 的交互，包括：
    - 提交工作流
    - 等待生成完成
    - 查询队列进度
    - 健康检查
    """

    def __init__(self, base_url: str, output_dir: str):
        self.base_url = base_url
        self.output_dir = output_dir
        self._http_session: Optional[aiohttp.ClientSession] = None

    def get_http_session(self) -> aiohttp.ClientSession:
        """获取共享 aiohttp session（复用连接，减少内存碎片）"""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close_http_session(self):
        """关闭共享 aiohttp session"""
        if self._http_session and not self._http_session.closed:
            try:
                await self._http_session.close()
            except Exception:
                pass
            self._http_session = None

    def invalidate_session(self):
        """标记 session 需要重建（同步方法无法 await close）"""
        self._http_session = None

    # ── 健康检查 ──────────────────────────────────────────────

    async def check_alive(self) -> bool:
        """检查 ComfyUI 是否在线"""
        try:
            session = self.get_http_session()
            async with session.get(
                f"{self.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def check_health(self) -> Dict[str, Any]:
        """检查 ComfyUI 健康状态"""
        try:
            session = self.get_http_session()
            async with session.get(
                f"{self.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "healthy",
                        "system": data.get("system", {}),
                        "devices": data.get("devices", []),
                    }
                return {"status": "unhealthy", "http_code": resp.status}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    # ── 队列操作 ──────────────────────────────────────────────

    async def get_queue_progress(self, prompt_id: str) -> Dict[str, Any]:
        """查询 ComfyUI 队列中指定任务的进度"""
        try:
            session = self.get_http_session()
            async with session.get(
                f"{self.base_url}/queue",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return {"in_queue": False}
                data = await resp.json()
                running = data.get("queue_running", [])
                pending = data.get("queue_pending", [])

                for idx, item in enumerate(running):
                    if len(item) >= 1 and item[0] == prompt_id:
                        return {
                            "in_queue": True,
                            "status": "running",
                            "position": idx,
                            "progress": idx + 1,
                        }

                for idx, item in enumerate(pending):
                    if len(item) >= 1 and item[0] == prompt_id:
                        return {
                            "in_queue": True,
                            "status": "pending",
                            "position": idx,
                            "progress": 0,
                        }

                return {"in_queue": False}
        except Exception as e:
            logger.warning(f"[ComfyUI] 查询队列进度失败: {e}")
            return {"in_queue": False, "error": str(e)}

    async def clear_queue(self):
        """清空 ComfyUI 队列"""
        try:
            session = self.get_http_session()
            async with session.post(
                f"{self.base_url}/queue",
                json={"clear": True},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                pass
        except Exception as e:
            logger.warning(f"[ComfyUI] 清空队列失败: {e}")

    # ── 提交工作流 ──────────────────────────────────────────

    async def queue_prompt(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI

        Returns:
            prompt_id

        Raises:
            RuntimeError: 提交失败或工作流验证失败
        """
        payload = {"prompt": workflow}
        session = self.get_http_session()

        async with session.post(
            f"{self.base_url}/prompt",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"[ComfyUI] 提交失败 | status={resp.status} | body={text[:500]}")
                raise RuntimeError(
                    f"ComfyUI 提交失败 ({resp.status}): {text[:300]}"
                )
            data = await resp.json()

            # 检查 node_errors（ComfyUI 验证失败时返回 200 但包含 node_errors）
            node_errors = data.get("node_errors", {})
            if node_errors:
                error_details = []
                for nid, errs in node_errors.items():
                    if isinstance(errs, dict) and "errors" in errs:
                        for e in errs["errors"]:
                            msg = e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            error_details.append(f"node {nid}: {msg}")
                    else:
                        error_details.append(f"node {nid}: {errs}")
                error_summary = "; ".join(error_details[:5])
                logger.error(f"[ComfyUI] 工作流验证失败 | node_errors={node_errors}")
                raise RuntimeError(f"ComfyUI 工作流验证失败: {error_summary}")

            prompt_id = data.get("prompt_id", "")
            if not prompt_id:
                logger.error(f"[ComfyUI] 提交返回空 prompt_id | data={data}")
                raise RuntimeError(f"ComfyUI 提交返回空 prompt_id: {str(data)[:300]}")

            logger.info(f"[ComfyUI] 工作流已提交 | prompt_id={prompt_id}")
            return prompt_id

    # ── 等待完成 ──────────────────────────────────────────────

    async def wait_for_completion(
        self,
        prompt_id: str,
        progress_callback: Optional[Callable] = None,
        task_type: str = "generate",
        on_crash: Optional[Callable] = None,
    ) -> List[str]:
        """等待 ComfyUI 生成完成并获取所有输出文件名

        Args:
            prompt_id: ComfyUI 任务 ID
            progress_callback: 进度回调 (progress_pct, elapsed_secs)
            task_type: 任务类型，用于设置不同的超时时间
            on_crash: ComfyUI 崩溃时的回调（用于触发重启）

        Returns:
            所有输出文件的文件名列表（优先非 temp 文件）

        Raises:
            RuntimeError: ComfyUI 执行错误
            TimeoutError: 生成超时
        """
        max_time = TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)
        elapsed = 0.0
        consecutive_failures = 0
        last_queue_log = -30

        logger.info(
            f"[ComfyUI] 开始等待生成完成 | prompt_id={prompt_id}"
            f" | task_type={task_type} | timeout={max_time}s"
        )

        while elapsed < max_time:
            try:
                # 先查历史（生成完成后）
                session = self.get_http_session()
                url = f"{self.base_url}/history/{prompt_id}"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        consecutive_failures = 0
                        data = await resp.json()
                        history = data.get(prompt_id, {})

                        # 检测执行错误
                        status_info = history.get("status", {})
                        if isinstance(status_info, dict):
                            status_str = status_info.get("status_str", "")
                            status_messages = status_info.get("messages", [])
                            if status_str == "error":
                                error_msgs = []
                                for msg in status_messages[:5]:
                                    if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                                        event_name, msg_data = msg[0], msg[1]
                                        if isinstance(msg_data, dict):
                                            exc_msg = msg_data.get("exception_message", "")
                                            exc_type = msg_data.get("exception_type", "")
                                            node_id = msg_data.get("node_id", "")
                                            node_type = msg_data.get("node_type", "")
                                            error_msgs.append(
                                                f"[{node_type}#{node_id}] {exc_type}: {exc_msg}"[:200]
                                            )
                                        else:
                                            error_msgs.append(str(msg_data)[:200])
                                    else:
                                        error_msgs.append(str(msg)[:200])
                                if not error_msgs:
                                    error_msgs = ["unknown error"]
                                logger.error(
                                    f"[ComfyUI] 执行错误详情 | status={status_str}"
                                    f" | messages={status_messages[:3]}"
                                )
                                raise RuntimeError(
                                    f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                                )

                        # 兼容旧版 errors 字段
                        errors = history.get("errors", [])
                        if errors:
                            error_msgs = [str(e)[:200] for e in errors[:5]]
                            logger.error(f"[ComfyUI] 执行错误详情(errors字段) | errors={errors[:5]}")
                            raise RuntimeError(
                                f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                            )

                        # 收集所有 SaveImage 节点的输出
                        outputs = history.get("outputs", {})
                        all_filenames: List[str] = []
                        temp_filenames: List[str] = []
                        for node_id, node_output in outputs.items():
                            images = node_output.get("images", [])
                            for img in images:
                                fname = img.get("filename", "")
                                if not fname.startswith("ComfyUI_temp"):
                                    all_filenames.append(fname)
                                elif not temp_filenames:
                                    temp_filenames.append(fname)

                        if all_filenames:
                            if progress_callback:
                                try:
                                    progress_callback(f"生成完成 ({elapsed}s)", 100)
                                except Exception:
                                    pass
                            return all_filenames
                        if temp_filenames:
                            if progress_callback:
                                try:
                                    progress_callback(f"生成完成 ({elapsed}s)", 100)
                                except Exception:
                                    pass
                            return temp_filenames

                # 还在生成中，查队列获取进度
                consecutive_failures = 0
                if progress_callback:
                    try:
                        prog = await self.get_queue_progress(prompt_id)
                        if prog.get("in_queue"):
                            progress_callback(f"队列处理中 ({elapsed}s)", prog["progress"])
                        elif elapsed >= 5:
                            estimated_pct = min(int(elapsed / 60 * 100), 99)
                            progress_callback(f"等待中 ({elapsed}s)", estimated_pct)
                    except Exception:
                        pass

                # 每 30s 打印一次队列状态
                if elapsed - last_queue_log >= 30:
                    last_queue_log = elapsed
                    try:
                        q_session = self.get_http_session()
                        async with q_session.get(
                            f"{self.base_url}/queue",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as qresp:
                            if qresp.status == 200:
                                qdata = await qresp.json()
                                logger.debug(
                                    f"[ComfyUI] 队列状态 (t={elapsed}s): "
                                    f"running={len(qdata.get('queue_running', []))}, "
                                    f"pending={len(qdata.get('queue_pending', []))}"
                                )
                    except Exception as qe:
                        logger.warning(f"[ComfyUI] 查询队列失败 (t={elapsed}s): {qe}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                consecutive_failures += 1
                logger.warning(f"[ComfyUI] 轮询失败 ({consecutive_failures}x): {e}")

                if consecutive_failures >= 5:
                    alive = await self.check_alive()
                    if not alive:
                        logger.warning(
                            f"[ComfyUI] ComfyUI 确认为崩溃状态"
                            f"（{consecutive_failures}x 失败, {elapsed:.0f}s）"
                        )
                        if on_crash:
                            await on_crash()
                        raise RuntimeError(
                            f"ComfyUI 在生成过程中崩溃，当前任务"
                            f"（prompt_id={prompt_id[:8]}）已丢失，请重新发起生成"
                        )
                    else:
                        logger.info(
                            f"[ComfyUI] ComfyUI 仍在线"
                            f"（{consecutive_failures}x 暂时波动），继续等待..."
                        )

            # 自适应轮询：
            # - 前 10s：0.5s（快速反馈）
            # - 10-30s：1.0s
            # - 30-60s：2.0s
            # - 60s+：5.0s（长任务减少请求）
            # - 连接失败时：退避到 max 5s
            if consecutive_failures > 0:
                poll_interval = min(POLL_INTERVAL * (1.5 ** consecutive_failures), 5.0)
            elif elapsed < 10:
                poll_interval = POLL_INTERVAL  # 0.5s
            elif elapsed < 30:
                poll_interval = 1.0
            elif elapsed < 60:
                poll_interval = 2.0
            else:
                poll_interval = 5.0

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"ComfyUI 生成超时 ({max_time}s, task={task_type})，"
            f"prompt_id={prompt_id[:8]}"
        )

    # ── 显存管理 ──────────────────────────────────────────────

    async def free_vram(self, unload_models: bool = False):
        """释放 ComfyUI 显存（调用 /free 端点）"""
        try:
            session = self.get_http_session()
            payload = {"unload_models": unload_models}
            async with session.post(
                f"{self.base_url}/free",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[ComfyUI] 显存释放成功 (unload_models={unload_models})")
                else:
                    logger.warning(f"[ComfyUI] 显存释放失败: status={resp.status}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 显存释放异常: {e}")

    async def get_vram_info(self) -> Dict[str, Any]:
        """获取 ComfyUI 显存信息"""
        try:
            session = self.get_http_session()
            async with session.get(
                f"{self.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    devices = data.get("devices", [])
                    if devices:
                        vram_total = devices[0].get("vram_total", 0)
                        vram_free = devices[0].get("vram_free", 0)
                        return {
                            "vram_total": vram_total,
                            "vram_free": vram_free,
                            "vram_used": vram_total - vram_free,
                            "vram_pct": (vram_total - vram_free) / vram_total * 100 if vram_total else 0,
                        }
                return {}
        except Exception:
            return {}
