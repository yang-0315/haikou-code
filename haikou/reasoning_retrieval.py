"""Force-aware reasoning retrieval.

Same shape as PageIndex's retrieval: read an outline, decide which sections to
open, answer from those sections. The difference is that every node in the
outline carries its document's legal-force status, so the reasoning itself can
weigh "this provision was repealed" instead of the system catching it later.

    python reasoning_retrieval.py "免税店的设立需要什么条件" --show-trace
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docstore
import llm_provider
import retrieval
import temporal
from force_layer import ForceLayer, doc_key
from force_protocol import (MAX_BLOCKS, build_answer_prompt,
                            build_select_prompt, explicit_invalidity)
from warn import IN_FORCE, QUESTIONABLE, REPEALED, SERIES_REPEALED

from paths import DEFAULT_DATABASE as DEFAULT_DB

# 每份候选文档给模型看多少条。候选文档的条文中位数 22 条、最多 66 条，全铺
# 出来也就一万字符上下；只给命中条会让模型看不见真正相关的那些条——实测
# 「投资基金的运作方式」只召回了第一条，模型据此拒答，第十四条根本没进目录。
MAX_OUTLINE_ARTICLES = 45

# ── 语料范围闸门 ───────────────────────────────────────────────
# 用户可能问别的辖区、别的领域。词形相似度分不开这两类：实测「免税店的设立
# 需要什么条件」与「量子计算机的制冷方案」在二元组覆盖上重叠（0.64 对 0.67），
# 拿任何一个词形阈值去切，都会同时切掉真问题。所以这里只做高精度的粗筛——
# 明确点了别处、又完全没提海南的问题直接判语料外；其余交给模型的 answerable
# 判断。召回层不假装自己能独立完成拒答。
FOREIGN = ("上海", "深圳", "前海", "横琴", "北京", "雄安", "浙江", "江苏",
           "广东", "广州", "福建", "厦门", "天津", "重庆", "成都", "杭州",
           "粤港澳", "大湾区", "临港", "浦东", "苏州", "南京", "武汉", "青岛")
HAINAN = ("海南", "琼", "自贸港", "自由贸易港", "离岛", "三沙", "洋浦",
          "博鳌", "乐城", "儋州", "三亚", "海口", "陵水", "琼海", "文昌",
          "五指山", "万宁", "定安", "屯昌", "澄迈", "临高", "白沙", "昌江",
          "乐东", "保亭", "琼中")


def scope_query(query: str) -> tuple[bool, str]:
    """粗筛语料外的问题：只拦得住"明确问了别处"这一类。"""
    foreign = [word for word in FOREIGN if word in query]
    if foreign and not any(word in query for word in HAINAN):
        return False, (f"问题指向非海南辖区（{'、'.join(foreign[:3])}），"
                       "本库只收录海南政策文件")
    return True, ""


def api_key() -> str:
    """Backward-compatible helper; provider configuration is the source."""
    try:
        return llm_provider.config_from_env().api_key
    except ValueError:
        return ""


def chat(messages: list[dict], temperature: float = 0.0,
         timeout: float | None = None, retries: int | None = None,
         provider=None) -> str:
    """Provider-neutral chat entry used by every retrieval backend."""
    chat_provider = llm_provider.get_provider(provider)
    return chat_provider.complete(messages, temperature=temperature,
                                  timeout=timeout, retries=retries)


_STATUS_CACHE: dict[tuple[str, str], dict] = {}
_TITLE_CACHE: dict[str, set[str]] = {}


def catalog_titles(database: str) -> set[str]:
    """目录（_all_documents.json）里的正式文件名。"""
    if database not in _TITLE_CACHE:
        _TITLE_CACHE[database] = {item["title"]
                                  for item in docstore.load_metadata(database)}
    return _TITLE_CACHE[database]


def status_of(layer: ForceLayer, database: str, doc_name: str,
              basename: str | None = None, as_of=None) -> dict:
    """Force status with the document's own text in hand.

    Passing only a name loses the masthead facts (year, amendment history),
    which is exactly what the 效力存疑 rule needs — so resolve by file.
    """
    point = temporal.parse_as_of(as_of) if as_of else None
    key = (basename or doc_name,
           temporal.period_key(point) if point else "")
    if key in _STATUS_CACHE:
        return _STATUS_CACHE[key]
    candidates = []
    if basename:
        candidates.append(os.path.join(database, basename))
        for folder, _, files in os.walk(os.path.join(database, "library")):
            if basename in files:
                candidates.append(os.path.join(folder, basename))
    for path in candidates:
        if os.path.exists(path):
            _STATUS_CACHE[key] = layer.status_for_file(path, as_of=point)
            return _STATUS_CACHE[key]
    _STATUS_CACHE[key] = layer.status_for(doc_name, as_of=point)
    return _STATUS_CACHE[key]


# 一份文件的两个版本（试行／正式）会落到同一个 doc_key。哪个版本进候选不能
# 由命中顺序决定，否则可能引到已被正式版取代的试行稿。
_VERSION_RANK = {IN_FORCE: 0, QUESTIONABLE: 1, REPEALED: 2, SERIES_REPEALED: 3}


def recall(database: str, query: str, docs_wanted: int,
           as_of=None) -> list[dict]:
    """Cheap recall first: which documents could hold the answer."""
    in_scope, _ = scope_query(query)
    if not in_scope:
        return []
    # 用"条文 + 段块"的合并单元召回：只查条文的话，没有条文结构的文档
    # （意见、方案、规划）整份检索不到——实测拿正文原句去查也是 0 命中。
    # 索引走持久化缓存：现场解析 1,248 份要 5.6 秒，读盘 0.5 秒；
    # 同进程连续提问还走进程内缓存（评测就是连续 30 问）
    index = retrieval.get_index(database)
    hits = index.search(query, top_k=60)
    # 题名通道：问题里点名的文档直接进候选并排在最前——否则会被"提到它的
    # 别的文件"挤掉（实测问某条例的名字，首位命中的是另一份文件）
    named_paths = retrieval.docs_named_in(query, database)
    asks_about_instrument = any(
        word in query for word in (
            "修改", "修订", "修正", "决定", "变化", "沿革",
            "新旧", "替代", "废止"))
    if not asks_about_instrument:
        # 修改决定是修改工具，不是被修改法规的正文。普通事实问法不应给它
        # 与现行法规同等的候选地位；明确问修改、沿革或废止时才保留。
        hits = [
            hit for hit in hits
            if not retrieval.amendment_target(hit["doc"])
        ]
    for path in named_paths:
        title = os.path.splitext(os.path.basename(path))[0]
        if (retrieval.amendment_target(title) and not asks_about_instrument):
            continue
        parsed = retrieval.parse_articles(path, {})
        # 表格型目录只切得出乱序的"条款引用"，不是条文；直接注入会把几千条
        # 伪条文灌进候选（实测那份指导目录有 7,016 条）。这类问题由路由拒答。
        if parsed and not retrieval.looks_like_articles(parsed):
            continue
        units = parsed or retrieval.block_units(path, {})
        # 即使词法命中里已经有这份，也要提到最前：命中过不等于排得前
        # （同名的条号在分组时会去重，不会重复）
        hits = [dict(unit, score=999.0, coverage=1.0) for unit in units] + hits
    layer_ = retrieval.layer(database)
    # 原法规已废止时，把废止依据点名的现行替代法规一并送进候选。用户仍能
    # 看到原法规的失效状态，同时不必再靠词法碰运气才能找到新法。
    library_paths = docstore.corpus_files(database) + \
        docstore.library_files(database)
    all_paths = {os.path.basename(path): path for path in library_paths}
    for path in named_paths:
        title = os.path.splitext(os.path.basename(path))[0]
        status = status_of(layer_, database, title, os.path.basename(path),
                           as_of=as_of)
        evidence = status.get("evidence") or {}
        replacement = re.sub(
            r"[（(]该文件生效时同时废止[）)]$", "",
            str(evidence.get("repealed_by") or "")).strip()
        if status["status"] not in (REPEALED, SERIES_REPEALED) or not replacement:
            continue
        replacement_key = doc_key(replacement)
        replacement_path = next(
            (candidate for candidate in library_paths
             if doc_key(os.path.splitext(os.path.basename(candidate))[0])
             == replacement_key),
            None)
        if not replacement_path:
            continue
        parsed = retrieval.parse_articles(replacement_path, {})
        units = parsed or retrieval.block_units(replacement_path, {})
        hits = [dict(unit, score=998.0, coverage=1.0)
                for unit in units] + hits
    all_by_doc: dict[str, list[dict]] = {}
    for article in index.articles:
        all_by_doc.setdefault(article["doc"], []).append(article)

    # 先按 (doc_key, 文档名) 分组：同一个 doc_key 下的不同版本各自留一组，
    # 之后才决定哪个版本当代表。
    groups: dict[str, dict[str, list[dict]]] = {}
    order: list[str] = []
    for hit in hits:
        key = doc_key(hit["doc"])
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key].setdefault(hit["doc"], []).append(hit)
    paths = all_paths

    def full_text(basename: str | None) -> str:
        path = paths.get(basename or "")
        if not path:
            return ""
        return open(path, encoding="utf-8", errors="ignore").read()

    out = []
    for key in order:
        versions = groups[key]
        # 同名不同文才是真版本组；整篇正文一致说明只是重复入库的同一份。
        signatures = ({name: full_text(hits_[0].get("file"))
                       for name, hits_ in versions.items()}
                      if len(versions) > 1 else {})
        ranked = []
        for index, (name, version_hits) in enumerate(versions.items()):
            status = status_of(layer_, database, name,
                               version_hits[0].get("file"))
            ranked.append((_VERSION_RANK.get(status["status"], 2), index,
                           name, version_hits, status))
        ranked.sort(key=lambda row: (row[0], row[1]))
        duplicate = bool(signatures) and len(set(signatures.values())) == 1
        divergent = len(versions) > 1 and not duplicate
        # 内容完全相同的两个副本里，用目录登记的那个正式名字当代表：
        # 库里一份正文有时会存成缺了「（试行）」之类的文件名。
        if duplicate:
            titles = catalog_titles(database)
            official = [index for index, row in enumerate(ranked)
                        if row[2] in titles]
            if len(official) == 1:
                ranked.insert(0, ranked.pop(official[0]))
        _, _, name, version_hits, _ = ranked[0]
        entry = {
            "doc": name,
            "doc_number": version_hits[0].get("doc_number"),
            "file": version_hits[0].get("file"),
            "articles": [],
            "variants": [{"doc": row[2], "status": row[4]["status"]}
                         for row in ranked[1:]],
            "duplicate_copy": duplicate,
            "divergent": divergent,
        }
        # 目录给的是这份文档的结构，不是检索命中的那几条：模型要能看见
        # 全文的条，才谈得上"读目录选条"。命中条打标记，其余照样列出。
        matched = {hit["article"]: hit for hit in version_hits}
        ordered = sorted(all_by_doc.get(name, version_hits),
                         key=lambda a: a["article_no"])
        marked = [dict(a, matched=True, score=matched[a["article"]].get("score"),
                       coverage=matched[a["article"]].get("coverage"))
                  for a in ordered if a["article"] in matched]
        rest = [dict(a, matched=False, score=None, coverage=None)
                for a in ordered if a["article"] not in matched]
        budget = max(MAX_OUTLINE_ARTICLES - len(marked), 0)
        entry["articles"] = sorted(marked + rest[:budget],
                                   key=lambda a: a["article_no"])
        entry["outline_truncated"] = len(entry["articles"]) < len(ordered)
        out.append(entry)

    out = out[:docs_wanted]
    for doc in out:
        doc["articles"].sort(key=lambda a: a["article_no"])
    return out


def annotated_outline(layer: ForceLayer, docs: list[dict],
                      database: str, as_of=None) -> tuple[str, dict]:
    """The outline the model reads: titles plus force, no body text."""
    lines, forces = [], {}
    for doc in docs:
        status = status_of(layer, database, doc["doc"], doc.get("file"), as_of)
        forces[doc["doc"]] = status
        head = f"【文档】{doc['doc']}"
        if doc.get("doc_number"):
            head += f"（{doc['doc_number']}）"
        lines.append(f"{head}　效力：{status['status']}")
        if doc.get("outline_truncated"):
            lines.append(f"  （条文过长，只列出前 {MAX_OUTLINE_ARTICLES} 条）")
        chapter = None
        for article in doc["articles"]:
            if article["chapter"] and article["chapter"] != chapter:
                chapter = article["chapter"]
                lines.append(f"  [{chapter}]")
            mark = "◆" if article.get("matched") else "·"
            item_status = layer.article_status_for(
                doc["doc"], article["article"], status,
                as_of=as_of, file=doc.get("file"))
            suffix = ""
            if item_status["status"] != status["status"]:
                suffix = f"　〔条文效力：{item_status['status']}〕"
            lines.append(
                f"  {mark} {article['article']}　"
                f"{article['text'][:34]}{suffix}")
    return "\n".join(lines), forces


def named_repealed_document(layer: ForceLayer, docs: list[dict],
                            query: str, database: str,
                            as_of=None) -> tuple[str, dict, str] | None:
    """Find an explicitly named document that candidate force marks as repealed."""
    target = doc_key(retrieval.normalize_query(query))
    for doc in docs:
        name = doc["doc"]
        if doc_key(name) not in target:
            continue
        status = status_of(layer, database, name, doc.get("file"), as_of)
        if status["status"] not in (REPEALED, SERIES_REPEALED):
            continue
        evidence = status.get("evidence") or {}
        replacement = re.sub(
            r"[（(]该文件生效时同时废止[）)]$", "",
            str(evidence.get("repealed_by") or "")).strip()
        return name, status, replacement
    return None


def named_document_notes(layer: ForceLayer, docs: list[dict],
                         query: str, database: str, as_of=None) -> list[str]:
    """Expose the status of an explicitly named document above the long outline."""
    target = doc_key(retrieval.normalize_query(query))
    notes = []
    for doc in docs:
        name = doc["doc"]
        if doc_key(name) not in target:
            continue
        status = status_of(layer, database, name, doc.get("file"), as_of)
        note = f"问题点名《{name}》，候选状态为「{status['status']}」。"
        if status["status"] in (REPEALED, SERIES_REPEALED):
            evidence = status.get("evidence") or {}
            replacement = re.sub(
                r"[（(]该文件生效时同时废止[）)]$", "",
                str(evidence.get("repealed_by") or "")).strip()
            note += "必须先明确说明该法规已废止，不能把修改决定当作其正文。"
            if replacement:
                note += f"现行替代法规为《{replacement}》。"
        notes.append(note)
    return notes


def parse_selection(selected) -> tuple[set[str], set[tuple[str, str]]]:
    """接受两种寻址：条文（第X条）与段块（第N段）。

    没有条文结构的文档按段块入库，模型引用它们时写的是「文档名|第7段」。
    """
    wanted_articles: set[str] = set()
    wanted_pairs: set[tuple[str, str]] = set()
    for item in selected or []:
        text = str(item)
        match = re.search(r"第[一二三四五六七八九十百]+条|第\d+段", text)
        if not match:
            continue
        article = match.group(0)
        wanted_articles.add(article)
        if "|" in text:
            wanted_pairs.add((doc_key(text.split("|")[0]), article))
    return wanted_articles, wanted_pairs


def match_doc(key: str, docs: list[dict]) -> tuple[dict | None, str]:
    """模型写的文档名对应哪份候选：先精确，再唯一包含。

    早期用「互相包含」当匹配条件，导致一个含糊的名字能同时命中多份文档，
    把每条同名条号都读进来。
    """
    exact = [doc for doc in docs if doc_key(doc["doc"]) == key]
    if exact:
        return exact[0], ""
    partial = [doc for doc in docs
               if key and (key in doc_key(doc["doc"])
                           or doc_key(doc["doc"]) in key)]
    if len(partial) == 1:
        return partial[0], ""
    if len(partial) > 1:
        return partial[0], (f"模型指名的「{key[:20]}」匹配到 {len(partial)} 份"
                            f"候选，已取排在最前的《{partial[0]['doc'][:24]}》")
    return None, f"模型指名的文档不在候选中（{key[:24]}），已改按条号取用"


def select_blocks(layer: ForceLayer, database: str, docs: list[dict],
                  picked: dict, as_of=None) -> tuple[list[dict], list[str]]:
    """把选条结果落成待读条文。

    裸条号（模型没写文档名）在多份候选里都存在时，取召回排序最靠前的那一份，
    并把取舍写进 notes。否则一句「第十条」会把每份文件的第十条都读进来，
    既有上下文浪费，也让引用分不清出自哪份文件。
    """
    wanted_articles, wanted_pairs = parse_selection(picked.get("read"))
    notes: list[str] = []
    blocks: list[dict] = []
    taken: set[tuple[str, str]] = set()

    def take(doc: dict, article: str) -> bool:
        """取一条进待读列表；同一份文件的多个条号各自独立，互不挤占。"""
        if (doc["doc"], article) in taken or len(blocks) >= MAX_BLOCKS:
            return False
        for item in doc["articles"]:
            if item["article"] == article:
                blocks.append(_block(layer, database, doc, item, as_of))
                taken.add((doc["doc"], article))
                return True
        return False

    # 指名的条号先落地
    for key, article in sorted(wanted_pairs):
        doc, note = match_doc(key, docs)
        if note:
            notes.append(note)
        if doc is None:
            continue
        if not take(doc, article):
            notes.append(f"《{doc['doc'][:24]}》里没有{article}")

    # 裸条号落到排在最前的、含该条的文档
    for article in sorted(wanted_articles):
        if any(source["article"] == article for source in blocks):
            continue
        holders = [doc for doc in docs
                   if any(item["article"] == article
                          for item in doc["articles"])]
        if not holders:
            continue
        doc = holders[0]
        take(doc, article)
        if len(holders) > 1:
            notes.append(
                f"「{article}」在 {len(holders)} 份候选中重复，"
                f"只取排在最前的《{doc['doc'][:26]}》；"
                "如需其他文件请指明文档名")
    return blocks, notes


def repealed_named_answer(layer: ForceLayer, database: str,
                          docs: list[dict], query: str, as_of=None) -> dict | None:
    """Deterministic notice when a model refuses a query naming repealed law.

    The replacement statute must contain the express repeal clause used here.
    This keeps the fallback evidence-based and prevents inventing a successor.
    """
    state = named_repealed_document(
        layer, docs, query, database, as_of)
    if not state:
        return None
    old_title, status, replacement = state
    if not replacement:
        return None
    replacement_key = doc_key(replacement)
    for doc in docs:
        if doc_key(doc["doc"]) != replacement_key:
            continue
        for article in doc.get("articles", []):
            if ("废止" in article.get("text", "")
                    and doc_key(old_title) in doc_key(article.get("text", ""))):
                blocks, _ = select_blocks(
                    layer, database, docs,
                    {"read": [f"{doc['doc']}|{article['article']}"]},
                    as_of=as_of)
                if not blocks:
                    continue
                sources = [{
                    "doc": block["doc"],
                    "article": block["article"],
                    "status": block["status"],
                    "doc_number": block["doc_number"],
                    "reason": block.get("force_reason", ""),
                    "evidence": block.get("force_detail", {}).get("evidence"),
                    "caveats": block.get("force_detail", {}).get("caveats", []),
                } for block in blocks]
                answer = (
                    f"**效力提示：** 您点名的《{old_title}》当前状态为“{status['status']}”。"
                    f"《{replacement}》{article['article']}已明文废止该法规，"
                    "因此其原规定只能作为历史参考，不能作为现行依据。"
                    f"如需办理或判断当前事项，应改查现行《{replacement}》。")
                return {
                    "answer": answer,
                    "sources": sources,
                    "reason": f"问题点名已废止法规；依据现行替代法规的明文废止条款返回失效提示。",
                    "force_note": answer.split("。", 1)[0] + "。",
                }
    return None


def ensure_named_repealed_notice(result: dict, layer: ForceLayer,
                                 docs: list[dict], query: str,
                                 database: str, as_of=None) -> None:
    """Guarantee that a named repealed law is labelled even if the model omits it."""
    repealed = named_repealed_document(
        layer, docs, query, database, as_of)
    if not repealed:
        return
    old_title, status, replacement = repealed
    answer = result.get("answer") or ""
    # Do not treat "未出现『已废止』" as a positive warning merely because
    # the old title appears elsewhere in the same answer.  Use the same
    # negation-aware detector as the evaluator, then inject the deterministic
    # notice when the model only silently substitutes the current file.
    notice_present = explicit_invalidity(answer)
    if not notice_present:
        warning = (
            f"**效力提示：** 您点名的《{old_title}》当前状态为"
            f"“{status['status']}”，不能作为现行依据。")
        if replacement:
            warning += f"现行替代法规为《{replacement}》。"
        result["answer"] = warning + "\n\n" + answer
    if not result.get("force_note"):
        result["force_note"] = (
            f"《{old_title}》当前状态为{status['status']}；"
            + (f"现行替代法规为《{replacement}》。"
               if replacement else "不得作为现行依据。"))


def prune_noncurrent_sources(result: dict, layer: ForceLayer,
                             docs: list[dict], query: str,
                             database: str, as_of=None) -> None:
    """For a generic current-law question, keep current sources when available.

    If the user explicitly names a repealed law, that named-law path takes
    precedence and is handled by ``ensure_named_repealed_notice`` instead.
    """
    if as_of or named_repealed_document(
            layer, docs, query, database, as_of):
        return
    sources = result.get("sources") or []
    current = [source for source in sources
               if source.get("status") == IN_FORCE]
    if not current or len(current) == len(sources):
        return
    removed = [source for source in sources
               if source.get("status") in (REPEALED, SERIES_REPEALED)]
    if not removed:
        return
    result["sources"] = [source for source in sources
                         if source.get("status") == IN_FORCE]
    result.setdefault("notes", []).append(
        "回答使用现行有效条文；已排除"
        + "、".join(f"《{source['doc']}》" for source in removed)
        + "等非现行依据。")


def _block(layer: ForceLayer, database: str, doc: dict, item: dict,
           as_of=None) -> dict:
    status = status_of(layer, database, doc["doc"], doc.get("file"), as_of)
    article_status = layer.article_status_for(
        doc["doc"], item["article"], status,
        as_of=as_of, file=doc.get("file"))
    reason = article_status.get("reason")
    return {"doc": doc["doc"], "doc_number": doc.get("doc_number"),
            "article": item["article"], "status": article_status["status"],
            "force_reason": reason.get("text") if isinstance(reason, dict)
            else str(reason or ""),
            "force_detail": article_status,
            "text": item["text"], "file": doc.get("file")}


def ask(query: str, database: str = DEFAULT_DB, docs_wanted: int = 4,
        temperature: float = 0.2, chat_fn=None, route: bool = True,
        as_of=None) -> dict:
    """完整问答：召回 → 模型选条 → 读条文 → 模型作答。

    返回结构化结果，供上层（比对、批量评测）直接取用：
      answer / refusal / sources / forces / outline / reason / notes

    chat_fn 可注入，测试时用假模型即可离线验证「拒答」「非 JSON」「选空」
    这三条分支，不必真的联网。
    """
    query = retrieval.normalize_query(query)   # 正体问题先归简，提示词里也用简体
    ask_model = chat_fn or chat
    point = temporal.parse_as_of(as_of) if as_of else temporal.find_as_of(query)

    # 策略路由：先决定去哪条检索路取证据（决定本身也可以被调用方看到）
    decision = None
    if route:
        from retrieval_router import route_query
        decision = route_query(query, database)
        if decision["path"] == "tree" and not decision["refuse"]:
            from tree_retrieval import tree_ask
            result = tree_ask(query, database, decision["docs_wanted"],
                              temperature, chat_fn, as_of=point)
            result["route"] = decision
            return result
        if decision["path"] == "catalog" and not decision["refuse"]:
            import catalog_index
            result = catalog_index.answer_catalog(query, database,
                                                  chat_fn=chat_fn,
                                                  temperature=temperature,
                                                  as_of=point)
            result["route"] = decision
            return result
        docs_wanted = decision["docs_wanted"]

    result = {"query": query, "answer": None, "refusal": None,
              "refusal_stage": None, "sources": [], "forces": {},
              "outline": "", "notes": [], "reason": "", "force_note": "",
              "route": decision, "as_of": point}
    if decision and decision["refuse"]:
        result["refusal"] = decision["reason"]
        result["refusal_stage"] = "route"
        return result

    layer = ForceLayer(database)
    in_scope, reason = scope_query(query)
    if not in_scope:
        result["refusal"] = reason
        result["refusal_stage"] = "scope"
        return result

    docs = recall(database, query, docs_wanted, as_of=point)
    outline, forces = annotated_outline(layer, docs, database, point)
    candidate_notes = named_document_notes(
        layer, docs, query, database, point)
    result["outline"] = outline
    result["forces"] = forces
    if not docs:
        result["refusal"] = "候选文档为空：问题用词在语料里没有对应条文"
        result["refusal_stage"] = "recall"
        return result

    try:
        raw = ask_model([{"role": "user",
                          "content": build_select_prompt(
                              query, outline, point, candidate_notes)}])
    except Exception as exc:                      # noqa: BLE001
        result["refusal"] = f"模型调用失败：{type(exc).__name__}: {exc}"
        result["refusal_stage"] = "llm_error"
        return result
    try:
        picked = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:
        result["refusal"] = f"模型未返回可解析的选择：{raw[:120]}"
        result["refusal_stage"] = "select_parse"
        return result

    result["reason"] = picked.get("reason") or ""
    result["force_note"] = picked.get("force_note") or ""
    if picked.get("answerable") is False:
        fallback = repealed_named_answer(
            layer, database, docs, query, point)
        if fallback:
            result.update(fallback)
            return result
        result["refusal"] = (f"候选中没有能回答该问题的条文。"
                             f"模型理由：{picked.get('reason') or '未说明'}")
        result["refusal_stage"] = "model"
        return result

    blocks, notes = select_blocks(layer, database, docs, picked, point)
    result["notes"] = notes
    if not blocks:
        result["refusal"] = ("模型选出的条号在候选里不存在："
                             + json.dumps(picked.get("read"),
                                          ensure_ascii=False))
        result["refusal_stage"] = "select_empty"
        return result

    result["sources"] = [{
        "doc": block["doc"],
        "article": block["article"],
        "status": block["status"],
        "doc_number": block["doc_number"],
        "reason": block.get("force_reason", ""),
        "evidence": block.get("force_detail", {}).get("evidence"),
        "caveats": block.get("force_detail", {}).get("caveats", []),
    } for block in blocks]
    prune_noncurrent_sources(
        result, layer, docs, query, database, point)
    try:
        result["answer"] = ask_model(
            [{"role": "user",
              "content": build_answer_prompt(query, blocks, notes, point)}],
            temperature=temperature)
        ensure_named_repealed_notice(
            result, layer, docs, query, database, point)
    except Exception as exc:                      # noqa: BLE001
        result["refusal"] = f"模型调用失败：{type(exc).__name__}: {exc}"
        result["refusal_stage"] = "llm_error"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--docs", type=int, default=4)
    parser.add_argument("--show-trace", action="store_true")
    parser.add_argument("--json", action="store_true", help="输出结构化结果")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印带效力的目录，不调模型")
    parser.add_argument("--as-of",
                        help="查询时点：2022-09-01、2022年9月或2022年")
    args = parser.parse_args()

    point = temporal.parse_as_of(args.as_of) if args.as_of \
        else temporal.find_as_of(args.query)
    if args.dry_run:
        layer = ForceLayer(args.database)
        docs = recall(args.database, args.query, args.docs, as_of=point)
        outline, _ = annotated_outline(layer, docs, args.database, point)
        total = sum(len(d["articles"]) for d in docs)
        if point:
            print(temporal.context(point) + "\n")
        print(f"候选文档 {len(docs)} 份，条文 {total} 条\n")
        print(outline)
        return

    result = ask(args.query, args.database, args.docs, as_of=point)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result["answer"] is None:
        print("== 拒答 ==")
        print(result["refusal"])
        return
    if args.show_trace:
        print("== 选条推理 ==")
        print(f"  理由：{result['reason']}")
        if result.get("force_note"):
            print(f"  效力说明：{result['force_note']}")
        for note in result["notes"]:
            print(f"  说明：{note}")
        print(f"  选了 {len(result['sources'])} 条\n")
    print("== 回答 ==")
    print(result["answer"])
    if args.show_trace:
        print("\n== 实际读取 ==")
        for source in result["sources"]:
            print(f"  [{source['status']}] {source['doc'][:30]} "
                  f"{source['article']}")


if __name__ == "__main__":
    main()
