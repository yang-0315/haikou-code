"""补抓目录里还缺的文档正文。

目录（`_all_documents.json`）有 1872 条，但其中 **594 条标题重复、15 条是抓取
噪声**（`.&nbsp;省工业和信息化厅` 这种），按标题去重后真正缺的是几百份，不是
"1872 减已抓"。这个工具据实算出待补清单，用与 `refresh_bodies.py` 同一套抽取
（段落 → 容器 → 附件 → Description 兜底），并把结果写进 `_library_fill.json`。

    python fill_library.py --limit 5      # 先试 5 份
    python fill_library.py                # 补完剩下的

抓完要重建索引：`python embedded_repeals.py` 然后 `python tree_index.py --build`。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
from repeal import (MIN_BODY_CHARS, attachment_links, attachment_text,
                    body_text_with_source, fetch, meta_fields)

from paths import DEFAULT_DATABASE as DEFAULT_DB
REPORT = "_library_fill.json"
JUNK_TITLE = re.compile(r"&nbsp;|&ldquo;|&rdquo;|^[.\s]")
# 栏目列表页（list.shtml／list3.shtml／nlist3.shtml）不是文档，标题就是厅名
LISTING_URL = re.compile(r"(?:list|nlist)\d*\.s?html?$|/$", re.I)
MIN_SAVE = 120          # 存盘门槛；低于此的记进报告，不写文件


def missing_documents(database: str, channel: str = "") -> tuple[list[dict], dict]:
    catalog = json.load(open(os.path.join(database, "_all_documents.json"),
                             encoding="utf-8"))
    have = set()
    for folder, _, files in os.walk(os.path.join(database, "library")):
        for name in files:
            if name.endswith(".txt"):
                have.add(os.path.splitext(name)[0])
    seen, missing, stats = set(), [], {"目录条数": len(catalog),
                                       "标题重复": 0, "标题噪声": 0,
                                       "栏目列表页": 0}
    for item in catalog:
        title = item["title"]
        if title in seen:
            stats["标题重复"] += 1
            continue
        seen.add(title)
        if JUNK_TITLE.search(title):
            stats["标题噪声"] += 1
            continue
        if LISTING_URL.search(item.get("url", "")):
            stats["栏目列表页"] += 1
            continue
        if docstore.safe_name(title) in have:
            continue
        if channel and item.get("channel") != channel:
            continue
        missing.append(item)
    return missing, stats


def fetch_one(item: dict, database: str) -> dict:
    """抓一份：页面文本不够就跟附件。返回报告条目。"""
    record = {"title": item["title"], "url": item["url"],
              "channel": item.get("channel")}
    try:
        html = fetch(item["url"], timeout=25, retries=2)
    except Exception as exc:                          # noqa: BLE001
        record["error"] = f"抓取失败 {type(exc).__name__}"
        return record
    text, source = body_text_with_source(html, meta_fields(html))
    if len("".join(text.split())) < MIN_BODY_CHARS:
        for link in attachment_links(html, item["url"])[:2]:
            try:
                attached, kind = attachment_text(link)
            except Exception as exc:                  # noqa: BLE001
                record["attachment_error"] = type(exc).__name__
                continue
            if len("".join(attached.split())) > len("".join(text.split())):
                text, source = attached, kind
    dense = len("".join(text.split()))
    record.update({"chars": len(text), "dense_chars": dense,
                   "extraction": source})
    if dense < MIN_SAVE:
        record["error"] = f"正文过短 {dense} 字"
        return record
    folder = os.path.join(database, "library", item.get("channel", "其他"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, docstore.safe_name(item["title"]) + ".txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    record["file"] = os.path.relpath(path, database)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--channel", default="")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    missing, stats = missing_documents(args.database, args.channel)
    print(f"目录 {stats['目录条数']} 条｜标题重复 {stats['标题重复']}"
          f"｜标题噪声 {stats['标题噪声']}｜**待补 {len(missing)} 份**")
    if args.limit:
        missing = missing[:args.limit]
    if not missing:
        return

    report, saved, short, failed = [], 0, 0, 0
    report_path = os.path.join(args.database, REPORT)
    for index, item in enumerate(missing, 1):
        try:
            record = fetch_one(item, args.database)
        except Exception as exc:                      # noqa: BLE001
            # 单份出意外不能让整轮白跑：记下来继续
            record = {"title": item["title"], "url": item["url"],
                      "channel": item.get("channel"),
                      "error": f"未预期异常 {type(exc).__name__}"}
        report.append(record)
        if record.get("file"):
            saved += 1
        elif "过短" in str(record.get("error")):
            short += 1
        else:
            failed += 1
        if index % 25 == 0 or index == len(missing):
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=1)
            print(f"  {index}/{len(missing)}　已存 {saved}"
                  f"　过短 {short}　失败 {failed}", flush=True)
        time.sleep(args.delay)

    from collections import Counter
    sources = Counter(r.get("extraction") for r in report if r.get("file"))
    print(f"\n=== 完成 ===\n  新增正文 {saved}　过短 {short}　失败 {failed}")
    print(f"  抽取来源：{dict(sources)}")
    print(f"  报告 → {report_path}")


if __name__ == "__main__":
    main()
