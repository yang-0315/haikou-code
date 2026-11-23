# 项目进度（截至 2026-11-23）

## 一句话

在 PageIndex 之上做**海南政策持续更新、效力追踪与执法问答平台**：跟随官方渠道
发现法规新增、正文变化、修订、废止和部分失效，经官方证据补证后版本化发布，再将
更新后的效力数据同时供通用政策问答、变化问答和执法事项问答使用。问答保留，并且
是持续更新政策库的前台入口。当前端到端可跑，并支持按历史时点重算效力；语料覆盖
约 99%，最终离线测试轮 21/21 项、单项检查合计 222/222，Web 服务审计
27/27，validity-QA v4 在线复跑 84/84；代码已
同步到 GitHub `yang-0315/haikou-code`。注意：本轮修复和比较实验仍是本地未提交
改动。

**当前开发重心：** 海南政策持续更新、官方证据补证和执法事项问答；通用政策问答
保留。软件著作权申报作为并行的项目收尾工作，执行计划见
`docs/SOFTWARE_COPYRIGHT_APPLICATION_PLAN.md`，原创性初筛见
`docs/SOFTWARE_ORIGINALITY_REVIEW.md`。

---

## 目录与分工

| 目录/文件 | 作用 |
|---|---|
| `PageIndex/` | 上游开源项目，**不改动**；真实客户端只在样例索引上验证过 |
| `chinastruct/` | 中文长文档结构层（PDF→树）。当前没有 PDF 语料，**处于闲置状态** |
| `haikou/` | 效力层 + 检索 + 协议 + 路由 + 审计（本项目主体） |
| `database/` | 语料库与索引；解析顺序为 `POLICY_DATABASE` → 旧外置目录 → 当前 `PageIndex\database` |
| `SPINE.md` | 架构主线说明（协议 = 贡献本体，检索后端 = 消费者） |
| `docs/PRODUCT_DIRECTION.md` | 产品方向、持续更新闭环、官方补证规则与验收指标 |
| `progress.md` | 本文件 |

---

## 当前数字

### 语料

| 项 | 数值 |
|---|---|
| 正文份数 | 1,248（根语料 4 + library 1,244） |
| 覆盖率 | 目录去重后约 1,255 份 → **99%** |
| 去空白总字数 | **887 万**（中位 4,460 字） |
| ≥500 字 | 1,173 份 |
| 乱码字符（U+FFFD） | 0 |
| 废止依据 | 通知 23 份 + **151 份文档正文里的「同时废止」条款** |

### 索引

| 项 | 数值 |
|---|---|
| 检索单元 | **43,642**（条文 24,471 + 句块 19,171） |
| 覆盖文档 | **1,244（全部）** |
| 倒排词条 | 276,199 |
| 持久化索引 `_retrieval_index.pkl` | 41 MB；冷启动 0.68s，进程内 0.16s |
| 效力树 `_force_trees.json` | 28.4 MB；1,246 份，654 份有章/条结构，33,665 节点 / 24,387 条文 |
| 事项目录 `_catalog_items.json` | 三版合计 **11,602 个事项**：2022 版 4,056、2023 版 3,877、2024 版 3,669；各版条款直引 100% |
| 条文效力 `_article_force_index.json` | **60 个事件、35 份修改决定、57 份目标文档、313 个条文目标**；32 份旧文本直接应用，5 份已重新公布文本不套旧条号 |
| 政策更新账本 `_update_ledger.json` | 首页扫描发现 **17 个待审核候选**；真实取证后 **16 个有正文快照**、1 个旧式 `.wps` 需人工补正文、1 个跨栏目重复正文；发布 0 |
| 文档效力 `_force_status.json` | 已修复为覆盖全部 **1,248 份**；此前实际只写根目录 4 份 |
| 文号索引 `_document_numbers.json` | 可提取 **733/1,248**；剩余 515 份已生成逐条补证队列 |

### 效力判定（1,244 份）

| 状态 | 数量 |
|---|---|
| 现行有效 | 1,178（95%） |
| 已废止 | 35 |
| 效力存疑 | 24 |
| 系列已废止 | 7 |

### 质量

| 检查 | 结果 |
|---|---|
| validity-QA（v4 84 题） | **84/84** |
| 其中：正题 48/48／已废止陷阱 21/21／语料外 6/6／存疑 8/8／历史时点 1/1 | trap-05、trap-07、trap-09 已修复 |
| 答案对（必备证据＋要点齐） | 57/57 |
| 引用合规（无未标注失效引用） | 84/84 |
| 现行有效题只引现行有效条文 | 48/48 |
| 所有者验收 | v3 **80/80**；A/B 为同一所有者转录，**不是独立盲评**；v4 新增 4 题尚未盲评 |
| 抽检 19 份（128 份语料时快照） | 二分类一致 17/19（89%）、“现行有效”精确率 100%、非现行召回率 100%、过度标记 2 |
| 审计合计 | 最终测试轮 **21/21 项全过**；单项检查合计 **222/222**（spine 6／路由 13／事项 10／数据债 15／树 10／沿树 8／推理 19／时点 13／时点证据 12／Provider 10／条文效力 28／更新工作流 27／废止 23 等） |
| 时点检索 | 废止前／废止后可切到不同效力；区间跨事件或缺少施行日时保守降为存疑 |
| 时点证据索引 | 1,248 份全部入索引；明确施行日 **752**；需施行日但缺失 **83**；政策发布型 261 |
| 模型 Provider | DeepSeek／OpenAI／OpenAI-compatible／本地模型可按配置切换；业务代码无模型 URL／密钥硬编码 |
| 条文级部分废止 | 整条删除标为“条文已废止”；只删款/项标为“条文部分失效”；变更日不足或区间跨越时降为存疑 |
| Web 服务审计 | **27/27**，含更新接口和发布期间拒绝新查询 |
| 稳定性 | 0 处反向放宽 |

---

## 新对话快照（2026-11-23 最新，先读这一段）

### 产品方向

- **政策库持续更新是底座**：监测海南官方渠道，发现新增、正文/附件变化、修订、
  废止和部分失效；任何更新先进入待审核账本，不静默覆盖正文或效力。
- **官方补证是发布前提**：效力变化必须有官方 URL、证据句、核对人和时间；证据
  不足时保持“效力存疑”。
