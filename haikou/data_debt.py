"""Audit and safely reduce temporal-date and document-number data debt.

The audit separates mechanical extraction misses from genuinely absent source
evidence.  It never supplies an effective date from a URL month or invents a
document number from a title.

    python data_debt.py audit
    python data_debt.py audit --samples 12
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
import document_numbers
import temporal_evidence

from paths import DEFAULT_DATABASE as DEFAULT_DB

NUMBER_INDEX = "_document_numbers.json"
NUMBER_REVIEW_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "document_number_review_v1")
NUMBER_REVIEW_FIELDS = [
    "queue_id", "priority", "current_status", "file", "title", "url",
    "extraction_reason", "candidate_evidence", "review_number",
    "review_confirmed_by", "review_checked_at", "review_official_url",
    "review_evidence", "review_note",
]
SELF_EFFECTIVE = re.compile(
    r"(?:自|从)\s*"
    r"(?P<y>20\d{2})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
    r"\s*起\s*(?:正式\s*)?(?:施行|生效|执行|实施)")
PUBLICATION_EFFECTIVE = re.compile(
    r"(?:自|从)?\s*(?:发布|公布|印发|公告)\s*之日\s*起"
    r"\s*(?:正式\s*)?(?:施行|生效|执行|实施)")


def _normal(text: str) -> str:
    value = (text or "").replace("\n", "")
    value = re.sub(r"\s+", "", value)
    return (value.replace("（", "(").replace("）", ")")
            .replace("〔", "[").replace("〕", "]")
            .replace("［", "[").replace("］", "]"))


def _paths(database: str) -> dict[str, str]:
    paths = {}
    for path in docstore.corpus_files(database) + docstore.library_files(database):
        paths.setdefault(os.path.basename(path), path)
    return paths


def _context(text: str, position: int, span: int = 90) -> str:
    return text[max(0, position - span):position + span].replace("\n", " ")


def number_from_masthead(text: str, title: str = "") -> dict | None:
    return document_numbers.extract(text, title)

def date_debt_record(record: dict, text: str) -> dict:
    flat = re.sub(r"\s+", "", text or "")
    self_effective = list(SELF_EFFECTIVE.finditer(flat))
    publication_rule = bool(PUBLICATION_EFFECTIVE.search(flat))
    has_any_date = bool(record.get("document_date")
                        or record.get("published_at")
                        or record.get("url_month"))
    if self_effective:
        reason = "explicit_effective_missed"
    elif publication_rule and record.get("document_date"):
        reason = "publication_effective_with_heading_date"
    elif publication_rule:
        reason = "publication_effective_without_usable_date"
    elif record.get("document_date") or record.get("published_at"):
        reason = "no_implementation_statement"
    elif has_any_date:
        reason = "only_url_month"
    else:
        reason = "no_date_evidence"
    return {
        "reason": reason,
        "self_effective_dates": [
            f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
            for m in self_effective[:2]
        ],
        "publication_rule": publication_rule,
        "context": _context(flat, self_effective[0].start())
        if self_effective else "",
    }


def audit_dates(database: str = DEFAULT_DB) -> dict:
    index_path = os.path.join(database, temporal_evidence.INDEX_NAME)
    index = json.load(open(index_path, encoding="utf-8"))
    paths = _paths(database)
    rows = []
    for record in index.get("documents", {}).values():
        if not record.get("requires_effective_date") \
                or record.get("effective_date"):
            continue
        path = paths.get(record.get("file", ""))
        text = open(path, encoding="utf-8", errors="ignore").read() \
            if path else ""
        debt = date_debt_record(record, text)
        rows.append({
            "file": record.get("file"),
            "title": record.get("title"),
            "kind": record.get("instrument_kind"),
            "document_date": record.get("document_date"),
            "published_at": record.get("published_at"),
            "url": record.get("url"),
            **debt,
        })
    return {
        "total_documents": index.get("count", 0),
        "missing_effective": len(rows),
        "reasons": dict(Counter(row["reason"] for row in rows)),
        "kinds": dict(Counter(row["kind"] for row in rows)),
        "rows": rows,
    }


def audit_numbers(database: str = DEFAULT_DB) -> dict:
    paths = docstore.corpus_files(database) + docstore.library_files(database)
    rows = []
    missing = []
    for path in paths:
        text = open(path, encoding="utf-8", errors="ignore").read()
        title = os.path.splitext(os.path.basename(path))[0]
        found = number_from_masthead(text, title)
        if found:
            rows.append({
                "file": os.path.basename(path),
                "title": title,
                **found,
            })
        else:
            head = re.sub(r"\s+", "", text[:800])
            missing.append({
                "file": os.path.basename(path),
                "title": title,
                "reason": (
                    "candidate_rejected"
                    if re.search(r"(?:〔[^〕]{0,15〕|公告|人民政府令)", head)
                    else "no_number_in_masthead"),
                "evidence": head[:160],
            })
    return {
        "total_documents": len(paths),
        "with_number": len(rows),
        "missing_number": len(paths) - len(rows),
        "rows": rows,
        "missing": missing,
    }


def build_number_index(database: str = DEFAULT_DB) -> dict:
    """Persist document-number evidence and unresolved rows atomically."""
    audit = audit_numbers(database)
    records = {}
    for row in audit["rows"]:
        records[row["file"]] = row
    index = {
        "version": 1,
        "database": database,
        "total_documents": audit["total_documents"],
        "with_number": audit["with_number"],
        "missing_number": audit["missing_number"],
        "missing_reasons": dict(Counter(
            row["reason"] for row in audit["missing"])),
        "documents": records,
        "missing": audit["missing"],
    }
    path = os.path.join(database, NUMBER_INDEX)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    return index


def build_number_review_queue(database: str = DEFAULT_DB) -> list[dict]:
    """Create an auditable CSV for numbers absent from the source masthead."""
    index_path = os.path.join(database, NUMBER_INDEX)
    index = (json.load(open(index_path, encoding="utf-8"))
             if os.path.exists(index_path) else build_number_index(database))
    metadata = {}
    catalog_path = os.path.join(database, "_all_documents.json")
    for item in json.load(open(catalog_path, encoding="utf-8")):
        metadata.setdefault(item.get("title", ""), item)
    status_path = os.path.join(database, "_force_status.json")
    status = json.load(open(status_path, encoding="utf-8")) \
        if os.path.exists(status_path) else {}
    by_file = {}
    for row in status.values():
        by_file.setdefault(row.get("file", ""), row.get("status", ""))
    priority = {"已废止": "P0", "系列已废止": "P0", "效力存疑": "P1",
                "现行有效": "P2"}
    queue = []
    for number, row in enumerate(index.get("missing", []), 1):
        meta = metadata.get(row.get("title", ""), {})
        current = by_file.get(row.get("file", ""), "")
        queue.append({
            "queue_id": f"DN-{number:03d}",
            "priority": priority.get(current, "P3"),
            "current_status": current,
            "file": row.get("file", ""),
            "title": row.get("title", ""),
            "url": meta.get("url", ""),
            "extraction_reason": row.get("reason", ""),
            "candidate_evidence": row.get("evidence", ""),
            **{field: "" for field in NUMBER_REVIEW_FIELDS
               if field.startswith("review_")},
        })
    queue.sort(key=lambda row: (row["priority"], row["title"]))
    for number, row in enumerate(queue, 1):
        row["queue_id"] = f"DN-{number:03d}"
    os.makedirs(NUMBER_REVIEW_DIR, exist_ok=True)
    csv_path = os.path.join(NUMBER_REVIEW_DIR, "review_queue.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=NUMBER_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(queue)
    manifest = {
        "version": 1,
        "cases": len(queue),
        "priority_counts": dict(Counter(row["priority"] for row in queue)),
        "extraction_reasons": dict(Counter(
            row["extraction_reason"] for row in queue)),
        "instructions": "核对官方原文后填写 review_* 字段。",
    }
    with open(os.path.join(NUMBER_REVIEW_DIR, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return queue


def print_samples(rows: list[dict], count: int) -> None:
    for row in rows[:count]:
        print(f"\n[{row.get('reason', 'number')}] {row.get('title', '')}")
        print(f"  kind={row.get('kind')} doc_date={row.get('document_date')} "
              f"published={row.get('published_at')}")
        if row.get("self_effective_dates"):
            print(f"  explicit={row['self_effective_dates']}")
        if row.get("number"):
            print(f"  number={row['number']}")
        if row.get("evidence"):
            print(f"  evidence={row['evidence'][:180]}")
        if row.get("context"):
            print(f"  context={row['context'][:180]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="政策数据债审计")
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument(
        "command", choices=["audit", "build-numbers", "build-number-review"])
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()
    dates = audit_dates(args.database)
    numbers = audit_numbers(args.database)
    if args.command == "build-numbers":
        index = build_number_index(args.database)
        print(f"文号索引 → {os.path.join(args.database, NUMBER_INDEX)}")
        print(f"可提取 {index['with_number']}/{index['total_documents']}；"
              f"待补证 {index['missing_number']}")
        return
    if args.command == "build-number-review":
        queue = build_number_review_queue(args.database)
        print(f"文号补证队列 → {NUMBER_REVIEW_DIR}（{len(queue)} 份）")
        return
    print("== 时点日期 ==")
    print(f"缺少明确施行日：{dates['missing_effective']}/"
          f"{dates['total_documents']}")
    print("分类：" + json.dumps(dates["reasons"], ensure_ascii=False))
    print_samples(dates["rows"], args.samples)
    print("\n== 文号 ==")
    print(f"可提取：{numbers['with_number']}/{numbers['total_documents']}；"
          f"仍缺：{numbers['missing_number']}")
    print_samples(numbers["rows"], args.samples)
    print("\n== 未提取文号样本 ==")
    print_samples(numbers["missing"], args.samples)


if __name__ == "__main__":
    main()
