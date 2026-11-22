"""Build a reproducible two-reviewer blind-review package.

Reviewer sheets intentionally omit:

  * the original item id and category;
  * the project's ground-truth ``must_cite`` / ``key_points``;
  * the automatic score and failure reasons.

They do include the question, candidate answer, candidate citations, force
labels and source excerpts, so reviewers can independently judge factual
correctness, citation support and force handling.

    python build_blind_review.py
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import eval_qa
import retrieval

from paths import DEFAULT_DATABASE as DEFAULT_DB
OUT_DIR = os.path.join(HERE, "blind_review_v3")
SEED = 20261121

RATING_COLUMNS = [
    "question_quality",
    "expected_behavior",
    "answer_verdict",
    "citation_verdict",
    "force_verdict",
    "confidence",
    "reviewer_notes",
]

CHOICES = {
    "question_quality": ["clear", "ambiguous", "leading", "duplicate", "invalid"],
    "expected_behavior": ["answer_with_current_law", "explain_repealed_or_refuse",
                          "refuse", "answer_with_uncertainty"],
    "answer_verdict": ["correct", "partially_correct", "incorrect", "uncertain"],
    "citation_verdict": ["supported", "partially_supported", "unsupported",
                         "not_applicable", "uncertain"],
    "force_verdict": ["appropriate", "inappropriate", "not_applicable", "uncertain"],
    "confidence": ["high", "medium", "low"],
}


def _article_excerpt(doc: str, article: str, database: str,
                     cache: dict[tuple[str, str], str]) -> str:
    key = (doc, article)
    if key in cache:
        return cache[key]
    for path in retrieval.docs_named_in(doc, database):
        for row in retrieval.parse_articles(path, {}):
            if row.get("article") == article:
                body = row.get("text", "")
                cache[key] = f"《{doc}》{article}\n{body[:1000]}"
                return cache[key]
    cache[key] = ""
    return ""


def _evidence_excerpts(item: dict, result: dict, database: str,
                       article_cache: dict[tuple[str, str], str]) -> str:
    excerpts = []
    for citation in item.get("must_cite", []):
        if "|" not in citation:
            continue
        doc, article = citation.split("|", 1)
        text = _article_excerpt(doc, article, database, article_cache)
        if text:
            excerpts.append(text)

    # For traps, expose the opening articles of the named legacy document.
    # Off-corpus cases intentionally have no source excerpt.
    if item.get("type") == "trap":
        for path in retrieval.docs_named_in(item["question"], database)[:2]:
            parsed = retrieval.parse_articles(path, {})
            doc = os.path.splitext(os.path.basename(path))[0]
            for row in parsed[:2]:
                text = row.get("text", "")
                if text:
                    excerpts.append(f"《{doc}》{row['article']}\n{text[:800]}")

    # Useful for documents without a single must-cite article (plans and
    # notices): show the excerpts the system actually read.
    for source in result.get("sources") or []:
        text = _article_excerpt(source.get("doc", ""), source.get("article", ""),
                                database, article_cache)
        if text:
            excerpts.append(text)
    return "\n\n".join(dict.fromkeys(excerpts))[:5000]


def _write_reviewer(path: str, rows: list[dict]) -> None:
    fields = [
        "case_id", "reviewer_id", "question", "model_answer",
        "cited_sources", "source_excerpt", *RATING_COLUMNS,
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build(database: str = DEFAULT_DB) -> dict:
    spec = eval_qa.load_set()
    bundle = eval_qa.load_results()
    answers = bundle.get("results", {})
    missing = [item["id"] for item in spec["questions"]
               if item["id"] not in answers]
    if missing:
        raise RuntimeError(f"模型结果缺少 {len(missing)} 题：{missing[:8]}")

    ordered = list(spec["questions"])
    random.Random(SEED).shuffle(ordered)
    article_cache: dict[tuple[str, str], str] = {}
    rows, key = [], {}
    for number, item in enumerate(ordered, 1):
        case_id = f"BR-{number:03d}"
        result = answers[item["id"]]["result"]
        auto = eval_qa.score_one(item, result)
        rows.append({
            "case_id": case_id,
            "reviewer_id": "",
            "question": item["question"],
            "model_answer": result.get("answer") or result.get("refusal") or "",
            "cited_sources": "\n".join(
                f"《{source.get('doc')}》{source.get('article')} "
                f"[{source.get('status')}]"
                for source in result.get("sources") or []),
            "source_excerpt": _evidence_excerpts(
                item, result, database, article_cache),
            **{column: "" for column in RATING_COLUMNS},
        })
        key[case_id] = {
            "original_id": item["id"],
            "type": item["type"],
            "question": item["question"],
            "notes": item.get("notes", ""),
            "must_cite": item.get("must_cite", []),
            "key_points": item.get("key_points", []),
            "auto_correct": auto["correct"],
            "auto_verdict": auto,
        }

    os.makedirs(OUT_DIR, exist_ok=True)
    _write_reviewer(os.path.join(OUT_DIR, "reviewer_A.csv"),
                    [dict(row, reviewer_id="A") for row in rows])
    _write_reviewer(os.path.join(OUT_DIR, "reviewer_B.csv"),
                    [dict(row, reviewer_id="B") for row in rows])
    with open(os.path.join(OUT_DIR, "coordinator_key.json"), "w",
              encoding="utf-8") as handle:
        json.dump({
            "version": 3,
            "seed": SEED,
            "cases": key,
            "rating_choices": CHOICES,
        }, handle, ensure_ascii=False, indent=2)
    manifest = {
        "version": 3,
        "seed": SEED,
        "cases": len(rows),
        "reviewers": ["reviewer_A.csv", "reviewer_B.csv"],
        "coordinator_only": ["coordinator_key.json"],
        "status": "waiting_for_reviewers",
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(f"盲评包已生成：{OUT_DIR}")
    print(f"随机顺序 {len(rows)} 题；两位评审文件已就绪；协调者密钥未写入评审表")
    return manifest


if __name__ == "__main__":
    build()
