"""Extract and resolve auditable point-in-time evidence.

The force layer needs more than a pair of dates.  It needs to know which date
came from an implementation clause, which came from a publication statement,
what precision the evidence has, and what is still missing.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re

from temporal import DATE_RE, _date
from warn import doc_key, is_instrument

INDEX_NAME = "_temporal_index.json"
OVERRIDE_NAME = "_temporal_overrides.json"

KINDS = {
    "regulation": {"requires_effective_date": True, "label": "地方性法规"},
    "government_rule": {"requires_effective_date": True, "label": "规章／规范性文件"},
    "decision": {"requires_effective_date": True, "label": "决定"},
    "repeal_instrument": {"requires_effective_date": True, "label": "废止／修改决定"},
    "notice": {"requires_effective_date": False, "label": "通知／公告"},
    "plan": {"requires_effective_date": False, "label": "规划／方案"},
    "policy_measure": {"requires_effective_date": False, "label": "政策措施"},
    "unknown": {"requires_effective_date": True, "label": "未分类文件"},
}

_EFFECTIVE_TAIL = re.compile(
    r"\s*(?:起\s*)?(?:正式\s*)?(?:施行|执行|实施|生效)")
_SELF_RULE = re.compile(r"本条例|本办法|本规定|本决定|本细则|本实施方案|本规划")
_IMPLEMENT_RULE = re.compile(
    r"(?:自)?(?:发布|公布|印发|下发)之日起(?:施行|执行|实施|生效)"
    r"|自本(?:条例|办法|规定|决定|细则|规划|方案)"
    r"(?:发布|公布|印发)之日起(?:施行|执行|实施|生效)")
_EFFECTIVE_TO = re.compile(r"有效期(?:至|到)")


def classify_instrument(title: str, text: str = "") -> dict:
    """Classify by title first, then use a small content override for wrappers."""
    clean = (title or "").replace(" ", "")
    if is_instrument(clean):
        kind = "repeal_instrument"
    elif "条例" in clean:
        kind = "regulation"
    elif re.search(r"(决定|决议)", clean):
        kind = "decision"
    elif re.search(r"(规划|纲要|行动计划|实施方案|工作方案)", clean):
        kind = "plan"
    elif re.search(r"(实施细则|实施办法|管理办法|暂行规定|管理规定|.*办法$|.*规定$)", clean):
        kind = "government_rule"
    elif re.search(r"(通知|公告)", clean):
        kind = "notice"
    elif re.search(r"(意见|措施|若干举措)", clean):
        kind = "policy_measure"
    else:
        head = (text or "")[:500]
        if re.search(r"(通告|公告|通知)", head):
            kind = "notice"
        elif re.search(r"(行动计划|实施方案|工作方案|规划|纲要)", head):
            kind = "plan"
        elif re.search(r"(实施意见|若干措施|若干举措)", head):
            kind = "policy_measure"
        elif _IMPLEMENT_RULE.search(head) or re.search(r"第[一二三四五六七八九十]+条", head):
            kind = "government_rule"
        else:
            kind = "unknown"
    meta = KINDS[kind]
    return {"instrument_kind": kind, "instrument_label": meta["label"],
            "requires_effective_date": meta["requires_effective_date"]}


def _sentence(text: str, start: int, end: int, span: int = 180) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind("。", 0, start),
               text.rfind("；", 0, start), start - span)
    right_candidates = [
        pos for pos in (text.find("\n", end, end + span),
                        text.find("。", end, end + span),
                        text.find("；", end, end + span))
        if pos >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else min(len(text), end + span)
    return text[max(0, left + 1):right].strip()


def _date_candidates(text: str) -> list[dict]:
    out = []
    for match in DATE_RE.finditer(text or ""):
        try:
            value = _date(match.group("y"), match.group("m"), match.group("d"))
        except ValueError:
            continue
        sentence = _sentence(text, match.start(), match.end())
        tail = text[match.end(): min(len(text), match.end() + 18)]
        out.append({
            "date": value.isoformat(),
            "start": match.start(),
            "end": match.end(),
            "sentence": sentence,
            "tail": tail,
        })
    return out


def extract_evidence(title: str, text: str, url: str | None = None,
                     file: str | None = None) -> dict:
    """Build one document's audit record from its full text."""
    text = text or ""
    kind = classify_instrument(title, text)
    candidates = _date_candidates(text)
    events: list[dict] = []

    head_candidates = [row for row in candidates if row["start"] < 3000]
    document_date = head_candidates[0]["date"] if head_candidates else None
    if document_date:
        events.append({
            "kind": "document_date", "date": document_date,
            "source": "文头首个日期", "evidence": head_candidates[0]["sentence"][:220],
            "confidence": "medium",
        })

    publication = []
    adopted = []
    effective = []
    expiry = []
    for row in candidates:
        sentence = row["sentence"]
        if re.search(r"(公布|发布|公告)", sentence):
            score = 2 + int(bool(re.search(r"(《.*?》|条例|办法|规定|决定)", sentence)))
            if row["start"] > 3000:
                score -= 1
            publication.append((score, row))
        if re.search(r"(通过|批准)", sentence):
            adopted.append(row)
        if _EFFECTIVE_TAIL.match(row["tail"]) or \
                re.search(r"自\s*$", text[max(0, row["start"] - 4):row["start"]]):
            score = 3 + 2 * int(bool(_SELF_RULE.search(sentence)))
            score += 1 if row["start"] > len(text) // 2 else 0
            score += 1 if _EFFECTIVE_TO.search(sentence) else 0
            effective.append((score, row))
        if _EFFECTIVE_TO.search(sentence):
            expiry.append(row)

    def strongest(rows):
        if not rows:
            return None
        if isinstance(rows[0], tuple):
            return max(rows, key=lambda pair: (pair[0], pair[1]["start"]))[1]
        return rows[-1]

    published = strongest(publication)
    adopted_date = strongest(adopted)
    effective_row = strongest(effective)
    expiry_row = strongest(expiry)
    # PDF line wrapping often splits "公布之\n日起施行"; whitespace is not
    # semantic here and must not hide an explicit implementation rule.
    rule_without_day = bool(
        _IMPLEMENT_RULE.search(re.sub(r"\s+", "", text)))

    if published:
        events.append({
            "kind": "published", "date": published["date"],
            "source": "正文公布／发布语句", "evidence": published["sentence"][:220],
            "confidence": "high",
        })
    elif document_date:
        events.append({
            "kind": "published_fallback", "date": document_date,
            "source": "文头日期（未找到明确公布语句）",
            "evidence": head_candidates[0]["sentence"][:220],
            "confidence": "low",
        })
    if adopted_date:
        events.append({
            "kind": "adopted", "date": adopted_date["date"],
            "source": "通过／批准语句", "evidence": adopted_date["sentence"][:220],
            "confidence": "high",
        })
    if effective_row:
        events.append({
            "kind": "effective", "date": effective_row["date"],
            "source": "正文施行条款", "evidence": effective_row["sentence"][:220],
            "confidence": "high",
        })
    elif rule_without_day and (published or document_date):
        # "自公布之日起施行" is auditable when the same document supplies an
        # explicit date.  The masthead date is acceptable here because the
        # implementation rule itself points to publication/issuance.  A URL
        # month alone is never sufficient.
        basis_row = published or head_candidates[0]
        events.append({
            "kind": "effective", "date": basis_row["date"],
            "source": ("自公布之日起施行＋明确公布日" if published
                       else "自公布之日起施行＋文头日期"),
            "evidence": basis_row["sentence"][:220],
            "confidence": "high" if published else "medium",
        })
    if expiry_row:
        events.append({
            "kind": "expiry", "date": expiry_row["date"],
            "source": "正文有效期条款", "evidence": expiry_row["sentence"][:220],
            "confidence": "high",
        })

    effective_date = effective_row["date"] if effective_row else None
    published_date = published["date"] if published else None
    effective_basis = effective_row["sentence"][:220] if effective_row else None
    if not effective_date and rule_without_day and (published_date or document_date):
        effective_date = published_date or document_date
        effective_basis = (
            "自公布之日起施行；采用明确公布日" if published_date
            else "自公布之日起施行；采用文头日期")
    elif not effective_date and rule_without_day:
        effective_basis = "自公布／发布之日起施行，但未识别到可用公布日"

    url_month = None
    match = re.search(r"/(20\d{2})(\d{2})/", url or "")
    if match and 1 <= int(match.group(2)) <= 12:
        url_month = f"{match.group(1)}-{match.group(2)}"

    warnings = []
    if effective_date and document_date and effective_date < document_date:
        warnings.append("施行日早于文头首个日期，需核对文头日期是否实为会议日期")
    if published_date and document_date and published_date < document_date:
        warnings.append("公布日早于文头首个日期，需核对抽取上下文")

    record = {
        "title": title,
        "file": file,
        "url": url,
        **kind,
        "document_date": document_date,
        "published_at": published_date,
        "adopted_at": adopted_date["date"] if adopted_date else None,
        "effective_date": effective_date,
        "effective_basis": effective_basis,
        "effective_rule_without_day": rule_without_day,
        "expiry_date": expiry_row["date"] if expiry_row else None,
        "url_month": url_month,
        "coverage": None,
        "missing": [],
        "warnings": warnings,
        "date_evidence": events,
        "candidate_count": len(candidates),
    }
    return refresh_coverage(record)


