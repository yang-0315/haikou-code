"""Offline audit for article-level partial repeal."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import article_force
import force_protocol
import pageindex_force_adapter
import reasoning_retrieval
import retrieval
from force_layer import ForceLayer
from warn import ARTICLE_PARTIAL, ARTICLE_REPEALED, IN_FORCE, QUESTIONABLE

from paths import DEFAULT_DATABASE as DEFAULT_DB
WORKSPACE = os.path.dirname(HERE)


class Audit:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, title: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {title}")
        else:
            self.failed += 1
            print(f"  FAIL  {title}" + (f"：{detail}" if detail else ""))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def walk(nodes):
    for node in nodes or []:
        yield node
        yield from walk(node.get("nodes") or [])


def synthetic_corpus(root: Path) -> None:
    write(root / "测试政策.txt", """测试政策
（2020年1月1日海南省人民政府发布）
第一条 第一条保持有效。
第二条 第二条将被整条删除。
第三条 第三条第（一）项有效。
第三条第（二）项将被删除。
第四条 第四条保持有效。
""")
    write(root / "关于修改《测试政策》的决定.txt", """关于修改《测试政策》的决定
（2025年5月1日通过，2025年6月1日起施行）
省人民政府决定对《测试政策》作如下修改：
一、删去第二条。
二、删去第三条第二款。
本决定自2025年6月1日起施行。
""")
    write(root / "library" / "已更新政策.txt", """关于修改《已更新政策》的决定
（2025年5月1日通过，2025年6月1日起施行）
已更新政策
第一条 现行文本。
第二条 这是重新编号后的新第二条，不是修改前的旧第二条。
""")
    write(root / "关于修改《已更新政策》的决定.txt", """关于修改《已更新政策》的决定
