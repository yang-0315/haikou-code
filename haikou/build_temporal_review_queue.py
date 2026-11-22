"""Create a prioritized CSV for reviewing documents with missing dates."""
from __future__ import annotations

import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
from paths import DEFAULT_DATABASE as DEFAULT_DB
DEFAULT_OUT = os.path.join(HERE, "temporal_review_v1", "review_queue.csv")

FIELDS = [
    "queue_id", "priority", "current_status", "instrument_kind", "file",
    "title", "document_date", "published_at", "url", "missing",
    "review_document_date", "review_published_at", "review_effective_date",
    "review_confirmed_by", "review_checked_at", "review_official_url",
    "review_evidence", "review_note",
]


def build(database: str = DEFAULT_DB, out_path: str = DEFAULT_OUT) -> list[dict]:
    index = json.load(open(
        os.path.join(database, "_temporal_index.json"), encoding="utf-8"))
    trees_path = os.path.join(database, "_force_trees.json")
    tree_status = {}
    if os.path.exists(trees_path):
        trees = json.load(open(trees_path, encoding="utf-8"))
        tree_status = {row["file"]: row.get("status", {}).get("status")
                       for row in trees.get("docs", {}).values()}
    priority = {"已废止": 0, "系列已废止": 0, "效力存疑": 1, "现行有效": 2}
    rows = [
        row for row in index["documents"].values()
        if row.get("requires_effective_date") and not row.get("effective_date")
    ]
    rows.sort(key=lambda row: (
        priority.get(tree_status.get(row["file"]), 3),
        row["instrument_kind"], row["title"]))
    queue = []
    for number, row in enumerate(rows, 1):
        queue.append({
            "queue_id": f"TQ-{number:03d}",
            "priority": {0: "P0", 1: "P1", 2: "P2"}.get(
                priority.get(tree_status.get(row["file"]), 3), "P3"),
            "current_status": tree_status.get(row["file"], "未入树"),
            "instrument_kind": row["instrument_kind"],
            "file": row["file"],
            "title": row["title"],
            "document_date": row.get("document_date") or "",
            "published_at": row.get("published_at") or "",
            "url": row.get("url") or "",
            "missing": "；".join(row.get("missing") or []),
            **{field: "" for field in FIELDS if field.startswith("review_")},
        })
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(queue)
    manifest_path = os.path.join(os.path.dirname(out_path), "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump({
            "version": 1,
            "cases": len(queue),
            "priority_counts": {
                level: sum(row["priority"] == level for row in queue)
                for level in ("P0", "P1", "P2", "P3")
            },
            "instructions": "核对官方来源后填写 review_* 字段，再运行 import_temporal_review.py。",
        }, handle, ensure_ascii=False, indent=2)
    print(f"人工核对队列 → {out_path}（{len(queue)} 份）")
    return queue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    build(args.database, args.out)


if __name__ == "__main__":
    main()
