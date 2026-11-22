"""Build the Hainan Free Trade Port policy project plan document."""
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = (r"C:\Users\Lenovo\Desktop\PageIndex"
       r"\海南自贸港政策文档问答与比对项目计划.docx")

CN_FONT = "Microsoft YaHei"
INK = RGBColor(0x00, 0x00, 0x00)
BORDER = "D9D9D9"
HEADER_FILL = "1F3864"
ROW_TINT = "F2F6FC"


def set_cjk(run, name=CN_FONT):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def style_font(style, size, bold=False, name=CN_FONT):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = INK
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def shade(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def cell_borders(cell):
    tcpr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "6")
        el.set(qn("w:color"), BORDER)
        borders.append(el)
    tcpr.append(borders)


def cell_margins(cell, top=80, bottom=80, left=110, right=110):
    tcpr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for tag, value in (("top", top), ("left", left), ("bottom", bottom),
                       ("right", right)):
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcpr.append(mar)


def _cant_split(row):
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _repeat_header(row):
    row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))


def _keep_with_next(cell):
    for para in cell.paragraphs:
        para.paragraph_format.keep_with_next = True


def add_table(doc, header, rows, widths, center_cols=(), keep_together=True):
    table = doc.add_table(rows=1, cols=len(header))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    head_row = table.rows[0]
    _cant_split(head_row)
    _repeat_header(head_row)
    for idx, text in enumerate(header):
        cell = head_row.cells[idx]
        cell.text = ""
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(3)
        para.paragraph_format.space_after = Pt(3)
        run = para.add_run(text)
        set_cjk(run)
        run.font.size = Pt(9.5)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(cell, HEADER_FILL)
    for row_index, values in enumerate(rows):
        row = table.add_row()
        _cant_split(row)
        for idx, text in enumerate(values):
            cell = row.cells[idx]
            cell.text = ""
            para = cell.paragraphs[0]
            para.paragraph_format.space_before = Pt(3)
            para.paragraph_format.space_after = Pt(3)
            para.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            if idx in center_cols:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(text)
            set_cjk(run)
            run.font.size = Pt(9.5)
            if row_index % 2 == 1:
                shade(cell, ROW_TINT)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            cell.width = Cm(widths[idx])
            cell_borders(cell)
            cell_margins(cell)
            if keep_together:
                _keep_with_next(cell)
    return table


def body(doc, text, space_after=7):
    para = doc.add_paragraph(text)
    fmt = para.paragraph_format
    fmt.space_after = Pt(space_after)
    fmt.line_spacing = 1.30
    fmt.first_line_indent = Pt(21)
    for run in para.runs:
        set_cjk(run)
        run.font.size = Pt(10.5)
        run.font.color.rgb = INK
    return para


def bullet(doc, text):
    para = doc.add_paragraph(text, style="List Bullet")
    fmt = para.paragraph_format
    fmt.space_after = Pt(5)
    fmt.line_spacing = 1.28
    fmt.left_indent = Cm(0.75)
    for run in para.runs:
        set_cjk(run)
        run.font.size = Pt(10.5)
        run.font.color.rgb = INK
    return para


def caption(doc, text):
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(12)
    run = para.add_run(text)
    set_cjk(run)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    return para


def add_footer_page_number(section):
    para = section.footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run()
    set_cjk(run)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._element.append(begin)
    run._element.append(instr)
    run._element.append(end)


doc = Document()

section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.4)
section.bottom_margin = Cm(2.2)
section.left_margin = Cm(2.5)
section.right_margin = Cm(2.5)
add_footer_page_number(section)

style_font(doc.styles["Normal"], 10.5)
style_font(doc.styles["Title"], 22, bold=True)
style_font(doc.styles["Heading 1"], 15, bold=True)
style_font(doc.styles["Heading 2"], 12, bold=True)
doc.styles["Heading 1"].paragraph_format.space_before = Pt(18)
doc.styles["Heading 1"].paragraph_format.space_after = Pt(8)
doc.styles["Heading 1"].paragraph_format.keep_with_next = True
doc.styles["Heading 2"].paragraph_format.space_before = Pt(12)
doc.styles["Heading 2"].paragraph_format.space_after = Pt(6)
doc.styles["Heading 2"].paragraph_format.keep_with_next = True

_title_ppr = doc.styles["Title"].element.get_or_add_pPr()
for _pbdr in _title_ppr.findall(qn("w:pBdr")):
    _title_ppr.remove(_pbdr)

