"""用修好的抽取规则重刷已有正文：段落优先，不再拿 meta 摘要当正文。

旧规则（Description 优先）把新闻导语/公告文头存成了正文，812 份里 603 份是
单段短文本。这个工具按 manifest 里已记录的 URL 重抓，对比新旧，确认变好之后再
覆盖；覆盖前先把原文件镜像备份到 `<database>/_library_backup/`。

    python refresh_bodies.py --limit 20            # 只对比，不写（默认 dry-run）
    python refresh_bodies.py --apply --only-short  # 覆盖 <600 字的那些

重刷完还要重建树索引：`python tree_index.py --build`。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrieval
from repeal import (MIN_BODY_CHARS, attachment_links, attachment_text,
                    body_text_with_source, fetch, meta_fields)

from paths import DEFAULT_DATABASE as DEFAULT_DB


def article_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if retrieval.article_name(line))


def load_targets(database: str, only_short: int, limit: int,
                 match: str = "") -> tuple[list[dict], list[str]]:
    """以**盘上的文件**为准，URL 从目录（_all_documents.json）里按标题 join。

    `_library_manifest.json` 不能当账本：它记着 1110 份"已存"，其中 462 份文件
    根本不在盘上，而盘上另有 164 份它没记。抓取工具每轮重写这份清单，多轮下来
    就成了一个既缺又多、对不上现实的文件。
    """
    catalog = json.load(open(os.path.join(database, "_all_documents.json"),
                             encoding="utf-8"))
    by_title = {item["title"]: item for item in catalog}
    targets, unresolved = [], []
    root = os.path.join(database, "library")
    for folder, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".txt"):
                continue
            path = os.path.join(folder, name)
            title = os.path.splitext(name)[0]
            dense = len("".join(open(path, encoding="utf-8",
                                     errors="ignore").read().split()))
            if match and match not in title:
                continue
            if dense >= only_short:
                continue
            item = by_title.get(title)
            if item is None:
                unresolved.append(title)
                continue
            targets.append({"title": title, "url": item["url"],
                            "channel": item.get("channel"),
                            "file": os.path.relpath(path, database),
                            "chars": dense})
    targets.sort(key=lambda row: row["chars"])
    return (targets[:limit] if limit else targets), unresolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-short", type=int, default=600)
    parser.add_argument("--match", default="",
                        help="只重刷标题含该字串的文档（不计长度门槛）")
    parser.add_argument("--apply", action="store_true", help="真的覆盖文件")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    if not args.limit and not args.apply:
        # 默认只抽样：跑全库要几百次抓取，别让人一个不留神就等半小时
        args.limit = 20

    if args.match:
        args.only_short = 10 ** 12      # 定点重刷时不受长度门槛限制
    targets, unresolved = load_targets(args.database, args.only_short,
                                       args.limit, args.match)
    print(f"待重刷 {len(targets)} 份（盘上文件、去空白 <{args.only_short} 字）"
          + ("" if args.apply else "　—— dry-run，不写任何文件"))
    if unresolved:
        print(f"  目录里找不到标题、无法定位 URL 的 {len(unresolved)} 份："
              f"{[t[:22] for t in unresolved[:3]]}")
    if not targets:
        return

    before, after, improved, gained, failed = [], [], 0, 0, 0
    sources: dict[str, int] = {}
    for index, row in enumerate(targets, 1):
        path = os.path.join(args.database, row["file"])
        old_text = open(path, encoding="utf-8", errors="ignore").read() \
            if os.path.exists(path) else ""
        try:
            html = fetch(row["url"], timeout=25, retries=2)
        except Exception as exc:
            failed += 1
            print(f"  [{index}] 抓取失败 {type(exc).__name__}  {row['title'][:32]}")
            continue
        new_text, source = body_text_with_source(html, meta_fields(html))
        # 页面正文仍不够：正文可能挂在附件里（规划、方案、决定最常见）
        if len("".join(new_text.split())) < MIN_BODY_CHARS:
            for link in attachment_links(html, row["url"])[:2]:
                try:
                    attached, kind = attachment_text(link)
                except Exception:                     # noqa: BLE001
                    continue          # 附件下载失败不该中断整轮重刷
                if len("".join(attached.split())) > len("".join(new_text.split())):
                    new_text, source = attached, kind
        sources[source] = sources.get(source, 0) + 1
        old_dense = len("".join(old_text.split()))
        new_dense = len("".join(new_text.split()))
        old_art, new_art = article_count(old_text), article_count(new_text)
        before.append(old_dense)
        after.append(new_dense)
        if new_dense > old_dense:
            improved += 1
        if new_art and not old_art:
            gained += 1
        flag = "↑" if new_dense > old_dense else ("=" if new_dense == old_dense else "↓")
        print(f"  [{index}/{len(targets)}] {flag} 存 {old_dense:>5} → 新 {new_dense:>5} 字"
              f"　条文 {old_art}→{new_art}　[{source}]  {row['title'][:30]}")
        if args.apply:
            backup = os.path.join(args.database, "_library_backup",
                                  row["file"])
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            if os.path.exists(path) and not os.path.exists(backup):
                shutil.copy2(path, backup)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new_text)
            row.update({"chars": len(new_text), "extraction": source,
                        "dense_chars": new_dense, "articles": new_art})
        time.sleep(args.delay)

    print(f"\n=== {'已覆盖' if args.apply else '对比结果'} ===")
    if before:
        print(f"  成功重抓 {len(before)} 份，失败 {failed}")
        print(f"  去空白字数中位：{statistics.median(before):.0f} → "
              f"{statistics.median(after):.0f}")
        flat = sum(1 for b, a in zip(before, after) if a == b)
        worse = sum(1 for b, a in zip(before, after) if a < b)
        print(f"  变多 {improved}／持平 {flat}／变少 {worse}")
        print(f"  首次出现条文结构：{gained} 份")
        print(f"  来源分布：{sources}")
    if args.apply:
        print(f"  原件备份在 {args.database}\\_library_backup")


if __name__ == "__main__":
    main()
