"""Extract a document's own official number without indexing cited numbers."""
from __future__ import annotations

import re

HAINAN = re.compile(
    r"(?P<number>(?:琼府办|琼府|国办发|国发|财税)"
    r"\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
    r"|海南省人民政府令第\s*\d+\s*号)")
GOVERNMENT_ORDER = re.compile(
    r"(?P<number>(?:[\u4e00-\u9fff]{2,8}(?:省|市|县|自治县|自治区))?"
    r"人民政府令第\s*\d+\s*号)")
YEAR_CODE = re.compile(
    r"(?P<prefix>[\u4e00-\u9fff]{1,12})"
    r"(?P<open>〔|\[|［|（|\()\s*(?P<year>20\d{2})\s*"
    r"(?P<close>〕|\]|］|）|\))\s*(?P<seq>\d{1,5})\s*号")
ANNOUNCEMENT = re.compile(
    r"(?P<announcement>[\u4e00-\u9fff]{2,24}公告)"
    r"\s*(?:[（(]\s*(?P<year>20\d{2})\s*[）)])?"
    r"\s*第\s*(?P<seq>\d{1,5})\s*号")

AGENCY_END = set("府办厅局委部院会所站中心发函省市区县")
TITLE_WORDS = (
    "通知", "公告", "决定", "规定", "办法", "意见", "方案", "细则",
    "批复", "函", "报告", "纪要",
)


def _canonical(number: str) -> str:
    value = re.sub(r"\s+", "", number)
    return (value.replace("[", "〔").replace("]", "〕")
            .replace("［", "〔").replace("］", "〕"))


def _trim_prefix(prefix: str) -> str:
    value = prefix
    for word in TITLE_WORDS:
        position = value.rfind(word)
        if position >= 0:
            value = value[position + len(word):]
    value = value.lstrip("的于关于")
    if 2 <= len(value) <= 10 and value[-1] in AGENCY_END:
        return value
    for size in range(2, len(value) + 1):
        candidate = value[-size:]
        if candidate[-1] in AGENCY_END:
            return candidate
    return ""


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = None
    for match in re.finditer(r"[《〈》〉]", text):
        if match.group(0) in "《〈":
            start = match.start()
        elif start is not None:
            spans.append((start, match.end()))
            start = None
    return spans


