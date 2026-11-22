"""Article-level retrieval over the policy corpus, with force warnings.

The corpus files are already segmented into 章/条 by fetch_and_check.py, so the
retrieval unit is the article, not a text chunk. Every hit carries its exact
source (document + 文号 + 第X条) and the document's legal-force status, so an
answer can never silently quote a repealed provision.

    python retrieval.py "离岛居民购买零关税商品需要什么条件"
    python retrieval.py --top 8 "免税店的设立审批"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pickle
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
from force_layer import ForceLayer
from warn import own_numbers

_LAYER: ForceLayer | None = None

from paths import DEFAULT_DATABASE as DEFAULT_DB


def layer(database: str = DEFAULT_DB) -> ForceLayer:
    """One shared force layer for the whole retrieval path."""
    global _LAYER
    if _LAYER is None or _LAYER.database != database:
        _LAYER = ForceLayer(database)
    return _LAYER

# Two gates against answering outside the corpus, which for policy text is
# worse than admitting ignorance:
#   MIN_IN_CORPUS - how much of the question's wording exists in the index at
#                   all (an off-topic question is mostly unseen bigrams)
#   MIN_COVERAGE  - how much of that wording one article actually carries
MIN_IN_CORPUS = 0.5
MIN_COVERAGE = 0.3

CHAPTER = re.compile(r"^第([一二三四五六七八九十百]+)章\s*(.*)$")
# 条号要容忍两种网页抽取瑕疵：数字与"条"之间插了空格（"第三十五 条"），
# 以及破折号顶替了"一"（"第十—条"其实是第十一条）。漏认的代价不小——
# 认不出来时，上一条会把这一整段、连带下一个章标题都吞进自己的正文。
ARTICLE = re.compile(r"^第([一二三四五六七八九十百]+)\s*[—–－−\-]?\s*条")
DASHES = "—–－−-"
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
      "八": 8, "九": 9, "十": 10}


def cn_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return sum(CN.get(ch, 0) for ch in text)
    head, _, tail = text.partition("十")
    tens = CN.get(head, 1) if head else 1
    return tens * 10 + sum(CN.get(ch, 0) for ch in tail)


def article_name(line: str) -> str | None:
    """条文的标准写法：第X条。抽取瑕疵在这里归一，两条路共用同一个名字。"""
    match = ARTICLE.match(line.strip())
    if not match:
        return None
    number = match.group(1)
    if any(dash in match.group(0) for dash in DASHES):
        number += "一"          # "第十—条"里的破折号就是被抽坏的"一"
    return f"第{number}条"


def looks_like_articles(articles: list[dict], min_count: int = 20,
                        min_ratio: float = 0.8) -> bool:
    """这份文档的"第X条"是真的条文，还是表格里的条款引用？

    《综合行政执法事项指导目录》这类表格型文档，每行开头就是它引用的条款
    （"第三十五条第一款 …"），于是被当成几千条"条文"；判据是**条号是否基本
    递增**——真法规基本一路往上（实测 ≥0.91），表格目录则乱跳（0.56/0.57）。
    条数太少时比例没有意义，所以设了最小条数。
    """
    numbers = [a["article_no"] for a in articles]
    if len(numbers) < min_count:
        return True
    rising = sum(1 for i in range(1, len(numbers))
                 if numbers[i] >= numbers[i - 1])
    return rising / (len(numbers) - 1) >= min_ratio


def bigrams(text: str) -> list[str]:
    """Character bigrams: a dependency-free stand-in for Chinese tokenisation."""
    clean = re.sub(r"[\s，。；：、（）()《》〈〉0-9a-zA-Z%·\-—]", "", text)
    return [clean[i:i + 2] for i in range(len(clean) - 1)]


def load_status(database: str) -> dict:
    """Force status keyed by both file name and document title."""
    path = os.path.join(database, "_force_status.json")
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path, encoding="utf-8"))
    out = {}
    for name, item in raw.items():
        out[name] = item
        out[os.path.splitext(name)[0]] = item
        if item.get("title"):
            out[item["title"]] = item
    return out


def parse_articles(path: str, doc_status: dict) -> list[dict]:
    name = os.path.basename(path)
    text = open(path, encoding="utf-8", errors="ignore").read()
    articles: list[dict] = []
    if text:
        # the title line of the file usually carries the 文号
        head = text[:200]
        number = re.search(
            r"((?:琼府办|琼府)\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
            r"|海南省人民政府令第\s*\d+\s*号)", head)

        chapter = ""
        current = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            chapter_match = CHAPTER.match(line)
            if chapter_match:
                # 章标题也是一道边界：不关掉上一条，章下的内容会被上一条吞掉
                if current:
                    articles.append(current[0])
                    current = []
                chapter = line
                continue
            article = article_name(line)
            if article:
                if current:
                    articles.append(current[0])
                current = [{
                    "doc": os.path.splitext(name)[0],
                    "file": name,
                    "doc_number": number.group(1) if number else None,
                    "chapter": chapter,
                    "article": article,
                    "article_no": cn_int(article[1:-1]),
                    "text": line,
                    "status": doc_status.get("status", "未判定"),
                    "status_detail": doc_status,
                }]
            elif current:
                current[0]["text"] += line
        if current:
            articles.append(current[0])
    return articles


def load_articles(database: str) -> list[dict]:
    """Curated corpus at the database root, force status resolved live."""
    status = load_status(database)
    articles = []
    for path in docstore.corpus_files(database):
        name = os.path.basename(path)
        doc_status = status.get(name) or {}
        if not doc_status:
            title = os.path.splitext(name)[0]
            head = open(path, encoding="utf-8", errors="ignore").read(400)
            doc_status = layer(database).status_for(title, [], head)
        parsed = parse_articles(path, doc_status)
        # 表格型目录不当条文入库：它的"条"是引用，几千条会把召回灌满
        if looks_like_articles(parsed):
            apply_article_force(parsed, database)
            articles += parsed
    return articles


def load_articles_from_paths(paths: list[str],
                             database: str = DEFAULT_DB) -> list[dict]:
    """Library files, each resolved against the repeal index on the fly."""
    resolved: dict[str, dict] = {}
    articles = []
    for path in paths:
        name = os.path.basename(path)
        title = os.path.splitext(name)[0]
        if title not in resolved:
            text_head = open(path, encoding="utf-8", errors="ignore").read(300)
            resolved[title] = layer(database).status_for(
                title, own_numbers(text_head), text_head)
        doc_status = resolved[title]
        parsed = parse_articles(path, doc_status)
        if looks_like_articles(parsed):
            apply_article_force(parsed, database)
            articles += parsed
    return articles


def apply_article_force(units: list[dict], database: str,
                        as_of=None) -> list[dict]:
    """Apply narrower article-level deletion events after parsing a document."""
    layer_ = layer(database)
    for unit in units:
        article = unit.get("article", "")
        if not article.startswith("第") or "条" not in article:
            continue
        detail = layer_.article_status_for(
            unit["doc"], article, unit.get("status_detail"),
            as_of=as_of, file=unit.get("file"))
        unit["status"] = detail["status"]
        unit["status_detail"] = detail
    return units


# 没有条文结构的文档按句切块：句子边界优先，累到这么多字就收一块。
# 上限不能太大——目录里只给模型看块的开头几十字，块太长的话"到2030年…"这种
# 目标句会被埋在中间看不见（实测模型因此判"没有段落讲目标"而拒答）。
BLOCK_CHARS = 180
MIN_BLOCK_CHARS = 40
SENTENCE_END = "。；！？!?;"


def block_units(path: str, doc_status: dict) -> list[dict]:
    """没有"第X条"的文档，切成可检索的段块。

    「意见／方案／规划／行动计划」这类文件用"一、二、三"编号，切不出条文，
    于是整份文档在检索层是**隐形的**——实测拿它们的正文原句去查，5 份全部 0 命中。
    这里按行聚成段块，照条文条目的形状入库，`article` 记作"第N段"。
    """
    name = os.path.basename(path)
    text = open(path, encoding="utf-8", errors="ignore").read()
    if not text:
        return []
    head = text[:200]
    number = re.search(
        r"((?:琼府办|琼府)\s*〔\s*20\d{2}\s*〕\s*\d+\s*号"
        r"|海南省人民政府令第\s*\d+\s*号)", head)
    units, buffer = [], []
    size = 0

    def flush():
        nonlocal buffer, size
        body = "".join(buffer).strip()
        if len(body) >= MIN_BLOCK_CHARS:
            units.append(body)
        buffer, size = [], 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            flush()                     # 空行即段落边界
            continue
        # 行内再按句子切开：句子是天然的意义单位，切句后预览才有信息量
        for piece in re.findall(r"[^" + SENTENCE_END + r"]*[" + SENTENCE_END + r"]?",
                                line):
            piece = piece.strip()
            if not piece:
                continue
            # 放不下就先收块：宁可块小一点，也别把新句挤到块中间——
            # 目录里只看得到块开头，"到2030年…"被埋进去就等于没给模型
            if buffer and size + len(piece) > BLOCK_CHARS:
                flush()
            buffer.append(piece)
            size += len(piece)
            if size >= BLOCK_CHARS:
                flush()
    flush()

    return [{
        "doc": os.path.splitext(name)[0],
        "file": name,
        "doc_number": number.group(1) if number else None,
        "chapter": "",
        "article": f"第{index}段",
        "article_no": index,
        "block": True,
        "text": body,
        "status": doc_status.get("status", "未判定"),
        "status_detail": doc_status,
    } for index, body in enumerate(units, 1)]


def load_retrieval_units(database: str, blocks: bool = True) -> list[dict]:
    """检索用的全部单元：条文 + （没有条文的文档的）段块。

    `load_articles*` 只产出条文，检索层过去也只用它，于是没有条文的文档一律
    检索不到。这个函数把两种单元合在一起给召回用；审计与树仍走
    `load_articles*`（它们的口径是"条文"，不该被段块搅动）。
    """
    units = load_articles(database)
    units += load_articles_from_paths(docstore.library_files(database), database)
    if not blocks:
        return units
    have = {unit["doc"] for unit in units}
    for path in docstore.corpus_files(database) + docstore.library_files(database):
        title = os.path.splitext(os.path.basename(path))[0]
        if title in have:
            continue
        if parse_articles(path, {}):
            # 只要切得出条文就不切块：其中有条文的已经在上面的 units 里，
            # 表格型目录（判据认为不是条文）也不再切——那一类是表格，切块只会
            # 灌进大量近似重复的条目。
            continue
        head = open(path, encoding="utf-8", errors="ignore").read(400)
        status = layer(database).status_for(title, own_numbers(head), head)
        units += block_units(path, status)
    return units


def docs_named_in(query: str, database: str = DEFAULT_DB) -> list[str]:
    """问题里点名的文档路径。

    问一份文件的名字时，光靠词频会被"提到它的别的文件"挤掉（实测查
    「海南自由贸易港土地管理条例」首位命中的是别的文件）。这里直接看标题：
    把查询归一后，标题（去掉书名号、空格、文号、试行）整串出现在查询里的，
    就是被点名的文档。
    """
    from warn import doc_key
    target = doc_key(normalize_query(query))
    out = []
    for path in docstore.corpus_files(database) + docstore.library_files(database):
        title = os.path.splitext(os.path.basename(path))[0]
        # 文件名常带"…关于印发《X》的通知"外壳；问的是 X。两种都当候选名。
        candidates = {doc_key(title)}
        inner = re.search(r"[《〈]([^》〉]{4,60})[》〉]", title)
        if inner:
            candidates.add(doc_key(inner.group(1)))
        if any(len(name) >= 6 and name in target for name in candidates):
            out.append(path)
    return out


# ── 查询归一：繁 → 简 ────────────────────────────────────────────
VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
_ZHCONV = None


def normalize_query(query: str) -> str:
    """把查询归成简体（语料是简体）。

    官方文件都是简体，但正体用户会问「遊艇碼頭建設程序」——不归一就是 0 命中。
    zhconv 放在本目录 `_vendor/`（不污染共享运行时）；装不上时退化为原样，
    不假装支持。
    """
    global _ZHCONV
    if _ZHCONV is None:
        if VENDOR not in sys.path:
            sys.path.insert(0, VENDOR)
        try:
            import zhconv
            _ZHCONV = zhconv
        except Exception:
            _ZHCONV = False
    if not _ZHCONV:
        return query
    try:
        return _ZHCONV.convert(query, "zh-cn")
    except Exception:
        return query


# ── 持久化倒排索引 ──────────────────────────────────────────────
INDEX_FILE = "_retrieval_index.pkl"
INDEX_VERSION = 2  # v2 adds article-level force to each retrieval unit
_INDEX_CACHE: dict[tuple[str, str], "Index"] = {}


def corpus_fingerprint(database: str) -> str:
    """语料指纹：文件数 + 总字节 + 最新修改时间。语料一变就失效。"""
    digest = hashlib.sha1()
    files = docstore.corpus_files(database) + docstore.library_files(database)
    for path in sorted(files):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        digest.update(os.path.basename(path).encode("utf-8", "ignore"))
        digest.update(str(stat.st_size).encode())
        digest.update(str(int(stat.st_mtime)).encode())
    return digest.hexdigest()[:16]


def get_index(database: str = DEFAULT_DB) -> "Index":
    """取倒排索引：进程内缓存 → 磁盘缓存 → 现场构建（并落盘）。

    现场解析 1,248 份文件再建倒排要 5.6 秒；落盘后读回 0.5 秒。同一个进程里
    连续问多个问题（评测就是这样）还走进程内缓存，一次都不用再读。
    """
    fingerprint = corpus_fingerprint(database)
    key = (database, fingerprint)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    path = os.path.join(database, INDEX_FILE)
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                saved = pickle.load(handle)
            if (saved.get("version") == INDEX_VERSION
                    and saved.get("fingerprint") == fingerprint):
                index = Index(saved["units"], saved["postings"])
                _INDEX_CACHE[key] = index
                return index
        except Exception:
            pass                        # 缓存坏了就重建，不挡路
    index = Index(load_retrieval_units(database))
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            pickle.dump({"version": INDEX_VERSION,
                         "fingerprint": fingerprint,
                         "units": index.articles,
                         "postings": dict(index.postings)}, handle,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except OSError:
        pass                            # 落盘失败只影响下次启动，不影响本次
    _INDEX_CACHE[key] = index
    return index


def title_candidates(query: str, metadata: list[dict],
                     top_n: int = 3) -> list[dict]:
    """Which library documents does the question name?

    Titles are indexed for the whole 1872-document catalogue, so a question
    that names a document (or a distinctive phrase from its title) can pull
    that document in without anything having been pre-fetched.
    """
    grams = set(bigrams(query))
    if not grams:
        return []
    scored = []
    for item in metadata:
        title_grams = set(bigrams(item["title"]))
        if not title_grams:
            continue
        overlap = len(grams & title_grams)
        if overlap == 0:
            continue
        scored.append((overlap / len(title_grams), overlap, item))
    scored.sort(key=lambda row: (-row[1], -row[0]))
    return [item for _, _, item in scored[:top_n]]


def articles_ondemand(query: str, database: str, top_n: int = 3) -> list[dict]:
    """Fetch whatever the question points at, then index its articles."""
    metadata = docstore.load_metadata(database)
    if not metadata:
        return []
    picked = title_candidates(query, metadata, top_n)
    paths = []
    for item in picked:
        text, how = docstore.ensure(database, item)
        if text:
            paths.append(docstore.cache_path(database, item))
    if not paths:
        return []
    print(f"  [按需] 命中 {len(picked)} 份，已取得正文 {len(paths)} 份", file=sys.stderr)
    return load_articles_from_paths(paths, database)


class Index:
    def __init__(self, articles: list[dict],
                 postings: dict[str, set[int]] | None = None):
        self.articles = articles
        if postings is not None:
            self.postings = postings      # 从持久化索引读入，省掉重建
        else:
            self.postings = defaultdict(set)
            for position, article in enumerate(articles):
                for gram in set(bigrams(article["text"])):
                    self.postings[gram].add(position)
                for gram in set(bigrams(article["chapter"])):
                    self.postings[gram].add(position)
        self.total = max(len(articles), 1)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        query = normalize_query(query)      # 正体查询先归成简体
        grams = bigrams(query)
        if not grams:
            return []
        distinct = set(grams)
        present = [gram for gram in distinct if self.postings.get(gram)]
        in_corpus = len(present) / len(distinct)
        if in_corpus < MIN_IN_CORPUS:
            return []
        # A phrase that appears in most articles carries no signal: "海南自由
        # 贸易港" is in almost every 第一条. Drop those before scoring.
        common_cut = self.total * 0.20
        informative = [gram for gram in distinct
                       if self.postings.get(gram)
                       and len(self.postings[gram]) <= common_cut]
        if not informative:
            informative = present
        if not informative:
            return []
        scores: dict[int, float] = defaultdict(float)
        matched: dict[int, set[str]] = defaultdict(set)
        for gram in set(grams):
            holders = self.postings.get(gram)
            if not holders or len(holders) > common_cut:
                continue
            weight = math.log(1 + self.total / len(holders))
            for position in holders:
                scores[position] += weight
                matched[position].add(gram)
        if not scores:
            return []
        # how much of the query's distinctive wording does the article carry?
        coverage: dict[int, float] = {}
        for position in list(scores):
            ratio = len(matched[position]) / len(informative)
            coverage[position] = ratio
            scores[position] *= 0.25 + 0.75 * ratio
        # an explicit 第X条 in the query pins that article
        pin = re.search(r"第([一二三四五六七八九十百]+)条", query)
        if pin:
            wanted = cn_int(pin.group(1))
            for position, article in enumerate(self.articles):
                if article["article_no"] == wanted:
                    scores[position] += 6.0
        # 同分不能靠 set 的遍历顺序决定名次：那会随进程（PYTHONHASHSEED）
        # 变化，同一个问题在不同次运行里给出不同排序。用条文位置兜底。
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if ranked:
            floor = ranked[0][1] * 0.55          # drop also-rans
            ranked = [pair for pair in ranked if pair[1] >= floor]
        ranked = ranked[:top_k]
        out = []
        for position, score in ranked:
            # an article matching a sliver of the question is not an answer
            if coverage[position] < MIN_COVERAGE:
                continue
            out.append(dict(self.articles[position], score=round(score, 2),
                            coverage=round(coverage[position], 2)))
        return out


def render(query: str, hits: list[dict],
           database: str = DEFAULT_DB, dedupe: bool = True) -> str:
    """审查与排版都交给效力层，检索层只负责呈现。

    展示路径默认去重：同一份文件重复入库的副本并列两遍只是噪声。数据路径
    （`ForceLayer.review`）默认不去重，那里宁可多给也不替调用方丢东西。
    """
    lines = [f"问题：{query}", ""]
    if not hits:
        return "\n".join(lines + ["未在语料中检索到相关条文。"])

    reviewed = layer(database).review(hits, policy="flag", dedupe=dedupe)
    lines.append("结论：" + reviewed["summary"])
    for note in reviewed["duplicates"]:
        lines.append(f"  ▣ {'已合并' if note['merged'] else '未合并'}"
                     f"：{note['reason']}")
    lines.append("")
    for warning in reviewed["warnings"]:
        evidence = warning["evidence"]
        lines.append(f"  ⚠ {warning['document']}（{warning['status']}）")
        if warning.get("reason"):
            lines.append(f"      原因：{warning['reason']['text']}")
        if evidence.get("repealed_by"):
            lines.append(f"      依据：{evidence['repealed_by']}"
                         f"（{evidence.get('effective_date') or '日期未知'}）")
        if warning.get("missing"):
            lines.append("      缺什么：" + "；".join(warning["missing"]))
        if warning.get("next_steps"):
            lines.append("      下一步：" + "；".join(warning["next_steps"]))
        for caveat in warning["caveats"]:
            lines.append(f"      注意：{caveat}")
    if reviewed["warnings"]:
        lines.append("")

    for rank, hit in enumerate(reviewed["kept"], 1):
        source = f"《{hit['doc']}》"
        if hit["doc_number"]:
            source += f"（{hit['doc_number']}）"
        if hit["chapter"]:
            source += f" {hit['chapter']}"
        lines.append(f"[{rank}] {source} {hit['article']}"
                     f"（相关度 {hit['score']}，{hit['force']['status']}）")
        body = hit["text"]
        lines.append("    " + body[:180] + ("…" if len(body) > 180 else ""))
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--library", action="store_true",
                        help="同时在已缓存的库文件里检索")
    parser.add_argument("--ondemand", action="store_true",
                        help="按问题匹配目录，必要时现场抓取正文")
    args = parser.parse_args()

    articles = load_articles(args.database)
    if args.library:
        articles += load_articles_from_paths(docstore.library_files(args.database),
                                             args.database)
    if args.ondemand:
        articles += articles_ondemand(args.query, args.database)
    index = Index(articles)
    hits = index.search(args.query, args.top)
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
    else:
        print(f"语料：{len({a['doc'] for a in articles})} 份文件，"
              f"{len(articles)} 条条文\n")
        print(render(args.query, hits, args.database))


if __name__ == "__main__":
    main()
