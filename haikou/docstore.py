"""On-demand document store: full metadata for everything, body text on demand.

The library holds 1872 documents' metadata (title, channel, url) for a few
hundred KB. Fetching every body would take an hour and store mostly text nobody
asks about, so a body is fetched the first time something needs it and then
cached under library/<channel>/.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repeal import body_text, fetch, is_boilerplate, meta_fields
from embedded_repeals import (document_number_of, effective_date_of,
                              record_in_index, scan_text)

MIN_CHARS = 120


def safe_name(title: str, limit: int = 70) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", title).strip("_")[:limit] or "untitled"


def load_metadata(database: str) -> list[dict]:
    path = os.path.join(database, "_all_documents.json")
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding="utf-8"))


def cache_path(database: str, item: dict) -> str:
    return os.path.join(database, "library", item.get("channel", "其他"),
                        safe_name(item["title"]) + ".txt")


def cached(database: str, item: dict) -> str | None:
    path = cache_path(database, item)
    if os.path.exists(path):
        return open(path, encoding="utf-8", errors="ignore").read()
    return None


def ensure(database: str, item: dict) -> tuple[str | None, str]:
    """Return (text, how) where how is 'cache' | 'fetched' | 'too_short' | 'error'."""
    text = cached(database, item)
    if text is not None:
        scan_for_repeals(database, text, item["title"], from_cache=True)
        return text, "cache"
    try:
        html = fetch(item["url"], timeout=25, retries=2)
    except Exception as exc:
        return None, f"error:{type(exc).__name__}"
    meta = meta_fields(html)
    text = body_text(html, meta)
    if len(text) < MIN_CHARS:
        return None, f"too_short:{len(text)}"
    if is_boilerplate(text):
        return None, "boilerplate"
    path = cache_path(database, item)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    scan_for_repeals(database, text, item["title"], from_cache=False)
    return text, "fetched"


def scan_for_repeals(database: str, text: str, title: str,
                     from_cache: bool = False) -> list[str]:
    """A document fetched for any reason is checked for its own repeal clause.

    The force index therefore grows with use: nothing has to be crawled up
    front for a new "同时废止" clause to be discovered.
    """
    found = scan_text(text)
    if not found:
        return []
    record = {
        "source_file": os.path.basename(title) + ".txt" if not title.endswith(".txt")
        else os.path.basename(title),
        "source_title": title,
        "source_number": document_number_of(text),
        "effective_date": effective_date_of(text),
        "repeals": found,
        "discovered_by": "cache_scan" if from_cache else "fetch_scan",
    }
    if record_in_index(database, record):
        print(f"  [效力] 新发现废止条款：{title[:34]} → {found}",
              file=sys.stderr)
    return found


def corpus_files(database: str) -> list[str]:
    """The curated corpus kept at the database root (not the library)."""
    return [os.path.join(database, name)
            for name in sorted(os.listdir(database))
            if not name.startswith("_") and name.lower().endswith((".txt", ".md"))]


def library_files(database: str) -> list[str]:
    root = os.path.join(database, "library")
    out = []
    for folder, _, files in os.walk(root):
        for name in sorted(files):
            if name.lower().endswith(".txt"):
                out.append(os.path.join(folder, name))
    return out
