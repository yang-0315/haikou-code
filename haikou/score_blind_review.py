"""Score two independent human blind reviews against the automatic results."""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "blind_review_v3")

RATING_FIELDS = [
    "question_quality", "expected_behavior", "answer_verdict",
    "citation_verdict", "force_verdict", "confidence",
]
CHOICES = {
    "question_quality": {"clear", "ambiguous", "leading", "duplicate", "invalid"},
    "expected_behavior": {"answer_with_current_law", "explain_repealed_or_refuse",
                          "refuse", "answer_with_uncertainty"},
    "answer_verdict": {"correct", "partially_correct", "incorrect", "uncertain"},
    "citation_verdict": {"supported", "partially_supported", "unsupported",
                         "not_applicable", "uncertain"},
    "force_verdict": {"appropriate", "inappropriate", "not_applicable", "uncertain"},
    "confidence": {"high", "medium", "low"},
}


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if not a:
        return None
    labels = set(a) | set(b)
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    expected = sum((a.count(label) / len(a)) * (b.count(label) / len(b))
                   for label in labels)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def _expected_behaviors(kind: str) -> set[str]:
    return {
        "in_force": {"answer_with_current_law"},
        "trap": {"explain_repealed_or_refuse", "refuse"},
        "off_corpus": {"refuse"},
        "questionable": {"answer_with_uncertainty"},
    }.get(kind, set())


def score(review_dir: str) -> tuple[int, dict]:
    paths = {
        "A": os.path.join(review_dir, "reviewer_A.csv"),
        "B": os.path.join(review_dir, "reviewer_B.csv"),
    }
    key_path = os.path.join(review_dir, "coordinator_key.json")
    for path in (*paths.values(), key_path):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
    reviews = {name: {row["case_id"]: row for row in load_csv(path)}
               for name, path in paths.items()}
    key = json.load(open(key_path, encoding="utf-8"))
    cases = key["cases"]
    case_ids = sorted(cases)
    if set(reviews["A"]) != set(case_ids) or set(reviews["B"]) != set(case_ids):
        raise ValueError("评审表题号与协调者密钥不一致")

    invalid, completion = [], {}
    for name, rows in reviews.items():
        complete = 0
        for case_id in case_ids:
            row = rows[case_id]
            filled = all(row.get(field, "").strip() for field in RATING_FIELDS)
            complete += int(filled)
            for field in RATING_FIELDS:
                value = row.get(field, "").strip()
                if value and value not in CHOICES[field]:
                    invalid.append(f"{name}/{case_id}/{field}={value!r}")
        completion[name] = complete
    reviewer_ids = {
        row.get("reviewer_id", "").strip()
        for rows in reviews.values() for row in rows.values()
        if row.get("reviewer_id", "").strip()
    }
    owner_attested = any("owner-attested" in value for value in reviewer_ids)

    complete_ids = [
        case_id for case_id in case_ids
        if all(reviews[name][case_id].get(field, "").strip()
               for name in reviews for field in RATING_FIELDS)
    ]
    agreements = {}
    kappas = {}
    for field in RATING_FIELDS:
        left = [reviews["A"][case_id][field] for case_id in complete_ids]
        right = [reviews["B"][case_id][field] for case_id in complete_ids]
        agreements[field] = (sum(x == y for x, y in zip(left, right))
                             / len(left) * 100 if left else None)
        kappas[field] = cohen_kappa(left, right)

    disagreements, resolved = [], []
    for case_id in complete_ids:
        a, b = reviews["A"][case_id], reviews["B"][case_id]
        verdicts = {a["answer_verdict"], b["answer_verdict"]}
        if verdicts == {"correct"}:
            human_correct = True
        elif verdicts <= {"incorrect", "partially_correct"}:
            human_correct = False
        else:
            human_correct = None
        hidden = cases[case_id]
        expected = _expected_behaviors(hidden["type"])
        behavior_ok = (a["expected_behavior"] in expected
                       and b["expected_behavior"] in expected)
        row = {
            "case_id": case_id,
            "original_id": hidden["original_id"],
            "type": hidden["type"],
            "auto_correct": hidden["auto_correct"],
            "human_correct": human_correct,
            "reviewer_A": {field: a[field] for field in RATING_FIELDS},
            "reviewer_B": {field: b[field] for field in RATING_FIELDS},
            "notes_A": a.get("reviewer_notes", ""),
            "notes_B": b.get("reviewer_notes", ""),
            "expected_behavior_matches": behavior_ok,
        }
        resolved.append(row)
        if human_correct is None or human_correct != hidden["auto_correct"] \
                or not behavior_ok:
            disagreements.append(row)

    total = len(case_ids)
    if not complete_ids or len(complete_ids) < total:
        status = "waiting_for_reviewers"
    elif owner_attested:
        status = "completed_owner_attested_not_independent"
    else:
        status = "complete"
    result = {
        "version": 3,
        "status": status,
        "independence": ("owner_attested_not_independent" if owner_attested
                         else "two_reviewer_sheets"),
        "reviewer_ids": sorted(reviewer_ids),
        "cases_expected": total,
        "completion": {name: f"{count}/{total}"
                       for name, count in completion.items()},
        "complete_both": len(complete_ids),
        "invalid_values": invalid,
        "agreement_percent": agreements,
        "cohen_kappa": kappas,
        "auto_vs_human": {
            "resolved": sum(1 for row in resolved
                            if row["human_correct"] is not None),
            "agreements": sum(1 for row in resolved
                              if row["human_correct"] is not None
                              and row["human_correct"] == row["auto_correct"]),
            "disagreements": len(disagreements),
        },
        "disagreements": disagreements,
    }
    return (0 if not invalid else 2), result