- **问答保留且是前台**：通用政策问答、政策变化问答、执法事项问答共用同一份
  更新后的现行效力和历史版本数据。
- 产品方向全文见 `docs/PRODUCT_DIRECTION.md`。

### 最新完成

1. **三版事项目录完成**：PDF 原始竖线逐页识别 8 列，跨页续段合并。
   2022／2023／2024 共 **11,602 个事项**，各版条款直引均 **100%**，
   `audit_catalog` **10/10**。
2. **政策更新闭环已补齐**：扫描后支持页面/附件取证、页面与正文 SHA-256、版本
   差异、CSV/命令行审核闸门、跨进程发布锁、完整备份、事务回滚和全部派生索引
   重建。真实首页扫描得到 **17 个候选**；16 个已取得正文快照，1 个 `.wps`
   附件需人工补正文，1 个跨栏目重复正文被标记，发布数仍为 0。
3. **trap-05、trap-07、trap-09 已修复**：修改决定不再替代被点名的原法规；
   用户点名已废止法规时，原法规与明文废止它的现行替代法规同时进入候选；模型若
   静默换成现行文件，系统会前置确定性失效提示。否定句中的“未出现已废止”不再
   被误当成失效警告。
4. **完整 validity-QA v4 已重跑并通过**：当前代码、当前题集、当前语料下
   **84/84**；正题 48/48、已废止陷阱 21/21、语料外 6/6、存疑 8/8、历史时点 1/1；
   答案对 57/57、引用合规 84/84、现行有效题 48/48。
5. **推理与索引审计扩展**：`audit_reasoning.py` 现为 **19/19**，新增“修改决定
   不得替代原法规”“沿树拒答时仍返回点名校验/替代法规”和“否定句不能掩盖旧法
   被替代”。检索缓存指纹现已包含效力与废止派生索引，避免效力重建后继续读旧状态。
6. **北大法宝真实任务预对比完成**：消防设施违法行为题中，本系统 8.1 秒完成并排除
   已废止旧条例；北大法宝游客模式只完成法规元数据核验，条款阅读受登录/订阅门槛
   阻断。报告在 `test_runs/2026-11-23_pkulaw-real-task-01/comparison.md`。
7. **三类数据债完成首轮清理**：2022／2023／2024 三版目录全部建立，共 11,602
   个事项；明确施行日从 635 提升到 752，缺口从 156 降到 83；文号从 536 提升到
   733，并生成 515 份逐条官方补证队列。URL 月份、引用文号和标题猜测均不升级为
   确定性事实。

### 当前验证证据

- `haikou/eval_results_v4.json`：当前代码的完整 84 题模型结果。
- `test_runs/2026-11-23_round-06/summary.md`：最终离线测试轮，21/21 项通过。
- `test_runs/2026-11-23_trap-fix/summary.md`：trap-05/trap-07 根因、修复和回归记录。
- `test_runs/2026-11-23_trap-fix/results-tree-fallback.json`：两条目标题的真实模型
  重跑结果。
- `test_runs/2026-11-23_catalog-direct-citation/summary.md`：目录直引离线回归。
- `database/_update_ledger.json`：官方更新扫描、取证、审核和发布账本。
- `database/update_review.csv`：17 个候选的业务审核队列。
- `database/_update_workflow/20261123T005343/`：页面、正文、附件、差异和哈希快照。
- `database/_document_numbers.json`：733 份文号、来源证据与 515 份缺口。
- `haikou/document_number_review_v1/review_queue.csv`：逐条文号人工补证队列。
- `haikou/temporal_review_v1/review_queue.csv`：83 份时点日期人工补证队列。

### 本轮主要改动文件

- `docs/PRODUCT_DIRECTION.md`：新的产品方向、更新闭环和验收指标。
- `haikou/policy_update_scan.py`：官方首页扫描、差异分类和待审核账本。
- `haikou/update_workflow.py`：更新取证、审核闸门、锁、发布、回滚和公开摘要。
- `haikou/audit_update_workflow.py`：27 项离线业务审计。
- `docs/UPDATE_WORKFLOW_RUNBOOK.md`：冻结前更新工作流操作手册。
- `haikou/catalog_index.py`：2022／2023／2024 三版目录、并发锁、断点续跑和状态修复。
- `haikou/data_debt.py`：时点/文号缺口分类、文号索引和人工补证队列。
- `haikou/document_numbers.py`：排除引用文号的本文件文号抽取。
- `haikou/audit_data_debt.py`：15 项三版目录、日期、文号与状态恢复审计。
- `docs/DATA_DEBT_RUNBOOK.md`：三类数据债修复口径和剩余边界。
- `haikou/catalog_index.py`、`haikou/audit_catalog.py`、`haikou/web/app.*`：
  表格目录 8 列解析、续页合并、条款直引和来源原文展示。
- `haikou/retrieval.py`：识别“关于修改《X》的决定”，禁止在普通事实问法中把
  它当作 X 的正文。
- `haikou/reasoning_retrieval.py`、`haikou/tree_retrieval.py`：点名已废止法规时
  同时召回替代法规，并统一失效提示与确定性兜底。
- `haikou/force_protocol.py`：新增“点名法规不得被修改决定替代”协议规则。
- `haikou/audit_reasoning.py`：新增修改决定、树检索和否定句回归审计。
- `haikou/retrieval.py`：持久化索引同时校验正文与效力/废止派生索引。
- `haikou/warn.py`：`_force_status.json` 从 4 份修复为全量 1,248 份。
- `haikou/service.py`、`haikou/web/`：新增更新状态接口和“最近发现的变化”面板。
- `haikou/eval_results_v4.json`：当前代码的 84 题完整在线结果。

### 已知边界

- 政策更新闭环的代码、取证、审核、事务发布和索引重建已完成；每日自动调度仍属于
  部署配置，不在 V1.0 离线程序内伪装为已运行。
- 17 个候选仍未完成人工官方证据审核，不能当作已更新政策库；其中 1 个 `.wps`
  附件需人工补正文，1 个跨栏目重复正文应核对后拒绝。
- 三版执法目录已建立；仍有 83 份缺明确施行日、515 份未抽到文号。缺少正文日期
  或正式文号的文件必须人工核对官方来源，不能从 URL 月份、标题或引用依据反推。
