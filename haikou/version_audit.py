"""Audit the version groups: are they complete, correctly ordered, comparable?

Checks each multi-version series against the full 1872-title catalogue for
missing versions, verifies date ordering, and reports whether the texts needed
for an actual comparison are on hand.

    python version_audit.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore

from paths import DEFAULT_DATABASE as DEFAULT_DB
YEAR_MARK = re.compile(r"[（(]?(19\d{2}|20\d{2})\s*年?[^）)]*[)）]?")


def series_key(title: str) -> str:
    """Same normalisation lineage.py uses, kept in one place."""
    clean = re.sub(r"(关于印发|的通知|的决定|（试行）|\(试行\)|修订|暂行)", "",
                   title.replace(" ", ""))
    clean = clean.replace("《", "").replace("》", "")
    return re.sub(r"(海南省人民政府办公厅|海南省人民政府|人民政府)", "", clean)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    path = os.path.join(args.database, "_version_lineage.json")
    if not os.path.exists(path):
        print("先跑 lineage.py 生成谱系")
        return
    series = json.load(open(path, encoding="utf-8"))
    metadata = docstore.load_metadata(args.database)
    cached = {os.path.splitext(os.path.basename(p))[0]
              for p in docstore.library_files(args.database)}
    for name in os.listdir(args.database):
        if name.endswith(".txt"):
            cached.add(os.path.splitext(name)[0])

    multi = [s for s in series if s["summary"]["versions_known"] > 1]
    print(f"共 {len(series)} 个系列，其中多版本 {len(multi)} 个\n")

    problems = []
    for group in multi:
        key = group["series"]
        versions = group["versions"]
        print(f"◆ {key[:46]}　{len(versions)} 个版本")

        # 1. ordering
        dates = [v.get("date") or v.get("repeal_date") or "" for v in versions]
        if dates != sorted(dates):
            problems.append((key, "版本未按时间排序"))
            print(f"    问题：版本顺序 {dates} 不是时间序")

        # 2. texts on hand
        with_text = sum(1 for v in versions if v.get("file"))
        if with_text < 2:
            problems.append((key, f"可直接对比的正文只有 {with_text} 份"))
            print(f"    问题：可直接对比的正文只有 {with_text} 份，"
                  "无法做条文级差异")

        # 3. catalogue entries belonging to this series but not in the group
        known_titles = {v["title"] for v in versions}
        missing = []
        for item in metadata:
            if item["title"] in known_titles:
                continue
            if series_key(item["title"]) != series_key(key):
                continue
            missing.append(item)
        if missing:
            problems.append((key, f"目录中还有 {len(missing)} 个同系列条目未纳入"))
            print(f"    问题：目录里还有 {len(missing)} 个同系列条目未纳入")
            for item in missing[:4]:
                state = "已抓" if docstore.safe_name(item["title"]) in cached \
                    else "未抓"
                print(f"        [{state}] {item['title'][:50]}")

        # 4. two versions sharing a date AND a title are the same document
        seen = set()
        for version in versions:
            fingerprint = (version["title"], version.get("date"),
                           tuple(version.get("doc_numbers") or []))
            if fingerprint in seen:
                problems.append((key, "同一文件重复计入"))
                print(f"    问题：{version['title'][:34]} 重复计入")
            seen.add(fingerprint)

        if with_text >= 2:
            print("    可直接做条文级对比")

        for version in versions:
            mark = "有正文" if version.get("file") else "缺正文"
            date = version.get("date") or version.get("repeal_date") or "?"
            number = (version.get("doc_numbers") or ["未标注"])[0]
            print(f"    [{mark}] {number:<22}{date:<12}"
                  f"{version['title'][:38]}")
        print()

    print(f"== 汇总 ==")
    print(f"  多版本组 {len(multi)} 个，发现问题 {len(problems)} 处")
    kinds = {}
    for _, issue in problems:
        head = issue.split("有")[0] if "有" in issue else issue
        kinds[head] = kinds.get(head, 0) + 1
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"    {count:>2}  {kind}")


if __name__ == "__main__":
    main()
