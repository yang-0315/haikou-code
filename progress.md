# 项目进度（截至 2026-11-22）

## 一句话

在 PageIndex 之上做**「法律效力感知」的政策问答与比对系统**：文档的效力状态
（现行有效／效力存疑／已废止／系列已废止）作为推理维度进入检索与作答，而不是
检索完成后再过滤。当前端到端可跑，并支持按历史时点重算效力；语料覆盖约 99%，
核心审计 123 项全过、Web 服务审计 22/22，validity-QA 80/80；代码已同步到
GitHub `yang-0315/haikou-code`。

---

## 目录与分工

| 目录/文件 | 作用 |
|---|---|
| `PageIndex/` | 上游开源项目，**不改动**；真实客户端只在样例索引上验证过 |
| `chinastruct/` | 中文长文档结构层（PDF→树）。当前没有 PDF 语料，**处于闲置状态** |
| `haikou/` | 效力层 + 检索 + 协议 + 路由 + 审计（本项目主体） |
| `database/` | 语料库与索引；解析顺序为 `POLICY_DATABASE` → 旧外置目录 → 当前 `PageIndex\database` |
| `SPINE.md` | 架构主线说明（协议 = 贡献本体，检索后端 = 消费者） |
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
| 事项目录 `_catalog_items.json` | 3.3 MB；2023 版 **2,839 个事项**（配套 PDF 8.2 MB 缓存在 `_catalog_pdfs/`） |
| 条文效力 `_article_force_index.json` | **60 个事件、35 份修改决定、57 份目标文档、313 个条文目标**；32 份旧文本直接应用，5 份已重新公布文本不套旧条号 |

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
| validity-QA（80 题） | **80/80** |
| 其中：正题 47／已废止陷阱 20／语料外 6／存疑 7 | 全过 |
| 答案对（必备证据＋要点齐） | 54/54 |
| 引用合规（无未标注失效引用） | 80/80 |
| 现行有效题只引现行有效条文 | 47/47 |
| 所有者验收 | **80/80 全部通过**；A/B 均为同一所有者确认后转录，**不是独立盲评** |
| 抽检 19 份 | 精确率 100%、召回率 100%、过度标记 2 |
| 审计合计 | **95 项全过**（spine 6／路由 13／事项 7／树 10／沿树 8／推理 16／时点 13／时点证据 12／Provider 10） |
| 时点检索 | 废止前／废止后可切到不同效力；区间跨事件或缺少施行日时保守降为存疑 |
| 时点证据索引 | 1,248 份全部入索引；明确施行日 635；需施行日但缺失 156；政策发布型 305 |
| 模型 Provider | DeepSeek／OpenAI／OpenAI-compatible／本地模型可按配置切换；业务代码无模型 URL／密钥硬编码 |
| 条文级部分废止 | 整条删除标为“条文已废止”；只删款/项标为“条文部分失效”；变更日不足或区间跨越时降为存疑 |
| Web 服务审计 | **22/22** |
| 稳定性 | 0 处反向放宽 |

---

## 本轮进展（2026-11-22）

- **评测集扩到 80 题**：基础 30 题＋新增 50 题，题集和模型输出分别落在独立文件；
  自动评测 80/80，数据契约校验通过。
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

- 网页正文是列式摊平的，改从**官方 PDF 附件**用字符坐标还原 6 列
- 「事项名称｜法律名称」合并格按**每页表头**算出的分界劈开
- 2023 版：1,625 页 → **2,839 个事项**，事项名形态合格 **99%**
- 条款列可直引 **27%**：串行的只给事项名/法律名，并提示核对原件
- 路由：`catalog_item`；无匹配 → `catalog_empty` 拒答
- 实测：「对未经登记擅自设立代表机构怎么处罚」→ 正确给出 5 万–20 万罚款、法律名、主体、效力状态

### 6. 评测与审计

- `eval_set.json`：v2 基础 30 题；`eval_set_v3_additions.json`：新增 50 题，独立保存题面、
  类型、人工真值和依据说明。合并后 80 题（47 正题／20 陷阱／6 语料外／7 存疑）。