- 三方案对比仍是历史快照（效力层 82/84）；当前 validity-QA 已单独重跑到 84/84，
  但没有重新跑三方案比较。
- 北大法宝对比还不是同等权限终局对比；v4 新增题也没有独立盲评。
- 工作区有大量未提交修改，包括本轮代码、评测结果、更新账本、测试工具和软著材料。
  **不要执行 `git reset --hard`、`git checkout --` 或清理未知目录。**

### 常用恢复命令

```powershell
$python = 'C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

# 当前评测结果新鲜度和完整 84 题复评
& $python haikou\eval_qa.py --validate-data
& $python haikou\eval_qa.py

# 目标规则离线审计
& $python haikou\audit_reasoning.py
& $python haikou\audit_catalog.py
& $python haikou\spine_check.py

# 官方更新首页只读扫描（需要时再加 --apply 写账本）
& $python haikou\policy_update_scan.py --max-pages 1

# 更新闭环：查看、取证、导入审核、事务发布与离线审计
& $python haikou\update_workflow.py list
& $python haikou\update_workflow.py stage
& $python haikou\update_workflow.py import-review
& $python haikou\update_workflow.py publish --id CANDIDATE_ID
& $python haikou\audit_update_workflow.py
```

真实模型调用可能需要网络权限；若出现 `WinError 10013`，用可联网的执行权限重跑。
`eval_qa.py --run` 会重写 `haikou/eval_results_v4.json`，84 题需要数分钟。

### 新对话建议顺序

1. 先读本快照和 `docs/PRODUCT_DIRECTION.md`。
2. 如继续产品数据，先按 `docs/UPDATE_WORKFLOW_RUNBOOK.md` 审核 17 个真实候选；
   1 个 `.wps` 需人工补正文，1 个跨栏目重复正文应拒绝。不要为了清空队列降低门槛。
3. 如继续问答质量，先读 `test_runs/2026-11-23_trap-fix/summary.md`，不要重新修已通过的
   trap-05/trap-07。
4. 如继续软著，读 `docs/SOFTWARE_COPYRIGHT_APPLICATION_PLAN.md` 和
   `docs/SOFTWARE_ORIGINALITY_REVIEW.md`，保持当前 V1.0 范围和源码边界。

---

## 历史开发摘要（追溯；以上方最新快照为准）

### 当时状态（历史轮次，已被上方最新快照替代）

- **最终目标：申请软件著作权。** 申报对象已收敛为“海南自由贸易港政策法规
  效力感知检索与问答系统 V1.0”；市场竞争力不再是开发前置条件。
- 工作目录：`C:\Users\Lenovo\Desktop\PageIndex`
- Git 分支：`main`；远程基线已同步，但本轮代码修改、v4 数据、测试工具和比较实验
  仍是未提交状态。新对话不得执行 `git reset --hard` 或 `git checkout --`。
- 自研核心最新测试批次：`test_runs/2026-11-22_round-02/`，核心 19/19 通过，
  在线 Provider 通过，真实 PageIndex 客户端 10/10。
- validity-QA v4：当前代码在线复跑 **84/84**；trap-05、trap-07 已修复。
  结果带题集、代码、语料和模型指纹，旧缓存默认失效。
- 历史三方案对比快照：效力层 82/84，普通本地 RAG 49/84，直接模型 3/84。详细报告：
  `test_runs/2026-11-22_comparison-01/summary.md`。
- 联网模型手测：普通提示下 `wm-03`、`wm-04` 都误用旧法；明确要求先核验效力后，
  `wm-03` 能正确识别废止和新法规，但具体条文可能来自草案。
- 原创性初筛：申报范围 37 个文件、12,993 行、454 个函数、21 个类；与 PageIndex
  非平凡相同代码行仅 4 行，未发现第三方版权头。
- `PageIndex/` 仍是固定上游子模块 `9c4c3ff`，没有修改源码；其 pytest 为
  668 passed／77 skipped／5 failed，其中 1 项是同名并发入库稳定失败。
- 已清理 `.cache`、`.slidep`、`.edge-headless`、旧服务截图、重复 4 题试跑批次
  和 20 个 `__pycache__`，约释放 487 MB。仅剩约 13.6 MB 的 `.edge-debug` 被
  Edge 调试进程占用，以及两个空的 `.pytest_cache`，不影响项目。

### 历史轮次已完成事项

1. 建立可复现测试批次运行器 `tools/run_test_round.py`，保存命令、耗时、退出码、
   stdout/stderr 和环境快照。
2. 将历史评测、盲评、时点核对、抽检和运行日志统一快照到
   `test_runs/legacy-archive/`，共 52 个文件，带来源路径和 SHA-256。
3. 修复审计“发现问题仍返回 0”的假通过；当前参与审计的脚本会在失败时返回非零。
4. 修复 QA 评分的否定句误判、`llm_error` 冒充拒答、部分失效条文误作正题证据、
   静默换文件通过陷阱题等问题；评分器自检 15/15。
5. 正题全部强制 `must_cite`；`eq-14` 至 `eq-17` 补上段落级证据，`eq-39` 因条文
   部分失效改列为存疑题。
6. 新增 v4 四道路由覆盖题，锁定 `article_exact`、`multi_doc`、`catalog_item`
   和历史 `as_of`；84 题实际覆盖全部 7 类路由。
7. 评测结果加入生成时间、Provider、模型、题集哈希、代码哈希和语料指纹；
   默认拒绝旧缓存，诊断时才允许 `--allow-stale`。
8. 从官方 DOCX 和页面正文补齐 3 份问题废止决定，清单分别恢复为 7、17、4 件；
   `audit_repeal` 23/23。
9. 修正盲评 manifest 状态，明确 v3 A/B 为所有者转录、非独立盲评；v4 新增 4 题
   尚未进入盲评。
10. 修正历史抽检指标：二分类一致 17/19（89%），不再把“效力存疑”算作精确判出
    已废止。
11. 新增 `haikou/compare_baselines.py`：同一模型下比较效力层、普通本地 RAG 和
    直接模型；保存逐题回答、自动判定和汇总报告。
