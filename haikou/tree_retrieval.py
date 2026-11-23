"""沿带效力的树做检索：模型看到的是树上的节点，不是一份条文清单。

与 `reasoning_retrieval.py` 的区别在"喂什么给模型"：

    reasoning_retrieval  按相关度排好的一串条文（条号寻址）
    tree_retrieval       持久化的带效力树（章 → 条，node_id 寻址）

树不是临时拼的：它来自 `_force_trees.json`，每个节点自带 force，选中的节点
按 node_id 回原文取正文——和 PageIndex 客户端 `get_tree`／`get_ocr` 同一套
接缝，所以这段逻辑换成真实 PageIndex 索引也成立。

    python tree_retrieval.py "免税店的设立需要什么条件" --dry-run
    python tree_retrieval.py "免税店的设立需要什么条件" --show-trace
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reasoning_retrieval as rr
import retrieval
import temporal
import tree_index
from force_layer import ForceLayer
from force_protocol import (MAX_BLOCKS, build_answer_prompt,
                            build_node_select_prompt)

from paths import DEFAULT_DATABASE as DEFAULT_DB
MARK_MATCHED = "◆"
MARK_OTHER = "·"


def load_records(store: tree_index.LocalTreeStore, database: str, query: str,
                 docs_wanted: int,
                 as_of=None) -> tuple[list[dict], dict[str, set[str]]]:
    """候选文档沿用已验证的文档级召回，再取出它们各自的树。"""
    hits = rr.recall(database, query, docs_wanted, as_of=as_of)
    records, matched = [], {}
    layer = ForceLayer(database)
    for hit in hits:
        record = store.by_file(hit["file"])
        if record is None:
            continue
        record = dict(record)
        numbers = [record["doc_number"]] if record.get("doc_number") else []
        record["status"] = layer.status_for(
            record["title"], numbers, store.text_of(record)[:1800], as_of,
            file=record["file"])
        record["structure"] = layer.annotate(
            record["structure"], record["title"], record["status"],
            file=record["file"], as_of=as_of)
        records.append(record)
        # recall 给的是整篇条文，命中的那些带 matched 标记
        matched[record["file"]] = {a["article"] for a in hit["articles"]
                                   if a.get("matched")}
    return records, matched


def tree_outline(store: tree_index.LocalTreeStore, records: list[dict],
                 matched: dict[str, set[str]]) -> str:
    """树上带效力的目录：章 → 条，命中条标 ◆。"""
    lines = []
    for record in records:
        hits = matched.get(record["file"], set())
        lines.append(f"【文档】{record['title']}　效力：{record['status']['status']}")

        def walk(nodes, depth=1):
            for node in nodes:
                mark = MARK_MATCHED if (node.get("article") in hits) else MARK_OTHER
                title = node["title"]
                force = node.get("force") or {}
                suffix = ""
                if (force.get("status")
                        and force["status"] != record["status"]["status"]):
                    suffix = f"　〔条文效力：{force['status']}〕"
                lines.append(f"{'  ' * depth}{mark} {node['node_id']}　{title}"
                             f"　p{node['start_index']}{suffix}")
                walk(node.get("nodes") or [], depth + 1)

        walk(record["structure"])
    return "\n".join(lines)


def node_index(record: dict) -> dict[str, dict]:
    return {node["node_id"]: node for node in tree_index.walk(record["structure"])}


def parse_node_selection(selected) -> list[tuple[str, str]]:
    """模型可能写「文档名|0001」，也可能只写「0001」。"""
    out = []
    for item in selected or []:
        text = str(item).strip()
        # 文档名里常有年份（"琼府办〔2021〕71号"），所以有竖线时只看竖线右边
        tail = text.rsplit("|", 1)[1] if "|" in text else text
        match = re.search(r"(\d{2,4})", tail)
        if not match:
            continue
        node_id = match.group(1)
        doc = text.split("|")[0].strip() if "|" in text else ""
        out.append((doc, node_id))
    return out


def find_path(nodes: list[dict], node_id: str,
              trail: list[str] | None = None) -> list[str] | None:
    """节点在树里的路径（章 → 条），用于引用时交代出处。"""
    trail = trail or []
    for node in nodes:
        if node["node_id"] == node_id:
            return trail + [node["title"]]
        found = find_path(node.get("nodes") or [], node_id,
                          trail + [node["title"]])
        if found:
            return found
    return None


def resolve_nodes(store: tree_index.LocalTreeStore, records: list[dict],
                  picked: dict) -> tuple[list[dict], list[str]]:
    """把选中的 node_id 落成待读节点（正文按 node_id 回原文取）。"""
    notes: list[str] = []
    blocks: list[dict] = []
    taken: set[tuple[str, str]] = set()
    texts: dict[str, str] = {}

    def take(record: dict, node: dict) -> None:
        if len(blocks) >= MAX_BLOCKS:
            return
        key = (record["file"], node["node_id"])
        if key in taken:
            return
        taken.add(key)
        if record["file"] not in texts:
            texts[record["file"]] = store.text_of(record)
        text = texts[record["file"]]
        trail = find_path(record["structure"], node["node_id"]) or []
        blocks.append({
            "doc": record["title"],
            "doc_number": record.get("doc_number"),
            "article": node.get("article") or node["title"][:12],
            "node_id": node["node_id"],
            "path": " → ".join(trail[:-1]),
            "page_index": node["start_index"],
            "status": (node.get("force") or record["status"])["status"],
            "force_reason": ((node.get("force") or {}).get("reason") or {}).get(
                "text", ""),
            "force_detail": node.get("force") or record["status"],
            "text": text[node["start_char"]:node["end_char"]],
        })

    for doc_name, node_id in parse_node_selection(picked.get("nodes")):
        found = [(record, node_index(record).get(node_id)) for record in records]
        found = [(record, node) for record, node in found if node]
        if not found:
            notes.append(f"模型选的 node_id {node_id} 在候选树里不存在")
            continue
        if doc_name:
            narrowed = [pair for pair in found
                        if doc_name in pair[0]["title"]
                        or doc_name in pair[0]["file"]]
            if narrowed:
                found = narrowed
        if len(found) > 1:
            notes.append(f"node_id {node_id} 在 {len(found)} 份候选里都存在，"
                         f"只取排在最前的《{found[0][0]['title'][:22]}》")
        record, node = found[0]
        take(record, node)
    return blocks, notes


def tree_ask(query: str, database: str = DEFAULT_DB, docs_wanted: int = 4,
             temperature: float = 0.2, chat_fn=None,
             store: tree_index.LocalTreeStore | None = None,
             as_of=None) -> dict:
    """沿树检索并作答。返回结构对齐 `reasoning_retrieval.ask`。"""
    query = retrieval.normalize_query(query)
    ask_model = chat_fn or rr.chat
    point = temporal.parse_as_of(as_of) if as_of else temporal.find_as_of(query)
    store = store or tree_index.LocalTreeStore(database)
    in_scope, reason = rr.scope_query(query)
    result = {"query": query, "answer": None, "refusal": None,
              "refusal_stage": None, "sources": [], "notes": [],
              "reason": "", "force_note": "", "path": "tree",
              "as_of": point}
    if not in_scope:
        result.update(refusal=reason, refusal_stage="scope")
        return result

    records, matched = load_records(store, database, query, docs_wanted, point)
    if not records:
        result.update(refusal="候选文档为空：问题用词在语料里没有对应条文",
                      refusal_stage="recall")
        return result
    outline = tree_outline(store, records, matched)
    layer = ForceLayer(database)
    pseudo_docs = [{"doc": record["title"], "file": record["file"]}
                   for record in records]
    candidate_notes = rr.named_document_notes(
        layer, pseudo_docs, query, database, point)

    try:
        raw = ask_model([{"role": "user",
                          "content": build_node_select_prompt(
                              query, outline, point, candidate_notes)}])
    except Exception as exc:                      # noqa: BLE001
        result.update(refusal=f"模型调用失败：{type(exc).__name__}: {exc}",
                      refusal_stage="llm_error")
        return result
    try:
        picked = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:
        result.update(refusal=f"模型未返回可解析的选择：{raw[:120]}",
                      refusal_stage="select_parse")
        return result

    result["reason"] = picked.get("reason") or ""
    result["force_note"] = picked.get("force_note") or ""
    if picked.get("answerable") is False:
        flat_docs = rr.recall(
            database, query, max(docs_wanted, 4), as_of=point)
        fallback = rr.repealed_named_answer(
            layer, database, flat_docs, query, point)
        if fallback:
            result.update(fallback)
            result["path"] = "tree"
            return result
        result.update(refusal=("候选中没有能回答该问题的节点。模型理由："
                               f"{picked.get('reason') or '未说明'}"),
                      refusal_stage="model")
        return result

    blocks, notes = resolve_nodes(store, records, picked)
    result["notes"] = notes
    if not blocks:
        result.update(refusal=("模型选的节点取不到："
                               + json.dumps(picked.get("nodes"),
                                            ensure_ascii=False)),
                      refusal_stage="select_empty")
        return result
    result["sources"] = [{"doc": block["doc"], "article": block["article"],
                          "node_id": block["node_id"], "status": block["status"],
                          "path": block["path"],
                          "reason": block.get("force_reason", ""),
                          "evidence": block.get("force_detail", {}).get(
                              "evidence"),
                          "caveats": block.get("force_detail", {}).get(
                              "caveats", [])}
                         for block in blocks]
    flat_docs = rr.recall(
        database, query, max(docs_wanted, 4), as_of=point)
    rr.prune_noncurrent_sources(
        result, layer, flat_docs, query, database, point)
    try:
        result["answer"] = ask_model(
            [{"role": "user",
              "content": build_answer_prompt(query, blocks, notes, point)}],
            temperature=temperature)
        flat_docs = rr.recall(
            database, query, max(docs_wanted, 4), as_of=point)
        rr.ensure_named_repealed_notice(
            result, layer, flat_docs, query, database, point)
    except Exception as exc:                      # noqa: BLE001
        result.update(refusal=f"模型调用失败：{type(exc).__name__}: {exc}",
                      refusal_stage="llm_error")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--docs", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印树目录，不调模型")
    parser.add_argument("--show-trace", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compare", action="store_true",
                        help="同时跑条文路径，比较两边取到的依据")
    parser.add_argument("--as-of",
                        help="查询时点：2022-09-01、2022年9月或2022年")
    args = parser.parse_args()

    store = tree_index.LocalTreeStore(args.database)
    point = temporal.parse_as_of(args.as_of) if args.as_of \
        else temporal.find_as_of(args.query)
    if args.dry_run:
        records, matched = load_records(store, args.database, args.query,
                                        args.docs, point)
        if point:
            print(temporal.context(point) + "\n")
        print(tree_outline(store, records, matched))
        return

    result = tree_ask(args.query, args.database, args.docs, store=store,
                      as_of=point)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result["answer"] is None:
        print("== 拒答 ==")
        print(result["refusal"])
    else:
        if args.show_trace:
            print("== 沿树选点 ==")
            print(f"  理由：{result['reason']}")
            for note in result["notes"]:
                print(f"  说明：{note}")
            for source in result["sources"]:
                print(f"  取到 [{source['status']}] node {source['node_id']}"
                      f"　{source['doc'][:20]} {source['article']}")
            print()
        print("== 回答 ==")
        print(result["answer"])

    if args.compare:
        flat = rr.ask(args.query, args.database, args.docs, as_of=point)
        tree_pairs = {(s["doc"], s["article"]) for s in result["sources"]}
        flat_pairs = {(s["doc"], s["article"]) for s in flat["sources"]}
        print("\n== 两条路径对照 ==")
        print(f"  树路径取到 {len(tree_pairs)} 条：{sorted(tree_pairs)}")
        print(f"  条文路径取到 {len(flat_pairs)} 条：{sorted(flat_pairs)}")
        print(f"  交集 {len(tree_pairs & flat_pairs)} 条")


if __name__ == "__main__":
    main()
