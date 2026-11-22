"""Chinese-aware heading detection and tree assembly.

Runs on top of lines.py (clean, column-split, reading-order lines) and produces
a PageIndex-shaped structure: [{"title", "node_id", "start_index", "end_index",
"nodes"}]. Numbering style is detected per document, then heading level follows
the numbering depth with font size as a tie-breaker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter

from lines import Line, body_size, extract_lines, graphic_pages

# ---------- numbering patterns (Chinese and Arabic) ----------
P_CHAPTER = re.compile(r"^第[一二三四五六七八九十百千\d]+[章篇部]")
P_SECTION = re.compile(r"^第[一二三四五六七八九十百千\d]+[节条]")
P_CN1 = re.compile(r"^[一二三四五六七八九十]+[、.．]")
P_CN2 = re.compile(r"^[（(][一二三四五六七八九十]+[)）]")
P_AR3 = re.compile(r"^\d+\.\d+\.\d+")
P_AR2 = re.compile(r"^\d+\.\d+(?!\d)")
P_AR1_DOT = re.compile(r"^\d+[.．](?!\d)")
P_AR1_SPACE = re.compile(r"^\d+\s+\S")
P_AR4 = re.compile(r"^[（(]\d+[)）]")

# ---------- negative signals: these read like running text ----------
ENDS_SENTENCE = re.compile(r"[。；;]$")
HAS_INNER_STOP = re.compile(r"[。；;]")
DANGLING_TAIL = re.compile(r"[的了和或及在与为是被到把对从等]$")
CAPTION = re.compile(r"^[图表]\s*\d")
REFERENCE = re.compile(r"^\[\d+[\]‐\-–,]|^\d+\.\s*[A-Z][a-z]+ [A-Z]"
                       r"|^\d+\.\d+\.\d+|10\.\d{4}/|doi|DOI|ISSN|www\.")
JOURNAL_HEAD = re.compile(r"(第\s*\d+\s*卷|Vol\.|Chin J|中华.+杂志|收稿日期|DOI|引用本文)")
STOP_SECTION = re.compile(r"^(参考文献|References?|附录|致\s*谢)\s*$", re.I)
INLINE_ENUM = re.compile(r"[（(]\d+[)）]")
BULLET = re.compile(r"[•·▪◆◇]")
QUESTION = re.compile(r"[？?]$")
INFOGRAPHIC = re.compile(r"\d{2,}.*[人万字篇%]|[人万字篇%].*\d{2,}")


def latin_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


@dataclass
class Heading:
    line: Line
    level: int
    kind: str
    title: str


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _numbering(text: str) -> tuple[str, int] | None:
    """Return (kind, level) for a numbering prefix, or None."""
    if P_CHAPTER.match(text):
        return "chapter", 1
    if P_SECTION.match(text):
        return "section", 2
    if P_CN1.match(text):
        return "cn1", 1
    if P_CN2.match(text):
        return "cn2", 2
    if P_AR3.match(text):
        return "ar3", 3
    if P_AR2.match(text):
        return "ar2", 2
    if P_AR4.match(text):
        return "ar4", 4
    if P_AR1_DOT.match(text):
        return "ar1", 1
    if P_AR1_SPACE.match(text):
        return "ar1", 1
    return None


def detect_style(lines: list[Line], body: float) -> str:
    """Decide whether the document's top-level numbering is Chinese or Arabic.

    Chinese guidelines number their major sections 一、二、三、; the "1." lines
    inside them are list items, not top-level sections. Counting all numbered
    lines would therefore misread a guideline as Arabic-numbered.
    """
    texts = [_clean_title(line.text) for line in lines]
    cn1 = sum(1 for text in texts if P_CN1.match(text))
    cn2 = sum(1 for text in texts if P_CN2.match(text))
    ar1 = sum(1 for text in texts if P_AR1_DOT.match(text) or P_AR1_SPACE.match(text))
    ar2 = sum(1 for text in texts if P_AR2.match(text))
    if cn1 >= 3 or (cn1 + cn2) > ar1:
        return "cn"
    if ar2 >= 3 and ar1 == 0:
        return "ar"
    return "ar" if ar1 > cn1 + cn2 else "cn"


def _trim_title(text: str) -> str:
    """A heading often carries a colon and then prose; keep the label only."""
    text = re.split(r"[：:]\s*", text, maxsplit=1)[0]
    text = re.split(r"[（(]\s*[表图]\s*\d", text, maxsplit=1)[0]
    return text.strip()


def is_heading(line: Line, body: float, style: str,
               page_height: float = 0.0) -> tuple[bool, int, str]:
    """Decide whether a line is a heading, and at what level."""
    text = _clean_title(line.text)
    if not text or len(text) > 34:
        return False, 0, ""
    # running heads and folios live in the page margins
    if page_height and (line.y_top > page_height - 58
                        or line.y_bottom < 58):
        return False, 0, ""
    if JOURNAL_HEAD.search(text) or CAPTION.match(text) or REFERENCE.match(text):
        return False, 0, ""
    if re.match(r"^[·・].{1,10}[·・]$", text):     # journal column labels
        return False, 0, ""
    if BULLET.search(text):                       # bullet lists, not headings
        return False, 0, ""
    if ENDS_SENTENCE.search(text) or HAS_INNER_STOP.search(text):
        return False, 0, ""
    if DANGLING_TAIL.search(text) and len(text) > 8:
        return False, 0, ""
    if body and line.height < body * 0.85:      # footnote markers, watermarks
        return False, 0, ""
    if latin_ratio(text) > 0.4:                 # Latin-heavy = reference text
        return False, 0, ""

    numbered = _numbering(text)
    if numbered:
        kind, level = numbered
        limit = 34 if QUESTION.search(text) else 26
        if len(text) > limit:
            return False, 0, ""
        if re.search(r"[，,].{6,}", text):
            return False, 0, ""
        if INLINE_ENUM.search(text):            # "1.xxx：（1）yyy" = list item
            return False, 0, ""
        if style == "cn" and kind == "ar1":
            level = 3
        if style == "ar" and kind == "cn1":
            level = 3
        return True, level, kind

    # unnumbered headings: short, larger than body type, but not display type
    if (len(text) <= 20 and body
            and body * 1.08 <= line.height <= body * 1.9):
        if not re.search(r"[，,。；]", text):
            if INFOGRAPHIC.search(text):
                return False, 0, ""      # infographic figures, not a title
            return True, 1, "plain"
    return False, 0, ""


def collect_headings(lines: list[Line], body: float, style: str,
                     skip_pages: set[int] | None = None) -> list[Heading]:
    out: list[Heading] = []
    stopped = False
    skip_pages = skip_pages or set()
    for line in lines:
        if line.page in skip_pages:
            continue
        text = _clean_title(line.text)
        if stopped:
            continue
        if STOP_SECTION.match(text):
            out.append(Heading(line=line, level=1, kind="tail", title=text))
            stopped = True
            continue
        ok, level, kind = is_heading(line, body, style, line.page_height)
        if ok:
            title = re.sub(r"^[（(]\s*[)）]", "", text)       # "（）哮喘的患病率"
            title = _trim_title(title)
            if len(title) < 2:
                continue
            out.append(Heading(line=line, level=level, kind=kind, title=title))
    return out


def build_structure(headings: list[Heading], page_count: int) -> list[dict]:
    """Nest headings by level and fill page ranges."""
    root: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for head in headings:
        node = {"title": head.title, "start_index": head.line.page}
        while stack and stack[-1][0] >= head.level:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("nodes", []).append(node)
        else:
            root.append(node)
        stack.append((head.level, node))

    flat: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            flat.append(node)
            walk(node.get("nodes") or [])

    walk(root)
    for index, node in enumerate(flat):
        nxt = flat[index + 1] if index + 1 < len(flat) else None
        node["end_index"] = max(node["start_index"],
                                (nxt["start_index"] - 1) if nxt else page_count)
    for index, node in enumerate(flat):
        node["node_id"] = f"{index:04d}"
    if root and root[0]["start_index"] > 1:
        root.insert(0, {"title": "Preface", "node_id": "", "start_index": 1,
                        "end_index": root[0]["start_index"] - 1})
        for index, node in enumerate(root):
            pass
    return root


def read_bookmarks(path: str) -> tuple[list[dict], int]:
    """Publisher-authored outline, if the PDF carries one."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    page_count = len(doc)
    out: list[dict] = []
    try:
        for bookmark in doc.get_toc():
            title = _trim_title(_clean_title(bookmark.get_title() or ""))
            if not title:
                continue
            try:
                page = bookmark.get_dest().get_index() + 1
            except Exception:
                page = None
            out.append({"level": max(1, int(bookmark.level) + 1),
                        "title": title, "page": page})
    except Exception:
        out = []
    doc.close()
    return out, page_count