12. 修正事项目录 PDF 列位解析：按逐页原始竖线识别 8 列，并回单元格内半句、
    合并 126 个跨页续段；事项恢复为 3,877 条，条款直引从中段错位提升到 100%。
13. 建立联网模型人工对比记录：普通提问、旧法陷阱和明确效力核验提示分开记录，
    证明“联网搜索”不自动等于“法规版本核验”。
14. 目标正式切换为软件著作权申请；新增申报计划、申报范围和项目收尾说明。
15. 新增原创性审计工具，生成带行数、函数数、哈希和 PageIndex 重合检查的
    `docs/SOFTWARE_ORIGINALITY_REVIEW.md` 与
    `copyright_assets/source_inventory.csv`。
16. 完成软著前功能检查：Web 服务 22/22、事项目录 9/9、推理协议 6/6、
    更新扫描自检和数据契约均通过。
17. 清理约 487 MB 可再生成缓存和调试资料，保留源码、语料、测试、演示文稿与
    软著材料。

### 未完成的待办（按优先级）

1. **冻结 V1.0 申报范围**：确认软件名称、版本、功能清单和自研源码清单。
2. **生成源代码申报材料**：清理第三方代码与语料，生成文件清单、SHA-256、
   50 行/页源码 PDF，以及超过 60 页时的前 30 页和后 30 页。
3. **完成用户说明书**：覆盖安装、启动、普通问答、效力提示、历史时点、拒答、
   事项目录、政策更新监控、错误处理和已知边界。
4. **制作真实功能截图**：使用 V1.0 当前程序，补齐至少十类核心截图。
5. **完成第三方边界复核**：保留 PageIndex MIT 许可以及依赖、语料和
   `_vendor` 排除说明。
6. **冻结申报版本**：建立 V1.0 标签和源码指纹，提交后不得无版本记录地覆盖。
7. **保存申请档案**：记录申请号、提交日期、材料版本、申请人和全部回执。

一般市场验证、联网模型对比和非软著必要的产品功能不再作为开发前置条件。

### 当时恢复入口（新对话请使用上方最新命令）

```powershell
$python = 'C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python tools\run_test_round.py --upstream
& $python haikou\eval_qa.py --validate-data
& $python haikou\eval_qa.py --allow-stale
& $python tools\audit_copyright_originality.py
& $python haikou\compare_baselines.py --reuse --output test_runs\2026-11-22_comparison-01
```

先读：

- `docs/PROJECT_SUMMARY_2026-11-23.md`
- `docs/SOFTWARE_COPYRIGHT_APPLICATION_PLAN.md`
- `docs/SOFTWARE_ORIGINALITY_REVIEW.md`
- `copyright_assets/source_inventory.csv`
- `test_runs/2026-11-22_round-02/summary.md`
- `test_runs/2026-11-22_round-02/findings.md`
- `test_runs/2026-11-22_comparison-01/summary.md`

---

## 本轮进展（2026-11-22／23）

- **大方向已调整**：保留通用政策问答，但不再以“复刻通用法律问答站”为目标；
  产品本体改为海南政策持续更新、效力追踪、官方补证，以及政策变化与执法事项的
  问答联动。首版 `policy_update_scan.py` 已落地：默认只扫描和生成差异，不静默
  覆盖目录或正文。官方六栏目首页实扫发现 17 个待审核候选，账本和报告已生成。
- **北大法宝真实任务预对比完成**：以“海南企业损坏、挪用或擅自拆除、停用消防设施”为题，
  本系统 8.1 秒给出完整答案并主动排除已废止的旧《海南省消防条例》；北大法宝游客模式
  精确确认《海南自由贸易港消防条例》现行有效及法规元数据，但第六十九条位于登录/订阅
  门槛之后，无法完成同等权限终局对比。报告见
  `test_runs/2026-11-23_pkulaw-real-task-01/comparison.md`。
- **事项目录条款直引完成**：不再依赖 pdfplumber 自动表格识别的固定格号，改为从
  PDF 原始竖线聚类出逐页 8 列，并按跨表宽的横线定位事项；单元格内部被横线切开
  的半句按“事项名称列以『对』起头”并回整行，126 个跨页事项按相邻页同序号合并。
  2023 版由旧口径 2,839 条修正为 **3,877 条**，条款直引 **3,877/3,877（100%）**，
  全部从法定条号起读；`audit_catalog` 扩为 **9/9**。
- **trap-05、trap-07 已解决**：识别“关于修改《X》的决定”是修改工具而非 X
  正文，普通事实问法不再把它当候选；用户点名已废止法规时，同时召回原法规和
  废止依据指向的现行替代法规。树检索与条文检索共用点名状态提示和确定性失效
  兜底，`audit_reasoning` 增至 **17/17**；完整 84 题在线复跑 **84/84**。
  结果证据见 `haikou/eval_results_v4.json` 和
  `test_runs/2026-11-23_trap-fix/`。
- **项目测试批次基础设施落地**：新增 `tools/run_test_round.py`，每条命令独立
  捕获 stdout／stderr、退出码、耗时和环境快照，结果写到
  `test_runs/<日期>_round-NN/`，避免测试输出混入题集或正文。首轮
  `2026-11-22_round-01` 保留为修复前基线，`round-02` 修复后自研核心 19/19、
  在线 Provider 和真实 PageIndex 客户端审计全部通过。此前的评测、盲评、
  时点核对、抽检与维护日志已统一快照到 `test_runs/legacy-archive/`
  （52 个文件，带来源路径和 SHA-256）。
- **评测集扩到 84 题**：v2 30 题＋v3 50 题＋v4 路由覆盖 4 题，新增精确条号、
  多文档、事项目录和历史时点题；当前模型输出在线复跑 84/84，trap-05、trap-07
  的“修改决定替代原法规”问题已修复。评分器修正否定句、
  `llm_error`、部分失效条文和静默换文件等误判，并默认校验模型、题集、
  代码和语料指纹，拒绝旧缓存冒充本轮结果。
- **三方案对比完成**：历史快照中效力层 82/84，
  普通本地 RAG 49/84，直接模型 3/84；内容要点分别为 57/57、51/57、23/57，
  已废止陷阱分别为 21/21、4/21、0/21。当前修复后的 validity-QA 已单独复跑到
  84/84。该结果说明差异主要来自证据与效力约束，
  但仍是内部测试集，且直接模型没有联网搜索能力，不能替代真实用户盲评。
