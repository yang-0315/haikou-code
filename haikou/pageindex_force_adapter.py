"""Put the force dimension into PageIndex's own tool responses.

The tool layer reads a document through three client methods: `get_tree` for
the outline, `get_ocr(format="page")` for page text, and `get_document` for
metadata. Wrapping those three is enough for every tool result the agent sees
to carry the document's legal-force status — so the model reasons with it
instead of the system filtering afterwards.

    from pageindex import PageIndexClient
    from pageindex_force_adapter import ForceAwareClient, force_instructions

    client = ForceAwareClient(PageIndexClient(index=..., chat=...), DATABASE)
    tools = client.as_openai_tools()      # tool results now carry force
    instructions = force_instructions()   # append to the agent's system prompt

Self-test with a fake client:

    python pageindex_force_adapter.py
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import temporal
from force_layer import ForceLayer
from force_protocol import agent_instructions
from warn import own_numbers

from paths import DEFAULT_DATABASE as DEFAULT_DB
HERE = os.path.dirname(os.path.abspath(__file__))
PI_REPO = r"C:\Users\Lenovo\Desktop\PageIndex\PageIndex"
PI_STORE = os.path.join(HERE, "_pi_store")
PI_MANIFEST = os.path.join(HERE, "_pi_manifest.json")

FORCE_DOC = {
    "status": str,
    "confidence": str,
    "reason": {"code": str, "text": str},
    "checked": dict,
    "missing": list,
    "next_steps": list,
    "evidence": dict,
    "caveats": list,
}


def force_instructions(as_of=None) -> str:
    """Text to append to an agent's system prompt.

    内容来自 `force_protocol`——协议只有一份源，这里只是换个名字对外
    （老调用方按这个名字用）。
    """
    point = temporal.parse_as_of(as_of) if as_of else None
    return agent_instructions(point)


class ForceAwareClient:
    """Wraps a PageIndex client so its tool responses carry legal force."""

    def __init__(self, client, database: str = DEFAULT_DB, as_of=None):
        self._client = client
        self._layer = ForceLayer(database)
        self.as_of = temporal.parse_as_of(as_of) if as_of else None
        self._titles: dict[str, str] = {}
        self._status: dict[tuple[str, str], dict] = {}
        self._keys: dict[str, str] = {}

    # everything not overridden passes straight through
    def __getattr__(self, name):
        return getattr(self._client, name)

    def _title(self, doc_id: str) -> str:
        if doc_id not in self._titles:
            name = ""
            try:
                info = self._client.get_document(doc_id)
                name = (info or {}).get("name") or (info or {}).get("title") or ""
            except Exception:
                pass
            # 客户端给的是文件名（"…办法.pdf"），效力层按标题查库，后缀必须去掉，
            # 否则查不到正文，存疑规则拿不到文头年份，会误报成"现行有效"。
            self._titles[doc_id] = os.path.splitext(name)[0]
        return self._titles[doc_id]

    def _force(self, doc_id: str) -> dict:
        key = (doc_id, temporal.period_key(self.as_of) if self.as_of else "")
        if key not in self._status:
            # 主键优先用文号：标题会被"（试行）"、文件名截断这类事搅乱，文号不会。
            self._status[key] = self._layer.status_for(
                self._title(doc_id), self._numbers(doc_id), self._head(doc_id),
                self.as_of, file=doc_id)
        return self._status[key]

    def client_text(self, doc_id: str) -> str:
        """永远读客户端那份正文（PDF）。核对主键、比对出处时用它。"""
        try:
            payload = self._client.get_ocr(doc_id, format="raw")
        except Exception:
            return ""
        # get_ocr 的 result 随 format 变：raw 是字符串，page/node 是列表
        text = payload.get("result") if isinstance(payload, dict) else payload
        if isinstance(text, list):
            text = "\n".join(str(page.get("text", "")) for page in text)
        return text or ""

    def _head(self, doc_id: str) -> str:
        """判定用的正文开头。

        优先用库内那份：客户端的正文可能是残缺副本或占位文本（假客户端尤其
        如此），拿它判"效力存疑"会因为读不到文头年份而失准。库内没有这份
        文件时才退回客户端正文。客户端正文与库内是否同一份文档，由
        `document_key()` 的文号核对兜着。
        """
        known = self._layer.text_of(self._title(doc_id))
        return known[:400] if known else self.client_text(doc_id)[:400]

    def _numbers(self, doc_id: str) -> list[str]:
        """文档自己的文号——从正文里读，不靠文件名推。"""
        if doc_id not in self._keys:
            found = own_numbers(self._head(doc_id))
            self._keys[doc_id] = found[0] if found else ""
        return [self._keys[doc_id]] if self._keys[doc_id] else []

    def document_key(self, doc_id: str) -> str:
        """从**客户端正文**读出的文号——核对 manifest 键与 PDF 是否同一份。"""
        found = own_numbers(self.client_text(doc_id)[:400])
        return found[0] if found else self._title(doc_id)

    # ── the three seams ──
    def get_tree(self, doc_id: str, node_summary: bool = False,
                 include_text: bool = True) -> dict:
        result = self._client.get_tree(doc_id, node_summary=node_summary,
                                       include_text=include_text)
        status = self._force(doc_id)
        nodes = result.get("result")
        if isinstance(nodes, list):
            result["result"] = self._layer.annotate(nodes,
                                                   self._title(doc_id), status,
                                                   file=doc_id,
                                                   as_of=self.as_of)
        result["force"] = status
        return result

    def get_ocr(self, doc_id: str, format: str = "page") -> dict:
        result = self._client.get_ocr(doc_id, format=format)
        status = self._force(doc_id)
        pages = result.get("result")
        if isinstance(pages, list):
            result["result"] = self._layer.annotate(
                pages, self._title(doc_id), status, file=doc_id,
                as_of=self.as_of)
        result["force"] = status
        return result

    def get_document(self, doc_id: str) -> dict:
        info = self._client.get_document(doc_id)
        if isinstance(info, dict):
            info = dict(info, force=self._force(doc_id))
        return info

    def as_openai_tools(self, **kwargs):
        """The client's own tool set — its results now carry force."""
        return self._client.as_openai_tools(**kwargs)


