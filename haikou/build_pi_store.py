"""给真实 PageIndexClient 准备样例索引：txt → 带版式中文 PDF → 本地入库。

效力层要证明"force 挂得上真实客户端返回的节点"，就得有真实索引。这里做三件事：

1. **带版式**：标题／章／条用不同字号排版，并写入 PDF 书签（章为一级、条为
   二级）。上游的编号识别只认拉丁式（数字、字母），不认"第一章"，所以中文的
   层级要靠版式与书签给它——这正是它 `use_embedded_toc` 支持的路径。
2. **按文号缝成 manifest**：文号是政策文件的稳定主键（标题会带"（试行）"、
   文件名还会被截断），manifest 以文号为键，把 PDF 与效力记录缝在一起；
   取不到文号的如实记在 `missing_number` 里，不硬编一个。
3. **真实入库**：用上游 PageIndexLocalClient 建索引，doc_id 回填进 manifest。

    python build_pi_store.py --pdf-only        # 只生成 PDF 与 manifest（不需要 pageindex）
    python build_pi_store.py --build           # 建索引（需要 pageindex + LLM）
    python build_pi_store.py --plain           # 不排版的对照：上游只会切出逐页节点

`--build` 必须在装有 litellm／PyPDF2 的解释器下跑（项目自带 .venv），
索引阶段上游会调模型写节点摘要，所以这一步要联网。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
import json as _json
import llm_provider
from force_layer import ForceLayer
from warn import own_numbers

from paths import DEFAULT_DATABASE as DEFAULT_DB
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(HERE, "_pi_samples")
STORE_DIR = os.path.join(HERE, "_pi_store")
MANIFEST = os.path.join(HERE, "_pi_manifest.json")
PI_REPO = r"C:\Users\Lenovo\Desktop\PageIndex\PageIndex"
FONT = r"C:\Windows\Fonts\simhei.ttf"

# 三份样例：括号里第二项是"挑它来覆盖哪一态"，不是判定结果——
# 判定结果一律由 force_layer 现算（见 build_pdfs），这里只是选题意图。
SAMPLES = [
    ("海南自由贸易港建设投资基金管理办法", "系列已废止"),
    ("海南省关于在琼台湾同胞待遇的若干规定", "效力存疑"),
    ("海南自由贸易港商事调解组织登记管理办法", "现行有效"),
]

# 三级字号：标题 17 / 章 13.5 / 条 11.5 / 正文 10.5。条要比正文大一档，
# 上游才会把它也当标题候选，于是切出"章 → 条"的两层，而不是只有一层。
TITLE_SIZE, META_SIZE, CHAPTER_SIZE, ARTICLE_SIZE, BODY_SIZE = \
    17, 9.5, 13.5, 11.5, 10.5

# 章标题、条号的识别与检索层共用同一套正则，免得两处认出来的不一样
from retrieval import CHAPTER, article_name  # noqa: E402


def parse_blocks(text: str) -> list[tuple[str, str]]:
    """把正文拆成 (kind, text)：title / meta / chapter / article / body。

    正文常见的顺序是：标题行、括号里的通过信息、可选目录、正文章条。
    目录行（连续章标题后跟同名章）在这里当普通行处理——排版时不需要它。
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    blocks: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if index == 0:
            blocks.append(("title", line))
            continue
        if index == 1 and line.startswith(("（", "(")):
            blocks.append(("meta", line))
            continue
        if CHAPTER.match(line):
            blocks.append(("chapter", line))
            continue
        if article_name(line):
            blocks.append(("article", line))
            continue
        blocks.append(("body", line))
    return blocks


