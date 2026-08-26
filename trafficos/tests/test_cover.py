"""B5 封面合成器 + 封面 API 测试"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b5_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.cover import badge_from_style, render_cover  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    import shutil
    covers = os.path.join(_tmp, "covers")
    if os.path.exists(covers):
        shutil.rmtree(covers)


# ---------- 合成器纯逻辑 ----------

def test_badge_from_style():
    assert badge_from_style("干货标题大字 + 编号清单列表") == "干货"
    assert badge_from_style("前后对比图 + 工具名大字 + '亲测可用'角标") == "亲测可用"
    assert badge_from_style("资源堆叠展示 + 箭头") == "资源"
    assert badge_from_style("高冲击悬念风格") == "高能"
    assert badge_from_style("") == "推荐"


def test_render_cover_creates_file():
    r = render_cover("3秒解决短视频去水印", "前后对比图 + 工具名大字 + 亲测可用角标",
                     output_dir=os.path.join(_tmp, "covers"))
    assert r["cover_id"].startswith("cover_")
    assert os.path.exists(r["path"])
    assert r["badge"] == "亲测可用"
    assert r["size"] == [1080, 1440]

    # 验证图片真实可读、尺寸正确
    from PIL import Image
    img = Image.open(r["path"])
    assert img.size == (1080, 1440)


def test_render_with_bg_url_and_wrap():
    # 无 bg_url：渐变背景；长标题多行不崩溃
    long_title = "这是一段非常非常非常长的封面标题用来测试自动换行的效果如何展示"
    r = render_cover(long_title, "干货清单风格", output_dir=os.path.join(_tmp, "covers"))
    assert os.path.exists(r["path"])


# ---------- API ----------

def test_render_api():
    r = client.post("/api/traffic/cover/render", params={
        "title": "3秒解决去水印",
        "cover_style": "亲测可用",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["cover_id"]
    assert body["url"].startswith("/api/traffic/cover/files/")

    # 静态访问
    r2 = client.get(body["url"])
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/jpeg")
    assert len(r2.content) > 1000  # 真实图片数据


def test_cover_file_path_traversal_blocked():
    r = client.get("/api/traffic/cover/files/..%2F..%2Fsecret")
    # 路径穿越被拦截（400 或 404）
    assert r.status_code in (400, 404)
    r2 = client.get("/api/traffic/cover/files/../x.jpg")
    assert r2.status_code in (400, 404)
