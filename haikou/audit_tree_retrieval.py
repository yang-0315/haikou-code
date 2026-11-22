"""沿树检索的离线审计：node_id 寻址、取错防护、拒答、与条文路径同源。

全部用注入的假模型，不联网。选点这条链路最容易出的问题是"模型给的编号
取不到却静默换一条"，所以每条分支都要有人看着。

    python audit_tree_retrieval.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrieval
import tree_index
import tree_retrieval as tr

from paths import DEFAULT_DATABASE as DB
QUERY = "日用消费品免税店的设立申请要提交哪些材料"
DOC = "海南自由贸易港岛内居民消费“零关税”进境商品经营主体及免税店管理办法（试行）"


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    label = "通过" if ok else ("跳过" if ok is None else "问题")
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok


def fake(payload: str):
    def call(messages, temperature=0.0, **kwargs):
        return payload
    return call


def main():
    store = tree_index.LocalTreeStore(DB)
    records, matched = tr.load_records(store, DB, QUERY, 4)
    results = []
    if not records:
        print("召回为空，审计无法进行")
        return
    record = records[0]
    index = tr.node_index(record)
    target = next(n["node_id"] for n in tree_index.walk(record["structure"])
                  if n.get("article") == "第十条")

    def hit_node_id():
        picked = {"nodes": [f"{record['title']}|{target}"]}
        blocks, notes = tr.resolve_nodes(store, records, picked)
        return (len(blocks) == 1 and blocks[0]["node_id"] == target
                and blocks[0]["article"] == "第十条"), \
            f"取到 node {blocks[0]['node_id']}（{blocks[0]['article']}）" \
            if blocks else "没取到"
    results.append(probe("按 node_id 取到正确节点", hit_node_id))

    def unknown_node_id():
        blocks, notes = tr.resolve_nodes(store, records, {"nodes": ["9999"]})
        return (not blocks and notes and "不存在" in notes[0]), \
            f"取到 {len(blocks)} 条；{notes[:1]}"
    results.append(probe("不存在的 node_id 不静默换一条", unknown_node_id))

    def bare_node_id():
        blocks, notes = tr.resolve_nodes(store, records, {"nodes": [target]})
        return (len(blocks) == 1 and blocks[0]["article"] == "第十条"), \
            f"只给编号也取到 {[b['article'] for b in blocks]}"
    results.append(probe("只给 node_id（不带文档名）也能取到", bare_node_id))

    def path_recorded():
        blocks, _ = tr.resolve_nodes(store, records,
                                     {"nodes": [f"{record['title']}|{target}"]})
        trail = tr.find_path(record["structure"], target)
        return (len(trail) == 2 and blocks[0]["path"] == trail[0]), \
            f"路径 {' → '.join(trail)}；块里记的是「{blocks[0]['path']}」"
    results.append(probe("引用带上章路径（章 → 条）", path_recorded))

    def answerable_false():
        result = tr.tree_ask(QUERY, DB, 4,
                             chat_fn=fake('{"answerable": false, "reason": "目录里没有"}'),
                             store=store)
        return (result["answer"] is None
                and result["refusal_stage"] == "model"), \
            f"阶段 {result['refusal_stage']}；{result['refusal'][:40]}"
    results.append(probe("模型判答不了 → 拒答", answerable_false))

    def bad_json():
        result = tr.tree_ask(QUERY, DB, 4, chat_fn=fake("抱歉，我无法回答"),
                             store=store)
        return (result["answer"] is None
                and result["refusal_stage"] == "select_parse"), \
            f"阶段 {result['refusal_stage']}"
    results.append(probe("模型返回非 JSON → 可捕获", bad_json))

    def end_to_end_offline():
        payload = json.dumps({"answerable": True,
                              "nodes": [f"{record['title']}|{target}"],
                              "reason": "第十条规定了申请材料"})
        calls = []

        def model(messages, temperature=0.0, **kwargs):
            calls.append(messages[0]["content"])
            return payload if len(calls) == 1 else "结论：需提交五类材料。"

        result = tr.tree_ask(QUERY, DB, 4, chat_fn=model, store=store)
        ok = (result["answer"] and len(result["sources"]) == 1
              and result["sources"][0]["status"] == "现行有效"
              and result["sources"][0]["node_id"] == target)
        return ok, (f"回答 {len(result['answer'])} 字；引用 "
                    f"{[(s['node_id'], s['status']) for s in result['sources']]}")
    results.append(probe("端到端（假模型）带 node_id 与效力", end_to_end_offline))

    def same_text_as_flat():
        """树上取的正文必须与检索层认的条文逐字一致。"""
        text = store.text_of(record)
        node = index[target]
        tree_text = text[node["start_char"]:node["end_char"]]
        flat = {a["article"]: a["text"]
                for a in retrieval.parse_articles(record["path"], {})}
        return (tree_text.replace(" ", "").replace("\n", "")
                == flat["第十条"].replace(" ", "")), \
            f"树 {len(tree_text)} 字符 / 条文 {len(flat['第十条'])} 字符"
    results.append(probe("树上取的正文与检索层逐字一致", same_text_as_flat))

    passed = sum(1 for ok in results if ok is True)
    skipped = sum(1 for ok in results if ok is None)
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")


if __name__ == "__main__":
    main()
