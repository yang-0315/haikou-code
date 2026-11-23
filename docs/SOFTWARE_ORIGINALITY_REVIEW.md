# 软件著作权原创性初筛

生成时间：2026-11-23T17:25:10+08:00

> 本报告是工程审计，不是法律意见，也不能替代版权登记机关的审查。

## 结论

- 申报范围由自研核心模块、测试/维护工具和前端组成；
- PageIndex 作为 MIT 子模块使用，不纳入本软件源码；
- 未发现大段 PageIndex 非平凡源码行重合；
- 核心功能集中在效力判定、历史时点、条文级变更、检索路由、
  目录解析、更新扫描和本地问答服务，不是单一 CRUD 页面；
- 申报前仍需由申请人确认真实创作过程，并清理输出材料中的第三方内容。

## 申报范围统计

| 分组 | 文件数 | 行数 |
|---|---:|---:|
| core | 16 | 7139 |
| support | 16 | 3757 |
| web | 3 | 2277 |
| tools | 2 | 738 |

## 文件清单

| 分组 | 文件 | 行数 | 函数 | 类 |
|---|---|---:|---:|---:|
| core | `haikou/article_force.py` | 529 | 21 | 1 |
| core | `haikou/catalog_index.py` | 675 | 25 | 0 |
| core | `haikou/force_layer.py` | 478 | 20 | 1 |
| core | `haikou/force_protocol.py` | 327 | 11 | 0 |
| core | `haikou/llm_provider.py` | 287 | 15 | 6 |
| core | `haikou/pageindex_force_adapter.py` | 462 | 29 | 2 |
| core | `haikou/policy_update_scan.py` | 350 | 11 | 0 |
| core | `haikou/reasoning_retrieval.py` | 730 | 20 | 0 |
| core | `haikou/retrieval.py` | 652 | 24 | 1 |
| core | `haikou/retrieval_router.py` | 178 | 4 | 0 |
| core | `haikou/service.py` | 600 | 30 | 4 |
| core | `haikou/temporal.py` | 357 | 10 | 0 |
| core | `haikou/temporal_evidence.py` | 380 | 11 | 0 |
| core | `haikou/tree_index.py` | 379 | 25 | 1 |
| core | `haikou/tree_retrieval.py` | 331 | 10 | 0 |
| core | `haikou/warn.py` | 424 | 12 | 0 |
| support | `haikou/audit_article_force.py` | 268 | 9 | 2 |
| support | `haikou/audit_catalog.py` | 173 | 12 | 0 |
| support | `haikou/audit_provider.py` | 191 | 19 | 1 |
| support | `haikou/audit_reasoning.py` | 318 | 23 | 0 |
| support | `haikou/audit_repeal.py` | 88 | 2 | 0 |
| support | `haikou/audit_router.py` | 132 | 8 | 0 |
| support | `haikou/audit_service.py` | 223 | 5 | 1 |
| support | `haikou/audit_temporal.py` | 211 | 17 | 0 |
| support | `haikou/audit_temporal_coverage.py` | 209 | 14 | 0 |
| support | `haikou/audit_tree_force.py` | 268 | 15 | 0 |
| support | `haikou/audit_tree_retrieval.py` | 139 | 13 | 0 |
| support | `haikou/compare_baselines.py` | 451 | 12 | 0 |
| support | `haikou/eval_qa.py` | 660 | 16 | 0 |
| support | `haikou/repair_repeal_index.py` | 91 | 1 | 0 |
| support | `haikou/stability_check.py` | 132 | 3 | 0 |
| support | `haikou/spine_check.py` | 203 | 8 | 0 |
| web | `haikou/web/index.html` | 238 | 0 | 0 |
| web | `haikou/web/app.js` | 583 | 0 | 0 |
| web | `haikou/web/app.css` | 1456 | 0 | 0 |
| tools | `tools/run_test_round.py` | 523 | 9 | 1 |
| tools | `tools/archive_legacy_tests.py` | 215 | 5 | 0 |

## 与 PageIndex 的非平凡行重合

共 4 行。短行、注释和小于 40 字符的常规语句未统计。

```text
def get_tree(self, doc_id: str, node_summary: bool = False,
from concurrent.futures import ThreadPoolExecutor
if not path or not os.path.exists(path):
start, end = text.find("{"), text.rfind("}")
```

## 第三方版权头或生成标记

未发现。

## 长字符串/潜在内嵌正文