- **盲评基础设施完成**：生成双人盲评包和 Cohen κ 汇总器。所有者已确认 A/B 全部
  通过，但两表同源，报告明确标记为非独立盲评。
- **时点证据完整性第一版落地**：正式 `_temporal_index.json` 覆盖 1,248 份；
  明确施行日 635 份、政策发布型 305 份、需人工补证 156 份。
- **模型 Provider 解耦完成**：DeepSeek／OpenAI／OpenAI-compatible／本地模型可配置切换，
  主业务和 PageIndex 建索引已收口到同一 Provider 层。
- **自适应路由已核对**：七类策略、候选数 4–10 自适应和前置拒答均落地，路由审计
  13/13；但尚未实现“第一轮证据不足后自动换路”的反馈式重路由。
- **条文级部分废止落地**：从修改决定中提取“删去/删除第X条”和只删款、项的事件，
  生成独立索引；检索目录、选条、作答、树节点与 PageIndex 适配器都使用条文级状态，
  不再把“文档整体有效”等同于“每条都有效”。
- **用户服务入口完成**：本机 Web 页面和异步 JSON API 已落地，支持提问、历史时点、
  效力状态、拒答原因和引用来源展示；服务审计 22/22。
- **版本管理完成**：建立 Git 基线，`PageIndex/` 固定为 MIT 上游子模块；项目已合并
  远程初始提交并推送到 GitHub，`main` 与 `origin/main` 同步。

---

## 已完成的事项

### 1. 效力层（`warn.py` / `force_layer.py`）

- 三态+一档判定：现行有效／效力存疑／已废止／系列已废止
- 双来源废止：专门废止通知 + 文件正文「同时废止」条款
- 每份判定带 `reason / checked / missing / next_steps / evidence / caveats`
- 名称匹配分档（`warn.name_strength`）：`exact/contained` 才判「已废止」；
  `version/superset` 降为「系列已废止＋请核对版本」
- 名称闸门（`warn.is_usable_name`）：拒绝「条例」「办法」这类简称和过短名称

### 2. 效力推理协议（`force_protocol.py`，项目 spine）

- 状态含义、选条规则、作答规则、输出契约**一处定义、三种渲染**（select／answer／agent）
- 三条后端共用：条文检索、沿树检索、PageIndex 适配器
- 只依赖 `warn` 与标准库，依赖方向单向

### 3. 检索

- 条文级二元组倒排（`retrieval.py`）
- **无条文文档按句切块**（~180 字/块），检索覆盖 654 → **1,244 份**
- **题名通道**：点名的文档直接排第一（此前问文件名会命中别的文件）
- **繁简归一**：`zhconv` 装在 `haikou/_vendor`，繁体查询可命中
- **持久化索引**：语料指纹校验，语料一变自动重建
- **策略路由**（`retrieval_router.py`）：

| 策略 | 触发 | 路径 |
|---|---|---|
| `article_exact` | 点名文件 + 第X条 | 条文索引 |
| `single_doc_tree` | 点名一份有条文结构的文件 | 沿树 |
| `single_doc_flat` | 点名一份无条文结构的文件 | 句块 |
| `multi_doc` | 比较/沿革/废止关系 | 多文档（候选 4–10 自适应） |
| `default_flat` | 其余 | 条文+段块 |
| `catalog_item` | 执法事项/指导目录 | 事项级索引 |
| `out_of_scope` | 非海南辖区等 | 拒答 |

### 4. 树与 PageIndex

- `tree_index.py`：按 PageIndex 形状建树（页码跨度、node_id、章→条），节点带 force
- `tree_retrieval.py`：按 node_id 沿树选点、按点读正文
- `pageindex_force_adapter.py`：包装 `get_tree/get_ocr/get_document`，三处注入 force
- **真实 PageIndexClient 验证**：3 份文档按文号入库（琼府办〔2021〕71号／令第80号／令第329号），
  45/11/24 个节点全部带 force，含 6 个嵌套节点（最深 2 层）
- `build_pi_store.py`：txt→带版式中文 PDF（字号+书签）→ 真实本地索引；manifest 以**文号**为主键

### 5. 表格型目录（`catalog_index.py`）

- 网页正文是列式摊平的，改从**官方 PDF 附件**用字符坐标还原逐页 8 列
- 列边界直接聚类 PDF 原始竖线，不再假定 `find_tables()` 永远返回同一种合并格
- 同一事项被单元格内部横线切开时，按事项名称列的新行起点并回整行
- 相邻页重复同一总序号/分序号时判为续排，合并法律名称、条款和实施主体原文
- 2023 版：1,625 页 → **3,877 个事项**，事项名形态合格 **99.9%**
- 条款可直引 **3,877/3,877（100%）**，且全部从法定条号起读；126 个跨页事项保留续页条款
- 回答来源带 PDF 页码、法律名称与条款原文；Web 来源卡片直接展示可回查的引文
- 路由：`catalog_item`；无匹配 → `catalog_empty` 拒答
- 实测：「对未经登记擅自设立代表机构怎么处罚」→ 正确给出 5 万–20 万罚款、法律名、主体、效力状态

### 6. 评测与审计

- `eval_set.json`：v2 基础 30 题；`eval_set_v3_additions.json`：新增 50 题；
  `eval_set_v4_additions.json`：再补 4 道路由与时点题。合并后 **84 题**
  （48 正题／21 陷阱／6 语料外／8 存疑／1 历史时点）。
- `eval_results_v4.json`：84 题模型回答的独立结果文件；每题保存模型、题集、
  代码、语料指纹和生成时间，不把测试输出写回题集或文档。
- `eval_qa.py`：默认合并 v2＋v3＋v4，三条指标（答案对／引用合规／现行有效题只引
  现行有效）；新增 `--set`、`--results`、离线 `--validate-data` 和
  `--allow-stale`，默认拒绝旧缓存冒充当前结果。
- 审计：`spine_check`、`audit_router`、`audit_catalog`、`audit_tree_force`、
  `audit_tree_retrieval`、`audit_reasoning`、`selfcheck`、`spotcheck`、`score_v2`、
  `stability_check`、`verify_evidence`、`audit_repeal_targets`
