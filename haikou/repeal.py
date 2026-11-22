"""Detect which policy documents a repeal notice takes out of force.

hainan.gov.cn article pages carry machine-readable metadata:
    <meta name="ArticleTitle" content="...">
    <meta name="PubDate"      content="2026-09-11 16:38:01">
    <meta name="ContentSource" content="海南省人民政府办公厅">
    <meta name="Description"  content="...full notice text...">
Policy documents cite each other by 文号 (琼府办〔2026〕54号), which is a far
more precise key than the title, so both are extracted and matched.

    python repeal.py [--database DIR] [--notices FILE]
"""
from __future__ import annotations

import argparse
import gzip
import html as html_module
import json
import os
import re
import ssl
import time
import urllib.request

from warn import is_usable_name

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
from paths import DEFAULT_DATABASE as DEFAULT_DB

REPEAL_WORDS = ("废止", "失效")
ISSUERS = ("海南省人民政府办公厅", "海南省人民政府",
           "海南省人民代表大会常务委员会", "人民代表大会",
           "海南省人大常委会")

# 判"这份书名号是被废止的对象，还是废止它的依据"用的词表。
REPEAL_VERB = re.compile(r"(废止|失效|停止执行|不再执行)")
BASIS_MARK = re.compile(r"(根据|依据|依照|按照|遵照|遵循|符合|贯彻落实|"
                        r"为贯彻|为落实|基于|施行|执行)")
AFTER_VERB = re.compile(r"[^。；]{0,40}?(同时废止|废止|失效)")

# Site furniture that shows up when a page's real body could not be extracted.
# Such text must never be stored as a document.
BOILERPLATE = re.compile(
    r"(版权所有|网站标识码|ICP备|公网安备|无障碍阅读|无障碍浏览|网站地图|"
    r"法律声明|相关稿件|您访问的链接即将离开|政府网站运行管理中心|"
    r"主办：海南省人民政府办公厅)")


def is_boilerplate(text: str) -> bool:
    """True when the text is site furniture rather than a document body."""
    if not text:
        return True
    hits = len(BOILERPLATE.findall(text))
    if hits >= 3:
        return True
    return hits >= 2 and len(text) < 900