# ---------------- 标题 ----------------
title = doc.add_paragraph("海南自贸港政策文档问答与比对项目计划", style="Title")
title.paragraph_format.space_after = Pt(6)
for run in title.runs:
    set_cjk(run)

sub = doc.add_paragraph()
sub.paragraph_format.space_after = Pt(16)
run = sub.add_run("面向政策文本的条文级检索、版本比对与效力判断，含六个月执行路线")
set_cjk(run)
run.font.size = Pt(11)
run.font.color.rgb = RGBColor(0x40, 0x40, 0x40)

# ---------------- 一 ----------------
doc.add_heading("一、项目目标与主要结论", level=1)
body(doc, "这份计划面向海南自由贸易港政策文本的智能问答与比对。目标不是做一个通用政策问答机器人，而是解决政策使用中最实际的一类问题：某条具体规定出自哪一份文件、第几条，是否仍然有效，以及多份文件对同一件事的口径是否已经发生变化。")
body(doc, "主要结论有三条。第一，技术底座已经具备：上一轮工作中建成的中文长文档结构提取层与数据管线，与领域无关，可以直接迁移到政策文本，不需要重写。第二，本领域的瓶颈不在版式解析，而在两件新事情上：政策文件多以网页发布而非 PDF，以及政策的价值单位是条文而不是章节。第三，创新点落在版本谱系与冲突判断上，这一点比在医学指南上更扎实，因为政策文件天然存在明确的版本更替与上下位关系。")

# ---------------- 二 ----------------
doc.add_heading("二、方向调整的情况", level=1)
body(doc, "项目从医疗指南转向自贸港政策，原有的技术资产按可迁移程度分三类。结构提取层与数据管线完全通用，因为它们处理的是中文文档共有的版式与编号问题；语料与评测题需要重建；上一轮的具体医学结论作废。")
add_table(
    doc,
    ["资产", "处理方式", "说明"],
    [
        ["分栏与行重建", "直接复用", "政策文件多为单栏，处理难度低于期刊"],
        ["中文编号与标题判据", "直接复用", "一、／（一）／1.／第X章的识别逻辑与领域无关"],
        ["入库闸门与下载管线", "直接复用", "换一份目标清单即可，代码不动"],
        ["评测与对比框架", "直接复用", "换语料即可产出前后对比"],
        ["医学语料与结论", "作废", "已从工作目录清除"],
        ["评测题设计", "重建", "政策问题与医学问题形态不同"],
    ],
    widths=[4.0, 3.0, 9.0],
)
caption(doc, "表 1  原有资产的迁移处理")

body(doc, "选择政策方向的理由不只是兴趣。政策文本有一个医学指南不具备的性质：同一件事往往有多个年份、多个机关的版本，而这些版本之间会真实地发生口径变化。这种变化直接影响企业的合规判断，而现有的检索工具只能找到相似段落，判断不出哪一条已被替代。")

# ---------------- 三 ----------------
doc.add_heading("三、领域特点与技术缺口", level=1)
body(doc, "政策文本与医学指南在三个维度上明显不同，这三点决定了接下来要新建的能力。")
add_table(
    doc,
    ["维度", "医学指南", "政策文件", "带来的缺口"],
    [
        ["发布形态", "期刊 PDF，版式复杂", "政府网站网页为主，PDF 稀少", "需要网页抓取与转 PDF"],
        ["引用单位", "章节与推荐意见", "第X条", "需要条文级定位节点"],
        ["时间维度", "多为单一版本", "同一政策有多个年度版本", "需要版本谱系与效力标注"],
    ],
    widths=[2.4, 4.2, 4.6, 4.8],
)
caption(doc, "表 2  两个领域的差异与对应缺口")

body(doc, "第一点已经实测确认：对十二份核心政策文件做 PDF 直链检索，只找到四个可用链接，官方公报与法规数据库也检索不到 PDF 版本。因此数据入口必须从“搜 PDF”改为“抓网页正文再转成规范 PDF”。转换后的文档是单栏、层级清晰的，结构提取反而比期刊更容易，风险主要在抓取环节的稳定性与正文提取的准确性。")
body(doc, "第二点决定了索引的粒度。政策问答中最常见的提问是“这条规定出自哪里”，而政策文本的编号体系是第X章下辖第X条。如果只按章节建索引，模型无法回答到条文这一级，引用也就落不到具体条款上。因此需要在现有的章节树之下，为每一条生成可定位、可引用的节点。")
body(doc, "第三点是本项目的创新点所在。同一个政策主题在不同年份往往有新的文件发布，新旧文件之间可能一致、可能补充、也可能实质改变。判断“某条规定现在是否仍然适用”，需要把版本关系和条文内容放在一起推理，这是相似度检索做不到的事。")

