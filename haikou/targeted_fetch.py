"""Fetch only the documents that could resolve a questionable force status.

Two strategies, both driven by the 1872-title catalogue:
  1. every unfetched document whose title says 废止/清理/修改/修订 — these are
     the instruments that change another document's force;
  2. for each 效力存疑 document, the nearest same-topic titles — a replacement
     usually announces itself in its own 附则.

    python targeted_fetch.py [--database DIR] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
from embedded_repeals import scan_text
from warn import load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DEFAULT_DB
INSTRUMENT = re.compile(r"(废止|清理|失效|修改|修订)")
TOPIC_NOISE = re.compile(
    r"(海南省|海南经济特区|人民政府|办公厅|关于|印发|的通知|的决定|办法|规定|"
    r"实施细则|若干|修订|暂行|试行|等[一二三四五六七八九十百\d]+件)")


def topic_key(title: str) -> str:
    return re.sub(r"[\s《》〈〉（）()、，,。．.\-—]", "", TOPIC_NOISE.sub("", title))


def questionable(database: str) -> list[dict]:
    index = load_index(database)
    out = []
    for path in docstore.library_files(database):
        text = open(path, encoding="utf-8", errors="ignore").read()
        title = os.path.splitext(os.path.basename(path))[0]
        status = status_for({"file": os.path.basename(path), "title": title,
                             "doc_numbers": own_numbers(text),
                             "text": text[:400]}, index)
        if status["status"] == "效力存疑":
            out.append({"title": title, "text": text, "status": status,
                        "path": path})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    metadata = docstore.load_metadata(args.database)
    cached = {os.path.splitext(os.path.basename(p))[0]
              for p in docstore.library_files(args.database)}

    suspects = questionable(args.database)
    print(f"效力存疑 {len(suspects)} 份，为它们寻找废止依据\n")

    wanted: list[dict] = []
    # strategy 1: every unfetched instrument
    for item in metadata:
        if docstore.safe_name(item["title"]) in cached:
            continue
        if INSTRUMENT.search(item["title"]):
            wanted.append(item)
    instruments = len(wanted)

    # strategy 2: same-topic neighbours of each doubtful document
    for suspect in suspects:
        key = topic_key(suspect["title"])
        if len(key) < 2:
            continue
        for item in metadata:
            if docstore.safe_name(item["title"]) in cached:
                continue
            other = topic_key(item["title"])
            if len(other) < 2:
                continue
            shorter, longer = sorted((key, other), key=len)
            if len(shorter) >= 4 and shorter in longer:
                wanted.append(item)

    seen, queue = set(), []
    for item in wanted:
        key = docstore.safe_name(item["title"])
        if key in seen:
            continue
        seen.add(key)
        queue.append(item)
    if args.limit:
        queue = queue[:args.limit]

    print(f"待抓：废止/修订类 {instruments} 份，"
          f"同主题候选 {len(queue) - instruments} 份，合计 {len(queue)}\n")

    fetched, new_clauses = 0, []
    for index_no, item in enumerate(queue, 1):
        text, how = docstore.ensure(args.database, item)
        if how == "fetched":
            fetched += 1
        if text:
            found = scan_text(text)
            if found:
                new_clauses.append((item["title"], found))
        if index_no % 20 == 0:
            print(f"  {index_no}/{len(queue)} 抓取 {fetched} 条条款 "
                  f"{len(new_clauses)}", flush=True)
        time.sleep(0.25)

    print(f"\n抓取完成：新增正文 {fetched} 份，"
          f"其中带废止条款 {len(new_clauses)} 份")
    for title, found in new_clauses[:10]:
        print(f"  {title[:42]} → {found}")


if __name__ == "__main__":
    main()
