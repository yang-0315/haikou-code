"""Compare the chinastruct layer against the untouched PageIndex baseline."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\chinastruct")

from build_tree import build            # noqa: E402

DATASET = r"C:\Users\Lenovo\Desktop\dataset"
OUT = r"C:\Users\Lenovo\Desktop\PageIndex\baseline\reports"

LEAD_ENUM = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千\d]+[章节条部分篇]"
    r"|[一二三四五六七八九十百]+[、.．)）]"
    r"|[（(][一二三四五六七八九十\d]+[)）]"
    r"|\d+(?:\.\d+)*[、.．]?)\s*")
INNER_PUNCT = re.compile(r"[。；;]")
MID_COMMA = re.compile(r"[，,].{2,}")
TAIL_WORD = re.compile(r"[的了和或及在与为是被到把对从等]$")


def is_fragment(title: str) -> bool:
    rest = LEAD_ENUM.sub("", (title or "").strip())
    if INNER_PUNCT.search(rest) or MID_COMMA.search(rest):
        return True
    if len(rest) > 26:
        return True
    if len(rest) >= 4 and TAIL_WORD.search(rest):
        return True
    return False


def walk(tree):
    for node in tree or []:
        yield node
        yield from walk(node.get("nodes"))


def metrics(structure, pages):
    nodes = list(walk(structure))
    titles = [n.get("title", "") or "" for n in nodes]
    covered = set()
    for node in nodes:
        start, end = node.get("start_index"), node.get("end_index")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            covered.update(range(start, end + 1))
    depth = 0

    def d(nodes, level=1):
        nonlocal depth
        for node in nodes or []:
            depth = max(depth, level)
            d(node.get("nodes"), level + 1)

    d(structure)
    frag = sum(1 for t in titles if is_fragment(t))
    dup = sum(1 for t, c in Counter(titles).items() if c > 1 and t.strip())
    return {
        "nodes": len(nodes),
        "depth": depth,
        "covered": len(covered),
        "gaps": len(set(range(1, pages + 1)) - covered) if pages else 0,
        "fragments": frag,
        "fragment_ratio": round(frag / len(nodes), 3) if nodes else 0,
        "duplicates": dup,
        "density": round(len(nodes) / pages * 100, 1) if pages else 0,
    }


def main():
    base = {}
    base_path = os.path.join(OUT, "baseline_full.json")
    if os.path.exists(base_path):
        for row in json.load(open(base_path, encoding="utf-8")):
            base[row["file"]] = row

    rows = []
    for theme in sorted(os.listdir(DATASET)):
        sub = os.path.join(DATASET, theme)
        if not os.path.isdir(sub) or theme.startswith("_"):
            continue
        for fname in sorted(os.listdir(sub)):
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(sub, fname)
            started = time.time()
            try:
                result = build(path)
                stats = metrics(result["structure"], result["pages"])
                row = {"theme": theme, "file": fname, "pages": result["pages"],
                       "style": result["style"], **stats,
                       "seconds": round(time.time() - started, 1)}
            except Exception as exc:
                row = {"theme": theme, "file": fname, "error":
                       f"{type(exc).__name__}: {exc}"[:150],
                       "seconds": round(time.time() - started, 1)}
            rows.append(row)
            old = base.get(fname, {})
            print(f"[{row.get('nodes', 0):>5}] 原{old.get('nodes', '-'):>5} "
                  f"残句{row.get('fragments', 0):>4}/原{old.get('fragments', '-'):>4} "
                  f"深度{row.get('depth', 0)}/{old.get('max_depth', '-')} "
                  f"| {fname[:36]}", flush=True)

    with open(os.path.join(OUT, "chinastruct_eval.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)

    ok = [r for r in rows if not r.get("error")]
    old_nodes = sum(base.get(r["file"], {}).get("nodes", 0) for r in ok)
    new_nodes = sum(r["nodes"] for r in ok)
    old_frag = sum(base.get(r["file"], {}).get("fragments", 0) for r in ok)
    new_frag = sum(r["fragments"] for r in ok)
    print(f"\n=== 汇总（{len(ok)} 份）===")
    print(f"节点总数  原版 {old_nodes}  ->  改进 {new_nodes}")
    print(f"残句标题  原版 {old_frag} ({old_frag/max(old_nodes,1):.0%})"
          f"  ->  改进 {new_frag} ({new_frag/max(new_nodes,1):.0%})")
    print(f"覆盖缺口  原版 {sum(base.get(r['file'], {}).get('page_gaps', 0) for r in ok)}"
          f"  ->  改进 {sum(r['gaps'] for r in ok)}")


if __name__ == "__main__":
    main()