class _FakeClient:
    """Stands in for a PageIndex client so the wiring can be tested offline."""

    def __init__(self, name: str):
        self.name = name
        self.calls = []

    def get_document(self, doc_id):
        return {"id": doc_id, "name": self.name, "status": "completed"}

    def get_tree(self, doc_id, node_summary=False, include_text=True):
        return {"doc_id": doc_id, "status": "completed",
                "retrieval_ready": True,
                "result": [{"title": "第一章 总则", "node_id": "0000",
                            "page_index": 1,
                            "nodes": [{"title": "第一条 为规范…",
                                       "node_id": "0001", "page_index": 1}]}]}

    def get_ocr(self, doc_id, format="page"):
        return {"doc_id": doc_id,
                "result": [{"page": 1, "text": "第一条 为规范…"}]}

    def as_openai_tools(self, **kwargs):
        return [{"type": "function",
                 "function": {"name": "get_document_structure"}}]


def _selftest(database: str):
    cases = ["海南自由贸易港建设投资基金管理办法",
             "海南省关于在琼台湾同胞待遇的若干规定",
             "海南自由贸易港商事调解组织登记管理办法"]
    print("用三个已知效力的文档验证注入（假客户端）：\n")
    for name in cases:
        client = ForceAwareClient(_FakeClient(name), database)
        tree = client.get_tree("doc-1")
        ocr = client.get_ocr("doc-1")
        node = tree["result"][0]
        page = ocr["result"][0]
        print(f"◆ {name[:38]}")
        print(f"   文档级 status: {tree['force']['status']}"
              f"（置信度 {tree['force']['confidence']}）")
        print(f"   节点 force.status: {node['force']['status']}"
              f"　页面 force.status: {page['force']['status']}")
        print(f"   原因: {tree['force']['reason']['text'][:60]}")
        if tree["force"]["next_steps"]:
            print(f"   下一步: {tree['force']['next_steps'][0]}")
        print()


def _real_client(database: str, store_path: str):
    """真实 PageIndexLocalClient 包上效力层。需要 pageindex 可 import。"""
    if PI_REPO not in sys.path:
        sys.path.insert(0, PI_REPO)
    from pageindex import PageIndexLocalClient
    return ForceAwareClient(PageIndexLocalClient(storage_path=store_path), database)


def _missing_deps() -> str | None:
    """真实客户端要的库在这个解释器里齐不齐。"""
    try:
        import PyPDF2  # noqa: F401
        import litellm  # noqa: F401
    except ImportError as exc:
        return str(exc)
    return None


def _venv_python() -> str | None:
    """能跑 pageindex 的解释器（项目自带 venv）——先探一下再决定换不换。"""
    venv = os.path.join(PI_REPO, ".venv", "Scripts", "python.exe")
    if not os.path.exists(venv):
        return None
    if os.path.abspath(sys.executable) == os.path.abspath(venv):
        return None
    if os.environ.get("PI_FORCE_REEXEC"):
        return None
    try:
        probe = subprocess.run([venv, "-c", "import PyPDF2, litellm"],
                               capture_output=True, timeout=180,
                               encoding="utf-8", errors="replace")
    except Exception:
        return None
    return venv if probe.returncode == 0 else None


