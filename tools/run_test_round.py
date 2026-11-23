"""Run one reproducible project test round and preserve its evidence.

The runner intentionally uses subprocesses instead of importing project
modules, so a partial failure cannot prevent the remaining audit groups from
running. Raw stdout/stderr, timing, exit codes, and environment metadata are
written under ``test_runs/<timestamp>_round-NN``.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = Path(os.environ.get("POLICY_PYTHON", sys.executable))
PAGEINDEX_PYTHON = ROOT / "PageIndex" / ".venv" / "Scripts" / "python.exe"


@dataclass(frozen=True)
class TestCase:
    test_id: str
    title: str
    args: tuple[str, ...]
    timeout: int
    required: bool = True
    category: str = "offline"
    python: Path | None = None
    cwd: Path | None = None


def core_tests() -> list[TestCase]:
    py = DEFAULT_PYTHON
    return [
        TestCase(
            "runtime-preflight",
            "Python 主运行时与关键依赖",
            (
                "-c",
                "import sys, pypdf, reportlab, pdfplumber; "
                "print(sys.executable); print(sys.version); print('deps ok')",
            ),
            30,
            python=py,
            category="preflight",
        ),
        TestCase(
            "qa-data-validation",
            "84 题数据契约、条文效力与路由覆盖",
            ("haikou/eval_qa.py", "--validate-data"),
            180,
            python=py,
            category="qa",
        ),
        TestCase(
            "qa-scorer-selftest",
            "validity-QA 评分器自检",
            ("haikou/eval_qa.py", "--selftest"),
            60,
            python=py,
            category="qa",
        ),
        TestCase(
            "validity-qa-cached",
            "84 题结果新鲜度校验与离线复评",
            ("haikou/eval_qa.py",),
            180,
            python=py,
            category="qa",
        ),
        TestCase(
            "spine-check",
            "推理协议单一来源与依赖方向",
            ("haikou/spine_check.py",),
            120,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-router",
            "检索策略路由",
            ("haikou/audit_router.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-catalog",
            "事项目录解析与接入",
            ("haikou/audit_catalog.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-data-debt",
            "三版目录、时点日期与文号数据债",
            ("haikou/audit_data_debt.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "policy-update-selftest",
            "政策更新扫描与差异分类",
            ("haikou/policy_update_scan.py", "--selftest"),
            60,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-update-workflow",
            "更新取证、审核闸门、发布与回滚",
            ("haikou/audit_update_workflow.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-tree-force",
            "带效力树与 PageIndex 适配层",
            ("haikou/audit_tree_force.py",),
            300,
            python=PAGEINDEX_PYTHON,
            category="audit",
        ),
        TestCase(
            "audit-tree-retrieval",
            "沿树检索",
            ("haikou/audit_tree_retrieval.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-reasoning",
            "带效力推理检索",
            ("haikou/audit_reasoning.py",),
            300,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-temporal",
            "历史时点效力计算",
            ("haikou/audit_temporal.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-temporal-coverage",
            "时点证据索引与降级边界",
            ("haikou/audit_temporal_coverage.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-provider",
            "模型 Provider 配置与重试",
            ("haikou/audit_provider.py",),
            120,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-article-force",
            "条文级部分废止",
            ("haikou/audit_article_force.py",),
            300,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-service",
            "Web 服务与异步 API",
            ("haikou/audit_service.py",),
            180,
            python=py,
            category="audit",
        ),
        TestCase(
            "audit-repeal",
            "废止关系与名称闸门",
            ("haikou/audit_repeal.py",),
            120,
            python=py,
            category="audit",
        ),
        TestCase(
            "score-v2",
            "标注样本精确率与召回率",
            ("haikou/score_v2.py",),
            120,
            python=py,
            category="analysis",
        ),
        TestCase(
            "stability-check",
            "效力和引用策略稳定性",
            ("haikou/stability_check.py",),
            180,
            python=py,
            category="analysis",
        ),
        TestCase(
            "provider-live-smoke",
            "在线模型最小连通性",
            ("haikou/llm_provider.py", "--chat", "只回复 OK"),
            300,
            required=False,
            category="online",
            python=py,
        ),
    ]


def upstream_tests() -> TestCase:
    return TestCase(
        "pageindex-pytest",
        "PageIndex 上游单元测试",
        ("-m", "pytest", "-q", "tests"),
        900,
        category="upstream",
        python=PAGEINDEX_PYTHON,
        cwd=ROOT / "PageIndex",
    )


def choose_output_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    parent = ROOT / "test_runs"
    for index in range(1, 1000):
        candidate = parent / f"{day}_round-{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("too many test rounds for today")


def git_value(*args: str) -> str:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
    except OSError as exc:
        return f"unavailable: {exc}"


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def last_meaningful_line(stdout: str, stderr: str) -> str:
    for block in (stdout, stderr):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines:
            return re.sub(r"\s+", " ", lines[-1])[:300]
    return ""


def run_case(case: TestCase, output: Path, env: dict[str, str]) -> dict:
    python = case.python or DEFAULT_PYTHON
    command = (str(python), *case.args)
    started = time.perf_counter()
    timed_out = False
    if not python.exists():
        return {
            "id": case.test_id,
            "title": case.title,
            "category": case.category,
            "required": case.required,
            "command": list(command),
            "status": "error",
            "exit_code": None,
            "duration_seconds": 0.0,
            "summary": f"interpreter not found: {python}",
            "stdout_file": None,
            "stderr_file": None,
        }
    try:
        completed = subprocess.run(
            command,
            cwd=case.cwd or ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=case.timeout,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        exit_code = None
    duration = time.perf_counter() - started

    stdout_path = output / f"{case.test_id}.stdout.txt"
    stderr_path = output / f"{case.test_id}.stderr.txt"
    write_text(stdout_path, stdout)
    write_text(stderr_path, stderr)
    status = "timeout" if timed_out else ("pass" if exit_code == 0 else "fail")
    return {
        "id": case.test_id,
        "title": case.title,
        "category": case.category,
        "required": case.required,
        "command": list(command),
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "summary": last_meaningful_line(stdout, stderr),
        "stdout_file": stdout_path.name,
        "stderr_file": stderr_path.name,
    }


def render_markdown(batch: dict) -> str:
    lines = [
        f"# 测试批次 {batch['batch_id']}",
        "",
        f"- 开始：{batch['started_at']}",
        f"- 结束：{batch['finished_at']}",
        f"- 核心结论：**{batch['core_status']}**",
        f"- 在线模型：**{batch['online_status']}**",
        f"- PageIndex 上游：**{batch['upstream_status']}**",
        f"- Git 提交：`{batch['environment']['git_commit'] or 'unknown'}`",
        f"- 工作区：{batch['environment']['git_status'] or 'clean'}",
        "",
        "| 类别 | 测试 | 结果 | 耗时(s) | 摘要 |",
        "|---|---|---|---:|---|",
    ]
    for item in batch["tests"]:
        summary = item["summary"].replace("|", r"\|")
        lines.append(
            f"| {item['category']} | {item['title']} | "
            f"{item['status']} | {item['duration_seconds']:.3f} | {summary} |"
        )
    lines.extend(
        [
            "",
            "说明：原始 stdout、stderr 与逐项退出码保存在同目录；"
            "在线模型测试不参与核心离线结论。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="", help="batch output directory")
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="also run PageIndex upstream pytest",
    )
    parser.add_argument(
        "--no-online",
        action="store_true",
        help="skip the minimal live provider smoke test",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated test ids",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="merge selected retries into an existing batch directory",
    )
    args = parser.parse_args()

    output = choose_output_dir(args.output or None)
    previous: dict = {}
    if output.exists() and not args.resume:
        print(f"output already exists: {output}", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=args.resume)
    summary_path = output / "summary.json"
    if args.resume and summary_path.exists():
        previous = json.loads(summary_path.read_text(encoding="utf-8"))
    tests = core_tests()
    if args.no_online:
        tests = [case for case in tests if case.category != "online"]
    if args.upstream:
        tests.append(upstream_tests())
    if args.only:
        selected = {item.strip() for item in args.only.split(",") if item.strip()}
        tests = [case for case in tests if case.test_id in selected]
    if not tests:
        print("no tests selected", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    started_at = (
        previous.get("started_at")
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )
    environment = {
        "host": platform.node(),
        "platform": platform.platform(),
        "runner_python": sys.executable,
        "runner_python_version": platform.python_version(),
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_branch": git_value("branch", "--show-current"),
        "git_status": git_value("status", "--porcelain"),
        "default_python": str(DEFAULT_PYTHON),
        "pageindex_python": str(PAGEINDEX_PYTHON),
    }
    write_text(
        output / "environment.json",
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n",
    )

    old_results = {
        item["id"]: item for item in previous.get("tests", [])
    }
    results = []
    for case in tests:
        print(f"[{len(results) + 1}/{len(tests)}] {case.test_id}: {case.title}")
        result = run_case(case, output, env)
        results.append(result)
        old_results[case.test_id] = result
        print(
            f"  {result['status']} ({result['duration_seconds']:.3f}s) "
            f"{result['summary']}"
        )

    finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
    selected_ids = {item.test_id for item in tests}
    results = [
        old_results[item["id"]]
        for item in previous.get("tests", [])
        if item["id"] not in selected_ids
    ] + results
    results.sort(key=lambda item: (
        next(
            index
            for index, case in enumerate(core_tests() + [upstream_tests()])
            if case.test_id == item["id"]
        )
        if any(
            case.test_id == item["id"]
            for case in core_tests() + [upstream_tests()]
        )
        else 999,
        item["id"],
    ))
    required = [
        item for item in results
        if item["required"] and item["category"] != "upstream"
    ]
    core_failed = [
        item for item in required if item["status"] != "pass"
    ]
    online = [item for item in results if item["category"] == "online"]
    online_failed = [item for item in online if item["status"] != "pass"]
    upstream = [item for item in results if item["category"] == "upstream"]
    upstream_failed = [item for item in upstream if item["status"] != "pass"]
    batch = {
        "batch_id": output.name,
        "started_at": started_at,
        "finished_at": finished_at,
        "core_status": "PASS" if not core_failed else "FAIL",
        "online_status": (
            "SKIPPED"
            if not online
            else ("PASS" if not online_failed else "FAIL")
        ),
        "upstream_status": (
            "NOT_RUN"
            if not upstream
            else ("PASS" if not upstream_failed else "FAIL")
        ),
        "environment": environment,
        "tests": results,
    }
    if args.resume:
        batch["resumed_at"] = finished_at
    write_text(
        output / "summary.json",
        json.dumps(batch, ensure_ascii=False, indent=2) + "\n",
    )
    write_text(output / "summary.md", render_markdown(batch))
    print(f"\nSaved test evidence to {output}")
    print(
        f"Core: {batch['core_status']}  Online: {batch['online_status']}  "
        f"Upstream: {batch['upstream_status']}"
    )
    return 0 if not core_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
