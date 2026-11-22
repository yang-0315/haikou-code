"""Attach a legal-force status to every document in the corpus.

Reads _repeal_index.json (produced by repeal.py) and decides, for each corpus
file, whether it is still in force, has been repealed as a whole, or belongs to
a series whose newest version was repealed. Version differences are reported
rather than hidden, because a corpus copy is often an earlier issue than the
one a repeal notice names.

    python warn.py [--database DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re

from paths import DEFAULT_DATABASE as DEFAULT_DB

IN_FORCE = "现行有效"
REPEALED = "已废止"
SERIES_REPEALED = "系列已废止"
QUESTIONABLE = "效力存疑"
UNKNOWN = "效力待核"
ARTICLE_REPEALED = "条文已废止"
ARTICLE_PARTIAL = "条文部分失效"

# A regulation from before this year, with no amendment in its masthead and no
# repeal record, is far more likely to have been quietly superseded than to
# still be in force: in the spot check, four of five such documents were dead.
OLD_YEAR = 1999
YEAR = re.compile(r"(19\d{2}|20\d{2})\s*年")
AMENDED = re.compile(r"(修改|修订|修正)")


def masthead_facts(text: str) -> dict:
    """Publication year and whether the masthead records any later amendment."""
    head = text[:300]
    years = [int(y) for y in YEAR.findall(head)]
    return {
        "year": min(years) if years else None,
        "amended": bool(AMENDED.search(head)),
    }


NEXT_STEPS = {
    "repealed": [
        "引用时改引现行有效文件；如需了解旧规内容，注明其已失效",
        "按依据链接核对废止范围（整件废止还是部分条款）",
    ],
    "series_repealed": [
        "核对本库这份的文号与被废止文号是否同一版本",
        "向发布机关确认该系列的最新版本",
    ],
    "questionable": [
        "查该主题是否已有新版规章或替代文件",
        "查国家法律法规数据库（flk.npc.gov.cn）确认效力状态",
        "向发布机关或业务主管部门确认",
    ],
    "in_force": [
        "可直接引用；若用于合规判断，建议再核对最新修订文本",
    ],
}


def _payload(status: str, confidence: str, reason_code: str,
             reason_text: str, evidence: dict, caveats: list[str],
             index: dict, extra_missing: list[str] | None = None) -> dict:
    """Every judgement carries why, what was checked, and what is missing."""
    checked = {
        "废止通知": len(index.get("notices", [])),
        "文件内废止条款": len(index.get("embedded", [])),
        "点名文件数": len({n for notice in index.get("notices", [])
                          + index.get("embedded", [])
                          for n in notice.get("repealed_documents", [])}),
    }
    missing = list(extra_missing or [])
    missing.append("本库未覆盖全部政策文件，也未见国家级法律与其他省级栏目")
    steps = NEXT_STEPS.get(reason_code, NEXT_STEPS["in_force"])
    return {
        "status": status,
        "confidence": confidence,
        "reason": {"code": reason_code, "text": reason_text},
        "checked": checked,
        "missing": missing,
        "next_steps": steps,
        "evidence": evidence,
        "caveats": caveats,
    }


def load_index(database: str) -> dict:
    path = os.path.join(database, "_repeal_index.json")
    if not os.path.exists(path):
        index = {"notices": [], "corpus_affected": []}
    else:
        index = json.load(open(path, encoding="utf-8"))

    # second source: repeal clauses written inside ordinary documents
    embedded_path = os.path.join(database, "_embedded_repeals.json")
    index["embedded"] = []
    if os.path.exists(embedded_path):
        for record in json.load(open(embedded_path, encoding="utf-8")):
            for name in record["repeals"]:
                index["embedded"].append({
                    "repealed_documents": [name],
                    "notice_title": (f"{record['source_title']}"
                                     "（该文件生效时同时废止）"),
                    # 来源文件名要带上：判断"这条记录是不是本文件自己的"靠它
                    "source_file": record.get("source_file"),
                    "source_title": record.get("source_title"),
                    "issuer": None,
                    "effective_date": record["effective_date"],
                    "scope": "whole",
                    "url": None,
                    # a clause names the repealed document, not its number;
                    # source_number belongs to the document doing the repealing
                    "repealed_numbers": [],
                    "source": "embedded_clause",
                })
    return index


def corpus_documents(database: str) -> list[dict]:
    docs = []
    for name in sorted(os.listdir(database)):
        if name.startswith("_") or not name.lower().endswith((".txt", ".md")):
            continue
        path = os.path.join(database, name)
        head = open(path, encoding="utf-8", errors="ignore").read(4000)
        numbers = own_numbers(head)
        docs.append({"file": name, "title": os.path.splitext(name)[0],
                     "doc_numbers": list(dict.fromkeys(numbers))})
    return docs


def own_numbers(text: str) -> list[str]:
    """The document's own 文号: the first one in its masthead.

    Later numbers in the body belong to documents it cites — a 废止决定 names
    the instrument it repeals, and treating those as its own numbers makes the
    decision look like the thing it repeals.
    """
    # 先压平空白：文号会被折行切开（"海南省人民\n政府令第329号"），
    # 正则里再多的 \s* 也接不住这种断法。PDF 抽取的正文尤其常见。
    head = re.sub(r"\s+", "", text[:300])
    found = re.findall(
        r"((?:琼府办|琼府|国办发|国发|财税)\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
        r"|海南省人民政府令第\s*\d+\s*号)", head)
    return [re.sub(r"\s+", "", found[0])] if found else []


# 只有文书类型、没有具体名称的词。正文里常见"（以下简称《条例》）"这类简称，
# 扫描器会把"条例"当成被废止的文件名；一旦进了索引，`n in title` 会让**每一份
# 标题含"条例"的文档**都被判成已废止（实测由此误判 200 多份）。
GENERIC_NAMES = {"条例", "办法", "规定", "细则", "通知", "决定", "意见",
                 "方案", "公告", "规则", "目录", "标准", "暂行条例",
                 "暂行办法", "暂行规定", "实施细则", "实施办法", "若干规定",
                 "若干意见", "工作条例", "管理条例"}
MIN_NAME_CHARS = 6


def is_usable_name(name: str) -> bool:
    """这份"文件名"能不能用来做匹配。

    太短或只有文书类型的一律不用：它们不是文件的名字，拿去做子串匹配会把
    半个语料误判成已废止。
    """
    clean = re.sub(r"[\s《》〈〉()（）]", "", name or "")
    if len(clean) < MIN_NAME_CHARS:
        return False
    return clean not in GENERIC_NAMES


def doc_key(name: str) -> str:
    """同一份文件的两种写法归一：文号、标点、空格、书名号，以及"（试行）"。

    与 `force_layer.doc_key` 同一份实现（那边从这里取），保证两处口径一致。
    """
    name = re.sub(r"[（(][^）)]{0,30}(?:〔[^〕]{0,20}〕|号)[^）)]{0,10}[)）]",
                  "", name or "")                    # 去掉尾部的文号括注
    return re.sub(r"[（(]\s*试行\s*[)）]|[\s_“”\"'、，,。．.\-—《》〈〉]",
                  "", name)


def name_strength(repealed_name: str, title: str) -> str:
    """这次名字匹配有多硬。

    `exact`      两边完全相同（去掉空格标点后）——就是它
    `contained`  废止名单里的名字被文件标题包着（"…关于印发《X》的通知"）
    `version`    归一后相同、但原文差着"（试行）"这类版本标记——同系列不同版本
    `superset`   我们的标题只是名字的一部分（对方多了"海南省"这类前缀）
    `none`       不相干

    只有 exact／contained 才够格判"已废止"；version／superset 只能判"系列已废止"，
    因为分不清本库这份是要废止的那一版，还是新的一版。
    """
    n_raw, t_raw = re.sub(r"\s+", "", repealed_name or ""), \
        re.sub(r"\s+", "", title or "")
    n_key, t_key = doc_key(n_raw), doc_key(t_raw)
    if not n_key or not t_key:
        return "none"
    if n_key == t_key:
        return "exact" if n_raw == t_raw else "version"
    if n_key in t_key:
        return "contained"
    if t_key in n_key:
        return "superset"
    return "none"


# A document that changes others' force. It is neither repealed by the
# documents it names nor a candidate for the 效力存疑 downgrade.
_INSTRUMENT = re.compile(
    r"(废止|失效|修改|修订)\s*[《〈]"
    r"|关于(废止|修改|修订)"
    r"|(废止|修改|修订)[^》〈]{0,30}?(办法|规定|条例|细则|规则|目录)")


def is_instrument(title: str) -> bool:
    clean = (title or "").replace(" ", "")
    return bool(_INSTRUMENT.search(clean))


def status_for(doc: dict, index: dict) -> dict:
    title = doc["title"].replace(" ", "")
    own_numbers = set(doc["doc_numbers"])
    # A document whose own title is "关于废止《X》的决定" is the instrument that
    # repeals X, not a document repealed by X. Name matching would flag it by
    # accident, because X appears in its title.
    # Such a document changes the force of OTHERS; it is not itself repealed
    # merely because their names appear in its title.
    is_repeal_instrument = is_instrument(title)
    for notice in index.get("notices", []) + index.get("embedded", []):
        # 文件不会废止自己。新版通知正文里那句"…2015 年公布的《同名办法》
        # （旧文号）同时废止"针对的是**上一版**，而手里这份是新版；把来源就是
        # 本文件的记录拿来判自己，会把现行文件判死（实测误判 4 份，含游艇、
        # 政府质量奖办法）。
        source_file = notice.get("source_file") or ""
        if source_file and doc_key(os.path.splitext(source_file)[0]) == doc_key(title):
            continue
        repealed_names = [n.replace(" ", "") for n in notice["repealed_documents"]
                          if is_usable_name(n)]
        name_hit = None
        strength = "none"
        if not is_repeal_instrument:
            for candidate in repealed_names:
                if len(title) < MIN_NAME_CHARS:
                    break
                strength = name_strength(candidate, title)
                if strength != "none":
                    name_hit = candidate
                    break
        repealed_numbers = set(notice.get("repealed_numbers") or [])
        number_hit = own_numbers & repealed_numbers
        if not name_hit and not number_hit:
            continue
        if is_repeal_instrument and not number_hit:
            continue
        # 只是"同系列不同版本"或"标题是名字的一部分"时，分不清本库这份是否在
        # 废止范围内，退回系列已废止（中置信度）——不硬判"已废止"
        weak_only = not number_hit and strength in ("version", "superset")

        evidence = {
            "repealed_by": notice["notice_title"],
            "issuer": notice["issuer"],
            "effective_date": notice["effective_date"],
            "scope": notice["scope"],
            "url": notice["url"],
        }
        if notice.get("source") == "embedded_clause":
            evidence["source"] = "被新文件生效时废止"
            if weak_only:
                evidence["matched_by"] = f"标题 {name_hit}"
                return _payload(
                    SERIES_REPEALED, "medium", "series_repealed",
                    f"同系列文件被点名（《{name_hit}》），"
                    f"但与本库这份标题只差版本标记或前缀，"
                    "无法确认本份是否在废止范围内",
                    evidence,
                    ["本库这份的标题与废止名单只差版本标记"
                     "（如「（试行）」）或前缀，请核对文号与版本"],
                    index,
                    extra_missing=["本库未收录该系列的全部版本"])
            return _payload(
                REPEALED, "high", "repealed",
                f"某文件生效时明文废止本文件（依据：{notice['notice_title']}，"
                f"{notice.get('effective_date') or '生效日期未识别'}）",
                evidence, [], index)
        if number_hit:
            evidence["matched_by"] = f"文号 {sorted(number_hit)[0]}"
            return _payload(
                REPEALED, "high", "repealed",
                f"本文件文号出现在废止依据中（{sorted(number_hit)[0]}），"
                f"依据为《{notice['notice_title']}》",
                evidence, [], index)

        # matched on name only: the notice may repeal a later issue than ours
        if weak_only:
            evidence["matched_by"] = f"标题 {name_hit}"
            return _payload(
                SERIES_REPEALED, "medium", "series_repealed",
                f"同名文件的系列已被废止（《{name_hit}》），"
                "但本库这份的标题与它只差版本标记或前缀，可能不是同一版本",
                evidence,
                ["在核实之前不应作为现行依据"],
                index,
                extra_missing=["本库未收录该系列的全部版本"])
        ours = sorted(own_numbers)
        theirs = sorted(repealed_numbers)
        caveats = []
        if theirs and ours:
            caveats.append(
                f"本库收录的文号是 {ours[0]}，废止通知点名的是 {theirs[0]}，"
                "两者不是同一版本；本库这份很可能是更早的版本，"
                "请核对是否还有更晚的版本")
        elif theirs:
            caveats.append(f"本库这份未标注文号，废止通知点名的是 {theirs[0]}")
        evidence["matched_by"] = f"标题 {name_hit}"
        return _payload(
            SERIES_REPEALED, "medium", "series_repealed",
            f"同名文件的系列已被废止（《{name_hit}》），"
            "但本库这份的文号与被点名的文号不同，可能不是同一版本",
            evidence, caveats, index,
            extra_missing=["本库未收录该系列的全部版本，无法确认本份是否在废止范围内"])

    facts = masthead_facts(doc.get("text") or "")
    year, amended = facts["year"], facts["amended"]
    if year and year <= OLD_YEAR and not amended and not is_repeal_instrument:
        age = 2026 - year
        return _payload(
            QUESTIONABLE, "medium", "questionable",
            f"{year} 年公布（距今约 {age} 年），文头无任何修订记录，"
            "本库已索引的废止依据中也未点名——"
            "这类老规章更常见的情形是被后续文件替代而非明文废止",
            {"note": "无废止依据，也无修订记录",
             "year": year, "amended": False},
            ["在核实之前不应作为现行依据；如被替代，其条文可能已被改写"],
            index,
            extra_missing=["未见该文件被明文废止的依据",
                           "未见后续修订版本或替代文件"])
    caveats = ["本库尚未覆盖全部废止文件，结论仅限已索引范围"]
    if amended:
        caveats.insert(0, "文头有修订记录，说明该文件经官方维护，"
                          "适用时以最新修订文本为准")
    reason = ("文头有修订记录，且本库已索引的废止依据中未点名，"
              "推定仍在施行" if amended else
              "本库已索引的废止依据中未点名")
    return _payload(IN_FORCE, "medium" if amended else "low", "in_force",
                    reason, {"note": reason}, caveats, index)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()

    index = load_index(args.database)
    docs = corpus_documents(args.database)
    print(f"语料 {len(docs)} 份，废止文件索引 {len(index.get('notices', []))} 份\n")

    result = {}
    for doc in docs:
        status = status_for(doc, index)
        result[doc["file"]] = {"title": doc["title"],
                               "doc_numbers": doc["doc_numbers"], **status}
        mark = {IN_FORCE: "○", REPEALED: "×", SERIES_REPEALED: "△",
                QUESTIONABLE: "?", UNKNOWN: "?"}.get(status["status"], "?")
        print(f"{mark} {doc['title'][:40]}")
        print(f"    状态: {status['status']}（置信度 {status['confidence']}）"
              f"  文号: {doc['doc_numbers'] or '未标注'}")
        for key in ("matched_by", "repealed_by", "effective_date", "scope"):
            if status["evidence"].get(key):
                print(f"    {key}: {status['evidence'][key]}")
        for caveat in status["caveats"]:
            print(f"    注意: {caveat}")
        print()

    path = os.path.join(args.database, "_force_status.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    counts = {}
    for item in result.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print("状态汇总:", counts)
    print("索引 →", path)


if __name__ == "__main__":
    main()
