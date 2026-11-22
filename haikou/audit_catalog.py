"""事项级目录索引的离线审计。

目录正文是列式摊平的，事项级索引靠官方 PDF 附件 + 坐标还原。这里验：事项形态、
检索命中、效力状态、干净/串行条款的处理、路由接入、无匹配时拒答。

    python audit_catalog.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalog_index

from paths import DEFAULT_DATABASE as DB


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    ok = None if ok is None else bool(ok)
    label = "通过" if ok else ("跳过" if ok is None else "问题")
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok


def main():
    rows = catalog_index.load_items(DB)
    if not rows:
        print("没有事项索引：先跑 python catalog_index.py --build --only 2023")
        return
    results = []
    good = [r for r in rows
            if r["item"].startswith("对")
            and re.search(r"(行政处罚|行政强制)", r["item"])]

    def coverage():
        ratio = len(good) / len(rows)
        return ratio >= 0.95, (f"事项 {len(rows)} 条，形态合格 {len(good)}"
                               f"（{ratio * 100:.0f}%）")
    results.append(probe("事项名形态合格率 ≥95%", coverage))

    def clause_clean():
        clean = [r for r in rows
                 if re.search(r"第[一二三四五六七八九十百\d]+条", r["article"])
                 and len(r["article"]) >= 20]
        return len(clean) > 0, (f"条款可直引 {len(clean)}/{len(rows)}"
                                f"（{len(clean) / len(rows) * 100:.0f}%），"
                                "其余按原文不完整处理")
    results.append(probe("条款可直引比例（仅报告，不作门槛）", clause_clean))

    def search_hits():
        hits = catalog_index.search_items("对未经登记擅自设立代表机构怎么处罚",
                                          DB, 3)
        ok = bool(hits) and hits[0]["item_row"]["item"].startswith("对未经登记")
        return ok, (f"首位：{hits[0]['item_row']['item'][:36]}" if hits else "无命中")
    results.append(probe("事项检索：首位是正确事项", search_hits))

    def force_attached():
        result = catalog_index.answer_catalog(
            "对未经登记擅自设立代表机构怎么处罚", DB,
            chat_fn=lambda *args, **kwargs: "（假模型）")
        ok = bool(result["sources"]) and all(
            s["status"] for s in result["sources"])
        return ok, f"来源状态：{[s['status'] for s in result['sources'][:3]]}"
    results.append(probe("事项来源带效力状态", force_attached))

    def unclean_flagged():
        result = catalog_index.answer_catalog(
            "擅自改变物业管理用房用途怎么处罚", DB,
            chat_fn=lambda *args, **kwargs: "（假模型）")
        return bool(result["notes"]), (result["notes"][:1] or "没有提示")
    results.append(probe("串行条款会被标出来（不当作依据）", unclean_flagged))

    def route_wired():
        from reasoning_retrieval import ask
        result = ask("综合行政执法事项指导目录里对无证经营怎么处罚", DB,
                     chat_fn=lambda *args, **kwargs: "（假模型）")
        return ((result.get("route", {}).get("strategy") == "catalog_item"
                 and bool(result["sources"])),
                f"路由 {result.get('route', {}).get('strategy')}，"
                f"来源 {len(result['sources'])} 条")
    results.append(probe("ask() 走事项级索引", route_wired))

    def no_match_refuses():
        result = catalog_index.answer_catalog(
            "量子纠缠与超导磁体的冷却", DB,
            chat_fn=lambda *args, **kwargs: "不应调用")
        return (result["answer"] is None
                and result["refusal_stage"] == "catalog_empty"), \
            f"阶段 {result['refusal_stage']}"
    results.append(probe("事项索引无匹配时拒答", no_match_refuses))

    passed = sum(1 for ok in results if ok is True)
    skipped = sum(1 for ok in results if ok is None)
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")


if __name__ == "__main__":
    main()