- 模型调用有 3 次重试（2s/4s 退避），最终失败返回 `refusal_stage="llm_error"`

### 7. 已修掉的关键误判（新对话务必知道）

1. **依据书当被废止对象**：「为贯彻落实《公平竞争条例》…决定废止《农药办法》」
2. **简称当文件名**：「以下简称《条例》」→ 所有含「条例」的文档被误判已废止（237 → 35）
3. **文件废止自己**：新版通知写「旧版《同名办法》同时废止」→ 现行新版被判死
4. **同名新旧版混淆**：洋浦（2010 vs 1992）、热带雨林（正式 vs 试行）
5. **表格目录当条文**：一份指导目录被切成 7,016 条「条文」
6. **章标题不关闭当前条**：整章内容被上一条吞掉
7. **条号抽取瑕疵**：「第十—条」（破折号）、「第三十五 条」（空格）
8. **meta Description 当正文**：812 份里 603 份存的是新闻导语（语料从 15.7 万字修到 887 万字）
9. **文号折行认不出**：「海南省人民\n政府令第329号」
10. **索引不持久化 / 繁简不分 / 问文件名命中别的文件**

### 8. 时点检索（as-of-date）

- 入口同时支持显式 `--as-of` 和问题中的“截至某年某月时／某年时”。
- `temporal.py` 从文档文头识别**文头日期**与紧邻“起施行／执行／生效”的
  **明确施行日**；不会把同一句里的公布日误当成施行日。
- 若查询时点早于明确废止生效日，精确废止文档在该时点判为现行有效；到达废止日
  后恢复为已废止。历史状态不会污染实时效力缓存。
- 月份／年份按完整区间判断：区间跨过施行日或废止日时，降为“效力存疑”，并提示
  需要具体到日；未来时点直接拒绝。
- 缺少明确施行日、系列版本无法确认或废止日缺失时，一律保守降为存疑，不把
  “没找到废止依据”升级成确定有效。
- 时点上下文进入同一份 `force_protocol`：选条、作答、沿树检索、事项目录与
  PageIndex 适配器都要求写明“在该时点”，不得改写成“目前有效”。
- 全量 1,248 份中，明确施行日已从 478 提升到 **635 份**；另有 305 份规划、
  通知、政策措施按发布日处理，不强行要求施行日；剩余需施行日但缺失的 **156 份**
  一律如实降为存疑。

### 9. 80 题双人盲评基础设施

- `blind_review_v3/reviewer_A.csv`、`reviewer_B.csv`：固定随机种子、相同题序的
  两份独立评审表；只含题面、候选回答、引用、原文证据和空白评分栏。
- `coordinator_key.json`：由协调者保管，含原题号、题型、人工真值和自动判定；
  不进入评审表。
- `score_blind_review.py`：收齐两份表后计算逐维一致率、Cohen κ、自动评分与
  人工共识差异，并输出 Markdown／JSON 报告。
- 所有者已确认 A/B 两表全部通过，汇总结果是 `completed_owner_attested_not_independent`；
  自动评分与所有者共识 80/80 一致。由于两份表来自同一人，Cohen κ 1.000 不能解释为
  两名独立评审者一致。

### 10. 时点证据索引与人工补证

- `_temporal_index.json`：数据库中的正式持久化索引，覆盖 1,248 份；每份含文件类型、
  是否要求施行日、公布／施行／废止日期、来源句、置信度、缺失项和语料指纹
  （文件数、字节数与 mtime 签名）。
- 类型已拆分：地方性法规、规章／规范性文件、决定、废止／修改决定、通知、规划、
  政策措施、未分类。规划／通知不再因没有施行日被误当成低质量法规。
- `temporal_evidence.py` 扫描完整正文，支持“自某日起施行”、
  “自公布之日起施行＋明确公布日”、通过日、有效期和 URL 月份等证据。
  URL 月份绝不单独升级为施行日。
- `ForceLayer` 在时点查询时优先读取证据索引；索引指纹与当前语料不一致时自动弃用，
  回退到正文保守解析，避免使用过期日期。
- `_temporal_overrides.json`：人工核对覆盖入口；必须记录核对人、日期、官方 URL、
  证据句和说明。`temporal_review_v1/review_queue.csv` 已把剩余 156 份按 P0–P2
  排序，供逐份补证。
- `audit_temporal_coverage.py` 12 项全过：索引覆盖、指纹、人工覆盖、类型分层、
  日期边界、来源句、URL 月份防误用及运行时行为。

### 11. 模型 Provider 解耦

- `llm_provider.py`：统一 `ChatProvider.complete()` 接口；DeepSeek、OpenAI、
  OpenAI-compatible 服务和本地模型均由配置选择。
- 请求 URL、模型名、密钥、超时、重试和响应解析只存在于 Provider 层；主问答、
  沿树检索、事项目录与评测代码不再出现 DeepSeek URL／模型硬编码。
