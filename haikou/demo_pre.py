"""Demo the pre-retrieval side: stamp a PageIndex-shaped tree with force data."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from force_layer import ForceLayer
from retrieval import ARTICLE, CHAPTER, cn_int

from paths import DEFAULT_DATABASE as DB
DOC = "海南自由贸易港建设投资基金管理办法"


def build_tree(text: str) -> list[dict]:
    """章 nodes with 条 children, the shape PageIndex produces."""
    root, chapter, counter = [], None, 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if CHAPTER.match(line):
            chapter = {"title": line, "node_id": f"{counter:04d}",
                       "start_index": 1, "end_index": 1, "nodes": []}
            counter += 1
            root.append(chapter)
            continue
        match = ARTICLE.match(line)
        if match:
            node = {"title": line[:28], "node_id": f"{counter:04d}",
                    "start_index": 1, "end_index": 1}
            counter += 1
            if chapter is None:
                root.append(node)
            else:
                chapter["nodes"].append(node)
    return root


def main():
    path = None
    # the curated corpus sits at the database root; the library is a subfolder
    for folder, _, files in os.walk(DB):
        if os.path.basename(folder).startswith("_"):
            continue
        for name in files:
            if os.path.splitext(name)[0] == DOC:
                path = os.path.join(folder, name)
    if not path:
        print("找不到示例文档")
        return

    text = open(path, encoding="utf-8", errors="ignore").read()
    tree = build_tree(text)
    layer = ForceLayer(DB)
    annotated = layer.annotate(tree, DOC)

    out = os.path.join(DB, "_tree_annotated_demo.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(annotated, fh, ensure_ascii=False, indent=2)

    total = 0

    def walk(nodes, depth=0):
        nonlocal total
        for node in nodes:
            total += 1
            if depth < 2:
                force = node["force"]
                print(f"{'  ' * depth}[{force['status']}] {node['title'][:44]}")
            walk(node.get("nodes") or [], depth + 1)

    print(f"《{DOC}》建树 {len(tree)} 个顶层节点\n")
    walk(annotated)
    print(f"\n已标注 {total} 个节点，每个都带 force 字段 → {out}")
    sample = json.dumps(annotated[0], ensure_ascii=False, indent=2)
    print("\n第一个节点的 force 字段：")
    print("\n".join(sample.splitlines()[:18]))


if __name__ == "__main__":
    main()
