"""ROI 轮询器（S3）测试。"""
from app.collectors import roi_poller


def test_append_sample_accumulates(tmp_path):
    rec = {"url": "https://www.bilibili.com/video/BV1roiAAA1ab", "platform": "bilibili",
           "title": "自产视频", "plays": 100, "likes": 5, "comments": 1, "shares": 2,
           "collects": 3}
    p1 = roi_poller.append_sample(str(tmp_path), rec)
    rec["plays"] = 150
    roi_poller.append_sample(str(tmp_path), rec)

    import json
    seq = json.load(open(p1, encoding="utf-8"))
    assert seq["video_id"] == "BV1roiAAA1ab"
    assert seq["sample_count"] == 2
    assert seq["samples"][0]["plays"] == 100
    assert seq["samples"][1]["plays"] == 150
    assert seq["last_seen"] >= seq["first_seen"]


def test_video_id_fallback_for_non_bilibili():
    rec = {"url": "https://www.douyin.com/video/12345", "platform": "douyin"}
    vid = roi_poller._video_id(rec)
    assert len(vid) == 12 and vid.isalnum()


def test_load_published_urls(tmp_path):
    store = tmp_path / "publish_jobs.json"
    store.write_text(
        '{"a": {"status": "published", "url": "https://b.com/1"},'
        ' "b": {"status": "pending", "url": "https://b.com/2"},'
        ' "c": {"status": "published", "url": ""}}', encoding="utf-8")
    urls = roi_poller.load_published_urls(str(store))
    assert urls == ["https://b.com/1"]
