cd D:\1\2\director\backend
$env:COMFYUI_DIR="D:\1\2\ComfyUI_windows_portable\ComfyUI"
$env:COMFYUI_PYTHON="D:\1\2\ComfyUI_windows_portable\python_embeded\python.exe"
$env:COMFYUI_BASE_URL="http://127.0.0.1:8188"
$env:PYTHONUTF8=1
$env:DIRECTOR_PORT=8000
.\.venv-test\Scripts\python.exe -m uvicorn main:app --app-dir "$PWD" --port 8000 --host 127.0.0.1


cd D:\llama-b9113-bin-win-cuda-13.1-x64

$env:CUDA_PATH="D:\llama-b9113-bin-win-cuda-13.1-x64"
$env:PATH="D:\llama-b9113-bin-win-cuda-13.1-x64;$env:PATH"

D:\llama-b9113-bin-win-cuda-13.1-x64\llama-server.exe ^
  -m D:\models\qwen3-vl-8b\Qwen3VL-8B-Instruct-Q4_K_M.gguf ^
  --mmproj D:\models\qwen3-vl-8b\mmproj-Qwen3VL-8B-Instruct-F16.gguf ^
  --port 8082 -ngl 99 --host 127.0.0.1

  方式一：现成脚本（推荐，一条命令跑完整质检）


PowerShell

# 完整 QC：技术质检 + AI 打分 + 红线拦截，输出 JSON 到 data/generated/qc/qc_run_latest.json
d:\1\2\director\backend\.venv-test\Scripts\python.exe d:\1\2\director\backend\tools\run_qc_report.py
方式二：代码调用（qc_service.py 入口）


Python

from services.qc.qc_service import run_qc_async
result = await run_qc_async(
    video_path=".../export_xxx.mp4",
    caption="视频文案",      # 可选，用于合规关键词兜底
    threshold=60.0,          # 通过阈值
    use_semantic=True,       # 是否启用 AI 语义打分
    manage_server=False,     # 默认 False：调用【已起的常驻】llama-server(8082)，绝不托管/杀掉它
)
# result.to_dict() → 总分/维度/红线/合规/版权/总结
流程：调用常驻 llama-server(8082) → 抽 6 帧转 base64 多图 → 调本地 Qwen3-VL-8B 打分 →
      聚合（cv2 技术分 + AI 语义分 + 红线分级拦截）→ 返回 QcResult。
注意：manage_server=False（默认）不会拉起/关闭 server；若需由函数自托管 server，传 manage_server=True
      （仅当常驻不可用时才会自拉起，避免双开抢端口）。

方式三：一键成片链路：qc_stage 挂在 export 之后，成片自动过质检，产出可复核 JSON 报告。
  - 默认 manage_server=False，依赖你手动起好的常驻 llama-server(8082)。
  - 想让 stage 自己管 server：在 OneClickVideoPage 的 qc 参数里加 manage_server:True（不推荐，易误杀常驻进程）。
  - 报告落盘：data/generated/qc/qc_report_{asset_id}.json（最新）+ qc_report_{asset_id}_{ts}.json（历史快照）
    + qc_history_{asset_id}.json（趋势列表）。
查询 API：
  GET  /api/qc/report/{asset_id}      # 查某视频/报告的质检结果
  POST /api/qc/force-publish          # 未达标强制发布留痕 {asset_id, operator?, reason}
  GET  /api/qc/history/{asset_id}     # 查历次质检趋势 + 首末对比结论
D:\llama-b9113-bin-win-cuda-13.1-x64\llama-server.exe ^
  -m D:\models\qwen3-vl-8b\Qwen3VL-8B-Instruct-Q4_K_M.gguf ^
  --mmproj D:\models\qwen3-vl-8b\mmproj-Qwen3VL-8B-Instruct-F16.gguf ^
  --port 8082 -ngl 99 --host 127.0.0.1
