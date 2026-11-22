"""检索策略路由：先判断这题该走哪条检索路，再让那条路去取证据。

现在手上能用的是四条路加两条"走不通"的路：

    article_exact    精确条号（"第十条"）→ 条文索引 + 条号 pin，候选 2 份
    single_doc_tree  点名了一份文件      → 沿树检索（node_id + 章节结构），候选 1 份
    multi_doc        比较／沿革／废止关系 → 多文档条文召回，候选 6 份
    default_flat     其余                → 条文＋段块召回，候选 4 份
    table_catalog    表格型目录主题      → 目前没有事项目录级索引，如实拒绝
    out_of_scope     非海南／语料外      → 拒答

路由是**确定性**的：只看问题文本和语料事实（是否点名文档、有没有条号、
是不是比较类问法），每个决定都带 `signals` 与 `reason`，可复现、可审计。
它只决定"去哪儿取证据"，不改变作答协议——效力维度照旧由 force_protocol 管。

    python retrieval_router.py "海南自由贸易港土地管理条例第三条是什么"
    python retrieval_router.py "海南省消防条例和自贸港消防条例有什么区别"
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reasoning_retrieval as rr
import retrieval

from paths import DEFAULT_DATABASE as DEFAULT_DB

ARTICLE_REF = re.compile(
    r"第[一二三四五六七八九十百]+条|第\d+条|第[一二三四五六七八九十百]+款")
COMPARISON = re.compile(
    r"(区别|相比|对比|差异|沿革|演变|替代|取代|新旧|版本|"
    r"废止了|被废止|同时废止|哪些.{0,6}废止|变迁)")
TABLE_TOPIC = re.compile(r"(执法事项|指导目录|事项清单|权力清单|处罚事项)")

# 每种策略给多少候选文档
DOCS = {"article_exact": 2, "single_doc_tree": 1, "single_doc_flat": 1,
        "multi_doc": 6, "default_flat": 4}


def comparison_docs(query: str, named_count: int) -> int:
    """比较题要几份候选：按被比较的实体数放大，再留出旧版/新版的位置。

    固定 6 份在"只比两份文件"时浪费，在"点三份以上 + 要看沿革"时又不够。
    """
    # 显式数量最可靠："五个领域""三种规定""等 8 件"
    CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}
    explicit = 0
    match = re.search(r"([一二三四五六七八九十]|\d+)\s*(个|项|份|种|类|部|件|条)",
                      query)
    if match:
        token = match.group(1)
        explicit = int(token) if token.isdigit() else CN.get(token, 0)
    entities = max(named_count, explicit)
    base = max(4, entities * 2)
    if re.search(r"(沿革|演变|变迁|版本|新旧|替代|取代)", query):
        base += 2
    return min(base, 10)


def _decision(strategy: str, reason: str, signals: dict,
              path: str = "flat", refuse: bool = False,
              caveats: list[str] | None = None,
              docs_wanted: int | None = None) -> dict:
    return {"strategy": strategy, "path": path,
            "docs_wanted": docs_wanted or DOCS.get(strategy, 4),
            "refuse": refuse, "reason": reason,
            "signals": signals, "caveats": caveats or []}


def route_query(query: str, database: str = DEFAULT_DB) -> dict:
    """判断一个问题该走哪条检索路。"""
    query = retrieval.normalize_query(query)
    in_scope, scope_reason = rr.scope_query(query)
    named = [os.path.splitext(os.path.basename(p))[0]
             for p in retrieval.docs_named_in(query, database)]
    has_article_ref = bool(ARTICLE_REF.search(query))
    is_comparison = bool(COMPARISON.search(query))
    is_table = bool(TABLE_TOPIC.search(query))
    signals = {"named_documents": named[:4],
               "article_ref": has_article_ref,
               "comparison": is_comparison,
               "table_topic": is_table}

    if not in_scope:
        return _decision("out_of_scope", scope_reason, signals,
                         refuse=True)

    # 点名了文件 + 指定了条号：直接去那份文件里定位
    if named and has_article_ref:
        return _decision(
            "article_exact",
            f"问题点名了《{named[0][:24]}》并指定了条号，直接在条文索引里定位",
            signals)

    # 比较题常常点名不止一份文件，或者要点出沿革
    if is_comparison:
        docs = comparison_docs(query, len(named))
        return _decision(
            "multi_doc",
            "比较／沿革类问法，需要多份文档一起看"
            + (f"（点名 {len(named)} 份）" if named else "")
            + f"，候选按比较实体数给到 {docs} 份",
            signals, docs_wanted=docs)

    # 只点名一份文件：有章/条结构的沿树选点；没结构的留在扁平路
    # （树里那种文档只有 800 字一页、预览 34 字，不如 180 字一句的段块）
    if len(named) == 1:
        paths = retrieval.docs_named_in(query, database)
        parsed = retrieval.parse_articles(paths[0], {}) if paths else []
        # 表格型目录（能切出"第X条"但条号乱序）没有可靠的事项边界：
        # 当前正文是列式摊平的（事项名被折成 10 字一行的多行），硬切会把事项
        # 和条款错配。宁可拒答并说明，也不给一份看着像、其实串了行的清单。
        if parsed and not retrieval.looks_like_articles(parsed):
            return _decision(
                "table_catalog",
                f"《{named[0][:24]}》是表格型目录，正文按列摊平、"
                "事项名被折行，无法可靠切出事项—条款对应关系",
                signals, refuse=True,
                caveats=["需要事项级检索时，请按官方表格原件核对"])
        if parsed and retrieval.looks_like_articles(parsed):
            return _decision(
                "single_doc_tree",
                f"问题点名了《{named[0][:24]}》，它有章/条结构，沿树选点",
                signals, path="tree")
        return _decision(
            "single_doc_flat",
            f"问题点名了《{named[0][:24]}》，它没有条文结构，"
            "按句切块走扁平检索",
            signals)

    # 表格型目录：当前只有页级结构，没有事项级索引
    if is_table:
        try:
            import catalog_index
            if catalog_index.load_items(database):
                return _decision(
                    "catalog_item",
                    "问题指向《执法事项指导目录》，事项级索引已建，按事项检索",
                    signals, path="catalog",
                    caveats=["部分章节的条款列抽取不完整，此类条目只给事项名与法律名"])
        except Exception:
            pass
        return _decision(
            "table_catalog",
            "问题指向《执法事项指导目录》这类表格型文件；目前只有页级结构，"
            "没有事项级索引，不能可靠回答",
            signals, refuse=True,
            caveats=["可以改为点名某份目录文件，沿树按页读取"])

    return _decision(
        "default_flat",
        "未点名具体文件、也不是比较或目录类问法，按条文＋段块召回",
        signals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--database", default=DEFAULT_DB)
    args = parser.parse_args()
    decision = route_query(args.query, args.database)
    print(f"策略：{decision['strategy']}　路径：{decision['path']}"
          f"　候选文档：{decision['docs_wanted']}"
          f"　{'拒答' if decision['refuse'] else '检索'}")
    print(f"理由：{decision['reason']}")
    print(f"信号：{decision['signals']}")
    if decision["caveats"]:
        print("注意：" + "；".join(decision["caveats"]))


if __name__ == "__main__":
    main()