（2025年5月1日通过）
省人民政府决定对《已更新政策》作如下修改：
一、删去第二条。
本决定自2025年6月1日起施行。
""")


def main() -> None:
    audit = Audit()

    print("== 条款解析 ==")
    mixed = article_force.extract_deletions(
        "删除第四条第一款第四项、第十条第三款、第十四条、第二十四条第三项。")
    by_article = {item["article"]: item["scope"] for item in mixed}
    audit.check("混合删除列表逐条保留",
                set(by_article) == {"第四条", "第十条", "第十四条",
                                    "第二十四条"})
    audit.check("整条与款项目删除被区分",
                by_article.get("第十四条") == "whole"
                and by_article.get("第十条") == "partial")
    implicit = article_force.extract_deletions(
        "第十八条改为第十六条，删除第二款。")
    audit.check("后置删除命令关联当前条",
                implicit and implicit[0]["article"] == "第十八条"
                and implicit[0]["scope"] == "partial")
    quoted = article_force.extract_deletions(
        "将第三十八条“违反本条例第X条规定并处罚款”删除。")
    audit.check("引文中的条号不把整条删除降成部分删除",
                quoted and quoted[0]["scope"] == "whole")
    sections = article_force.target_sections(
        "一、对《甲规定》作出修改\n（一）删去第一条。\n"
        "二、对《乙规定》作出修改\n（一）删去第二条。")
    audit.check("多目标修改决定按目标分段",
                len(sections) == 2
                and [name for name, _ in sections] == ["甲规定", "乙规定"])

    with tempfile.TemporaryDirectory(prefix="article-force-", dir=WORKSPACE) as tmp:
        database = Path(tmp)
        synthetic_corpus(database)
        index = article_force.build_index(str(database))
        write(database / article_force.INDEX_NAME,
              json.dumps(index, ensure_ascii=False))

        print("== 索引构建 ==")
        audit.check("索引覆盖两件修改决定",
                    index["summary"]["sources"] == 2,
                    json.dumps(index["summary"], ensure_ascii=False))
        audit.check("旧文本被列入可应用文件",
                    any(event["target"] == "测试政策"
                        and event["pre_change_files"] == ["测试政策.txt"]
                        for event in index["events"]))
        audit.check("已重新公布文本不按旧条号套用",
                    any(event["target"] == "已更新政策"
                        and event["consolidated_files"] == ["已更新政策.txt"]
                        and not event["pre_change_files"]
                        for event in index["events"]))
        audit.check("索引通过语料指纹校验",
                    article_force.index_is_current(index, str(database)))

        layer = ForceLayer(str(database))
        document = layer.status_for(
            "测试政策", file="测试政策.txt")
        second = layer.article_status_for(
            "测试政策", "第二条", document, file="测试政策.txt")
        third = layer.article_status_for(
            "测试政策", "第三条", document, file="测试政策.txt")
        first = layer.article_status_for(
            "测试政策", "第一条", document, file="测试政策.txt")

        print("== 条文级判定 ==")
        audit.check("文档整体仍为现行有效", document["status"] == IN_FORCE)
        audit.check("整条删除标为条文已废止",
                    second["status"] == ARTICLE_REPEALED)
        audit.check("只删款项标为条文部分失效",
                    third["status"] == ARTICLE_PARTIAL)
        audit.check("其他条文不随整篇文档一起失效",
                    first["status"] == IN_FORCE)
        audit.check("整条状态不改写文档级状态",
                    second["status"] != document["status"]
                    and document["status"] == IN_FORCE)

        before = layer.article_status_for(
            "测试政策", "第二条", document, file="测试政策.txt",
            as_of="2025-05-31")
        exact = layer.article_status_for(
            "测试政策", "第二条", document, file="测试政策.txt",
            as_of="2025-06-01")
        crossing = layer.article_status_for(
            "测试政策", "第二条", document, file="测试政策.txt",
            as_of="2025年")
        audit.check("变更日前保持原文档状态", before["status"] == IN_FORCE)
        audit.check("变更日当天条文失效",
                    exact["status"] == ARTICLE_REPEALED)
        audit.check("年份区间跨变更日降为存疑",
                    crossing["status"] == QUESTIONABLE)

        updated = layer.article_status_for(
            "已更新政策", "第二条",
            layer.status_for("已更新政策", file="已更新政策.txt"),
            file="已更新政策.txt")
        audit.check("重新公布文本不误套旧条号",
                    updated["status"] == IN_FORCE)

        nodes = [
            {"title": "第一章", "article": None, "nodes": [
                {"title": "第一条 保持有效", "article": "第一条"},
                {"title": "第二条 已删除", "article": "第二条"},
            ]},
        ]
        stamped = layer.annotate(
            nodes, "测试政策", document, file="测试政策.txt")
        article_forces = {
            node["article"]: node["force"]["status"]
            for node in walk(stamped)
            if node.get("article")
        }
        audit.check("树节点分别携带条文级状态",
                    article_forces == {"第一条": IN_FORCE,
                                       "第二条": ARTICLE_REPEALED})

        units = retrieval.load_articles_from_paths(
            [str(database / "测试政策.txt")], str(database))
        unit_status = {unit["article"]: unit["status"] for unit in units}
        audit.check("检索单元在解析后应用条文状态",
                    unit_status.get("第一条") == IN_FORCE
                    and unit_status.get("第二条") == ARTICLE_REPEALED)
        block = reasoning_retrieval._block(
            layer, str(database),
            {"doc": "测试政策", "file": "测试政策.txt"}, units[1])
        prompt = force_protocol.build_answer_prompt("测试问题", [block])
        audit.check("作答提示携带条文级效力说明",
                    block["status"] == ARTICLE_REPEALED
                    and "条文已废止" in prompt)

        hits = [
            {"doc": "测试政策", "file": "测试政策.txt", "article": "第一条"},
            {"doc": "测试政策", "file": "测试政策.txt", "article": "第二条"},
            {"doc": "测试政策", "file": "测试政策.txt", "article": "第三条"},
        ]
        dropped = layer.review(hits, policy="drop")
        strict = layer.review(hits, policy="strict")
        audit.check("drop 策略只丢弃已废止条文",
                    {hit["article"] for hit in dropped["kept"]}
                    == {"第一条", "第三条"}
                    and dropped["dropped"][0]["article"] == "第二条")
        audit.check("strict 策略也丢弃部分失效条文",
                    {hit["article"] for hit in strict["kept"]} == {"第一条"})

        class FakeClient:
            def get_document(self, doc_id):
                return {"name": "测试政策.txt"}

            def get_tree(self, doc_id, node_summary=False, include_text=True):
                return {"result": [{
                    "title": "第二条", "article": "第二条",
                    "node_id": "0001", "page_index": 1, "nodes": [],
                }]}

            def get_ocr(self, doc_id, format="page"):
                return {"result": "第二条 原文"}

        wrapped = pageindex_force_adapter.ForceAwareClient(
            FakeClient(), str(database))
        wired = wrapped.get_tree("doc-1")
        audit.check("PageIndex 树节点携带条文级状态",
                    wired["result"][0]["force"]["status"] == ARTICLE_REPEALED)

    print("== 真实语料索引 ==")
    live_index = article_force.load_index(DEFAULT_DB)
    audit.check("正式条文索引存在且指纹有效", bool(live_index))
    if live_index:
        summary = live_index["summary"]
        audit.check("正式索引含条文级事件",
                    summary["events"] > 0
                    and summary["article_targets"] > 0)
        live = ForceLayer(DEFAULT_DB)
        document = live.status_for(
            "海南省无线电管理条例", file="海南省无线电管理条例.txt")
        deleted = live.article_status_for(
            "海南省无线电管理条例", "第十七条", document,
            file="海南省无线电管理条例.txt")
        retained = live.article_status_for(
            "海南省无线电管理条例", "第十八条", document,
            file="海南省无线电管理条例.txt")
        audit.check("真实语料整条删除命中",
                    deleted["status"] == ARTICLE_REPEALED)
        audit.check("同一文档其他条文保持文档状态",
                    retained["status"] == document["status"])

    total = audit.passed + audit.failed
    print(f"\narticle-force audit: {audit.passed}/{total}")
    raise SystemExit(0 if audit.failed == 0 else 1)


if __name__ == "__main__":
    main()