PLACEHOLDER = re.compile(r"^(幻灯片|Slide|Page|第\s*\d+\s*页?)\s*\d*$", re.I)


def bookmarks_are_useful(entries: list[dict]) -> bool:
    """Exporters emit one bookmark per slide/page with no real title."""
    if not entries:
        return False
    placeholder = sum(1 for e in entries if PLACEHOLDER.match(e["title"].strip()))
    return placeholder < max(3, len(entries) * 0.4)


def slide_headings(lines: list[Line], body: float) -> list[Heading]:
    """Slide decks: the biggest line near the top of each slide is its title."""
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    out: list[Heading] = []
    for page in sorted(by_page):
        items = sorted(by_page[page], key=lambda l: -l.y_top)[:4]
        if not items:
            continue
        best = max(items, key=lambda l: l.height)
        title = _trim_title(_clean_title(best.text))
        if (title and len(title) <= 40 and best.height >= (body or 8) * 1.15
                and not ENDS_SENTENCE.search(title)):
            out.append(Heading(line=best, level=1, kind="slide", title=title))
    return out


def structure_from_bookmarks(entries: list[dict], page_count: int) -> list[dict]:
    root: list[dict] = []
    stack: list[tuple[int, dict]] = []
    last_page = 1
    for entry in entries:
        page = entry["page"] or last_page
        last_page = page
        node = {"title": entry["title"], "start_index": page}
        while stack and stack[-1][0] >= entry["level"]:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("nodes", []).append(node)
        else:
            root.append(node)
        stack.append((entry["level"], node))
    flat: list[dict] = []

    def walk(nodes):
        for node in nodes:
            flat.append(node)
            walk(node.get("nodes") or [])

    walk(root)
    for index, node in enumerate(flat):
        nxt = flat[index + 1] if index + 1 < len(flat) else None
        node["end_index"] = max(node["start_index"],
                                (nxt["start_index"] - 1) if nxt else page_count)
    for index, node in enumerate(flat):
        node["node_id"] = f"{index:04d}"
    return root


