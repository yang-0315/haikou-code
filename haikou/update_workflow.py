"""Auditable policy-update workflow between discovery and publication.

The scanner in ``policy_update_scan.py`` only discovers candidates.  This
module adds the missing, deliberately separated stages:

1. stage: download the official page and attachments, extract the body, hash
   every source, compare it with the current cache, and write a review queue;
2. review: record an explicit human decision, reviewer, official evidence,
   and intended publication action;
3. publish: lock the database, back up mutable files, apply the verified
   change, rebuild affected indexes, and roll back on any failure.

Nothing here treats an automatic scan as official verification.  A body can
only be published after an explicit review record is present.

    python update_workflow.py list
    python update_workflow.py stage --max-attachments 5
    python update_workflow.py review --id CANDIDATE_ID --decision verified \
        --by "审核人" --evidence "官方页面中的证据句" --action publish_new
    python update_workflow.py publish --id CANDIDATE_ID
    python update_workflow.py selftest
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docstore
from repeal import (
    attachment_links,
    body_text_with_source,
    fetch,
    fetch_bytes,
    is_boilerplate,
    meta_fields,
)

from paths import DEFAULT_DATABASE as DEFAULT_DB

LEDGER_NAME = "_update_ledger.json"
REVIEW_NAME = "update_review.csv"
WORK_ROOT = "_update_workflow"
PUBLISH_LOG = "_update_publications.json"
LOCK_NAME = "_update_publish.lock"
SOURCE_HOST = "hainan.gov.cn"
MIN_BODY_CHARS = 120
MAX_DIFF_LINES = 240
MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024
PUBLISH_ACTIONS = {"publish_new", "replace_body", "record_only"}

REVIEW_FIELDS = [
    "candidate_id",
    "scan_id",
    "kind",
    "title",
    "channel",
    "official_url",
    "detected_at",
    "page_sha256",
    "body_sha256",
    "body_chars",
    "extraction",
    "attachment_count",
    "baseline_file",
    "body_changed",
    "suggested_action",
    "duplicate_of",
    "duplicate_reason",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "evidence",
    "notes",
    "action",
    "staged_at",
]


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return dt.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return _sha256_bytes(normalized.encode("utf-8"))


def candidate_id(url: str, title: str) -> str:
    """Stable identity across rescans; review fields can therefore survive."""
    raw = f"{(url or '').strip()}\0{(title or '').strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _atomic_text(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)


def _atomic_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "wb") as handle:
        handle.write(data)
    os.replace(temp, path)


def _atomic_json(path: str, value) -> None:
    _atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _official_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == SOURCE_HOST or host.endswith("." + SOURCE_HOST)
    )


def _safe_file_name(name: str, fallback: str = "attachment") -> str:
    clean = urllib.parse.unquote(os.path.basename(name or ""))
    clean = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", clean).strip(" .")
    return clean[:120] or fallback


def _attachment_extension(url: str) -> str:
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".pdf", ".docx", ".doc", ".ofd", ".wps"} else ""


def _attachment_text_from_bytes(url: str, data: bytes) -> tuple[str, str]:
    """Extract one downloaded attachment without a second network request."""
    suffix = _attachment_extension(url)
    try:
        if suffix == ".pdf":
            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages)
            return _tidy_text(text), "attachment:pdf"
        if suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                xml = archive.read("word/document.xml").decode(
                    "utf-8", "ignore")
            xml = xml.replace("</w:p>", "\n")
            return _tidy_text(re.sub(r"<[^>]+>", "", xml)), "attachment:docx"
    except Exception as exc:  # noqa: BLE001
        return "", f"attachment:{type(exc).__name__}"
    return "", "attachment:unsupported"


def _tidy_text(text: str) -> str:
    lines = []
    seen = set()
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line in seen or is_boilerplate(line):
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def _dense_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _load_ledger(database: str) -> dict:
    ledger = _load_json(
        os.path.join(database, LEDGER_NAME),
        {"version": 1, "scans": []},
    )
    if not isinstance(ledger, dict):
        ledger = {"version": 1, "scans": []}
    ledger.setdefault("version", 1)
    ledger.setdefault("scans", [])
    if not isinstance(ledger.get("scans"), list):
        ledger["scans"] = []
    return ledger


def _save_ledger(database: str, ledger: dict) -> None:
    _atomic_json(os.path.join(database, LEDGER_NAME), ledger)


def _latest_scan(ledger: dict, scan_id: str = "") -> dict:
    scans = ledger.get("scans") or []
    if scan_id:
        for scan in reversed(scans):
            if scan.get("scan_id") == scan_id:
                return scan
        raise ValueError(f"账本中没有扫描批次：{scan_id}")
    latest = ledger.get("latest")
    if isinstance(latest, dict):
        # ``latest`` is a convenience copy for external readers.  Workflow
        # mutations must target the canonical entry in ``scans`` as well, or a
        # later reload sees split state.
        latest_id = latest.get("scan_id")
        for scan in reversed(scans):
            if scan.get("scan_id") == latest_id:
                ledger["latest"] = scan
                return scan
        return latest
    if scans:
        return scans[-1]
    raise ValueError("更新账本中没有扫描结果")


def _candidate_in_scan(scan: dict, cid: str) -> dict:
    for candidate in scan.get("candidates") or []:
        candidate["id"] = candidate.get("id") or candidate_id(
            candidate.get("url") or candidate.get("official_url", ""),
            candidate.get("title", ""),
        )
        if candidate["id"] == cid:
            return candidate
    raise ValueError(f"扫描批次中没有候选：{cid}")


def _candidate_dir(database: str, scan: dict, cid: str) -> str:
    return os.path.join(
        database, WORK_ROOT, str(scan.get("scan_id") or "unknown"), cid)


def _relative(database: str, path: str) -> str:
    return os.path.relpath(path, database).replace(os.sep, "/")


def _metadata_maps(database: str) -> tuple[dict[str, dict], dict[str, dict]]:
    by_url: dict[str, dict] = {}
    by_safe: dict[str, dict] = {}
    for item in docstore.load_metadata(database):
        if item.get("url"):
            by_url[item["url"]] = item
        if item.get("title"):
            by_safe[docstore.safe_name(item["title"])] = item
    return by_url, by_safe


def _baseline_for(database: str, candidate: dict) -> dict:
    by_url, by_safe = _metadata_maps(database)
    official_url = candidate.get("url") or candidate.get("official_url", "")
    item = by_url.get(official_url)
    if item is None:
        item = by_safe.get(docstore.safe_name(candidate.get("title", "")))
    if item is None:
        return {
            "exists": False,
            "title": "",
            "url": "",
            "file": "",
            "body_sha256": "",
            "body_chars": 0,
        }
    body_path = docstore.cache_path(database, item)
    body = ""
    if os.path.exists(body_path):
        body = open(body_path, encoding="utf-8", errors="ignore").read()
    return {
        "exists": True,
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "file": _relative(database, body_path),
        "body_sha256": _sha256_text(body) if body else "",
        "body_chars": _dense_len(body),
    }


def _diff_text(old: str, new: str) -> tuple[str, dict]:
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    raw = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="current",
        tofile="official",
        n=3,
        lineterm="",
    ))
    patch = "\n".join(raw[:MAX_DIFF_LINES])
    if len(raw) > MAX_DIFF_LINES:
        patch += "\n... diff truncated ..."
    added = sum(1 for line in raw if line.startswith("+")
                and not line.startswith("+++"))
    removed = sum(1 for line in raw if line.startswith("-")
                  and not line.startswith("---"))
    ratio = difflib.SequenceMatcher(None, old or "", new or "").ratio()
    return patch, {
        "changed": old != new,
        "added_lines": added,
        "removed_lines": removed,
        "similarity": round(ratio, 4),
    }


def _download_attachments(
    database: str,
    html: str,
    official_url: str,
    target_dir: str,
    max_attachments: int,
    binary_loader,
) -> list[dict]:
    rows = []
    links = attachment_links(html, official_url)
    for index, link in enumerate(links[:max_attachments], 1):
        name = _safe_file_name(
            urllib.parse.urlsplit(link).path,
            f"attachment-{index}{_attachment_extension(link)}",
        )
        if _attachment_extension(link) and not name.lower().endswith(
                _attachment_extension(link)):
            name += _attachment_extension(link)
        stored = os.path.join(target_dir, f"{index:02d}-{name}")
        row = {
            "index": index,
            "url": link,
            "file": _relative(database, stored),
            "sha256": "",
            "bytes": 0,
            "chars": 0,
            "extraction": "",
            "error": "",
        }
        try:
            data = binary_loader(link)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"download:{type(exc).__name__}"
            rows.append(row)
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            row["error"] = "too_large"
            rows.append(row)
            continue
        _atomic_bytes(stored, data)
        text, source = _attachment_text_from_bytes(link, data)
        row.update({
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
            "chars": _dense_len(text),
            "extraction": source,
        })
        if text:
            text_path = stored + ".txt"
            _atomic_text(text_path, text)
            row["text_file"] = _relative(database, text_path)
        elif source.startswith("attachment:") and source != "attachment:unsupported":
            row["error"] = source
        rows.append(row)
    return rows


def _stage_one(
    database: str,
    scan: dict,
    candidate: dict,
    page_loader,
    binary_loader,
    max_attachments: int,
) -> dict:
    cid = candidate["id"]
    stem = _candidate_dir(database, scan, cid)
    attachment_dir = os.path.join(stem, "attachments")
    os.makedirs(attachment_dir, exist_ok=True)
    official_url = candidate.get("official_url") or candidate.get("url", "")
    candidate.update({
        "id": cid,
        "official_url": official_url,
        "review_status": candidate.get("review_status") or "pending",
        "workflow_status": "staging",
        "staged_at": _now(),
        "last_error": "",
    })
    if not _official_url(official_url):
        candidate["workflow_status"] = "stage_error"
        candidate["last_error"] = "official_url must use hainan.gov.cn"
        return candidate
    try:
        html = page_loader(official_url)
    except Exception as exc:  # noqa: BLE001
        candidate["workflow_status"] = "stage_error"
        candidate["last_error"] = f"page_download:{type(exc).__name__}"
        return candidate

    page_path = os.path.join(stem, "page.html")
    _atomic_text(page_path, html)
    page_hash = _sha256_text(html)
    previous_page_hash = candidate.get("page_sha256", "")
    if previous_page_hash and previous_page_hash != page_hash:
        _reset_review(candidate)
    meta = meta_fields(html)
    canonical_title = (meta.get("ArticleTitle") or "").strip()
    if canonical_title and canonical_title != candidate.get("title"):
        candidate.setdefault("listing_title", candidate.get("title", ""))
        candidate["title"] = canonical_title
    page_body, page_source = body_text_with_source(html, meta)
    attachments = _download_attachments(
        database,
        html,
        official_url,
        attachment_dir,
        max_attachments,
        binary_loader,
    )

    candidates_for_body = [(
        page_body,
        page_source,
        "",
    )]
    for row in attachments:
        text_file = row.get("text_file")
        if not text_file:
            continue
        text_path = os.path.join(database, text_file)
        text = open(text_path, encoding="utf-8", errors="ignore").read()
        candidates_for_body.append((text, row["extraction"], text_file))
    candidates_for_body.sort(key=lambda item: _dense_len(item[0]), reverse=True)
    body, extraction, extraction_file = candidates_for_body[0]
    body = _tidy_text(body)
    body_chars = _dense_len(body)
    if body_chars < MIN_BODY_CHARS or is_boilerplate(body):
        unsupported = [
            row for row in attachments
            if row.get("extraction") == "attachment:unsupported"
        ]
        candidate["workflow_status"] = "stage_error"
        candidate["last_error"] = (
            f"body_too_short:{body_chars}"
            + (":manual_attachment_review" if unsupported else "")
        )
        candidate["suggested_action"] = (
            "manual_attachment_review" if unsupported else "")
        candidate["page_sha256"] = page_hash
        candidate["attachment_files"] = attachments
        _atomic_json(os.path.join(stem, "snapshot.json"), candidate)
        return candidate

    body_hash = _sha256_text(body)
    previous_body_hash = candidate.get("body_sha256", "")
    if previous_body_hash and previous_body_hash != body_hash:
        _reset_review(candidate)
    body_path = os.path.join(stem, "body.txt")
    _atomic_text(body_path, body)
    baseline = _baseline_for(database, candidate)
    old_body = ""
    if baseline.get("file"):
        old_path = os.path.join(database, baseline["file"])
        if os.path.exists(old_path):
            old_body = open(old_path, encoding="utf-8", errors="ignore").read()
    patch, diff_stats = _diff_text(old_body, body)
    diff_path = os.path.join(stem, "body.diff")
    _atomic_text(diff_path, patch + ("\n" if patch else ""))

    if not baseline["exists"]:
        suggested = "publish_new"
    elif baseline.get("body_sha256") != body_hash:
        suggested = "replace_body"
    else:
        suggested = "record_only"

    candidate.update({
        "workflow_status": "staged",
        "last_error": "",
        "page_sha256": page_hash,
        "source_hash": page_hash,
        "body_sha256": body_hash,
        "body_chars": body_chars,
        "extraction": extraction,
        "extraction_file": extraction_file,
        "page_meta": {
            key: meta.get(key) for key in (
                "ArticleTitle", "PubDate", "ContentSource", "Keywords")
            if meta.get(key)
        },
        "page_file": _relative(database, page_path),
        "body_file": _relative(database, body_path),
        "diff_file": _relative(database, diff_path),
        "attachment_files": attachments,
        "attachment_count": len(attachments),
        "baseline": baseline,
        "body_changed": bool(baseline["exists"] and diff_stats["changed"]),
        "diff_stats": diff_stats,
        "suggested_action": suggested,
    })
    _atomic_json(os.path.join(stem, "snapshot.json"), candidate)
    return candidate


def _reset_review(candidate: dict) -> None:
    """A changed official snapshot cannot inherit an earlier approval."""
    previous_page = candidate.get("page_sha256", "")
    history = candidate.get("review_history") or []
    if previous_page:
        history.append({
            "decision": "invalidated",
            "reviewed_at": _now(),
            "evidence": "官方页面或正文快照发生变化，旧审核自动失效",
            "page_sha256": previous_page,
            "body_sha256": candidate.get("body_sha256", ""),
        })
    candidate.update({
        "review_status": "pending",
        "workflow_status": "staged",
        "verified_by": "",
        "verified_at": "",
        "reviewed_by": "",
        "reviewed_at": "",
        "evidence": "",
        "review_notes": "",
        "action": "",
        "review_history": history,
    })


def stage_candidates(
    database: str = DEFAULT_DB,
    scan_id: str = "",
    candidate_ids: list[str] | None = None,
    max_attachments: int = 5,
    page_loader=fetch,
    binary_loader=fetch_bytes,
    delay: float = 0.2,
) -> dict:
    """Fetch official evidence into the workflow area without policy mutation."""
    ledger = _load_ledger(database)
    scan = _latest_scan(ledger, scan_id)
    wanted = set(candidate_ids or [])
    selected = []
    for candidate in scan.get("candidates") or []:
        candidate["id"] = candidate.get("id") or candidate_id(
            candidate.get("url") or candidate.get("official_url", ""),
            candidate.get("title", ""),
        )
        if not wanted or candidate["id"] in wanted:
            selected.append(candidate)
    found = {candidate["id"] for candidate in selected}
    missing = wanted - found
    if missing:
        raise ValueError(f"未知候选：{', '.join(sorted(missing))}")

    results = []
    for index, candidate in enumerate(selected, 1):
        results.append(_stage_one(
            database,
            scan,
            candidate,
            page_loader,
            binary_loader,
            max_attachments,
        ))
        _save_ledger(database, ledger)
        if index < len(selected) and delay:
            time.sleep(delay)
    mark_duplicate_bodies(scan)
    _save_ledger(database, ledger)
    write_review_queue(database, scan)
    return {
        "scan_id": scan.get("scan_id"),
        "selected": len(selected),
        "staged": sum(1 for row in results
                      if row.get("workflow_status") == "staged"),
        "errors": sum(1 for row in results
                      if row.get("workflow_status") == "stage_error"),
        "duplicates": sum(1 for row in results if row.get("duplicate_of")),
        "candidates": results,
    }


def mark_duplicate_bodies(scan: dict) -> None:
    """Flag one official body reachable through several listing URLs."""
    groups: dict[str, list[dict]] = {}
    for candidate in scan.get("candidates") or []:
        digest = candidate.get("body_sha256", "")
        if candidate.get("workflow_status") == "staged" and digest:
            groups.setdefault(digest, []).append(candidate)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: (
            0 if "flfgxzgfxwj" in row.get("official_url", "") else 1,
            row.get("official_url", ""),
        ))
        primary = rows[0]
        for duplicate in rows[1:]:
            duplicate["duplicate_of"] = primary["id"]
            duplicate["duplicate_reason"] = (
                "正文哈希相同，同一官方文件通过多个栏目 URL 重复列出")


def _review_row(candidate: dict, scan: dict) -> dict:
    return {
        "candidate_id": candidate.get("id", ""),
        "scan_id": scan.get("scan_id", ""),
        "kind": candidate.get("kind", ""),
        "title": candidate.get("title", ""),
        "channel": candidate.get("channel", ""),
        "official_url": candidate.get("official_url")
        or candidate.get("url", ""),
        "detected_at": candidate.get("detected_at", ""),
        "page_sha256": candidate.get("page_sha256", ""),
        "body_sha256": candidate.get("body_sha256", ""),
        "body_chars": candidate.get("body_chars", 0),
        "extraction": candidate.get("extraction", ""),
        "attachment_count": candidate.get("attachment_count", 0),
        "baseline_file": (candidate.get("baseline") or {}).get("file", ""),
        "body_changed": candidate.get("body_changed", ""),
        "suggested_action": candidate.get("suggested_action", ""),
        "duplicate_of": candidate.get("duplicate_of", ""),
        "duplicate_reason": candidate.get("duplicate_reason", ""),
        "review_status": candidate.get("review_status", "pending"),
        "reviewed_by": candidate.get("verified_by")
        or candidate.get("reviewed_by", ""),
        "reviewed_at": candidate.get("verified_at")
        or candidate.get("reviewed_at", ""),
        "evidence": candidate.get("evidence", ""),
        "notes": candidate.get("review_notes", ""),
        "action": candidate.get("action", ""),
        "staged_at": candidate.get("staged_at", ""),
    }


def _read_review_queue(database: str) -> dict[str, dict]:
    path = os.path.join(database, REVIEW_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return {
                row.get("candidate_id", ""): row
                for row in csv.DictReader(handle)
                if row.get("candidate_id")
            }
    except (OSError, csv.Error):
        return {}


def write_review_queue(database: str, scan: dict | None = None) -> str:
    """Write the business review queue while preserving matching reviews."""
    if scan is None:
        scan = _latest_scan(_load_ledger(database))
    old_rows = _read_review_queue(database)
    rows = []
    review_keys = {
        "review_status", "reviewed_by", "reviewed_at", "evidence", "notes",
        "action",
    }
    for candidate in scan.get("candidates") or []:
        candidate["id"] = candidate.get("id") or candidate_id(
            candidate.get("url") or candidate.get("official_url", ""),
            candidate.get("title", ""),
        )
        fresh = _review_row(candidate, scan)
        old = old_rows.get(candidate["id"], {})
        same_snapshot = (
            old.get("page_sha256", "") == fresh["page_sha256"]
            and old.get("body_sha256", "") == fresh["body_sha256"]
        )
        if same_snapshot:
            for key in review_keys:
                if old.get(key):
                    fresh[key] = old[key]
        rows.append(fresh)
    path = os.path.join(database, REVIEW_NAME)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)
    return path


def record_review(
    database: str,
    cid: str,
    decision: str,
    reviewed_by: str,
    evidence: str,
    action: str = "",
    notes: str = "",
    scan_id: str = "",
) -> dict:
    """Record a human review. This function never publishes a change."""
    decision = (decision or "").strip().lower()
    action = (action or "").strip().lower()
    reviewed_by = (reviewed_by or "").strip()
    evidence = re.sub(r"\s+", " ", evidence or "").strip()
    if decision not in {"verified", "rejected", "hold"}:
        raise ValueError("decision must be verified, rejected, or hold")
    if len(reviewed_by) < 2:
        raise ValueError("必须记录核对人")
    if len(evidence) < 8:
        raise ValueError("必须记录至少 8 个字符的官方证据或拒绝理由")

    ledger = _load_ledger(database)
    scan = _latest_scan(ledger, scan_id)
    candidate = _candidate_in_scan(scan, cid)
    if candidate.get("workflow_status") not in {"staged", "verified"}:
        raise ValueError("候选必须先成功 stage，不能直接审核")
    if not candidate.get("body_file") or not candidate.get("body_sha256"):
        raise ValueError("候选缺少已取证的正文快照")
    if not _official_url(candidate.get("official_url", "")):
        raise ValueError("候选缺少 hainan.gov.cn 官方链接")
    if decision == "verified":
        if candidate.get("duplicate_of"):
            raise ValueError(
                "重复正文不能作为新文件发布；请核对重复关系后选择 rejected")
        if action not in PUBLISH_ACTIONS:
            raise ValueError(
                "verified 动作必须是 publish_new、replace_body 或 record_only")
        if action == "publish_new" and (candidate.get("baseline") or {}).get(
                "exists"):
            raise ValueError("已有同源文件，publish_new 不适用")
        if action == "replace_body" and not (candidate.get("baseline") or {}).get(
                "exists"):
            raise ValueError("没有现有正文，replace_body 不适用")
    else:
        action = "reject" if decision == "rejected" else "hold"

    previous = candidate.get("review_history") or []
    review = {
        "decision": decision,
        "action": action,
        "reviewed_by": reviewed_by,
        "reviewed_at": _now(),
        "evidence": evidence,
        "notes": notes.strip(),
        "page_sha256": candidate.get("page_sha256", ""),
        "body_sha256": candidate.get("body_sha256", ""),
    }
    previous.append(review)
    candidate.update({
        "review_status": (
            "verified" if decision == "verified"
            else "rejected" if decision == "rejected"
            else "pending"
        ),
        "workflow_status": (
            "verified" if decision == "verified"
            else "rejected" if decision == "rejected"
            else "staged"
        ),
        "verified_by": reviewed_by if decision == "verified" else "",
        "verified_at": review["reviewed_at"]
        if decision == "verified" else "",
        "reviewed_by": reviewed_by,
        "reviewed_at": review["reviewed_at"],
        "evidence": evidence,
        "review_notes": notes.strip(),
        "action": action,
        "review_history": previous,
    })
    _save_ledger(database, ledger)
    write_review_queue(database, scan)
    return candidate


def import_review_csv(database: str, path: str = "") -> dict:
    """Apply completed rows from ``update_review.csv`` through the same gate."""
    path = path or os.path.join(database, REVIEW_NAME)
    if not os.path.exists(path):
        raise ValueError(f"找不到审核队列：{path}")
    applied, skipped, errors = [], [], []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        cid = (row.get("candidate_id") or "").strip()
        status = (row.get("review_status") or "").strip().lower()
        if not cid or status in {"", "pending", "staged", "stage_error",
                                 "published"}:
            continue
        aliases = {
            "approved": "verified",
            "通过": "verified",
            "拒绝": "rejected",
            "暂缓": "hold",
        }
        status = aliases.get(status, status)
        if status not in {"verified", "rejected", "hold"}:
            errors.append({"candidate_id": cid, "error": f"未知状态 {status}"})
            continue
        ledger = _load_ledger(database)
        candidate = _candidate_in_scan(_latest_scan(ledger), cid)
        evidence = (row.get("evidence") or "").strip()
        reviewed_by = (
            row.get("reviewed_by") or row.get("verified_by") or ""
        ).strip()
        action = (row.get("action") or candidate.get("suggested_action")
                  or "").strip().lower()
        if (candidate.get("review_status") == status
                and candidate.get("evidence") == evidence
                and candidate.get("action") == action):
            skipped.append(cid)
            continue
        try:
            record_review(
                database,
                cid,
                status,
                reviewed_by,
                evidence,
                action,
                (row.get("notes") or "").strip(),
                row.get("scan_id", ""),
            )
        except (ValueError, RuntimeError) as exc:
            errors.append({"candidate_id": cid, "error": str(exc)})
            continue
        applied.append(cid)
    return {
        "path": path,
        "rows": len(rows),
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
    }


@contextlib.contextmanager
def publish_lock(database: str):
    """Cross-process lock so two publish commands cannot interleave."""
    path = os.path.join(database, LOCK_NAME)
    token = _sha256_bytes(os.urandom(24))[:16]
    os.makedirs(database, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("已有发布任务正在运行，请勿并发发布") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({
                "token": token,
                "pid": os.getpid(),
                "started_at": _now(),
            }, handle, ensure_ascii=False)
        yield token
    finally:
        try:
            current = _load_json(path, {})
        except Exception:  # noqa: BLE001
            current = {}
        if current.get("token") == token:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)


def is_publish_in_progress(database: str) -> bool:
    return os.path.exists(os.path.join(database, LOCK_NAME))


def _mutable_files() -> list[str]:
    return [
        LEDGER_NAME,
        REVIEW_NAME,
        PUBLISH_LOG,
        "_all_documents.json",
        "_repeal_notices.json",
        "_repeal_index.json",
        "_embedded_repeals.json",
        "_article_force_index.json",
        "_temporal_index.json",
        "_retrieval_index.pkl",
        "_force_trees.json",
        "_force_status.json",
    ]


def _backup_mutable(database: str, target: str) -> dict[str, str]:
    mapping = {}
    for name in _mutable_files():
        source = os.path.join(database, name)
        if not os.path.exists(source):
            continue
        backup = os.path.join(target, name)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copy2(source, backup)
        mapping[name] = backup
    return mapping


def _restore_mutable(database: str, mapping: dict[str, str]) -> None:
    for name in _mutable_files():
        target = os.path.join(database, name)
        backup = mapping.get(name)
        if backup and os.path.exists(backup):
            temp = target + ".rollback"
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(backup, temp)
            os.replace(temp, target)
        elif os.path.exists(target):
            os.remove(target)


def _merge_catalog(
    database: str,
    candidate: dict,
    action: str,
) -> tuple[dict, str]:
    path = os.path.join(database, "_all_documents.json")
    catalog = _load_json(path, [])
    if not isinstance(catalog, list):
        raise ValueError("_all_documents.json 不是列表")
    official_url = candidate.get("official_url") or candidate.get("url", "")
    same_url = next(
        (item for item in catalog if item.get("url") == official_url), None)
    if action == "publish_new":
        if same_url is not None:
            raise ValueError("官方 URL 已存在，不能以 publish_new 重复入库")
        item = {
            "channel": candidate.get("channel", "其他"),
            "page": candidate.get("page", 1),
            "title": candidate.get("title", ""),
            "url": official_url,
        }
        catalog.append(item)
    elif action == "replace_body":
        if same_url is None:
            raise ValueError("找不到待替换的官方 URL")
        item = dict(same_url)
    else:
        if same_url is None:
            raise ValueError("record_only 候选缺少现有目录项")
        item = dict(same_url)

    item.update({
        "source_page_sha256": candidate.get("page_sha256", ""),
        "source_body_sha256": candidate.get("body_sha256", ""),
        "source_checked_at": _now(),
        "source_verified_by": candidate.get("verified_by", ""),
        "source_evidence": candidate.get("evidence", ""),
        "published_version": f"V1.0-update-{_stamp()}",
    })
    if item.get("PubDate") is None and candidate.get("page_meta", {}).get(
            "PubDate"):
        item["PubDate"] = candidate["page_meta"]["PubDate"]
    if item.get("ContentSource") is None and candidate.get("page_meta", {}).get(
            "ContentSource"):
        item["ContentSource"] = candidate["page_meta"]["ContentSource"]
    _atomic_json(path, catalog)
    return item, official_url


def _target_body_path(database: str, item: dict) -> str:
    return docstore.cache_path(database, item)


def _merge_repeal_candidate(
    database: str,
    candidate: dict,
    catalog_item: dict,
) -> None:
    """Fold a verified repeal notice into the local repeal index offline."""
    if candidate.get("kind") != "repeal_or_invalidation_notice":
        return
    page_file = candidate.get("page_file")
    if not page_file:
        return
    path = os.path.join(database, page_file)
    if not os.path.exists(path):
        return
    import repeal

    html = open(path, encoding="utf-8", errors="ignore").read()
    record = repeal.parse_notice(candidate["official_url"], html)
    if not record.get("notice_title"):
        record["notice_title"] = candidate.get("title", "")
    if not any(word in record["notice_title"] for word in repeal.REPEAL_WORDS):
        return

    index_path = os.path.join(database, "_repeal_index.json")
    index = _load_json(index_path, {
        "notices": [],
        "corpus_affected": [],
        "rejected_pages": [],
        "failed_notices": [],
    })
    notices = [
        row for row in index.get("notices", [])
        if row.get("url") != candidate["official_url"]
    ]
    notices.append(record)
    corpus = repeal.corpus_index(database)
    affected = []
    for notice in notices:
        affected.extend(repeal.match_against_corpus(notice, corpus))
    index.update({
        "notices": notices,
        "corpus_affected": affected,
    })
    _atomic_json(index_path, index)

    notices_path = os.path.join(database, "_repeal_notices.json")
    notice_rows = _load_json(notices_path, [])
    notice_rows = [
        row for row in notice_rows
        if row.get("url") != candidate["official_url"]
    ]
    notice_rows.append({
        "channel": catalog_item.get("channel", candidate.get("channel")),
        "page": catalog_item.get("page", candidate.get("page", 1)),
        "title": catalog_item.get("title", candidate.get("title")),
        "url": candidate["official_url"],
    })
    _atomic_json(notices_path, notice_rows)


def refresh_indexes(database: str) -> dict:
    """Rebuild all derived artifacts used by retrieval, force and tree APIs."""
    import article_force
    import build_temporal_index
    import embedded_repeals
    import retrieval
    import tree_index
    import warn

    embedded_repeals.build_index(database)
    article_index = article_force.build_index(database)
    _atomic_json(
        os.path.join(database, article_force.INDEX_NAME), article_index)
    temporal_index = build_temporal_index.build(database)
    retrieval.get_index(database)
    tree = tree_index.build_store(database)
    force_status = warn.build_status(database)
    retrieval._INDEX_CACHE.clear()
    return {
        "retrieval_units": len(retrieval.get_index(database).articles),
        "temporal_documents": temporal_index.get("count", 0),
        "tree_documents": len(tree.get("docs", {})),
        "force_documents": len(force_status),
        "article_events": article_index.get("summary", {}).get("events", 0),
    }


def publish_candidate(
    database: str = DEFAULT_DB,
    cid: str = "",
    rebuild: bool = True,
    scan_id: str = "",
    index_rebuilder=refresh_indexes,
) -> dict:
    """Publish one verified candidate with backup and rollback semantics."""
    ledger = _load_ledger(database)
    scan = _latest_scan(ledger, scan_id)
    candidate = _candidate_in_scan(scan, cid)
    if candidate.get("workflow_status") != "verified":
        raise ValueError("候选尚未通过人工审核，拒绝发布")
    if candidate.get("review_status") != "verified":
        raise ValueError("候选审核状态不是 verified")
    action = candidate.get("action", "")
    if action not in PUBLISH_ACTIONS:
        raise ValueError("审核记录中的发布动作无效")
    if not _official_url(candidate.get("official_url", "")):
        raise ValueError("发布来源不是 hainan.gov.cn 官方链接")
    body_path = os.path.join(database, candidate.get("body_file", ""))
    if not candidate.get("body_file") or not os.path.exists(body_path):
        raise ValueError("候选正文快照不存在")
    body = open(body_path, encoding="utf-8", errors="ignore").read()
    if _sha256_text(body) != candidate.get("body_sha256"):
        raise ValueError("候选正文在审核后发生变化，必须重新审核")

    publication_id = f"{_stamp()}-{cid}"
    backup_dir = os.path.join(
        database, WORK_ROOT, "publications", publication_id)
    os.makedirs(backup_dir, exist_ok=True)
    body_target = ""
    body_existed = False
    catalog_item = {}

    with publish_lock(database):
        backups = _backup_mutable(database, backup_dir)
        try:
            if action in {"publish_new", "replace_body"}:
                catalog_item, _ = _merge_catalog(database, candidate, action)
                body_target = _target_body_path(database, catalog_item)
                if os.path.exists(body_target):
                    body_existed = True
                    backup_target = os.path.join(
                        backup_dir, "published-body.txt")
                    shutil.copy2(body_target, backup_target)
                    backups["__body__"] = backup_target
                _atomic_text(body_target, body)
                _merge_repeal_candidate(database, candidate, catalog_item)
            else:
                by_url, _ = _metadata_maps(database)
                catalog_item = by_url.get(
                    candidate.get("official_url", ""), {})
            index_summary = index_rebuilder(database) if rebuild else {}

            publication = {
                "publication_id": publication_id,
                "candidate_id": cid,
                "scan_id": scan.get("scan_id"),
                "title": candidate.get("title"),
                "official_url": candidate.get("official_url"),
                "action": action,
                "verified_by": candidate.get("verified_by"),
                "verified_at": candidate.get("verified_at"),
                "evidence": candidate.get("evidence"),
                "body_file": _relative(database, body_target)
                if body_target else "",
                "body_sha256": candidate.get("body_sha256"),
                "page_sha256": candidate.get("page_sha256"),
                "published_at": _now(),
                "indexes": index_summary,
                "backup_dir": _relative(database, backup_dir),
            }
            log_path = os.path.join(database, PUBLISH_LOG)
            publication_log = _load_json(
                log_path, {"version": 1, "publications": []})
            publication_log.setdefault("publications", []).append(publication)
            _atomic_json(log_path, publication_log)
            candidate.update({
                "workflow_status": "published",
                "review_status": "verified",
                "published_at": publication["published_at"],
                "published_version": f"V1.0-update-{publication_id}",
                "publication_id": publication_id,
                "published_body_sha256": candidate.get("body_sha256"),
                "index_summary": index_summary,
            })
            _save_ledger(database, ledger)
            write_review_queue(database, scan)
            return publication
        except Exception as exc:
            _restore_mutable(database, backups)
            if body_target:
                if body_existed and backups.get("__body__"):
                    temp = body_target + ".rollback"
                    shutil.copy2(backups["__body__"], temp)
                    os.replace(temp, body_target)
                elif not body_existed and os.path.exists(body_target):
                    os.remove(body_target)
            from retrieval import _INDEX_CACHE
            _INDEX_CACHE.clear()
            raise RuntimeError(
                f"发布失败，已回滚：{type(exc).__name__}: {exc}") from exc


def public_update_summary(
    database: str = DEFAULT_DB,
    limit: int = 6,
) -> dict:
    """Browser-safe update summary; never exposes local paths or reviewer data."""
    path = os.path.join(database, LEDGER_NAME)
    if not os.path.exists(path):
        return {
            "available": False,
            "scan_id": "",
            "pending": 0,
            "verified": 0,
            "published": 0,
            "items": [],
            "message": "尚无政策更新扫描记录",
        }
    ledger = _load_ledger(database)
    try:
        scan = _latest_scan(ledger)
    except ValueError:
        return {
            "available": False,
            "scan_id": "",
            "pending": 0,
            "verified": 0,
            "published": 0,
            "items": [],
            "message": "更新账本无有效扫描结果",
        }
    candidates = scan.get("candidates") or []
    counts = {
        "pending": sum(
            1 for row in candidates
            if row.get("workflow_status") in {"pending", "staged", "stage_error"}
            or row.get("review_status") == "pending"
        ),
        "verified": sum(
            1 for row in candidates
            if row.get("workflow_status") == "verified"
        ),
        "published": sum(
            1 for row in candidates
            if row.get("workflow_status") == "published"
        ),
    }
    kind_labels = {
        "new_document": "新增文件",
        "repeal_or_invalidation_notice": "废止或失效",
        "amendment_notice": "修改决定",
        "possible_new_version": "可能新版本",
        "title_changed": "标题变化",
    }
    sorted_rows = sorted(
        candidates,
        key=lambda row: (
            row.get("workflow_status") not in {"verified", "staged"},
            row.get("title", ""),
        ),
    )
    items = [{
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "kind": row.get("kind", ""),
        "kind_label": kind_labels.get(row.get("kind"), row.get("kind", "")),
        "status": row.get("workflow_status", "pending"),
        "official_url": row.get("official_url") or row.get("url", ""),
        "detected_at": row.get("detected_at", ""),
        "published_at": row.get("published_at", ""),
    } for row in sorted_rows[:max(0, limit)]]
    return {
        "available": True,
        "scan_id": scan.get("scan_id", ""),
        "detected_at": scan.get("started_at", ""),
        **counts,
        "items": items,
        "message": (
            f"已发现 {len(candidates)} 项更新候选；"
            f"待审核 {counts['pending']}，已发布 {counts['published']}。"
        ),
    }


def _print_list(database: str) -> None:
    ledger = _load_ledger(database)
    scan = _latest_scan(ledger)
    print(f"扫描 {scan.get('scan_id')}：{len(scan.get('candidates', []))} 个候选")
    for candidate in scan.get("candidates", []):
        cid = candidate.get("id") or candidate_id(
            candidate.get("url") or candidate.get("official_url", ""),
            candidate.get("title", ""),
        )
        status = candidate.get("workflow_status") or candidate.get(
            "review_status", "pending")
        print(f"  {cid}  [{status:10}] {candidate.get('title', '')[:52]}")


def selftest() -> None:
    """Offline end-to-end fixture for staging, review, publish and rollback."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="policy-update-selftest-") as root:
        os.makedirs(os.path.join(root, "library", "测试栏目"))
        old_url = "https://www.hainan.gov.cn/test/old.shtml"
        old_item = {
            "channel": "测试栏目",
            "page": 1,
            "title": "测试旧办法",
            "url": old_url,
        }
        _atomic_json(os.path.join(root, "_all_documents.json"), [old_item])
        old_path = docstore.cache_path(root, old_item)
        old_text = "测试旧办法\n第一条 旧正文。\n第二条 保留的条款。"
        _atomic_text(old_path, old_text)
        new_url = "https://www.hainan.gov.cn/test/new.shtml"
        replacement_url = "https://www.hainan.gov.cn/test/replacement.shtml"
        candidates = [
            {
                "title": "测试新办法",
                "url": new_url,
                "official_url": new_url,
                "channel": "测试栏目",
                "page": 1,
                "kind": "new_document",
                "review_status": "pending",
                "detected_at": _now(),
                "id": candidate_id(new_url, "测试新办法"),
            },
            {
                "title": "测试替换办法",
                "url": replacement_url,
                "official_url": replacement_url,
                "channel": "测试栏目",
                "page": 1,
                "kind": "possible_new_version",
                "review_status": "pending",
                "detected_at": _now(),
                "id": candidate_id(replacement_url, "测试替换办法"),
            },
        ]
        ledger = {
            "version": 1,
            "scans": [{
                "scan_id": "selftest",
                "started_at": _now(),
                "candidates": candidates,
            }],
            "latest": None,
        }
        ledger["latest"] = ledger["scans"][0]
        _save_ledger(root, ledger)

        def page_loader(url):
            body = (
                "第一条 这是官方发布的测试正文。第二条 正文长度足以通过"
                "自动提取门槛，并且包含明确的政策语句。三、测试附件用于"
                "验证下载哈希与正文来源记录。第四条 本文件用于离线验证"
                "扫描候选从发现、取证、人工审核到安全发布的完整流程，"
                "所有测试数据均为虚构内容，不改变真实政策语料和效力状态。"
                "第五条 发布流程完成前不得覆盖现行正文或切换检索索引，"
                "任何索引重建失败都必须恢复目录、正文和派生文件的原始状态。"
            )
            return (
                "<html><head>"
                '<meta name="ArticleTitle" content="测试新办法">'
                '<meta name="PubDate" content="2026-11-23 09:00:00">'
                "</head><body>"
                f"<p>{body}</p><p>{body}</p>"
                '<a href="/files/test.ofd">附件</a>'
                "</body></html>"
            )

        binary_loader = lambda url: b"%PDF-1.4 selftest attachment"
        result = stage_candidates(
            root,
            candidate_ids=[candidates[0]["id"]],
            page_loader=page_loader,
            binary_loader=binary_loader,
            delay=0,
        )
        staged = result["candidates"][0]
        assert result["staged"] == 1
        assert staged["body_sha256"]
        assert staged["page_sha256"]
        assert os.path.exists(os.path.join(root, REVIEW_NAME))
        assert len(_load_json(os.path.join(root, "_all_documents.json"), [])) == 1

        try:
            record_review(
                root, candidates[0]["id"], "verified", "测试审核员", "",
                "publish_new")
        except ValueError:
            pass
        else:
            raise AssertionError("review without evidence should fail")

        verified = record_review(
            root,
            candidates[0]["id"],
            "verified",
            "测试审核员",
            "官网页面明确载明本办法自发布之日起施行。",
            "publish_new",
        )
        assert verified["workflow_status"] == "verified"
        publication = publish_candidate(root, candidates[0]["id"], rebuild=False)
        assert publication["body_sha256"] == staged["body_sha256"]
        catalog = _load_json(os.path.join(root, "_all_documents.json"), [])
        assert len(catalog) == 2
        assert any(item["url"] == new_url for item in catalog)
        summary = public_update_summary(root)
        assert summary["published"] == 1
        assert "body_file" not in json.dumps(summary, ensure_ascii=False)

        stage_candidates(
            root,
            candidate_ids=[candidates[1]["id"]],
            page_loader=page_loader,
            binary_loader=binary_loader,
            delay=0,
        )
        record_review(
            root,
            candidates[1]["id"],
            "verified",
            "测试审核员",
            "官网页面明确载明本办法自发布之日起施行。",
            "publish_new",
        )
        catalog_before = open(
            os.path.join(root, "_all_documents.json"),
            encoding="utf-8",
        ).read()

        def fail_rebuild(database):
            raise RuntimeError("injected rebuild failure")

        try:
            publish_candidate(
                root,
                candidates[1]["id"],
                rebuild=True,
                index_rebuilder=fail_rebuild,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("injected rebuild failure should roll back")
        catalog_after = open(
            os.path.join(root, "_all_documents.json"),
            encoding="utf-8",
        ).read()
        assert catalog_after == catalog_before
        assert not is_publish_in_progress(root)
    print("通过  更新工作流：取证、审核闸门、发布与回滚")


def _command_stage(args) -> int:
    result = stage_candidates(
        args.database,
        scan_id=args.scan_id,
        candidate_ids=args.id or [],
        max_attachments=args.max_attachments,
        delay=args.delay,
    )
    print(
        f"取证 {result['staged']} 项，失败 {result['errors']} 项，"
        f"重复正文 {result['duplicates']} 项；"
        f"审核队列 → {os.path.join(args.database, REVIEW_NAME)}")
    for candidate in result["candidates"]:
        suffix = f"  {candidate.get('last_error')}" if candidate.get(
            "last_error") else ""
        print(
            f"  {candidate['id']} [{candidate['workflow_status']}] "
            f"{candidate['title'][:52]}{suffix}")
    return 0 if result["errors"] == 0 else 1


def _command_review(args) -> int:
    candidate = record_review(
        args.database,
        args.id,
        args.decision,
        args.by,
        args.evidence,
        args.action,
        args.notes,
        args.scan_id,
    )
    print(
        f"{candidate['id']} → {candidate['workflow_status']} "
        f"({candidate['action']})")
    return 0


def _command_publish(args) -> int:
    publication = publish_candidate(
        args.database,
        args.id,
        rebuild=not args.no_rebuild,
        scan_id=args.scan_id,
    )
    print(json.dumps(publication, ensure_ascii=False, indent=2))
    return 0


def _command_import_review(args) -> int:
    result = import_review_csv(args.database, args.file)
    print(
        f"审核队列 {result['rows']} 行：应用 {len(result['applied'])}，"
        f"跳过 {len(result['skipped'])}，错误 {len(result['errors'])}")
    for error in result["errors"]:
        print(f"  {error['candidate_id']}  {error['error']}", file=sys.stderr)
    return 0 if not result["errors"] else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="政策更新取证、审核与安全发布")
    parser.add_argument("--database", default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出最新扫描候选")
    list_parser.set_defaults(func=lambda args: (
        _print_list(args.database) or 0))

    stage_parser = subparsers.add_parser("stage", help="抓取官方页面和附件证据")
    stage_parser.add_argument("--scan-id", default="")
    stage_parser.add_argument("--id", action="append", default=[])
    stage_parser.add_argument("--max-attachments", type=int, default=5)
    stage_parser.add_argument("--delay", type=float, default=0.2)
    stage_parser.set_defaults(func=_command_stage)

    review_parser = subparsers.add_parser("review", help="记录人工补证决定")
    review_parser.add_argument("--scan-id", default="")
    review_parser.add_argument("--id", required=True)
    review_parser.add_argument(
        "--decision", required=True,
        choices=["verified", "rejected", "hold"])
    review_parser.add_argument("--by", required=True)
    review_parser.add_argument("--evidence", required=True)
    review_parser.add_argument("--action", default="")
    review_parser.add_argument("--notes", default="")
    review_parser.set_defaults(func=_command_review)

    import_parser = subparsers.add_parser(
        "import-review", help="导入已填写的 update_review.csv")
    import_parser.add_argument("--file", default="")
    import_parser.set_defaults(func=_command_import_review)

    publish_parser = subparsers.add_parser(
        "publish", help="发布已审核候选并重建索引")
    publish_parser.add_argument("--scan-id", default="")
    publish_parser.add_argument("--id", required=True)
    publish_parser.add_argument("--no-rebuild", action="store_true")
    publish_parser.set_defaults(func=_command_publish)

    selftest_parser = subparsers.add_parser("selftest")
    selftest_parser.set_defaults(
        func=lambda args: (selftest() or 0))

    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
