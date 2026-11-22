"""Fetch the full text of every document found by crawl_all_channels.py.

Writes one .txt per document under <database>/library/<channel>/. Pages whose
body is a fragment (under MIN_CHARS) are recorded but not saved, so the
library only holds documents usable for retrieval and clause scanning.
Resumable: existing files are skipped.

    python fetch_library.py [--database DIR] [--limit N] [--delay 0.4]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

from repeal import body_text, fetch, meta_fields

from paths import DEFAULT_DATABASE as DEFAULT_DB
MIN_CHARS = 120

# 规章与地方性法规最先抓：它们才是"谁废止谁""哪个版本替代哪个"的证据来源
CHANNEL_ORDER = ["政府规章", "地方性法规", "行政规范性文件", "其他政策文件",
                 "省委有关文件", "省政府文件"]


def safe_name(title: str, limit: int = 70) -> str:
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", title).strip("_")
    return name[:limit] or "untitled"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    manifest_path = os.path.join(args.database, "_all_documents.json")
    documents = json.load(open(manifest_path, encoding="utf-8"))
    documents.sort(key=lambda item: CHANNEL_ORDER.index(item["channel"])
                   if item["channel"] in CHANNEL_ORDER else 99)
    if args.limit:
        documents = documents[:args.limit]

    library = os.path.join(args.database, "library")
    report, skipped, done = [], 0, 0
    print(f"待抓取 {len(documents)} 份\n", flush=True)

    for index, item in enumerate(documents, 1):
        folder = os.path.join(library, item["channel"])
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, safe_name(item["title"]) + ".txt")
        if os.path.exists(path):
            done += 1
            continue
        try:
            html = fetch(item["url"], timeout=25, retries=2)
        except Exception as exc:
            report.append({"title": item["title"], "url": item["url"],
                           "error": type(exc).__name__})
            continue
        meta = meta_fields(html)
        text = body_text(html, meta)
        if len(text) < MIN_CHARS:
            skipped += 1
            report.append({"title": item["title"], "url": item["url"],
                           "error": f"正文过短 {len(text)} 字"})
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            report.append({"title": item["title"], "url": item["url"],
                           "channel": item["channel"], "chars": len(text),
                           "publish_date": (meta.get("PubDate") or "")[:10],
                           "publisher": meta.get("ContentSource"),
                           "file": os.path.relpath(path, args.database)})
        if index % 50 == 0:
            print(f"  {index}/{len(documents)}  已存 {done + index - skipped}  "
                  f"跳过 {skipped}", flush=True)
            with open(os.path.join(args.database, "_library_manifest.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        time.sleep(args.delay)

    with open(os.path.join(args.database, "_library_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    saved = sum(1 for r in report if r.get("chars"))
    failed = sum(1 for r in report if r.get("error") and "正文过短" not in r["error"])
    print(f"\n=== 完成 ===")
    print(f"  已存正文 {saved}")
    print(f"  正文过短跳过 {skipped}")
    print(f"  抓取失败 {failed}")
    print(f"  库目录 {library}")


if __name__ == "__main__":
    main()
