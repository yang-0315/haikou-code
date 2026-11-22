"""One-pass baseline: parse every dataset PDF once and record all metrics.

Writes per-document trees to reports/trees/ and a combined metrics file to
reports/baseline_full.json, printing progress line by line so a background
run can be watched from its log.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from collections import Counter

REPO = r"C:\Users\Lenovo\Desktop\PageIndex\PageIndex"
DATASET = r"C:\Users\Lenovo\Desktop\dataset"
OUT = r"C:\Users\Lenovo\Desktop\PageIndex\baseline\reports"
TREES = os.path.join(OUT, "trees")

sys.path.insert(0, REPO)

import pypdfium2 as pdfium                             # noqa: E402
from pageindex.flash import page_index_flash           # noqa: E402
from pageindex.flash.api import flash_rejection_reason  # noqa: E402

LEAD_ENUM = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千\d]+[章节条部分篇]"
    r"|[一二三四五六七八九十百]+[、.．)）]"
    r"|[（(][一二三四五六七八九十\d]+[)）]"
    r"|\d+(?:\.\d+)*[、.．]?)\s*")
INNER_PUNCT = re.compile(r"[。；;]")
MID_COMMA = re.compile(r"[，,].{2,}")
TAIL_WORD = re.compile(r"[的了和或及在与为是被到把对从等]$")
LONG_TITLE = 26


def walk(tree, depth=1):
    for node in tree or []:
        yield node, depth
        for child in node.get("nodes") or []:
            yield from walk([child], depth + 1)


def is_fragment(title):
    rest = LEAD_ENUM.sub("", (title or "").strip())
    reasons = []
    if INNER_PUNCT.search(rest) or MID_COMMA.search(rest):
        reasons.append("句读")
    if len(rest) > LONG_TITLE:
        reasons.append("过长")
    if len(rest) >= 4 and TAIL_WORD.search(rest):
        reasons.append("断尾")
    if len(rest) > 14 and re.search(r"(推荐|应给予|治疗\s*\d|患者\s*\d)", rest):
        reasons.append("正文措辞")
    return reasons


def analyse(result, page_count):
    structure = result.get("structure") or []
    nodes = list(walk(structure))
    titles = [n.get("title", "") or "" for n, _ in nodes]

    covered = set()
    for node, _ in nodes:
        start, end = node.get("start_index"), node.get("end_index")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            covered.update(range(start, end + 1))
    gaps = sorted(set(range(1, page_count + 1)) - covered)

    fragments, samples = 0, []
    for title in titles:
        reasons = is_fragment(title)
        if reasons:
            fragments += 1
            if len(samples) < 3:
                samples.append({"title": title[:60], "why": reasons})

    dup = [t for t, c in Counter(titles).items() if c > 1 and t.strip()]
    return {
        "nodes": len(nodes),
        "top_level": len(structure),
        "max_depth": max((d for _, d in nodes), default=0),
        "pages_covered": len(covered),
        "page_gaps": len(gaps),
        "first_gaps": gaps[:8],
        "dup_titles": len(dup),
        "dup_samples": dup[:3],
        "fragments": fragments,
        "fragment_ratio": round(fragments / len(nodes), 3) if nodes else 0,
        "fragment_samples": samples,
        "density": round(len(nodes) / page_count * 100, 1) if page_count else 0,
    }


def main():
    os.makedirs(TREES, exist_ok=True)
    targets = []
    for theme in sorted(os.listdir(DATASET)):
        sub = os.path.join(DATASET, theme)
        if not os.path.isdir(sub) or theme.startswith("_"):
            continue
        for fname in sorted(os.listdir(sub)):
            if fname.lower().endswith(".pdf"):
                targets.append((theme, fname, os.path.join(sub, fname)))

    print(f"documents: {len(targets)}", flush=True)
    rows = []
    for index, (theme, fname, path) in enumerate(targets, 1):
        started = time.time()
        row = {"theme": theme, "file": fname,
               "mb": round(os.path.getsize(path) / 1048576, 2)}
        try:
            doc = pdfium.PdfDocument(path)
            page_count = len(doc)
            doc.close()
            result = page_index_flash(path, summary=False, optimize=False,
                                      use_embedded_toc=True)
            row.update(analyse(result, page_count))
            row["pages"] = page_count
            row["toc_source"] = result.get("toc_source")
            row["doc_title"] = (result.get("doc_title") or "")[:70]
            reason = flash_rejection_reason(result)
            row["rejected"] = bool(reason)
            row["reject_reason"] = (reason or "")[:90]
            cache = os.path.join(TREES, re.sub(r"[^\w\u4e00-\u9fff.-]", "_",
                                               fname.replace(".pdf", "")) + ".json")
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"doc_title": result.get("doc_title"),
                           "toc_source": result.get("toc_source"),
                           "pages": page_count,
                           "structure": result.get("structure")},
                          fh, ensure_ascii=False)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:200]
            row["toc_source"] = "-"
        row["seconds"] = round(time.time() - started, 1)
        rows.append(row)
        print(f"[{index:>2}/{len(targets)}] {row.get('toc_source', '-'):>9} "
              f"{row['seconds']:>6.1f}s 节点{row.get('nodes', 0):>5} "
              f"残句{row.get('fragments', 0):>4} | {fname}", flush=True)
        with open(os.path.join(OUT, "baseline_full.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)

    total_nodes = sum(r.get("nodes", 0) for r in rows)
    total_frag = sum(r.get("fragments", 0) for r in rows)
    print("\n=== DONE ===", flush=True)
    print(f"成功 {sum(1 for r in rows if not r.get('error'))}/{len(rows)}", flush=True)
    print("toc_source:", dict(Counter(r.get("toc_source") for r in rows)), flush=True)
    print(f"拒绝 {sum(1 for r in rows if r.get('rejected'))} 份", flush=True)
    print(f"总时长 {sum(r['seconds'] for r in rows):.0f}s，节点 {total_nodes}，"
          f"疑似残句 {total_frag} ({total_frag / max(total_nodes, 1):.0%})", flush=True)


if __name__ == "__main__":
    main()
