"""守住 spine：协议只有一份源，两条后端不许各说各话。

`force_protocol.py` 是引擎无关的效力推理协议，`reasoning_retrieval.py`（条文
语料推理回路）与 `pageindex_force_adapter.py`（PageIndex 工具循环）都从它取
规则。这四项检查都是离线可跑的：

  1. 协议自身自洽——三种渲染的每条规则都落在同一份 POLICY 里
  2. 两条后端确实在用协议，而不是各自留了一份
  3. 依赖方向单向——协议不依赖任何后端
  4. 同一个效力层——同一份文档经两条后端得到同一状态

    python spine_check.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import force_protocol as fp
from force_layer import ForceLayer

from paths import DEFAULT_DATABASE as DEFAULT_DB


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    label = "通过" if ok else ("跳过" if ok is None else "问题")
    print(f"{label}  {title}")
    if detail:
        print(f"        {detail}")
    return ok, detail


def check_single_source():
    """每条规则在一处定义，三种渲染各自取用，不许漏。"""
    problems = []
    for order, field in ((fp.SELECT_ORDER, "select"),
                         (fp.ANSWER_ORDER, "answer"),
                         (fp.AGENT_ORDER, "agent")):
        for rule_id in order:
            if rule_id not in fp.POLICY:
                problems.append(f"{rule_id} 不在 POLICY 里")
            elif field not in fp.POLICY[rule_id]:
                problems.append(f"{rule_id} 缺 {field} 渲染")
    # 反过来也要查：定义了却谁都不用，等于规则文档和实际提示词脱节
    for rule_id, renderings in fp.POLICY.items():
        if not any(rule_id in order for order in
                   (fp.SELECT_ORDER, fp.ANSWER_ORDER, fp.AGENT_ORDER)):
            problems.append(f"{rule_id} 定义了但没有任何渲染用它")
        if not renderings:
            problems.append(f"{rule_id} 没有任何渲染文字")
    rendered = len(fp.select_rules()) - 1, len(fp.answer_rules()) - 1
    if rendered != (len(fp.SELECT_ORDER), len(fp.ANSWER_ORDER)):
        problems.append(f"渲染条数对不上：{rendered}")
    return not problems, ("规则 " + str(len(fp.POLICY)) +
                          f" 条，选条 {len(fp.SELECT_ORDER)} 条、"
                          f"作答 {len(fp.ANSWER_ORDER)} 条、"
                          f"agent {len(fp.AGENT_ORDER)} 条；"
                          + ("；".join(problems) if problems else "无缺口"))


def check_backends_use_protocol():
    """两条后端取到的是同一份协议，且协议先于后端可用。"""
    import pageindex_force_adapter as adapter
    import reasoning_retrieval as rr
    problems = []
    if adapter.force_instructions() != fp.agent_instructions():
        problems.append("adapter 的 agent 提示与协议不一致")
    # 后端模块里不该再留一份规则文本
    for module, names in ((rr, ("SELECT_RULES", "ANSWER_RULES",
                                "FORCE_MEANING")),
                          (adapter, ("STATUS_MEANING",
                                     "AGENT_STATUS_MEANING"))):
        for name in names:
            if hasattr(module, name):
                problems.append(f"{module.__name__} 里还留着自己的一份 {name}")
    if rr.MAX_BLOCKS != fp.MAX_BLOCKS:
        problems.append("选条上限两处不一致")
    return not problems, ("两条后端均取自 force_protocol；"
                          + ("；".join(problems) if problems else "无副本残留"))


def check_dependency_direction():
    """协议只依赖 warn 与标准库；后端可以被单独替换。"""
    allowed = {"__future__", "argparse", "os", "re", "sys", "warn"}
    tree = ast.parse(open(os.path.join(HERE, "force_protocol.py"),
                          encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    extra = imported - allowed
    return not extra, (f"协议 import：{sorted(imported)}"
                       + (f"；越界 {sorted(extra)}" if extra else "；方向正确"))


def check_same_verdict(database: str):
    """同一份文档，两条后端给出同一状态。"""
    import pageindex_force_adapter as adapter
    cases = ["海南自由贸易港建设投资基金管理办法",
             "海南省关于在琼台湾同胞待遇的若干规定",
             "海南自由贸易港商事调解组织登记管理办法"]
    layer = ForceLayer(database)
    mismatch = []
    for name in cases:
        direct = layer.status_for(name)["status"]
        client = adapter.ForceAwareClient(adapter._FakeClient(name), database)
        via_tree = client.get_tree("doc-1")["force"]["status"]
        if direct != via_tree:
            mismatch.append(f"{name}：{direct} / {via_tree}")
    return not mismatch, (f"{len(cases)} 份文档两条后端判定一致"
                          if not mismatch else "；".join(mismatch))


def check_tree_backend():
    """沿树检索是第三条后端，规则同样只能来自协议。"""
    import tree_retrieval as tr
    problems = []
    prompt = fp.build_node_select_prompt("问题", "目录")
    for _, label, _ in fp.STATUS_MEANING:
        if label not in prompt:
            problems.append(f"树路径的提示里缺状态「{label}」")
    for rule_id in fp.SELECT_ORDER:
        text = fp.POLICY[rule_id].get("select")
        if text and text not in prompt:
            problems.append(f"树路径的提示里缺规则 {rule_id}")
    for name in ("SELECT_RULES", "ANSWER_RULES", "FORCE_MEANING"):
        if hasattr(tr, name):
            problems.append(f"tree_retrieval 里还留着自己的一份 {name}")
    if tr.build_answer_prompt is not fp.build_answer_prompt:
        problems.append("树路径的回答提示不是协议里的那一份")
    return not problems, ("沿树检索用同一份协议（状态含义＋选条规则＋作答提示）"
                          + ("；" + "；".join(problems) if problems else ""))


def check_manifest_uses_layer():
    """真实索引的 manifest 不许另立一份判定。

    manifest 把"带版式 PDF"和"效力记录"按文号缝在一起，但效力记录必须是
    `ForceLayer` 现算的那一份——写死的预期值等于开了第二个真值来源，判定
    一改它就悄悄过期。这里逐条重算比对，并报告三态覆盖是否还成立。
    """
    import json as _json
    import build_pi_store as bps
    if not os.path.exists(bps.MANIFEST):
        return None, "还没有 manifest（先跑 build_pi_store.py --pdf-only）"
    manifest = _json.load(open(bps.MANIFEST, encoding="utf-8"))
    layer = ForceLayer(bps.DEFAULT_DB)
    problems, states = [], []
    for number, entry in manifest["documents"].items():
        from paths import resolve_corpus_path
        source = resolve_corpus_path(entry.get("txt") or "")
        if not source or not os.path.exists(source):
            problems.append(f"{number}：源文件不在，无法复算")
            continue
        live = layer.status_for_file(source)
        text = open(source, encoding="utf-8", errors="ignore").read()
        numbers = bps.own_numbers(text[:400])
        if not numbers or numbers[0] != number:
            problems.append(f"{number}：正文里的文号是 "
                            f"{numbers[0] if numbers else '未识别'}")
        if (entry.get("force") or {}).get("status") != live["status"]:
            problems.append(f"{number}：manifest 记的是 "
                            f"{(entry.get('force') or {}).get('status')}，"
                            f"现算是 {live['status']}")
        for field in ("reason", "evidence", "caveats"):
            if field not in (entry.get("force") or {}):
                problems.append(f"{number}：force 记录缺 {field}")
        states.append(live["status"])
        if entry.get("sampled_for") and entry["sampled_for"] != live["status"]:
            problems.append(f"{number}：选题意图 {entry['sampled_for']} "
                            f"已与实际判定 {live['status']} 不符")
    coverage = "、".join(f"{s}×{states.count(s)}" for s in sorted(set(states)))
    return not problems, (f"{len(manifest['documents'])} 条按文号索引，效力记录与"
                          f" ForceLayer 现算一致；三态覆盖：{coverage}"
                          + ("；" + "；".join(problems) if problems else ""))


def main():
    database = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    results = [
        probe("协议自身自洽（一份源、三种渲染）", check_single_source),
        probe("两条后端都在用协议，没有留副本", check_backends_use_protocol),
        probe("依赖方向单向（协议不依赖后端）", check_dependency_direction),
        probe("两条后端判定一致", lambda: check_same_verdict(database)),
        probe("第三条后端（沿树检索）也用同一份协议", check_tree_backend),
        probe("manifest 的效力记录来自 force_layer（不是第二份判定）",
              check_manifest_uses_layer),
    ]
    passed = sum(1 for ok, _ in results if ok is True)
    skipped = sum(1 for ok, _ in results if ok is None)
    print(f"\n通过 {passed}/{len(results) - skipped}（跳过 {skipped}）")


if __name__ == "__main__":
    main()