def refresh_coverage(record: dict) -> dict:
    """Recompute missing fields and coverage after extraction or override."""
    missing = []
    if not record.get("document_date"):
        missing.append("文头日期")
    if record.get("requires_effective_date") and not record.get("effective_date"):
        missing.append("明确施行日")
    if record.get("requires_effective_date") and not record.get("published_at"):
        missing.append("明确公布日")

    if record.get("effective_date"):
        coverage = "confirmed_effective"
    elif record.get("requires_effective_date"):
        coverage = ("published_effective_missing"
                    if record.get("published_at") or record.get("document_date")
                    else "missing")
    elif record.get("published_at") or record.get("document_date"):
        coverage = "published_policy"
    else:
        coverage = "missing"
    record["missing"] = list(dict.fromkeys(missing))
    record["coverage"] = coverage
    return record


def apply_override(record: dict, override: dict) -> dict:
    """Apply a reviewed date correction without changing the source text."""
    record = dict(record)
    for field in ("document_date", "published_at", "effective_date",
                  "expiry_date"):
        value = override.get(field)
        if not value:
            continue
        _date(*map(int, value.split("-")))
        record[field] = value
        kind = {"document_date": "document_date",
                "published_at": "published",
                "effective_date": "effective",
                "expiry_date": "expiry"}[field]
        record.setdefault("date_evidence", []).append({
            "kind": kind,
            "date": value,
            "source": "人工核对覆盖",
            "evidence": override.get("evidence") or override.get("note") or "",
            "confidence": override.get("confidence", "high"),
            "confirmed_by": override.get("confirmed_by"),
            "url": override.get("url"),
        })
    if override.get("effective_basis"):
        record["effective_basis"] = override["effective_basis"]
    if override.get("instrument_kind") in KINDS:
        record["instrument_kind"] = override["instrument_kind"]
        meta = KINDS[override["instrument_kind"]]
        record["instrument_label"] = meta["label"]
        record["requires_effective_date"] = meta["requires_effective_date"]
    record["override"] = {
        "confirmed_by": override.get("confirmed_by"),
        "checked_at": override.get("checked_at"),
        "note": override.get("note"),
        "url": override.get("url"),
    }
    return refresh_coverage(record)