字符串达到 300 字符时会在此列出，用于检查是否把政策正文或第三方内容混入源码。

- haikou\article_force.py:1: 830 chars: 'Article-level legal-force events inside otherwise effective documents.'
- haikou\article_force.py:158: 304 chars: 'Extract whole-article and partial-article deletions from one target se'
- haikou\catalog_index.py:1: 441 chars: '把《执法事项指导目录》的 PDF 附件还原成事项级索引。\n\n网页正文是**列式摊平**的（事项名被折成十来个字一行），切不出事项；但页面上挂'
- haikou\force_layer.py:1: 668 chars: 'Legal-force layer for PageIndex trees: annotate before, review after.\n'
- haikou\force_protocol.py:1: 385 chars: '引擎无关的效力推理协议——本项目的 spine。\n\n「带效力信息怎么推理」这件事只在这里写一次：状态含义、选条规则、作答规则、\n以及给工具型'
- haikou\pageindex_force_adapter.py:1: 833 chars: "Put the force dimension into PageIndex's own tool responses.\n\nThe tool"
- haikou\policy_update_scan.py:1: 503 chars: 'Scan official Hainan policy channels without mutating the policy libra'
- haikou\reasoning_retrieval.py:1: 407 chars: "Force-aware reasoning retrieval.\n\nSame shape as PageIndex's retrieval:"
- haikou\retrieval.py:1: 441 chars: 'Article-level retrieval over the policy corpus, with force warnings.\n\n'
- haikou\retrieval_router.py:1: 614 chars: '检索策略路由：先判断这题该走哪条检索路，再让那条路去取证据。\n\n现在手上能用的是四条路加两条"走不通"的路：\n\n    article_ex'
- haikou\service.py:1: 347 chars: 'User-facing HTTP service for force-aware policy question answering.\n\nT'
- haikou\temporal.py:1: 552 chars: 'Point-in-time legal force without pretending that every date is known.'
- haikou\temporal.py:134: 330 chars: "Read the document's own masthead and implementation clause.\n\n    ``doc"
- haikou\tree_index.py:1: 485 chars: '把效力挂到真正的 PageIndex 树上，并让树本身成为检索底座。\n\n上游的树以**页**为单位：节点是 `{title, node_id'
- haikou\tree_retrieval.py:1: 424 chars: '沿带效力的树做检索：模型看到的是树上的节点，不是一份条文清单。\n\n与 `reasoning_retrieval.py` 的区别在"喂什么给模'
- haikou\warn.py:1: 438 chars: 'Attach a legal-force status to every document in the corpus.\n\nReads _r'
- haikou\warn.py:208: 312 chars: '这次名字匹配有多硬。\n\n    `exact`      两边完全相同（去掉空格标点后）——就是它\n    `contained`  废止名'
- haikou\audit_reasoning.py:1: 319 chars: 'Functional audit of the force-aware reasoning retrieval.\n\nProbes the p'
- haikou\audit_temporal.py:1: 353 chars: 'Offline audit for point-in-time legal force.\n\nThe checks cover three d'
- haikou\audit_tree_force.py:1: 404 chars: '效力挂到树上以后，树本身对不对。\n\n挂维度不能只是"字段加上了"。这里查六件事，全库跑，不是抽样：\n\n  1. 树上的条文 == 检索层的条'
- haikou\eval_qa.py:1: 428 chars: 'validity-QA 评测：答案既要对，又必须引用现行有效条文。\n\n分类指标（三态判定对没对）和问答指标是两回事。这个集子考的是端到端问答'
- haikou\stability_check.py:1: 451 chars: 'Is the force rule stable as the corpus grows?\n\nThree questions:\n  1. d'
- haikou\spine_check.py:1: 311 chars: '守住 spine：协议只有一份源，两条后端不许各说各话。\n\n`force_protocol.py` 是引擎无关的效力推理协议，`reason'
- tools\run_test_round.py:1: 344 chars: 'Run one reproducible project test round and preserve its evidence.\n\nTh'

## 申报前人工确认

- [ ] 申请人确认需求、架构、规则、代码审查和验收由本人主导；
- [ ] 申报源码不包含 PageIndex、`database/`、`_vendor` 或依赖源码；
- [ ] 说明书中不把政策原文、模型原始回答或第三方界面当作自有内容；
- [ ] 保留 Git 快照、测试报告和版本说明作为开发过程证据；
- [ ] 由申请人或代理机构按登记要求完成最终原创性判断。
