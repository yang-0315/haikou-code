"""效力挂到树上以后，树本身对不对。

挂维度不能只是"字段加上了"。这里查六件事，全库跑，不是抽样：

  1. 树上的条文 == 检索层的条文（同一段文字，逐字比）——两条路不能各说各话
  2. 每个节点都带 force，且与文档级判定一致
  3. 页码跨度合法，子节点落在父节点范围内
  4. node_id 唯一、章节条数对得上（没漏节点）
  5. 包上 ForceAwareClient 走真实索引：三个接缝都带 force
  6. 取不存在的文档要报错，不能静默返回空树
  7. 树上的文档级效力与检索层判定一致（全库 814 份）
  8. 真实 PageIndexClient 的索引：get_tree 返回的节点确实带 force
  9. 嵌套节点（含子节点的那些）也带 force，且文号能对上 manifest

    python audit_tree_force.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrieval
import tree_index
from force_layer import ForceLayer
from pageindex_force_adapter import (ForceAwareClient, real_selftest,
                                     real_stats)

from paths import DEFAULT_DATABASE as DB


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    label = "通过" if ok else ("跳过" if ok is None else "问题")
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok, detail


def flat_by_file() -> dict[str, list[dict]]:
    """检索层认到的条文，按**路径**归档。

    按文件名归档会踩坑：同一份文件可能同时存在于根语料与 library 里（文件名
    相同），后写入的那个会把前面覆盖掉，于是拿残缺副本去比完整副本，凭空
    多出一堆"差异"。

    还要**跟着检索层的口径过滤**：表格型目录（条号乱序的"执法事项指导目录"）
    不进条文索引，这里也要同样排除，否则会拿几千条伪条文去比一棵没有条文的树。
    """
    out: dict[str, list[dict]] = {}
    for path in tree_index.docstore.corpus_files(DB) \
            + tree_index.docstore.library_files(DB):
        parsed = retrieval.parse_articles(path, {})
        out[path] = parsed if retrieval.looks_like_articles(parsed) else []
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main():
    store = tree_index.LocalTreeStore(DB)
    layer = ForceLayer(DB)
    docs = store.documents()
    results = []

    # 1. 树上的条文与检索层逐字一致
    def tree_matches_retrieval():
        flat = flat_by_file()
        mismatched, compared = [], 0
        for record in docs:
            text = store.text_of(record)
            tree_articles = {node["article"]: norm(text[node["start_char"]:node["end_char"]])
                             for node in tree_index.walk_articles(record["structure"])}
            flat_articles = {a["article"]: norm(a["text"])
                             for a in flat.get(record["path"], [])}
            compared += 1
            if tree_articles == flat_articles:
                continue
            only_tree = sorted(set(tree_articles) - set(flat_articles))
            only_flat = sorted(set(flat_articles) - set(tree_articles))
            changed = [a for a in set(tree_articles) & set(flat_articles)
                       if tree_articles[a] != flat_articles[a]]
            mismatched.append(f"{record['file'][:34]}（多 {only_tree[:2]} "
                              f"少 {only_flat[:2]} 改 {changed[:2]}）")
        return not mismatched, (f"比对 {compared} 份；不一致 {len(mismatched)} 份"
                                + ("：" + "；".join(mismatched[:3]) if mismatched else ""))
    results.append(probe("树上的条文与检索层逐字一致", tree_matches_retrieval))

    # 2. 每个节点都带 force，且与文档级判定一致
    def every_node_carries_force():
        missing, wrong = [], []
        for record in docs:
            document_status = record["status"]
            for node in tree_index.walk(record["structure"]):
                force = node.get("force")
                if not isinstance(force, dict):
                    missing.append(record["file"])
                    break
                article = node.get("article", "")
                expected = layer.article_status_for(
                    record["title"], article, document_status,
                    file=record["file"])["status"] if article else \
                    document_status["status"]
                if force.get("status") != expected:
                    wrong.append(record["file"])
                    break
        return (not missing and not wrong), \
            (f"{len(docs)} 份、{sum(d['nodes'] for d in docs)} 个节点；"
             f"缺 force {len(missing)} 份，状态不一致 {len(wrong)} 份")
    results.append(probe("每个节点都带 force 且与文档级判定一致",
                         every_node_carries_force))

    # 3. 跨度合法
    def spans_valid():
        bad = []
        for record in docs:
            text = store.text_of(record)
            pages = record["pages"]
            for node in tree_index.walk(record["structure"]):
                ok = (1 <= node["start_index"] <= node["end_index"] <= pages
                      and 0 <= node["start_char"] <= node["end_char"] <= len(text))
                if not ok:
                    bad.append(f"{record['file'][:30]}:{node['node_id']}")
                    break
        return not bad, f"越界 {len(bad)} 处" + (f"：{bad[:3]}" if bad else "")
    results.append(probe("页码与字符跨度都在界内", spans_valid))

    # 4. node_id 唯一
    def node_ids_unique():
        bad = []
        for record in docs:
            ids = [node["node_id"] for node in tree_index.walk(record["structure"])]
            if len(ids) != len(set(ids)):
                bad.append(record["file"])
        return not bad, f"重号 {len(bad)} 份"
    results.append(probe("node_id 在同一份文档内唯一", node_ids_unique))

    # 5. 走真实索引的 ForceAwareClient
    def adapter_over_real_store():
        client = ForceAwareClient(tree_index.LocalTreeStore(DB), DB)
        names = ["海南自由贸易港禁毒条例",
                 "海南自由贸易港建设投资基金管理办法",
                 "海南省太阳能热水系统建筑应用管理办法"]
        problems = []
        for name in names:
            record = client._client.find(name)
            tree = client.get_tree(record["file"])
            ocr = client.get_ocr(record["file"], format="page")
            info = client.get_document(record["file"])
            flat = layer.status_for(record["title"])["status"]
            nodes = list(tree_index.walk(tree["result"])) \
                if "nodes" in json_dumps_safe(tree["result"][0]) else tree["result"]
            if tree["force"]["status"] != flat:
                problems.append(f"{name[:18]} 目录 force={tree['force']['status']} 判定={flat}")
            if any(not n.get("force") for n in nodes):
                problems.append(f"{name[:18]} 有节点没 force")
            if any(not p.get("force") for p in ocr["result"]):
                problems.append(f"{name[:18]} 有页没 force")
            if info.get("force", {}).get("status") != flat:
                problems.append(f"{name[:18]} 元数据 force 不一致")
        return not problems, ("三处接缝（目录／正文页／元数据）都带 force 且与判定一致"
                              + ("；" + "；".join(problems) if problems else ""))
    results.append(probe("ForceAwareClient 跑真实索引时三处都带 force",
                         adapter_over_real_store))

    # 6. 不存在的文档要报错
    def missing_doc_raises():
        try:
            store.get_tree("这份文档不存在")
            return False, "没有报错"
        except KeyError:
            return True, "抛 KeyError（未静默返回空树）"
    results.append(probe("取不存在的文档会报错", missing_doc_raises))

    # 7. 树上的文档级效力 == 检索层的判定（含文号证据，全库比）
    def status_matches_layer():
        mismatched = []
        for record in docs:
            expected = layer.status_for_file(record["path"])["status"]
            if record["status"]["status"] != expected:
                mismatched.append(f"{record['file'][:32]}"
                                  f"（树 {record['status']['status']}／"
                                  f"判定 {expected}）")
        return not mismatched, (f"{len(docs)} 份逐份比对；不一致 {len(mismatched)} 份"
                                + ("：" + "；".join(mismatched[:3])
                                   if mismatched else ""))
    results.append(probe("树上的文档级效力与检索层判定一致",
                         status_matches_layer))

    # 8. 真实 PageIndexClient（上游自己建的索引）——节点带 force 的实证
    def real_pageindex_client():
        ok, detail = real_selftest(DB)
        if not ok and ("跳过" in detail or "缺" in detail):
            return None, detail
        return ok, detail
    results.append(probe("真实 PageIndexClient：上游树节点带 force",
                         real_pageindex_client))

    # 9. 嵌套节点带 force —— 只验一层不够，含子节点的节点也要各自带
    def nested_nodes_carry_force():
        stats = real_stats(DB)
        if not stats:
            return None, "真实索引不可用（见上一项），跳过嵌套检查"
        nested_docs = {num: item for num, item in stats.items()
                       if item["nested"]}
        if not nested_docs:
            return None, ("真实索引里没有含子节点的树"
                          "（样例源的章被上游合并了），跳过")
        problems = [f"{num}：{item['bare_nested']} 个嵌套节点没 force"
                    for num, item in nested_docs.items()
                    if item["bare_nested"]]
        shape = "；".join(
            f"{num}（{item['title'][:16]}）{item['nested']}/{item['nodes']} "
            f"个节点含子节点，最深 {item['depth']} 层"
            for num, item in nested_docs.items())
        return not problems, (shape + ("；" + "；".join(problems)
                                       if problems else ""))
    results.append(probe("嵌套节点也带 force（真实客户端）",
                         nested_nodes_carry_force))

    # 10. 表格型目录不进条文索引——但正文不能丢，仍以页节点入树
    def table_catalogs_excluded():
        found = []
        for record in docs:
            parsed = retrieval.parse_articles(record["path"], {})
            if not parsed or retrieval.looks_like_articles(parsed):
                continue
            found.append(record)
        if not found:
            return None, "本轮没有表格型目录（跳过）"
        problems, detail = [], []
        for record in found:
            articles = list(tree_index.walk_articles(record["structure"]))
            page_nodes = [n for n in tree_index.walk(record["structure"])
                          if not n.get("article")]
            detail.append(f"《{record['title'][:20]}》{record['nodes']} 页节点"
                          f"，条文节点 {len(articles)}")
            if articles:
                problems.append(f"{record['title'][:20]}：仍被当成条文入库")
            if not page_nodes:
                problems.append(f"{record['title'][:20]}：正文没进树")
        return not problems, ("；".join(detail)
                              + ("；" + "；".join(problems) if problems else ""))
    results.append(probe("表格型目录不进条文索引但仍以页节点入树",
                         table_catalogs_excluded))

    passed = sum(1 for ok, _ in results if ok is True)
    skipped = sum(1 for ok, _ in results if ok is None)
    failed = len(results) - passed - skipped
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")
    if failed:
        print(f"问题 {failed} 项")
        raise SystemExit(1)


def json_dumps_safe(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    main()
