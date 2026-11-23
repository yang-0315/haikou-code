"""Crawl every page of the policy channels and collect repeal-type notices.

Page counts come from the createPageHTML(...) call each listing carries.
Page 1 is the base URL; page N is <base>_N.shtml.

    python crawl_all_channels.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from repeal import fetch

from paths import DEFAULT_DATABASE as DEFAULT_DB
REPEAL_WORDS = ("废止", "失效")

CHANNELS = [
    ("政府规章", "https://www.hainan.gov.cn/hainan/xxyxzfgz/zfgzk.shtml"),
    ("行政规范性文件", "https://www.hainan.gov.cn/hainan/flfgxzgfxwj/fgsjk_ywh.shtml"),
    ("其他政策文件", "https://www.hainan.gov.cn/hainan/zfwj/zcwj_list.shtml"),
    ("地方性法规", "https://www.hainan.gov.cn/hainan/dfxfg/flfg2021_mwh.shtml"),
    ("省委有关文件", "https://www.hainan.gov.cn/hainan/swygwj/list3.shtml"),
    ("省政府文件", "https://www.hainan.gov.cn/hainan/szfwj/index.shtml"),
]

DOC_LINK = re.compile(r'href="([^"]+)"[^>]*>([^<]{4,100})</a>')


def page_count(html: str) -> int:
    match = re.search(r"createPageHTML\('page_div',\s*(\d+)", html)
    return int(match.group(1)) if match else 1


def page_url(base: str, index: int) -> str:
    if index == 1:
        return base
    stem, ext = base.rsplit(".", 1)
    return f"{stem}_{index}.{ext}"


def extract(html: str, url: str, channel: str, page: int) -> list[dict]:
    out = []
    for href, text in DOC_LINK.findall(html):
        full = urllib.parse.urljoin(url, href)
        if not full.endswith(".shtml") or "hainan.gov.cn" not in full:
            continue
        title = re.sub(r"\s+", "", text).lstrip("·")
        if len(title) < 6:
            continue
        out.append({"channel": channel, "page": page,
                    "title": title, "url": full})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    all_docs: list[dict] = []
    seen = set()
    for channel, base in CHANNELS:
        try:
            first = fetch(base)
        except Exception as exc:
            print(f"ERR {channel}: {type(exc).__name__}", flush=True)
            continue
        total_pages = page_count(first)
        print(f"{channel}: 共 {total_pages} 页", flush=True)
        for index in range(1, total_pages + 1):
            url = page_url(base, index)
            try:
                html = first if index == 1 else fetch(url)
            except Exception:
                continue
            found = extract(html, url, channel, index)
            fresh = [d for d in found if d["url"] not in seen]
            for doc in fresh:
                seen.add(doc["url"])
            all_docs.extend(fresh)
            if index % 5 == 0 or index == total_pages:
                print(f"   {channel} {index}/{total_pages} 页，"
                      f"累计 {len(all_docs)} 条", flush=True)
            time.sleep(0.4)

    repeal = [d for d in all_docs
              if any(word in d["title"] for word in REPEAL_WORDS)]
    print(f"\n共收集 {len(all_docs)} 份文件，其中废止类 {len(repeal)} 份")

    for path, rows in (("_all_documents.json", all_docs),
                       ("_repeal_notices.json", repeal)):
        full = os.path.join(args.database, path)
        with open(full, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        print(f"  → {full}")

    print("\n废止类文件清单：")
    for item in repeal:
        print(f"  [{item['channel']}] {item['title'][:52]}")


if __name__ == "__main__":
    main()
