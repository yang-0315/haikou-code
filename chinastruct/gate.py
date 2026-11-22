"""Dataset intake gate: is this PDF a normal text guideline?

Checks a PDF against the four intake rules and returns a machine-readable
verdict plus the measurements behind it. Used by the download pipeline and
runnable as an audit over a whole folder.

    python gate.py <folder> [--park]

--park moves rejected files into <folder>/_excluded and writes
_excluded/_excluded.json with the reason for each file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lines import body_size, extract_lines, graphic_pages   # noqa: E402
from build_tree import PLACEHOLDER, read_bookmarks          # noqa: E402

MIN_CHARS_PER_PAGE = 200
MIN_LINES_PER_PAGE = 6
MAX_GRAPHIC_RATIO = 0.5
MAX_PLACEHOLDER_RATIO = 0.4


def measure(path: str) -> dict:
    lines = extract_lines(path)
    pages = max((line.page for line in lines), default=0)
    body = body_size(lines)
    graphics = graphic_pages(lines, body)
    bookmarks, bookmark_pages = read_bookmarks(path)
    placeholder = sum(1 for b in bookmarks
                      if PLACEHOLDER.match(b["title"].strip()))

    chars = sum(len(line.stripped) for line in lines)
    return {
        "pages": pages or bookmark_pages,
        "lines": len(lines),
        "body_size": round(body, 2),
        "chars": chars,
        "chars_per_page": round(chars / pages, 1) if pages else 0,
        "lines_per_page": round(len(lines) / pages, 1) if pages else 0,
        "graphic_pages": len(graphics),
        "graphic_ratio": round(len(graphics) / pages, 3) if pages else 0,
        "bookmarks": len(bookmarks),
        "placeholder_bookmarks": placeholder,
        "placeholder_ratio": (round(placeholder / len(bookmarks), 3)
                              if bookmarks else 0),
    }


def judge(metrics: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if metrics["chars_per_page"] < MIN_CHARS_PER_PAGE:
        reasons.append("no_text_layer: 每页文字过少，可能是扫描件")
    if (metrics["pages"] >= 5
            and metrics["graphic_ratio"] >= MAX_GRAPHIC_RATIO):
        reasons.append("graphic_dominant: 过半页面是大字图形页，疑似幻灯片或图解")
    if metrics["placeholder_ratio"] >= MAX_PLACEHOLDER_RATIO and metrics["bookmarks"]:
        reasons.append("placeholder_bookmarks: 书签是导出占位名，不是真实目录")
    if metrics["lines_per_page"] < MIN_LINES_PER_PAGE and metrics["pages"] >= 5:
        reasons.append("too_little_text: 每页行数过少，不是正文文档")
    return ("reject" if reasons else "pass"), reasons


def evaluate_pdf(path: str) -> dict:
    try:
        metrics = measure(path)
    except Exception as exc:
        return {"file": os.path.basename(path), "verdict": "reject",
                "reasons": [f"unreadable: {type(exc).__name__}"],
                "metrics": {}}
    verdict, reasons = judge(metrics)
    return {"file": os.path.basename(path), "verdict": verdict,
            "reasons": reasons, "metrics": metrics}


def audit(folder: str, park: bool = False) -> list[dict]:
    results = []
    excluded_dir = os.path.join(folder, "_excluded")
    moved = []
    for theme in sorted(os.listdir(folder)):
        sub = os.path.join(folder, theme)
        if not os.path.isdir(sub) or theme.startswith("_"):
            continue
        for fname in sorted(os.listdir(sub)):
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(sub, fname)
            result = evaluate_pdf(path)
            result["theme"] = theme
            result["path"] = path
            results.append(result)
            print(f"[{result['verdict']:>6}] {fname[:44]:<46}"
                  f"{'; '.join(result['reasons'])[:60]}", flush=True)
            if park and result["verdict"] == "reject":
                os.makedirs(excluded_dir, exist_ok=True)
                target = os.path.join(excluded_dir, fname)
                shutil.move(path, target)
                moved.append({"file": fname, "from": theme,
                              "reasons": result["reasons"]})
    if moved:
        report = os.path.join(excluded_dir, "_excluded.json")
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(moved, fh, ensure_ascii=False, indent=2)
        print(f"\n已移出 {len(moved)} 份到 {excluded_dir}")
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    target = sys.argv[1]
    rows = audit(target, park="--park" in sys.argv)
    rejected = sum(1 for r in rows if r["verdict"] == "reject")
    print(f"\n检查 {len(rows)} 份，通过 {len(rows) - rejected}，"
          f"不合格 {rejected}")
