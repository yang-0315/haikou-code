"""引擎无关的效力推理协议——本项目的 spine。

「带效力信息怎么推理」这件事只在这里写一次：状态含义、选条规则、作答规则、
以及给工具型 agent 的系统提示。两条后端都从这里取，所以"同一个效力层、
同一套推理规则"不是靠文档承诺，而是靠只有一份源：

    force_protocol  ←──  reasoning_retrieval.py（条文语料推理回路，主 demo）
                    └──  pageindex_force_adapter.py（PageIndex 工具循环，可移植证明）

依赖方向单向：协议只依赖 `warn` 里的状态常量，不依赖任何后端，也不依赖
ForceLayer。`spine_check.py` 会守住这条线——加一条规则只改 POLICY，不去
改两处提示词，否则两条线迟早各说各话。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warn import (ARTICLE_PARTIAL, ARTICLE_REPEALED, IN_FORCE, QUESTIONABLE,
                  REPEALED, SERIES_REPEALED)

# 选条数量的硬上限。提示词里写了 8，解析时也要拦一次，模型超选不能带崩上下文。
MAX_BLOCKS = 8

INVALIDATION_WORDS = (
    "已废止",
    "系列已废止",
    "失效",
    "不再有效",
    "不能作为现行依据",
    "不得作为现行依据",
)
NEGATED_BEFORE = (
    "未发现",
    "没有发现",
    "未出现",
    "没有出现",
    "未见",
    "不存在",
    "并未",
    "尚未",
    "没有被",
    "未被",
    "不是",
    "无需",
)
CONTRAST = re.compile(r"(?:但是?|然而|不过|可是|仍应|仍需)")


def explicit_invalidity(answer: str) -> bool:
    """Whether an answer positively says a source is no longer usable.

    The same detector is used for answer repair and evaluation.  A bare
    substring check would confuse "未发现已废止" with a positive warning.
    """
    answer = answer or ""
    for word in INVALIDATION_WORDS:
        start = 0
        while True:
            position = answer.find(word, start)
            if position == -1:
                break
            sentence_start = max(
                answer.rfind(mark, 0, position)
                for mark in ("。", "；", "\n")
            ) + 1
            clause = answer[sentence_start:position]
            negated_at = max(
                (clause.rfind(negated) for negated in NEGATED_BEFORE),
                default=-1,
            )
            tail = clause[negated_at:] if negated_at >= 0 else ""
            if negated_at < 0 or CONTRAST.search(tail):
                return True
            start = position + len(word)
    return bool(re.search(
        r"(?:已被|已由|已经由)[^。；\n]{0,40}(?:取代|替代|废止)",
        answer,
    ))

# 三态在两种渲染里的说法。法律状态只有一个源，措辞按读者不同。
STATUS_MEANING = [
    ((IN_FORCE,), "现行有效", "可以作为现行依据引用"),
    ((QUESTIONABLE,), "效力存疑", "年代久远或缺废止依据，不能直接当作现行依据"),
    ((REPEALED, SERIES_REPEALED), "已废止／系列已废止",
     "内容已失效，只能用于说明历史沿革"),
    ((ARTICLE_REPEALED,), "条文已废止",
     "文件其余部分可能仍有效，但该条不得作为现行条文引用"),
    ((ARTICLE_PARTIAL,), "条文部分失效",
     "该条只删除或变更了部分款、项，不能把原文本当作完整现行条文"),
]

# 给工具型 agent 的说法（逐状态展开，语气是命令）。
AGENT_STATUS_MEANING = {
    IN_FORCE: "可以作为现行依据引用",
    QUESTIONABLE: "年代久远或缺少废止依据，引用时必须提示需人工核实",
    REPEALED: "内容已失效，只能用于说明历史沿革，不得作为现行依据",
    SERIES_REPEALED: "内容已失效，只能用于说明历史沿革，不得作为现行依据",
    ARTICLE_REPEALED: "文件其余部分可能仍有效，但该条本身不得作为现行条文引用",
    ARTICLE_PARTIAL: "该条只有部分款、项失效或变更，不能引用原整条文本",
}

# ── 推理规则：一处定义，三种渲染 ────────────────────────────────
# select 给"读目录、选条文"的回路；answer 给拿到条文后作答的回路；
# agent 给"自己调工具读文档"的 agent。同一 id 的三份文字必须是同一件事，
# 缺哪份就是那条后端不需要它，不是忘了写。
POLICY = {
    "temporal_scope": {
        "select": "若给出查询时点，只能按该时点的效力状态选条；"
                  "不得把今天的效力状态套用到历史问题",
        "answer": "若给出查询时点，结论必须写明该时点，并说明这是历史效力"
                  "而非当前效力",
        "agent": "若给出查询时点，结论必须写明该时点，不得改写为“目前有效”",
    },
    "conclusion_first": {
        "answer": "结论在前，然后给出依据条文",
    },
    "in_force_first": {
        "select": "优先选「现行有效」的条文",
        "agent": "优先使用现行有效的条文",
    },
    "repealed_only_if_needed": {
        "select": "只有当问题无法由现行有效条文回答时，才选已废止的，并在理由里说明",
        "answer": "依据中若有「已废止」「系列已废止」，必须明确提示已失效并给出依据",
        "agent": "只有当现行有效条文无法回答问题时，才引用已废止的，"
                 "并在答案中明确说明该内容已失效及其依据",
    },
    "named_document_no_substitution": {
        "select": "问题点名某法规时，先选该法规本身；若它已废止，保留该法规条目"
                  "并可按废止依据另选现行替代法规。不得用“关于修改该法规的"
                  "决定”顶替被点名的法规正文",
        "answer": "问题点名某法规时，必须明确回答该法规；若已废止，说明其失效"
                  "状态及内容仅供参考，不得把修改决定说成该法规本身。可另行说明"
                  "现行替代法规",
        "agent": "用户点名某法规时，不得用对该法规的修改决定顶替其正文；"
                 "法规已废止时必须明确说明",
    },
    "questionable_flag": {
        "answer": "依据中若有「效力存疑」，提示需人工核实",
        "agent": "引用存疑条文时标注需核实",
    },
    "article_level_force": {
        "select": "文档整体有效不代表每一条仍有效；目录中单条标为"
                  "「条文已废止」或「条文部分失效」时，必须按单条状态取舍",
        "answer": "引用条目标注「条文已废止」或「条文部分失效」时，必须逐条说明"
                  "该条不能作为现行完整依据，同时说明文档其他条文可能仍有效",
        "agent": "节点下的 force 可能是条文级状态；不得只按文档级状态"
                 "认定该条仍有效",
    },
    "cite_source_and_force": {
        "answer": "每条依据写明：文件名、第X条、效力状态",
        "agent": "给出结论时，把出处与效力状态一并写出",
    },
    "refuse_insufficient": {
        "select": "目录里没有任何条文能回答该问题时，不要勉强选："
                  "把 answerable 置为 false",
        "answer": "条文不足以回答时直接说明，不要推测",
        "agent": "条文不足以回答时直接说明，不要推测",
    },
    "max_blocks": {
        "select": f"最多选 {MAX_BLOCKS} 条",
    },
}

# 每条的编号顺序单独给：同一条规则在选条与作答里的先后本来就不一样。
SELECT_ORDER = ["temporal_scope", "named_document_no_substitution",
                "in_force_first", "repealed_only_if_needed",
                "article_level_force", "max_blocks", "refuse_insufficient"]
ANSWER_ORDER = ["temporal_scope", "named_document_no_substitution",
                "conclusion_first", "cite_source_and_force",
                "article_level_force", "repealed_only_if_needed",
                "questionable_flag",
                "refuse_insufficient"]
AGENT_ORDER = ["temporal_scope", "named_document_no_substitution",
               "in_force_first", "repealed_only_if_needed",
               "article_level_force", "questionable_flag",
               "cite_source_and_force",
               "refuse_insufficient"]

SELECT_HEADER = "请判断回答这个问题需要读哪几条，并说明理由。选择时遵守："
ANSWER_HEADER = "回答要求："

# 选条阶段的输出契约。模型必须按这个形状回答，管道据此判断能不能答。
SELECT_SCHEMA = ('{"answerable": true/false, "read": ["文档名|第X条"], '
                 '"reason": "选择依据", '
                 '"force_note": "涉及失效文件时说明，否则留空"}')

# 沿树检索时的契约：选的是树上的节点，不是条号——同一套规则换个寻址方式。
SELECT_NODE_SCHEMA = ('{"answerable": true/false, "nodes": ["文档名|node_id"], '
                      '"reason": "选择依据", '
                      '"force_note": "涉及失效文件时说明，否则留空"}')


def _render(order: list[str], field: str, header: str) -> list[str]:
    lines = [header]
    for number, rule_id in enumerate(order, 1):
        text = POLICY[rule_id].get(field)
        if text is None:
            raise KeyError(f"规则 {rule_id} 没有 {field} 渲染")
        lines.append(f"{number}. {text}")
    return lines


def select_rules() -> list[str]:
    return _render(SELECT_ORDER, "select", SELECT_HEADER)


def answer_rules() -> list[str]:
    return _render(ANSWER_ORDER, "answer", ANSWER_HEADER)


def temporal_context(as_of: dict | None) -> str:
    """Render a historical point without taking a dependency on a backend."""
    if not as_of:
        return ""
    label = as_of.get("label") or as_of.get("as_of") or ""
    date = as_of.get("as_of") or label
    precision = {"day": "具体日期", "month": "月份",
                 "year": "年份"}.get(as_of.get("precision"), "查询点")
    return (f"查询时点：{label}（按{precision}，证据日按 {date} 判断）。"
            "以下效力均指该时点，不是当前状态；回答必须明确写出该时点。")


def force_meaning(as_of: dict | None = None) -> list[str]:
    """给推理回路看的状态含义（三态合并成三行）。"""
    lines = ["效力状态的含义："] + [
        f"- {label}：{meaning}" for _, label, meaning in STATUS_MEANING]
    context = temporal_context(as_of)
    return ([context, ""] if context else []) + lines


def agent_instructions(as_of: dict | None = None) -> str:
    """追加到工具型 agent 系统提示的效力说明。"""
    lines = ["每份文档和每页都带 force 字段，说明其法律效力状态："]
    context = temporal_context(as_of)
    if context:
        lines += [context, ""]
    for keys, label, _ in STATUS_MEANING:
        meanings = {AGENT_STATUS_MEANING[key] for key in keys}
        if len(meanings) != 1:
            raise ValueError(f"{label} 的各状态在 agent 渲染里说法不一致")
        lines.append(f"- {label}：{meanings.pop()}")
    lines += ["", "回答规则："]
    for number, rule_id in enumerate(AGENT_ORDER, 1):
        lines.append(f"{number}. {POLICY[rule_id]['agent']}；")
    lines[-1] = lines[-1].rstrip("；") + "。"
    return "\n".join(lines)


def build_select_prompt(query: str, outline: str,
                        as_of: dict | None = None,
                        candidate_notes: list[str] | None = None) -> str:
    """选条提示：目录每条带效力，模型选条或判定答不了。"""
    lines = ["你是政策检索助手。下面是候选条文的目录，每条带所属文档的效力状态。"]
    if candidate_notes:
        lines += ["", "问题点名文件提示："] + [
            f"- {note}" for note in candidate_notes]
    lines += ([""] + force_meaning(as_of) + [""] + select_rules() + [
        "", f"问题：{query}", "", "目录：", outline, "",
        "只输出 JSON，不要其他文字：", SELECT_SCHEMA])
    return "\n".join(lines)


def build_node_select_prompt(query: str, outline: str,
                             as_of: dict | None = None,
                             candidate_notes: list[str] | None = None) -> str:
    """沿树选节点的提示：目录是树（章 → 条），每个节点带 node_id 与效力。"""
    lines = [
        "你是政策检索助手。下面是候选文档的树目录，节点带 node_id 与效力状态。",
        "选节点时给出 node_id（照抄目录里的编号），不要改写。",
    ]
    if candidate_notes:
        lines += ["", "问题点名文件提示："] + [
            f"- {note}" for note in candidate_notes]
    lines += ([""] + force_meaning(as_of) + [""] + select_rules() + [
        "", f"问题：{query}", "", "树目录：", outline, "",
        "只输出 JSON，不要其他文字：", SELECT_NODE_SCHEMA])
    return "\n".join(lines)


def build_answer_prompt(query: str, blocks: list[dict],
                        notes: list[str] | None = None,
                        as_of: dict | None = None) -> str:
    """作答提示：条文带效力，已废止的必须说明失效。"""
    body = []
    for block in blocks:
        head = f"《{block['doc']}》"
        if block.get("doc_number"):
            head += f"（{block['doc_number']}）"
        head += f" {block.get('article') or ''}　效力：{block['status']}"
        if block.get("force_reason"):
            head += f"\n效力说明：{block['force_reason']}"
        body.append(f"{head}\n{block['text'][:600]}")
    lines = ["你是政策问答助手。下面是从政策库取出的条文，每条标注了效力状态。",
             ""]
    context = temporal_context(as_of)
    if context:
        lines += [context, ""]
    lines += answer_rules()
    if notes:
        lines += ["", "选条时的情况说明（回答里需要时请一并说明）："] + \
            [f"- {note}" for note in notes]
    lines += ["", f"问题：{query}", "", "条文：", "\n\n".join(body)]
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="打印 spine 的三种渲染")
    parser.add_argument("--which", default="agent",
                        choices=["agent", "select", "answer", "status"])
    args = parser.parse_args()
    if args.which == "agent":
        print(agent_instructions())
    elif args.which == "select":
        print("\n".join(select_rules()))
    elif args.which == "answer":
        print("\n".join(answer_rules()))
    else:
        print("\n".join(force_meaning()))


if __name__ == "__main__":
    main()
