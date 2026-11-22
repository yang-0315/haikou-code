"""Prove the gate rejects the formats it is meant to reject."""
import os
import sys
import tempfile

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\chinastruct")

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from gate import evaluate_pdf

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def make_slides(path: str) -> None:
    """A slide deck: few lines, very large type, one idea per page."""
    c = canvas.Canvas(path, pagesize=A4)
    for index in range(1, 7):
        c.setFont("STSong-Light", 34)
        c.drawString(60, 700, f"第 {index} 部分 高血压防治要点")
        c.setFont("STSong-Light", 20)
        c.drawString(60, 630, "• 诊室血压≥140/90mmHg 可诊断")
        c.drawString(60, 590, "• 推荐生活方式干预")
        c.showPage()
    c.save()


def make_scan(path: str) -> None:
    """A scan-like page: no extractable text at all."""
    c = canvas.Canvas(path, pagesize=A4)
    for _ in range(6):
        c.setFillGray(0.85)
        c.rect(60, 300, 480, 300, stroke=0, fill=1)
        c.setFillGray(0.7)
        for row in range(12):
            c.rect(80, 560 - row * 20, 420, 8, stroke=0, fill=1)
        c.setFillGray(0)
        c.showPage()
    c.save()


def main():
    tmp = tempfile.mkdtemp(prefix="gate_selftest_")
    slides = os.path.join(tmp, "synthetic_slides.pdf")
    scan = os.path.join(tmp, "synthetic_scan.pdf")
    make_slides(slides)
    make_scan(scan)
    for path in (slides, scan):
        result = evaluate_pdf(path)
        print(f"{os.path.basename(path):<24} -> {result['verdict'].upper()}")
        for reason in result["reasons"]:
            print(f"     {reason}")
        metrics = result["metrics"]
        print(f"     pages={metrics.get('pages')} "
              f"lines/page={metrics.get('lines_per_page')} "
              f"chars/page={metrics.get('chars_per_page')} "
              f"graphic_ratio={metrics.get('graphic_ratio')}")


if __name__ == "__main__":
    main()
