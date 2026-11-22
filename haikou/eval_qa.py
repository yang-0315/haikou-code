"""validity-QA 评测：答案既要对，又必须引用现行有效条文。

分类指标（三态判定对没对）和问答指标是两回事。这个集子考的是端到端问答：

  1. 答案对不对——必须引到能支撑答案的条文（`must_cite`），且答出要点
     （`key_points`）。真值是人工读条文写进去的，不是模型自评。
  2. 引用是不是现行有效——引了已废止文件却没有就近标注失效，就是违规。
     这是本项目的立身之本：答错可以忍，拿失效条文当现行依据不行。
  3. 该拒的拒没拒——语料外的问题必须拒答，语料内的问题不许乱拒。

    python eval_qa.py --run          # 联网跑一遍（结果缓存，可反复评分）
    python eval_qa.py                # 只按缓存评分，不联网
    python eval_qa.py --show eq-01   # 看某题的回答与逐项判定
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reasoning_retrieval as rr

HERE = os.path.dirname(os.path.abspath(__file__))
from paths import DEFAULT_DATABASE as DEFAULT_DB
SET_PATH = os.path.join(HERE, "eval_set_v3_additions.json")
RESULTS_PATH = os.path.join(HERE, "eval_results_v3.json")

OUT_OF_FORCE = ("已废止", "系列已废止")
FAIL_WORDS = ("已废止", "系列已废止", "失效", "废止", "不再有效", "不能作为现行依据",
              "不得作为现行依据")


def load_set(path: str = SET_PATH) -> dict:
    """Load a set, recursively merging an optional ``extends`` parent."""
    path = os.path.abspath(path)
    spec = json.load(open(path, encoding="utf-8"))
    parent_name = spec.get("extends")
    if not parent_name:
        return spec
    parent_path = os.path.join(os.path.dirname(path), parent_name)
    parent = load_set(parent_path)
    merged = dict(spec)
    merged["questions"] = list(parent.get("questions", [])) + \
        list(spec.get("questions", []))
    merged["sources"] = [os.path.basename(parent_path), os.path.basename(path)]
    merged["parent_version"] = parent.get("version")
    return merged


def load_results(path: str = RESULTS_PATH) -> dict:
    if not os.path.exists(path):
        return {"version": 3, "dataset": os.path.basename(SET_PATH),
                "results": {}}
    data = json.load(open(path, encoding="utf-8"))
    # Accept the old flat cache format so earlier runs remain inspectable.
    if "results" in data and isinstance(data["results"], dict):
        return data
    return {"version": 3, "dataset": os.path.basename(SET_PATH),
            "results": data}


def save_results(results: dict, path: str = RESULTS_PATH) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=1)


def run(database: str, only: list[str] | None = None,
        set_path: str = SET_PATH, results_path: str = RESULTS_PATH) -> dict:
    spec = load_set(set_path)
    bundle = load_results(results_path)
    bundle["dataset"] = os.path.abspath(set_path)
    answers = bundle["results"]
    for item in spec["questions"]:
        if only and item["id"] not in only:
            continue
        print(f"  跑 {item['id']} …", file=sys.stderr)
        answers[item["id"]] = {"result": rr.ask(
            item["question"], database, 4)}
        save_results(bundle, results_path)
    return bundle


def near_label(answer: str, name: str, span: int = 220) -> bool:
    """答案里提到这份文件时，附近有没有说它失效。

    答案常常用"该办法""该文件"指代，正文里根本不出现全名——这时退一步要求
    整篇答案里出现过失效字样。宁可宽一点：这里的目的是抓"把失效条文当现行
    依据用"，不是做严格的指代消解。
    """
    answer = answer or ""
    found_name = False
    needles = [name, name[:16], name[:12]]
    for needle in needles:
        start = 0
        while True:
            position = answer.find(needle, start)
            if position == -1:
                break
            found_name = True
            window = answer[max(0, position - span):position + span + len(needle)]
            if any(word in window for word in FAIL_WORDS):
                return True
            start = position + len(needle)
    if found_name:
        return False
    return any(word in answer for word in FAIL_WORDS)


def doc_matches(cited: str, wanted: str) -> bool:
    """引用里的文档名是否就是 ground truth 指的那份。

    库内文件名常把办法名包在通知里（"…关于印发《海南省游艇管理办法》的通知"），
    而 ground truth 写的是办法名；两边归一后做包含判断，避免把**答对的**判成
    缺证据。
    """
    a, b = rr.doc_key(cited or ""), rr.doc_key(wanted or "")
    if not a or not b:
        return False
    return a == b or a in b or b in a


def key_points_hit(answer: str, groups: list[list[str]]) -> tuple[bool, list[str]]:
    answer = answer or ""
    missed = [group[0] for group in groups
              if not any(word in answer for word in group)]
    return not missed, missed


def score_one(item: dict, result: dict) -> dict:
    answer = result.get("answer") or ""
    refused = result.get("answer") is None
    sources = result.get("sources") or []
    repealed = [s for s in sources if s.get("status") in OUT_OF_FORCE]
    unlabeled = [s for s in repealed if not near_label(answer, s["doc"])]
    # 文件名要归一后再比："零关税" 的书名号、"（试行）"后缀在不同副本里写法不一
    cited = {(s["doc"], s["article"]) for s in sources}
    missing_evidence = [
        pair for pair in item["must_cite"]
        if not any(article == pair.split("|")[1]
                   and doc_matches(doc, pair.split("|")[0])
                   for doc, article in cited)]
    points_ok, missed_points = key_points_hit(answer, item["key_points"])
    in_force_only = all(s.get("status") == "现行有效" for s in sources)

    kind = item["type"]
    if kind == "in_force":
        correct = (not refused) and not missing_evidence and points_ok
    elif kind == "off_corpus":
        correct = refused
    elif kind == "trap":
        # 拒答也行，引了但标明失效也行；唯独不能当现行依据用
        flagged = any(word in answer for word in FAIL_WORDS)
        correct = (not unlabeled) and (refused or flagged or in_force_only)
    else:  # questionable
        correct = (not refused) and not missing_evidence and points_ok \
            and ("核实" in answer or "存疑" in answer)
    return {
        "refused": refused,
        "missing_evidence": missing_evidence,
        "missed_points": missed_points,
        "repealed_citations": [s["doc"] for s in repealed],
        "unlabeled_repealed": [s["doc"] for s in unlabeled],
        "all_in_force": in_force_only,
        "correct": bool(correct),
    }


def selftest() -> None:
    """用构造出来的答案验证评分器：每种判定路径都要能分对。"""
    doc = "海南自由贸易港商事调解组织登记管理办法"
    in_force = {"type": "in_force", "must_cite": [f"{doc}|第七条"],
                "key_points": [["申请书"], ["验资报告"]], "notes": ""}
    good = {"answer": "结论：需提交申请书、章程草案与验资报告。",
            "sources": [{"doc": doc, "article": "第七条", "status": "现行有效"}]}
    cases = [
        ("答对且引用现行有效", in_force, good, True),
        ("漏引必备条文", in_force,
         {"answer": "需提交申请书与验资报告。",
          "sources": [{"doc": doc, "article": "第五条", "status": "现行有效"}]},
         False),
        ("答漏要点", in_force,
         {"answer": "需提交申请书与章程草案。",
          "sources": [{"doc": doc, "article": "第七条", "status": "现行有效"}]},
         False),
        ("语料内问题却拒答", in_force,
         {"answer": None, "refusal": "答不了", "sources": []}, False),
        ("引了已废止条文且未标注",
         {"type": "trap", "must_cite": [], "key_points": [], "notes": ""},
         {"answer": "根据该办法，12 层以下住宅应当配建。",
          "sources": [{"doc": "海南省太阳能热水系统建筑应用管理办法",
                      "article": "第三条", "status": "已废止"}]},
         False),
        ("引了已废止条文但明确标注失效",
         {"type": "trap", "must_cite": [], "key_points": [], "notes": ""},
         {"answer": "该办法已废止，不能作为现行依据；原规定 12 层以下住宅"
                    "应当配建太阳能热水系统，仅供参考。",
          "sources": [{"doc": "海南省太阳能热水系统建筑应用管理办法",
                      "article": "第三条", "status": "已废止"}]},
         True),
        ("语料外问题拒答",
         {"type": "off_corpus", "must_cite": [], "key_points": [], "notes": ""},
         {"answer": None, "refusal": "语料外", "sources": []}, True),
        ("语料外问题硬答",
         {"type": "off_corpus", "must_cite": [], "key_points": [], "notes": ""},
         {"answer": "可以这样设计……", "sources": []}, False),
        ("存疑条文未提示核实",
         {"type": "questionable", "must_cite": ["海南省行政执法和行政执法监督规定|第六条"],
          "key_points": [["违法必究"]], "notes": ""},
         {"answer": "应当坚持违法必究的原则。",
          "sources": [{"doc": "海南省行政执法和行政执法监督规定",
                      "article": "第六条", "status": "效力存疑"}]},
         False),
        ("存疑条文已提示核实",
         {"type": "questionable", "must_cite": ["海南省行政执法和行政执法监督规定|第六条"],
          "key_points": [["违法必究"]], "notes": ""},
         {"answer": "该文件效力存疑，需人工核实；其第六条规定违法必究。",
          "sources": [{"doc": "海南省行政执法和行政执法监督规定",
                      "article": "第六条", "status": "效力存疑"}]},
         True),
    ]
    failed = 0
    for title, spec, result, expected in cases:
        verdict = score_one({**spec, "id": "t", "question": "t"}, result)
        ok = verdict["correct"] is expected
        failed += 0 if ok else 1
        print(f"{'通过' if ok else '问题'}  {title}"
              f"（判定 {verdict['correct']}，应有 {expected}）")
    print(f"\n评分器自检 {len(cases) - failed}/{len(cases)}")


def validate_data(spec: dict, database: str) -> int:
    """Validate references, current force labels and case construction."""
    from force_layer import ForceLayer
    import retrieval
    from warn import IN_FORCE, QUESTIONABLE, REPEALED, SERIES_REPEALED

    questions = spec.get("questions", [])
    problems = []
    ids = [item.get("id") for item in questions]
    texts = [item.get("question") for item in questions]
    if len(ids) != len(set(ids)):
        problems.append("题号不唯一")
    if len(texts) != len(set(texts)):
        problems.append("题面不唯一")

    layer = ForceLayer(database)
    doc_cache: dict[str, tuple[set[str], set[str]]] = {}
    expected_status = {"in_force": IN_FORCE, "questionable": QUESTIONABLE}
    for item in questions:
        kind = item.get("type")
        if kind not in ("in_force", "trap", "questionable", "off_corpus"):
            problems.append(f"{item.get('id')}：未知类型 {kind}")
        if kind in ("trap", "off_corpus") and \
                (item.get("must_cite") or item.get("key_points")):
            problems.append(f"{item.get('id')}：{kind} 题不应设置答案真值")
        for citation in item.get("must_cite", []):
            if "|" not in citation:
                problems.append(f"{item.get('id')}：引用格式错误 {citation}")
                continue
            doc, article = citation.split("|", 1)
            if doc not in doc_cache:
                paths = retrieval.docs_named_in(doc, database)
                articles = set()
                for path in paths:
                    articles |= {row["article"]
                                 for row in retrieval.parse_articles(path, {})}
                statuses = {layer.status_for_file(path)["status"]
                            for path in paths}
                doc_cache[doc] = (articles, statuses)
            articles, statuses = doc_cache[doc]
            if not articles:
                problems.append(f"{item.get('id')}：找不到文档《{doc}》的条文")
            elif article not in articles:
                problems.append(f"{item.get('id')}：《{doc}》没有 {article}")
            if kind in expected_status and \
                    expected_status[kind] not in statuses:
                problems.append(
                    f"{item.get('id')}：《{doc}》匹配到的状态为 "
                    f"{sorted(statuses)}，但题类要求 {expected_status[kind]}")

    # Each trap must name a document that is currently out of force.  Recall
    # can legitimately return the repeal notice instead of the target, so this
    # check is against the corpus status map rather than recall order.
    trees_path = os.path.join(database, "_force_trees.json")
    out_of_force = []
    if os.path.exists(trees_path):
        trees = json.load(open(trees_path, encoding="utf-8"))
        out_of_force = [
            row["title"] for row in trees.get("docs", {}).values()
            if row.get("status", {}).get("status") in OUT_OF_FORCE
        ]
    for item in (row for row in questions if row.get("type") == "trap"):
        question_key = rr.doc_key(item["question"])
        if out_of_force and not any(
                rr.doc_key(title) in question_key for title in out_of_force):
            problems.append(f"{item['id']}：题面没有点名任何已知失效文档")

    by_type: dict[str, int] = {}
    for item in questions:
        by_type[item.get("type", "?")] = by_type.get(item.get("type", "?"), 0) + 1
    detail = "、".join(f"{kind} {count}" for kind, count in sorted(by_type.items()))
    print(f"数据 {len(questions)} 题：{detail}")
    if problems:
        for problem in problems:
            print(f"问题  {problem}")
        print(f"\n数据校验 {len(questions) - len(problems)}/{len(questions)}")
        return 1
    print("\n数据校验通过：题号/题面唯一，条文与当前效力标注一致，陷阱均点名失效文档")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--run", action="store_true", help="联网跑一遍")
    parser.add_argument("--only", default="", help="只跑指定的题号，逗号分隔")
    parser.add_argument("--show", default="", help="打印某题的完整回答")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true",
                        help="用构造答案验证评分器本身（不联网）")
    parser.add_argument("--set", default=SET_PATH,
                        help="评测数据 JSON；支持 extends 合并父集")
    parser.add_argument("--results", default=RESULTS_PATH,
                        help="模型回答缓存 JSON")
    parser.add_argument("--validate-data", action="store_true",
                        help="不调模型，只校验测试数据和条文引用")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    spec = load_set(args.set)
    if args.validate_data:
        raise SystemExit(validate_data(spec, args.database))
    only = [x for x in args.only.split(",") if x] or None
    bundle = run(args.database, only, args.set, args.results) \
        if args.run else load_results(args.results)
    results = bundle.get("results", {})
    if not results:
        print(f"还没有结果：先跑 python eval_qa.py --set \"{args.set}\" "
              f"--results \"{args.results}\" --run")
        return

    rows = []
    for item in spec["questions"]:
        entry = results.get(item["id"])
        if not entry:
            continue
        rows.append({**score_one(item, entry["result"]),
                     "id": item["id"], "type": item["type"],
                     "question": item["question"]})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    print(f"{'题号':8} {'类型':12} {'结果':4} 说明")
    for row in rows:
        notes = []
        if row["missing_evidence"]:
            notes.append(f"缺证据 {row['missing_evidence']}")
        if row["missed_points"]:
            notes.append(f"缺要点 {row['missed_points']}")
        if row["unlabeled_repealed"]:
            notes.append(f"引失效条文未标注 {row['unlabeled_repealed']}")
        elif row["repealed_citations"]:
            notes.append(f"引了已废止条文但已标注 {row['repealed_citations']}")
        if row["refused"]:
            notes.append("拒答")
        print(f"{row['id']:8} {row['type']:12} "
              f"{'✓' if row['correct'] else '✗':4} {'；'.join(notes) or '—'}")

    total = len(rows)
    passed = sum(1 for row in rows if row["correct"])
    expected = len(spec.get("questions", []))
    print(f"\n总正确率 {passed}/{total}"
          f"（{passed / total * 100:.0f}%）")
    if total != expected:
        print(f"结果覆盖 {total}/{expected}；未跑题数 {expected - total}")

    by_type: dict[str, list[dict]] = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row)
    for kind, group in by_type.items():
        ok = sum(1 for row in group if row["correct"])
        print(f"  {kind:12} {ok}/{len(group)}")

    # 两条硬指标：答案对（引到必备证据且要点齐全）与引用合规（不把失效条文
    # 当现行依据），分开算，避免一条拖另一条。
    evidence_rows = [r for r in rows
                     if r["type"] in ("in_force", "questionable")]
    evidence_ok = sum(1 for r in evidence_rows
                      if not r["missing_evidence"] and not r["missed_points"])
    citations_ok = sum(1 for r in rows if not r["unlabeled_repealed"])
    print(f"\n答案对（必备证据＋要点齐） {evidence_ok}/{len(evidence_rows)}")
    print(f"引用合规（无未标注的失效引用） {citations_ok}/{total}")
    in_force_rows = [r for r in rows if r["type"] == "in_force"]
    pure = sum(1 for r in in_force_rows if r["all_in_force"])
    print(f"现行有效题只引现行有效条文 {pure}/{len(in_force_rows)}")

    if args.show:
        for item in spec["questions"]:
            if item["id"] != args.show:
                continue
            entry = results.get(item["id"])
            if not entry:
                print(f"{args.show} 还没有结果")
                continue
            result = entry["result"]
            print("\n" + "=" * 96)
            print(f"【{item['id']}】{item['question']}")
            print(f"类型 {item['type']}　效力判定：{item['notes']}")
            print(f"拒答阶段：{result.get('refusal_stage')}")
            print(f"引用：{json.dumps(result.get('sources'), ensure_ascii=False)}")
            print("-" * 96)
            print(result.get("answer") or result.get("refusal"))


if __name__ == "__main__":
    main()