- 配置优先读取 `haikou/.env`，不存在时兼容旧的 `PageIndex/.env`；模板在
  `llm_provider.example.env`。支持 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL`、
  `LLM_API_KEY`、`LLM_API_KEY_ENV`、`LLM_TIMEOUT`、`LLM_RETRIES`。
- 认证错误不重试，429／5xx 和网络错误统一退避重试；配置展示自动脱敏。
- `build_pi_store.py` 也改为从同一 Provider 配置映射到 PageIndex／litellm。
- `audit_provider.py` 10 项全过；现有 `chat_fn` 注入接口保持兼容。

### 12. 条文级部分废止

- `article_force.py` 定义独立于文档效力的条文事件：整条删除为“条文已废止”，
  只删除款、项为“条文部分失效”，二者都不改写成“整份文件已废止”。
- `build_article_force_index.py` 从修改决定中识别目标文件、删除条款和生效日期；
  多目标决定按“一、对《X》作出修改”分段，避免把甲的修改条款挂到乙。
- 索引为 `_article_force_index.json`，含语料指纹。当前识别 **60 个事件、35 份修改
  决定、57 份目标文档、313 个条文目标**；32 份未修订旧文本可直接应用。
- 已在正文中吸收该次修改并重新公布的 5 份文本不套用旧条号，防止后续条文重新
  编号后把现行“第X条”误判为已删除。
- 历史时点按修改决定生效日切换；月／年区间跨越变更日、或决定日期无法识别时降为
  存疑。文档整体状态保持原值，避免单条变更污染全文判定。
- `reasoning_retrieval` 的带效力目录会逐条标出“条文已废止/部分失效”；沿树节点、
  PageIndex 工具结果、回答提示和 Web 来源均携带同一份状态。
- `audit_article_force.py` 28 项全过，覆盖解析、关联、整条/部分删除、历史时点、
  重新公布文本、检索/作答/树/PageIndex、`drop`/`strict` 策略与真实语料样例。

### 13. 面向用户的服务入口

- `service.py` 用 Python 标准库提供同源 Web 页面、异步任务 API 和健康检查，不引入
  Web 框架依赖；默认监听 `127.0.0.1:8787`。
- `web/index.html`、`web/app.js`、`web/app.css` 支持政策提问、可选历史时点、效力
  状态、引用来源和检索说明；HTML 同时使用 UTF-8 BOM、Meta 与 HTTP 响应头编码。
- 长模型调用以任务方式后台执行，页面轮询 `GET /api/ask/{id}`；提供请求体大小限制、
  JSON 输入校验、错误脱敏、CSP 和本机监听默认值。
- `audit_service.py` 22/22 全过；真实 DeepSeek 接口曾完成异步问答验证。

### 14. Git、远程仓库与软著边界

- 项目根目录已建立 Git 仓库，主分支 `main` 跟踪
  `https://github.com/yang-0315/haikou-code.git`。
- 首次可运行基线提交为 `225f9c5`，标签为 `baseline-2026-11-22`；远程原有
  `Initial commit` 已通过合并提交保留，没有覆盖远程历史。
- `PageIndex/` 作为 MIT 上游子模块引用，固定上游提交 `9c4c3ff`；上游许可证保留在
  `PageIndex/LICENSE`。
- 语料库、`.env`、虚拟环境、`haikou/_vendor/`、浏览器调试目录和大型生成索引不进入
  Git；大型语料需独立备份。
- `THIRD_PARTY_NOTICES.md` 记录第三方边界，
  `docs/SOFTWARE_COPYRIGHT_SCOPE.md` 给出现有自研代码的软著建议申报范围。
- 项目当前没有对自研代码授予开源许可证，对外分发前需单独确定许可证。

---

## 后续待办

### 产品主线（优先）

1. **完成持续更新闭环**：为 17 个 pending 候选抓取正文/附件，保存页面与正文
   哈希，生成版本差异，接入审核发布和受影响索引事务重建。
2. **官方补证工作台**：把日期缺失、废止依据、修改依据、版本谱系和条目变化
   统一进可审核队列，记录官方 URL、证据句、核对人和时间。
3. **执法事项问答持续更新**：普通自然语言处罚题自动进入事项目录；补齐
   2024／2022 版目录并关联政策变化。
4. **更新后回归**：每次发布自动重跑当前效力、历史时点、目录直引和 QA 新鲜度。
5. **真正真实任务比较**：在北大法宝同等订阅权限下重跑真实任务；补足独立盲评。
6. **剩余一般数据债**：83 份时点日期、515 份文号、反馈式二次路由和“修改为”差异。

### 软著收尾（并行）

1. 冻结 V1.0 名称、版本、功能清单和自研源码边界；
2. 生成源码清单、SHA-256、50 行/页源码 PDF 和前后 30 页；
3. 完成用户说明书和至少十类真实截图；
4. 复核 PageIndex、依赖、语料、`_vendor` 和密钥边界；
5. 建立申报标签与源码指纹，提交并保存申请号和回执。

---

## 常用命令

```powershell
# 一轮完整测试（建议使用主运行时；加 --upstream 同时跑 PageIndex pytest）
python tools\run_test_round.py
python tools\run_test_round.py --upstream

# 问答（自动路由）
python haikou\reasoning_retrieval.py "免税店的设立需要什么条件" --show-trace
python haikou\reasoning_retrieval.py "对未经登记擅自设立代表机构怎么处罚" --show-trace

# 时点问答：截至 2023 年末，这篇已废止文件当时是否有效
python haikou\reasoning_retrieval.py "三亚市河道生态保护管理条例第一条是什么" --as-of 2023-12-31
python haikou\reasoning_retrieval.py "三亚市河道生态保护管理条例" --as-of 2023年 --dry-run

# 沿树目录按历史时点刷新
python haikou\tree_retrieval.py "三亚市河道生态保护管理条例第一条" --as-of 2023-12-31 --dry-run

# 只看路由决定
python haikou\retrieval_router.py "海南省消防条例和自贸港消防条例有什么区别"

# 评测（默认合并 v2 30 题＋v3 新增 50 题＋v4 路由覆盖 4 题，共 84 题）
python haikou\eval_qa.py
python haikou\eval_qa.py --run
python haikou\eval_qa.py --validate-data
python haikou\eval_qa.py --allow-stale
python haikou\eval_qa.py --run --only eq-38,eq-43,qst-06
python haikou\eval_qa.py --show eq-01

# 三方案对比（直接模型／普通 RAG／效力层）
python haikou\compare_baselines.py --workers 4
python haikou\compare_baselines.py --reuse --output test_runs\2026-11-22_comparison-01

# 官方政策更新扫描：默认 dry-run，仅生成待审核候选
python haikou\policy_update_scan.py --max-pages 1
python haikou\policy_update_scan.py --max-pages 1 --apply

# 人工盲评：生成双人评审包，收齐后计算一致性
python haikou\build_blind_review.py
python haikou\score_blind_review.py

# 审计（全部离线可跑，树/真实客户端各有一项需要本地索引）
python haikou\spine_check.py
python haikou\audit_router.py
python haikou\audit_catalog.py
python haikou\audit_tree_force.py
python haikou\audit_tree_retrieval.py
python haikou\audit_reasoning.py
python haikou\audit_temporal.py
python haikou\audit_temporal_coverage.py
python haikou\audit_provider.py
python haikou\audit_article_force.py
python haikou\audit_service.py
python haikou\score_v2.py
python haikou\stability_check.py

# 事项目录
python haikou\catalog_index.py --build --only 2023
python haikou\catalog_index.py --build --only 2024
python haikou\catalog_index.py --build --only 2022
python haikou\catalog_index.py --stats

# 时点日期与文号数据债
python haikou\data_debt.py audit --samples 12
python haikou\data_debt.py build-numbers
python haikou\data_debt.py build-number-review
python haikou\audit_data_debt.py

# 语料维护
python haikou\fill_library.py --limit 20      # 补抓缺档（--apply 才写）
python haikou\refresh_bodies.py --limit 20    # 重刷正文（--apply 才写）
python haikou\embedded_repeals.py             # 重扫文件内废止条款
python haikou\tree_index.py --build           # 重建带效力树

# 时点证据索引与人工补证
python haikou\build_temporal_index.py
python haikou\build_temporal_review_queue.py
python haikou\import_temporal_review.py       # 填完队列后才运行

# 条文级部分废止
python haikou\build_article_force_index.py
python haikou\audit_article_force.py

# 废止清单数量与官方附件补全
python haikou\repair_repeal_index.py --fetch --apply

# 模型 Provider 配置与连通性
python haikou\llm_provider.py --show-config
python haikou\llm_provider.py --chat "只回复 OK"

# 面向用户的服务
python haikou\service.py

# Git 状态与远程同步
git status --short --branch
git log --oneline --decorate -5
git push origin main
```