def disambiguate(root: list[dict]) -> None:
    """Repeated section names (common in multi-guideline compendia) get their
    top-level section as a prefix so a citation names one place."""
    return  # disabled: the prefix inflates titles; hierarchy already disambiguates
    counts: Counter[str] = Counter()

    def collect(nodes):
        for node in nodes or []:
            counts[_clean_title(node.get("title", ""))] += 1
            collect(node.get("nodes"))

    collect(root)

    def fix(nodes, prefix=""):
        for node in nodes or []:
            title = _clean_title(node.get("title", ""))
            if prefix and counts[title] > 1:
                node["title"] = f"{prefix} · {title}"
            child_prefix = prefix or _clean_title(node.get("title", ""))[:24]
            fix(node.get("nodes"), child_prefix)

    fix(root)


def build(path: str, min_bookmarks: int = 100) -> dict:
    bookmarks, page_count = read_bookmarks(path)
    if len(bookmarks) >= min_bookmarks and bookmarks_are_useful(bookmarks):
        # The embedded outline is authoritative for these documents, but its
        # raw level values are often malformed, so the upstream bookmark parser
        # builds the tree and this layer only cleans the titles afterwards.
        structure = upstream_tree(path)
        if structure is None:
            structure = structure_from_bookmarks(bookmarks, page_count)
        clean_titles(structure)
        disambiguate(structure)
        return {"structure": structure, "pages": page_count,
                "toc_source": "bookmarks", "style": "bookmark",
                "body_size": 0.0, "bookmark_count": len(bookmarks)}

    lines = extract_lines(path)
    body = body_size(lines)
    style = detect_style(lines, body)
    slides = graphic_pages(lines, body)
    if page_count and len(slides) >= page_count * 0.5 and page_count >= 5:
        structure = build_structure(slide_headings(lines, body), page_count)
        disambiguate(structure)
        return {"doc_title": None, "toc_source": "slides",
                "structure": structure, "body_size": body, "style": "slide",
                "pages": page_count, "slide_pages": len(slides)}
    headings = collect_headings(lines, body, style, slides)
    pages = max((line.page for line in lines), default=0)
    structure = build_structure(headings, pages)
    disambiguate(structure)

    mine = count_nodes(structure)
    alternatives = upstream_tree(path)
    if alternatives:
        theirs = count_nodes(alternatives)
        if mine < theirs * 0.6 and fragment_ratio(alternatives) <= 0.20:
            clean_titles(alternatives)
            disambiguate(alternatives)
            return {"doc_title": None, "toc_source": "upstream-fallback",
                    "structure": alternatives, "body_size": body,
                    "style": style, "pages": pages,
                    "slide_pages": len(slides)}
    return {"doc_title": None, "toc_source": "chinastruct",
            "structure": structure, "body_size": body, "style": style,
            "pages": pages, "slide_pages": len(slides)}


