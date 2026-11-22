"""Build a risk-stratified spot-check list for manual verification.

Sampling deliberately over-weights the cases where the automation is weakest:
the one document flagged as repealed, the documents with no 文号, and the
oldest documents, which are the ones a clean-up decision is most likely to
have taken out.

    python spotcheck.py [--database DIR] [--size 20]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warn import load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DEFAULT_DB
YEAR = re.compile(r"(19\d{2}|20\d{2})\s*年")


def gather(database: str) -> list[dict]:
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
            years = YEAR.findall(text[:400])
            doc = {
                "title": title,
                "file": os.path.relpath(path, database),
                "channel": os.path.basename(folder),
                "doc_numbers": own_numbers(text),
                "text": text[:400],
                "year": int(years[0]) if years else None,
            }
            doc["status"] = status_for(doc, index)
            rows.append(doc)
    return rows, index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--size", type=int, default=20)
    args = parser.parse_args()

    rows, index = gather(args.database)

    # cross-reference: does any other document in the library mention this one
    # near a repeal word? That catches repeals stated somewhere we did not index.
    corpus = {}
    root = os.path.join(args.database, "library")
    for folder, _, files in os.walk(root):
        for name in files:
            if name.endswith(".txt"):
                path = os.path.join(folder, name)
                corpus[os.path.splitext(name)[0]] = re.sub(
                    r"\s+", "", open(path, encoding="utf-8",
                                    errors="ignore").read())

    def cross_reference(row: dict) -> list[str]:
        hits = []
        keys = [row["title"]] + row["doc_numbers"]
        for other_title, text in corpus.items():
            if other_title == row["title"]:
                continue
            for key in keys:
                if len(key) < 6 or key not in text:
                    continue
                at = text.find(key)
                window = text[max(0, at - 60):at + 60]
                if any(word in window for word in ("废止", "失效", "替代")):
                    hits.append(f"{other_title[:34]}（{window[:46]}…）")
                    break
        return hits[:3]
    repealed = [r for r in rows if r["status"]["status"] != "现行有效"]
    no_number = [r for r in rows if not r["doc_numbers"]]
    dated = [r for r in rows if r["year"]]
    oldest = sorted(dated, key=lambda r: r["year"])[:8]
    newest = sorted(dated, key=lambda r: -r["year"])[:8]

    picked, seen = [], set()
    for group in (repealed, no_number, oldest, newest):
        for row in group:
            if row["title"] in seen:
                continue
            seen.add(row["title"])
            row = dict(row)
            row["why"] = ("系统标为已废止" if row in repealed else
                          "无文号，证据最弱" if row in no_number else
                          "年代最早，最可能被清理" if row in oldest else
                          "年代最近，最可能现行")
            row["cross"] = cross_reference(row)
            picked.append(row)

    lines = ["# 效力判定抽检清单", "",
             f"库内共 {len(rows)} 份，本次抽检 {len(picked)} 份。"
             "抽样刻意偏向自动化最薄弱的环节。", "",
             "| # | 文件 | 栏目 | 文号 | 年代 | 系统判定 | 抽检原因 |",
             "|---|---|---|---|---|---|---|"]
    for index_no, row in enumerate(picked[:args.size], 1):
        number = row["doc_numbers"][0] if row["doc_numbers"] else "**无**"
        lines.append(f"| {index_no} | {row['title'][:34]} | {row['channel']} | "
                     f"{number} | {row['year'] or '?'} | "
                     f"{row['status']['status']} | {row['why']} |")

    lines += ["", "## 逐份核对要点", ""]
    for index_no, row in enumerate(picked[:args.size], 1):
        status = row["status"]
        evidence = status["evidence"]
        lines.append(f"### {index_no}. {row['title']}")
        lines.append("")
        lines.append(f"- 栏目：{row['channel']}　文号："
                     f"{row['doc_numbers'][0] if row['doc_numbers'] else '未标注'}"
                     f"　年代：{row['year'] or '未知'}")
        lines.append(f"- 系统判定：**{status['status']}**"
                     f"（置信度 {status['confidence']}）")
        if evidence.get("matched_by"):
            lines.append(f"- 判定依据：{evidence['matched_by']}")
        if evidence.get("repealed_by"):
            lines.append(f"- 由谁废止：{evidence['repealed_by']}")
        if evidence.get("effective_date"):
            lines.append(f"- 生效日期：{evidence['effective_date']}")
        for caveat in status["caveats"]:
            lines.append(f"- 系统提示：{caveat}")
        if row.get("cross"):
            lines.append("- 库内交叉线索（别处提到它的上下文）：")
            for hit in row["cross"]:
                lines.append(f"    - {hit}")
        lines.append(f"- **请核对**：这份文件现在是否仍然有效？（"
                     + ("系统说已废止，核对废止依据是否成立"
                        if status["status"] != "现行有效"
                        else "系统说未见废止，查一下有没有后续文件把它替代或废止了")
                     + "）")
        lines.append("")

    path = os.path.join(args.database, "_spotcheck.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"抽检 {len(picked[:args.size])} 份，构成：")
    print(f"  已废止 {sum(1 for r in picked[:args.size] if r['why'] == '系统标为已废止')}")
    print(f"  无文号 {sum(1 for r in picked[:args.size] if r['why'] == '无文号，证据最弱')}")
    print(f"  最老   {sum(1 for r in picked[:args.size] if r['why'] == '年代最早，最可能被清理')}")
    print(f"  最新   {sum(1 for r in picked[:args.size] if r['why'] == '年代最近，最可能现行')}")
    print(f"\n清单 → {path}\n")
    for index_no, row in enumerate(picked[:args.size], 1):
        number = row["doc_numbers"][0] if row["doc_numbers"] else "无文号"
        mark = "!" if row.get("cross") else " "
        print(f"{index_no:>2}. [{row['status']['status']:<6}] "
              f"{row['year'] or '????'} {mark} {number:<22}{row['title'][:34]}")


if __name__ == "__main__":
    main()