# ---------------- 四 ----------------
doc.add_heading("四、技术底座现状", level=1)
body(doc, "上一轮工作建成的结构提取层位于项目仓库之外，包含以下能力，全部与领域无关。")
add_table(
    doc,
    ["能力", "作用"],
    [
        ["分栏识别", "从行内空隙定位栏位，修复双栏文本被拼成一行的问题"],
        ["行重建与字号估计", "按字符坐标重建文本行，用中位数估算正文基准字号"],
        ["标题判据", "结合编号前缀、行长、标点、字号与位置判断标题，剔除正文残句"],
        ["层级映射", "按文档实际使用的编号体系映射章节层级"],
        ["书签优先", "文档自带可用目录时优先采用，并识别导出产生的占位书签"],
        ["入库闸门", "四条规则判断文件是否为正常文本文档，附自测"],
        ["下载管线", "检索、下载、校验、过闸、归档，并记录来源与官方域名标记"],
        ["评测框架", "在语料上对比改造前后的节点数、标题质量与页面覆盖"],
    ],
    widths=[4.0, 12.0],
)
caption(doc, "表 3  已建成的通用能力")

body(doc, "这些能力在上一轮语料上验证过一轮：标题为正文残句的比例从百分之六降到百分之二，全部文档页面覆盖完整，欠分割的文档节点数由五到十一个提升到五十到八十五个。这些数字来自医学语料，语料本身已清除，但方法与代码保留，换领域后可直接复测。")

# ---------------- 五 ----------------
doc.add_heading("五、五个主题与拟收录文件", level=1)
body(doc, "按政策领域划分五个主题，每个主题收录四份左右文件，同一主题内保证存在可对比的版本或机关差异。下表为初拟清单，正式收录前需核对发布机关与最新版本。")
add_table(
    doc,
    ["主题", "拟收录文件", "对比价值"],
    [
        ["税收政策",
         "企业所得税优惠政策、高端紧缺人才个人所得税政策、原辅料零关税政策、交通工具及游艇零关税政策",
         "优惠条件与适用目录的年度调整"],
        ["贸易与投资自由便利",
         "外商投资准入负面清单、跨境服务贸易特别管理措施、贸易自由化便利化措施、投资自由化便利化措施",
         "负面清单的版本缩减与口径变化"],
        ["人员与人才",
         "人才引进政策、个人所得税优惠人才清单管理办法、停居留与免签安排",
         "人才认定口径与优惠条件的衔接"],
        ["法规与法治",
         "海南自由贸易港法、反走私条例、公平竞争条例、优化营商环境条例",
         "上位法与经济特区法规的对应关系"],
        ["产业与园区",
         "鼓励类产业目录、洋浦保税港区管理办法、重点园区政策、产业发展措施",
         "目录版本调整对优惠资格的影响"],
    ],
    widths=[2.8, 7.0, 6.2],
)
caption(doc, "表 4  主题划分与拟收录文件")

body(doc, "离岛免税政策单独作为一组重点语料，因为它自试点以来历经多次调整，每次调整涉及的免税额度、商品品类与适用人群都不相同，版本谱系最清晰，最能支撑版本比对的演示。")

# ---------------- 六 ----------------
doc.add_heading("六、六个月路线图", level=1)
body(doc, "路线图按单人执行设计，每一步都留下可展示的成果，任何阶段停下都有可交付物。可视化演示安排在第四个月，因为它是竞赛的入场券，也是检验整条链路是否真正跑通的最快方式。")
add_table(
    doc,
    ["阶段", "时间", "目标", "验收标准"],
    [
        ["0", "第 1 至 2 周", "网页抓取与语料入库", "二十份政策文件入库，含多版本，附来源记录"],
        ["1", "第 2 至 3 周", "条文级索引", "每条生成可定位节点，引用可落到具体条款"],
        ["2", "第 2 个月", "版本谱系与效力标注", "同主题文件建立版本关系，标注现行与已替代"],
        ["3", "第 3 个月", "可视化问答演示", "一个能现场完整跑完的三分钟演示"],
        ["4", "第 4 至 5 个月", "版本冲突比对，核心创新", "冲突检出率与人工抽检准确率"],
        ["5", "第 6 个月", "场景包装与参赛材料", "一分钟主张、三分钟演示、对比数据齐备"],
    ],
    widths=[1.6, 2.6, 5.4, 6.4],
    center_cols=(0,),
)
caption(doc, "表 5  六个月执行路线")