- `eval_results_v3.json`：80 题模型回答的独立结果文件，不把测试输出写回题集或文档。
- `eval_qa.py`：默认合并 v2＋v3，三条指标（答案对／引用合规／现行有效题只引现行有效）；
  新增 `--set`、`--results` 和离线 `--validate-data`（检查条文存在、当前效力及陷阱归属）。
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

## 未完成的待办

### 高优先级

1. **条款可直引率 27% → 提高**：表格目录后段章节列位偏移。
   建议：按章节做列模板，或向官方要 Excel 版；当前串行条目不引原文，只给事项名+法律名。
2. **2024／2022 版指导目录未建**：同一套可跑
   `python catalog_index.py --build --only 2024`（各约 2–3 分钟）。
3. **真正独立的第二人盲评**：A/B 已完成所有者确认转录（80/80），但仍需至少一位
   未参与实现的独立评审者填写新的匿名表，才能形成独立的双人一致率与 Cohen κ。
4. **时点日期人工补证 156 份**：自动抽取已达到 635 份明确施行日；剩余队列在
   `temporal_review_v1/review_queue.csv`，按官方来源补填后回灌覆盖索引。

### 中优先级

5. **条文级修改内容补全**：当前覆盖整条删除和款/项删除事件；单纯“修改为”仍按
   文档级有效处理，后续可为被改写的条文保存新旧文本差异。
6. **版本分不清的那批（系列已废止 7 份）**：需要版本谱系 + 文号一起判。
7. **事项索引并入持久化索引**：现在每次查询现场建（2,839 条约 0.2s）。
8. **712 份文档没有文号**（有文号 536 / 1,248）：只能靠标题检索；可补文号抽取。
9. **反馈式二次路由**：当前路由是一次性规则决策；尚未根据第一轮召回或条文质量
   自动切换到另一条检索路并再次尝试。

### 低优先级 / 已知边界

10. 11 份正文本身过短、75 份 <500 字（多为正常短文）。
11. 老式 `.doc` 附件读不了（PDF/DOCX 已支持）。
12. `chinastruct` 闲置：当前没有 PDF 语料，真实 PageIndex 样例只有 3 份文本 PDF（退化为页节点）。
13. 比较题候选数是启发式（点名的文件数 + 显式数量词），模糊说法会退化到 4 份。

---

## 常用命令

```powershell
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

# 评测（默认合并 v2 30 题＋v3 新增 50 题，共 80 题）
python haikou\eval_qa.py
python haikou\eval_qa.py --run
python haikou\eval_qa.py --validate-data
python haikou\eval_qa.py --run --only eq-38,eq-43,qst-06
python haikou\eval_qa.py --show eq-01

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
python haikou\catalog_index.py --stats

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
- **时点证据边界**：正式索引覆盖 1,248 份；明确施行日 635 份，剩余 156 份需人工补证。月份／年份跨过施行或废止日时降为存疑，URL 月份不会单独充当施行日。
- **写文件约定**：本项目一直用 `apply_patch` 编辑；不要用 `cat`/重定向写文件。

---

## 最近一次验证（2026-11-22）

```
spine_check          6/6
audit_router        13/13
audit_catalog        7/7
audit_tree_force    10/10
audit_tree_retrieval 8/8
audit_reasoning     16/16
audit_temporal      13/13
audit_temporal_coverage 12/12
audit_provider      10/10
audit_article_force 28/28
audit_service       22/22
git worktree        clean
git remote          main = origin/main
github repo         https://github.com/yang-0315/haikou-code
validity-QA         80/80（答案对 54/54、引用合规 80/80、现行有效题 47/47）
live provider       DeepSeek 经统一 Provider 调用返回 OK
data validation     80/80 题引用与失效归属通过
owner acceptance    80/80（A/B 同源转录；非独立盲评，Cohen κ 不作独立性证据）
live as-of          2023-12-31 问答正确标注“历史效力，非当前效力”
stability_check      无反向放宽
```
