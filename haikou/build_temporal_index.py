"""Build the persistent point-in-time evidence index for all cached documents."""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docstore
import temporal_evidence
from warn import doc_key

from paths import DEFAULT_DATABASE as DEFAULT_DB


def _metadata_maps(database: str) -> tuple[dict[str, dict], dict[str, dict]]:
    by_key, by_safe = {}, {}
    for item in docstore.load_metadata(database):
        title = item.get("title", "")
        if not title:
            continue
        by_key.setdefault(doc_key(title), item)
        by_safe.setdefault(docstore.safe_name(title), item)
    return by_key, by_safe


def build(database: str = DEFAULT_DB, out_path: str | None = None,
          limit: int = 0) -> dict:
    paths = docstore.corpus_files(database) + docstore.library_files(database)
    if limit:
        paths = paths[:limit]
    by_key, by_safe = _metadata_maps(database)
    fingerprint = temporal_evidence.corpus_fingerprint(database)
    override_path = os.path.join(database, temporal_evidence.OVERRIDE_NAME)
    overrides = {}
    if os.path.exists(override_path):
        override_doc = json.load(open(override_path, encoding="utf-8"))
        overrides = override_doc.get("documents", override_doc)
    documents, by_title, by_file = {}, {}, {}
    warnings = []
    total_chars = 0
    total_bytes = 0
    for position, path in enumerate(paths):
        file_name = os.path.basename(path)
        title = os.path.splitext(file_name)[0]
        text = open(path, encoding="utf-8", errors="ignore").read()
        total_chars += len(text)
        total_bytes += os.path.getsize(path)
        meta = by_key.get(doc_key(title)) or by_safe.get(docstore.safe_name(title)) or {}
        record = temporal_evidence.extract_evidence(
            title, text, url=meta.get("url"), file=file_name)
        if file_name in overrides:
            record = temporal_evidence.apply_override(
                record, overrides[file_name])
        record["path"] = os.path.relpath(path, database)
        record["chars"] = len(text)
        key = str(position)
        documents[key] = record
        by_title.setdefault(doc_key(title), []).append(position)
        by_file[file_name] = position
        if record["warnings"]:
            warnings.append({"file": file_name, "warnings": record["warnings"]})
        if (position + 1) % 200 == 0:
            print(f"  {position + 1}/{len(paths)}", file=sys.stderr)

    kinds = collections.Counter(row["instrument_kind"] for row in documents.values())
    coverage = collections.Counter(row["coverage"] for row in documents.values())
    required_missing = [
        row for row in documents.values()
        if row["requires_effective_date"] and not row["effective_date"]
    ]
    nonrequired_missing = [
        row for row in documents.values()
        if not row["requires_effective_date"]
        and not (row["document_date"] or row["published_at"])
    ]
    index = {
        "version": 1,
        "database": database,
        "count": len(documents),
        "total_chars": total_chars,
        "fingerprint": {**fingerprint, "bytes_chars": total_chars},
        "stats": {
            "kinds": dict(kinds),
            "coverage": dict(coverage),
            "effective_date": sum(bool(row["effective_date"])
                                  for row in documents.values()),
            "requires_effective_date": sum(bool(row["requires_effective_date"])
                                           for row in documents.values()),
            "required_missing_effective": len(required_missing),
            "nonrequired_missing_any_date": len(nonrequired_missing),
            "with_url_month": sum(bool(row["url_month"])
                                  for row in documents.values()),
            "manual_overrides": sum(bool(row.get("override"))
                                    for row in documents.values()),
            "date_warnings": len(warnings),
        },
        "documents": documents,
        "by_title": by_title,
        "by_file": by_file,
        "warnings": warnings,
    }
    target = out_path or os.path.join(database, temporal_evidence.INDEX_NAME)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, target)
    if not os.path.exists(override_path):
        with open(override_path, "w", encoding="utf-8") as handle:
            json.dump({
                "version": 1,
                "note": "按文件名维护人工核对日期；字段示例见 README。",
                "documents": {},
            }, handle, ensure_ascii=False, indent=2)
    print(f"时点证据索引 → {target}")
    print(f"文件 {len(documents)}；字符 {total_chars:,}；"
          f"明确施行日 {index['stats']['effective_date']}；"
          f"需要施行日但缺失 {len(required_missing)}")
    print("类型：" + "、".join(f"{key} {value}"
                              for key, value in sorted(kinds.items())))
    print("覆盖：" + "、".join(f"{key} {value}"
                              for key, value in sorted(coverage.items())))
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    build(args.database, args.out or None, args.limit)


if __name__ == "__main__":
    main()