def render_pdf(blocks: list[tuple[str, str]], pdf_path: str,
               font_path: str = FONT, bookmarks: bool = True,
               article_per_page: bool = True) -> str:
    """按版式排 PDF：标题／章／条字号不同，并给章与条写书签。

    `article_per_page` 不是排版洁癖，是为了让分层在上游的 optimize 里活下来：
    上游先合并"同页兄弟"，再按"折叠后要扫的页数是否比走树更省"决定要不要折叠
    子树（`merge iff S(v) <= tree_cost(v)`）。条与章挤在同一页时，兄弟全被并成
    一个，层级就没了；一条一页时 `tree_cost = 1 + 1 = 2`，远小于章的页数，
    层级保得住。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("SimHei", font_path))
    canvas_obj = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4
    margin, usable = 56, width - 112
    y = height - margin
    chapter_key: str | None = None
    counter = 0

    def new_page():
        nonlocal y
        canvas_obj.showPage()
        y = height - margin

    def ensure(space: float):
        nonlocal y
        if y - space < margin:
            new_page()

    def draw(text: str, size: float, indent: float = 0,
             center: bool = False, fresh_page: bool = False):
        nonlocal y
        if fresh_page and y < height - margin:
            new_page()
        canvas_obj.setFont("SimHei", size)
        per_line = int((usable - indent) / size)
        chunks = [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]
        for chunk in chunks:
            ensure(size * 1.7)
            x = (width - len(chunk) * size) / 2 if center else margin + indent
            canvas_obj.drawString(x, y, chunk)
            y -= size * 1.7

    for kind, text in blocks:
        if kind == "title":
            ensure(TITLE_SIZE * 3)
            draw(text, TITLE_SIZE, center=True)
            y -= 6
        elif kind == "meta":
            draw(text, META_SIZE, center=True)
            y -= 4
        elif kind == "chapter":
            ensure(CHAPTER_SIZE * 3)
            y -= 10
            counter += 1
            chapter_key = f"ch{counter}"
            if bookmarks:
                canvas_obj.bookmarkPage(chapter_key)
                canvas_obj.addOutlineEntry(text[:60], chapter_key, level=0)
            draw(text, CHAPTER_SIZE)
            y -= 4
        elif kind == "article":
            counter += 1
            key = f"art{counter}"
            ensure(ARTICLE_SIZE * 2)
            if bookmarks:
                canvas_obj.bookmarkPage(key)
                canvas_obj.addOutlineEntry(text[:60], key,
                                           level=1 if chapter_key else 0)
            draw(text, ARTICLE_SIZE,
                 fresh_page=article_per_page and y < height - margin)
        else:
            draw(text, BODY_SIZE, indent=BODY_SIZE * 2)
    canvas_obj.save()
    return pdf_path


def find_document(database: str, title: str) -> str | None:
    for path in docstore.corpus_files(database) \
            + docstore.library_files(database):
        if os.path.splitext(os.path.basename(path))[0] == title:
            return path
    return None


def text_to_pdf(text: str, pdf_path: str, font_path: str = FONT) -> str:
    """用系统中文字体排成 PDF。字体内嵌，PyPDF2 能抽回文字。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("SimHei", font_path))
    canvas_obj = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4
    size, margin, leading = 10.5, 56, 16
    y = height - margin
    canvas_obj.setFont("SimHei", size)
    for raw in text.splitlines():
        line = raw.rstrip()
        # 手动折行：reportlab 的中文字体不会自己断行
        while len(line) > 46:
            canvas_obj.drawString(margin, y, line[:46])
            line = line[46:]
            y -= leading
            if y < margin:
                canvas_obj.showPage()
                canvas_obj.setFont("SimHei", size)
                y = height - margin
        canvas_obj.drawString(margin, y, line)
        y -= leading
        if y < margin:
            canvas_obj.showPage()
            canvas_obj.setFont("SimHei", size)
            y = height - margin
    canvas_obj.save()
    return pdf_path


def build_pdfs(database: str, plain: bool = False,
               force: bool = False, bookmarks: bool = True) -> dict:
    """生成 PDF，并把 PDF 与效力记录按文号缝成 manifest。

    **效力记录来自 `ForceLayer`**，不是写死的字符串：manifest 是"PDF ↔ 效力"
    的连接表，判定仍然只有一份源。三态覆盖是选题意图（`sampled_for`），
    与现算结果不一致时如实报出来，而不是拿意图去当结论。
    """
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    layer = ForceLayer(database)
    previous = {}
    if os.path.exists(MANIFEST):
        try:
            previous = _json.load(open(MANIFEST, encoding="utf-8")).get(
                "documents", {})
        except ValueError:
            previous = {}
    documents: dict[str, dict] = {}
    missing: list[str] = []
    for title, sampled_for in SAMPLES:
        source = find_document(database, title)
        if source is None:
            print(f"  找不到《{title}》，跳过")
            continue
        text = open(source, encoding="utf-8", errors="ignore").read()
        pdf_path = os.path.join(SAMPLE_DIR, title + ".pdf")
        if (not force and os.path.exists(pdf_path)
                and os.path.getsize(pdf_path) > 0):
            # 建索引那一步跑在项目 .venv 里（有 litellm/PyPDF2，没有 reportlab），
            # 所以已生成的 PDF 直接复用；要换版式就加 --force
            print(f"  《{title}》复用已有 PDF")
        elif plain:
            text_to_pdf(text, pdf_path)
        else:
            render_pdf(parse_blocks(text), pdf_path, bookmarks=bookmarks)
        record = layer.status_for_file(source)
        numbers = own_numbers(text[:400])
        key = numbers[0] if numbers else None
        entry = {"title": title, "sampled_for": sampled_for,
                 "force": record, "pdf": pdf_path, "txt": source,
                 "number": key,
                 "markup": ("plain" if plain else
                            ("layout+bookmarks" if bookmarks else "layout"))}
        # 重建 manifest 时保住已有的入库信息，免得每次重跑都要重新建索引
        for field in ("doc_id", "stored_name", "pages", "top_nodes",
                      "nodes", "nested"):
            if key and field in previous.get(key, {}):
                entry[field] = previous[key][field]
        if key:
            documents[key] = entry
        else:
            missing.append(title)
        drift = "" if record["status"] == sampled_for else \
            f"　⚠ 现算为 {record['status']}，与选题意图不符"
        print(f"  《{title}》→ {os.path.basename(pdf_path)}"
              f"（{os.path.getsize(pdf_path) // 1024} KB，"
              f"文号 {key or '未识别'}，效力 {record['status']}{drift}）")
    manifest = {
        "version": 1,
        "key": "文号",
        "markup": ("plain" if plain else
                   ("layout+bookmarks" if bookmarks else "layout")),
        "storage": STORE_DIR,
        "purpose": "把带版式 PDF 与效力记录缝在一起：键是文号，不是文件名",
        "documents": documents,
        "missing_number": missing,
    }
    with open(MANIFEST, "w", encoding="utf-8") as handle:
        _json.dump(manifest, handle, ensure_ascii=False, indent=1)
    if missing:
        print(f"  取不到文号、未入 manifest：{missing}")
    print(f"  manifest → {MANIFEST}（键 {len(documents)} 个文号）")
    return manifest


