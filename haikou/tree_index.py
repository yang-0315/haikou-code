"""把效力挂到真正的 PageIndex 树上，并让树本身成为检索底座。

上游的树以**页**为单位：节点是 `{title, node_id, start_index, end_index,
nodes}`，`start_index/end_index` 是页码。政策文本是从网页抽出来的，没有 PDF
分页，所以这里先定义一个确定的页单位（每 `PAGE_CHARS` 字一页），再用与检索层
**同一套**章/条正则切层级——树与检索层必须指向同一段文字，否则两条路会各说
各话。

每个节点都带 `force`（效力是文档级事实，节点继承），这就是"效力作为维度挂在
树上"：模型看到的不再是一份判定结论，而是树上每个位置自带的效力。

    python tree_index.py --build          # 全库建树 + 效力标注，落盘
    python tree_index.py --stats          # 看索引概况
    python tree_index.py --doc "文档名"    # 打印一份树的带效力目录
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
import retrieval
from force_layer import ForceLayer
from paths import resolve_corpus_path
from retrieval import CHAPTER, article_name
from warn import own_numbers

from paths import DEFAULT_DATABASE as DEFAULT_DB
STORE_NAME = "_force_trees.json"

# 一"页"多少字。网页文本没有真实分页，取一个确定的值：既让节点跨度有意义，
# 也让 get_ocr(format="page") 有东西可读。
PAGE_CHARS = 800


def paginate(text: str, size: int = PAGE_CHARS) -> list[str]:
    """把正文切成定长页，页码从 1 开始（与上游一致）。"""
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


def page_of(offset: int, size: int = PAGE_CHARS) -> int:
    return offset // size + 1


def build_structure(text: str, title: str, structured: bool = True) -> list[dict]:
    """用与检索层同款的正则切出 章 → 条 两层；切不出来就退化成逐页节点。

    节点保留 `start_char/end_char`（字符偏移），读正文时按它切片，索引里
    因此不必复制一遍正文；对外的 `start_index/end_index` 是页码，与上游同义。
    """
    nodes: list[dict] = []
    chapter: dict | None = None
    current: dict | None = None
    offset = 0
    for raw in text.splitlines(keepends=True):
        line = raw.strip()
        start, offset = offset, offset + len(raw)
        if not line:
            continue
        end = start + len(raw.rstrip("\r\n"))
        match = CHAPTER.match(line)
        if match:
            chapter = {"title": line, "start_char": start, "end_char": end,
                       "nodes": []}
            nodes.append(chapter)
            current = None
            continue
        article = article_name(line)
        if article:
            current = {"title": line[:28], "start_char": start, "end_char": end,
                       "article": article}
            (chapter["nodes"] if chapter else nodes).append(current)
            continue
        # 续行属于上一条（或当前章）
        target = current or chapter
        if target is not None:
            target["end_char"] = max(target["end_char"], end)

    if not nodes or not structured:
        # 切不出层级的短页：按页成节点，标题取该页首行（上游的 pages 退化同理）
        # structured=False 用于表格型目录：它的"第X条"是引用不是条文
        nodes = []
        for index, page in enumerate(paginate(text), 1):
            head = next((line.strip() for line in page.splitlines()
                         if line.strip()), "")
            nodes.append({"title": head[:28] or f"第 {index} 页",
                          "start_char": (index - 1) * PAGE_CHARS,
                          "end_char": min(index * PAGE_CHARS, len(text)),
                          "nodes": []})

    def finish(items: list[dict], depth: int = 0) -> list[dict]:
        out = []
        for node in items:
            start, end = node["start_char"], max(node["end_char"], node["start_char"])
            clean = {
                "title": node["title"],
                "node_id": "",
                "start_index": page_of(start),
                "end_index": page_of(max(end - 1, start)),
                "start_char": start,
                "end_char": end,
            }
            if node.get("article"):
                clean["article"] = node["article"]
            children = node.get("nodes") or []
            if children:
                clean["nodes"] = finish(children, depth + 1)
            out.append(clean)
        return out

    structure = finish(nodes)
    structure = prune_toc(structure)
    counter = 0

    def number(items: list[dict]) -> None:
        nonlocal counter
        for node in items:
            node["node_id"] = f"{counter:04d}"
            counter += 1
            if node.get("nodes"):
                number(node["nodes"])

    number(structure)
    return structure


def prune_toc(items: list[dict]) -> list[dict]:
    """删掉目录行造出来的空章节点。

    法规正文前常有一份目录（"第一章 总则 / 第二章 …"），它会把同名章节点
    造两遍：目录里那个没有条文，正文里那个有。同名章中，空的那些是目录。
    """
    normalized = [re.sub(r"\s+", "", node["title"]) for node in items]
    counts: dict[str, int] = {}
    for name in normalized:
        counts[name] = counts.get(name, 0) + 1
    has_children = {name for name, node in zip(normalized, items)
                    if node.get("nodes")}
    out = []
    for name, node in zip(normalized, items):
        if counts[name] > 1 and not node.get("nodes") and name in has_children:
            continue
        if node.get("nodes"):
            node["nodes"] = prune_toc(node["nodes"])
        out.append(node)
    return out


def build_record(path: str, layer: ForceLayer) -> dict:
    """一份文档的树 + 每个节点上的效力标注。"""
    text = open(path, encoding="utf-8", errors="ignore").read()
    title = os.path.splitext(os.path.basename(path))[0]
    # 文号要带上：废止依据里有"按文号点名"这一类，漏掉它判定会比检索层松
    status = layer.status_for(title, own_numbers(text), text[:400])
    parsed = retrieval.parse_articles(path, {})
    structure = build_structure(
        text, title, structured=retrieval.looks_like_articles(parsed))
    annotated = layer.annotate(
        structure, title, status, file=os.path.basename(path))
    nodes = count_nodes(annotated)
    return {
        "title": title,
        "file": os.path.basename(path),
        "path": path,
        "doc_number": parsed[0]["doc_number"] if parsed else None,
        "chars": len(text),
        "pages": len(paginate(text)),
        "nodes": nodes,
        "articles": (sum(1 for _ in walk_articles(annotated))
                     if retrieval.looks_like_articles(parsed) else 0),
        "status": status,
        "structure": annotated,
    }


def walk(nodes: list[dict]):
    for node in nodes or []:
        yield node
        yield from walk(node.get("nodes") or [])


def walk_articles(nodes: list[dict]):
    for node in walk(nodes):
        if node.get("article"):
            yield node


def count_nodes(nodes: list[dict]) -> int:
    return sum(1 for _ in walk(nodes))


def build_store(database: str, out_path: str | None = None,
                limit: int = 0) -> dict:
    layer = ForceLayer(database)
    paths = docstore.corpus_files(database) + docstore.library_files(database)
    if limit:
        paths = paths[:limit]
    docs: dict[str, dict] = {}
    for index, path in enumerate(paths, 1):
        record = build_record(path, layer)
        # 同一份文件的两个副本（试行／正式）按内容各自的树都留着，
        # 用文件名区分；取用时由检索层决定代表版本。
        docs.setdefault(record["file"], record)
        if index % 100 == 0:
            print(f"  {index}/{len(paths)}", file=sys.stderr)
    store = {"version": 1, "page_chars": PAGE_CHARS,
             "database": database, "docs": docs}
    target = out_path or os.path.join(database, STORE_NAME)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(store, handle, ensure_ascii=False)
    os.replace(temp, target)
    return store


class LocalTreeStore:
    """以本地树索引充当 PageIndex 客户端的数据层。

    只用三个方法：`get_document`（元数据）、`get_tree`（带效力的目录）、
    `get_ocr`（按页／按节点读正文）。`ForceAwareClient` 包住它就能在真实
    语料上跑，页面级 force 也由它提供。
    """

    def __init__(self, database: str = DEFAULT_DB, store_path: str | None = None):
        self.database = database
        self.store_path = store_path or os.path.join(database, STORE_NAME)
        with open(self.store_path, encoding="utf-8") as handle:
            self.store = json.load(handle)
        for record in self.store.get("docs", {}).values():
            record["path"] = resolve_corpus_path(record.get("path", ""))
        self.page_chars = self.store.get("page_chars", PAGE_CHARS)

    # ── 索引侧 ──
    def documents(self) -> list[dict]:
        return list(self.store["docs"].values())

    def find(self, needle: str) -> dict | None:
        for record in self.documents():
            if needle == record["title"] or needle in record["title"]:
                return record
        return None

    def by_file(self, file_name: str) -> dict | None:
        """按文件名精确取（召回层给的是文件名，用 find 会匹配不上带 .txt 的）"""
        return self.store["docs"].get(file_name)

    def text_of(self, record: dict) -> str:
        return open(record["path"], encoding="utf-8", errors="ignore").read()

    # ── PageIndex 客户端的三个接缝 ──
    def get_document(self, doc_id: str) -> dict:
        record = self._require(doc_id)
        return {"id": record["file"], "name": record["title"],
                "status": "completed", "node_count": record["nodes"],
                "page_count": record["pages"], "force": record["status"]}

    def get_tree(self, doc_id: str, node_summary: bool = False,
                 include_text: bool = True) -> dict:
        record = self._require(doc_id)
        text = self.text_of(record) if include_text else ""
        result = [self._wire(node, text, node_summary)
                  for node in record["structure"]]
        return {"doc_id": record["file"], "status": "completed",
                "retrieval_ready": True, "result": result,
                "metadata": None, "features": {},
                "force": record["status"]}

    def get_ocr(self, doc_id: str, format: str = "page") -> dict:
        record = self._require(doc_id)
        text = self.text_of(record)
        if format == "page":
            result = [{"page": index, "text": page,
                       "force": record["status"]}
                      for index, page in enumerate(paginate(text, self.page_chars), 1)]
        elif format == "node":
            result = []

            def walk_nodes(nodes, level):
                for node in nodes:
                    result.append({
                        "title": node["title"],
                        "node_id": node["node_id"],
                        "level": level,
                        "page_index": node["start_index"],
                        "text": text[node["start_char"]:node["end_char"]],
                        "force": node.get("force"),
                    })
                    walk_nodes(node.get("nodes") or [], level + 1)

            walk_nodes(record["structure"], 1)
        elif format == "raw":
            result = text
        else:
            raise ValueError("format 只能是 'page'、'node' 或 'raw'")
        return {"doc_id": record["file"], "status": "completed",
                "retrieval_ready": True, "result": result,
                "metadata": None, "features": {},
                "force": record["status"]}

    def raw_tree(self, doc_id: str) -> list[dict]:
        return self._require(doc_id)["structure"]

    # ── 内部 ──
    def _require(self, doc_id: str) -> dict:
        if doc_id in self.store["docs"]:
            return self.store["docs"][doc_id]
        record = self.find(doc_id)
        if record is None:
            raise KeyError(f"索引里没有这份文档：{doc_id}")
        return record

    def _wire(self, node: dict, text: str, node_summary: bool) -> dict:
        """与上游 `_format_tree_node` 同形，外加 force。"""
        out = {"title": node["title"], "node_id": node["node_id"],
               "page_index": node["start_index"], "force": node.get("force")}
        if node.get("article"):
            out["article"] = node["article"]
        if text:
            out["text"] = text[node["start_char"]:node["end_char"]]
        children = node.get("nodes") or []
        if children:
            out["nodes"] = [self._wire(child, text, node_summary)
                            for child in children]
        return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--build", action="store_true", help="重建树索引")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--doc", default="", help="打印某份文档的带效力目录")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.build:
        store = build_store(args.database, limit=args.limit)
        print(f"已建树 {len(store['docs'])} 份 → "
              f"{os.path.join(args.database, STORE_NAME)}")
        return

    store = LocalTreeStore(args.database)
    if args.doc:
        record = store.find(args.doc)
        if record is None:
            print("索引里没有这份文档")
            return
        print(f"《{record['title']}》 效力：{record['status']['status']}"
              f"　{record['nodes']} 节点／{record['pages']} 页"
              f"／{record['articles']} 条")

        def show(nodes, depth=0):
            for node in nodes:
                mark = f"[{node['force']['status']}] "
                print(f"{'  ' * depth}{mark}{node['title'][:52]}"
                      f"　p{node['start_index']}-{node['end_index']}")
                show(node.get("nodes") or [], depth + 1)

        show(record["structure"])
        return

    docs = store.documents()
    structured = [d for d in docs if d["articles"]]
    print(f"文档 {len(docs)} 份；有章/条结构的 {len(structured)} 份；"
          f"节点 {sum(d['nodes'] for d in docs)}，条文 {sum(d['articles'] for d in docs)}")
    print(f"页单位 {store.page_chars} 字；总页数 {sum(d['pages'] for d in docs)}")
    from collections import Counter
    print("效力分布：", dict(Counter(d["status"]["status"] for d in docs)))


if __name__ == "__main__":
    main()