# document numbers used across Hainan policy files
DOC_NO = re.compile(
    r"((?:琼府办|琼府|国办发|国发|财税|海关总署公告|财政部\s*公告)"
    r"\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
    r"|海南省人民政府令第\s*\d+\s*号"
    r"|第\s*\d+\s*号公告)")


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,*/*",
                "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9"})
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=CTX) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "ignore")
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("fetch failed")


def fetch_bytes(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    """抓二进制（附件）。"""
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9"})
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=CTX) as response:
                return response.read()
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("fetch failed")


# 附件类链接：规划、方案、决定常以 PDF/Word 挂在页面上，页面正文只有抬头
ATTACHMENT = re.compile(
    r'(?:href|src)="([^"]+\.(?:pdf|docx?|ofd|wps))"', re.I)


def attachment_links(html: str, base_url: str = "") -> list[str]:
    """页面上的附件链接（绝对化、去重、保序）。"""
    from urllib.parse import urljoin
    out = []
    for raw in ATTACHMENT.findall(html):
        url = urljoin(base_url, html_module.unescape(raw))
        if url not in out:
            out.append(url)
    return out


def attachment_text(url: str) -> tuple[str, str]:
    """读附件正文：返回 (文本, 类型)。读不了就返回 ("", 原因)。"""
    import io
    data = fetch_bytes(url)
    lower = url.lower().split("?")[0]
    try:
        if lower.endswith(".pdf"):
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or ""
                             for page in reader.pages)
            return _tidy(text), "attachment:pdf"
        if lower.endswith(".docx"):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8",
                                                               "ignore")
            # 段落结束即换行，再剥标签
            xml = xml.replace("</w:p>", "\n")
            return _tidy(re.sub(r"<[^>]+>", "", xml)), "attachment:docx"
    except Exception as exc:                          # noqa: BLE001
        return "", f"attachment:{type(exc).__name__}"
    return "", "attachment:unsupported"


def _tidy(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return dedupe_lines("\n".join(l for l in lines if l and not BOILERPLATE.search(l)))


def dedupe_lines(text: str) -> str:
    """同一行只留一次。

    表格型文档（"综合行政执法事项指导目录"）每行都引用同一批条款，同一句话会
    重复上万次；不去重的话，语料与索引会被这几句话灌满。
    """
    out, seen = [], set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return "\n".join(out)


def meta_fields(html: str) -> dict:
    fields = {}
    for name, content in re.findall(
            r'<meta\s+name="([^"]+)"\s+content="([^"]*)"', html, re.I):
        fields.setdefault(name, html_module.unescape(content))
    return fields


MIN_BODY_CHARS = 500     # 段落正文到这个字数才算"抓到了正文"
CJK = re.compile(r"[\u4e00-\u9fff]")
CONTAINER = re.compile(r"<(div|td|section|article)([^>]*)>(.*?)</\1>",
                       re.S | re.I)


def paragraph_blocks(html: str) -> str:
    """<p> 段落拼出的正文，顺带去掉站内杂项。"""
    html_clean = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html_clean = re.sub(r"<style.*?</style>", " ", html_clean, flags=re.S)
    blocks = re.findall(r"<p[^>]*>(.*?)</p>", html_clean, re.S)
    text = "\n".join(re.sub(r"<[^>]+>", "", block) for block in blocks)
    for src, dst in (("&ensp;", " "), ("&nbsp;", " "), ("&ldquo;", "“"),
                     ("&rdquo;", "”"), ("&amp;", "&")):
        text = text.replace(src, dst)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    # 版权、主办单位、网站标识码这类页脚不进正文
    return html_module.unescape(
        "\n".join(line for line in lines if line and not BOILERPLATE.search(line)))


def container_blocks(html: str, min_chars: int = 40) -> str:
    """正文常常直接摆在无 class 的 <div> 里（一页拆成好几段），<p> 阶段取不到。

    只收"叶子块"：内部不含嵌套块、不是链接列表（导航）、以中文为主、达到最小
    长度、不带页脚词。这样既能把散落的正文段收齐，又不会把整页容器（含导航与
    脚本）一口吞进来。
    """
    html_clean = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html_clean = re.sub(r"<style.*?</style>", " ", html_clean, flags=re.S)
    out, seen = [], set()
    for match in CONTAINER.finditer(html_clean):
        inner = match.group(3)
        if re.search(r"<(div|p|table|ul|ol|script|style|iframe)\b", inner, re.I):
            continue
        if re.search(r"<(a|li)\b", inner, re.I):
            continue
        text = re.sub(r"\s+", " ", html_module.unescape(
            re.sub(r"<[^>]+>", "", inner))).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) < min_chars or BOILERPLATE.search(text):
            continue
        if len(CJK.findall(text)) / max(len(text), 1) < 0.5:
            continue
        key = text[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return "\n".join(out)


def body_text_with_source(html: str, meta: dict) -> tuple[str, str]:
    """返回 (正文, 来源)。来源是 `paragraphs`／`description` 之一（含短变体）。

    **段落优先。** 网站的 meta Description 写的是新闻导语或公告文头，它常常
    一百多字就"够长"，于是旧逻辑（Description 优先）把摘要当成正文存了下来——
    全库 812 份里有 603 份是单段短文本，实测同一页面：段落 4847 字、摘要 122 字。
    Description 现在只在段落抽不出东西时兜底，并且会把来源记进清单。
    """
    paragraphs = paragraph_blocks(html)
    containers = container_blocks(html)
    dense = len(re.sub(r"\s+", "", paragraphs))
    dense_containers = len(re.sub(r"\s+", "", containers))
    if dense_containers > dense:
        paragraphs, dense = containers, dense_containers
        kind = "containers"
    else:
        kind = "paragraphs"
    description = html_module.unescape(
        meta.get("Description") or meta.get("description") or "").strip()
    paragraphs = dedupe_lines(paragraphs)
    dense = len(re.sub(r"\s+", "", paragraphs))
    if dense >= MIN_BODY_CHARS:
        return paragraphs, kind
    if description and len(description) >= dense:
        return description, "description"
    if paragraphs:
        return paragraphs, kind + "_short"
    return description, "description_short"


def body_text(html: str, meta: dict) -> str:
    """正文文本（取法见 `body_text_with_source`）。"""
    return body_text_with_source(html, meta)[0]


ITEM_LINE = re.compile(r"^\s*(?:[一二三四五六七八九十百]+[、.．]|\d+[、.．])\s*(.+)$")


def listed_documents(text: str) -> list[str]:
    """Documents listed as 一、二、三、 items under a masthead sentence.

    A clean-up decision often repeals a dozen regulations at once; the names
    live in the list, not in the title. Items may or may not use 《》 and are
    usually followed by the original promulgation date in brackets.
    """
    out = []
    for line in text.splitlines():
        match = ITEM_LINE.match(line)
        if not match:
            continue
        item = match.group(1).strip()
        quoted = re.search(r"[《〈]([^》〉]{4,60})[》〉]", item)
        if quoted:
            name = quoted.group(1).strip()
        else:
            name = re.split(r"[（(]", item)[0].strip()
        name = re.sub(r"\s+", "", name)
        if 4 <= len(name) <= 60 and name not in out:
            out.append(name)
    return out


def governed_by_repeal(flat: str, start: int, end: int,
                       window: int = 300) -> bool:
    """这个书名号是被废止的对象，还是废止它的依据？

    废止决定里两类书名号混在同一句话里：

        为贯彻落实《海南自由贸易港公平竞争条例》以及 2023 年 10 月 1 日起
        施行的《海南经济特区农药管理若干规定》有关规定，省人民政府决定
        废止《海南经济特区农药批发零售经营许可管理办法》。

    只看书名号附近有没有"废止"二字，会把前两个依据书也当成被废止对象——
    实测把《海南自由贸易港公平竞争条例》误判成"系列已废止"。判据要看动词
    管辖：离书名号最近的动宾关系才是它。

      1. 前面最近的动词是废止，且中间没有"根据／依据／为贯彻落实／施行"
         这类依据标记 → 被废止（"决定废止《X》"）
      2. 后面紧跟"…同时废止"（"《X》《Y》同时废止"）→ 被废止
      3. 前面最近的是依据标记，或压根没有废止动词 → 是依据，不是对象
    """
    head = flat[max(0, start - window):start]
    # 只在本句里找动词：跨句会把上一句的"废止"算到这一句的书名号头上
    cut = max(head.rfind(ch) for ch in "。；！？")
    if cut != -1:
        head = head[cut + 1:]
    verb = max((m.start() for m in REPEAL_VERB.finditer(head)), default=None)
    mark = max((m.start() for m in BASIS_MARK.finditer(head)), default=None)
    if verb is not None and (mark is None or verb > mark):
        return True
    tail = flat[end:end + 48]
    for stop in ("。", "；"):
        cut_tail = tail.find(stop)
        if cut_tail != -1:
            tail = tail[:cut_tail]
    return bool(AFTER_VERB.match(tail))


def inline_series(text: str) -> list[str]:
    """Documents listed inside one sentence: 《A》(文号)、《B》(文号)…"""
    flat = text.replace("\n", "")
    out = []
    for match in re.finditer(
            r"[《〈]([^》〈]{4,80})[》〉]\s*[（(]\s*[^）)]{0,24}号\s*[)）]", flat):
        name = re.sub(r"\s+", "", match.group(1))
        # 带文号的引用同样可能是依据（"根据《X》（琼府〔2010〕5号）"），
        # 用同一套管辖判断过滤，别把依据当对象。
        if name and name not in out and governed_by_repeal(
                flat, match.start(), match.end()):
            out.append(name)
    return out


def document_numbers(text: str) -> list[str]:
    found = []
    for match in DOC_NO.finditer(text.replace("\n", "")):
        number = re.sub(r"\s+", "", match.group(1))
        if number not in found:
            found.append(number)
    return found


def named_documents(text: str, own_title: str) -> list[str]:
    """Documents a notice repeals, from its body and from its own title.

    Titles nest brackets ("关于废止〈X〉的决定"), and the body often quotes the
    notice's own title, so the innermost name is taken and anything that is
    part of the notice's own title is dropped. What survives must then be
    genuinely governed by a repeal verb — see `governed_by_repeal`, which is
    what keeps "为贯彻落实《X》…决定废止《Y》" from flagging X.
    """
    flat = text.replace("\n", "").replace("〈", "《").replace("〉", "》")
    own = re.sub(r"\s+", "", own_title).replace("〈", "《").replace("〉", "》")
    names = []
    for match in re.finditer(r"《([^《》]{4,60})》", flat):
        name = re.sub(r"\s+", "", match.group(1))
        if not name or name in names:
            continue
        if name == own or name in own or own in name:
            continue
        if not governed_by_repeal(flat, match.start(), match.end()):
            continue
        names.append(name)

    # 通知标题里的书名号同样要过管辖判断：一份"关于对《A》等16个规章的修改
    # 决定和关于对《B》等7个规章的废止决定"里，A 是修改对象，不是废止对象。
    for match in re.finditer(r"《([^《》]{4,60})》", own):
        name = re.sub(r"\s+", "", match.group(1))
        if (name and not name.startswith("关于") and name not in names
                and governed_by_repeal(own, match.start(), match.end())):
            names.append(name)
    return names


STRAY_BRACKET = re.compile(r"[《》〈〉《》]")


def clean_names(names: list[str]) -> list[str]:
    """Drop names a bracket crossed over.

    A nested quote like 关于印发〈X〉的通知 can be captured as the fragment
    "X〉的通知"; that is not a document name, and left in place it becomes a
    bogus series in the version lineage.
    """
    out = []
    for name in names:
        name = name.strip()
        if not name or STRAY_BRACKET.search(name):
            continue
        if not is_usable_name(name) or len(name) > 60:
            continue
        if name not in out:
            out.append(name)
    return out


def repealed_numbers(text: str, own_numbers: list[str]) -> list[str]:
    """Document numbers of the documents actually repealed.

    A list entry reads "《X》（1995年…令第78号发布 1997年…令第106号修改）":
    only the first number is X's own; the rest are amendment history and
    belong to decisions that this notice does not repeal.
    """
    flat = text.replace("\n", "")
    found = []
    # preferred reading: the first 文号 inside each 《X》（…） parenthetical
    for match in re.finditer(r"》\s*[（(]([^）)]{0,240})[)）]", flat):
        inner = match.group(1)
        hit = DOC_NO.search(inner)
        if hit:
            number = re.sub(r"\s+", "", hit.group(1))
            if number not in own_numbers and number not in found:
                found.append(number)
    if found:
        return found
    for match in DOC_NO.finditer(flat):
        number = re.sub(r"\s+", "", match.group(1))
        if number in own_numbers:
            continue
        window = flat[max(0, match.start() - 120):match.start() + 120]
        if any(word in window for word in REPEAL_WORDS) and number not in found:
            found.append(number)
    return found


def issuing_body(meta: dict, text: str) -> str | None:
    """The body that issued the document, not the web column that posted it."""
    flat = text.replace("\n", "")
    match = re.search(r"(海南省人民政府令第\s*\d+\s*号|琼府办|琼府)", flat)
    if match:
        token = match.group(1)
        if token.startswith("海南省人民政府令"):
            return "海南省人民政府"
        return "海南省人民政府办公厅" if token == "琼府办" else "海南省人民政府"
    for name in ISSUERS:
        if name in flat[:300]:
            return name
    return meta.get("ContentSource") or None


def parse_notice(url: str, html: str) -> dict:
    meta = meta_fields(html)
    title = meta.get("ArticleTitle") or ""
    text = body_text(html, meta)
    flat = text.replace("\n", "")

    repealed_names = named_documents(text, title)
    for extra in listed_documents(text) + inline_series(text):
        if extra not in repealed_names:
            repealed_names.append(extra)
    repealed_names = clean_names(repealed_names)
    if not repealed_names:
        hit = re.search(r"[《〈]([^》〉]{4,60})[》〉]", title)
        if hit:
            repealed_names = [re.sub(r"\s+", "", hit.group(1))]

    scope = ("partial" if re.search(
        r"第[一二三四五六七八九十百\d]+条[^。；]{0,16}(?:废止|失效)", flat)
        else "whole")
    # the operative clause ("决定废止《X》…"), never the title block
    operative = re.search(
        r"((?:现)?决定|予以)[^。；]{0,60}?废止[^。；]{0,80}", flat)
    if not operative:
        operative = re.search(r"((?:现)?决定|予以)[^。；]{0,60}?失效[^。；]{0,80}", flat)
    sentence = operative.group(0).strip() if operative else ""
    header = re.compile(r"(审议通过|会议通过|号公布)")
    parts = [part.strip() for part in re.split(r"[。；]", flat)]
    if not sentence:
        sentence = next((part for part in parts
                         if any(word in part for word in REPEAL_WORDS)
                         and len(part) < 260
                         and not header.search(part)), "")

    pub = meta.get("PubDate") or ""
    effective = pub[:10] if re.search(r"自(?:本通知|本决定)?公布之日起", flat) else None
    if not effective:
        hit = re.search(r"自(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起", flat)
        if hit:
            effective = f"{hit.group(1)}-{int(hit.group(2)):02d}-{int(hit.group(3)):02d}"
    if not effective:
        effective = pub[:10] or None

    numbers = document_numbers(flat)
    # a notice that says "等N件" but lists fewer means the list lives in an
    # attachment we did not fetch — record the shortfall instead of hiding it
    hint = None
    for candidate in re.finditer(
            r"等\s*([一二三四五六七八九十百\d]+)\s*(?:件|个|部|项)", title):
        window = title[max(0, candidate.start() - 40):candidate.start() + 6]
        if any(word in window for word in REPEAL_WORDS):
            hint = candidate
            break
    if hint is None and "废止" not in title:
        hint = re.search(r"等\s*([一二三四五六七八九十百\d]+)\s*(?:件|个|部|项)",
                         title)
    declared = None
    if hint:
        token = hint.group(1)
        if token.isdigit():
            declared = int(token)
        elif "十" not in token:
            declared = sum({"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                            "七": 7, "八": 8, "九": 9}.get(c, 0) for c in token)
        else:
            head, _, tail = token.partition("十")
            declared = (int(head) if head.isdigit() else
                        {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                         "七": 7, "八": 8, "九": 9}.get(head, 1) if head else 1) * 10
            declared += sum({"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                             "七": 7, "八": 8, "九": 9}.get(c, 0) for c in tail)
    complete = declared is None or len(repealed_names) >= declared
    return {
        "notice_title": title,
        "issuer": issuing_body(meta, flat),
        "publish_date": pub[:10] or None,
        "url": url,
        "doc_numbers": numbers,
        "repealed_numbers": repealed_numbers(flat, numbers[:1]),
        "repealed_documents": repealed_names,
        "declared_count": declared,
        "list_complete": complete,
        "list_note": ("" if complete else
                      f"标题声明废止 {declared} 件，正文只列出 "
                      f"{len(repealed_names)} 件，其余可能在附件中"),
        "scope": scope,
        "effective_date": effective,
        "repeal_sentence": sentence[:200],
    }


def corpus_index(database: str) -> list[dict]:
    """Known documents in the corpus, keyed by title and by 文号."""
    out = []
    for name in sorted(os.listdir(database)):
        if name.startswith("_") or not name.lower().endswith((".txt", ".md")):
            continue
        path = os.path.join(database, name)
        head = open(path, encoding="utf-8", errors="ignore").read(4000)
        out.append({
            "file": name,
            "title": os.path.splitext(name)[0],
            "doc_numbers": document_numbers(head),
        })
    return out


def match_against_corpus(record: dict, corpus: list[dict]) -> list[dict]:
    hits = []
    notice_numbers = set(record.get("repealed_numbers") or []) | set(
        document_numbers(record["repeal_sentence"]))
    if not notice_numbers:
        notice_numbers = set(record["doc_numbers"][1:])
    for doc in corpus:
        reason = None
        for number in doc["doc_numbers"]:
            if number in notice_numbers:
                reason = f"文号匹配 {number}"
                break
        if not reason:
            for name in record["repealed_documents"]:
                if name in doc["title"] or doc["title"] in name:
                    reason = f"标题匹配《{name}》"
                    break
        if reason:
            hits.append({
                "file": doc["file"],
                "matched_by": reason,
                "repealed_by": record["notice_title"],
                "effective_date": record["effective_date"],
                "scope": record["scope"],
            })
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--notices", default="")
    args = parser.parse_args()

    notices_path = args.notices or os.path.join(args.database,
                                                "_repeal_notices.json")
    if not os.path.exists(notices_path):
        print(f"找不到废止文件清单：{notices_path}")
        return
    notices = json.load(open(notices_path, encoding="utf-8"))
    corpus = corpus_index(args.database)
    print(f"语料 {len(corpus)} 份，废止类文件 {len(notices)} 份\n")

    records, affected, rejected = [], [], []
    failed_notices = []
    for item in notices:
        try:
            html = fetch(item["url"])
        except Exception as exc:
            print(f"抓取失败 {item['title'][:26]}: {type(exc).__name__}")
            failed_notices.append({"title": item["title"], "url": item["url"],
                                   "error": type(exc).__name__})
            continue
        record = parse_notice(item["url"], html)
        if not record["notice_title"]:
            record["notice_title"] = item["title"]
        # the listing title and the article title can differ: a page collected
        # as a repeal may turn out not to be one
        if not any(word in record["notice_title"] for word in REPEAL_WORDS):
            record["not_a_repeal_notice"] = True
            rejected.append(record)
            print(f"跳过（非废止文件）: {record['notice_title'][:44]}")
            continue
        records.append(record)
        hits = match_against_corpus(record, corpus)
        affected.extend(hits)
        print(f"===== {record['notice_title'][:44]}")
        print(f"  发布机关: {record['issuer'] or '未识别'}"
              f"   发布日期: {record['publish_date'] or '未识别'}"
              f"   生效: {record['effective_date'] or '未识别'}")
        print(f"  范围: {'部分条款' if record['scope'] == 'partial' else '整件'}"
              f"   文号: {record['doc_numbers'] or '未识别'}")
        print(f"  被废止: {record['repealed_documents'] or '未识别'}")
        print(f"  依据句: {record['repeal_sentence'][:100]}")
        print(f"  命中语料: {[h['file'] for h in hits] or '无'}\n")

    index_path = os.path.join(args.database, "_repeal_index.json")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump({"notices": records, "corpus_affected": affected,
                   "rejected_pages": rejected,
                   "failed_notices": failed_notices},
                  fh, ensure_ascii=False, indent=2)
    parsed = len(records)
    print(f"解析成功 {parsed}/{len(notices)} 份；"
          f"整件废止 {sum(1 for r in records if r['scope'] == 'whole')}，"
          f"部分条款 {sum(1 for r in records if r['scope'] == 'partial')}；"
          f"影响语料 {len(affected)} 处")
    print(f"索引 → {index_path}")


if __name__ == "__main__":
    main()
