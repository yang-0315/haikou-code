"""Build the article-level legal-force event index.

By default the generated file is kept beside this module so the corpus
database remains untouched. Runtime accepts this fallback and also prefers an
index copied into the database when one is available.

    python build_article_force_index.py
    python build_article_force_index.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import article_force

from paths import DEFAULT_DATABASE as DEFAULT_DB
DEFAULT_OUT = os.path.join(HERE, article_force.INDEX_NAME)


def main() -> None:
    parser = argparse.ArgumentParser(description="重建条文级部分废止索引")
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    index = article_force.build_index(args.database)
    summary = index["summary"]
    print(
        f"条文效力事件 {summary['events']} 条，"
        f"来源决定 {summary['sources']} 份，"
        f"目标文件 {summary['targets']} 份，"
        f"条文目标 {summary['article_targets']} 个"
    )
    print(
        f"可直接应用 {summary['applicable_files']} 份；"
        f"已吸收修改、旧条号不直接套用 "
        f"{summary['consolidated_files']} 份"
    )
    for note, count in summary["notes"].items():
        print(f"  {note}：{count}")
    if args.dry_run:
        return
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
    print(f"写入 {args.out}")


if __name__ == "__main__":
    main()
