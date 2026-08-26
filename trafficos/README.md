# TrafficOS — 流量侧系统

独立部署的流量侧服务（端口 8001），经内容生产契约对接 director（端口 8000）。

## 定位
**3 类内容 × 7 轨变现 × 5 类账号 + 工具传感器 + 双服务解耦**
详见 `docs/01_规划/流量侧系统规划.md`（v1.4）。

## 结构
```
trafficos/
├── app/
│   ├── main.py          # FastAPI 入口（端口 8001）
│   ├── models.py        # 数据模型（对齐 traffic_contract.openapi.yaml）
│   ├── storage.py       # JSON 文件集合存储（可审计）
│   ├── scoring.py       # 选题打分器（6 权重）
│   ├── packaging.py     # 包装生成器（三维度×7 变现）
│   ├── cover.py         # 封面合成器（Pillow）
│   ├── analytics.py     # 看板聚合 + ROI 归因
│   └── api/             # 路由：dimensions/monetizers/accounts/topics/signals/...
├── sdk/
│   └── tool_tracker.py  # 工具传感器埋点 SDK（无第三方依赖）
├── tests/               # pytest
├── requirements.txt
└── .env.example
```

## 运行
```bash
cd trafficos
pip install -r requirements.txt
# 开发
python -m uvicorn app.main:app --port 8001 --reload
# 测试
python -m pytest tests -q
# lint
python -m flake8 app tests sdk --max-line-length=100
```

## 工具接入埋点（B8，工具即传感器）
产品工具（去水印等）接入 `sdk/tool_tracker.py`，把"用户下载/搜索了什么"变成需求信号，反哺选题：
```python
from sdk.tool_tracker import ToolTracker
tracker = ToolTracker("http://127.0.0.1:8001", tool_name="watermark-remover")
tracker.track(action="download", title="明星采访视频", url="https://...")  # → 需求信号
```
- 服务端自动提取关键词 + 按动作加权热度（save 1.2 / download 1.0 / analyze 0.8）
- `GET /api/traffic/signals/suggest-topics?save=true`：信号 → 选题建议（自动打标+打分）入库

## 对接 director（契约）
- 契约 SSOT：`docs/01_规划/traffic_contract.openapi.yaml`
- director 端适配器：`backend/api/contract_api.py`（未挂载，P0 待接入）

## 当前进度
- [x] B1 服务骨架 + 存储 + 数据模型
- [x] B2 维度/变现/账号 配置 API
- [x] B3 选题打分器 + 选题库
- [x] B4 标题/封面生成器
- [x] B5 封面合成
- [ ] B6 抖音发布接入（需权限验证）
- [x] B7 数据看板 + ROI 归因
- [x] B8 工具信号上报 + 埋点 SDK
