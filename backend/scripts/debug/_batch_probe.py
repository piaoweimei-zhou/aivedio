import asyncio
import httpx
from tools.baseline_oneclick import build_oneclick_steps


async def main():
    steps = build_oneclick_steps("comfyui", "一只会做饭的猫")
    payload = {"project_id": "baseline", "name": "基线测量", "steps": steps}
    async with httpx.AsyncClient() as c:
        r = await c.post("http://127.0.0.1:8000/api/director/batches", json=payload, timeout=30)
        print("STATUS", r.status_code)
        print("BODY", r.text[:800])


asyncio.run(main())
