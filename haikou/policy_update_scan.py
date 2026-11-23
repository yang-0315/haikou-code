"""Scan official Hainan policy channels without mutating the policy library.

The scanner compares official listing pages with the current
``_all_documents.json`` inventory and writes an auditable update ledger.  It
never downloads policy bodies, overwrites catalogue entries, or changes a
validity state.  Those actions require a later reviewed update step.

    python policy_update_scan.py --max-pages 1
    python policy_update_scan.py --max-pages 1 --apply
    python policy_update_scan.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from crawl_all_channels import CHANNELS, extract, fetch, page_count, page_url
from paths import DEFAULT_DATABASE as DEFAULT_DB
from update_workflow import REVIEW_NAME, candidate_id, write_review_queue

LEDGER_NAME = "_update_ledger.json"
REPORT_DIR = "_update_reports"
LISTING_URL = re.compile(r"(?:list|nlist)\d*\.s?html?$|/$", re.I)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _hash_json(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_catalog(database: str) -> list[dict]:
    path = os.path.join(database, "_all_documents.json")
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding="utf-8"))


def _title_key(value: str) -> str:
    value = re.sub(r"\.\.\.|…|&nbsp;|\s+", "", value or "")
    return value.lstrip(".")


def _same_display_title(left: str, right: str) -> bool:
    """Listing pages often truncate long titles without changing the document."""
    left, right = _title_key(left), _title_key(right)
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 12 and longer.startswith(shorter)


def classify(old_by_title: dict[str, list[dict]],
             old_by_url: dict[str, dict], found: list[dict]) -> list[dict]:
    """Classify listing differences as review candidates, never auto-publish."""
    candidates = []
    for item in found:
        same_url = old_by_url.get(item["url"])
        same_title = old_by_title.get(_title_key(item["title"]), [])
        if LISTING_URL.search(item["url"]):
            continue
        if same_url and _same_display_title(
                same_url.get("title", ""), item["title"]):
            continue
        if same_url and same_url.get("title") != item["title"]:
            kind = "title_changed"
            reason = "同一官方 URL 的标题发生变化"
        elif same_title and any(row.get("url") != item["url"]
                                for row in same_title):
            kind = "possible_new_version"
            reason = "同名文件出现新的官方 URL"
        elif "废止" in item["title"] or "失效" in item["title"]:
            kind = "repeal_or_invalidation_notice"
            reason = "标题包含废止或失效"
        elif any(word in item["title"] for word in ("修改", "修订", "决定")):
            kind = "amendment_notice"
            reason = "标题包含修改、修订或决定"
        else:
            kind = "new_document"
            reason = "当前目录未收录"
        candidates.append({
            **item,
            "id": candidate_id(item["url"], item["title"]),
            "kind": kind,
            "reason": reason,
            "review_status": "pending",
            "workflow_status": "pending",
            "detected_at": _now(),
            "official_url": item["url"],
        })
    return candidates


def preserve_review_state(database: str, candidates: list[dict]) -> list[dict]:
    """Carry human decisions across repeated scans of the same official item."""
    path = os.path.join(database, LEDGER_NAME)
    if not os.path.exists(path):
        return candidates
    try:
        ledger = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return candidates
    previous = (ledger.get("latest") or {}).get("candidates") or []
    by_id = {
        row.get("id") or candidate_id(
            row.get("official_url") or row.get("url", ""),
            row.get("title", "")): row
        for row in previous
    }
    by_identity = {
        (
            row.get("official_url") or row.get("url", ""),
            row.get("title", ""),
        ): row
        for row in previous
    }
    preserved = {
        "title", "listing_title", "review_status", "workflow_status",
        "verified_by", "verified_at",
        "reviewed_by", "reviewed_at", "evidence", "review_notes", "action",
        "review_history", "staged_at", "page_sha256", "source_hash",
        "body_sha256", "body_chars", "extraction", "extraction_file",
        "page_meta", "page_file", "body_file", "diff_file",
        "attachment_files", "attachment_count", "baseline", "body_changed",
        "diff_stats", "suggested_action", "published_at",
        "duplicate_of", "duplicate_reason",
        "published_version", "publication_id", "index_summary", "last_error",
    }
    for candidate in candidates:
        old = by_id.get(candidate["id"]) or by_identity.get(
            (candidate["official_url"], candidate["title"]))
        if not old:
            continue
        for key in preserved:
            if key in old:
                candidate[key] = old[key]
    return candidates


def scan(database: str = DEFAULT_DB, max_pages: int = 0,
         channels: list[str] | None = None) -> dict:
    """Fetch official listing pages and return a no-write scan result."""
    wanted = set(channels or [])
    selected = [(name, base) for name, base in CHANNELS
                if not wanted or name in wanted]
    if wanted - {name for name, _ in selected}:
        missing = sorted(wanted - {name for name, _ in selected})
        raise ValueError(f"未知官方栏目：{', '.join(missing)}")

    discovered, failures = [], []
    source_stats = []
    for channel, base in selected:
        try:
            first = fetch(base, timeout=30, retries=2)
        except Exception as exc:                       # noqa: BLE001
            failures.append({"channel": channel, "url": base,
                             "error": f"{type(exc).__name__}: {exc}"})
            continue
        total = page_count(first)
        stop = min(total, max_pages) if max_pages else total
        before = len(discovered)
        for index in range(1, stop + 1):
            try:
                html = first if index == 1 else fetch(
                    page_url(base, index), timeout=30, retries=2)
                discovered.extend(extract(html, page_url(base, index),
                                          channel, index))
            except Exception as exc:                   # noqa: BLE001
                failures.append({"channel": channel,
                                 "url": page_url(base, index),
                                 "error": f"{type(exc).__name__}: {exc}"})
            if index % 5 == 0 or index == stop:
                print(f"  {channel} {index}/{stop}", flush=True)
            time.sleep(0.2)
        source_stats.append({
            "channel": channel,
            "url": base,
            "pages_total": total,
            "pages_scanned": stop,
            "complete": stop == total,
            "documents_listed": len(discovered) - before,
        })

    deduped = {}
    for item in discovered:
        deduped.setdefault(item["url"], item)
    discovered = list(deduped.values())
    current = _load_catalog(database)
    old_by_url = {}
    for item in current:
        url = item.get("url")
        if not url:
            continue
        previous = old_by_url.get(url)
        if previous is None or len(item.get("title", "")) > len(
                previous.get("title", "")):
            old_by_url[url] = item
    old_by_title: dict[str, list[dict]] = {}
    for item in current:
        old_by_title.setdefault(
            _title_key(item.get("title", "")), []).append(item)
    candidates = classify(old_by_title, old_by_url, discovered)
    candidates = preserve_review_state(database, candidates)
    candidates.sort(key=lambda row: (
        0 if row["kind"] in ("repeal_or_invalidation_notice",
                             "amendment_notice",
                             "possible_new_version") else 1,
        row["title"]))
    kind_counts: dict[str, int] = {}
    for row in candidates:
        kind_counts[row["kind"]] = kind_counts.get(row["kind"], 0) + 1
    scan_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return {
        "version": 1,
        "scan_id": scan_id,
        "started_at": _now(),
        "database": database,
        "catalog_count": len(current),
        "catalog_hash": _hash_json(current),
        "channels": source_stats,
        "failures": failures,
        "documents_listed": len(discovered),
        "candidate_count": len(candidates),
        "kind_counts": kind_counts,
        "candidates": candidates,
        "policy": "仅生成审核候选，不自动修改正文、目录或效力状态。",
    }


def write_result(database: str, result: dict) -> tuple[str, str]:
    ledger_path = os.path.join(database, LEDGER_NAME)
    if os.path.exists(ledger_path):
        ledger = json.load(open(ledger_path, encoding="utf-8"))
    else:
        ledger = {"version": 1, "scans": []}
    ledger.setdefault("scans", []).append(result)
    ledger["latest"] = result
    temp = ledger_path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
    os.replace(temp, ledger_path)
    review_queue = write_review_queue(database, result)

    folder = os.path.join(database, REPORT_DIR)
    os.makedirs(folder, exist_ok=True)
    report_path = os.path.join(folder, result["scan_id"] + ".md")
    lines = [
        f"# 政策更新扫描 {result['scan_id']}",
        "",
        f"- 当前目录：{result['catalog_count']} 条",
        f"- 官方列表：{result['documents_listed']} 条",
        f"- 审核候选：{result['candidate_count']} 条",
        f"- 分类：{json.dumps(result['kind_counts'], ensure_ascii=False)}",
        f"- 抓取失败：{len(result['failures'])} 次",
        "",
        "未经官方证据补证不得修改效力或发布正文。"
        f"业务审核队列：`{REVIEW_NAME}`（{review_queue}）。",
        "",
    ]
    for row in result["candidates"]:
        lines.extend([
            f"## {row['title']}",
            "",
            f"- 类型：{row['kind']}",
            f"- 原因：{row['reason']}",
            f"- 栏目：{row['channel']}",
            f"- 官方链接：{row['official_url']}",
            "",
        ])
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return ledger_path, report_path


def selftest() -> None:
    old = [
        {"title": "甲办法", "url": "https://example.test/a"},
        {"title": "乙条例", "url": "https://example.test/b"},
    ]
    found = [
        {"title": "甲办法", "url": "https://example.test/a",
         "channel": "测试", "page": 1},
        {"title": "甲办法", "url": "https://example.test/a2",
         "channel": "测试", "page": 1},
        {"title": "关于废止丙办法的决定", "url": "https://example.test/c",
         "channel": "测试", "page": 1},
        {"title": "丁规定", "url": "https://example.test/d",
         "channel": "测试", "page": 1},
    ]
    by_url = {row["url"]: row for row in old}
    by_title: dict[str, list[dict]] = {}
    for row in old:
        by_title.setdefault(row["title"], []).append(row)
    rows = classify(by_title, by_url, found)
    kinds = {row["title"]: row["kind"] for row in rows}
    ok = (len(rows) == 3
          and kinds["甲办法"] == "possible_new_version"
          and kinds["关于废止丙办法的决定"]
          == "repeal_or_invalidation_notice"
          and kinds["丁规定"] == "new_document")
    print(f"{'通过' if ok else '失败'}  政策更新差异分类自检")
    if not ok:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="每个栏目最多扫描页数；0 为全量")
    parser.add_argument("--channel", action="append", default=[],
                        help="只扫描指定栏目，可重复传入")
    parser.add_argument("--apply", action="store_true",
                        help="写入更新账本和 Markdown 报告")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return
    result = scan(args.database, args.max_pages, args.channel)
    print(f"官方列表 {result['documents_listed']} 条；"
          f"待审核更新 {result['candidate_count']} 条")
    print("分类：" + json.dumps(result["kind_counts"], ensure_ascii=False))
    for row in result["candidates"][:30]:
        print(f"  [{row['kind']}] {row['title'][:52]}")
        print(f"      {row['official_url']}")
    if args.apply:
        ledger, report = write_result(args.database, result)
        print(f"更新账本 → {ledger}")
        print(f"扫描报告 → {report}")
    else:
        print("dry-run：未写入任何账本、正文或目录")


if __name__ == "__main__":
    main()
