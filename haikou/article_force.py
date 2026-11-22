"""Article-level legal-force events inside otherwise effective documents.

The existing force layer answers whether a whole instrument is in force.  This
module adds a separate, narrower question: did a later amendment decision
delete a specific article, or delete only a paragraph/item inside it?

Article-level state is deliberately kept separate from the document state:

* deleting 第X条 invalidates that article while the rest of the document may
  remain effective;
* deleting 第X条第Y款 means only part of the article is no longer current;
* a republished text that already absorbed the amendment is not mapped using
  the old numbering, because later article numbers may have shifted.

The generated index is ``_article_force_index.json``. Runtime prefers the copy
in the corpus database and falls back to this module's directory.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import temporal
import temporal_evidence
from paths import DEFAULT_DATABASE
from warn import (ARTICLE_PARTIAL, ARTICLE_REPEALED, IN_FORCE, QUESTIONABLE,
                  doc_key)

INDEX_NAME = "_article_force_index.json"
INDEX_VERSION = 1

ARTICLE = re.compile(r"第([一二三四五六七八九十百零〇两\d]+)条")
DELETE = re.compile(r"(?:删去|删除)")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
TARGET_HEADING = re.compile(
    r"^\s*[一二三四五六七八九十百]+、\s*(?:对\s*)?《([^》]+)》"
    r"(?:\s*(?:作出修改|作如下修改|进行修改|修改)|$)", re.M)
QUOTED = re.compile(r"[《〈]([^》〉]{4,100})[》〉]")
OUTER_QUOTED = re.compile(r"《([^》]{4,200})》")
DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
EFFECTIVE_DECISION = re.compile(
    r"本决定自\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"\s*起施行")


def _canonical_article(value: str) -> str | None:
    match = ARTICLE.search(re.sub(r"\s+", "", value or ""))
    return f"第{match.group(1)}条" if match else None


def _cn_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return sum(CN_NUM.get(char, 0) for char in text)
    head, _, tail = text.partition("十")
    tens = CN_NUM.get(head, 1) if head else 1
    return tens * 10 + sum(CN_NUM.get(char, 0) for char in tail)


def article_number(article: str) -> int:
    match = ARTICLE.search(re.sub(r"\s+", "", article or ""))
    return _cn_int(match.group(1)) if match else 0


def _date_text(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def decision_effective_date(text: str) -> str | None:
    """Use the decision's commencement date; fall back to its head date."""
    flat = re.sub(r"\s+", "", text)
    explicit = EFFECTIVE_DECISION.search(flat)
    if explicit:
        return _date_text(*explicit.groups())
    if "自公布之日起施行" in flat:
        first = DATE.search(flat[:1200])
        if first:
            return _date_text(*first.groups())
    first = DATE.search(flat[:1200])
    return _date_text(*first.groups()) if first else None


