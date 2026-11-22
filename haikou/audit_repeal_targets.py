"""重抓废止通知，核对索引里的"被废止名单"还对不对。

解析器改动之后必须能说清"改了哪些名单"，而不是重跑一遍就完事。这个工具重新
抓取通知正文，用当前解析器解一遍，跟 `_repeal_index.json` 里存着的名单逐条
对比，报出新增与移除，并打印被移除那份在本句里的上下文，好判断是修掉了误判
还是删掉了真目标。

    python audit_repeal_targets.py            # 只看差异
    python audit_repeal_targets.py --apply    # 用新解析结果刷新索引（先备份）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import repeal

from paths import DEFAULT_DATABASE as DEFAULT_DB


def context_of(text: str, name: str, span: int = 60) -> str:
    flat = text.replace("\n", "")
    position = flat.find(name)
    if position == -1:
        return ""
    return flat[max(0, position - span):position + len(name) + span]


def fetch_all(database: str, limit: int = 0) -> list[dict]:
    path = os.path.join(database, "_repeal_notices.json")
    notices = json.load(open(path, encoding="utf-8"))
    if limit:
        notices = notices[:limit]
    fresh = []
    for index, item in enumerate(notices, 1):
        try:
            html = repeal.fetch(item["url"], timeout=30, retries=2)
            record = repeal.parse_notice(item["url"], html)
            record["_text"] = repeal.body_text(html, repeal.meta_fields(html))
            fresh.append(record)
            print(f"  [{index}/{len(notices)}] {item['title'][:46]}",
                  file=sys.stderr)
        except Exception as exc:
            print(f"  [{index}/{len(notices)}] 抓取失败 {item['url'][:60]}"
                  f"：{type(exc).__name__}", file=sys.stderr)
        time.sleep(0.4)
    return fresh


def diff_against_index(records: list[dict], stored: dict) -> list[dict]:
    by_title = {r["notice_title"]: r for r in stored.get("notices", [])}
    out = []
    for record in records:
        # 检索会捞到"关于设立重点园区管理体制的决定"这类不含废止内容的文件，
        # 它们没有被废止名单，不算废止通知，不参与对比。
        if not record["repealed_documents"] and record["notice_title"] not in by_title:
            continue
        old = by_title.get(record["notice_title"])
        if old is None:
            out.append({"record": record, "old": None,
                        "added": sorted(record["repealed_documents"]),
                        "removed": [], "new": True})
            continue
        old_names = set(old.get("repealed_documents") or [])
        new_names = set(record["repealed_documents"])
        if old_names == new_names:
            continue
        out.append({
            "record": record, "old": old,
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
            "new": False,
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true",
                        help="用新解析结果刷新 _repeal_index.json（先备份）")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    stored_path = os.path.join(args.database, "_repeal_index.json")
    stored = json.load(open(stored_path, encoding="utf-8"))
    print(f"重抓 {len(json.load(open(os.path.join(args.database, '_repeal_notices.json'), encoding='utf-8')))} 份通知…\n",
          file=sys.stderr)
    records = fetch_all(args.database, args.limit)
    diffs = diff_against_index(records, stored)

    if not diffs:
        print(f"名单一致：{len(records)} 份通知，无差异")
        return
    print(f"有差异的通知 {len(diffs)} 份：\n")
    for item in diffs:
        record = item["record"]
        print("=" * 96)
        print(f"《{record['notice_title'][:70]}》")
        for name in item["added"]:
            print(f"  ＋ 新增  {name}")
        for name in item["removed"]:
            print(f"  － 移除  {name}")
            snippet = context_of(record["_text"], name)
            if snippet:
                print(f"          上下文：…{snippet}…")
    if not args.apply:
        print("\n（未改动索引；确认后用 --apply 刷新）")
        return

    backup = stored_path + ".bak"
    shutil.copy2(stored_path, backup)

    def clean(record: dict) -> dict:
        out = dict(record)
        # 把正文一并存进索引：以后再改解析器，核对名单就不用重新抓网
        out["notice_text"] = (out.pop("_text", "") or "")[:4000]
        return out

    fresh_by_title = {record["notice_title"]: record for record in records}
    known = {old["notice_title"] for old in stored["notices"]}
    refreshed = []
    for old in stored["notices"]:
        fresh = fresh_by_title.get(old["notice_title"])
        if fresh is None:
            # 抓取失败时保留原记录：宁可名单旧一点，也不能因为一次网络抖动
            # 把已有依据整条删掉
            print(f"  ! 未抓到，保留原记录：{old['notice_title'][:44]}")
            refreshed.append(old)
        else:
            refreshed.append(clean(fresh))
    for record in records:
        if record["repealed_documents"] and record["notice_title"] not in known:
            print(f"  ＋ 新收录通知：{record['notice_title'][:44]}")
            refreshed.append(clean(record))
    stored["notices"] = refreshed
    with open(stored_path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, ensure_ascii=False, indent=1)
    print(f"\n已刷新索引（旧文件备份在 {backup}）")


if __name__ == "__main__":
    main()
