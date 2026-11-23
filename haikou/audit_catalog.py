"""事项级目录索引的离线审计。

目录正文是列式摊平的，事项级索引靠官方 PDF 附件 + 坐标还原。这里验：事项形态、
检索命中、效力状态、干净/串行条款的处理、路由接入、无匹配时拒答。

    python audit_catalog.py
"""
import os
import re
import sys
from collections import defaultdict

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
    by_year = defaultdict(list)
    for row in rows:
        by_year[row.get("catalog_year")].append(row)
    good = [r for r in rows
            if r["item"].startswith("对")
            and re.search(r"(行政处罚|行政强制)", r["item"])]

    def versions_present():
        missing = sorted({2022, 2023, 2024} - set(by_year))
        detail = "；".join(
            f"{year} 版 {len(by_year.get(year, []))} 项"
            for year in (2022, 2023, 2024))
        return not missing, detail
    results.append(probe("2022／2023／2024 三版目录均已建立",
                         versions_present))

    def coverage():
        details = []
        ok = True
        for year in (2022, 2023, 2024):
            group = by_year[year]
            year_good = [row for row in group
                         if row["item"].startswith("对")
                         and re.search(r"(行政处罚|行政强制)", row["item"])]
            ratio = len(year_good) / len(group)
            ok = ok and ratio >= 0.95
            details.append(
                f"{year}: {len(year_good)}/{len(group)}（{ratio * 100:.1f}%）")
        return ok, "；".join(details)
    results.append(probe("各版事项名形态合格率 ≥95%", coverage))

    def clause_clean():
        details = []
        ok = True
        for year in (2022, 2023, 2024):
            group = by_year[year]
            clean = [r for r in group
                     if catalog_index.ARTICLE_RE.search(r["article"])
                     and len(r["article"]) >= 20]
            ratio = len(clean) / len(group)
            ok = ok and ratio >= 0.95
            details.append(
                f"{year}: {len(clean)}/{len(group)}（{ratio * 100:.2f}%）")
        return ok, "；".join(details)
    results.append(probe("各版条款可直引比例 ≥95%", clause_clean))

    def clause_starts():
        details = []
        ok = True
        total = 0
        started = 0
        for year in (2022, 2023, 2024):
            group = by_year[year]
            starts = [r for r in group
                      if catalog_index.ARTICLE_RE.match(
                          r["article"].removeprefix("（接上页）"))]
            ok = ok and len(starts) == len(group)
            total += len(group)
            started += len(starts)
            details.append(f"{year}: {len(starts)}/{len(group)}")
        return ok, "；".join(details) + (
            f"；合计 {started}/{total}")
    results.append(probe("各版条款列从法定条号起读", clause_starts))

    def continuation_merged():
        row = next((r for r in by_year[2023] if r["page"] == 13
                    and r["page_end"] == 14 and r["seq_total"] == 9), None)
        return bool(row and "第二十五条" in row["article"]), (
            "第9项已合并第13–14页续段" if row
            else "未找到第9项跨页续段")
    results.append(probe("跨页事项已合并且保留续页条款", continuation_merged))

    def search_hits():
        hits = catalog_index.search_items("对未经登记擅自设立代表机构怎么处罚",
                                          DB, 3)
        ok = (bool(hits)
              and hits[0]["item_row"]["item"].startswith("对未经登记")
              and hits[0]["catalog_year"] == 2024)
        return ok, (
            f"首位：{hits[0]['catalog_year']} 版 "
            f"{hits[0]['item_row']['item'][:30]}" if hits else "无命中")
    results.append(probe("事项检索：首位是正确事项", search_hits))

    def force_attached():
        result = catalog_index.answer_catalog(
            "对未经登记擅自设立代表机构怎么处罚", DB,
            chat_fn=lambda *args, **kwargs: "（假模型）")
        ok = bool(result["sources"]) and all(s["status"]
                                             for s in result["sources"])
        evidence = result["sources"][0].get("evidence", {}) if ok else {}
        ok = bool(ok and evidence.get("text")
                  and evidence.get("page"))
        return ok, (
            f"来源状态：{[s['status'] for s in result['sources'][:3]]}；"
            f"证据：第{evidence.get('page')}页 {evidence.get('text', '')[:28]}")
    results.append(probe("事项来源带效力状态、页码与条款原文", force_attached))

    def split_fragment_repaired():
        row = next((r for r in by_year[2023] if r["page"] == 125
                    and r["seq_total"] == 267), None)
        ok = bool(row and row["item"].startswith("对拍卖企业雇佣")
                  and row["article"].startswith("第四十七条"))
        return ok, (row["article"][:45] if row else "未找到事项267")
    results.append(probe("单元格内分行片段并回完整条款", split_fragment_repaired))

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
    failed = len(results) - passed - skipped
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")
    if failed:
        print(f"问题 {failed} 项")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
