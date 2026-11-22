"""把《执法事项指导目录》的 PDF 附件还原成事项级索引。

网页正文是**列式摊平**的（事项名被折成十来个字一行），切不出事项；但页面上挂着
官方 PDF 附件，PDF 里每页是一张无边框表格。pdfplumber 的
`{"vertical_strategy": "text", "horizontal_strategy": "lines"}` 能按文本对齐还原
出 6 列，其中"事项名称"与"法律名称"被并成了一格、逐行交错——再按单元格内
**中部最大的 x 空档**劈成左右两半，就得到干净的事项名与法律名
（实测："对未经登记…的行政处罚" / "《外国企业常驻代表机构登记管理条例》（2018修订）"）。

    python catalog_index.py --build --limit-pages 6   # 小范围试跑
    python catalog_index.py --build                    # 全量（下载附件 + 解析）
    python catalog_index.py --stats
"""
from __future__ import annotations

import argparse
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
# 无边框表格：按文本对齐定列、按行线定行（实测这份 PDF 只有这一组参数能还原）
SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "lines"}
CATALOG_KEY = "综合行政执法事项指导目录"


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
    """一页 PDF 里的事项行。"""
    out = []
    boundary = header_boundary(page)
    for table in page.find_tables(SETTINGS):
        for row in table.rows:
            cells = row.cells
            if len(cells) < 4 or not cells[2]:
                continue
            def text_of(index: int) -> str:
                if index >= len(cells) or not cells[index]:
                    return ""
                return re.sub(r"\s+", "", page.crop(cells[index]).extract_text() or "")
            seq_total, seq_sub = text_of(0), text_of(1)
            if not seq_total.isdigit():
                continue                    # 表头、领域标题等非事项行
            item, law = split_merged_cell(page, cells[2], boundary)
            out.append({
                "catalog": catalog,
                "seq_total": int(seq_total),
                "seq_sub": int(seq_sub) if seq_sub.isdigit() else None,
                "item": item,
                "law": law,
                "article": text_of(3),
                "subject": (text_of(4) + (" " + text_of(5) if len(cells) > 5 else "")).strip(),
                "page": page_no,
            })
    return out


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


def build(database: str = DEFAULT_DB, limit_pages: int = 0,
          only: str = "", reset: bool = False) -> dict:
    import pdfplumber

    state_path = os.path.join(database, ITEMS_FILE)
    state = {"version": 1, "catalogs": {}}
    if os.path.exists(state_path):
        state = json.load(open(state_path, encoding="utf-8"))
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
                entry["items"].extend(page_items(pdf.pages[index], title, index + 1))
                entry["done"] = index + 1
                if (index + 1) % 25 == 0:
                    _save(state_path, state)
                    print(f"  {title[:24]} p{index + 1}/{entry['pages']}"
                          f"　事项 {len(entry['items'])}", flush=True)
                time.sleep(0.01)
        # 跨页重复的表头/续页行去重
        seen, unique = set(), []
        for row in entry["items"]:
            key = (row["seq_total"], row["seq_sub"], row["item"][:30])
            if key in seen and not row["item"]:
                continue
            seen.add(key)
            unique.append(row)
        entry["items"] = unique
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
        rows.extend(entry.get("items", []))
    return rows


def catalog_units(database: str = DEFAULT_DB) -> list[dict]:
    """把事项行做成与条文同形的检索单元（供现有 Index 复用）。"""
    units = []
    for row in load_items(database):
        text = "｜".join(x for x in (row.get("item"), row.get("law"),
                                    row.get("article"), row.get("subject")) if x)
        clean = bool(re.search(r"第[一二三四五六七八九十百\d]+条", row["article"])
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
        })
    return units


def search_items(query: str, database: str = DEFAULT_DB,
                 top_k: int = 6) -> list[dict]:
    units = catalog_units(database)
    if not units:
        return []
    return retrieval.Index(units).search(query, top_k)


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
    hits = sorted(hits, key=lambda h: (0 if h.get("article_clean") else 1,
                                       -h.get("score", 0)))
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
        result["sources"].append({"doc": row["catalog"],
                                  "article": f"事项{row['seq_total']}",
                                  "status": status})
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
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    if args.build:
        build(args.database, args.limit_pages, args.only, args.reset)
        return
    rows = load_items(args.database)
    print(f"事项 {len(rows)} 条")
    for row in rows[:5]:
        print(f"  #{row['seq_total']} {row['item'][:40]} ← {(row['law'] or '')[:24]}")


if __name__ == "__main__":
    main()
