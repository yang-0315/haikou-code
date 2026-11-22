"""Point-in-time legal force without pretending that every date is known.

The live force layer answers "is it in force now".  A historical question needs
three facts instead:

  * when the document existed / took effect;
  * whether a repeal event happened before the requested point;
  * whether an event falls inside a requested month or year.

This module only changes the force payload.  It does not invent a second set of
reasoning rules: the same force protocol receives an ``as_of`` context and the
retrieval backends consume the adjusted status.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

from warn import IN_FORCE, QUESTIONABLE, REPEALED, SERIES_REPEALED

DATE_RE = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*[年./-]\s*"
    r"(?P<m>\d{1,2})\s*[月./-]\s*(?P<d>\d{1,2})\s*日?")
MONTH_RE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*年\s*(?P<m>\d{1,2})\s*月")
YEAR_RE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*年")
ISO_RE = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?!\d)")
TIME_CUE = re.compile(
    r"(时|当时|截至|截止|此前|之际|以前|算不算数|是否有效|是否施行|效力|"
    r"有效吗|能否适用|可以适用)")


def _date(year: int, month: int, day: int) -> dt.date:
    try:
        return dt.date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ValueError(f"无效日期：{year}-{month}-{day}") from exc


def _point(start: dt.date, end: dt.date, precision: str,
           source: str) -> dict:
    today = dt.date.today()
    if start > today:
        raise ValueError("不能查询未来时点；请给出今天或之前的日期")
    clipped = end > today
    if clipped:
        end = today
    date = end.isoformat()
    label = {
        "day": date,
        "month": f"{start.year}年{start.month}月",
        "year": f"{start.year}年",
    }[precision]
    return {
        "as_of": date,
        "label": label,
        "source": source,
        "precision": precision,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "exact_day": precision == "day",
        "period_clipped_at_today": clipped,
    }


def parse_as_of(value) -> dict:
    """Parse an explicit historical point into an auditable interval.

    A day is exact.  A month or year is evaluated at the end of that period;
    callers must still demote the answer if an effective/repeal event falls
    inside the interval, because the source does not identify a day.
    """
    if isinstance(value, dict):
        if value.get("as_of") and value.get("start") and value.get("end"):
            return dict(value)
        value = value.get("as_of") or value.get("date")
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return _point(value, value, "day", "explicit")

    text = str(value or "").strip()
    if not text:
        raise ValueError("时点不能为空")
    match = ISO_RE.search(text) or DATE_RE.search(text)
    if match:
        day = _date(match.group("y"), match.group("m"), match.group("d"))
        return _point(day, day, "day", text)
    match = MONTH_RE.search(text)
    if match:
        year, month = int(match.group("y")), int(match.group("m"))
        end_day = calendar.monthrange(year, month)[1]
        start = _date(year, month, 1)
        end = _date(year, month, end_day)
        return _point(start, end, "month", text)
    match = YEAR_RE.search(text)
    if match:
        year = int(match.group("y"))
        return _point(_date(year, 1, 1), _date(year, 12, 31),
                      "year", text)
    raise ValueError(f"无法识别时点：{text!r}（示例：2022-09-01、2022年9月、2022年）")


def find_as_of(query: str) -> dict | None:
    """Find a historical date only when the wording asks about that time.

    A bare date in a title (for example "2021年办法") is not a time query.  A
    cue such as 时／截至／是否有效 must be nearby, which keeps ordinary policy
    questions unchanged.
    """
    candidates = []
    candidates += list(DATE_RE.finditer(query or ""))
    candidates += list(MONTH_RE.finditer(query or ""))
    candidates += list(YEAR_RE.finditer(query or ""))
    candidates.sort(key=lambda match: (match.start(), -len(match.group(0))))
    occupied = -1
    for match in candidates:
        if match.start() < occupied:
            continue
        around = query[max(0, match.start() - 18):match.end() + 12]
        if not TIME_CUE.search(around):
            continue
        try:
            return parse_as_of(match.group(0))
        except ValueError:
            continue
        finally:
            occupied = match.end()
    return None


def parse_source_dates(text: str) -> dict:
    """Read the document's own masthead and implementation clause.

    ``document_date`` is the first date in the masthead, not a claim that the
    text is already in force: some instruments are published now and
    implemented later.  ``effective_date`` is only filled from an explicit
    implementation clause (or "自公布之日起施行").
    """
    text = text or ""
    head = text[:1800]
    found = []
    for match in DATE_RE.finditer(head):
        date = _date(match.group("y"), match.group("m"), match.group("d"))
        found.append((match, date))
    document_date = found[0][1] if found else None
    effective_date = None
    effective_basis = None
    for match, date in found:
        # The effective date is the date immediately attached to the
        # implementation words.  Looking for "施行" anywhere in the sentence
        # misattributes "2020年1月2日公布，自2020年3月1日起施行" to Jan 2.
        tail = head[match.end(): min(len(head), match.end() + 16)]
        if re.match(r"\s*(?:起\s*)?(?:正式\s*)?(?:施行|执行|生效)", tail):
            effective_date = date
            effective_basis = head[
                match.start(): min(len(head), match.end() + 16)
            ].splitlines()[0].strip()
            break
    if effective_date is None and document_date is not None and \
            re.search(r"自(?:发布|公布)之日起(?:施行|执行|生效)", head):
        effective_date = document_date
        effective_basis = "自公布之日起施行"
    return {
        "document_date": document_date.isoformat() if document_date else None,
        "effective_date": effective_date.isoformat() if effective_date else None,
        "effective_basis": effective_basis,
    }


def _repeal_date(base: dict) -> str | None:
    evidence = base.get("evidence") or {}
    return evidence.get("effective_date") or evidence.get("repeal_date")


def _unknown(base: dict, point: dict, verdict: str, text: str,
             missing: list[str], dates: dict | None = None) -> dict:
    evidence = dict(base.get("evidence") or {})
    evidence.update({"as_of": point["as_of"], "verdict": verdict,
                     "current_status": base.get("status")})
    reason = {
        "not_published": f"在 {point['label']} 时，文头日期晚于查询时点，"
                         "该文件尚不可用",
        "before_effective": f"文件已经公布，但在 {point['label']} 时"
                            "是否已到达施行日无法确认",
        "changed_within_period": f"查询区间 {point['start']} 至 {point['end']} "
                                 "跨过生效或废止日，期间内效力发生变化",
        "missing_effective": "本库识别到文件发布日期，但没有识别到明确的施行日期",
        "ambiguous_version": "这是系列文件中无法确认具体版本的一份，"
                             "不能按时点直接判定",
        "future_repeal_unknown": "文件现已被废止，但无法确认废止生效日",
    }.get(verdict, "时点效力证据不足")
    caveats = ["以下结论只针对查询时点，不是当前效力状态"]
    caveats += list(base.get("caveats") or [])
    return {
        "status": QUESTIONABLE,
        "confidence": "low",
        "reason": {"code": f"temporal_{verdict}", "text": reason},
        "checked": dict(base.get("checked") or {}),
        "missing": list(dict.fromkeys(list(base.get("missing") or [])
                                      + list(missing))),
        "next_steps": ["给出具体到日的时点，或核对官方公布页与施行条款"],
        "evidence": evidence,
        "caveats": list(dict.fromkeys(caveats)),
        "temporal": {**point, "verdict": verdict,
                     "document_dates": dates or parse_source_dates(text)},
    }


def status_at(base: dict, as_of, text: str = "",
              evidence: dict | None = None) -> dict:
    """Return force at a point, or a conservative uncertainty payload."""
    point = parse_as_of(as_of)
    start = dt.date.fromisoformat(point["start"])
    end = dt.date.fromisoformat(point["end"])
    dates = evidence or parse_source_dates(text)
    document_date = (dt.date.fromisoformat(dates["document_date"])
                     if dates["document_date"] else None)
    effective = (dt.date.fromisoformat(dates["effective_date"])
                 if dates["effective_date"] else None)
    published_text = dates.get("published_at") or dates.get("document_date")
    published = dt.date.fromisoformat(published_text) if published_text else None
    requires_effective = dates.get("requires_effective_date", True)
    repeal_text = _repeal_date(base)
    repeal = dt.date.fromisoformat(repeal_text) if repeal_text else None
    state = base.get("status")
    evidence = dict(base.get("evidence") or {})
    evidence.update({"as_of": point["as_of"],
                     "current_status": state,
                     "document_date": dates["document_date"],
                     "published_at": dates.get("published_at"),
                     "effective_date": dates["effective_date"],
                     "repeal_effective_date": repeal_text,
                     "instrument_kind": dates.get("instrument_kind"),
                     "requires_effective_date": requires_effective,
                     "coverage": dates.get("coverage")})

    if document_date and end < document_date:
        return _unknown(base, point, "not_published", text,
                        ["查询时点早于文头日期"], dates)
    if effective and end < effective:
        return _unknown(base, point, "before_effective", text,
                        ["文件尚未到明确施行日"], dates)
    if effective and start < effective <= end:
        return _unknown(base, point, "changed_within_period", text,
                        ["查询区间跨过施行日"], dates)
    if not requires_effective and published and end < published:
        return _unknown(base, point, "not_published", text,
                        ["查询时点早于文件发布日"], dates)
    if not requires_effective and published and start < published <= end:
        return _unknown(base, point, "changed_within_period", text,
                        ["查询区间跨过文件发布日"], dates)

    if state in (REPEALED, SERIES_REPEALED):
        if repeal is None:
            return _unknown(base, point, "future_repeal_unknown", text,
                            ["未识别废止生效日"], dates)
        if start < repeal <= end:
            return _unknown(base, point, "changed_within_period", text,
                            ["查询区间跨过废止生效日"], dates)
        if end < repeal:
            if state == SERIES_REPEALED:
                return _unknown(base, point, "ambiguous_version", text,
                                ["版本与被废止对象是否同一份尚未确认"], dates)
            if effective is None:
                if not requires_effective and published:
                    effective = published
                else:
                    return _unknown(base, point, "missing_effective", text,
                                    ["只有文头日期或废止日，未识别明确施行日"],
                                    dates)
            if start >= effective:
                result = dict(base)
                basis = (f"文件在 {effective.isoformat()} 起施行"
                         if requires_effective else
                         f"文件于 {effective.isoformat()} 发布，无独立施行日")
                result.update({
                    "status": IN_FORCE,
                    "confidence": "high",
                    "reason": {
                        "code": "temporal_in_force",
                        "text": f"在 {point['label']} 时，{basis}，"
                                f"对应的废止依据尚未于 {repeal.isoformat()} 生效",
                    },
                    "evidence": evidence,
                    "next_steps": [
                        "这只表示查询时点的状态；文件现已被废止，不能当作现行依据"
                    ],
                    "caveats": [
                        "时点有效，但当前状态为已废止",
                        *(["该文件按发布日期判断，缺少独立施行日"]
                          if not requires_effective else []),
                    ],
                    "temporal": {**point, "verdict": "in_force",
                                 "document_dates": dates},
                })
                return result
        result = dict(base)
        result.update({
            "evidence": evidence,
            "caveats": list(dict.fromkeys(
                ["以下结论只针对查询时点，不是当前效力状态"]
                + list(base.get("caveats") or []))),
            "temporal": {**point, "verdict": "repealed",
                         "document_dates": dates},
        })
        return result

    if state == IN_FORCE:
        if effective is None:
            if not requires_effective and published:
                effective = published
            else:
                return _unknown(base, point, "missing_effective", text,
                                ["未识别明确施行日"], dates)
        result = dict(base)
        basis = (f"文件在 {effective.isoformat()} 起施行"
                 if requires_effective else
                 f"文件于 {effective.isoformat()} 发布，无独立施行日")
        result.update({
            "status": IN_FORCE,
            "evidence": evidence,
            "reason": {
                "code": "temporal_in_force",
                "text": f"按文头证据，{basis}；"
                        f"在 {point['label']} 时，本库未见此前发生的废止事件",
            },
            "next_steps": ["如需精确到日，请指定具体日期"],
            "caveats": list(dict.fromkeys(
                ["时点结论只限本库已收录的废止依据"]
                + (["该文件按发布日期判断，缺少独立施行日"]
                   if not requires_effective else [])
                + list(base.get("caveats") or []))),
            "temporal": {**point, "verdict": "in_force",
                         "document_dates": dates},
        })
        return result

    return _unknown(base, point, "ambiguous_version", text,
                    ["当前状态为效力存疑，历史时点不能升级为确定有效"], dates)


def context(as_of) -> str:
    """One-line context shared by every prompt renderer."""
    if not as_of:
        return ""
    point = parse_as_of(as_of)
    precision = {"day": "具体日期", "month": "月份", "year": "年份"}[point["precision"]]
    return (f"查询时点：{point['label']}（按 {precision}，证据日按 "
            f"{point['as_of']} 判断）。以下“现行有效／已废止”均指该时点的状态，"
            "不是今天的状态；回答必须明确写出该时点，不得改写为“目前有效”。")


def period_key(as_of) -> str:
    point = parse_as_of(as_of)
    return f"{point['precision']}:{point['start']}:{point['end']}"
