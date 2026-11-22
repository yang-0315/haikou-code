"""Scan the dataset for files that are not normal text guidelines."""
import os
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\chinastruct")

from lines import body_size, extract_lines, graphic_pages
from build_tree import read_bookmarks, PLACEHOLDER

DATASET = r"C:\Users\Lenovo\Desktop\dataset"


def main():
    print(f"{'文档':<30}{'页':>4}{'行数':>7}{'正文字号':>7}"
          f"{'图形页':>8}{'书签':>6}{'占位书签':>9}  判断")
    for theme in sorted(os.listdir(DATASET)):
        sub = os.path.join(DATASET, theme)
        if not os.path.isdir(sub) or theme.startswith("_"):
            continue
        for fname in sorted(os.listdir(sub)):
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(sub, fname)
            try:
                lines = extract_lines(path)
            except Exception as exc:
                print(f"{fname[:28]:<30} 读取失败 {type(exc).__name__}")
                continue
            body = body_size(lines)
            pages = max((l.page for l in lines), default=0)
            graphics = graphic_pages(lines, body)
            bookmarks, _ = read_bookmarks(path)
            placeholder = sum(1 for b in bookmarks
                              if PLACEHOLDER.match(b["title"].strip()))

            verdict = "正常指南"
            if body and graphics and len(graphics) >= pages * 0.5:
                verdict = "疑似幻灯片/图解为主"
            elif placeholder and placeholder >= len(bookmarks) * 0.4 and bookmarks:
                verdict = "书签为导出占位"
            elif pages and len(lines) / max(pages, 1) < 6:
                verdict = "文字极少，疑似扫描件"
            print(f"{fname.replace('.pdf','')[:28]:<30}{pages:>4}{len(lines):>7}"
                  f"{body:>7.1f}{len(graphics):>8}{len(bookmarks):>6}"
                  f"{placeholder:>9}  {verdict}")


if __name__ == "__main__":
    main()
