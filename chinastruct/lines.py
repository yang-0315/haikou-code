"""Line-level extraction with layout features, used by the Chinese heading rules.

pypdfium2 gives us character boxes; grouping them into lines lets us read the
signals Chinese guidelines actually use to mark a heading: bigger type, short
line, own paragraph, left or centre alignment, no sentence-ending punctuation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

import pypdfium2 as pdfium

SENTENCE_END = re.compile(r"[。；;]$")
INNER_STOP = re.compile(r"[。；;]")


@dataclass
class Line:
    page: int
    text: str
    x0: float
    x1: float
    y_top: float
    y_bottom: float
    height: float
    page_width: float
    page_height: float
    chars: list = field(default_factory=list)

    @property
    def stripped(self) -> str:
        return self.text.strip()

    @property
    def length(self) -> int:
        return len(self.stripped)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    def is_centered(self, tolerance: float = 12.0) -> bool:
        return abs(self.center_x - self.page_width / 2) < tolerance

    def left_ratio(self) -> float:
        return self.x0 / self.page_width


def extract_lines(path: str, max_pages: int | None = None) -> list[Line]:
    """Lines with layout features, splitting two-column journal pages first.

    Chinese society journals (中华医学会系列) print in two columns. A naive
    y-grouping splices the left and right column into one line, which is what
    breaks heading detection; we locate the gutter and group within columns.
    """
    doc = pdfium.PdfDocument(path)
    lines: list[Line] = []
    for page_index in range(len(doc)):
        if max_pages and page_index >= max_pages:
            break
        page = doc[page_index]
        width, height = page.get_size()
        textpage = page.get_textpage()
        raw = []
        for index in range(textpage.count_chars()):
            ch = textpage.get_text_range(index, 1)
            if not ch or ch in "\r\n":
                continue
            x0, y0, x1, y1 = textpage.get_charbox(index)
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                continue
            raw.append((x0, y0, x1, y1, ch))
        for items in page_lines(raw, float(width)):
            if not items:
                continue
            heights = [it[3] - it[1] for it in items]
            lines.append(Line(
                page=page_index + 1,
                text="".join(it[4] for it in items),
                x0=min(it[0] for it in items),
                x1=max(it[2] for it in items),
                y_top=min(it[1] for it in items),
                y_bottom=max(it[3] for it in items),
                height=median(heights),
                page_width=float(width),
                page_height=float(height),
                chars=items,
            ))
    doc.close()
    return lines


def y_rows(chars: list, base_tol: float = 3.0) -> list[list]:
    """Group characters into visual rows by their vertical position.

    Punctuation and Latin fragments sit at slightly different heights inside
    one visual line (a Chinese comma can be only 1pt tall), so the tolerance
    follows the tallest character in the row rather than the first one.
    """
    rows: list[dict] = []
    for item in sorted(chars, key=lambda c: (-c[3], c[0])):
        y0, y1 = item[1], item[3]
        height = y1 - y0
        for row in rows:
            tol = max(base_tol, 0.5 * row["h"], 0.5 * height)
            if abs(row["y"] - y0) <= tol:
                row["items"].append(item)
                row["ys"].append(y0)
                row["y"] = median(row["ys"])
                row["h"] = max(row["h"], height)
                break
        else:
            rows.append({"y": y0, "ys": [y0], "h": height, "items": [item]})
    return [row["items"] for row in rows]


def find_gutter(rows: list[list], min_votes: int = 8) -> float | None:
    """Locate a two-column gutter from the largest within-row gaps.

    A body line of a two-column journal is two independent column lines at the
    same height; the space between them is a wide gap that repeats at the same
    x on most rows. Full-width rows (headers, wide titles) have no such gap and
    therefore do not vote.
    """
    votes = []
    for row in rows:
        if len(row) < 8:
            continue
        ordered = sorted(row, key=lambda c: c[0])
        best = (0.0, None)
        for a, b in zip(ordered, ordered[1:]):
            gap = b[0] - a[2]
            if gap > best[0]:
                best = (gap, (a[2] + b[0]) / 2)
        if best[0] >= 8.0 and best[1] is not None:
            votes.append(best[1])
    if len(votes) < min_votes:
        return None
    centre = median(votes)
    near = [v for v in votes if abs(v - centre) <= 25]
    if len(near) < max(min_votes, 0.25 * len(votes)):
        return None
    return median(near)


def page_lines(chars: list, page_width: float) -> list[list]:
    """Characters -> lines in reading order, honouring a two-column layout."""
    if not chars:
        return []
    active = [c for c in chars if c[4].strip()]
    if not active:
        return [chars]
    rows = y_rows(active)
    gutter = find_gutter(rows)
    if gutter is None:
        return [sorted(row, key=lambda it: it[0]) for row in rows]

    columnar, spanning = [], []
    for row in rows:
        ordered = sorted(row, key=lambda c: c[0])
        y = min(c[1] for c in row)
        left = [c for c in ordered if c[2] <= gutter]
        right = [c for c in ordered if c[0] >= gutter]
        middle = [c for c in ordered if c not in left and c not in right]
        if left and right:
            seam = right[0][0] - left[-1][2]
            if seam >= 12:
                columnar.append((left, right, y))      # body line of both columns
            else:
                spanning.append((ordered, y))          # continuous across the gutter
        elif left:
            columnar.append((left + middle, [], y))    # short line, left column only
        elif right:
            columnar.append(([], middle + right, y))   # short line, right column only
        else:
            spanning.append((ordered, y))

    if not columnar:
        return [row for row, _ in spanning]

    body_top = max(y for _, _, y in columnar)
    body_bottom = min(y for _, _, y in columnar)
    above = [row for row, y in sorted(spanning, key=lambda t: -t[1]) if y >= body_top]
    below = [row for row, y in sorted(spanning, key=lambda t: -t[1]) if y < body_bottom]
    inside = [row for row, y in sorted(spanning, key=lambda t: -t[1])
              if body_bottom <= y < body_top]

    out: list[list] = []
    out.extend(above)
    out.extend(left for left, _, _ in sorted(columnar, key=lambda t: -t[2]))
    out.extend(right for _, right, _ in sorted(columnar, key=lambda t: -t[2]))
    out.extend(inside)
    out.extend(below)
    return [line for line in out if line]


def body_size(lines: list[Line]) -> float:
    """Body text size: the most common height among substantial text lines.

    Journal pages also carry small print (headers, references, table notes) and
    a few oversized display lines; weighting by character count over every line
    lets the small print win. Long CJK lines are the body text.
    """
    candidates = [line for line in lines
                  if line.length >= 15 and re.search(r"[\u4e00-\u9fff]", line.text)]
    if not candidates:
        candidates = [line for line in lines if line.length >= 10] or lines
    if not candidates:
        return 0.0
    heights = sorted(line.height for line in candidates)
    return heights[len(heights) // 2]          # median: slides and headings
                                               # cannot drag it upward


def graphic_pages(lines: list[Line], body: float,
                  ratio: float = 1.55) -> set[int]:
    """Pages whose text is mostly display type (summary slides, cover pages).

    Such pages are figure-like: their lines are large but short, so heading
    rules misfire on them. They are excluded from heading detection.
    """
    if not body:
        return set()
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    out = set()
    for page, items in by_page.items():
        big = [line for line in items if line.height > body * ratio]
        if items and len(big) / len(items) >= 0.5:
            out.add(page)
    return out


def describe(lines: list[Line], body: float, page: int | None = None,
             limit: int | None = None) -> None:
    shown = 0
    for line in lines:
        if page and line.page != page:
            continue
        size = line.height / body if body else 0
        flag = "HEAD?" if size > 1.08 else "     "
        print(f"p{line.page:<3} y={line.y_top:6.1f} x={line.x0:6.1f} "
              f"h={line.height:4.1f} ({size:4.2f}x) {flag} | {line.stripped[:64]}")
        shown += 1
        if limit and shown >= limit:
            return


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\Lenovo\Desktop\dataset\05-慢阻肺与哮喘"
        r"\支气管哮喘防治指南(2020年版).pdf")
    want_page = int(sys.argv[2]) if len(sys.argv) > 2 else None
    per_page = int(sys.argv[3]) if len(sys.argv) > 3 else None
    all_lines = extract_lines(target)
    body = body_size(all_lines)
    print(f"# {target}")
    print(f"# lines={len(all_lines)}  body_height={body:.2f}\n")
    describe(all_lines, body, page=want_page, limit=per_page)
