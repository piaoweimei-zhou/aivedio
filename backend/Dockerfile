# 后端镜像（ComfyUI 为宿主机 GPU 进程，不容器化）
FROM python:3.13.0-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
# 生产镜像只装运行时依赖（不装 pytest 等测试依赖）；开发者本地/CI 用 requirements.lock 做可复现安装
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 复制后端代码与工作流模板（保持 ../workflows 相对布局）
COPY backend /app/backend
COPY workflows /app/workflows

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
