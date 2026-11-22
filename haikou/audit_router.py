"""检索策略路由的离线审计。

路由错了，后面的检索再准也白搭（比如把比较题当单文档、或对表格目录硬答）。
这里逐条验：六种策略各归其位、拒绝的口子确实拒绝、繁简归一后决策不变、
同一问题两次决策一致、每个决定都带信号与理由。

    python audit_router.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from retrieval_router import route_query

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
    results = []

    def case(query, strategy, path="flat", refuse=False):
        decision = route_query(query, DB)
        ok = (decision["strategy"] == strategy and decision["path"] == path
              and decision["refuse"] is refuse)
        return ok, (f"{decision['strategy']}/{decision['path']}"
                    f"/{'拒答' if decision['refuse'] else '检索'}"
                    f"（应为 {strategy}/{path}"
                    f"/{'拒答' if refuse else '检索'}）")

    results.append(probe(
        "点名文件 + 条号 → article_exact",
        lambda: case("海南自由贸易港土地管理条例第三条是什么", "article_exact")))
    results.append(probe(
        "只点名一份文件 → single_doc_tree",
        lambda: case("海南自由贸易港土地管理条例对土地出让是怎么规定的",
                     "single_doc_tree", "tree")))
    results.append(probe(
        "点名一份『没有条文结构』的文件 → single_doc_flat",
        lambda: case("《百万英才兴海南行动计划（2026—2035年）》提出什么目标",
                     "single_doc_flat")))
    results.append(probe(
        "比较／沿革 → multi_doc（6 份候选）",
        lambda: case("海南省消防条例和海南自由贸易港消防条例有什么区别",
                     "multi_doc")))
    results.append(probe(
        "表格型目录（已建事项索引）→ catalog_item",
        lambda: case("综合行政执法事项指导目录里对无证经营怎么处罚",
                     "catalog_item", "catalog")))
    results.append(probe(
        "点名一份表格型目录 → 走事项级索引",
        lambda: case("海南省综合行政执法事项指导目录（2023年版）里"
                     "对无证经营怎么处罚", "catalog_item", "catalog")))
    results.append(probe(
        "非海南辖区 → out_of_scope 且拒答",
        lambda: case("上海自贸区临港新片区的人才落户条件", "out_of_scope",
                     refuse=True)))
    results.append(probe(
        "普通问题 → default_flat",
        lambda: case("免税店的设立需要什么条件", "default_flat")))

    def traditional_same():
        a = route_query("遊艇碼頭建設程序是什麼", DB)
        b = route_query("游艇码头建设程序是什么", DB)
        return (a["strategy"] == b["strategy"] and a["path"] == b["path"]
                and a["docs_wanted"] == b["docs_wanted"]), \
            f"繁体 {a['strategy']}／简体 {b['strategy']}"
    results.append(probe("繁简问题走同一条路", traditional_same))

    def deterministic():
        q = "海南自由贸易港土地管理条例对土地出让是怎么规定的"
        first, second = route_query(q, DB), route_query(q, DB)
        return first == second, f"两次决定一致：{first == second}"
    results.append(probe("同一问题决策可复现", deterministic))

    def adaptive_candidates():
        """比较题候选随被比较实体数与"沿革"意图放大。"""
        two = route_query("海南省消防条例和海南自由贸易港消防条例有什么区别", DB)
        more = route_query("土地管理、旅游、城乡规划、消防和养犬这五个领域"
                           "新旧规定的沿革有什么区别", DB)
        ok = (two["strategy"] == more["strategy"] == "multi_doc"
              and 4 <= two["docs_wanted"] < more["docs_wanted"] <= 10)
        return ok, (f"两实体 {two['docs_wanted']} 份｜"
                    f"五实体+沿革 {more['docs_wanted']} 份")
    results.append(probe("比较题候选规模自适应", adaptive_candidates))

    def decisions_explained():
        queries = ["免税店的设立需要什么条件",
                   "海南自由贸易港土地管理条例第三条是什么",
                   "综合行政执法事项指导目录怎么处罚",
                   "上海自贸区人才落户"]
        missing = [q for q in queries
                   if not route_query(q, DB).get("reason")
                   or "signals" not in route_query(q, DB)]
        return not missing, (f"{len(queries)} 条决策都带 reason/signals"
                             if not missing else f"缺解释：{missing}")
    results.append(probe("每个决定都带理由与信号", decisions_explained))

    def refusal_stage_wired():
        """ask() 要真的按路由拒答，而不只是 router 自己会拒。"""
        import reasoning_retrieval as rr
        result = rr.ask("上海自贸区临港新片区的人才落户条件",
                        DB, chat_fn=lambda *a, **k: "不应被调用")
        return (result["answer"] is None
                and result["refusal_stage"] == "route"), \
            f"阶段 {result['refusal_stage']}：{(result['refusal'] or '')[:40]}"
    results.append(probe("ask() 按路由拒答（不调模型）", refusal_stage_wired))

    passed = sum(1 for ok in results if ok is True)
    skipped = sum(1 for ok in results if ok is None)
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")


if __name__ == "__main__":
    main()