def api_key() -> str:
    try:
        return llm_provider.config_from_env().api_key
    except ValueError:
        return ""


def litellm_settings() -> tuple[str, dict[str, str]]:
    """Map the neutral provider config to PageIndex's litellm contract."""
    config = llm_provider.config_from_env()
    if config.name == "deepseek":
        return (f"deepseek/{config.model}",
                {"DEEPSEEK_API_KEY": config.api_key})
    provider_env = {"OPENAI_API_KEY": config.api_key or "not-needed"}
    if config.name in ("openai_compatible", "local"):
        provider_env["OPENAI_API_BASE"] = config.base_url
    return f"openai/{config.model}", provider_env


def build_store(manifest: dict) -> dict:
    if PI_REPO not in sys.path:
        sys.path.insert(0, PI_REPO)
    model, provider_env = litellm_settings()
    os.environ.update({key: value for key, value in provider_env.items()
                       if value})
    from pageindex import PageIndexLocalClient

    client = PageIndexLocalClient(storage_path=STORE_DIR,
                                  model=model,
                                  summary_model=model)
    for number, entry in manifest["documents"].items():
        result = client.submit_document(entry["pdf"], wait=True)
        doc_id = result["doc_id"]
        info = client.get_document(doc_id)
        tree = client.get_tree(doc_id)
        nodes = list(_walk_all(tree.get("result") or []))
        entry.update({
            "doc_id": doc_id,
            "stored_name": result.get("name"),
            "pages": info.get("pageNum"),
            "top_nodes": len(tree.get("result") or []),
            "nodes": len(nodes),
            "nested": any(node.get("nodes") for node in nodes),
        })
        print(f"  已入库 {number} → {doc_id}"
              f"（{entry['pages']} 页，{entry['nodes']} 节点，"
              f"嵌套={'是' if entry['nested'] else '否'}）")
    with open(MANIFEST, "w", encoding="utf-8") as handle:
        _json.dump(manifest, handle, ensure_ascii=False, indent=1)
    return manifest


def _walk_all(nodes):
    for node in nodes or []:
        yield node
        yield from _walk_all(node.get("nodes") or [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--pdf-only", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--plain", action="store_true",
                        help="不排版：上游只会切出逐页节点（对照组）")
    parser.add_argument("--force", action="store_true", help="重新生成 PDF")
    parser.add_argument("--no-bookmarks", action="store_true",
                        help="只靠版式，不写 PDF 书签")
    args = parser.parse_args()

    print("准备样例 PDF 与 manifest：")
    manifest = build_pdfs(args.database, plain=args.plain, force=args.force,
                          bookmarks=not args.no_bookmarks)
    if not manifest["documents"]:
        print("没有可用的样例")
        return
    if args.pdf_only:
        print(f"\nPDF 在 {SAMPLE_DIR}，manifest 在 {MANIFEST}"
              "（用 --build 建索引）")
        return
    if args.build:
        print("\n用真实 PageIndexLocalClient 建索引：")
        build_store(manifest)
        print(f"\n索引在 {STORE_DIR}")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
