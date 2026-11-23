"""把《执法事项指导目录》的 PDF 附件还原成事项级索引。

网页正文是**列式摊平**的（事项名被折成十来个字一行），切不出事项；但页面上挂着
官方 PDF 附件。PDF 每页都有 8 个纵向栏位，但不同章节的栏宽会平移，且
pdfplumber 的自动表格识别会把部分独立列误并成一格。这里改为从 PDF 自身的
竖线聚类得到列边界，再用跨表宽的横线切事项行，因此不依赖固定格号。

目录中的长事项会跨页续排，下一页会重复总序号/分序号。建索引时把相邻页的同一
序号续段合并成一个事项，既保留续页中的法律名称和条款原文，也不制造重复事项。

    python catalog_index.py --build --limit-pages 6   # 小范围试跑
    python catalog_index.py --build                    # 全量（下载附件 + 解析）
    python catalog_index.py --stats
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
import retrieval
from repeal import attachment_links, fetch, fetch_bytes

from paths import DEFAULT_DATABASE as DEFAULT_DB
ITEMS_FILE = "_catalog_items.json"
PDF_DIR = "_catalog_pdfs"
LOCK_FILE = "_catalog_items.lock"
# 无边框表格：按文本对齐定列、按行线定行（实测这份 PDF 只有这一组参数能还原）
SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "lines"}
CATALOG_KEY = "综合行政执法事项指导目录"
ARTICLE_RE = re.compile(r"[第笫][一二三四五六七八九十百千万零〇\d]+条")
CATALOG_YEAR = re.compile(r"(20\d{2})\s*年版")


def _lines_text(chars: list[dict]) -> str:
    """按 y 聚类成行，再逐行按 x 拼字。

    不能用 `round(top/4)` 分桶：行距约 8–9pt 时相邻两行会落进同一桶，
    再按 x 排序就把两行的字交错在一起（实测长事项名会被打乱词序）。
    """
    ordered = sorted(chars, key=lambda c: (c["top"], c["x0"]))
    lines: list[list[dict]] = []
    current: list[dict] = []
    line_top = None
    for char in ordered:
        if line_top is None or abs(char["top"] - line_top) <= 4:
            current.append(char)
            if line_top is None:
                line_top = char["top"]
        else:
            lines.append(current)
            current, line_top = [char], char["top"]
    if current:
        lines.append(current)
    return "".join(
        "".join(c["text"] for c in sorted(line, key=lambda c: c["x0"]))
        for line in lines)


PUNCT = "、，,。；;：:（）()《》"


def _split_at(chars: list[dict], split: float) -> tuple[list[dict], list[dict]]:
    """按 x 劈开；落在分界上的标点归左侧（顿号常正好压在边界上）。

    不分的话会出现"登、记管理条例"这种把顿号切给法律名的串行。
    """
    left_rows = {round(c["top"] / 4) for c in chars if c["x0"] < split - 3}
    left, right = [], []
    for char in chars:
        if char["x0"] < split:
            left.append(char)
        elif (abs(char["x0"] - split) <= 3 and char["text"] in PUNCT
              and round(char["top"] / 4) in left_rows):
            left.append(char)
        else:
            right.append(char)
    return left, right


def header_boundary(page) -> float | None:
    """从本页表头算"事项名称｜法律名称"的分界 x。

    表头每页都重复，但**位置随章节略有偏移**（事项名 141–151、法律名
    211–224），所以按页取，不写死。
    """
    words = page.extract_words()
    item = next((w for w in words if w["text"] == "事项名称"), None)
    law = next((w for w in words if w["text"] == "法律名称"), None)
    if item and law and item["x1"] < law["x0"]:
        return (item["x1"] + law["x0"]) / 2
    return None


def _cluster_positions(values: list[float], tolerance: float = 5.0) -> list[float]:
    """合并相邻的 PDF 边线坐标，消除双线描边和分页写法造成的细小抖动。"""
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def column_boundaries(page) -> list[float]:
    """一页表格的 9 条纵向边界（8 列）。

    自动表格识别在后段章节常把“事项名称/法律名称/法律条款”误并，但 PDF 的
    原始竖线仍然完整。这里直接聚类竖线；普通文字页没有表格，返回空列表。
    """
    boundaries = _cluster_positions(
        [edge["x0"] for edge in page.vertical_edges], tolerance=5.0)
    return boundaries if len(boundaries) == 9 else []


def _covered_width(edges: list[dict]) -> float:
    """同一 y 上多条短横线的合并覆盖宽度。"""
    spans = sorted((edge["x0"], edge["x1"]) for edge in edges
                   if edge["x1"] - edge["x0"] > 0.5)
    total = 0.0
    left = right = None
    for x0, x1 in spans:
        if left is None:
            left, right = x0, x1
        elif x0 <= right + 0.8:
            right = max(right, x1)
        else:
            total += right - left
            left, right = x0, x1
    if left is not None:
        total += right - left
    return total


def row_bands(page, boundaries: list[float]) -> list[tuple[float, float]]:
    """用跨表宽的横线识别事项行，返回正文区的 y 区间。"""
    words = page.extract_words()
    header_bottom = max(
        (word["bottom"] for word in words
         if word["text"] in ("总序号", "分序号", "事项名称",
                             "法律名称", "法律条款")),
        default=90.0)
    width = boundaries[-1] - boundaries[0]
    by_y: dict[float, list[dict]] = {}
    for edge in page.horizontal_edges:
        if (boundaries[0] - 3 <= edge["x0"] <= boundaries[-1] + 3
                and boundaries[0] - 3 <= edge["x1"] <= boundaries[-1] + 3):
            by_y.setdefault(round(edge["top"], 1), []).append(edge)

    lines: list[float] = []
    for y in sorted(y for y, edges in by_y.items()
                    if _covered_width(edges) >= width * 0.85):
        if not lines or y - lines[-1] > 0.8:
            lines.append(y)
    body_lines = [y for y in lines if y >= header_bottom + 2]
    return list(zip(body_lines, body_lines[1:]))


def _cell_text(chars: list[dict], x0: float, top: float,
               x1: float, bottom: float) -> str:
    selected = [
        char for char in chars
        if char["text"].strip()
        and x0 + 0.5 <= char["x0"] < x1 - 0.5
        and top <= char["top"] < bottom
    ]
    return re.sub(r"\s+", "", _lines_text(selected))


def _join_fragments(left: str, right: str) -> str:
    """合并跨页文字，并去掉分页处常见的短重叠。"""
    if not left:
        return right
    if not right or right in left:
        return left
    if left in right:
        return right
    limit = min(len(left), len(right), 80)
    for size in range(limit, 2, -1):
        if left[-size:] == right[:size]:
            return left + right[size:]
    return left + right


def _item_row_starts(page, boundaries: list[float]) -> list[float]:
    """把被单元格内横线切开的片段重新并成一个事项行。

    目录中同一事项的长文会在法律名称/条款列内出现横线，但事项名称列只有新事项
    的第一行以“对”开头。以它为行锚点，避免条款从“处以……”或“（三）……”
    这种半句开始。
    """
    bands = row_bands(page, boundaries)
    starts = [
        top for top, bottom in bands
        if _cell_text(page.chars, boundaries[2], top,
                      boundaries[3], bottom).startswith("对")
    ]
    if not starts and bands:
        starts = [bands[0][0]]

    # 极少数事项名的首字不在“对”，用未被行锚点覆盖的总序号兜底。
    body_top = bands[0][0] if bands else 0
    body_bottom = bands[-1][1] if bands else 0
    totals = [
        word["top"] for word in page.extract_words()
        if boundaries[0] - 2 <= word["x0"] < boundaries[1] + 2
        and body_top <= word["top"] < body_bottom
        and re.fullmatch(r"\d{1,5}", word["text"].strip())
    ]
    all_starts = sorted(set(starts))
    for total_top in totals:
        covered = any(
            all_starts[i] <= total_top
            < (all_starts[i + 1] if i + 1 < len(all_starts) else body_bottom)
            for i in range(len(all_starts))
        )
        if covered:
            continue
        previous = [top for top in (band[0] for band in bands)
                    if top <= total_top]
        if previous:
            all_starts.append(previous[-1])
    return sorted(set(all_starts))


def split_merged_cell(page, cell, boundary: float | None = None) -> tuple[str, str]:
    """把"事项名称｜法律名称"合并格劈成左右两半。

    这两列在 PDF 里是一格、逐行交错。**不能按"最大空档"切**：事项名内部的
    字距（约 9pt）常比两列之间的真空档（约 7pt）还大。改为按内容评分：
    候选切点切出来的左边应像"对…的行政处罚"，右边应像"《…》"。
    """
    x0, top, x1, bottom = cell
    chars = [c for c in page.chars
             if c["text"].strip() and x0 <= c["x0"] < x1
             and top <= c["top"] < bottom]
    if not chars:
        return "", ""
    xs = sorted({round(c["x0"]) for c in chars})
    mid = (x0 + x1) / 2
    gaps = [(xs[i + 1] - xs[i], xs[i], xs[i + 1])
            for i in range(len(xs) - 1)
            if xs[i] >= x0 + 8 and xs[i + 1] <= x1 - 20]
    candidates = [((g[1] + g[2]) / 2, g[0]) for g in gaps
                  if abs((g[1] + g[2]) / 2 - mid) <= 40]
    candidates.append((x0 + (x1 - x0) * 0.55, 0))   # 版式兜底切点
    # 右侧要留出至少 20pt 才算"有法律名称列"；否则这一格只有事项名
    # （长事项名的格子可能比表头分界还窄，硬切会把事项名从中间劈开）
    if boundary is not None and x0 + 8 < boundary < x1 - 20:
        left_chars, right_chars = _split_at(chars, boundary)
        left, right = _lines_text(left_chars), _lines_text(right_chars)
        # 表头给的分界最可信，先做内容校验，过了就直接用
        if left and right and (left.startswith("对") or "《" in right):
            return left, right
    if boundary is not None:
        # 表头说这里该有法律列，但这格窄到装不下它——这一行只有事项名，
        # 整格返回；不能退回评分硬切（会把长事项名从中间劈开）。
        return _lines_text(chars), ""
    best = None
    for split, gap_size in candidates:
        left_chars, right_chars = _split_at(chars, split)
        left, right = _lines_text(left_chars), _lines_text(right_chars)
        score = 0
        if left.startswith("对"):
            score += 4
        if re.search(r"(行政处罚|行政强制)", left):
            score += 3
        if right.startswith("《") or "《" in right:
            score += 3
        if "》" in right:
            score += 2
        if not left or not right:
            score -= 4
        score += min(gap_size, 10) / 10
        if best is None or score > best[0]:
            best = (score, left, right)
    return best[1], best[2]


def page_items(page, catalog: str, page_no: int) -> list[dict]:
    """一页 PDF 里的事项行；跨页续段在 build() 中随后合并。"""
    boundaries = column_boundaries(page)
    if not boundaries:
        return []
    words = page.extract_words()
    chars = page.chars
    year_match = CATALOG_YEAR.search(catalog)
    catalog_year = int(year_match.group(1)) if year_match else None
    out = []
    bands = row_bands(page, boundaries)
    starts = _item_row_starts(page, boundaries)
    body_bottom = bands[-1][1] if bands else 0
    for index, top in enumerate(starts):
        bottom = (starts[index + 1]
                  if index + 1 < len(starts) else body_bottom)
        if bottom - top < 8:
            continue
        totals = [
            word for word in words
            if boundaries[0] - 2 <= word["x0"] < boundaries[1] + 2
            and top - 1 <= word["top"] < bottom - 1
            and re.fullmatch(r"\d{1,5}", word["text"].strip())
        ]
        if not totals:
            continue                    # 表头、领域标题、空白续页
        total = sorted(totals, key=lambda word: (word["top"], word["x0"]))[0]
        subs = [
            word for word in words
            if boundaries[1] - 2 <= word["x0"] < boundaries[2] + 2
            and abs(word["top"] - total["top"]) < 8
            and re.fullmatch(r"\d+", word["text"].strip())
        ]
        item = _cell_text(
            chars, boundaries[2], top, boundaries[3], bottom)
        law = _cell_text(
            chars, boundaries[3], top, boundaries[4], bottom)
        article = _cell_text(
            chars, boundaries[4], top, boundaries[5], bottom)
        subject = _cell_text(
            chars, boundaries[5], top, boundaries[6], bottom)
        out.append({
            "catalog": catalog,
            "catalog_year": catalog_year,
            "seq_total": int(total["text"]),
            "seq_sub": int(subs[0]["text"]) if subs else None,
            "item": item,
            "law": law,
            "article": article,
            "subject": subject,
            "page": page_no,
        })
    return out


def merge_continued_items(rows: list[dict]) -> list[dict]:
    """合并相邻页中相同总序号/分序号的续排事项。"""
    merged: list[dict] = []
    last_page: dict[tuple[int, int | None], int] = {}
    by_key: dict[tuple[int, int | None], dict] = {}
    for raw in rows:
        row = dict(raw)
        key = (row["seq_total"], row["seq_sub"])
        previous = by_key.get(key)
        previous_page = last_page.get(key)
        if (previous is not None and previous_page is not None
                and 0 <= row["page"] - previous_page <= 2):
            previous["item"] = _join_fragments(
                previous.get("item", ""), row.get("item", ""))
            previous["law"] = _join_fragments(
                previous.get("law", ""), row.get("law", ""))
            previous["article"] = _join_fragments(
                previous.get("article", ""), row.get("article", ""))
            previous["subject"] = _join_fragments(
                previous.get("subject", ""), row.get("subject", ""))
            previous["page_end"] = row["page"]
            last_page[key] = row["page"]
            continue
        row["page_end"] = row["page"]
        merged.append(row)
        by_key[key] = row
        last_page[key] = row["page"]
    return merged


def find_catalogs(database: str) -> list[dict]:
    catalog = json.load(open(os.path.join(database, "_all_documents.json"),
                             encoding="utf-8"))
    return [item for item in catalog if CATALOG_KEY in item["title"]]


def fetch_pdf(database: str, item: dict) -> str:
    """取目录页上的 PDF 附件，缓存到 database/_catalog_pdfs/。"""
    folder = os.path.join(database, PDF_DIR)
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", item["title"])[:70]
    for name in os.listdir(folder):
        if name.startswith(safe[:40]) and name.endswith(".pdf"):
            return os.path.join(folder, name)
    html = fetch(item["url"], timeout=30, retries=2)
    links = [l for l in attachment_links(html, item["url"]) if l.lower().endswith(".pdf")]
    if not links:
        raise RuntimeError(f"页面上没有 PDF 附件：{item['title'][:30]}")
    data = fetch_bytes(links[0])
    path = os.path.join(folder, safe + ".pdf")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def load_state(path: str, repair: bool = False) -> dict:
    """Load catalog state and optionally salvage a valid JSON prefix.

    Older parallel builds could append a partial write after the complete JSON
    object.  ``raw_decode`` recovers the last valid object without accepting
    corrupted catalog rows; normal builds stay strict.
    """
    if not os.path.exists(path):
        return {"version": 1, "catalogs": {}}
    raw = open(path, "rb").read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        text = raw.decode("utf-8", "replace")
        try:
            state, end = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            raise ValueError(
                f"目录状态无法解析：{exc}；请检查 {path}") from exc
        if not isinstance(state, dict) or "catalogs" not in state:
            raise ValueError(f"目录状态缺少 catalogs：{path}")
        if not repair:
            raise ValueError(
                f"目录状态尾部有 {len(text) - end} 个多余字符；"
                "运行 --repair-state 修复")
        _save(path, state)
        return state


@contextlib.contextmanager
def build_lock(database: str):
    """Prevent parallel builds from racing on the shared JSON state file."""
    path = os.path.join(database, LOCK_FILE)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            "已有目录解析任务正在运行；不同版本必须串行构建") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(),
                       "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                      handle, ensure_ascii=False)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


def build(database: str = DEFAULT_DB, limit_pages: int = 0,
          only: str = "", reset: bool = False) -> dict:
    import pdfplumber

    state_path = os.path.join(database, ITEMS_FILE)
    with build_lock(database):
        state = load_state(state_path)
        for item in find_catalogs(database):
            if only and only not in item["title"]:
                continue
            title = item["title"]
            entry = state["catalogs"].setdefault(
                title, {"pdf": None, "pages": 0, "done": 0, "items": []})
            if reset:
                entry.update({"done": 0, "items": []})
            pdf_path = fetch_pdf(database, item)
            entry["pdf"] = os.path.relpath(pdf_path, database)
            with pdfplumber.open(pdf_path) as pdf:
                entry["pages"] = len(pdf.pages)
                start = entry.get("done", 0)
                stop = min(len(pdf.pages), start + limit_pages) if limit_pages \
                    else len(pdf.pages)
                for index in range(start, stop):
                    entry["items"].extend(
                        page_items(pdf.pages[index], title, index + 1))
                    entry["done"] = index + 1
                    if (index + 1) % 25 == 0:
                        _save(state_path, state)
                        print(f"  {title[:24]} p{index + 1}/{entry['pages']}"
                              f"　事项 {len(entry['items'])}", flush=True)
                    time.sleep(0.01)
            entry["items"] = merge_continued_items(entry["items"])
            _save(state_path, state)
            print(f"完成 {title[:30]}：{entry['done']}/{entry['pages']} 页，"
                  f"{len(entry['items'])} 个事项", flush=True)
        return state


def _save(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(tmp, path)


def load_items(database: str = DEFAULT_DB) -> list[dict]:
    path = os.path.join(database, ITEMS_FILE)
    if not os.path.exists(path):
        return []
    state = json.load(open(path, encoding="utf-8"))
    rows = []
    for entry in state.get("catalogs", {}).values():
        for row in entry.get("items", []):
            if "catalog_year" not in row:
                match = CATALOG_YEAR.search(row.get("catalog", ""))
                row["catalog_year"] = (
                    int(match.group(1)) if match else None)
            rows.append(row)
    return rows


def catalog_stats(database: str = DEFAULT_DB) -> list[dict]:
    path = os.path.join(database, ITEMS_FILE)
    state = load_state(path)
    out = []
    for title, entry in state.get("catalogs", {}).items():
        match = CATALOG_YEAR.search(title)
        out.append({
            "title": title,
            "year": int(match.group(1)) if match else None,
            "pages": entry.get("pages", 0),
            "done": entry.get("done", 0),
            "items": len(entry.get("items", [])),
        })
    return sorted(out, key=lambda row: row["year"] or 0)


def catalog_units(database: str = DEFAULT_DB) -> list[dict]:
    """把事项行做成与条文同形的检索单元（供现有 Index 复用）。"""
    units = []
    for row in load_items(database):
        text = "｜".join(x for x in (row.get("item"), row.get("law"),
                                    row.get("article"), row.get("subject")) if x)
        clean = bool(ARTICLE_RE.search(row["article"])
                     and len(row["article"]) >= 20)
        units.append({
            "doc": row["catalog"],
            "file": row["catalog"] + ".txt",
            "doc_number": None,
            "chapter": "",
            "article": f"事项{row['seq_total']}",
            "article_no": row["seq_total"],
            "text": text,
            "status": row.get("status", "未判定"),
            "status_detail": {},
            "item_row": row,
            "article_clean": clean,
            "catalog_year": row.get("catalog_year"),
        })
    return units


def search_items(query: str, database: str = DEFAULT_DB,
                 top_k: int = 6) -> list[dict]:
    units = catalog_units(database)
    if not units:
        return []
    hits = retrieval.Index(units).search(query, top_k * 3)
    hits.sort(key=lambda hit: (
        -(hit.get("catalog_year") or 0),
        0 if hit.get("article_clean") else 1,
        -hit.get("score", 0),
    ))
    return hits[:top_k]


def answer_catalog(query: str, database: str = DEFAULT_DB,
                   top_k: int = 6, temperature: float = 0.2,
                   chat_fn=None, as_of=None) -> dict:
    """用事项级索引回答目录类问题。

    "条款"列在部分章节会串行（只有约 27% 抽得干净），所以**干净的直接引原文，
    不干净的只给事项名与法律名并明确提示核对原件**——不把串行文本当依据。
    """
    from force_layer import ForceLayer
    from force_protocol import build_answer_prompt
    import reasoning_retrieval as rr
    import temporal

    ask_model = chat_fn or rr.chat
    point = temporal.parse_as_of(as_of) if as_of else temporal.find_as_of(query)
    hits = search_items(query, database, top_k)
    result = {"query": query, "answer": None, "refusal": None,
              "refusal_stage": None, "sources": [], "forces": {},
              "outline": "", "notes": [], "reason": "", "force_note": "",
              "route": None, "as_of": point}
    if not hits:
        result["refusal"] = "事项级索引里没有匹配的条目"
        result["refusal_stage"] = "catalog_empty"
        return result
    layer = ForceLayer(database)
    # 条款抽干净的排前：让模型优先引可靠的那几条，串行的只作补充
    hits = sorted(hits, key=lambda h: (
        -(h.get("catalog_year") or 0),
        0 if h.get("article_clean") else 1,
        -h.get("score", 0),
    ))
    blocks, notes = [], []
    for hit in hits:
        row = hit["item_row"]
        status = layer.status_for(row["catalog"], as_of=point)["status"]
        body = [f"事项：{row['item']}"]
        if row.get("law"):
            body.append(f"法律：{row['law']}")
        if hit.get("article_clean"):
            body.append(f"条款：{row['article']}")
        else:
            body.append("条款原文抽取不完整，请按官方表格原件核对")
            notes.append(f"所列事项{row['seq_total']} 的条款列未完整抽取，"
                         "其余事项的条款可正常引用")
        if row.get("subject"):
            body.append(f"实施主体：{row['subject']}")
        blocks.append({"doc": row["catalog"], "doc_number": None,
                       "status": status, "text": "\n".join(body)})
        result["sources"].append({
            "doc": row["catalog"],
            "catalog_year": row.get("catalog_year"),
            "article": f"事项{row['seq_total']}",
            "status": status,
            "page": row["page"],
            "page_end": row.get("page_end", row["page"]),
            "law": row["law"],
            "article_text": row["article"] if hit.get("article_clean") else "",
            "evidence": {
                "kind": "catalog_row",
                "page": row["page"],
                "page_end": row.get("page_end", row["page"]),
                "law": row["law"],
                "catalog_year": row.get("catalog_year"),
                "text": row["article"] if hit.get("article_clean") else "",
            },
        })
    result["notes"] = notes
    try:
        result["answer"] = ask_model(
            [{"role": "user",
              "content": build_answer_prompt(query, blocks, notes, point)}],
            temperature=temperature)
    except Exception as exc:                      # noqa: BLE001
        result["refusal"] = f"模型调用失败：{type(exc).__name__}: {exc}"
        result["refusal_stage"] = "llm_error"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--limit-pages", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--reset", action="store_true",
                        help="清空该目录已抽取的事项，从头再来")
    parser.add_argument("--repair-state", action="store_true",
                        help="从有效的 JSON 前缀恢复被中断写入的目录状态")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    if args.repair_state:
        state_path = os.path.join(args.database, ITEMS_FILE)
        state = load_state(state_path, repair=True)
        print(f"目录状态已修复：{len(state.get('catalogs', {}))} 个版本")
        return
    if args.build:
        build(args.database, args.limit_pages, args.only, args.reset)
        return
    stats = catalog_stats(args.database)
    if args.stats:
        for row in stats:
            print(f"{row['year']} 年版：{row['done']}/{row['pages']} 页，"
                  f"{row['items']} 个事项")
        print(f"合计 {sum(row['items'] for row in stats)} 个事项")
        return
    rows = load_items(args.database)
    print(f"事项 {len(rows)} 条")
    for row in rows[:5]:
        print(f"  #{row['seq_total']} {row['item'][:40]} ← {(row['law'] or '')[:24]}")


if __name__ == "__main__":
    main()