def count_nodes(nodes: list[dict]) -> int:
    total = 0
    for node in nodes or []:
        total += 1 + count_nodes(node.get("nodes") or [])
    return total


def fragment_ratio(nodes: list[dict]) -> float:
    titles: list[str] = []

    def walk(items):
        for node in items or []:
            titles.append(_clean_title(node.get("title", "")))
            walk(node.get("nodes"))

    walk(nodes)
    if not titles:
        return 1.0
    bad = 0
    for title in titles:
        rest = re.sub(r"^\s*(?:第[一二三四五六七八九十百千\d]+[章节条部分篇]"
                      r"|[一二三四五六七八九十百]+[、.．)）]"
                      r"|[（(][一二三四五六七八九十\d]+[)）]"
                      r"|\d+(?:\.\d+)*[、.．]?)\s*", "", title)
        if (re.search(r"[。；;]", rest) or re.search(r"[，,].{2,}", rest)
                or len(rest) > 26 or re.search(r"[的了和或及在与为是被到把对从等]$", rest)):
            bad += 1
    return bad / len(titles)


def clean_titles(nodes: list[dict]) -> None:
    for node in nodes or []:
        title = _trim_title(_clean_title(node.get("title", "")))
        if title:
            node["title"] = title
        clean_titles(node.get("nodes") or [])


def upstream_tree(path: str) -> list[dict] | None:
    """The project's own bookmark-aware tree, when the package is importable."""
    repo = r"C:\Users\Lenovo\Desktop\PageIndex\PageIndex"
    import sys
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from pageindex.flash import page_index_flash
        result = page_index_flash(path, summary=False, optimize=False,
                                  use_embedded_toc=True)
        return result.get("structure") or None
    except Exception:
        return None


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\Lenovo\Desktop\dataset\05-慢阻肺与哮喘"
        r"\支气管哮喘防治指南(2020年版).pdf")
    result = build(target)
    print(f"style={result['style']} body={result['body_size']:.1f} "
          f"pages={result['pages']}")

    def show(nodes, indent=0):
        for node in nodes:
            print("  " * indent + f"[{node['start_index']}-{node['end_index']}] "
                  f"{node['title'][:52]}")
            show(node.get("nodes") or [], indent + 1)

    show(result["structure"])

    flat = []

    def walk(nodes):
        for n in nodes:
            flat.append(n)
            walk(n.get("nodes") or [])

    walk(result["structure"])
    print(f"\n节点总数: {len(flat)}")
