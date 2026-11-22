"""Self-check the force annotation: evidence, misses, and evidence strength.

Three questions:
  1. for every document flagged as repealed, does the evidence chain hold?
  2. among documents called in-force, does a looser match turn up a miss?
  3. how strong is the evidence behind each call (own 文号 vs title only)?

    python selfcheck.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warn import is_instrument, load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DEFAULT_DB


def normalise(title: str) -> str:
    t = re.sub(r"[（(][^）)]*[)）]", "", title)
    t = re.sub(r"(试行|暂行|修订|修正|草案)", "", t)
    return re.sub(r"[\s《》〈〉_、，,。．.\-—]", "", t)


def collect(database: str) -> list[dict]:
    index = load_index(database)
    root = os.path.join(database, "library")
    rows = []
    for folder, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".txt"):
                continue
            path = os.path.join(folder, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            title = os.path.splitext(name)[0]
            doc = {"file": os.path.relpath(path, database), "title": title,
                   "doc_numbers": own_numbers(text),
                   "text": text[:400],
                   "channel": os.path.basename(folder)}
            doc["status"] = status_for(doc, index)
            rows.append(doc)
    return rows, index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    rows, index = collect(args.database)
    repealed = [r for r in rows if r["status"]["status"] != "现行有效"]
    in_force = [r for r in rows if r["status"]["status"] == "现行有效"]
    breakdown = Counter(r["status"]["status"] for r in rows)
    print(f"库文件 {len(rows)} 份：" +
          "，".join(f"{k} {v}" for k, v in breakdown.most_common()) + "\n")

    definite = [r for r in repealed if r["status"]["status"] != "效力存疑"]
    doubtful = [r for r in repealed if r["status"]["status"] == "效力存疑"]

    print("== 1. 已废止的证据链 ==")
    for row in definite:
        ev = row["status"]["evidence"]
        print(f"◆ {row['title'][:44]}")
        print(f"    自身文号: {row['doc_numbers'] or '未标注'}")
        print(f"    判定依据: {ev.get('matched_by') or ev.get('source') or '-'}")
        print(f"    由谁废止: {ev.get('repealed_by') or '-'}")
        print(f"    生效日期: {ev.get('effective_date') or '未识别'}"
              f"   出处: {(ev.get('url') or '-')[:60]}")

    print(f"\n== 2. 效力存疑（年代久远且文头无修订记录）{len(doubtful)} 份 ==")
    for row in doubtful:
        evidence = row["status"]["evidence"]
        print(f"    ? {row['title'][:44]}")
        print(f"        {evidence.get('note', '')}")

    print("\n== 2. 现行有效里的漏报排查（模糊匹配）==")
    repealed_names = []
    for notice in index.get("notices", []) + index.get("embedded", []):
        for name in notice["repealed_documents"]:
            repealed_names.append((normalise(name), name, notice["notice_title"]))
    suspects = []
    for row in in_force:
        if is_instrument(row["title"]):
            continue        # a repeal/amendment decision is not a missed repeal
        key = normalise(row["title"])
        for norm_name, raw_name, notice_title in repealed_names:
            if not norm_name or not key:
                continue
            if norm_name == key:
                continue                    # exact match would already have hit
            shorter, longer = sorted((norm_name, key), key=len)
            if len(shorter) >= 6 and shorter in longer:
                suspects.append((row["title"], raw_name, notice_title))
                break
    if suspects:
        print(f"  疑似漏报 {len(suspects)} 份：")
        for title, raw, notice in suspects[:10]:
            print(f"    ? {title[:40]}")
            print(f"        与「{raw[:40]}」高度相似（依据：{notice[:34]}）")
    else:
        print("  未发现疑似漏报")

    print("\n== 3. 判定的证据强度 ==")
    with_number = [r for r in in_force if r["doc_numbers"]]
    without = [r for r in in_force if not r["doc_numbers"]]
    print(f"  有文号（可被文号检索）：{len(with_number)}")
    print(f"  无文号（仅靠标题检索）：{len(without)}")
    if without:
        print("  无文号的样本：")
        for row in without[:6]:
            print(f"    - {row['title'][:46]}")

    print("\n== 4. 索引覆盖 ==")
    print(f"  废止通知 {len(index.get('notices', []))} 份")
    print(f"  文件内废止条款 {len(index.get('embedded', []))} 条")
    kinds = Counter(r["status"]["confidence"] for r in rows)
    print(f"  置信度分布: {dict(kinds)}")

    report = os.path.join(args.database, "_selfcheck.json")
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"repealed": [{"title": r["title"], "status": r["status"]}
                                for r in repealed],
                   "suspects": suspects,
                   "in_force_without_number": [r["title"] for r in without]},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n报告 → {report}")


if __name__ == "__main__":
    main()
