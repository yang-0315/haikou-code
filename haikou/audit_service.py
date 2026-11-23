"""Offline audit for the user-facing policy service.

The audit starts the real HTTP server on an ephemeral localhost port and injects
a deterministic fake ask function.  It therefore checks routing, validation,
job lifecycle, result minimization and browser assets without calling a model.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reasoning_retrieval
import service
import update_workflow


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


def fake_ask(question, database, as_of=None):
    time.sleep(0.08)
    return {
        "query": question,
        "answer": "答复正文。",
        "refusal": None,
        "refusal_stage": None,
        "sources": [{
            "doc": "测试政策文件",
            "article": "第三条",
            "status": "现行有效",
            "doc_number": "琼测试〔2026〕1号",
        }],
        "forces": {
            "测试政策文件": {
                "status": "现行有效",
                "confidence": "high",
                "reason": {"text": "未发现废止依据。"},
                "missing": [],
                "next_steps": [],
                "caveats": ["仅用于离线审计。"],
                "evidence": {"note": "测试证据"},
            },
        },
        "outline": "不应出现在浏览器响应中的大段目录",
        "notes": ["测试选条说明"],
        "reason": "测试选条理由",
        "force_note": "优先使用现行有效条文",
        "route": {
            "strategy": "article_exact",
            "path": "flat",
            "docs_wanted": 2,
            "refuse": False,
            "reason": "识别到具体条号",
            "signals": {"article_ref": True},
            "caveats": [],
        },
        "as_of": as_of,
    }


def request(port: int, method: str, path: str, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = body
    if isinstance(body, dict):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    default_headers = {}
    if isinstance(body, dict):
        default_headers["Content-Type"] = "application/json"
    default_headers.update(headers or {})
    connection.request(method, path, body=payload, headers=default_headers)
    response = connection.getresponse()
    data = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    try:
        parsed = json.loads(data.decode("utf-8")) if data else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    return response.status, response_headers, parsed, data


def main():
    audit = Audit()
    app = service.ServiceApplication(
        reasoning_retrieval.DEFAULT_DB, ask_fn=fake_ask, max_workers=1)
    server = service.PolicyHTTPServer(("127.0.0.1", 0), app)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        print("== 静态入口 ==")
        status, headers, _, body = request(port, "GET", "/")
        html = body.decode("utf-8", errors="replace")
        audit.check("首页可访问", status == 200 and "琼策" in html)
        audit.check("首页带安全响应头",
                    "default-src 'self'" in headers.get(
                        "Content-Security-Policy", ""))
        audit.check("首页引用可移植的脚本和样式",
                    'href="assets/app.css?v=4"' in html
                    and 'src="assets/app.js?v=4"' in html)
        audit.check("首页展示政策库更新账本",
                    'id="updatesList"' in html
                    and "更新有审核闸门" in html)
        audit.check("首页声明文件与 HTTP 双编码",
                    '<meta charset="utf-8">' in html
                    and 'http-equiv="Content-Type"' in html
                    and "charset=utf-8" in headers.get("Content-Type", ""))
        status, _, _, css = request(port, "GET", "/assets/app.css")
        audit.check("样式资源可访问", status == 200 and len(css) > 1000)
        status, _, _, js = request(port, "GET", "/assets/app.js")
        audit.check("脚本资源可访问", status == 200 and len(js) > 1000)

        print("== 健康检查 ==")
        status, _, health, _ = request(port, "GET", "/api/health")
        audit.check("健康接口返回 JSON", status == 200 and isinstance(health, dict))
        audit.check("健康接口报告语料",
                    health.get("database", {}).get("corpus_files", 0) > 0)
        audit.check("健康接口不返回密钥",
                    "api_key" not in health.get("provider", {}))
        audit.check("健康接口报告更新积压",
                    isinstance(health.get("updates"), dict)
                    and "pending" in health["updates"])
        status, _, updates, _ = request(port, "GET", "/api/updates")
        serialized_updates = json.dumps(updates, ensure_ascii=False)
        audit.check("更新接口返回公开摘要",
                    status == 200 and isinstance(updates.get("items"), list))
        audit.check("更新接口不暴露本地路径",
                    "body_file" not in serialized_updates
                    and "数据库" not in serialized_updates)

        print("== 问答任务 ==")
        status, _, created, _ = request(
            port, "POST", "/api/ask",
            {"question": "测试政策第三条是什么？", "as_of": "2023-12-31"})
        audit.check("提交问题返回 202",
                    status == 202 and bool(created.get("id")))
        job_id = created.get("id")
        job = None
        for _ in range(50):
            status, _, job, _ = request(port, "GET", f"/api/ask/{job_id}")
            if job.get("status") in {"completed", "failed"}:
                break
            time.sleep(0.03)
        result = (job or {}).get("result") or {}
        audit.check("异步任务完成",
                    status == 200 and job.get("status") == "completed")
        audit.check("回答与引用被保留",
                    result.get("answer") == "答复正文。"
                    and len(result.get("sources", [])) == 1)
        audit.check("效力摘要被保留",
                    result.get("force_summary", [{}])[0].get("status")
                    == "现行有效")
        audit.check("内部目录不暴露",
                    "outline" not in result
                    and "不应出现在浏览器响应中" not in json.dumps(
                        result, ensure_ascii=False))
        audit.check("历史时点被结构化",
                    result.get("as_of", {}).get("as_of") == "2023-12-31")

        print("== 输入与错误处理 ==")
        status, _, invalid, _ = request(
            port, "POST", "/api/ask",
            {"question": "测试", "as_of": "不是日期"})
        audit.check("无效时点返回 400",
                    status == 400 and "无法识别" in invalid.get("error", ""))
        status, _, missing, _ = request(
            port, "POST", "/api/ask", {"question": "  "})
        audit.check("空问题返回 400", status == 400 and bool(missing.get("error")))
        status, _, _, _ = request(
            port, "POST", "/api/ask", b"{not-json",
            {"Content-Type": "application/json"})
        audit.check("无效 JSON 返回 400", status == 400)
        status, _, _, _ = request(
            port, "POST", "/api/ask", b'{"question":"test"}',
            {"Content-Type": "text/plain"})
        audit.check("错误 Content-Type 返回 415", status == 415)
        status, _, _, _ = request(
            port, "POST", "/api/ask", b"x" * (service.MAX_BODY_BYTES + 1),
            {"Content-Type": "application/json"})
        audit.check("超大请求返回 413", status == 413)
        with update_workflow.publish_lock(reasoning_retrieval.DEFAULT_DB):
            status, _, blocked, _ = request(
                port, "POST", "/api/ask", {"question": "发布期间查询"})
        audit.check("发布期间拒绝新查询",
                    status == 503
                    and "发布更新" in blocked.get("error", ""))
        status, _, _, _ = request(
            port, "GET", "/api/ask/00000000000000000000000000000000")
        audit.check("未知任务返回 404", status == 404)
        status, _, _, _ = request(port, "GET", "/api/not-found")
        audit.check("未知接口返回 404", status == 404)
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=2)

    total = audit.passed + audit.failed
    print(f"\nservice audit: {audit.passed}/{total}")
    raise SystemExit(0 if audit.failed == 0 else 1)


if __name__ == "__main__":
    main()
