"""
ComfyUI 服务 — 图像生成 Mixin 主类

文生图/图生图核心与队列管理。P2 治理：
精修/标准化方法拆至 comfyui_generation_vision.py。
"""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui_helpers import (
    ComfyUIGenResult,
    MAX_POLL_TIME,
    POLL_INTERVAL,
    TASK_TIMEOUTS,
    _extract_clip_text,
    _mem_log,
)
from services.workflow_builder import (
    build_comfyui_workflow,
    build_refinement_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)
from services.qwen_workflow import YAOGUANG_DEFAULT_NEGATIVE

from services.comfyui_generation_vision import ComfyUIGenerationVisionMixin, _project_prefix

logger = logging.getLogger(__name__)


class ComfyUIGenerationMixin(ComfyUIGenerationVisionMixin):
    async def _cache_image(self, filename: str):
        """确保图片存在于磁盘（委托到 file_handler 子模块）"""
        await self._file_handler.cache_image(filename)

    async def _ensure_image_in_input_dir(
        self, image_url: str, project_id: Optional[str] = None
    ) -> str:
        """确保参考图像存在于 ComfyUI input 目录中（委托到 file_handler 子模块）"""
        return await self._file_handler.ensure_image_in_input_dir(image_url, project_id)

    def get_cached_image(self, filename: str) -> Optional[bytes]:
        """获取缓存的图片数据（委托到 file_handler 子模块）"""
        return self._file_handler.get_cached_image(filename)

    def clear_image_cache(self):
        """清理内存中的图片缓存（委托到 file_handler 子模块）"""
        self._file_handler.clear_image_cache()

    async def _normalize_reference_images(
        self, ref_items: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """检测并统一参考图的尺寸比例（委托到 file_handler 子模块）"""
        return await self._file_handler.normalize_reference_images(ref_items)

    async def _generate_scene_prompts(
        self,
        concept_prompt_json: Optional[dict] = None,
        refined_prompt: str = "",
        user_scene_desc: str = "",
    ) -> Dict[str, Any]:
        """调用 DeepSeek 从文生图+精修优化提示词生成场景多角度提示词"""
        from services.prompt_service import get_prompt_service

        psvc = get_prompt_service()
        return await psvc.generate_scene_prompts(
            concept_prompt_json=concept_prompt_json,
            refined_prompt=refined_prompt,
            user_scene_desc=user_scene_desc,
        )

    async def check_health(self) -> Dict[str, Any]:
        """检查 ComfyUI 是否在线（委托到 client 子模块）"""
        return await self._client.check_health()

    async def get_queue_progress(self, prompt_id: str) -> Dict[str, Any]:
        """查询 ComfyUI 队列中指定 prompt 的生成进度（委托到 client 子模块）"""
        return await self._client.get_queue_progress(prompt_id)

    async def generate(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",  # yaoguang | qwen_refinement | qwen_standardization
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """
        通过 ComfyUI 生成图像（自动等待服务就绪）

        Args:
            prompt_json: 结构化提示词
            custom_text: 自定义文本
            negative_prompt: 负向提示词
            width: 图像宽度
            height: 图像高度
            seed: 随机种子
            steps: 采样步数
            cfg: CFG 强度（Control Free Guidance）
            progress_callback: 进度回调函数
            reference_image: 参考图路径（图生图模式）
            workflow_type: 工作流类型（yaoguang/qwen_refinement/qwen_standardization）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        # 并发控制：通过 _queue_prompt_with_retry 的 _semaphore 统一限制
        # （移除 _generate_lock，避免与 _semaphore 双重串行化）
        return await self._generate_impl(
            prompt_json=prompt_json,
            custom_text=custom_text,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            progress_callback=progress_callback,
            reference_image=reference_image,
            project_id=project_id,
            asset_tag=asset_tag,
            workflow_type=workflow_type,
            content_type=content_type,
        )

    async def _generate_impl(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """generate() 的实际实现（已获取互斥锁）"""
        self._mark_generation_active()
        # ⭐ Fix 3: 概念探索阶段入口，重置 sd 计数
        self.reset_generation_count("sd")
        # 0. 释放显存 + 检查内存/显存使用情况
        logger.info(
            f"[ComfyUI] generate() 入口 | workflow={workflow_type} | ref={reference_image[:30] if reference_image else 'none'}"  # noqa: E501
        )
        await self._release_vram_for_comfyui()
        logger.info("[ComfyUI] generate() VRAM释放完成")
        await self.check_and_release_memory()
        logger.info("[ComfyUI] generate() 内存检查完成")

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        logger.info(f"[ComfyUI] generate() ensure_running={ready}")
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（按模型类型分别计数）
        model_family = "sd" if workflow_type in (None, "", "yaoguang") else "qwen"
        await self._ensure_clean_state(model_family)

        # 预解析参考图（qwen 工作流需要 input 目录下的文件）
        resolved_image = reference_image
        if workflow_type in ("qwen_refinement", "qwen_standardization") and reference_image:
            resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 根据工作流类型构建工作流
        if workflow_type == "qwen_refinement":
            # Qwen精修模式（单图编辑）
            prefix = f"{_project_prefix(project_id)}_{asset_tag or 'refine'}"
            workflow, opt_prompt, prompt_sections = build_refinement_workflow(
                reference_image=resolved_image,
                role_desc=custom_text,
                seed=actual_seed,
                filename_prefix=prefix,
            )
            logger.info("[ComfyUI] 使用 Qwen 精修工作流")

        elif workflow_type == "qwen_standardization":
            # Qwen标准化模式（多视图生成）
            workflow, _, _ = build_standardization_workflow(
                reference_image=resolved_image,
                views=3,
                character_name=custom_text or "角色",
                seed=actual_seed,
                filename_prefix=f"{_project_prefix(project_id)}_{asset_tag or 'std'}",
            )
            logger.info("[ComfyUI] 使用 Qwen 标准化工作流（3视图）")

        else:
            # 默认使用 Z-Image 瑶光版（文生图）
            positive_text = structured_prompt_to_comfyui_prompt(prompt_json, custom_text)
            neg_text = negative_prompt or YAOGUANG_DEFAULT_NEGATIVE

            # 打印最终发给 ComfyUI 的 prompt 文本，方便排查模型不按类型生成的问题
            logger.info(f"[ComfyUI] 正向提示词文本: {positive_text[:200]}")
            # 选择工作流模板：character 用影视级（25步/AuraFlow），scene 用标准版
            # prop 用道具专用版（+SeedVR2超分管线）
            # steps/cfg 使用 workflow_builder 函数的默认值（影视级=25/2，标准=8/1）
            if content_type in ("prop", "scene"):
                _workflow_type = "prop"
                # 道具/场景工作流：不传默认尺寸，让模板自带的尺寸生效
                _wf_width = width
                _wf_height = height
            else:
                _workflow_type = "cinematic" if content_type in ("character", "") else "standard"
                _wf_width = width or self.config.default_width
                _wf_height = height or self.config.default_height
            workflow = build_comfyui_workflow(
                positive_prompt=positive_text,
                negative_prompt=neg_text,
                width=_wf_width,
                height=_wf_height,
                seed=actual_seed,
                steps=steps,  # ⭐ 修复 A1：传递 steps 到工作流
                cfg=cfg,  # ⭐ 修复 A1：传递 cfg 到工作流
                reference_image=reference_image,
                content_type=content_type,
                workflow=_workflow_type,
            )
            # 打印工作流中最终的 CLIP 提示词，方便排查生成与预期不符的问题
            clip_text = _extract_clip_text(workflow)
            if clip_text:
                logger.info(f"[ComfyUI] 最终 CLIP 正向提示词: {clip_text[:300]}")
            logger.info("[ComfyUI] 使用 Z-Image 瑶光工作流")

        # ⭐ 断裂点3修复：ParamInjector 补漏注入
        # build_comfyui_workflow 已手写注入核心参数，此处用 ParamInjector 做二次校验+补漏
        # 确保 schema 中定义的所有参数都被注入，不依赖手写逻辑的完整性
        try:
            from services.workflow_params import inject_workflow_params

            schema_name = "文生图影视级" if _workflow_type == "cinematic" else None
            if schema_name:
                user_params = {
                    "prompt": positive_text,
                    "negative": neg_text,
                    "width": _wf_width,
                    "height": _wf_height,
                    "steps": steps,
                    "cfg": cfg,
                    "seed": actual_seed,
                }
                # 过滤 None 值（避免覆盖工作流模板默认值）
                user_params = {k: v for k, v in user_params.items() if v is not None}
                _, injected = inject_workflow_params(schema_name, workflow, user_params)
                if injected:
                    logger.info(f"[ComfyUI] ParamInjector 补漏注入: {list(injected.keys())}")
        except Exception as pie:
            logger.warning(f"[ComfyUI] ParamInjector 补漏失败（不影响主流程）: {pie}")

        # 3. 提交到 ComfyUI（含重试）
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        logger.info(
            f"[ComfyUI] 已提交: prompt_id={prompt_id[:8]}..., "
            f"seed={actual_seed}, workflow={workflow_type}"
        )

        # 4. 等待生成完成（带进度回调）
        filenames = await self._wait_for_completion(
            prompt_id, progress_callback, task_type="generate"
        )
        if not filenames:
            raise RuntimeError("ComfyUI 生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 5. 缓存所有图片到内存（ComfyUI 停止后仍可访问）
        for fn in filenames:
            await self._cache_image(fn)

        # 6. 构建图像 URL（通过后端代理，避免 CSP 阻止）
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 完成: prompt_id={prompt_id[:8]}..., "
            f"elapsed={total_elapsed}ms, filenames={filenames}"
        )

        # 7. 记录使用时间并调度空闲自停
        self._mark_generation_complete()

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
        )

    async def _queue_prompt_with_retry(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（带并发控制信号量，最多 2 个并发生成）"""
        async with self._semaphore:
            logger.debug("[ComfyUI] 获取并发生成许可")
            return await self._queue_prompt_with_retry_impl(workflow)

    async def _queue_prompt_with_retry_impl(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（先快速释放显存，再检查队列，3 次重试）"""
        _mem_log("提交工作流前", f"nodes={len(workflow)}")
        # 每次提交前快速释放显存
        try:
            await self._quick_release_vram()
        except Exception:
            pass

        # 提交前检查队列是否有堆积
        try:
            q_session = self._get_http_session()
            async with q_session.get(
                f"{self.config.base_url}/queue",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    running = len(data.get("queue_running", []))
                    pending = len(data.get("queue_pending", []))
                    if pending > 2:
                        logger.warning(
                            f"[ComfyUI] 队列堆积 ({pending} pending, {running} running)，"
                            "清空中..."
                        )
                        async with q_session.post(
                            f"{self.config.base_url}/queue",
                            json={"clear": True},
                        ):
                            pass
        except Exception:
            pass

        last_error = None
        for attempt in range(3):
            try:
                return await self._queue_prompt(workflow)
            except (aiohttp.ClientConnectorError, ConnectionRefusedError) as e:
                last_error = e
                wait = 2**attempt
                logger.warning(
                    f"[ComfyUI] 连接失败 (attempt {attempt + 1}), " f"{wait}s 后重试: {e}"
                )
                await asyncio.sleep(wait)
                # 重试前再检查一下 ComfyUI
                if not await self._check_alive():
                    await self.ensure_running()
            except RuntimeError as e:
                last_error = e
                if attempt < 2:
                    wait = 2**attempt
                    logger.warning(
                        f"[ComfyUI] 提交失败 (attempt {attempt + 1}), {wait}s 后重试: {e}"
                    )
                    await asyncio.sleep(wait)
                    # 检测 HTTP 500 错误，强制重启恢复内部状态
                    if "500" in str(e) or "Server got itself" in str(e):
                        logger.error("[ComfyUI] 检测到 HTTP 500 错误，强制重启恢复...")
                        await self._close_http_session()
                        self.stop()
                        await self.ensure_running()
                    elif not await self._check_alive():
                        await self.ensure_running()
                else:
                    raise
        last_msg = str(last_error) if last_error else "未知错误"
        raise RuntimeError(f"ComfyUI 提交失败（已重试3次）: {last_msg}")

    @staticmethod
    def _strip_workflow_meta(workflow: dict) -> dict:
        """深拷贝工作流并剥离可能引起自定义节点崩溃的 _meta / _comment 字段

        同时剥离工作流顶层的非节点键（如 _meta、_comment），
        避免被 ComfyUI 当作节点 ID 解析导致 missing_node_type 错误。
        """
        cleaned = {}
        for nid, ndata in workflow.items():
            # 跳过顶层非节点键（以下划线开头且值不是标准节点 dict）
            if nid.startswith("_") and not (isinstance(ndata, dict) and "class_type" in ndata):
                continue
            if not isinstance(ndata, dict):
                cleaned[nid] = ndata
                continue
            cleaned[nid] = {k: v for k, v in ndata.items() if k not in ("_meta", "_comment")}
        return cleaned

    async def _queue_prompt(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI"""
        # 剥离 _meta / _comment（某些自定义节点遇到这些字段会崩溃）
        workflow = self._strip_workflow_meta(workflow)

        # 提交前检查关键节点的图片值（Fish 融合模板节点11 = 场景槽位）
        if "11" in workflow:
            _img = workflow["11"].get("inputs", {}).get("image", "")
            logger.info(f"[ComfyUI] 提交前节点11(场景)图片: {_img}")
        payload = {"prompt": workflow}

        session = self._get_http_session()
        async with session.post(
            f"{self.config.base_url}/prompt",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"[ComfyUI] 提交失败 | status={resp.status} | body={text[:500]}")
                raise RuntimeError(f"ComfyUI 提交失败 ({resp.status}): {text[:300]}")
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

    async def _wait_for_completion(
        self,
        prompt_id: str,
        progress_callback: Optional[callable] = None,
        task_type: str = "generate",
        output_fields: tuple = ("images", "audio"),
    ) -> List[str]:
        """
        等待 ComfyUI 生成完成并获取所有输出文件名（含多图片场景）。
        带进度回调，自动恢复 ComfyUI 崩溃。
        增加错误检测：如果 ComfyUI 返回执行错误，立即抛出。

        Args:
            task_type: 任务类型，用于设置不同的超时时间
                       'generate'=300s / 'refine'=600s / 'standardize_3'=600s
                       'standardize_6'=1200s / 'storyboard'=900s

        Returns:
            所有输出文件的文件名列表（优先非 temp 文件）
        """
        _mem_log(
            "等待生成开始",
            f"prompt={prompt_id[:8]} task={task_type} timeout={TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)}s",  # noqa: E501
        )
        max_time = TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)
        elapsed = 0
        consecutive_failures = 0
        last_queue_log = -30  # 每 30s 打印一次队列状态
        logger.info(
            f"[ComfyUI] 开始等待生成完成 | prompt_id={prompt_id}"
            f" | task_type={task_type} | timeout={max_time}s"
        )
        while elapsed < max_time:
            try:
                # 先查历史（生成完成后）
                session = self._get_http_session()
                url = f"{self.config.base_url}/history/{prompt_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        consecutive_failures = 0  # 连接成功则重置计数
                        try:
                            data = await resp.json()
                        except (json.JSONDecodeError, ValueError) as json_err:
                            logger.warning(
                                f"[ComfyUI] history JSON解析失败 (t={elapsed}s): {json_err}"
                            )
                            data = None
                        if data:
                            history = data.get(prompt_id, {})
                            # 检测执行错误 — ComfyUI history 格式：
                            #   {"status": {"status_str": "error", "completed": bool, "messages": [[event, data], ...]}}  # noqa: E501
                            #   messages 中每个元素是 [event_name, {exception_message, exception_type, ...}]  # noqa: E501
                            status_info = history.get("status", {})
                            if isinstance(status_info, dict):
                                status_str = status_info.get("status_str", "")
                                status_messages = status_info.get("messages", [])
                                if status_str == "error":
                                    error_msgs = []
                                    for msg in status_messages[:5]:
                                        if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                                            _, msg_data = msg[0], msg[1]
                                            if isinstance(msg_data, dict):
                                                exc_msg = msg_data.get("exception_message", "")
                                                exc_type = msg_data.get("exception_type", "")
                                                node_id = msg_data.get("node_id", "")
                                                node_type = msg_data.get("node_type", "")
                                                error_msgs.append(
                                                    f"[{node_type}#{node_id}] {exc_type}: {exc_msg}"[  # noqa: E501
                                                        :200
                                                    ]
                                                )
                                            else:
                                                error_msgs.append(str(msg_data)[:200])
                                        else:
                                            error_msgs.append(str(msg)[:200])
                                    if not error_msgs:
                                        error_msgs = ["unknown error"]
                                    logger.error(
                                        f"[ComfyUI] 执行错误详情 | status={status_str} | messages={status_messages[:3]}"  # noqa: E501
                                    )
                                    raise RuntimeError(f"ComfyUI 执行错误: {'; '.join(error_msgs)}")
                            # 兼容旧版 errors 字段
                            errors = history.get("errors", [])
                            if errors:
                                error_msgs = [str(e)[:200] for e in errors[:5]]
                                logger.error(
                                    f"[ComfyUI] 执行错误详情(errors字段) | errors={errors[:5]}"
                                )
                                raise RuntimeError(f"ComfyUI 执行错误: {'; '.join(error_msgs)}")
                            # 检测节点错误状态
                            outputs = history.get("outputs", {})
                            # 收集所有 SaveImage / SaveAudio 节点的输出（跳过 PreviewImage 的 temp 文件）
                            all_filenames: List[str] = []
                            temp_filenames: List[str] = []
                            for node_id, node_output in outputs.items():
                                media_items = []
                                for _f in output_fields:
                                    media_items.extend(node_output.get(_f, []) or [])
                                for item in media_items:
                                    fname = item.get("filename", "")
                                    subfolder = item.get("subfolder", "")
                                    if subfolder:
                                        self._output_subfolders[fname] = subfolder
                                    if not fname.startswith("ComfyUI_temp"):
                                        all_filenames.append(fname)
                                    elif not temp_filenames:
                                        # 兜底：只保留第一个 temp 文件名
                                        temp_filenames.append(fname)
                            if all_filenames:
                                _mem_log(
                                    "等待生成完成",
                                    f"prompt={prompt_id[:8]} files={all_filenames} elapsed={elapsed}s",  # noqa: E501
                                )
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(all_filenames)
                                return all_filenames
                            if temp_filenames:
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(temp_filenames)
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

                # 每 30s 打印一次队列状态（方便排障）
                if elapsed - last_queue_log >= 30:
                    last_queue_log = elapsed
                    try:
                        q_session = self._get_http_session()
                        async with q_session.get(
                            f"{self.config.base_url}/queue",
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
                # 连续失败 5 次（~2.5s）后检查 ComfyUI 是否还活着
                # 用 _check_alive() 区分"模型加载中暂时断连"和"真正崩溃"
                # - /history 超时但 _check_alive 成功 = 临时波动，继续等
                # - _check_alive 也失败 = ComfyUI 崩溃，立即重启
                if consecutive_failures >= 5:
                    alive = await self._check_alive()
                    if not alive:
                        logger.warning(
                            f"[ComfyUI] ComfyUI 确认为崩溃状态（{consecutive_failures}x 失败, "
                            f"{elapsed:.0f}s），尝试重启..."
                        )
                        await self._close_http_session()  # ⭐ 关闭旧 session，重启后自动创建新的
                        self._kill_process_on_port(8188)
                        await self.ensure_running()
                        # ⭐ 重启后 prompt_id 已丢失，新实例不认旧 ID，必须立即失败
                        raise RuntimeError(
                            f"ComfyUI 在生成过程中崩溃并已自动重启，当前任务（prompt_id={prompt_id[:8]}）"
                            f"已丢失，请重新发起生成"
                        )
                    else:
                        logger.info(
                            f"[ComfyUI] ComfyUI 仍在线（{consecutive_failures}x 暂时波动），继续等待..."
                        )

            # 自适应轮询：
            # - 前 10s：0.5s（快速反馈，适合文生图等短任务）
            # - 10-30s：1.0s
            # - 30-60s：2.0s
            # - 60s+：5.0s（长任务如视频，减少无效请求）
            # - 连接失败时：退避到 max 5s
            if consecutive_failures > 0:
                poll_interval = min(POLL_INTERVAL * (1.5**consecutive_failures), 5.0)
            elif elapsed < 10:
                poll_interval = POLL_INTERVAL  # 0.5s
            elif elapsed < 30:
                poll_interval = 1.0
            elif elapsed < 60:
                poll_interval = 2.0
            else:
                poll_interval = 5.0

            # ⭐ 每 30 秒打印一次内存状态，便于追踪内存泄漏
            _elapsed_int = int(elapsed)
            if (
                _elapsed_int > 0
                and _elapsed_int % 30 == 0
                and (_elapsed_int - 30) < poll_interval + 1
            ):
                _mem_log("轮询中", f"prompt={prompt_id[:8]} elapsed={_elapsed_int}s")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"ComfyUI 生成超时 ({max_time}s, task={task_type})，prompt_id={prompt_id[:8]}"
        )

    def get_available_models(self) -> List[Dict[str, Any]]:
        """获取可用模型信息"""
        return [
            {
                "id": "comfyui_yaoguang",
                "name": "Z-Image 瑶光版",
                "provider": "comfyui",
                "description": "本地ComfyUI + Z-Image瑶光LoRA，超真实细节增强，8步出图",
                "width": 1080,
                "height": 1920,
                "steps": 8,
            },
            {
                "id": "qwen_refinement",
                "name": "Qwen 精修",
                "provider": "comfyui",
                "description": "Qwen Image Edit单图编辑模式，用于角色/场景/道具精修定妆",
                "width": 1536,
                "height": 1024,
                "steps": 20,
            },
            {
                "id": "qwen_standardization",
                "name": "Qwen 标准化",
                "provider": "comfyui",
                "description": "Qwen Image Edit多图融合模式，用于3视图/6视图标准化生成",
                "width": 1536,
                "height": 512,
                "steps": 20,
            },
        ]