def rerun_with_venv() -> int | None:
    """当前解释器缺 pageindex 的依赖时，用项目 venv 重跑自己。

    用子进程而不是 execve：沙箱下 execve 会静默失败，子进程的报错反而看得见。
    子进程直接继承标准输出，所以用户看到的还是同一份结果。
    """
    venv = _venv_python()
    if not venv:
        return None
    env = dict(os.environ, PI_FORCE_REEXEC="1")
    proc = subprocess.run([venv, os.path.abspath(__file__)] + sys.argv[1:],
                          env=env)
    return proc.returncode


def _walk_nodes(nodes):
    for node in nodes or []:
        yield node
        yield from _walk_nodes(node.get("nodes") or [])


def real_selftest(database: str = DEFAULT_DB, store_path: str = PI_STORE,
                  verbose: bool = True) -> tuple[bool, str]:
    """用真实 PageIndexClient 的索引验 get_tree 返回的节点确实带 force。

    这是"可移植证明"从接线变成实证的那一步：索引是上游自己建的（PDF 入库、
    节点由上游布局分析产出），这里只包住它的三个读文档方法，看 force 有没有
    落到每一个节点上、状态对不对。

    当前解释器缺 PyPDF2／litellm 时，自动用项目 venv 跑同一段检查并把结果带回来，
    所以从哪个解释器调用都能拿到真实结论。
    """
    cache_key = (database, store_path)
    if cache_key in _REAL_CACHE:
        ok, detail, stats = _REAL_CACHE[cache_key]
        if verbose:
            _print_real_detail(detail, stats)
        return ok, detail
    missing = _missing_deps()
    if missing:
        venv = _venv_python()
        if venv is None:
            return False, (f"缺 PyPDF2／litellm（{missing}），也没有可用的项目 venv"
                           "：跳过真实客户端")
        proc = subprocess.run(
            [venv, os.path.abspath(__file__), "--real-json",
             "--database", database, "--store", store_path],
            capture_output=True, timeout=900,
            encoding="utf-8", errors="replace")
        for line in reversed((proc.stdout or "").splitlines()):
            if line.startswith("{"):
                data = json.loads(line)
                _REAL_CACHE[cache_key] = (data.get("ok", False),
                                          data.get("detail", ""),
                                          data.get("stats", {}))
                return data.get("ok", False), data.get("detail", "")
        return False, (f"venv 子进程没给出结果（退出码 {proc.returncode}）："
                       + (proc.stderr or "")[:200])
    try:
        # manifest 在 haikou/ 下（键为文号），里面的 storage 指向索引目录
        manifest_path = PI_MANIFEST if os.path.exists(PI_MANIFEST) \
            else os.path.join(store_path, "samples.json")
        if not os.path.exists(manifest_path):
            return False, (f"缺索引：{store_path}（先跑 "
                           "haikou/build_pi_store.py --build）")
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        store_path = manifest.get("storage") or store_path
        client = _real_client(database, store_path)
    except ImportError as exc:
        return False, f"pageindex 不可 import（{exc}）：跳过真实客户端"

    problems, stats = [], {}
    if verbose:
        print(f"用真实 PageIndexClient 验证注入（索引 {store_path}）：\n")
    for number, meta in manifest["documents"].items():
        title = meta.get("title", number)
        # 预期值来自 manifest 里的 force 记录（由 ForceLayer 现算），
        # 所以这里验的是"客户端路径复现了力层判定"，不是对照一份写死的字符串
        expected = (meta.get("force") or {}).get("status") \
            or meta.get("expected")
        doc_id = meta["doc_id"]
        tree = client.get_tree(doc_id)
        nodes = list(_walk_nodes(tree.get("result")))
        nested = [n for n in nodes if n.get("nodes")]
        depth = _depth(tree.get("result") or [])
        ocr = client.get_ocr(doc_id, format="page")
        info = client.get_document(doc_id)
        bare = [n["node_id"] for n in nodes if not isinstance(n.get("force"), dict)]
        bare_nested = [n["node_id"] for n in nested
                       if not isinstance(n.get("force"), dict)]
        statuses = {n["force"]["status"] for n in nodes if n.get("force")}
        page_bare = [p.get("page") for p in (ocr.get("result") or [])
                     if not isinstance(p.get("force"), dict)]
        verdict = tree.get("force", {}).get("status")
        key_from_text = client.document_key(doc_id)
        stats[number] = {"title": title, "nodes": len(nodes),
                         "nested": len(nested), "depth": depth,
                         "bare": len(bare), "bare_nested": len(bare_nested),
                         "expected": expected, "verdict": verdict}
        if verbose:
            print(f"◆ 《{title}》")
            print(f"   文号（主键）：manifest {number}"
                  f"／从文档正文读到 {key_from_text}")
            print(f"   上游树：{len(nodes)} 个节点"
                  f"（顶层 {len(tree.get('result') or [])}），"
                  f"含子节点的 {len(nested)}，最深 {depth} 层，"
                  f"节点带 force {len(nodes) - len(bare)}/{len(nodes)}")
            print(f"   文档级 force：{verdict}（预期 {expected}）"
                  f"　页面 {len(ocr.get('result') or [])} 页，"
                  f"未带 force {len(page_bare)}")
            print(f"   元数据 force：{info.get('force', {}).get('status')}")
            print()
        if key_from_text != number:
            problems.append(f"{title}：正文读到的文号 {key_from_text!r} "
                            f"≠ manifest 键 {number!r}")
        if bare:
            problems.append(f"{title}：{len(bare)} 个节点没有 force")
        if bare_nested:
            problems.append(f"{title}：{len(bare_nested)} 个嵌套节点没有 force")
        if statuses and statuses != {expected}:
            problems.append(f"{title}：节点状态 {statuses} ≠ 预期 {expected}")
        if verdict != expected:
            problems.append(f"{title}：文档级 {verdict} ≠ 预期 {expected}")
        if page_bare:
            problems.append(f"{title}：{len(page_bare)} 页没有 force")
    if problems:
        _REAL_CACHE[cache_key] = (False, "；".join(problems), stats)
        return False, "；".join(problems)
    nested_total = sum(s["nested"] for s in stats.values())
    detail = (f"{len(stats)} 份文档（按文号索引）：树节点、页面、元数据三处都带"
              f"force，且与预期效力一致；其中 {nested_total} 个节点含子节点"
              f"（最深 {max((s['depth'] for s in stats.values()), default=1)} 层）")
    _REAL_CACHE[cache_key] = (True, detail, stats)
    return True, detail


