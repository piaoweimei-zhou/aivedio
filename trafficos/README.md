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
│   └── api/             # 路由：dimensions/monetizers/accounts/topics/...
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
python -m flake8 app tests
```

## 对接 director（契约）
- 契约 SSOT：`docs/01_规划/traffic_contract.openapi.yaml`
- director 端适配器：`backend/api/contract_api.py`（未挂载，P0 待接入）

## 当前进度
- [x] B1 服务骨架 + 存储 + 数据模型
- [x] B2 维度/变现/账号 配置 API
- [ ] B3 选题打分器 + 选题库
- [ ] B4 标题/封面生成器
- [ ] B5 封面合成
- [ ] B6 抖音发布接入（需权限验证）
- [ ] B7 数据看板 + ROI 归因
- [ ] B8 工具信号上报 + 埋点