def status_dates(record: dict | None, fallback_text: str = "") -> dict:
    """Convert an evidence record to the compact shape used by status_at."""
    if not record:
        from temporal import parse_source_dates
        parsed = parse_source_dates(fallback_text)
        parsed.update({"instrument_kind": "unknown",
                       "requires_effective_date": True,
                       "coverage": "legacy_text_scan", "evidence": []})
        return parsed
    return {
        "document_date": record.get("document_date"),
        "published_at": record.get("published_at"),
        "effective_date": record.get("effective_date"),
        "effective_basis": record.get("effective_basis"),
        "expiry_date": record.get("expiry_date"),
        "instrument_kind": record.get("instrument_kind"),
        "instrument_label": record.get("instrument_label"),
        "requires_effective_date": record.get("requires_effective_date", True),
        "coverage": record.get("coverage"),
        "missing": record.get("missing", []),
        "warnings": record.get("warnings", []),
        "evidence": record.get("date_evidence", []),
    }


def locate(index: dict | None, title: str, file: str | None = None) -> dict | None:
    """Resolve a document record by exact file, then normalized title."""
    if not index:
        return None
    documents = index.get("documents") or {}
    if file:
        position = (index.get("by_file") or {}).get(os.path.basename(file))
        if position is not None:
            record = documents.get(str(position))
            if record:
                return record
    positions = (index.get("by_title") or {}).get(doc_key(title), [])
    if positions:
        return documents.get(str(positions[0])) if isinstance(documents, dict) \
            else documents[positions[0]]
    return None


def index_is_current(index: dict | None, database: str) -> bool:
    """Check count, size and mtime signature without reading document bodies."""
    if not index:
        return False
    current = corpus_fingerprint(database)
    stored = index.get("fingerprint") or {}
    return all(stored.get(key) == value for key, value in current.items())


def corpus_fingerprint(database: str) -> dict:
    """Stat-only corpus fingerprint; no text hashing on every runtime query."""
    import docstore
    paths = docstore.corpus_files(database) + docstore.library_files(database)
    digest = hashlib.sha256()
    total_bytes = 0
    for path in sorted(paths):
        stat = os.stat(path)
        total_bytes += stat.st_size
        relative = os.path.relpath(path, database).replace("\\", "/")
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
                      .encode("utf-8"))
    return {"files": len(paths), "total_bytes": total_bytes,
            "mtime_checksum": digest.hexdigest()}
