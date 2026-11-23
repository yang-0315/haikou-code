"""Refresh repeal-count completeness metadata without refetching pages."""
from __future__ import annotations

import argparse
import json
import os
import tempfile

from paths import DEFAULT_DATABASE as DEFAULT_DB
import repeal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fetch", action="store_true",
                        help="read official attachments and merge enumerated repeal lists")
    args = parser.parse_args()

    path = os.path.join(args.database, "_repeal_index.json")
    index = json.load(open(path, encoding="utf-8"))
    changed = 0
    for notice in index.get("notices", []):
        names = notice.get("repealed_documents") or []
        original_names = list(names)
        declared = repeal.declared_repeal_count(
            notice.get("notice_title", "")
        )
        mixed_decision = ("修改" in notice.get("notice_title", "")
                          and "废止" in notice.get("notice_title", ""))
        if args.fetch and notice.get("url") and (
                notice.get("list_complete") is False or mixed_decision):
            try:
                html = repeal.fetch(notice["url"])
                attachments = repeal.attachment_links(html, notice["url"])
                candidate_lists = []
                for attachment_url in attachments:
                    text, _ = repeal.attachment_text(attachment_url)
                    items = repeal.enumerated_repeal_items(text)
                    if items:
                        candidate_lists.append(items)
                if not candidate_lists:
                    body = repeal.body_text(html, repeal.meta_fields(html))
                    items = repeal.numbered_repeal_items(body)
                    if items:
                        candidate_lists.append(items)
                exact = [items for items in candidate_lists
                         if declared and len(items) == declared]
                chosen = exact[0] if exact else (
                    max(candidate_lists, key=len) if candidate_lists else []
                )
                if chosen:
                    names = list(chosen)
                notice["repealed_documents"] = names
            except Exception as exc:  # noqa: BLE001
                print(f"fetch failed: {notice.get('url')}: {type(exc).__name__}")
        complete = declared is None or len(names) >= declared
        note = "" if complete else (
            f"标题声明废止 {declared} 件，正文只列出 {len(names)} 件，"
            "其余可能在附件中"
        )
        metadata_changed = (
            (notice.get("declared_count"), notice.get("list_complete"),
             notice.get("list_note")) != (declared, complete, note)
        )
        if metadata_changed or names != original_names:
            changed += 1
            notice["declared_count"] = declared
            notice["list_complete"] = complete
            notice["list_note"] = note

    print(f"repeal metadata: {changed} records changed")
    if args.apply and changed:
        directory = os.path.dirname(path)
        handle, temp_path = tempfile.mkstemp(
            prefix="_repeal_index_", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(index, stream, ensure_ascii=False, indent=1)
                stream.write("\n")
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
