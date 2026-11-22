"""Find repeal clauses inside ordinary documents.

Not every repeal comes as a stand-alone decision. A new regulation usually
ends with "本条例自X日起施行。《旧条例》同时废止。" — so scanning the 附则 of
every document is the second, and easily missed, source of force changes.

    python embedded_repeals.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warn import doc_key, is_usable_name  # noqa: E402

from paths import DEFAULT_DATABASE as DEFAULT_DB

# The repeal clause: a document named in 《》 next to 同时废止 / 废止 / 予以废止,
# usually in the final article together with the commencement date.
CLAUSE = re.compile(
    r"[^。；]{0,80}?(?:自\s*)?(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)?[^。；]{0,20}?"
    r"(?:同时废止|予以废止|决定废止)[^。；]{0,40}")
# 书名号可以套一层：《海南省实施〈中华人民共和国车船税暂行条例〉办法》
NAMED = re.compile(r"《([^《》]*(?:《[^《》]*》[^《》]*)*)》")
EFFECTIVE = re.compile(r"自\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起")

VERB = re.compile(r"(同时废止|予以废止|决定废止|废止|失效|停止执行)")
BASIS_MARK = re.compile(r"(根据|依据|依照|按照|遵照|遵循|贯彻落实|为贯彻|"
                        r"为落实|基于|施行)")
# 目录与正文里的"第X章/第X条"标记：废止动词窗口不许越过它，否则目录里的
# "解释、修改、废止和清理"会把后面第一章的"根据《立法法》…"抓成被废止对象
STRUCTURE = re.compile(r"第[一二三四五六七八九十百\d]+(?:章|节|条|款|项)")
# "关于对《A》等16个规章的修改决定和关于对《B》等7个规章的废止决定"：
# A 是被修改的，不是被废止的。括号里的"（2015年修改）"不算，那只是沿革说明。
MODIFY_DECISION = re.compile(r"(修改决定|修正决定|修订决定)")
# 名字与动词之间允许的填充：文号、日期、顿号、"公布的/印发的"这类交代
FILLER_STOP = ("，", ",", "：", ":", "，")
SPAN = 60


def scan_text(text: str) -> list[str]:
    """Documents this text repeals, from its own repeal clause.

    窗口式取法两头都错：把依据书当对象（"为贯彻落实《公平竞争条例》…决定
    废止《农药…办法》"），又把文件名里的"废止"当动词（《国务院关于废止和
    修改部分行政法规的决定》后面的表格内容）。所以：按句切开、跳过书名号
    内部的动词、只认动词辖域内的书名号——动词之后 60 字内的，或紧邻动词
    之前、中间只隔文号日期之类的。
    """
    flat = re.sub(r"\s+", "", text).replace("〈", "《").replace("〉", "》")
    spans = [(m.start(), m.end()) for m in NAMED.finditer(flat)]

    def inside_title(position: int) -> bool:
        return any(start <= position < end for start, end in spans)

    found = []
    base = 0
    for sentence in re.split(r"[。；]", flat):
        names = [(m.start(), m.end(), m.group(1).strip())
                 for m in NAMED.finditer(sentence)]
        for verb in VERB.finditer(sentence):
            if inside_title(base + verb.start()):
                continue
            # 动词之后：60 字内的书名号都是它管的
            tail_start, tail_end = verb.end(), verb.end() + SPAN
            stop = STRUCTURE.search(sentence, verb.end())
            if stop:
                tail_end = min(tail_end, stop.start())
            for start, end, name in names:
                # 名称闸门：正文里的简称（"以下简称《条例》"）不是文件名，
                # 放进去会让所有含"条例"的文档被误判成已废止
                if (tail_start <= start <= tail_end and is_usable_name(name)
                        and name not in found):
                    found.append(name)
            # 动词之前：从最近的名字往回走，中间只许夹文号、日期、顿号
            limit = verb.start()
            for start, end, name in reversed(names):
                if end > verb.start():
                    continue
                gap = sentence[end:limit]
                if len(gap) > SPAN or any(ch in gap for ch in FILLER_STOP) \
                        or BASIS_MARK.search(gap) or VERB.search(gap) \
                        or MODIFY_DECISION.search(gap):
                    break
                if is_usable_name(name) and name not in found:
                    found.append(name)
                limit = start
        base += len(sentence) + 1
    return found


def effective_date_of(text: str) -> str | None:
    date = EFFECTIVE.search(re.sub(r"\s+", "", text))
    if not date:
        return None
    return (f"{date.group(1)}-{int(date.group(2)):02d}-"
            f"{int(date.group(3)):02d}")


def document_number_of(text: str) -> str | None:
    flat = re.sub(r"\s+", "", text)
    number = re.search(
        r"((?:琼府办|琼府)\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
        r"|海南省人民政府令第\s*\d+\s*号)", flat[:400])
    return number.group(1) if number else None


def scan_file(path: str) -> dict | None:
    text = open(path, encoding="utf-8", errors="ignore").read()
    title = os.path.splitext(os.path.basename(path))[0]
    # a document titled "关于废止《X》的决定" is covered by the notice crawl;
    # counting it here too would double-count the same repeal
    if re.search(r"(废止|失效)[《〈]", title) or title.startswith("关于废止"):
        return None
    found = scan_text(text)
    # 文件不会废止自己。正文写"（修订后印发）…2015 年公布的《同名办法》同时废止"
    # 时，被废止的是**上一版**，而手里这份是新版——把它记成被废止会把现行文件
    # 判死。同名即视为换版，不作为废止依据。
    found = [name for name in found if doc_key(name) != doc_key(title)]
    if not found:
        return None
    return {
        "source_file": os.path.basename(path),
        "source_title": title,
        "source_number": document_number_of(text),
        "effective_date": effective_date_of(text),
        "repeals": found,
    }


def record_in_index(database: str, record: dict) -> bool:
    """Merge one record into _embedded_repeals.json; True when it was new."""
    path = os.path.join(database, "_embedded_repeals.json")
    rows = []
    if os.path.exists(path):
        try:
            rows = json.load(open(path, encoding="utf-8"))
        except ValueError:
            rows = []
    if any(row.get("source_file") == record["source_file"]
           and row.get("repeals") == record["repeals"] for row in rows):
        return False
    rows = [row for row in rows if row.get("source_file") != record["source_file"]]
    rows.append(record)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--root", default="",
                        help="扫描根目录，默认扫描整个 database（含 library）")
    args = parser.parse_args()

    root = args.root or args.database
    records = []
    scanned = 0
    for folder, dirs, files in os.walk(root):
        # 剪枝，不只是跳过：_library_backup 这类目录下面还有一层普通目录名，
        # 光 continue 会继续往里走，把备份副本当成正文扫一遍（同一份文件两条记录）
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        if os.path.basename(folder).startswith("_"):
            continue
        for name in sorted(files):
            if name.startswith("_") or not name.lower().endswith((".txt", ".md")):
                continue
            scanned += 1
            record = scan_file(os.path.join(folder, name))
            if record:
                record["folder"] = os.path.relpath(folder, args.database)
                records.append(record)

    # 同名文件可能分布在多个栏目（library/<栏目>/同名.txt），按文件名去重，
    # 保留废止条款更全的那份
    best: dict[str, dict] = {}
    for record in records:
        key = record["source_file"]
        if key not in best or len(record["repeals"]) > len(best[key]["repeals"]):
            best[key] = record
    records = list(best.values())

    print(f"扫描 {scanned} 份文件，发现 {len(records)} 份自带废止条款\n")
    for record in records:
        print(f"◆ {record['source_title'][:44]}")
        print(f"    生效: {record['effective_date'] or '未识别'}"
              f"   文号: {record['source_number'] or '未标注'}")
        print(f"    废止: {record['repeals']}")

    path = os.path.join(args.database, "_embedded_repeals.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
