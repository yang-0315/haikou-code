"""Report and remove duplicate documents in the library.

The same document is sometimes published in several channels (a 规章 also
listed under 省政府文件, a 条例 also under 地方性法规). Keep one copy, by
channel priority, and drop the rest so retrieval does not return the same
article twice under two titles.

    python cleanup_library.py [--database DIR] [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repeal import is_boilerplate

from paths import DEFAULT_DATABASE as DEFAULT_DB
PRIORITY = ["政府规章", "地方性法规", "行政规范性文件", "其他政策文件",
            "省委有关文件", "省政府文件"]


def normalise(title: str) -> str:
    clean = re.sub(r"[（(]试行[)）]|[\s_]+", "", title)
    return re.sub(r"[《》〈〉]", "", clean)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = os.path.join(args.database, "library")
    by_title: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for folder, _, files in os.walk(root):
        channel = os.path.basename(folder)
        for name in files:
            if not name.endswith(".txt"):
                continue
            path = os.path.join(folder, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            digest = hashlib.sha1(
                re.sub(r"\s+", "", text)[:2000].encode()).hexdigest()[:12]
            by_title[normalise(os.path.splitext(name)[0])].append(
                (channel, path, digest))

    print(f"库中 {sum(len(v) for v in by_title.values())} 份文件，"
          f"{len(by_title)} 个不同标题")

    by_digest: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for items in by_title.values():
        for channel, path, digest in items:
            by_digest[digest].append((channel, path, digest))
    content_dups = {k: v for k, v in by_digest.items() if len(v) > 1}
    print(f"正文指纹相同的组 {len(content_dups)} 个")
    for _, items in list(content_dups.items())[:6]:
        print("   ", [os.path.basename(p)[:34] for _, p, _ in items])

    dup_groups = {k: v for k, v in by_title.items() if len(v) > 1}
    print(f"\n同名组 {len(dup_groups)} 个\n")

    removals = []
    junk = []
    for folder, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".txt"):
                continue
            path = os.path.join(folder, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            if is_boilerplate(text):
                junk.append(path)
    if junk:
        print(f"\n正文只有网站样板的文件 {len(junk)} 份，应删除：")
        for path in junk[:8]:
            print("    ", os.path.basename(path)[:52])
        removals.extend(junk)
    else:
        print("\n未发现样板文件")
    for title, items in sorted(dup_groups.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda row: (PRIORITY.index(row[0])
                                    if row[0] in PRIORITY else 99))
        keep = items[0]
        drop = items[1:]
        kinds = {d for _, _, d in items}
        mark = "正文相同" if len(kinds) == 1 else "正文不同"
        print(f"◆ {title[:44]}  {len(items)} 份（{mark}）")
        print(f"    保留 [{keep[0]}] {os.path.basename(keep[1])[:44]}")
        for channel, path, digest in drop:
            print(f"    删除 [{channel}] {os.path.basename(path)[:44]}")
            removals.append(path)

    removals = list(dict.fromkeys(removals))
    if args.apply and removals:
        for path in removals:
            os.remove(path)
        print(f"\n已删除 {len(removals)} 份重复文件")
        manifest = os.path.join(args.database, "_library_cleanup.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump({"removed": removals}, fh, ensure_ascii=False, indent=2)
    # content-identical copies under different titles
    if not args.apply:
        for digest, items in content_dups.items():
            items.sort(key=lambda row: (PRIORITY.index(row[0])
                                        if row[0] in PRIORITY else 99))
            for channel, path, _ in items[1:]:
                removals.append(path)
        if content_dups:
            print(f"\n正文相同的不同标题副本 {sum(len(v) - 1 for v in content_dups.values())} 份")
    elif content_dups:
        for digest, items in content_dups.items():
            items.sort(key=lambda row: (PRIORITY.index(row[0])
                                        if row[0] in PRIORITY else 99))
            for channel, path, _ in items[1:]:
                if os.path.exists(path):
                    os.remove(path)
                    removals.append(path)

    if removals and not args.apply:
        print(f"\n（预览模式）将删除 {len(removals)} 份；"
              f"加 --apply 执行")
    elif removals:
        manifest = os.path.join(args.database, "_library_cleanup.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump({"removed": removals}, fh, ensure_ascii=False, indent=2)
        print(f"\n已删除 {len(removals)} 份 → {manifest}")


if __name__ == "__main__":
    main()