body(doc, "第一阶段的关键是语料质量而非数量。政策文件篇幅普遍短于医学指南，因此更需要靠版本组来体现价值：同一主题下至少要有两份不同年份或不同机关的文件，否则没有可对比的内容。")

# ---------------- 七 ----------------
doc.add_heading("七、创新点与竞赛表达", level=1)
body(doc, "本项目的创新不在版式解析，而在政策文本特有的三个问题上。")
body(doc, "条文级可追溯是最基础的一层。系统的回答不指向“某份文件”，而指向“某份文件的第几条”，并且可以点回原文。政策使用者需要的正是这种精度，因为公文引用必须落到条款。")
body(doc, "版本冲突侦测是核心创新。系统对同一主题的多份文件做交叉比对，指出哪些条款内容不一致、哪些条款已被新文件覆盖。现有检索工具的原理是相似度匹配，没有比对与判断能力，做不到这件事。")
body(doc, "效力判断是冲突侦测的延伸。当用户问某条规定的现行状态时，系统需要结合文件的发布时间、发布机关层级与后续文件，判断该条款是现行有效、已被替代还是部分失效。这是政策问答与普通文档问答最大的区别。")
body(doc, "现场演示可以这样组织：先让系统回答一个需要跨文件的问题并点出条文出处，再展示同一问题在三个年份的文件中口径如何变化，最后演示当依据的是已废止条款时系统的提示方式。三分之内的演示里，评委不需要理解检索技术，只要看到“系统指出了这是旧版条款”，就已经明白了价值。")

# ---------------- 八 ----------------
doc.add_heading("八、数据来源与合规边界", level=1)
body(doc, "数据优先取官方网站原始发布版本，便于在答辩中说明来源。主要渠道包括中国政府网、海南省人民政府网、海南省人民代表大会常务委员会网站，以及商务部、财政部、国家税务总局、海关总署等部门的政策发布页。检索类站点与商业转载版本仅作定位参考，不作为最终语料。")
body(doc, "每份入库文件都要记录四项信息：来源链接、发布机关、发布日期、版本标识。这些信息既是溯源依据，也是版本比对的输入。来源域名不在官方白名单内的文件需要人工确认，因为商业机构制作的解读版或概要版版式完全正常，只有出处能区分。")
body(doc, "有两条边界必须守住。第一，只用公开发布的政策文件，不涉及任何非公开材料。第二，系统定位是政策检索与比对工具，不提供政策解读或合规建议，答辩时应主动说明这一点，避免被追问政策解释的法律责任。所有输出必须带条文出处，没有出处的结论视为不合格回答。")

# ---------------- 九 ----------------
doc.add_heading("九、单人执行要点", level=1)
bullet(doc, "抓取器要稳不要快。政府网站结构各异且有访问限制，宁可放慢抓取速度、逐站适配，也不要写一个大而全的爬虫。")
bullet(doc, "转换后的 PDF 保持单栏、层级清晰即可，不必还原原网页版式。语料的可读性比外观重要。")
bullet(doc, "条文切分先做规则再做校验。第X条有固定句式，用规则切分后人工抽查一批，确认边界正确再全量处理。")
bullet(doc, "评测题先手工标二十条。政策问题与医学问题形态不同，先标一小批看清问题类型，再决定标注规范。")
bullet(doc, "不要改动该项目本身的上游代码。抓取、条文切分、版本谱系都做在项目之外，作为独立一层，便于单独测试与替换。")
bullet(doc, "不要只投一场比赛。先用小赛事演练演示脚本与答辩话术，这类材料通常要迭代三四轮才顺畅。")

# ---------------- 十 ----------------
doc.add_heading("十、近期可执行步骤", level=1)
body(doc, "本周的目标是把语料入口打通，具体分三步。第一步，选定三个官方网站作为起点，针对每个站点写一个稳定的正文抓取规则，先各抓一份文件验证可行性。第二步，把抓到的正文转换成单栏 PDF，用现成的结构提取层跑一遍，确认章节与条文层级都能正确识别。第三步，优先收集离岛免税政策的多个版本，跑一次版本对照，看条文级差异是否能够被自动捕捉。")
body(doc, "第三步的结果决定后续投入方向。如果条文切分的边界可靠、版本差异能够自动定位，那么核心创新点就成立，可以接着做冲突侦测与可视化；如果条文边界不稳定，需要先解决切分规则，不要急着做上层功能。")

doc.save(OUT)
print("saved:", OUT)
