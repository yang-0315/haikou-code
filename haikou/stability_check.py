"""Is the force rule stable as the corpus grows?

Three questions:
  1. determinism - same input, same verdict, every run
  2. monotonicity - more evidence may turn 现行有效 into 已废止, never back
  3. convergence - does the 存疑 count fall as documents arrive?

The annotated spot-check records the verdicts taken when the library held 128
documents; comparing against the current state shows how the rules move.

    python stability_check.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
from warn import IN_FORCE, load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DEFAULT_DB


_INDEX_CACHE: dict = {}


def current_status(database: str, title: str,
                   index: dict | None = None) -> dict | None:
    if index is None:
        if "index" not in _INDEX_CACHE:
            _INDEX_CACHE["index"] = load_index(database)
        index = _INDEX_CACHE["index"]
    for path in docstore.library_files(database):
        if os.path.splitext(os.path.basename(path))[0] == title:
            text = open(path, encoding="utf-8", errors="ignore").read()
            return status_for({"file": os.path.basename(path), "title": title,
                               "doc_numbers": own_numbers(text),
                               "text": text[:400]}, index)
    root = os.path.join(database, title + ".txt")
    if os.path.exists(root):
        text = open(root, encoding="utf-8", errors="ignore").read()
        return status_for({"file": title + ".txt", "title": title,
                           "doc_numbers": own_numbers(text),
                           "text": text[:400]}, index)
    return None


def old_verdicts(database: str) -> list[dict]:
    path = os.path.join(database, "_spotcheck.md")
    if not os.path.exists(path):
        return []
    text = open(path, encoding="utf-8").read()
    out = []
    for block in re.split(r"\n### ", text)[1:]:
        title = re.sub(r"^\d+\.\s*", "", block.split("\n", 1)[0].strip())
        system = re.search(r"系统判定：\*\*(.+?)\*\*", block)
        out.append({"title": title,
                    "old": system.group(1) if system else "?"})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    index = load_index(args.database)
    library = docstore.library_files(args.database)
    print(f"当前语料：目录 {len(docstore.load_metadata(args.database))} 份，"
          f"正文 {len(library)} 份")
    print(f"废止依据：通知 {len(index.get('notices', []))} 份，"
          f"文件内条款 {len(index.get('embedded', []))} 条\n")

    # 1. determinism: same inputs twice
    sample_titles = [os.path.splitext(os.path.basename(p))[0]
                     for p in library[:40]]
    first = {t: current_status(args.database, t) for t in sample_titles}
    second = {t: current_status(args.database, t) for t in sample_titles}
    unstable = [t for t in sample_titles
                if first[t] and second[t]
                and first[t]["status"] != second[t]["status"]]
    print(f"1. 确定性：同一批 {len(sample_titles)} 份跑两遍，"
          f"判定不同的 {len(unstable)} 份"
          + (f" → {unstable[:3]}" if unstable else "（稳定）"))

    # 2. monotonicity against the annotations taken at 128 documents
    old = old_verdicts(args.database)
    flips = []
    for item in old:
        now = current_status(args.database, item["title"])
        if not now:
            continue
        if now["status"] != item["old"]:
            flips.append((item["title"], item["old"], now["status"]))
    print(f"\n2. 单调性：抽检清单 {len(old)} 份里，"
          f"判定发生变化的 {len(flips)} 份")
    for title, before, after in flips:
        arrow = "→"
        flag = ""
        if before == IN_FORCE and after != IN_FORCE:
            flag = "（证据增多，收紧了）"
        elif before != IN_FORCE and after == IN_FORCE:
            flag = "（⚠ 反向放宽，需查）"
        print(f"    {title[:38]:<40}{before}{arrow}{after} {flag}")
    reversals = [f for f in flips
                 if f[1] != IN_FORCE and f[2] == IN_FORCE]

    # 3. convergence: the 存疑 share across the whole library
    counts = {}
    for path in library:
        text = open(path, encoding="utf-8", errors="ignore").read()
        title = os.path.splitext(os.path.basename(path))[0]
        status = status_for({"file": os.path.basename(path), "title": title,
                             "doc_numbers": own_numbers(text),
                             "text": text[:400]}, index)
        counts[status["status"]] = counts.get(status["status"], 0) + 1
    total = sum(counts.values()) or 1
    print(f"\n3. 收敛：{len(library)} 份的判定分布")
    for state, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {state:<8}{count:>5}  ({count/total:.0%})")

    print(f"\n结论：{'规则稳定，无反向放宽' if not reversals else '存在反向放宽'}"
          f"；反向 {len(reversals)} 处")


if __name__ == "__main__":
    main()
