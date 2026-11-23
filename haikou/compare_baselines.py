"""Compare force-aware QA with direct-LLM and plain-RAG baselines.

Both baselines use the same configured model. The plain-RAG baseline receives
local excerpts but no force labels, version ranking, temporal index or
article-level invalidation metadata. The existing v4 result is the treatment.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eval_qa
import llm_provider
import reasoning_retrieval as rr
import retrieval
from paths import DEFAULT_DATABASE


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "test_runs" / (
    datetime.now().astimezone().strftime("%Y-%m-%d") + "_comparison-01"
)
TODAY = datetime.now().astimezone().date().isoformat()
JSON_SCHEMA = """{
  "answer": "完整回答；无法回答时写 null",
  "citations": [
    {"doc": "文件名或法规名", "article": "第X条或第N段"}
  ],
  "reason": "选材或拒答理由"
}"""


def parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def direct_prompt(question: str) -> str:
    return "\n".join([
        f"今天是 {TODAY}。请独立回答以下海南政策问题。",
        "不得声称使用了未提供的本地语料；不确定时可以不回答。",
        "如果引用文件或条文，请只填写你确信的名称，不确定就留空。",
        "",
        f"问题：{question}",
        "",
        "只输出 JSON，不要代码围栏：",
        JSON_SCHEMA,
    ])


def plain_context(question: str, index, database: str,
                 docs_wanted: int = 4) -> list[dict]:
    """Lexical retrieval with no force, version or temporal layer."""
    hits = index.search(question, top_k=80)
    for path in retrieval.docs_named_in(question, database):
        rows = retrieval.parse_articles(path, {})
        if not rows:
            rows = retrieval.block_units(path, {})
        hits = [dict(row, score=999.0, coverage=1.0) for row in rows[:8]] + hits

    by_doc: dict[str, list[dict]] = {}
    order = []
    for hit in hits:
        doc = hit["doc"]
        key = hit.get("file") or rr.doc_key(doc)
        if key not in by_doc:
            by_doc[key] = []
            order.append(key)
        if hit["article"] not in {row["article"] for row in by_doc[key]}:
            by_doc[key].append(hit)

    evidence = []
    for key in order[:docs_wanted]:
        for hit in by_doc[key][:6]:
            evidence.append({
                "doc": hit["doc"],
                "article": hit["article"],
                "text": hit.get("text", "")[:700],
            })
    return evidence


def plain_prompt(question: str, evidence: list[dict]) -> str:
    if evidence:
        material = "\n\n".join(
            f"[材料 {index}]\n文件名：{row['doc']}\n位置：{row['article']}\n"
            f"正文：{row['text']}"
            for index, row in enumerate(evidence, 1)
        )
    else:
        material = "（本地检索没有返回材料）"
    return "\n".join([
        f"今天是 {TODAY}。你是基于给定材料作答的政策助手。",
        "只能依据下面材料作答；材料不足时回答 null 并说明。",
        "引用只能填写材料中出现的文件名和位置。",
        "",
        f"问题：{question}",
        "",
        "材料：",
        material,
        "",
        "只输出 JSON，不要代码围栏：",
        JSON_SCHEMA,
    ])


def citation_sources(citations: list, index, layer) -> list[dict]:
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for row in index.articles:
        by_doc[rr.doc_key(row["doc"])].append(row)

    out = []
    seen = set()
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        doc = str(citation.get("doc") or "").strip()
        article = str(citation.get("article") or "").strip()
        key = rr.doc_key(doc)
        candidates = by_doc.get(key, [])
        if not candidates:
            candidates = [
                row for other_key, rows in by_doc.items()
                if key and (key in other_key or other_key in key)
                for row in rows
            ]
        match = next((row for row in candidates if row["article"] == article), None)
        if not match or (match["doc"], article) in seen:
            continue
        seen.add((match["doc"], article))
        out.append({
            "doc": match["doc"],
            "article": article,
            "status": match.get("status")
            or match.get("status_detail", {}).get("status", "未判定"),
        })
    return out


def parse_answer(raw: str, index, layer) -> dict:
    parsed = parse_json_object(raw)
    if parsed is None:
        return {
            "answer": raw.strip() or None,
            "refusal": None if raw.strip() else "模型没有返回内容",
            "refusal_stage": "parse_error",
            "sources": [],
            "reason": "模型输出不是 JSON",
        }
    answer = parsed.get("answer")
    citations = parsed.get("citations") or []
    sources = citation_sources(citations, index, layer)
    return {
        "answer": answer,
        "refusal": None if answer else (parsed.get("reason") or "模型拒答"),
        "refusal_stage": None if answer else "model",
        "sources": sources,
        "reason": parsed.get("reason") or "",
        "citations": citations,
    }


def run_one(item: dict, arm: str, database: str, index, layer) -> dict:
    provider = llm_provider.get_provider()
    if arm == "direct":
        prompt = direct_prompt(item["question"])
    else:
        evidence = plain_context(item["question"], index, database)
        prompt = plain_prompt(item["question"], evidence)
    try:
        raw = provider.complete(
            [
                {"role": "system",
                 "content": f"今天是 {TODAY}。严格按用户要求输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        result = parse_answer(raw, index, layer)
        error = None
    except Exception as exc:  # noqa: BLE001
        raw = ""
        result = {
            "answer": None,
            "refusal": f"{type(exc).__name__}: {exc}",
            "refusal_stage": "llm_error",
            "sources": [],
        }
        error = type(exc).__name__
    return {
        "id": item["id"],
        "arm": arm,
        "result": result,
        "raw_response": raw,
        "error": error,
    }


def metrics(item: dict, result: dict) -> dict:
    clean_item = {**item}
    clean_item.pop("expected_route", None)
    verdict = eval_qa.score_one(clean_item, result)
    verdict.update({
        "id": item["id"],
        "type": item["type"],
        "question": item["question"],
        "refusal_stage": result.get("refusal_stage"),
        "source_count": len(result.get("sources") or []),
    })
    return verdict


def summarize(arm: str, rows: list[dict]) -> dict:
    by_type = {}
    for kind in sorted({row["type"] for row in rows}):
        group = [row for row in rows if row["type"] == kind]
        by_type[kind] = {
            "correct": sum(row["correct"] for row in group),
            "total": len(group),
        }
    evidence_rows = [
        row for row in rows
        if row["type"] in ("in_force", "questionable", "temporal")
    ]
    content_correct = sum(
        (not row["refused"]) and not row["missed_points"]
        for row in evidence_rows
    )
    return {
        "arm": arm,
        "correct": sum(row["correct"] for row in rows),
        "total": len(rows),
        "accuracy": round(
            sum(row["correct"] for row in rows) / max(len(rows), 1), 4
        ),
        "missing_evidence": sum(bool(row["missing_evidence"]) for row in rows),
        "unlabeled_repealed": sum(bool(row["unlabeled_repealed"]) for row in rows),
        "refused": sum(bool(row["refused"]) for row in rows),
        "llm_errors": sum(row["refusal_stage"] == "llm_error" for row in rows),
        "evidence_questions": len(evidence_rows),
        "content_correct": content_correct,
        "content_accuracy": round(
            content_correct / max(len(evidence_rows), 1), 4
        ),
        "by_type": by_type,
    }


def treatment_rows(spec: dict, path: Path) -> list[dict]:
    bundle = eval_qa.load_results(str(path))
    rows = []
    for item in spec["questions"]:
        entry = bundle["results"].get(item["id"])
        if entry:
            rows.append(metrics(item, entry["result"]))
    return rows


def render_report(meta: dict, summaries: list[dict],
                  baseline_rows: dict[str, list[dict]]) -> str:
    lines = [
        "# 效力层对比实验",
        "",
        f"- 生成时间：{meta['generated_at']}",
        f"- 模型：{meta['provider']} / {meta['model']}",
        f"- 题目：{meta['questions']}",
        "- treatment：当前效力感知系统",
        "- plain_rag：相同本地词法检索，但完全移除效力、版本和时点信息",
        "- direct_llm：不使用本地语料，也不使用联网搜索",
        "",
        "| 方案 | 完整通过 | 内容要点 | 证据缺失 | 引用失效未标注 | 拒答 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    type_order = ("in_force", "questionable", "temporal", "trap", "off_corpus")
    for summary in summaries:
        lines.append(
            f"| {summary['arm']} | {summary['correct']}/{summary['total']} "
            f"({summary['accuracy']:.0%}) | "
            f"{summary['content_correct']}/{summary['evidence_questions']} "
            f"({summary['content_accuracy']:.0%}) | "
            f"{summary['missing_evidence']} | "
            f"{summary['unlabeled_repealed']} | {summary['refused']} |"
        )
    lines += [
        "",
        "“完整通过”同时要求答案要点、必备证据、效力与拒答行为正确；"
        "“内容要点”只看 57 道证据型题的答案内容，不看引用和效力提示。",
        "",
        "## 分题型正确率", "",
              "| 方案 | " + " | ".join(type_order) + " |",
              "|---|" + "---:|" * len(type_order),
    ]
    for summary in summaries:
        cells = [
            f"{summary['by_type'].get(kind, {'correct': 0, 'total': 0})['correct']}"
            f"/{summary['by_type'].get(kind, {'correct': 0, 'total': 0})['total']}"
            for kind in type_order
        ]
        lines.append(f"| {summary['arm']} | " + " | ".join(cells) + " |")
    failures = []
    for arm in ("plain_rag", "direct_llm"):
        for row in baseline_rows.get(arm, []):
            if not row["correct"]:
                failures.append(
                    f"| {arm} | {row['id']} | {row['type']} | "
                    f"{'缺证据' if row['missing_evidence'] else ''}"
                    f"{' 引用失效未标注' if row['unlabeled_repealed'] else ''}"
                    f"{' 拒答' if row['refused'] else ''} |"
                )
    if failures:
        lines += ["", "## 基线失败题", "",
                  "| 方案 | 题号 | 类型 | 原因 |", "|---|---|---|---|"]
        lines += failures[:80]
    lines += [
        "",
        "## 边界",
        "",
        "- 本实验使用现有 84 题，仍属于内部测试集，不是独立用户集。",
        "- 自动评分按现有 must_cite、key_points 和引用效力规则执行。",
        "- direct_llm 没有联网搜索；因此它代表“模型自身知识”，不代表带浏览器的通用助手。",
        "- plain_rag 使用与 treatment 相同的语料库，但检索、目录和回答过程不含效力信息。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--set", default=eval_qa.SET_PATH)
    parser.add_argument("--treatment", default=eval_qa.RESULTS_PATH)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reuse", action="store_true",
                        help="recompute report from baseline_results.json")
    parser.add_argument("--only", default="",
                        help="comma-separated question ids for a smoke run")
    args = parser.parse_args()

    spec = eval_qa.load_set(args.set)
    selected = [item.strip() for item in args.only.split(",") if item.strip()]
    questions = [
        item for item in spec["questions"]
        if not selected or item["id"] in selected
    ]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    provider = llm_provider.get_provider()
    config = provider.config.public()
    expected = eval_qa.result_meta(spec, args.database)
    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": config["provider"],
        "model": config["model"],
        "questions": len(questions),
        "dataset_sha256": expected["dataset_sha256"],
        "corpus_fingerprint": expected["corpus_fingerprint"],
        "code_sha256": expected["code_sha256"],
    }
    index = retrieval.get_index(args.database)
    from force_layer import ForceLayer
    layer = ForceLayer(args.database)

    result_path = output / "baseline_results.json"
    results = {arm: {} for arm in ("direct_llm", "plain_rag")}
    if args.reuse and result_path.exists():
        saved = json.loads(result_path.read_text(encoding="utf-8"))
        results = saved["results"]
        meta = saved.get("meta", meta)
    else:
        lock = threading.Lock()
        tasks = [
            (item, arm)
            for arm in ("direct_llm", "plain_rag")
            for item in questions
        ]
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(
                    run_one, item,
                    "direct" if arm == "direct_llm" else "plain_rag",
                    args.database, index, layer,
                ): (item["id"], arm)
                for item, arm in tasks
            }
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                test_id, arm = futures[future]
                results[arm][test_id] = future.result()
                completed += 1
                print(f"[{completed}/{len(tasks)}] {arm} {test_id}", flush=True)
                with lock:
                    result_path.write_text(
                        json.dumps({"meta": meta, "results": results},
                                   ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8",
                    )

    baseline_rows = {
        arm: [
            metrics(item, results[arm][item["id"]]["result"])
            for item in questions
        ]
        for arm in results
    }
    rows_by_arm = {
        "treatment": treatment_rows(
            {"questions": questions}, Path(args.treatment)
        ),
        **baseline_rows,
    }
    summaries = [summarize(arm, rows) for arm, rows in rows_by_arm.items()]
    report = render_report(meta, summaries, baseline_rows)
    result_path.write_text(
        json.dumps({"meta": meta, "results": results,
                    "metrics": rows_by_arm, "summary": summaries},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    (output / "summary.md").write_text(
        report + "\n", encoding="utf-8", newline="\n"
    )
    print(report)
    print(f"\nSaved comparison to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
