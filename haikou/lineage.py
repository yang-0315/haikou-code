"""Build the version lineage of policy series, including missing versions.

A repealed version is usually deleted from the official site, so the corpus
will always have holes. The lineage keeps the NODE for every known version
(number, date, status) and marks whether its full text was obtained, so the
system can still answer "when was this repealed" while being honest about what
it cannot compare.

    python lineage.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re

from paths import DEFAULT_DATABASE as DEFAULT_DB

SERIES_NOISE = re.compile(r"(关于印发|的通知|的决定|（试行）|\(试行\)|修订|暂行)")
DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# Only a leading 印发/关于印发 wrapper marks a carrying notice; matching 《》
# anywhere mangles titles that merely quote another document.
WRAPPER = re.compile(
    r"^(?:海南省人民政府办公厅|海南省人民政府|中共海南省委办公厅)?"
    r"(?:关于)?印发[《〈]([^》〉]+)[》〉]")


def canonical_title(title: str) -> str:
    """A 通知 that merely carries a 办法 is not a separate version of it."""
    hit = WRAPPER.search(title)
    return hit.group(1) if hit else title


def series_key(title: str) -> str:
    """Documents of one policy family collapse to the same key."""
    clean = SERIES_NOISE.sub("", title.replace(" ", ""))
    clean = clean.replace("《", "").replace("》", "")
    clean = re.sub(r"(海南省人民政府办公厅|海南省人民政府|人民政府)", "", clean)
    return clean


def corpus_entries(database: str) -> list[dict]:
    """Every document we hold: the curated corpus plus the fetched library."""
    out = []
    paths = []
    for name in sorted(os.listdir(database)):
        if name.startswith("_") or not name.lower().endswith((".txt", ".md")):
            continue
        paths.append(os.path.join(database, name))
    for folder, _, files in os.walk(os.path.join(database, "library")):
        for name in sorted(files):
            if name.lower().endswith((".txt", ".md")):
                paths.append(os.path.join(folder, name))

    for path in paths:
        name = os.path.basename(path)
        text = open(path, encoding="utf-8", errors="ignore").read()
        head = text[:600]
        numbers = [re.sub(r"\s+", "", m) for m in re.findall(
            r"((?:琼府办|琼府|国办发|国发|财税)\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
            r"|海南省人民政府令第\s*\d+\s*号)", head)]
        dates = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in DATE.findall(head)]
        title = os.path.splitext(name)[0]
        # keep the most specific title: the one carrying the document number
        picked = title
        match = re.search(r"关于印发《([^》]{4,60})》|印发《([^》]{4,60})》", title)
        if match:
            picked = match.group(1) or match.group(2)
        out.append({
            "title": picked,
            "file": name,
            "path": os.path.relpath(path, database),
            "doc_numbers": list(dict.fromkeys(numbers)),
            "date": dates[0] if dates else None,
            "text_available": True,
        })
    return out


def repeal_entries(database: str) -> list[dict]:
    path = os.path.join(database, "_repeal_index.json")
    if not os.path.exists(path):
        return []
    index = json.load(open(path, encoding="utf-8"))
    out = []
    for notice in index.get("notices", []):
        names = notice["repealed_documents"]
        # a notice naming several documents cannot attribute one 文号 to each
        numbers = notice.get("repealed_numbers") or [] if len(names) == 1 else []
        for name in names:
            out.append({
                "title": name,
                "file": None,
                "doc_numbers": numbers,
                "date": None,
                "date_kind": "未知",
                "repealed_on": notice.get("effective_date"),
                "repeal_date": notice.get("effective_date"),
                "repealed_by": notice["notice_title"],
                "repealed_by_url": notice["url"],
                "text_available": False,
            })
    return out


def merge(corpus: list[dict], repeals: list[dict]) -> dict:
    """Versions come from documents; repeal records are events on them.

    A repeal record is not itself a version — it is attached to the version it
    names. Only when no version matches does it create a placeholder, which is
    the "the text is gone" case.
    """
    series: dict[str, dict] = {}

    for item in corpus:
        item = dict(item, kind="document",
                    title=canonical_title(item["title"]))
        key = series_key(item["title"])
        entry = series.setdefault(key, {"series": key, "versions": []})
        # the same document can appear in the corpus root and in the library
        for existing in entry["versions"]:
            same_title = existing["title"] == item["title"]
            same_number = bool(set(existing["doc_numbers"]) & set(item["doc_numbers"]))
            if same_title and (same_number or not item["doc_numbers"]):
                if item.get("file") and not existing.get("file"):
                    existing.update(item)
                break
        else:
            entry["versions"].append(item)

    for record in repeals:
        name = canonical_title(record["title"])
        key = series_key(name)
        entry = series.setdefault(key, {"series": key, "versions": []})
        target = None
        numbers = set(record.get("doc_numbers") or [])
        for version in entry["versions"]:
            if numbers:
                # the repeal names a specific issue: match on that, never on
                # the title alone, or a repeal of the 2024 issue would be
                # pinned onto the 2021 one
                if set(version["doc_numbers"]) & numbers:
                    target = version
                    break
            elif (name in version["title"]) or (version["title"] in name):
                target = version
                break
        if target is not None:
            target["repealed_by"] = record["repealed_by"]
            target["repeal_date"] = record["repeal_date"]
            target.setdefault("doc_numbers", [])
            continue
        entry["versions"].append({
            "title": (f"{name}（{record['doc_numbers'][0]}）"
                      if record.get("doc_numbers") else name),
            "file": None,
            "doc_numbers": record.get("doc_numbers") or [],
            "date": None, "text_available": False, "kind": "missing_version",
            "repealed_by": record["repealed_by"],
            "repeal_date": record["repeal_date"],
        })

    for entry in series.values():
        versions = entry["versions"]
        for version in versions:
            if version.get("repealed_by"):
                version["status"] = ("已废止" if version.get("file")
                                     else "已废止（原文未获取）")
            else:
                version["status"] = "现行有效"
        # order by publication date; a version with only a repeal date goes
        # last, because we know when it died but not when it was born
        versions.sort(key=lambda v: (v.get("date") is None,
                                     v.get("date") or v.get("repeal_date") or ""))
        with_text = [v for v in versions if v.get("file")]
        entry["summary"] = {
            "versions_known": len(versions),
            "texts_available": len(with_text),
            "texts_missing": len(versions) - len(with_text),
            "comparable": len(with_text) >= 2,
            "doc_numbers": sorted({n for v in versions
                                   for n in v.get("doc_numbers") or []}),
        }
    return series


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    corpus = corpus_entries(args.database)
    repeals = repeal_entries(args.database)
    series = merge(corpus, repeals)

    print(f"语料 {len(corpus)} 份，废止记录 {len(repeals)} 条，"
          f"归并为 {len(series)} 个政策系列\n")
    for entry in series.values():
        summary = entry["summary"]
        print(f"◆ {entry['series'][:48]}")
        print(f"    版本 {summary['versions_known']} 个，"
              f"有正文 {summary['texts_available']}，"
              f"缺正文 {summary['texts_missing']}")
        for version in entry["versions"]:
            mark = "有正文" if version.get("file") else "缺正文"
            number = (version["doc_numbers"] or ["未标注"])[0]
            if version.get("file"):
                date = f"发布 {version.get('date') or '未知'}"
            else:
                date = f"废止 {version.get('repeal_date') or '未知'}"
            print(f"      [{mark}] {number:<20}{date:<20}{version['status']}")
            print(f"              {version['title'][:56]}")
        print()

    path = os.path.join(args.database, "_version_lineage.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(list(series.values()), fh, ensure_ascii=False, indent=2)
    missing = sum(v["summary"]["texts_missing"] for v in series.values())
    print(f"合计缺失正文 {missing} 份（节点保留，正文标记为未获取）")
    print("谱系 →", path)


if __name__ == "__main__":
    main()
