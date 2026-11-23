"""User-facing HTTP service for force-aware policy question answering.

The service deliberately uses only the Python standard library.  It exposes a
small same-origin web application and an asynchronous JSON API around the
established ``reasoning_retrieval.ask()`` pipeline.

    python service.py
    python service.py --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_provider
import reasoning_retrieval
import temporal
import update_workflow

SERVICE_NAME = "琼策"
SERVICE_VERSION = "1.0.0"
WEB_ROOT = os.path.join(HERE, "web")
MAX_BODY_BYTES = 16 * 1024
MAX_QUESTION_CHARS = 800
MAX_AS_OF_CHARS = 32
MAX_PUBLIC_SOURCES = 20
DEFAULT_JOB_TTL = 30 * 60

LOGGER = logging.getLogger("haikou.service")

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("text") or value.get("message") or ""
    return str(value)[:limit]


def _string_list(value, limit: int = 8) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item, 600) for item in value[:limit] if _text(item, 600)]


def _public_as_of(point) -> dict | None:
    if not isinstance(point, dict):
        return None
    keys = ("as_of", "label", "source", "precision", "start", "end",
            "exact_day", "period_clipped_at_today")
    return {key: point.get(key) for key in keys if key in point}


def _public_route(route) -> dict | None:
    if not isinstance(route, dict):
        return None
    return {
        "strategy": _text(route.get("strategy"), 80),
        "path": _text(route.get("path"), 40),
        "docs_wanted": route.get("docs_wanted"),
        "refuse": bool(route.get("refuse")),
        "reason": _text(route.get("reason"), 800),
        "caveats": _string_list(route.get("caveats"), 6),
        "signals": route.get("signals")
        if isinstance(route.get("signals"), dict) else {},
    }


def public_result(result: dict) -> dict:
    """Reduce the internal retrieval result to a stable browser-facing shape.

    In particular, omit the model outline and full candidate force records.
    Only evidence that was actually cited is returned to the user.
    """
    result = result if isinstance(result, dict) else {}
    sources: list[dict] = []
    seen_sources: set[tuple[str, str]] = set()
    for item in result.get("sources") or []:
        if not isinstance(item, dict):
            continue
        source = {
            "doc": _text(item.get("doc"), 300),
            "article": _text(item.get("article"), 80),
            "status": _text(item.get("status"), 40),
            "doc_number": _text(item.get("doc_number"), 120),
            "reason": _text(item.get("reason"), 1200),
            "caveats": _string_list(item.get("caveats"), 6),
            "evidence": item.get("evidence")
            if isinstance(item.get("evidence"), dict) else {},
        }
        key = (source["doc"], source["article"])
        if key in seen_sources:
            continue
        seen_sources.add(key)
        sources.append(source)
        if len(sources) >= MAX_PUBLIC_SOURCES:
            break

    forces = result.get("forces")
    forces = forces if isinstance(forces, dict) else {}
    cited_docs: list[str] = []
    for source in sources:
        if source["doc"] and source["doc"] not in cited_docs:
            cited_docs.append(source["doc"])

    force_summary: list[dict] = []
    for doc in cited_docs:
        force = forces.get(doc)
        force = force if isinstance(force, dict) else {}
        reason = force.get("reason")
        evidence = force.get("evidence")
        force_summary.append({
            "doc": doc,
            "status": _text(force.get("status"), 40),
            "confidence": _text(force.get("confidence"), 40),
            "reason": _text(reason, 1200),
            "evidence": evidence if isinstance(evidence, dict) else {},
            "missing": _string_list(force.get("missing"), 6),
            "next_steps": _string_list(force.get("next_steps"), 6),
            "caveats": _string_list(force.get("caveats"), 6),
        })

    answer = _text(result.get("answer"), 50000)
    refusal = _text(result.get("refusal"), 5000)
    return {
        "query": _text(result.get("query"), MAX_QUESTION_CHARS),
        "answer": answer,
        "refusal": refusal,
        "refusal_stage": _text(result.get("refusal_stage"), 80),
        "sources": sources,
        "force_summary": force_summary,
        "notes": _string_list(result.get("notes"), 12),
        "reason": _text(result.get("reason"), 2000),
        "force_note": _text(result.get("force_note"), 2000),
        "route": _public_route(result.get("route")),
        "as_of": _public_as_of(result.get("as_of")),
        "has_answer": bool(answer.strip()),
    }


def provider_status() -> dict:
    try:
        config = llm_provider.config_from_env()
        return {
            "configured": True,
            "name": config.name,
            "model": config.model,
            "timeout": config.timeout,
        }
    except (ValueError, OSError) as exc:
        return {
            "configured": False,
            "name": "",
            "model": "",
            "timeout": None,
            "error": str(exc)[:240],
        }


def count_corpus_files(database: str) -> int:
    total = 0
    try:
        for _, _, files in os.walk(os.path.join(database, "library")):
            total += sum(1 for name in files if name.lower().endswith(".txt"))
        if os.path.isdir(database):
            total += sum(
                1 for name in os.listdir(database)
                if name.lower().endswith(".txt")
                and os.path.isfile(os.path.join(database, name))
            )
    except OSError:
        return 0
    return total


class AskRunner:
    """In-memory asynchronous job runner for long model calls."""

    def __init__(self, database: str, ask_fn=None, max_workers: int = 1,
                 job_ttl: int = DEFAULT_JOB_TTL):
        self.database = database
        self.ask_fn = ask_fn or reasoning_retrieval.ask
        self.job_ttl = max(60, int(job_ttl))
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="policy-ask",
        )
        self._closed = False

    def submit(self, question: str, as_of: str = "") -> dict:
        point = temporal.parse_as_of(as_of) if as_of else None
        job_id = uuid.uuid4().hex
        record = {
            "id": job_id,
            "question": question,
            "as_of": point,
            "status": "queued",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("服务正在关闭")
            if update_workflow.is_publish_in_progress(self.database):
                raise RuntimeError("政策库正在发布更新，请稍后重新提交")
            self._prune_locked()
            self._jobs[job_id] = record
            self._executor.submit(self._run, job_id)
            return self._public_job_locked(record)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            self._prune_locked()
            record = self._jobs.get(job_id)
            return self._public_job_locked(record) if record else None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return
            record["status"] = "running"
            record["started_at"] = utc_now()
            question = record["question"]
            point = record["as_of"]
        started = time.monotonic()
        try:
            result = self.ask_fn(
                question,
                database=self.database,
                as_of=point,
            )
            clean = public_result(result)
            with self._lock:
                record = self._jobs.get(job_id)
                if not record:
                    return
                record["status"] = "completed"
                record["result"] = clean
                record["duration_ms"] = round(
                    (time.monotonic() - started) * 1000)
                record["finished_at"] = utc_now()
        except Exception:  # noqa: BLE001
            LOGGER.exception("ask job %s failed", job_id)
            with self._lock:
                record = self._jobs.get(job_id)
                if not record:
                    return
                record["status"] = "failed"
                record["error"] = "服务处理失败，请稍后重试。"
                record["duration_ms"] = round(
                    (time.monotonic() - started) * 1000)
                record["finished_at"] = utc_now()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.job_ttl
        expired = []
        for job_id, record in self._jobs.items():
            finished = record.get("finished_at")
            if not finished:
                continue
            try:
                stamp = datetime.fromisoformat(finished).timestamp()
            except ValueError:
                continue
            if stamp < cutoff:
                expired.append(job_id)
        for job_id in expired:
            self._jobs.pop(job_id, None)

    def _public_job_locked(self, record: dict) -> dict:
        payload = {
            "id": record["id"],
            "status": record["status"],
            "created_at": record["created_at"],
            "started_at": record["started_at"],
            "finished_at": record["finished_at"],
            "duration_ms": record.get("duration_ms"),
            "error": record.get("error"),
        }
        if record["status"] == "queued":
            payload["queue_ahead"] = sum(
                1 for item in self._jobs.values()
                if item["status"] == "queued"
                and item["created_at"] < record["created_at"]
            )
        if record.get("result") is not None:
            payload["result"] = record["result"]
        return payload


class ServiceApplication:
    def __init__(self, database: str, ask_fn=None, max_workers: int = 1):
        self.database = os.path.abspath(database)
        self.runner = AskRunner(
            self.database, ask_fn=ask_fn, max_workers=max_workers)
        self.started_at = utc_now()
        self.corpus_files = count_corpus_files(self.database)

    def health(self) -> dict:
        database_ready = os.path.isdir(self.database)
        index_path = os.path.join(self.database, "_retrieval_index.pkl")
        index_ready = os.path.isfile(index_path)
        provider = provider_status()
        updates = update_workflow.public_update_summary(self.database, limit=0)
        status = "ok"
        if not database_ready or not provider["configured"]:
            status = "degraded"
        return {
            "status": status,
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "started_at": self.started_at,
            "database": {
                "ready": database_ready,
                "corpus_files": self.corpus_files,
                "retrieval_index": index_ready,
            },
            "provider": provider,
            "updates": {
                key: updates.get(key) for key in (
                    "available", "pending", "verified", "published",
                    "message")
            },
        }

    def close(self) -> None:
        self.runner.close()


class PolicyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: ServiceApplication):
        super().__init__(address, PolicyRequestHandler)
        self.app = app


class PolicyRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Policy-Service/1.0.0"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            self._serve_static(path)
            return
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, self.server.app.health())
            return
        if path == "/api/updates":
            self._send_json(
                HTTPStatus.OK,
                update_workflow.public_update_summary(
                    self.server.app.database),
            )
            return
        match = re.fullmatch(r"/api/ask/([0-9a-f]{32})", path)
        if match:
            job = self.server.app.runner.get(match.group(1))
            if job is None:
                self._send_error(HTTPStatus.NOT_FOUND,
                                 "未找到该问答任务，任务可能已过期。")
            else:
                self._send_json(HTTPStatus.OK, job)
            return
        if path.startswith("/api/"):
            self._send_error(HTTPStatus.NOT_FOUND, "接口不存在。")
            return
        self._send_error(HTTPStatus.NOT_FOUND, "页面不存在。")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path != "/api/ask":
            self._send_error(HTTPStatus.NOT_FOUND, "接口不存在。")
            return

        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                             "请求必须使用 application/json。")
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._send_error(HTTPStatus.LENGTH_REQUIRED,
                             "缺少有效的 Content-Length。")
            return
        if length <= 0:
            self._send_error(HTTPStatus.BAD_REQUEST, "请求体不能为空。")
            return
        if length > MAX_BODY_BYTES:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                             "请求体过大。")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error(HTTPStatus.BAD_REQUEST,
                             "请求体不是有效的 UTF-8 JSON。")
            return
        if not isinstance(payload, dict):
            self._send_error(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象。")
            return

        question = payload.get("question", "")
        if not isinstance(question, str):
            self._send_error(HTTPStatus.BAD_REQUEST, "question 必须是字符串。")
            return
        question = question.strip()
        if not question:
            self._send_error(HTTPStatus.BAD_REQUEST, "请输入政策问题。")
            return
        if len(question) > MAX_QUESTION_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                f"问题不能超过 {MAX_QUESTION_CHARS} 个字符。")
            return

        as_of = payload.get("as_of", "")
        if not isinstance(as_of, str):
            self._send_error(HTTPStatus.BAD_REQUEST, "as_of 必须是字符串。")
            return
        as_of = as_of.strip()
        if len(as_of) > MAX_AS_OF_CHARS:
            self._send_error(HTTPStatus.BAD_REQUEST, "时点文本过长。")
            return
        if as_of:
            try:
                temporal.parse_as_of(as_of)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return

        try:
            job = self.server.app.runner.submit(question, as_of)
        except RuntimeError as exc:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        self._send_json(HTTPStatus.ACCEPTED, job)

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            asset_name, content_type = STATIC_FILES[path]
            try:
                size = os.path.getsize(os.path.join(WEB_ROOT, asset_name))
            except OSError:
                self._send_error(HTTPStatus.NOT_FOUND, "页面不存在。")
                return
            self._send_headers(HTTPStatus.OK, content_type, size,
                               "no-cache" if content_type.startswith("text/html")
                               else "public, max-age=3600")
            return
        if path in {"/api/health", "/api/updates"}:
            self._send_headers(HTTPStatus.OK,
                               "application/json; charset=utf-8", 0)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "资源不存在。")

    def _serve_static(self, path: str) -> None:
        asset_name, content_type = STATIC_FILES[path]
        asset_path = os.path.join(WEB_ROOT, asset_name)
        try:
            data = open(asset_path, "rb").read()
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND, "页面资源不存在。")
            return
        cache = "no-cache" if content_type.startswith("text/html") \
            else "public, max-age=3600"
        self._send_bytes(HTTPStatus.OK, data, content_type, cache)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8")

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_bytes(self, status: HTTPStatus, data: bytes,
                    content_type: str, cache: str = "no-store") -> None:
        self._send_headers(status, content_type, len(data), cache)
        self.wfile.write(data)

    def _send_headers(self, status: HTTPStatus, content_type: str,
                      length: int, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Connection", "close")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)


def _loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(description="海南政策效力感知问答服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--database", default=reasoning_retrieval.DEFAULT_DB)
    parser.add_argument("--workers", type=int, default=1,
                        help="同时执行的问答数；默认 1，避免首次建索引竞争")
    parser.add_argument("--check", action="store_true",
                        help="检查语料与模型配置后退出")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    app = ServiceApplication(
        args.database, max_workers=max(1, args.workers))
    if args.check:
        print(json.dumps(app.health(), ensure_ascii=False, indent=2))
        app.close()
        return

    if not os.path.isdir(app.database):
        raise SystemExit(f"数据库目录不存在：{app.database}")
    if not _loopback(args.host):
        LOGGER.warning("服务监听非本机地址 %s；当前入口没有身份认证，"
                       "请仅在受控网络中使用", args.host)

    server = PolicyHTTPServer((args.host, args.port), app)
    LOGGER.info("服务已启动：http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
