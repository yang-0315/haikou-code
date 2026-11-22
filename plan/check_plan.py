"""Structural QA for the plan document (no renderer available)."""
import re
import zipfile

from docx import Document

PATH = (r"C:\Users\Lenovo\Desktop\PageIndex"
        r"\海南自贸港政策文档问答与比对项目计划.docx")


def main():
    doc = Document(PATH)
    styles = zipfile.ZipFile(PATH).read("word/styles.xml").decode("utf-8")

    print("== 样式检查 ==")
    for name in ("Title", "Heading1", "Heading2"):
        match = re.search(
            rf'<w:style [^>]*w:styleId="{name}".*?</w:style>', styles, re.S)
        body = match.group(0) if match else ""
        color = re.search(r'<w:color w:val="([0-9A-Fa-f]{6})"', body)
        print(f"  {name:<9} 边框: {'有' if 'pBdr' in body else '无':<2}"
              f"  颜色: {color.group(1) if color else '未设置'}")

    print("\n== 内容统计 ==")
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    headings = [p for p in paragraphs if p.style.name.startswith("Heading")]
    print(f"  段落 {len(paragraphs)}，标题 {len(headings)}，表格 {len(doc.tables)}")

    print("\n== 大纲 ==")
    for para in headings:
        level = para.style.name.replace("Heading", "").strip()
        indent = "  " * (int(level) - 1 if level.isdigit() else 0)
        print(f"  {indent}{para.text}")

    print("\n== 表格检查 ==")
    for index, table in enumerate(doc.tables, 1):
        rows = len(table.rows)
        cols = len(table.columns)
        first = table.rows[0].cells[0].text.strip()
        width_ok = all(cell.width is not None for cell in table.rows[0].cells)
        print(f"  表{index}: {rows}行×{cols}列  表头起于「{first[:14]}」 "
              f" 列宽已设: {width_ok}")

    print("\n== 页脚 ==")
    footer_xml = zipfile.ZipFile(PATH).read(
        [n for n in zipfile.ZipFile(PATH).namelist()
         if n.startswith("word/footer")][0]).decode("utf-8")
    print(f"  含 PAGE 域: {'PAGE' in footer_xml}")

    print("\n== 占位符残留 ==")
    text = "\n".join(p.text for p in paragraphs)
    leftovers = [w for w in ("TODO", "待补", "XXX", "[[", "]]") if w in text]
    print("  ", leftovers or "无")

    print("\n== 引号检查 ==")
    ascii_quotes = len(re.findall(r'"', text))
    print(f"  正文中的半角双引号: {ascii_quotes}（应为 0）")


if __name__ == "__main__":
    main()
