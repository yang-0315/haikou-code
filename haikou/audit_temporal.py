"""Offline audit for point-in-time legal force.

The checks cover three different failure modes:

  * dates parsed at the wrong precision;
  * a period that crosses an effective/repeal event silently taking one end;
  * the historical status leaking into the live status, or vice versa.

No model or network call is required.

    python audit_temporal.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reasoning_retrieval as rr
import temporal
from force_layer import ForceLayer
from force_protocol import build_answer_prompt, build_select_prompt
from warn import IN_FORCE, QUESTIONABLE, REPEALED

from paths import DEFAULT_DATABASE as DEFAULT_DB
SAMPLE = "三亚市河道生态保护管理条例"


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:                      # noqa: BLE001
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    label = "通过" if ok else "失败"
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok, detail


def _base(status=REPEALED, effective_date="2025-09-15"):
    return {
        "status": status,
        "confidence": "high",
        "reason": {"code": "synthetic", "text": "audit"},
        "checked": {},
        "missing": [],
        "next_steps": [],
        "caveats": [],
        "evidence": {"effective_date": effective_date},
    }


def main():
    database = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    layer = ForceLayer(database)

    def exact_day():
        point = temporal.parse_as_of("2025-09-15")
        ok = point["start"] == point["end"] == "2025-09-15"
        return ok, f"{point['precision']} / {point['as_of']}"

    def month_and_year():
        month = temporal.parse_as_of("2024年2月")
        year = temporal.parse_as_of("2024年")
        ok = (month["as_of"] == "2024-02-29"
              and (year["start"], year["end"]) == ("2024-01-01", "2024-12-31"))
        return ok, f"月份末日 {month['as_of']}；年份区间 {year['start']}–{year['end']}"

    def future_rejected():
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        try:
            temporal.parse_as_of(tomorrow)
        except ValueError:
            return True, f"{tomorrow} 被拒绝，未来时点不能伪装成法律事实"
        return False, f"{tomorrow} 未拒绝"

    def intent_gate():
        asked = temporal.find_as_of("截至2024年9月时，这个办法还有效吗")
        ordinary = temporal.find_as_of("2021年海南自由贸易港办法第三条是什么")
        ok = asked and asked["precision"] == "month" and ordinary is None
        return ok, (f"历史问法识别为 {asked['label'] if asked else None}；"
                    "普通文件名年份不改写为时点查询")

    def source_dates():
        facts = temporal.parse_source_dates(
            "示例办法\n2020年1月2日公布，自2020年3月1日起施行。")
        ok = (facts["document_date"] == "2020-01-02"
              and facts["effective_date"] == "2020-03-01")
        return ok, json.dumps(facts, ensure_ascii=False)

    def synthetic_before_after():
        base = _base()
        text = "示例条例\n2016年8月17日公布\n自2016年11月1日起施行。"
        before = temporal.status_at(base, "2020-12-31", text)
        after = temporal.status_at(base, "2025-11-21", text)
        ok = (before["status"] == IN_FORCE
              and before["temporal"]["verdict"] == "in_force"
              and after["status"] == REPEALED)
        return ok, (f"废止前 {before['status']}；废止后 {after['status']}")

    def period_crossing():
        base = _base()
        text = "示例条例\n2016年8月17日公布\n自2016年11月1日起施行。"
        status = temporal.status_at(base, "2025年9月", text)
        ok = (status["status"] == QUESTIONABLE
              and status["temporal"]["verdict"] == "changed_within_period")
        return ok, status["reason"]["text"]

    def missing_effective():
        base = _base(IN_FORCE, effective_date=None)
        status = temporal.status_at(
            base, "2024-01-01", "示例办法\n2020年1月2日公布，施行日期另行公布。")
        ok = status["status"] == QUESTIONABLE
        return ok, status["reason"]["text"]

    def real_sample():
        current = layer.status_for(SAMPLE)
        before = layer.status_for(SAMPLE, as_of="2023-12-31")
        after = layer.status_for(SAMPLE, as_of="2025-11-21")
        unchanged = layer.status_for(SAMPLE)
        ok = (current["status"] == REPEALED
              and before["status"] == IN_FORCE
              and after["status"] == REPEALED
              and unchanged["status"] == REPEALED)
        return ok, (f"当前/2023-12-31/2025-11-21 = "
                    f"{current['status']}/{before['status']}/{after['status']}；"
                    "历史查询未污染实时缓存")

    def prompt_context():
        point = temporal.parse_as_of("2023-12-31")
        prompt = build_select_prompt("示例问题", "目录", point)
        answer = build_answer_prompt("示例问题", [], [], point)
        ok = ("2023-12-31" in prompt and "2023-12-31" in answer
              and "不得把今天的效力状态套用到历史问题" in prompt
              and "历史效力" in answer)
        return ok, "选条与作答提示都带时点，并明确禁止把历史状态写成当前状态"

    def end_to_end():
        query = f"{SAMPLE}第一条是什么"
        docs = rr.recall(database, query, 2)
        target = next(doc for doc in docs if doc["doc"] == SAMPLE)
        article = target["articles"][0]["article"]
        selected = json.dumps({
            "answerable": True,
            "read": [f"{SAMPLE}|{article}"],
            "reason": "audit",
            "force_note": "",
        }, ensure_ascii=False)

        def fake(messages, temperature=0.0, **kwargs):
            return selected if "JSON" in messages[0]["content"] else "TEST"

        before = rr.ask(query, database, 2, chat_fn=fake, route=False,
                        as_of="2023-12-31")
        after = rr.ask(query, database, 2, chat_fn=fake, route=False,
                       as_of="2025-11-21")
        ok = (before["sources"][0]["status"] == IN_FORCE
              and after["sources"][0]["status"] == REPEALED
              and before["as_of"]["as_of"] == "2023-12-31")
        return ok, (f"同一篇 {article}：2023 → {before['sources'][0]['status']}；"
                    f"2025 → {after['sources'][0]['status']}")

    def adapter_same():
        import pageindex_force_adapter as adapter
        direct = layer.status_for(SAMPLE, as_of="2023-12-31")["status"]
        client = adapter.ForceAwareClient(
            adapter._FakeClient(SAMPLE), database, as_of="2023-12-31")
        via_tree = client.get_tree("doc-1")["force"]["status"]
        return direct == via_tree, f"直连 {direct}／适配器 {via_tree}"

    def tree_backend_same():
        import tree_index
        import tree_retrieval as tr
        store = tree_index.LocalTreeStore(database)
        query = f"{SAMPLE}第一条是什么"
        before, _ = tr.load_records(
            store, database, query, 2, temporal.parse_as_of("2023-12-31"))
        after, _ = tr.load_records(
            store, database, query, 2, temporal.parse_as_of("2025-11-21"))
        old = next(row for row in before if row["title"] == SAMPLE)["status"]["status"]
        new = next(row for row in after if row["title"] == SAMPLE)["status"]["status"]
        return (old == IN_FORCE and new == REPEALED,
                f"沿树目录：2023 → {old}；2025 → {new}")

    results = [
        probe("具体日期保留日精度", exact_day),
        probe("月份与年份取完整区间，不猜成月初", month_and_year),
        probe("未来时点被拒绝", future_rejected),
        probe("只有历史语义才自动触发时点检索", intent_gate),
        probe("从文头分离公布日与明确施行日", source_dates),
        probe("合成样本：废止前有效、废止后失效", synthetic_before_after),
        probe("月份跨过废止日时降为存疑", period_crossing),
        probe("缺少施行日时不升级为确定有效", missing_effective),
        probe("真实样本三时点判定且缓存隔离", real_sample),
        probe("选条与作答提示贯穿查询时点", prompt_context),
        probe("完整问答在历史时点改变引用效力", end_to_end),
        probe("PageIndex 适配器与直连力层一致", adapter_same),
        probe("沿树检索目录使用同一时点状态", tree_backend_same),
    ]
    passed = sum(1 for ok, _ in results if ok)
    print(f"\n通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