def source_title(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if "关于" in line and any(word in line for word in
                                  ("修改", "修正", "修订")):
            if "已由" in line or "现予公布" in line or len(line) > 130:
                cuts = [position for marker in ("已由", "现予公布")
                        if (position := line.find(marker)) >= 0]
                title_part = line[:min(cuts)] if cuts else line
                if title_part.startswith("《") and "》" in title_part:
                    return re.sub(
                        r"\s+", "", title_part[1:title_part.rfind("》")])[:120]
                quoted = OUTER_QUOTED.search(line) or QUOTED.search(line)
                if quoted and any(word in quoted.group(1)
                                  for word in ("修改", "修正", "修订")):
                    return re.sub(r"\s+", "", quoted.group(1))[:120]
            return line[:120]
    return ""


def operative_region(text: str) -> str:
    """Return amendment clauses, excluding any appended republished full text."""
    stops = []
    for pattern in (r"本决定自", r"根据本决定作相应修改",
                    r"根据本决定[^。]{0,120}重新公布"):
        match = re.search(pattern, text)
        if match:
            stops.append(match.start())
    return text[:min(stops)] if stops else text


def target_sections(text: str) -> list[tuple[str, str]]:
    """Top-level target headings in a multi-instrument amendment decision."""
    matches = list(TARGET_HEADING.finditer(text))
    if not matches:
        return []
    out = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((re.sub(r"\s+", "", match.group(1)),
                    text[match.end():end]))
    return out


def targets_from_preamble(region: str, title: str) -> list[str]:
    head = region[:2500]
    match = re.search(
        r"决定(?:对)?\s*(.{0,500}?)\s*(?:作如下修改|作出修改|进行修改)", head)
    source = match.group(1) if match else title
    quoted_names = OUTER_QUOTED.findall(source) or QUOTED.findall(source)
    names = [re.sub(r"\s+", "", name) for name in quoted_names]
    return list(dict.fromkeys(name for name in names if len(name) >= 4))


def _partial_tail(tail: str) -> bool:
    # Text inside a quoted old article is payload, not a qualifier on whether
    # the whole article was deleted.  "将第三十八条“完整原文”删除" is a
    # whole-article deletion even if the quoted text mentions 第X款.
    tail = re.sub(r"[“\"][^”\"]{0,1000}[”\"]", "", tail)[:100]
    return bool(re.search(
        r"第[一二三四五六七八九十百零〇两\d]+(?:款|项|目|句)"
        r"|其中|中的|之中|第[一二三四五六七八九十百零〇两\d]+款|"
        r"第[一二三四五六七八九十百零〇两\d]+项", tail))


def extract_deletions(segment: str) -> list[dict]:
    """Extract whole-article and partial-article deletions from one target segment.

    A deletion is treated as whole-article only when the command names 第X条
    and does not narrow it with 款/项/目/中的.  This is the key guard against
    saying an entire article is gone when only one paragraph was removed.
    """
    flat = re.sub(r"\s+", "", segment)
    found: dict[tuple[str, str], dict] = {}

    def add(article: str, scope: str, sentence: str) -> None:
        article = _canonical_article(article)
        if not article:
            return
        rank = 0 if scope == "whole" else 1
        key = (article, scope)
        old = found.get(key)
        record = {
            "article": article,
            "scope": scope,
            "sentence": sentence[:360],
            "number": article_number(article),
        }
        if old is None or len(sentence) < len(old["sentence"]):
            found[key] = record

    for command in DELETE.finditer(flat):
        body = flat[command.end():command.end() + 360]
        stop = re.search(r"[。；]", body)
        if stop:
            body = body[:stop.start()]
        refs = list(ARTICLE.finditer(body))
        for index, ref in enumerate(refs):
            tail_end = refs[index + 1].start() if index + 1 < len(refs) else len(body)
            tail = body[ref.end():tail_end]
            add(ref.group(0), "partial" if _partial_tail(tail) else "whole",
                "删去" + body)

    # "将第X条……删除" and "第X条予以删除" put the verb after the reference.
    for match in re.finditer(
            r"(第[一二三四五六七八九十百零〇两\d]+条)"
            r"([^。；]{0,500}?)(?:予以)?(?:删除|删去)"
            r"([^。；]{0,100})", flat):
        body = match.group(2)
        after = match.group(3)
        add(match.group(1),
            "partial" if _partial_tail(body) or _partial_tail(after) else "whole",
            match.group(0))

    # Explicit partial force changes are also article-level events.
    for match in re.finditer(
            r"(第[一二三四五六七八九十百零〇两\d]+条)"
            r"([^。；]{0,80}?)(?:予以)?(?:废止|失效|停止执行)", flat):
        body = match.group(2)
        add(match.group(1), "partial" if _partial_tail(body) else "whole",
            match.group(0))

    return sorted(found.values(),
                  key=lambda row: (row["number"], row["scope"]))


def _record_for_source(path: str, text: str) -> list[dict]:
    title = source_title(text) or os.path.splitext(os.path.basename(path))[0]
    region = operative_region(text)
    sections = target_sections(region)
    if sections:
        targets = sections
    else:
        names = targets_from_preamble(region, title)
        if len(names) != 1:
            return []
        targets = [(names[0], region)]
    effective = decision_effective_date(text)
    records = []
    for target, segment in targets:
        changes = extract_deletions(segment)
        if not changes:
            continue
        sequence_adjusted = bool(re.search(
            r"(?:条文|条款)(?:序号|顺序).{0,20}(?:调整|顺延)"
            r"|序号.{0,12}(?:调整|顺延)", segment))
        records.append({
            "source_file": os.path.basename(path),
            "source_title": title,
            "target": target,
            "target_key": doc_key(target),
            "effective_date": effective,
            "sequence_adjusted": sequence_adjusted,
            "deleted_articles": [row["article"] for row in changes
                                 if row["scope"] == "whole"],
            "partial_articles": [row["article"] for row in changes
                                 if row["scope"] == "partial"],
            "changes": changes,
        })
    return records


def _corpus_documents(database: str) -> list[dict]:
    import docstore
    docs = []
    for path in docstore.corpus_files(database) + docstore.library_files(database):
        text = open(path, encoding="utf-8", errors="ignore").read()
        docs.append({
            "path": path,
            "relative": os.path.relpath(path, database).replace("\\", "/"),
            "file": os.path.basename(path),
            "title": os.path.splitext(os.path.basename(path))[0],
            "key": doc_key(os.path.splitext(os.path.basename(path))[0]),
            "head_key": doc_key(text[:2500]),
        })
    return docs


def build_index(database: str) -> dict:
    """Build article-level amendment events from the current corpus."""
    import docstore
    documents = _corpus_documents(database)
    by_key: dict[str, list[dict]] = defaultdict(list)
    for doc in documents:
        by_key[doc["key"]].append(doc)

    events = []
    rejected: Counter = Counter()
    paths = docstore.corpus_files(database) + docstore.library_files(database)
    for path in paths:
        title = os.path.splitext(os.path.basename(path))[0]
        text = open(path, encoding="utf-8", errors="ignore").read()
        if not re.search(r"(修改|修正|修订)", title):
            continue
        if not re.search(r"(决定|修正案|规定)", title):
            continue
        if "关于" not in title and "修改" not in text[:2500]:
            continue
        records = _record_for_source(path, text)
        if not records:
            rejected["无法唯一关联目标或没有删除条款"] += 1
            continue
        for record in records:
            matches = [doc for doc in by_key.get(record["target_key"], [])
                       if doc["file"] != record["source_file"]]
            consolidated, pre_change = [], []
            consolidated_paths, pre_change_paths = [], []
            source_key = doc_key(record["source_title"])
            for doc in matches:
                # The masthead of a republished text names the decision that
                # already changed it.  Its numbering is post-change, so old
                # article numbers from the decision must not be applied to it.
                if source_key and source_key in doc["head_key"]:
                    consolidated.append(doc["file"])
                    consolidated_paths.append(doc["relative"])
                else:
                    pre_change.append(doc["file"])
                    pre_change_paths.append(doc["relative"])
            record["pre_change_files"] = pre_change
            record["consolidated_files"] = consolidated
            record["pre_change_paths"] = pre_change_paths
            record["consolidated_paths"] = consolidated_paths
            events.append(record)
            if not matches:
                rejected["目标文件不在语料中"] += 1
            elif not pre_change:
                rejected["目标文本已吸收该次修改"] += 1

    fingerprint = temporal_evidence.corpus_fingerprint(database)
    return {
        "version": INDEX_VERSION,
        "database": os.path.abspath(database),
        "fingerprint": fingerprint,
        "events": events,
        "summary": {
            "sources": len({event["source_file"] for event in events}),
            "events": len(events),
            "targets": len({event["target_key"] for event in events}),
            "article_targets": sum(len(event["deleted_articles"])
                                   + len(event["partial_articles"])
                                   for event in events),
            "applicable_files": len({
                name for event in events for name in event["pre_change_files"]}),
            "consolidated_files": len({
                name for event in events for name in event["consolidated_files"]}),
            "notes": dict(rejected),
        },
    }


def index_is_current(index: dict | None, database: str) -> bool:
    return bool(index) and temporal_evidence.index_is_current(index, database)


def load_index(database: str) -> dict | None:
    for path in (os.path.join(database, INDEX_NAME),
                 os.path.join(HERE, INDEX_NAME)):
        if not os.path.exists(path):
            continue
        try:
            index = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if index_is_current(index, database):
            return index
    return None


class ArticleForceResolver:
    """Apply indexed article-level change events to a document-level status."""

    def __init__(self, index: dict | None):
        self.index = index or {}
        self.events: dict[str, list[dict]] = defaultdict(list)
        for event in self.index.get("events") or []:
            self.events[event.get("target_key", "")].append(event)

    def resolve(self, base: dict, title: str, article: str,
                file: str | None = None, as_of=None) -> dict:
        if not article:
            return base
        if base.get("status") not in (IN_FORCE, QUESTIONABLE):
            return base
        events = self.events.get(doc_key(title), [])
        if not events:
            return base
        point = temporal.parse_as_of(as_of) if as_of else None
        file_value = (file or "").replace("\\", "/")
        basename = os.path.basename(file_value)
        relevant = []
        for event in events:
            if file_value:
                consolidated_paths = event.get("consolidated_paths", [])
                pre_change_paths = event.get("pre_change_paths", [])
                exact_consolidated = any(
                    path == file_value or path.endswith("/" + file_value)
                    for path in consolidated_paths)
                exact_pre_change = any(
                    path == file_value or path.endswith("/" + file_value)
                    for path in pre_change_paths)
                if not exact_consolidated and not exact_pre_change:
                    exact_consolidated = basename in event.get(
                        "consolidated_files", [])
                    exact_pre_change = basename in event.get(
                        "pre_change_files", [])
                # A live PageIndex client may identify a document by a
                # synthetic id rather than its corpus filename.  The title
                # already matched this event; an opaque id must not disable
                # article-level force.
                if (pre_change_paths and not exact_consolidated
                        and not exact_pre_change
                        and basename not in event.get("pre_change_files", [])
                        and basename not in event.get("consolidated_files", [])):
                    exact_pre_change = True
                # The same basename in two corpus folders can be one pre-change
                # and one republished copy.  Without a stronger identity, do not
                # apply old article numbers to an ambiguous hit.
                if exact_consolidated and not exact_pre_change:
                    continue
                if exact_consolidated and exact_pre_change:
                    continue
                if pre_change_paths and not exact_pre_change:
                    continue
            scope = "whole" if article in event.get("deleted_articles", []) \
                else ("partial" if article in event.get("partial_articles", [])
                      else "")
            if scope:
                relevant.append((event, scope))
        if not relevant:
            return base

        applicable = []
        crosses = []
        unknown_dates = []
        for event, scope in relevant:
            date = event.get("effective_date")
            if not date:
                unknown_dates.append((event, scope))
                continue
            start, end = date, date
            if point:
                start, end = point["start"], point["end"]
                if date > end:
                    continue
                if date > start and not point.get("exact_day"):
                    crosses.append((event, scope))
                    continue
            applicable.append((event, scope))

        if crosses:
            event, scope = crosses[-1]
            return self._result(
                base, event, scope, QUESTIONABLE, "medium",
                f"查询区间跨越《{event['source_title']}》对{article}的变更日"
                f"（{event['effective_date']}）；需精确到日核实",
                ["当前查询时点跨过条文变更日，不能确定前后哪一侧适用"])
        if not applicable and unknown_dates:
            event, scope = unknown_dates[-1]
            return self._result(
                base, event, scope, QUESTIONABLE, "low",
                f"已有修改决定涉及{article}，但无法识别其生效日期",
                ["修改决定的生效日期未识别，需核对原件"])
        if not applicable:
            return base

        event, scope = applicable[-1]
        status = ARTICLE_REPEALED if scope == "whole" else ARTICLE_PARTIAL
        confidence = "high" if event.get("effective_date") else "medium"
        if scope == "whole":
            reason = (f"《{event['source_title']}》删去{article}"
                      f"（{event.get('effective_date') or '日期未识别'}）")
        else:
            reason = (f"《{event['source_title']}》删去或变更{article}中的"
                      f"部分款、项（{event.get('effective_date') or '日期未识别'}）")
        caveats = [
            "文档整体效力与单条状态不同：本文件其他条文仍可能有效"
            if scope == "whole" else
            "只删除或变更该条内部分内容，不能把原文本当作完整的现行条文"
        ]
        if event.get("sequence_adjusted"):
            caveats.append("该修改决定同时调整了后续条文顺序，引用时需核对现行编号")
        return self._result(base, event, scope, status, confidence, reason,
                            caveats)

    @staticmethod
    def _result(base: dict, event: dict, scope: str, status: str,
                confidence: str, reason: str,
                caveats: list[str]) -> dict:
        result = copy.deepcopy(base)
        event_object = {
            "source_title": event.get("source_title"),
            "source_file": event.get("source_file"),
            "effective_date": event.get("effective_date"),
            "article_scope": scope,
            "sentence": next(
                (change.get("sentence") for change in event.get("changes", [])
                 if change.get("article") in event.get(
                     "deleted_articles" if scope == "whole"
                     else "partial_articles", [])),
                ""),
        }
        result.update({
            "status": status,
            "confidence": confidence,
            "reason": {"code": f"article_{scope}", "text": reason},
            "evidence": {
                **copy.deepcopy(base.get("evidence") or {}),
                "article_change": event_object,
            },
            "caveats": list(dict.fromkeys(
                caveats + list(base.get("caveats") or []))),
            "next_steps": [
                f"核对《{event.get('source_title')}》原件",
                "按修改决定生效日区分修改前后的条文文本与编号",
            ],
            "missing": [
                "本库未保存修改前的每一版完整条文",
                "如现行文本已重新编号，不能只按旧条号对应",
            ],
            "article_force": True,
        })
        return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="打印条文级效力索引统计")
    parser.add_argument("--database",
                        default=DEFAULT_DATABASE)
    args = parser.parse_args()
    index = load_index(args.database)
    if not index:
        raise SystemExit("未找到且未通过语料指纹校验的条文效力索引")
    print(json.dumps(index.get("summary") or {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
