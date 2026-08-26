"""TrafficOS 内容初始化种子（C 组）：账号矩阵 + 包装模板 + 选题库。

幂等：按唯一键跳过已存在（账号按 name、模板按 dimension+monetizer、选题按 title）。
数据来源贴合产品矩阵（bupvideo：去水印工具 / 192 工具方向 / 虚拟资源 / 网课 / SaaS）。

用法：
    python scripts/seed.py                 # 灌数据（幂等）
    python scripts/seed.py --force         # 强制重建（先清空再灌）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.topics import build_topic  # noqa: E402
from app.models import (AccountConfig, AccountCadence, Dimension, Monetizer,  # noqa: E402
                        PackagingTemplate, Topic)
from app.storage import get_collection  # noqa: E402


# ==================== 账号矩阵（5 类账号，C3）====================

SEED_ACCOUNTS = [
    AccountConfig(name="内容号·轻松分享", dimension=Dimension.PURE_CONTENT,
                  monetizer=Monetizer.ADSHARE, persona="轻松有趣的内容分享者",
                  cadence=AccountCadence.HIGH, bio="每天一条，带你换个角度看世界", note="内容号"),
    AccountConfig(name="知识号·干货输出", dimension=Dimension.KNOWLEDGE,
                  monetizer=Monetizer.COURSE, persona="干货知识博主",
                  cadence=AccountCadence.MEDIUM, bio="零基础也能学会的实用技能", note="知识号"),
    AccountConfig(name="工具号·亲测好用", dimension=Dimension.SOFT_AD,
                  monetizer=Monetizer.TOOL, persona="效率工具实测博主",
                  cadence=AccountCadence.HIGH, bio="亲测好用的效率工具，每天一个", note="工具号"),
    AccountConfig(name="资源号·福利分享", dimension=Dimension.KNOWLEDGE,
                  monetizer=Monetizer.RESOURCE, persona="资源福利君",
                  cadence=AccountCadence.MEDIUM, bio="转存就送，持续更新", note="资源号"),
    AccountConfig(name="产品号·企业方案", dimension=Dimension.SOFT_AD,
                  monetizer=Monetizer.SAAS, persona="企业数字化方案顾问",
                  cadence=AccountCadence.LOW, bio="让企业运营更高效", note="产品号"),
]

# ==================== 包装模板固化（C2）====================

SEED_TEMPLATES = [
    PackagingTemplate(dimension=Dimension.SOFT_AD, monetizer=Monetizer.TOOL,
                      title_templates=["3 秒解决 {topic}，亲测可用", "还在手动 {topic}？这个工具直接解放你"],
                      hook_templates=["以前要半小时，现在 3 秒", "真实演示，不吹不黑"],
                      cover_style="前后对比图 + 工具名大字 + 亲测可用角标", note="工具号主模板"),
    PackagingTemplate(dimension=Dimension.KNOWLEDGE, monetizer=Monetizer.COURSE,
                      title_templates=["零基础学会 {topic}，别再走弯路", "{topic} 避坑指南（收藏级）"],
                      hook_templates=["这 3 个坑，90% 新手都踩过", "今天一次讲清 {topic}"],
                      cover_style="干货标题大字 + 编号清单列表", note="知识号主模板"),
    PackagingTemplate(dimension=Dimension.PURE_CONTENT, monetizer=Monetizer.ADSHARE,
                      title_templates=["{topic}，看完我直接愣住", "3 分钟看懂 {topic}"],
                      hook_templates=["别划走，这个你绝对没见过", "看完第 3 秒，你也会沉默"],
                      cover_style="大字悬念 + 高反差对比图", note="内容号主模板"),
    PackagingTemplate(dimension=Dimension.KNOWLEDGE, monetizer=Monetizer.RESOURCE,
                      title_templates=["{topic} 全套资料，转存就送", "这份 {topic} 资料，够你用一年"],
                      hook_templates=["资料整理不易，点个关注持续更新"],
                      cover_style="资源堆叠展示 + 免费领取标签", note="资源号主模板"),
    PackagingTemplate(dimension=Dimension.SOFT_AD, monetizer=Monetizer.SAAS,
                      title_templates=["企业都在用的 {topic} 方案", "{topic} 系统化，效率翻倍的秘密"],
                      hook_templates=["别再手工操作了，上系统", "这套方案我们跑了半年"],
                      cover_style="商务感排版 + 数据提升对比", note="产品号主模板"),
    PackagingTemplate(dimension=Dimension.SOFT_AD, monetizer=Monetizer.XIANYU,
                      title_templates=["{topic} 闲鱼实操，日入小目标", "闲鱼卖 {topic}，新手也能上手"],
                      hook_templates=["实操截图，真实收益"],
                      cover_style="收益截图 + 实操步骤", note="闲鱼号主模板"),
]

# ==================== 选题库（30 条，C1，贴合产品矩阵）====================

# (title, dimension, monetizer)
SEED_TOPICS_RAW = [
    # 工具号（soft_ad × tool）8 条——来自 192 工具方向
    ("短视频去水印神器，3 秒搞定", Dimension.SOFT_AD, Monetizer.TOOL),
    ("批量下载抖音视频，一个工具全搞定", Dimension.SOFT_AD, Monetizer.TOOL),
    ("电商卖家都在用的竞品价格监控工具", Dimension.SOFT_AD, Monetizer.TOOL),
    ("AI 视频字幕翻译+配音出海，小白也能做", Dimension.SOFT_AD, Monetizer.TOOL),
    ("直播录屏自动切片，主播效率翻倍", Dimension.SOFT_AD, Monetizer.TOOL),
    ("电商评价采集+差评情感分析工具", Dimension.SOFT_AD, Monetizer.TOOL),
    ("AI Token 用量监控，省钱神器", Dimension.SOFT_AD, Monetizer.TOOL),
    ("短视频矩阵多平台分发工具", Dimension.SOFT_AD, Monetizer.TOOL),
    # 知识号（knowledge × course）6 条
    ("零基础学会视频剪辑，别再走弯路", Dimension.KNOWLEDGE, Monetizer.COURSE),
    ("AI 绘画入门，从提示词开始", Dimension.KNOWLEDGE, Monetizer.COURSE),
    ("电商选品数据分析实战教程", Dimension.KNOWLEDGE, Monetizer.COURSE),
    ("AI 客服机器人搭建教程", Dimension.KNOWLEDGE, Monetizer.COURSE),
    ("短视频矩阵运营避坑指南", Dimension.KNOWLEDGE, Monetizer.COURSE),
    ("数据采集入门：招投标监控", Dimension.KNOWLEDGE, Monetizer.COURSE),
    # 资源号（knowledge × resource/netdisk）5 条
    ("电商运营全套模板合集，转存就送", Dimension.KNOWLEDGE, Monetizer.RESOURCE),
    ("AI 提示词大全，够用一年", Dimension.KNOWLEDGE, Monetizer.RESOURCE),
    ("短视频文案库 1000 条，免费领", Dimension.KNOWLEDGE, Monetizer.RESOURCE),
    ("电商财税核算模板，直接套用", Dimension.KNOWLEDGE, Monetizer.RESOURCE),
    ("全套电商运营资料，网盘版", Dimension.PURE_CONTENT, Monetizer.NETDISK),
    # 产品号（soft_ad × saas）4 条
    ("企业都在用的 AI 工作流自动化方案", Dimension.SOFT_AD, Monetizer.SAAS),
    ("云服务器成本优化，省一半预算", Dimension.SOFT_AD, Monetizer.SAAS),
    ("企业知识库+AI 问答私有化部署", Dimension.SOFT_AD, Monetizer.SAAS),
    ("多平台订单库存同步系统", Dimension.SOFT_AD, Monetizer.SAAS),
    # 内容号（pure_content × adshare）3 条
    ("3 分钟看懂 AI 到底改变了什么", Dimension.PURE_CONTENT, Monetizer.ADSHARE),
    ("电商人的一天，真实记录", Dimension.PURE_CONTENT, Monetizer.ADSHARE),
    ("副业赚钱的 7 个真实路径", Dimension.PURE_CONTENT, Monetizer.ADSHARE),
    # 闲鱼号（soft_ad × xianyu）2 条
    ("闲鱼卖虚拟资源，新手实操", Dimension.SOFT_AD, Monetizer.XIANYU),
    ("闲鱼日入小目标的选品思路", Dimension.SOFT_AD, Monetizer.XIANYU),
    # 网盘拉新（pure_content × netdisk）2 条
    ("短视频工具合集，懂的都懂", Dimension.PURE_CONTENT, Monetizer.NETDISK),
    ("设计师素材合集，网盘自取", Dimension.PURE_CONTENT, Monetizer.NETDISK),
]

SEED_TOPICS = [
    build_topic(Topic(
        title=t, dimension=d, monetizer=m, source="seed",
        weights={"hot": 0.5, "fit": 0.5, "convert": 0.5,
                 "signal": 0.3, "competition": 0.5, "timeliness": 0.5},
    ))
    for t, d, m in SEED_TOPICS_RAW
]


def _as_dict(rec) -> dict:
    return rec if isinstance(rec, dict) else rec.model_dump()


def _insert_unique(col_name: str, records: list, key_fn) -> int:
    """幂等插入：key_fn 接收 dict（storage.list() 返回 dict 形态）。"""
    col = get_collection(col_name)
    existing = {key_fn(r) for r in col.list()}
    inserted = 0
    for rec in records:
        if key_fn(_as_dict(rec)) in existing:
            continue
        col.insert(rec)
        inserted += 1
        existing.add(key_fn(_as_dict(rec)))
    return inserted


def seed(force: bool = False) -> dict:
    if force:
        for name in ("accounts", "packaging", "topics"):
            get_collection(name).clear()

    n_acc = _insert_unique("accounts", SEED_ACCOUNTS, lambda d: d["name"])
    n_tpl = _insert_unique(
        "packaging", SEED_TEMPLATES,
        lambda d: (d["dimension"], d["monetizer"]),
    )
    n_topic = _insert_unique("topics", SEED_TOPICS, lambda d: d["title"])
    return {"accounts": n_acc, "templates": n_tpl, "topics": n_topic}


if __name__ == "__main__":
    force = "--force" in sys.argv
    result = seed(force=force)
    print(f"[TrafficOS seed] 新插入: 账号 {result['accounts']} / "
          f"模板 {result['templates']} / 选题 {result['topics']}")
