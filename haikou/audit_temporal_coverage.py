"""Audit temporal evidence integrity without requiring 100% date coverage."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docstore
import temporal_evidence
from force_layer import ForceLayer
from warn import IN_FORCE, QUESTIONABLE

from paths import DEFAULT_DATABASE as DEFAULT_DB


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:                      # noqa: BLE001
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    print(f"{'通过' if ok else '失败'}  {title}")
    if detail:
        print(f"        {detail}")
    return bool(ok), detail


def main():
    database = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    paths = [
        os.path.join(database, temporal_evidence.INDEX_NAME),
        os.path.join(HERE, temporal_evidence.INDEX_NAME),
    ]
    index_path = next((path for path in paths if os.path.exists(path)), "")
    if not index_path:
        print("缺少时点证据索引：先运行 build_temporal_index.py")
        return 1
    index = json.load(open(index_path, encoding="utf-8"))
    rows = list(index.get("documents", {}).values())
    results = []

    def corpus_count():
        expected = len(docstore.corpus_files(database)
                       + docstore.library_files(database))
        return len(rows) == expected, f"索引 {len(rows)}／语料 {expected}"

    def fingerprint_current():
        ok = temporal_evidence.index_is_current(index, database)
        return ok, ("文件数、字节与 mtime 指纹一致" if ok
                    else "语料已变化，请重建时点证据索引")

    def overrides_traceable():
        path = os.path.join(database, temporal_evidence.OVERRIDE_NAME)
        if not os.path.exists(path):
            return False, f"缺少 {temporal_evidence.OVERRIDE_NAME}"
        data = json.load(open(path, encoding="utf-8"))
        entries = data.get("documents", data)
        docs_by_file = {row["file"]: row for row in rows}
        bad = []
        for file_name, override in entries.items():
            if file_name not in docs_by_file:
                bad.append(f"{file_name}：不在语料索引")
            elif not override.get("confirmed_by") or not override.get("note"):
                bad.append(f"{file_name}：缺 confirmed_by／note")
            elif docs_by_file[file_name].get("override", {}).get("confirmed_by") \
                    != override.get("confirmed_by"):
                bad.append(f"{file_name}：覆盖未进入正式索引")
        return not bad, (f"人工覆盖 {len(entries)} 条均可追溯"
                         if not bad else f"异常 {bad[:8]}")

    def classifications():
        allowed = set(temporal_evidence.KINDS)
        bad = [row["title"] for row in rows
               if row.get("instrument_kind") not in allowed
               or not isinstance(row.get("requires_effective_date"), bool)]
        return not bad, (f"{len(rows)} 份均已分类" if not bad
                         else f"异常 {bad[:5]}")

    def confirmed_traceable():
        bad = []
        for row in rows:
            if not row.get("effective_date"):
                continue
            events = [event for event in row.get("date_evidence", [])
                      if event.get("kind") == "effective"
                      and event.get("date") == row["effective_date"]
                      and event.get("source")
                      and event.get("evidence")
                      and event.get("confidence")]
            if not events:
                bad.append(row["title"])
        return not bad, (f"明确施行日 {sum(bool(r.get('effective_date')) for r in rows)}"
                         f" 份均有来源句" if not bad else f"缺证据 {bad[:8]}")

    def missing_declared():
        bad = []
        for row in rows:
            if row.get("requires_effective_date") and not row.get("effective_date"):
                if "明确施行日" not in (row.get("missing") or []):
                    bad.append(row["title"])
        return not bad, (f"需施行日但缺失 {sum(bool(r.get('requires_effective_date')) and not bool(r.get('effective_date')) for r in rows)}"
                         f" 份均显式声明缺失" if not bad else f"漏标 {bad[:8]}")

    def policy_separated():
        bad = [
            row["title"] for row in rows
            if not row.get("requires_effective_date")
            and (row.get("published_at") or row.get("document_date"))
            and row.get("coverage") != ("confirmed_effective"
                                        if row.get("effective_date")
                                        else "published_policy")
        ]
        return not bad, ("政策／规划类文件已单独归类"
                         if not bad else f"异常 {bad[:8]}")

    def date_bounds():
        low, high = dt.date(1949, 1, 1), dt.date(2100, 1, 1)
        bad = []
        for row in rows:
            for event in row.get("date_evidence", []):
                try:
                    value = dt.date.fromisoformat(event["date"])
                except (KeyError, ValueError):
                    bad.append(f"{row['title']}：格式 {event}")
                    continue
                if not low <= value <= high:
                    bad.append(f"{row['title']}：{value}")
        return not bad, ("日期均落在 1949-01-01 至 2100 年"
                         if not bad else f"越界 {bad[:8]}")

    def url_month_not_effective():
        bad = [row["title"] for row in rows if row.get("effective_date")
               and not any(event.get("kind") == "effective"
                           for event in row.get("date_evidence", []))]
        return not bad, ("URL 月份从未升级为施行日"
                         if not bad else f"疑似推断 {bad[:8]}")

    def runtime_known_regulation():
        layer = ForceLayer(database)
        title = "三亚市河道生态保护管理条例"
        record = next(row for row in rows if row["title"] == title)
        status = layer.status_for(
            title, as_of="2023-12-31", file=record["file"])
        temporal = status.get("temporal") or {}
        ok = (status["status"] == IN_FORCE
              and temporal.get("document_dates", {}).get("coverage")
              == "confirmed_effective")
        return ok, f"{status['status']}／{temporal.get('document_dates', {}).get('coverage')}"

    def runtime_missing_required():
        candidate = next(row for row in rows
                         if row.get("requires_effective_date")
                         and not row.get("effective_date")
                         and (row.get("published_at") or row.get("document_date")))
        point = candidate.get("published_at") or candidate.get("document_date")
        layer = ForceLayer(database)
        status = layer.status_for(candidate["title"], as_of=point,
                                  file=candidate["file"])
        ok = status["status"] == QUESTIONABLE
        return ok, f"{candidate['title'][:34]} → {status['reason']['code']}"

    def runtime_policy_basis():
        trees_path = os.path.join(database, "_force_trees.json")
        tree_status = {}
        if os.path.exists(trees_path):
            trees = json.load(open(trees_path, encoding="utf-8"))
            tree_status = {row["file"]: row.get("status", {}).get("status")
                           for row in trees.get("docs", {}).values()}
        candidate = next(row for row in rows
                         if not row.get("requires_effective_date")
                         and row.get("coverage") == "published_policy"
                         and tree_status.get(row["file"]) == IN_FORCE)
        point = candidate.get("published_at") or candidate.get("document_date")
        layer = ForceLayer(database)
        status = layer.status_for(candidate["title"], as_of=point,
                                  file=candidate["file"])
        ok = (status["status"] == IN_FORCE
              and status["temporal"]["verdict"] == "in_force")
        return ok, (f"{candidate['title'][:34]}／{candidate['instrument_kind']}"
                    f" → {status['status']}")

    results += [
        probe("索引覆盖全部缓存语料", corpus_count),
        probe("证据索引与当前语料指纹一致", fingerprint_current),
        probe("人工日期覆盖可追溯并已进入索引", overrides_traceable),
        probe("每份文件均有类型和施行日要求", classifications),
        probe("明确施行日都带来源句", confirmed_traceable),
        probe("施行日缺失均显式声明", missing_declared),
        probe("规划／通知／政策与法规分层", policy_separated),
        probe("日期范围合法", date_bounds),
        probe("URL 月份不单独充当施行日", url_month_not_effective),
        probe("已知法规按证据索引切换时点", runtime_known_regulation),
        probe("法规缺少施行日仍降为存疑", runtime_missing_required),
        probe("发布型政策按发布日给出较低依据结论", runtime_policy_basis),
    ]
    passed = sum(ok for ok, _ in results)
    stats = index.get("stats", {})
    print(f"\n覆盖：明确施行日 {stats.get('effective_date')}，"
          f"需施行日但缺失 {stats.get('required_missing_effective')}，"
          f"政策发布型 {stats.get('coverage', {}).get('published_policy')}")
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