def write_report(review_dir: str, result: dict) -> None:
    json_path = os.path.join(review_dir, "blind_review_report.json")
    md_path = os.path.join(review_dir, "blind_review_report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    lines = [
        "# 盲评报告",
        "",
        f"- 状态：{result['status']}",
        f"- 独立性：{result['independence']}",
        f"- 评审标识：{', '.join(result['reviewer_ids']) or '—'}",
        f"- 双人完成：{result['complete_both']}/{result['cases_expected']}",
        f"- A 完成：{result['completion']['A']}",
        f"- B 完成：{result['completion']['B']}",
        f"- 自动评分与人工共识一致："
        f"{result['auto_vs_human']['agreements']}/"
        f"{result['auto_vs_human']['resolved']}",
        f"- 待复核差异：{result['auto_vs_human']['disagreements']}",
        "",
        "## 一致性",
        "",
        "| 维度 | 完全一致 | Cohen κ |",
        "|---|---:|---:|",
    ]
    for field in RATING_FIELDS:
        pct = result["agreement_percent"][field]
        kappa = result["cohen_kappa"][field]
        lines.append(f"| {field} | "
                     f"{'—' if pct is None else f'{pct:.1f}%'} | "
                     f"{'—' if kappa is None else f'{kappa:.3f}'} |")
    lines += ["", "## 差异题", ""]
    if not result["disagreements"]:
        lines.append("无已解决的人工差异。")
    else:
        lines += ["| 盲评号 | 原题号 | 类型 | A 答案判定 | B 答案判定 | 说明 |",
                  "|---|---|---|---|---|---|"]
        for row in result["disagreements"]:
            note = (row["notes_A"] or row["notes_B"]).replace("|", "／")[:120]
            lines.append(
                f"| {row['case_id']} | {row['original_id']} | {row['type']} | "
                f"{row['reviewer_A']['answer_verdict']} | "
                f"{row['reviewer_B']['answer_verdict']} | {note or '—'} |")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"报告 → {md_path}")
    print(f"结构化结果 → {json_path}")


def selftest(review_dir: str = DEFAULT_DIR) -> None:
    """Fill two temporary reviewer sheets and verify complete scoring paths."""
    with tempfile.TemporaryDirectory() as temp:
        for name in ("reviewer_A.csv", "reviewer_B.csv",
                     "coordinator_key.json"):
            shutil.copy2(os.path.join(review_dir, name), temp)
        key = json.load(open(os.path.join(temp, "coordinator_key.json"),
                             encoding="utf-8"))
        for name in ("reviewer_A.csv", "reviewer_B.csv"):
            path = os.path.join(temp, name)
            rows = load_csv(path)
            fields = list(rows[0])
            for row in rows:
                hidden = key["cases"][row["case_id"]]
                kind = hidden["type"]
                row.update({
                    "reviewer_id": f"selftest-{name}",
                    "question_quality": "clear",
                    "expected_behavior": {
                        "in_force": "answer_with_current_law",
                        "trap": "explain_repealed_or_refuse",
                        "off_corpus": "refuse",
                        "questionable": "answer_with_uncertainty",
                    }[kind],
                    "answer_verdict": "correct" if hidden["auto_correct"]
                    else "incorrect",
                    "citation_verdict": "not_applicable"
                    if kind == "off_corpus" else "supported",
                    "force_verdict": "appropriate",
                    "confidence": "high",
                    "reviewer_notes": "selftest",
                })
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        code, result = score(temp)
        ok = (code == 0 and result["status"] == "complete"
              and result["complete_both"] == 80
              and result["auto_vs_human"]["disagreements"] == 0)
        print(f"{'通过' if ok else '失败'}  双人盲评汇总自检："
              f"{result['complete_both']}/80，差异 "
              f"{result['auto_vs_human']['disagreements']}")
        if not ok:
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=DEFAULT_DIR)
    parser.add_argument("--selftest", action="store_true",
                        help="用临时合成的双人评分验证汇总器")
    args = parser.parse_args()
    if args.selftest:
        selftest(args.dir)
        return
    code, result = score(args.dir)
    write_report(args.dir, result)
    print(f"状态 {result['status']}；双人完成 "
          f"{result['complete_both']}/{result['cases_expected']}")
    if result["invalid_values"]:
        print("非法评分值：")
        for problem in result["invalid_values"][:20]:
            print(" ", problem)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