def _inside_quotes(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _line_bounds(text: str, position: int) -> tuple[int, int]:
    return text.rfind("\n", 0, position) + 1, text.find("\n", position)


def _citation_context(text: str, position: int) -> bool:
    before = text[max(0, position - 90):position]
    if re.search(r"《[^》]{0,100}[》〉]\s*[（(][^）)]{0,40}$", before):
        return True
    if re.search(r"(根据|依据|按照|参照|引用|见)[^。；\n]{0,80}$", before):
        return True
    return False


def _issuer_from_title(title: str) -> str:
    match = re.match(
        r"\s*([\u4e00-\u9fff]{2,12}?(?:省|市|县|自治县|自治区))", title or "")
    return match.group(1) if match else ""


def _issuer_from_announcement(raw: str) -> str:
    value = re.sub(r"\s+", "", raw)
    if "日" in value:
        value = value.rsplit("日", 1)[-1]
    for marker in ("人民代表大会常务委员会公告",
                   "代表大会常务委员会公告",
                   "常务委员会公告",
                   "人民代表大会公告",
                   "人民政府公告"):
        if marker not in value:
            continue
        prefix = value.split(marker, 1)[0]
        cleaned = _trim_prefix(prefix)
        if cleaned and cleaned[-1] in "省市县区":
            return cleaned + marker
        for suffix in ("自治县", "自治区", "省", "市", "县"):
            position = prefix.rfind(suffix)
            if position < 1:
                continue
            length = 8 if suffix in {"自治县", "自治区"} else 3
            region = prefix[max(0, position + len(suffix) - length):
                            position + len(suffix)]
            return region + marker
    return re.split(r"\d{1,2}日", value)[-1] or "公告机关"


def _clean_order(number: str) -> str:
    value = re.sub(r"\s+", "", number)
    if "日" in value:
        value = value.rsplit("日", 1)[-1]
    marker = value.rfind("人民政府令")
    if marker > 0:
        head = value[:marker]
        for suffix in ("省", "市", "自治县", "县", "自治区"):
            position = head.rfind(suffix)
            if position >= 0:
                value = head[max(0, position - 5):marker] + value[marker:]
                break
    return value


def candidates(text: str, title: str = "") -> list[dict]:
    text = text or ""
    if not title:
        title = next(
            (line.strip() for line in text.splitlines() if line.strip()), "")
    spans = _quoted_spans(text)
    out = []
    for pattern, kind in ((HAINAN, "provincial"), (GOVERNMENT_ORDER, "order")):
        for match in pattern.finditer(text[:1800]):
            number = match.group("number")
            if kind == "order":
                number = _clean_order(number)
            out.append({
                "number": number,
                "kind": kind,
                "position": match.start(),
                "confidence": "high",
            })
    for match in YEAR_CODE.finditer(text[:1800]):
        prefix = _trim_prefix(match.group("prefix"))
        if not prefix:
            continue
        position = match.start("prefix") + len(match.group("prefix")) - len(prefix)
        number = prefix + match.group("open") + match.group("year") \
            + match.group("close") + match.group("seq") + "号"
        line_start, line_end = _line_bounds(text, position)
        line = text[line_start:line_end if line_end >= 0 else len(text)]
        score = 2
        if line.strip().startswith(prefix):
            score += 3
        if len(re.sub(r"\s+", "", line)) <= 80:
            score += 2
        if re.search(r"(公布|发布|印发|文号)", text[match.end():match.end() + 30]):
            score += 2
        out.append({
            "number": number,
            "kind": "year_code",
            "position": position,
            "confidence": "high" if score >= 6 else "medium",
            "score": score,
        })
    head = text[:900]
    flat_head = re.sub(r"\s+", "", head)
    if "公告" in flat_head:
        for match in ANNOUNCEMENT.finditer(flat_head):
            issuer = _issuer_from_title(title)
            raw_issuer = re.sub(r"\s+", "", match.group("announcement"))
            if issuer and "人民代表大会" in head:
                issuer += "人民代表大会常务委员会公告"
            elif issuer and "人民政府" in head:
                issuer += "人民政府公告"
            else:
                issuer = _issuer_from_announcement(raw_issuer)
            year = match.group("year")
            number = issuer + (f"({year})" if year else "") \
                + f"第{int(match.group('seq'))}号"
            out.append({
                "number": number,
                "kind": "announcement",
                "position": 0,
                "confidence": "high",
                "score": 8,
            })
    filtered = []
    for row in out:
        position = row["position"]
        if _inside_quotes(position, spans) or _citation_context(text, position):
            continue
        row["canonical"] = _canonical(row["number"])
        filtered.append(row)
    filtered.sort(key=lambda row: (
        -row.get("score", 10 if row["confidence"] == "high" else 5),
        row["position"],
    ))
    return filtered


def extract(text: str, title: str = "") -> dict | None:
    found = candidates(text, title)
    if not found:
        # PDF OCR can split a number as "第32\n9号".  A second pass over the
        # masthead without whitespace recovers that mechanical failure while
        # keeping the original citation exclusion rules.
        flattened = re.sub(r"\s+", "", (text or "")[:2000])
        found = candidates(flattened, title)
        if found:
            text = flattened
    if not found:
        return None
    best = found[0]
    return {
        "number": best["canonical"],
        "raw": best["number"],
        "kind": best["kind"],
        "confidence": best["confidence"],
        "position": best["position"],
        "evidence": text[max(0, best["position"] - 70):
                         best["position"] + 120].replace("\n", " ").strip(),
    }


def own_numbers(text: str, title: str = "") -> list[str]:
    found = extract(text, title)
    return [found["number"]] if found else []
