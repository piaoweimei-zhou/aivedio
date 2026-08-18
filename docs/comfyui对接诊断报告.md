ComfyUI 工作流深度对接诊断报告
一、全景链路视图
code
应用
┌──────────────────────────────────────────────────────────────────┐
│                     问题根本原因                                    │
│                                                                   │
│  workflows/*.json（40个模板）                                       │
│    ↓ 硬编码参数在 JSON 内部                                         │
│  workflow_builder.py / qwen_workflow.py（参数提取层）               │
│    ↓ 选择性提取部分参数，遗漏部分                                    │
│  ProviderPlugin.generate_image/generate_video（供应商层）            │
│    ↓ 每个 provider 有自己的参数映射逻辑                               │
│  StagePlugin.execute（阶段层）                                       │
│    ↓ 每个 stage 自己编组 params                                      │
│  director_stage_api.py（API层）                                     │
│    ↓ 前端 params 透传到 stage                                        │
│  AssetsPage / VideoPage（前端）                                      │
│                                                                   │
│  ⚠ 每一层都在"理解"参数含义，且理解可能不一致！                     │
└──────────────────────────────────────────────────────────────────┘
二、A 级问题：参数提取不完整 / 遗漏
A1. 工作流模板参数硬编码，外部无法覆写
我对 40 个工作流 JSON 中的 8 个核心模板进行了参数扫描：

工作流	width	height	steps	cfg	denoise	sampler	外部可覆写能力
1人分镜.json	1920 硬编码	1080 硬编码	4 硬编码	1 硬编码	1.0	euler	仅 prompt+参考图
LTX-MSRS_V2.json	1280 硬编码	720 硬编码	97帧 硬编码	3	-	euler	❌ 无 config 暴露
最终文生图.json	1080	1920	25 硬编码	2 硬编码	1	res_multistep	❌ 完全硬编码
Z-Image.json	512	512	10	1	1	euler	❌ 完全硬编码
Z-Image-Enhance.json	-	-	10	0.7~1	外部引用	euler_cfg_pp	❌ 完全硬编码
upScalse.json	2048 res	-	-	-	-	-	❌ 完全硬编码
问题：只有 精修优化.json（通过 build_qwen_workflow）的参数被代码层提取并支持动态覆写。其他工作流的参数全部是 JSON 硬编码值，代码层面根本不提取它们。

具体影响：

用户在视频页选择 1280x720 分辨率，但如果 MSR 工作流模板里写的是 1920x1080，实际生成的是模板尺寸，前端传的分辨率无效
用户在 SettingsPage 设置的 steps/cfg 值不会传递到工作流
config.json 机制（如 1人分镜.config.json）只暴露了 prompt 和参考图字段，steps、cfg、width、height 都不暴露
A2. 视频参数完全未提取
视频生成的核心参数在链路中传递路径稀疏：

参数	前端输入	API 接收	Provider 接收	最终注入工作流
fps	无输入	❌ 无此字段	❌	硬编码 24
duration(秒)	有输入 ✓	duration/segment_seconds ✓	duration=5.0 ✓	❌ 不转换
frame_count	有输入 41 ✓	frame_count ✓	❌ 无此字段	手动注入节点 50/22
width	有输入 1280 ✓	width ✓	❌ 无此字段	手动注入节点 43/44
height	有输入 720 ✓	height ✓	❌ 无此字段	手动注入节点 43/44
seed	有输入 ✓	seed ✓	❌	手动注入节点 15
resolution	有输入 480p	resolution ✓	resolution ✓	❌ 不处理
核心断裂：ProviderService.generate_video() 的参数列表只定义了 prompt, images, model, duration, aspect_ratio, resolution，根本没有 width/height/frame_count/seed 参数！

python
应用
# provider_service.py:209-237
async def generate_video(self, provider_id, prompt, images=None, videos=None,
                         model="", duration=5.0, aspect_ratio="16:9",
                         resolution="480p", **kwargs) -> ProviderResult:
    # ↑↑↑ 没有 width/height/frame_count/seed 参数 ↑↑↑
而 MSR 视频中这些参数是通过 API 层直接注入 ComfyUI 工作流（跳过 ProviderService），走的是另一条路径：

python
应用
# canvas_api.py — MSR 路径（跳过 ProviderService）
wf["节点ID"]["inputs"]["width"] = request.width     # 直接改 JSON
wf["节点ID"]["inputs"]["height"] = request.height
wf["节点ID"]["inputs"]["length"] = request.frame_count
# → 直接调 comfyui_service._queue_prompt_with_retry(wf)
结论：系统中有两条并行的视频参数注入路径，参数提取不统一：

路径	调用的参数	目的
canvas_api.py → ComfyUI 直接提交	width/height/frame_count/seed	MSR 多角色视频
stage → provider_svc.generate_video() → Provider.generate_video()	prompt/duration/aspect_ratio/resolution	通用视频生成
两路径的参数集完全不同，且都无法完全覆盖所有必要参数。

三、B 级问题：参数语义混乱 / 不统一
B1. 时间控制字段：duration / segment_seconds / frame_count 三重含义
前端传来多个"时间"相关字段，各层理解不同：

字段	出现在哪	含义	单位
duration	MsrVideoRequest	❌ 不存在（MSR 用 frame_count）	-
duration	video_stage.execute() params	每段视频时长	秒
duration	provider_service.generate_video()	视频时长	秒
frame_count	MsrVideoRequest	视频帧数	帧
segment_seconds	video_stage params	每段秒数（优先于 duration）?	秒
total_duration	视频生成任务	(不存在)	-
fps	JSON 硬编码 24	帧率	帧/秒
问题：frame_count=41 + fps=24 实际对应 41/24≈1.7秒，但前端看到的 "duration" 概念跟实际不符。系统没有任何地方实现 duration → frame_count 的转换逻辑。

B2. resolution / width / height 三重表述
字段	示例值	处理方式
resolution	"480p"	字符串，靠每个 Provider 自行解析
aspect_ratio	"16:9"	字符串，靠每个 Provider 自行解析
width/height	1280/720	数字，部分路径传递部分不传递
python
应用
# volcengine_provider.py 收到 resolution="480p"，但同时需要用 width/height
# 但 generate_video 没有 width/height 参数，只能从 **kwargs 捞
B3. prompt 字段在多层被重命名
层	字段名
前端提交	prompt
stage_api.py 接收	params.prompt
stage.execute()	params.get("prompt")
provider_svc.generate_image()	prompt= 参数
comfyui_provider.generate_image()	prompt_text= 再转为 prompt_json
workflow_builder.build_comfyui_workflow()	positive_prompt 参数
qwen_workflow.py	prompt_text 参数
每条链路 prompt 都叫不同的名字，不存在统一的 Prompt 数据传输对象（DTO）。

四、C 级问题：图片尺寸传递断裂
C1. 概念图 → 精修 → 分镜 链路的尺寸不一致
code
应用
概念图生成（workflow_builder.build_comfyui_workflow）
  content_type=character → 竖图 1080×1920
  content_type=scene → 横图 1920×1080
  ↓ 输出尺寸由工作流模板决定，代码层不再调整
精修/超分（refine_stage）
  ↓ LoadImage 输入图片 → 输出尺寸由工作流模板的 EmptyLatentImage 决定
  ↓ 模板 512×512 → 输出 512×512（与输入完全不同！）
分镜生成（storyboard_stage）
  ↓ 需要 1920×1080 的参考图，但输入可能是 512×512
具体问题：workflow_builder.py 中 build_comfyui_workflow 函数会根据 content_type 设置工作流模板（cinematic vs prop），但在模板加载之后，尺寸覆写逻辑只对"道具工作流"路径执行，对 cinematic 路径不覆写 width/height。

python
应用
# workflow_builder.py:922-960
if is_prop_workflow:
    # ... 覆写 EmptyLatentImage.width/height ...
else:
    # cinematic 路径：不覆写尺寸，完全使用工作流模板自带的 width/height
    # 但模板默认值可能是 512×512（Z-Image.json）或 1080×1920（最终文生图.json）
C2. 精修工作流：输入图片尺寸 → 输出失配
build_qwen_workflow（加载 精修优化.json）对尺寸的处理：

python
应用
# workflow_builder.py:326-532
if content_type == "prop":
    scale_length = 1024     # 道具用1024
else:
    scale_length = 1344     # 角色/场景用1344

# 通过 ImageScale 节点缩放，但只控制最短边为 1344/1024
# 另一边保持原比例 → 输出尺寸不可预测
问题：输出图片的宽高比取决于输入，输出尺寸不可预测。前端展示时可能出现拉伸。

五、D 级问题：提示词系统未与工作流参数系统打通
D1. 提示词中心（PromptService）与工作流执行相互独立
python
应用
# prompt_service.py 管理的是：
#   - 提示词 CRUD
#   - 变量替换（{{character_name}} → "张三"）
#   - 版本管理

# stage_service.py 中提示词的集成过于简单：
def _resolve_prompt_params(self, params):
    """简单的 prompt_id 解析"""
    prompt_id = params.get("prompt_id")
    if prompt_id:
        prompt = prompt_svc.resolve(prompt_id, variables)
        params["prompt"] = prompt.content
问题：

提示词中心只解析"提示词文本本身"，不涉及工作流参数（steps/cfg/seed/size）
没有"一个 prompt 对应一组完整工作流参数"的概念
用户不能"选择一个预设风格 = 同时设定 prompt + steps + cfg + size"
D2. 预设系统（PresetService）的边界
preset_service.py 管理的是阶段执行参数预置：

python
应用
# preset 的内容本质上是 params 字典
preset.params = {"prompt": "xxx", "model": "aaa", "duration": 5}
但预设也不提取工作流参数，只传递 Stage.params。而 Stage.params 中缺少 steps/cfg/seed 等参数，所以预设系统也无法设定生成质量参数。

六、E 级问题：缺少统一参数契约
E1. 没有统一的数据传输对象（DTO）
系统中至少存在 5 种"参数集"表述：

位置	参数集	示例
工作流 JSON 节点	节点 inputs dict	{"width":1920, "height":1080}
config.json	fields 数组	[{"id":"prompt_1199", "type":"textarea"}]
workflow_builder 函数参数	Python kwargs	positive_prompt, width, height, steps, seed, cfg
StagePlugin.execute params	Python dict	{"prompt":"...","model":"..."}
前端表单	React state	{prompt, width, height, steps, seed}
每个表述互不兼容，转换靠手动编写映射代码。

E2. 没有参数校验合约
工作流模板的 config.json 只声明了 "有哪些字段"，没有声明：

字段类型（Int/Float/Str/Image？）
字段约束（min/max/default？）
字段与工作流节点的绑定关系（哪个字段对应哪个节点的哪个 input？）
json
应用
// 当前 config.json 的缺陷版本
{
  "fields": [
    { "id": "prompt_1199", "type": "textarea", "name": "提示词" }
    // ❌ 没有 node_id 绑定（靠字段名中的数字推断）
    // ❌ 没有数据类型约束
    // ❌ 没有默认值
    // ❌ 没有校验规则
  ]
}
E3. 前端提交的 params 无类型安全
typescript
应用
// directorApi.ts 中 stageApi.execute(data)
// data.params 的类型是 any
async execute(data: { stage_id: string; input_asset_ids: string[]; params: Record<string, any> }) {
  // params 可以是任何东西，没有类型约束
}
前端可以提交任意字段名到后端，后端无法验证参数有效性。

七、根因总结
code
应用
┌───────────────────────────────────────────────────────────────┐
│                   根本原因                                        │
│                                                               │
│  1. 无统一参数定义层                                              │
│     └── 工作流 JSON / config.json / Python 代码 三分离         │
│  2. 参数提取是"碰运气式"的                                          │
│     └── 每个工作流各自写一套硬编码注入代码                          │
│  3. 视频参数两套路径互不通信                                        │
│  4. 宽度/高度/分辨率/宽高比 四种表述无转换层                         │
│  5. 提示词系统只管理文本，不管理生成参数                              │
│  6. 缺少参数校验契约（类型/范围/必填）                               │
└───────────────────────────────────────────────────────────────┘
八、系统化解决方案
方案一：引入统一的工作流参数描述格式
在 workflows/ 下引入 workflow_params.json 集中管控，取代散落在各文件中的硬编码：

json
应用
{
  "workflow_name": "1人分镜",
  "template": "1人分镜.json",
  "params": {
    "width":    { "node_id": "1298", "field": "value", "type": "int",    "default": 1920, "min": 512,  "max": 4096 },
    "height":   { "node_id": "1299", "field": "value", "type": "int",    "default": 1080, "min": 512,  "max": 4096 },
    "steps":    { "node_id": "844",  "field": "value", "type": "int",    "default": 4,    "min": 1,    "max": 100 },
    "cfg":      { "node_id": "850",  "field": "value", "type": "float",  "default": 1.0,  "min": 0.5,  "max": 10 },
    "seed":     { "node_id": "1525", "field": "seed",  "type": "int",    "default": -1 },
    "prompt":   { "node_id": "1199", "field": "text",  "type": "string", "required": true },
    "ref_image":{"node_id": "213",  "field": "image", "type": "image" }
  },
  "ui_groups": [
    { "id": "basic", "label": "基本参数", "fields": ["prompt", "ref_image", "seed"] },
    { "id": "advanced", "label": "高级参数", "fields": ["steps", "cfg", "width", "height"] }
  ],
  "video_params": {
    "fps":        { "node_id": "7",  "field": "frame_rate", "type": "int",   "default": 24 },
    "frame_count":{"node_id": "50", "field": "value",      "type": "int",   "default": 97 },
    "duration_s": { "derived": true, "formula": "frame_count / fps" }
  }
}
收益：一个声明文件覆盖所有工作流参数，代码自动生成注入逻辑，不再手写。

方案二：统一参数注入引擎
在 workflow_builder.py 之上新增 ParamInjector：

python
应用
class ParamInjector:
    def __init__(self, workflow_name: str):
        self.schema = load_param_schema(workflow_name)
        self.workflow = load_workflow_template(workflow_name)

    def inject(self, user_params: Dict[str, Any]) -> Dict:
        """根据 schema 定义，将用户参数注入到工作流 JSON"""
        for param_name, param_def in self.schema["params"].items():
            value = user_params.get(param_name, param_def.get("default"))
            if value is None and param_def.get("required"):
                raise ValidationError(f"缺少必填参数: {param_name}")
            self._set_field(param_def["node_id"], param_def["field"], value)
        return self.workflow
方案三：统一视频参数 DTO + 时间转换
python
应用
@dataclass
class VideoGenerationParams:
    """统一视频生成参数——所有路径都用这个"""
    prompt: str
    # 尺寸
    width: int = 1280
    height: int = 720
    # 时间控制（三选一，自动转换）
    frame_count: Optional[int] = None      # 直接指定帧数
    duration_seconds: Optional[float] = None  # 自动 = frame_count / fps
    total_duration: Optional[float] = None    # 总时长（多段时）
    fps: int = 24
    # 质量
    seed: int = -1
    cfg: float = 3.0
    steps: int = 20
    # 参考
    reference_images: List[str] = field(default_factory=list)
    background_image: Optional[str] = None

    def __post_init__(self):
        if self.duration_seconds and not self.frame_count:
            self.frame_count = int(self.duration_seconds * self.fps)
        if self.frame_count and not self.duration_seconds:
            self.duration_seconds = self.frame_count / self.fps
确保所有视频生成路径（MSR、通用、批量）都使用同一个 DTO。

方案四：参数校验中间件
python
应用
# 在 director_stage_api.py 中添加
STAGE_PARAM_SCHEMAS = {
    "video": {
        "prompt": {"type": str, "required": True},
        "width":  {"type": int, "min": 256, "max": 4096, "default": 1280},
        "height": {"type": int, "min": 256, "max": 4096, "default": 720},
        "duration": {"type": (int, float), "min": 1, "max": 30, "default": 5},
        "fps": {"type": int, "choices": [8, 12, 16, 24, 30], "default": 24},
    },
    "concept": {
        "prompt": {"type": str, "required": True},
        "width":  {"type": int, "default": 1080},
        "height": {"type": int, "default": 1920},
        "steps":  {"type": int, "min": 1, "max": 100, "default": 25},
        "cfg":    {"type": float, "min": 0.5, "max": 10, "default": 2},
    }
}
方案五：提示词系统与参数系统合并
将 PromptService 扩展为 PromptParamService，一个提示词条目同时携带生成参数：

python
应用
@dataclass
class PromptEntry:
    prompt_id: str
    name: str
    content: str                # 提示词文本
    params: Dict[str, Any] = field(default_factory=dict)  # 关联的工作流参数
    # ↑ 例如 {"steps": 25, "cfg": 2, "width": 1080, "height": 1920}
用户选择一个"预设风格"时，一次获取 prompt + 所有生成参数。

九、实施路线
阶段	任务	工作量	优先级
Phase 1	为 5 个核心工作流编写 workflow_params.json	1天	P0
Phase 2	实现 ParamInjector 引擎替代手写注入	2天	P0
Phase 3	VideoGenerationParams DTO + 两路径统一	1天	P0
Phase 4	API 层参数校验中间件	0.5天	P1
Phase 5	Prompt + Param 系统合并	1天	P1
Phase 6	前端参数表单类型安全	1天	P2
十、紧急修复 Checklist（如果明天就上线）
 修复 MSR 视频路径：duration 转 frame_count 的计算逻辑（用户选 5 秒 → 24fps×5=120 帧）
 修复 provider_service.generate_video() 缺少 width/height/frame_count 参数
 为 最终文生图.json 添加 width/height/steps/cfg 的 config.json 暴露层（当前完全无法覆写）
 在 build_comfyui_workflow 的 cinematic 路径添加 width/height 覆写
 统一前端视频页传递的参数集：加 fps/cfg/steps/seed 到 MsrVideoRequest 中
任务清单 1/5 已完成
生成深度整合分析报告
深度思考
以上是对 ComfyUI 工作流与导演工作台系统深度对接 的全链路深度诊断。核心结论是：当前参数提取是"逐层手动映射"模式，每一层都有遗漏和语义偏差，导致视频参数、图片尺寸、时间控制在传递链断裂。如有需要，我可以立即从 Phase 1（编写 workflow_params.json） 开始推进。

---

## 十一、修复进展附注（2026-08-19）

> 本文为 2026-06-29 的诊断快照，保留原貌作为当时的决策记录。后续迭代已解决下述问题，**当前系统状态请以 `docs/系统功能.md` 为准**，以下为对照现状的进展核实：

| 诊断问题 / 方案 | 后续落地情况 |
|---|---|
| 紧急修复 1：duration → frame_count 时间转换 | 已统一：新增 `video_time.py`，提供时长/帧数互转，覆盖常规与 MSR 视频路径 |
| 紧急修复 2/4：`generate_video()` 缺 width/height + cinematic 路径不覆写尺寸 | 已统一：新增 `video_resolution.py`、`param_validator.py`，尺寸/分辨率参数在阶段层解析并下发；文生图标准版（Z-Image 瑶光）已能端到端出图 |
| 紧急修复 3 + 方案一：工作流参数集中描述 | 已部分落地：巨型单文件拆分后新增 `workflow_params.py` 承载参数定义；火山引擎 Seedream 4.5 采用官方 2K 尺寸映射（1:1→2048、3:4→1728×2304、9:16→1600×2848） |
| 方案二：统一参数注入引擎 | 由模块化后的 `workflow_core.py` / `workflow_params.py` / `qwen_workflow*.py` 共同承担，替代逐层手写注入 |
| 方案三：统一视频参数 DTO | 由 `video_time.py` + `video_resolution.py` + 阶段层 `Params` 校验承载，两套视频路径共用一套尺寸/时间规约 |
| 方案四：参数校验中间件 | 已落地：`param_validator.py` 提供必填/类型/范围校验，阶段层执行 |
| D 级 / 方案五：提示词与参数合并（风格=prompt+参数） | 已落地：`style_registry.py` 定义 6 套网感风格，一次注入脚本文案 + 视觉提示词 + 生成参数，前端提供风格选择器 |
| 架构层面 | `comfyui_service.py`（原 4606 行）拆分为 6 个 Mixin，`workflow_builder.py`（原 2433 行）拆分为 7 个模块；新增一键成片 DAG 全链路、TTS、发布素材包/模板库、多平台导出 |

未完全消除、仍属设计约束的点：部分工作流模板尺寸仍由模板默认值主导（依赖模板正确预设）、`params` 前端 `Record<string, any>` 仍非强类型，均不影响当前管线端到端可用。