_REAL_CACHE: dict = {}


def real_stats(database: str = DEFAULT_DB,
               store_path: str = PI_STORE) -> dict:
    """真实验证的逐文档统计（供审计项用）。"""
    real_selftest(database, store_path, verbose=False)
    return _REAL_CACHE.get((database, store_path), (None, None, {}))[2] or {}


def _depth(nodes: list[dict], level: int = 1) -> int:
    best = 0
    for node in nodes or []:
        children = node.get("nodes") or []
        best = max(best, level if not children else _depth(children, level + 1))
    return best


def _print_real_detail(detail: str, stats: dict) -> None:
    for number, item in (stats or {}).items():
        print(f"◆ 《{item['title']}》（{number}）")
        print(f"   节点 {item['nodes']}，含子节点 {item['nested']}，"
              f"最深 {item['depth']} 层，未带 force {item['bare']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--instructions", action="store_true",
                        help="打印要追加到系统提示的效力说明")
    parser.add_argument("--as-of",
                        help="把工具返回的效力换算成该时点状态")
    parser.add_argument("--real", action="store_true",
                        help="只跑真实 PageIndexClient 的验证（需要 pageindex 与索引）")
    parser.add_argument("--store", default=PI_STORE,
                        help="真实索引所在目录（默认 haikou/_pi_store）")
    parser.add_argument("--real-json", action="store_true",
                        help="只输出 JSON 结果（供其他脚本调用）")
    args = parser.parse_args()
    if args.instructions:
        print(force_instructions(args.as_of))
        return

    if args.real_json:
        ok, detail = real_selftest(args.database, args.store, verbose=False)
        print(json.dumps({"ok": ok, "detail": detail,
                          "stats": real_stats(args.database, args.store)},
                         ensure_ascii=False))
        sys.exit(0 if ok else 1)

    if args.real and _missing_deps():
        # 缺 PyPDF2/litellm 的解释器跑不了真实客户端；项目 venv 里有，换过去重跑
        code = rerun_with_venv()
        if code is not None:
            sys.exit(code)

    ok, detail = real_selftest(args.database, args.store)
    if ok:
        print(f"真实客户端验证：通过——{detail}")
    else:
        print(f"真实客户端验证：{'未通过' if '跳过' not in detail else '跳过'}"
              f"——{detail}")
        if args.real:
            sys.exit(1)
    _selftest(args.database)


if __name__ == "__main__":
    main()
