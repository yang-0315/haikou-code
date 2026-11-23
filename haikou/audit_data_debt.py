"""Offline audit for catalog versions, temporal dates, and document numbers."""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import catalog_index
import data_debt
import document_numbers
import temporal_evidence

from paths import DEFAULT_DATABASE as DB


class Audit:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}{': ' + detail if detail else ''}")


def main() -> None:
    audit = Audit()
    print("== 三版事项目录 ==")
    rows = catalog_index.load_items(DB)
    years = {}
    for row in rows:
        years.setdefault(row.get("catalog_year"), []).append(row)
    audit.check(
        "2022／2023／2024 版本齐全",
        all(year in years for year in (2022, 2023, 2024)),
        str(sorted(years)),
    )
    for year in (2022, 2023, 2024):
        group = years.get(year, [])
        clean = sum(
            1 for row in group
            if catalog_index.ARTICLE_RE.search(row.get("article", ""))
            and len(row.get("article", "")) >= 20
        )
        audit.check(
            f"{year} 年版事项与条款完整",
            bool(group) and clean == len(group),
            f"{clean}/{len(group)}",
        )

    print("== 时点日期 ==")
    synthetic = (
        "测试办法\n"
        "2025年1月2日\n"
        "本办法自公布之\n日起施行。\n"
        "第一条 测试。"
    )
    record = temporal_evidence.extract_evidence(
        "测试办法", synthetic, url="https://www.hainan.gov.cn/202501/test.shtml")
    audit.check(
        "自公布之日起施行可跨 PDF 折行识别",
        record.get("effective_date") == "2025-01-02",
        repr(record.get("effective_date")),
    )
    no_publication = temporal_evidence.extract_evidence(
        "测试办法",
        "第一条 测试办法。\n本办法自公布之日起施行。",
        url="https://www.hainan.gov.cn/202501/test.shtml",
    )
    audit.check(
        "只有 URL 月份时不得猜施行日",
        no_publication.get("effective_date") is None,
        repr(no_publication.get("effective_date")),
    )
    dates = data_debt.audit_dates(DB)
    audit.check(
        "时点缺口已由 156 降至 83",
        dates["missing_effective"] == 83
        and sum(dates["reasons"].values()) == 83,
        f"{dates['missing_effective']} {dates['reasons']}",
    )

    print("== 文号 ==")
    cases = [
        ("琼府办〔2021〕71号\n各市县人民政府", "琼府办〔2021〕71号"),
        ("（2025年2月22日海南省人民政府令第329号公布）",
         "海南省人民政府令第329号"),
        ("三亚市人民代表大会常务委员会公告 (2025)第4号",
         "三亚市人民代表大会常务委员会公告(2025)第4号"),
    ]
    for text, expected in cases:
        found = document_numbers.extract(text, "三亚市测试条例")
        audit.check(
            f"提取文号 {expected}",
            bool(found) and found["number"] == expected,
            repr(found),
        )
    cited = document_numbers.extract(
        "测试办法\n根据《某规定》（国办发〔2019〕19号）制定本办法。",
        "测试办法",
    )
    audit.check("不把引用依据文号当成本文件文号", cited is None, repr(cited))
    numbers = data_debt.audit_numbers(DB)
    audit.check(
        "文号可提取数由 536 提升到至少 700",
        numbers["with_number"] >= 700,
        f"{numbers['with_number']}/{numbers['total_documents']}",
    )
    audit.check(
        "缺失文号与可提取数覆盖全部正文",
        numbers["with_number"] + numbers["missing_number"]
        == numbers["total_documents"],
        str(numbers["with_number"] + numbers["missing_number"]),
    )

    print("== 状态修复与并发保护 ==")
    with tempfile.TemporaryDirectory(prefix="catalog-state-audit-") as temp:
        state_path = os.path.join(temp, catalog_index.ITEMS_FILE)
        with open(state_path, "wb") as handle:
            handle.write(b'{"version":1,"catalogs":{}}garbage')
        strict_failed = False
        try:
            catalog_index.load_state(state_path)
        except ValueError:
            strict_failed = True
        repaired = catalog_index.load_state(state_path, repair=True)
        audit.check(
            "中断写入可显式恢复有效 JSON 前缀",
            strict_failed and repaired == {"version": 1, "catalogs": {}},
        )
        with catalog_index.build_lock(temp):
            lock_failed = False
            try:
                with catalog_index.build_lock(temp):
                    pass
            except RuntimeError:
                lock_failed = True
            audit.check("目录不同版本构建禁止并发写", lock_failed)

    total = audit.passed + audit.failed
    print(f"\ndata debt audit: {audit.passed}/{total}")
    raise SystemExit(0 if audit.failed == 0 else 1)


if __name__ == "__main__":
    main()
