# 海南政策更新与执法问答平台

面向海南自由贸易港政策文本的持续更新、效力追踪、官方补证与执法事项问答平台。
问答继续保留，但它是持续维护的政策库的前台入口；产品本体是让政策库跟随官方
法规更新，并让每次变化都有官方证据、版本记录和审核轨迹。

**当前最终目标：申请软件著作权。** 登记对象收敛为“海南自由贸易港政策法规
效力感知检索与问答系统 V1.0”。申报范围和执行步骤见
[`docs/SOFTWARE_COPYRIGHT_APPLICATION_PLAN.md`](docs/SOFTWARE_COPYRIGHT_APPLICATION_PLAN.md)。

```text
官方渠道更新
 → 新增/正文/附件/废止/修订检测
 → 官方原文与证据补证
 → 版本化发布与效力重算
 → 重建政策、效力、事项和树索引
 → 通用政策问答 / 变化问答 / 执法事项问答
 → 繁简归一与策略路由
 → 字符二元组混合召回
 → 文档效力 + 时间效力 + 条文级部分废止
 → 模型读取带效力的目录并选条
 → 回读具体条文
 → 带出处、效力说明作答；证据不足则拒答
```

## 当前状态

- 语料：1,248 份政策正文。
- 检索：43,642 个条文与段块单元。
- 文档效力：现行有效、效力存疑、已废止、系列已废止。
- 条文效力：条文已废止、条文部分失效。
- 时点检索：支持按日、月、年重算效力。
- 条文效力索引：60 个事件、57 份目标文档、313 个条文目标。
- 政策更新账本：已建立；首轮官方首页扫描发现 17 个待审核候选。
- 执法目录：2022／2023／2024 三版共 11,602 个事项，各版条款直引 100%。
- 时点日期：明确施行日 752 份，剩余 83 份进入官方补证队列。
- 文号：733 份可提取，515 份进入逐条文补证队列。
- validity-QA：v4 当前代码在线复跑 **84/84**，覆盖精确条号、多文档、事项目录
  和历史时点；trap-05、trap-07、trap-09 的旧法/修改决定误用已修复；
  结果带模型、题集、代码和语料指纹。v3 的 80 题 A/B 仍为同一所有者转录，
  不构成独立盲评。
- 条文级部分废止审计：28/28。
- Web 服务审计：27/27。

产品主线与更新闭环见
[docs/PRODUCT_DIRECTION.md](docs/PRODUCT_DIRECTION.md)，
架构主线见 [SPINE.md](SPINE.md)，完整进度和已知边界见
[progress.md](progress.md)，核心模块说明见
[haikou/README.md](haikou/README.md)。


## 目录

| 路径 | 作用 |
|---|---|
| `haikou/` | 自研效力层、检索、路由、推理协议、Web 服务与审计 |
| `chinastruct/` | 中文 PDF 结构层；当前语料以网页文本为主 |
| `PageIndex/` | MIT 上游子模块，不改动上游代码 |
| `database/` | 本地语料与大型索引，不进入 Git |
| `plan/` | 项目计划生成与检查工具 |

## 启动

模型配置优先读取 `haikou/.env`，模板见
`haikou/llm_provider.example.env`。

```powershell
cd C:\Users\Lenovo\Desktop\PageIndex
python haikou\service.py
```

打开 `http://127.0.0.1:8787/`。

数据库路径按以下顺序解析：

1. 环境变量 `POLICY_DATABASE`
2. 旧外置目录 `C:\Users\Lenovo\Desktop\database`
3. 当前工作区目录 `PageIndex\database`

## 验证

```powershell
# 一轮完整测试：保存命令、耗时、退出码和原始输出
python tools\run_test_round.py

# 三方案对比：效力层 / 普通 RAG / 直接模型
python haikou\compare_baselines.py --workers 4

python haikou\policy_update_scan.py --max-pages 1       # 官方首页只读扫描
python haikou\policy_update_scan.py --max-pages 1 --apply # 写入待审核账本
python haikou\update_workflow.py list                   # 查看最新候选
python haikou\update_workflow.py stage                  # 抓正文/附件与哈希
python haikou\update_workflow.py import-review          # 导入 update_review.csv
python haikou\update_workflow.py publish --id <ID>      # 审核后事务发布
python haikou\audit_update_workflow.py                  # 更新闭环离线审计
python haikou\catalog_index.py --stats                  # 2022/2023/2024 三版目录
python haikou\data_debt.py audit                        # 时点与文号数据债分类
python haikou\data_debt.py build-number-review          # 文号人工补证队列
python haikou\audit_data_debt.py                        # 三版目录/日期/文号审计

python haikou\spine_check.py
python haikou\audit_router.py
python haikou\audit_reasoning.py
python haikou\audit_article_force.py
python haikou\audit_temporal.py
python haikou\audit_tree_retrieval.py
python haikou\audit_service.py
```

主运行时不带项目依赖时，请使用 `progress.md` 中记录的解释器执行。测试证据写到
`test_runs/<日期>_round-NN/`；加 `--upstream` 可同时运行 PageIndex 上游测试。
更新取证、审核、发布和回滚的完整步骤见
[`docs/UPDATE_WORKFLOW_RUNBOOK.md`](docs/UPDATE_WORKFLOW_RUNBOOK.md)。
目录版本、时点日期和文号的修复口径见
[`docs/DATA_DEBT_RUNBOOK.md`](docs/DATA_DEBT_RUNBOOK.md)。

## 仓库范围与权利

本仓库未对自研代码授予开源许可证。对外分发前应结合项目用途选择并补充明确的
许可证。PageIndex 子模块和第三方依赖仍适用各自许可证，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

政策语料、生成的检索索引和本地模型密钥不进入本仓库。软著材料的建议申报范围见
[docs/SOFTWARE_COPYRIGHT_SCOPE.md](docs/SOFTWARE_COPYRIGHT_SCOPE.md)。

## 初始化子模块

```powershell
git submodule update --init --recursive
```
