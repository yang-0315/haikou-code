"""Functional audit of the force-aware reasoning retrieval.

Probes the places where a retrieval loop usually breaks: empty recall, article
numbers that repeat across documents, duplicate document basenames, version
groups, status caching, model output that is not JSON, and the refusal
contract. 除最后一条真实联调外都用注入的假模型，离线可跑。
"""
import json
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

import docstore
import reasoning_retrieval as rr
from force_layer import ForceLayer

from paths import DEFAULT_DATABASE as DB


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    # 归一成真正的布尔：检查里常写 `x and some_list`，那会返回列表——
    # 标签按真值判、计数按 is True 判，两个口径就会不一致。
    ok = None if ok is None else bool(ok)
    label = "通过" if ok else ("跳过" if ok is None else "问题")
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok, detail


def fake_model(payload: str):
    """离线假模型：按脚本回答选择阶段的 JSON。"""
    def call(messages, temperature=0.0, **kwargs):
        return payload
    return call


def main():
    layer = ForceLayer(DB)
    results = []

    # 1a. 辖区外：确定性挡掉，不调模型
    def out_of_scope():
        docs = rr.recall(DB, "上海自贸区临港新片区人才落户条件", 4)
        ok, reason = rr.scope_query("上海自贸区临港新片区人才落户条件")
        return len(docs) == 0 and not ok, f"召回 {len(docs)} 份；{reason}"
    results.append(probe("空召回：非海南辖区的问题返回 0 份", out_of_scope))

    # 1b. 语料外主题：召回层挡不住（实测词形相似度分不开），必须由模型拒答
    def off_topic_refusal():
        result = rr.ask("量子计算机的制冷方案", DB, 4,
                        chat_fn=fake_model('{"answerable": false, '
                                           '"reason": "目录中无相关条文"}'))
        return (result["answer"] is None
                and result["refusal_stage"] == "model"), \
            f"阶段 {result['refusal_stage']}；{(result['refusal'] or '')[:60]}"
    results.append(probe("空召回：语料外主题由模型拒答（注入假模型）",
                         off_topic_refusal))

    # 1c. 真调模型看它会不会硬答（需要联网）
    def live_refusal():
        try:
            result = rr.ask("量子计算机的制冷方案", DB, 4)
        except Exception as exc:
            return None, f"跳过（{type(exc).__name__}，需联网）"
        return result["answer"] is None, \
            f"阶段 {result['refusal_stage']}；{(result['refusal'] or '')[:60]}"
    results.append(probe("空召回：真模型也拒答（联网）", live_refusal))

    # 2. 正常召回
    def normal_recall():
        docs = rr.recall(DB, "免税店的设立需要什么条件", 4)
        docs_ok = len(docs) > 0
        outline, forces = rr.annotated_outline(layer, docs, DB)
        return docs_ok and "效力：" in outline, \
            f"召回 {len(docs)} 份，目录含效力标注：{'效力：' in outline}"
    results.append(probe("正常召回并生成带效力的目录", normal_recall))

    # 3. 同一份文件的两个副本是否被合并
    def duplicate_docs():
        docs = rr.recall(DB, "免税店的设立需要什么条件", 6)
        keys = [rr.doc_key(d["doc"]) for d in docs]
        return len(keys) == len(set(keys)), f"{len(keys)} 份中有 {len(keys)-len(set(keys))} 个重复键"
    results.append(probe("语料与库中的同一文件不被重复召回", duplicate_docs))

    # 4. 同名合并只在两版正文一致时才算安全
    def version_group():
        docs = rr.recall(DB, "岛内居民购买零关税商品有什么要求", 6)
        merged = [d for d in docs if d.get("variants")]
        if not merged:
            return True, "本轮未召回到版本组（跳过）"
        doc = merged[0]
        again = rr.recall(DB, "岛内居民购买零关税商品有什么要求", 6)
        same = [d for d in again if d.get("variants")][0]
        # 合并只能发生在两版正文完全一致（同一份重复入库）时；正文不同的
        # 必须标成 divergent，否则等于偷偷丢掉一个版本。
        safe = doc["duplicate_copy"] or doc["divergent"]
        deterministic = doc["doc"] == same["doc"] and doc["duplicate_copy"] == same["duplicate_copy"]
        # 重复副本里要用目录登记的那个正式名，避免正文没错但文件名叫错
        official = (not doc["duplicate_copy"]
                    or doc["doc"] in rr.catalog_titles(DB))
        return safe and deterministic and official, \
            (f"代表版本《{doc['doc'][:24]}》；重复入库={doc['duplicate_copy']}"
             f"；正文不同={doc['divergent']}"
             f"；采用目录正式名={official}"
             f"；未入正文 {[(v['doc'][:16], v['status']) for v in doc['variants']]}")
    results.append(probe("版本组只留现行有效的那一版进正文", version_group))

    # 5. 条号在多份文档中重复时，裸条号是否会过度选中
    def bare_article_numbers():
        docs = rr.recall(DB, "第十条 规定", 3)
        if len(docs) < 2:
            return None, "候选不足以触发（跳过）"
        blocks, notes = rr.select_blocks(layer, DB, docs,
                                        {"read": ["第十条"]})  # 裸条号
        return len(blocks) == 1, \
            (f"跨 {len(docs)} 份候选只取了 {len(blocks)} 条"
             + (f"；{notes[0]}" if notes else ""))
    results.append(probe("裸条号不会跨文档过度选中", bare_article_numbers))

    # 6. 模型指名文档时，要按指名的文档取，且只取一份
    def named_document():
        docs = rr.recall(DB, "免税店的设立需要什么条件", 4)
        blocks, _ = rr.select_blocks(
            layer, DB, docs, {"read": ["海南自由贸易港岛内居民消费零关税进境商品经营主体及免税店管理办法|第八条"]})
        if not blocks:
            return False, "指名文档却一条都没取到"
        return (len(blocks) == 1 and "免税店管理办法" in blocks[0]["doc"]
                and blocks[0]["article"] == "第八条"), \
            f"取到《{blocks[0]['doc'][:30]}》{blocks[0]['article']}"
    results.append(probe("指名文档时按文档取条且不重复", named_document))

    # 7. 同一文档名的效力缓存
    def several_articles_one_doc():
        docs = rr.recall(DB, "免税店的设立需要什么条件", 4)
        name = "海南自由贸易港岛内居民消费零关税进境商品经营主体及免税店管理办法"
        blocks, _ = rr.select_blocks(
            layer, DB, docs,
            {"read": [f"{name}|第八条", f"{name}|第十条", "第十一条"]})
        # 同一份文件的多个条号必须都能进来：早期把「已取过该文件」当成了
        # 「这份文件的其余条号也不要」，模型选 4 条只剩 1 条。
        return len(blocks) == 3, \
            f"选了 3 条，实际取到 {[b['article'] for b in blocks]}"
    results.append(probe("同一份文件的多个条号都要能取到", several_articles_one_doc))

    # 8. 同一文档名的效力缓存
    def status_cache():
        a = layer.status_for("海南省关于在琼台湾同胞待遇的若干规定")
        b = layer.status_for("海南省关于在琼台湾同胞待遇的若干规定")
        return a["status"] == b["status"] == "效力存疑", \
            f"两次判定 {a['status']} / {b['status']}"
    results.append(probe("同名文档的效力缓存一致且正确", status_cache))

    # 8. 只给标题时能否自解析正文
    def title_only():
        st = layer.status_for("海南省植物检疫实施办法")
        return st["status"] == "现行有效", f"判定 {st['status']}（该文件有修订记录）"
    results.append(probe("只给标题也能读到正文（修订记录生效）", title_only))

    # 9. 模型输出非 JSON：ask 要走拒答，不能抛栈
    def bad_json():
        result = rr.ask("免税店的设立需要什么条件", DB, 4,
                        chat_fn=fake_model("抱歉，我无法回答"))
        return (result["answer"] is None
                and result["refusal_stage"] == "select_parse"), \
            f"阶段 {result['refusal_stage']}；{(result['refusal'] or '')[:60]}"
    results.append(probe("模型返回非 JSON 时可捕获", bad_json))

    # 10. 目录要给出文档结构，不能只有命中的那几条
    def outline_is_structural():
        docs = rr.recall(DB, "海南自由贸易港建设投资基金的运作方式是什么", 4)
        if not docs:
            return None, "本轮无候选（跳过）"
        doc = docs[0]
        return len(doc["articles"]) > 5, \
            (f"《{doc['doc'][:26]}》目录列出 {len(doc['articles'])} 条，"
             f"其中命中标记 {sum(1 for a in doc['articles'] if a.get('matched'))} 条")
    results.append(probe("目录给出整份文档的条文而非仅命中条",
                         outline_is_structural))

    # 11. 答复上下文长度
    def context_size():
        docs = rr.recall(DB, "免税店的设立需要什么条件", 4)
        outline, _ = rr.annotated_outline(layer, docs, DB)
        total = sum(len(a["text"]) for d in docs for a in d["articles"])
        return len(outline) < 60000 and total < 60000, \
            f"目录 {len(outline)} 字符，条文合计 {total} 字符"
    results.append(probe("目录与候选条文规模在可控范围", context_size))

    # 12. 后置去重：重复副本合并，但不吞掉真正的版本差异
    def post_dedupe():
        import retrieval
        articles = retrieval.load_articles(DB)
        articles += retrieval.load_articles_from_paths(
            docstore.library_files(DB), DB)
        hits = retrieval.Index(articles).search("免税店的设立需要什么条件", 6)
        if not any(h["doc"].endswith("（试行）") for h in hits):
            return None, "本轮命中里没有重复副本（跳过）"
        raw = layer.review(hits, "drop")
        merged = layer.review(hits, "drop", dedupe=True)
        return (len(merged["kept"]) < len(raw["kept"])
                and bool(merged["duplicates"])), \
            (f"去重前 {len(raw['kept'])} 条 → 去重后 {len(merged['kept'])} 条；"
             f"合并说明 {len(merged['duplicates'])} 组，"
             f"代表名用目录正式名="
             f"{all(n['representative'] in rr.catalog_titles(DB) for n in merged['duplicates'])}")
    results.append(probe("后置去重：重复副本合并并说明", post_dedupe))

    def post_dedupe_keeps_versions():
        same = "海南自由贸易港反走私条例"
        fake = [{"doc": same, "article": "第一条", "text": "甲公司版正文内容"},
                {"doc": same, "article": "第一条", "text": "乙公司版正文内容"}]
        kept, notes = layer.dedupe_copies(fake)
        # 正文不一致 = 真版本差异，两条都得留，且要标出来
        return (len(kept) == 2 and notes and notes[0]["divergent"]), \
            (f"保留 {len(kept)} 条；divergent="
             f"{bool(notes and notes[0]['divergent'])}")
    results.append(probe("后置去重不吞掉正文不同的版本", post_dedupe_keeps_versions))

    passed = sum(1 for ok, _ in results if ok is True)
    skipped = sum(1 for ok, detail in results
                  if ok is None or (detail or "").endswith("（跳过）"))
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")


if __name__ == "__main__":
    main()
