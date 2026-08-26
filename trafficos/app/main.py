"""TrafficOS — 流量侧服务入口。

独立部署（端口 8001），经内容生产契约对接 director（端口 8000）。
本服务只做流量/内容/数据/变现，不碰生产逻辑。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (accounts, cover, dashboard, dimensions, hits, hotspots,
                     metrics, monetizers, packaging, publishing, signals,
                     topics)

logger = logging.getLogger(__name__)

APP_TITLE = "TrafficOS 流量侧系统"
APP_VERSION = "0.1.0"

DATA_DIR = os.environ.get(
    "TRAFFICOS_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info("[TrafficOS] 数据目录就绪: %s", DATA_DIR)

    # P1a 热点定时刷新（默认关；TRAFFICOS_HOTSPOT_AUTO=1 且间隔>0 才启）
    interval_h = float(os.environ.get("TRAFFICOS_HOTSPOT_INTERVAL_HOURS", "0"))
    if os.environ.get("TRAFFICOS_HOTSPOT_AUTO", "0") == "1" and interval_h > 0:
        import asyncio
        from app.hotspots import sync as _hs_sync

        async def _loop():
            while True:
                try:
                    await asyncio.to_thread(_hs_sync, 50)
                    logger.info("[TrafficOS] 热点定时同步完成")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[TrafficOS] 热点定时同步异常: %s", exc)
                await asyncio.sleep(interval_h * 3600)

        task = asyncio.create_task(_loop())
        logger.info("[TrafficOS] 热点定时刷新已启用（间隔 %.1fh）", interval_h)
        yield
        task.cancel()
    else:
        logger.info("[TrafficOS] 热点定时刷新未启用（TRAFFICOS_HOTSPOT_AUTO=1 可开启）")
        yield


app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)

_routers = [
    dimensions.router,
    monetizers.router,
    accounts.router,
    topics.router,
    signals.router,
    hits.router,
    hotspots.router,
    packaging.router,
    cover.router,
    metrics.router,
    dashboard.router,
    publishing.router,
]
for r in _routers:
    app.include_router(r)
    logger.info("[TrafficOS] 路由注册: %s", r.prefix)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": APP_TITLE, "version": APP_VERSION}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("TRAFFICOS_PORT", "8001"))
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)