---

## 环境与注意事项

- **数据库路径已统一解析**：优先环境变量 `POLICY_DATABASE`，其次兼容旧
  `C:\Users\Lenovo\Desktop\database`，最后使用当前 `PageIndex\database`。索引中的
  旧外置绝对路径会自动映射到移动后的工作区副本。
- **Python 解释器**：
  - 主运行时：`C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
    （有 `pypdf` / `reportlab` / `pdfplumber` / `zhconv`（`_vendor`））
  - PageIndex venv：`C:\Users\Lenovo\Desktop\PageIndex\PageIndex\.venv\Scripts\python.exe`
    （有 `litellm` / `PyPDF2`，**没有** `reportlab`）
- **沙箱执行器经常报 `helper_unknown_error`**：改用 `require_escalated` 执行。
- **模型配置**：优先 `haikou\.env`，兼容旧 `PageIndex\.env`；参见
  `haikou\llm_provider.example.env`。DeepSeek key 变量为 `DEEPSEEK_API_KEY`。
- **Git 仓库**：外层项目已建立 `main` 基线；`PageIndex/` 作为上游 MIT 子模块，
  语料库、虚拟环境、密钥和大型索引不纳入版本控制。远程为
  `https://github.com/yang-0315/haikou-code.git`，已推送 `main` 和
  `baseline-2026-11-22` 标签。重要功能完成后提交并同步远程。
- **GitHub 推送网络**：本机直连 GitHub 间歇性超时；成功推送使用本机 SOCKS5
  `127.0.0.1:10808`。若新会话推送超时，可检查该代理是否监听，并使用 Git 的
  `http.proxy=socks5h://127.0.0.1:10808` 临时配置；不要把代理或凭据写进仓库。
- **索引重建**：批量改语料后第一次查询会现场重建（约 6.5s），之后 0.68s；也可先跑一次查询「烘」索引。
- **时点证据边界**：正式索引覆盖 1,248 份；明确施行日 752 份，剩余 83 份需人工补证。月份／年份跨过施行或废止日时降为存疑，URL 月份不会单独充当施行日。
- **写文件约定**：本项目一直用 `apply_patch` 编辑；不要用 `cat`/重定向写文件。

---

## 最近一次验证（2026-11-23）

```
目录直引            2022/2023/2024 三版 11,602 个事项；各版条款直引 100%
data debt           明确施行日 752，缺口 83；文号 733，补证队列 515
catalog regression   audit_catalog 10/10；三版各自形态、直引、续页和路由通过
目标陷阱复跑        trap-05、trap-07、trap-09 均通过；两条后端共用失效兜底
project test round   2026-11-23_round-06；最终离线 21/21 项，单项检查 222/222
core audits          tree-force 10/10（真实客户端无跳过），其余审计全过
update workflow      27/27；取证不写库、审核闸门、CSV 导入、重建、回滚
validity-QA v4       84/84（答案对 57/57、引用合规 84/84、正题 48/48；陷阱 21/21）
result freshness     dataset/code/corpus/model metadata verified
route coverage       article_exact / multi_doc / catalog_item / as_of each 1/1
baseline comparison  force-aware 82/84；plain RAG 49/84；direct LLM 3/84
web manual           wm-01 正确；wm-03/wm-04 默认联网回答误用旧法；wm-03 明确核验后通过
copyright originality 37 files / 13,911 lines；PageIndex common non-trivial lines 4
workspace cleanup    released about 487 MB；source/data/test artifacts retained
PageIndex upstream   668 passed／77 skipped／5 failed
upstream failures    4 项为 Windows 符号链接或文件名测试前提；1 项同名并发入库稳定失败
spine_check          6/6
audit_router        13/13
audit_catalog        10/10
audit_data_debt      15/15
audit_tree_force    10/10
audit_tree_retrieval 8/8
audit_reasoning     19/19
audit_temporal      13/13
audit_temporal_coverage 12/12
audit_provider      10/10
audit_article_force 28/28
audit_service       27/27
audit_repeal        23/23
audit_update_workflow 27/27
git worktree        modified（本轮修复尚未提交）
git remote          main = origin/main
github repo         https://github.com/yang-0315/haikou-code
validity-QA         84/84（答案对 57/57、引用合规 84/84、现行有效题 48/48；陷阱 21/21）
live provider       DeepSeek 经统一 Provider 调用返回 OK
data validation     84/84 题引用、条文效力与 expected_route 通过
owner acceptance    v3 80/80（A/B 同源转录；非独立盲评）；v4 新增 4 题尚未盲评
live as-of          2023-12-31 问答正确标注“历史效力，非当前效力”
stability_check      无反向放宽
```

测试原始输出见 `test_runs/2026-11-23_round-06/summary.md` 和同目录逐项日志；
round-01／round-02 保留为修复前和中间基线。测试批次目录按 `.gitignore` 保留在
本机但不进入版本控制。
