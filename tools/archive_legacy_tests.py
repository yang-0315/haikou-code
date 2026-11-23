"""Copy historical test evidence into one inspectable archive.

The canonical files stay at their original paths because existing scripts use
them at runtime. This tool creates a read-only-in-practice snapshot with
SHA-256 hashes and source provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "test_runs" / "legacy-archive"

FILES = (
    ("qa/eval_set.json", "haikou/eval_set.json", "qa"),
    (
        "qa/eval_set_v3_additions.json",
        "haikou/eval_set_v3_additions.json",
        "qa",
    ),
    ("qa/eval_results_v3.json", "haikou/eval_results_v3.json", "qa"),
    ("qa/_eval_results.json", "haikou/_eval_results.json", "qa"),
    ("legacy-checks/_check.json", "database/_check.json", "legacy-checks"),
    (
        "legacy-checks/_selfcheck.json",
        "database/_selfcheck.json",
        "legacy-checks",
    ),
    (
        "legacy-checks/_spotcheck.md",
        "database/_spotcheck.md",
        "legacy-checks",
    ),
    (
        "pageindex-sample/manifest.json",
        "haikou/_pi_manifest.json",
        "pageindex-sample",
    ),
)

DIRECTORIES = (
    ("blind-review-v3", "haikou/blind_review_v3", "blind-review"),
    ("temporal-review-v1", "haikou/temporal_review_v1", "temporal-review"),
    (
        "pageindex-sample/pdf",
        "haikou/_pi_samples",
        "pageindex-sample",
    ),
    (
        "pageindex-sample/store",
        "haikou/_pi_store",
        "pageindex-sample",
    ),
)

PATTERNS = (
    ("logs", "database", "_crawl.log*", "legacy-logs"),
    ("logs", "database", "_repeal_run*.log*", "legacy-logs"),
    ("logs", "database", "_fetch_*.log*", "legacy-logs"),
    ("logs", "database", "_targeted.log*", "legacy-logs"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def add_entry(
    entries: list[dict],
    source: Path,
    archive: Path,
    group: str,
) -> None:
    if not source.is_file():
        return
    destination = DEST / archive
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    entries.append(
        {
            "group": group,
            "source": relative(source),
            "archive": destination.relative_to(DEST).as_posix(),
            "bytes": source.stat().st_size,
            "source_modified": datetime.fromtimestamp(
                source.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds"),
            "sha256": sha256(source),
        }
    )


def render_readme(manifest: dict) -> str:
    return f"""# 历史测试资料归档

本目录汇总了引入 `test_runs/YYYY-MM-DD_round-NN/` 批次目录之前的测试资料。

原始文件仍保留在原路径，因为项目命令和命令行默认参数仍从那里读取。这里的文件是
归档快照，不替代运行时真实来源。

## 内容

| 分类 | 归档路径 | 文件数 | 字节数 |
|---|---|---:|---:|
| 评测题集与回答 | `qa/` | {manifest['groups']['qa']['files']} | {manifest['groups']['qa']['bytes']} |
| 双人评审材料 | `blind-review-v3/` | {manifest['groups']['blind-review']['files']} | {manifest['groups']['blind-review']['bytes']} |
| 时点证据人工核对队列 | `temporal-review-v1/` | {manifest['groups']['temporal-review']['files']} | {manifest['groups']['temporal-review']['bytes']} |
| 自检与抽检 | `legacy-checks/` | {manifest['groups']['legacy-checks']['files']} | {manifest['groups']['legacy-checks']['bytes']} |
| 抓取与废止扫描日志 | `logs/` | {manifest['groups']['legacy-logs']['files']} | {manifest['groups']['legacy-logs']['bytes']} |
| PageIndex 真实客户端样例 | `pageindex-sample/` | {manifest['groups']['pageindex-sample']['files']} | {manifest['groups']['pageindex-sample']['bytes']} |

## 边界

- `blind-review-v3` 是所有者确认转录的 A/B 表，不是独立双人盲评。
- 2026 年 9–10 月日志记录的是当时的数据维护过程，不能替代当前审计脚本重跑。
- 本轮完整测试仍位于 `../2026-11-22_round-01/`。
- 每个归档文件的来源路径、大小和 SHA-256 均记录在 `manifest.json`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_DEST))
    args = parser.parse_args()

    global DEST
    DEST = Path(args.output).resolve()
    DEST.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    for archive_name, source_name, group in FILES:
        add_entry(entries, ROOT / source_name, Path(archive_name), group)

    for archive_name, source_name, group in DIRECTORIES:
        source_dir = ROOT / source_name
        if not source_dir.is_dir():
            continue
        for source in sorted(path for path in source_dir.rglob("*") if path.is_file()):
            rel = source.relative_to(source_dir)
            add_entry(entries, source, Path(archive_name) / rel, group)

    for archive_name, source_name, pattern, group in PATTERNS:
        source_dir = ROOT / source_name
        for source in sorted(source_dir.glob(pattern)):
            add_entry(
                entries,
                source,
                Path(archive_name) / source.name,
                group,
            )

    unique = {entry["archive"]: entry for entry in entries}
    entries = [unique[key] for key in sorted(unique)]
    group_names = (
        "qa",
        "blind-review",
        "temporal-review",
        "legacy-checks",
        "legacy-logs",
        "pageindex-sample",
    )
    groups = {
        group: {
            "files": sum(item["group"] == group for item in entries),
            "bytes": sum(
                item["bytes"] for item in entries if item["group"] == group
            ),
        }
        for group in group_names
    }
    manifest = {
        "archive_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(ROOT),
        "authoritative_location": "original source paths",
        "groups": groups,
        "total": {
            "files": len(entries),
            "bytes": sum(item["bytes"] for item in entries),
        },
        "files": entries,
    }
    (DEST / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (DEST / "README.md").write_text(
        render_readme(manifest),
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Archived {len(entries)} files "
        f"({manifest['total']['bytes']} bytes) to {DEST}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
