"""Import completed manual temporal review rows into the override file."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import temporal_evidence

from paths import DEFAULT_DATABASE as DEFAULT_DB
DEFAULT_QUEUE = os.path.join(HERE, "temporal_review_v1", "review_queue.csv")


def _valid_date(value: str, field: str, row_id: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{row_id}：{field} 不是 YYYY-MM-DD：{value!r}") from exc


def import_reviews(database: str = DEFAULT_DB,
                   queue_path: str = DEFAULT_QUEUE) -> int:
    override_path = os.path.join(database, temporal_evidence.OVERRIDE_NAME)
    if os.path.exists(override_path):
        data = json.load(open(override_path, encoding="utf-8"))
    else:
        data = {"version": 1, "documents": {}}
    entries = data.setdefault("documents", {})
    imported = 0
    with open(queue_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            dates = {
                "document_date": row.get("review_document_date", "").strip(),
                "published_at": row.get("review_published_at", "").strip(),
                "effective_date": row.get("review_effective_date", "").strip(),
            }
            if not any(dates.values()):
                continue
            row_id = row.get("queue_id") or row.get("file")
            if not row.get("review_confirmed_by", "").strip() or \
                    not row.get("review_note", "").strip():
                raise ValueError(f"{row_id}：填写日期时必须提供核对人和说明")
            for field, value in dates.items():
                if value:
                    _valid_date(value, field, row_id)
            override = {key: value for key, value in dates.items() if value}
            override.update({
                "effective_basis": row.get("review_evidence", "").strip(),
                "confirmed_by": row["review_confirmed_by"].strip(),
                "checked_at": row.get("review_checked_at", "").strip()
                or dt.date.today().isoformat(),
                "note": row["review_note"].strip(),
                "url": row.get("review_official_url", "").strip()
                or row.get("url", "").strip(),
                "confidence": "high",
            })
            entries[row["file"]] = override
            imported += 1
    temp = override_path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp, override_path)
    print(f"导入人工核对 {imported} 条 → {override_path}")
    print("下一步重建索引：python haikou\\build_temporal_index.py")
    return imported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--queue", default=DEFAULT_QUEUE)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        with tempfile.TemporaryDirectory() as temp:
            queue = os.path.join(temp, "review.csv")
            with open(queue, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["queue_id", "file", "url",
                                        "review_document_date",
                                        "review_published_at",
                                        "review_effective_date",
                                        "review_confirmed_by",
                                        "review_checked_at",
                                        "review_official_url",
                                        "review_evidence", "review_note"])
                writer.writeheader()
                writer.writerow({
                    "queue_id": "TQ-test", "file": "sample.txt",
                    "url": "https://example.test",
                    "review_published_at": "2020-01-02",
                    "review_effective_date": "2020-03-01",
                    "review_confirmed_by": "test",
                    "review_checked_at": "2026-11-22",
                    "review_official_url": "https://example.test",
                    "review_evidence": "自2020年3月1日起施行",
                    "review_note": "selftest",
                })
            count = import_reviews(temp, queue)
            data = json.load(open(os.path.join(
                temp, temporal_evidence.OVERRIDE_NAME), encoding="utf-8"))
            ok = (count == 1
                  and data["documents"]["sample.txt"]["effective_date"]
                  == "2020-03-01")
            print(f"{'通过' if ok else '失败'}  人工日期覆盖导入自检")
            if not ok:
                raise SystemExit(1)
            return
    import_reviews(args.database, args.queue)


if __name__ == "__main__":
    main()
