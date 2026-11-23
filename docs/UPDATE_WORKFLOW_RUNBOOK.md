# 政策更新工作流运行手册

本手册对应 V1.0 冻结前的更新闭环。扫描、取证、审核和发布是四个独立阶段；
自动扫描不能直接改变政策正文或效力状态。

## 1. 状态与文件

| 状态 | 含义 | 是否进入政策库 |
|---|---|---|
| `pending` | 官方列表发现差异，尚未取证 | 否 |
| `staging` | 正在抓取页面和附件 | 否 |
| `staged` | 页面、正文、附件和哈希已保存，等待人工补证 | 否 |
| `stage_error` | 页面或附件无法得到足够正文 | 否 |
| `verified` | 核对人、时间、官方证据和发布动作齐全 | 否 |
| `published` | 已通过事务化发布并重建派生索引 | 是 |
| `rejected` | 人工确认不是应发布的变化 | 否 |

主要文件：

- `database/_update_ledger.json`：扫描、取证、审核和发布账本；
- `database/update_review.csv`：业务审核队列；
- `database/_update_workflow/<scan_id>/<candidate_id>/`：页面、正文、附件、
  差异和哈希快照；
- `database/_update_publications.json`：成功发布记录；
- `database/_update_publish.lock`：跨进程发布锁；
- `database/_update_workflow/publications/<publication_id>/`：发布前备份。

## 2. 扫描

默认只读：

```powershell
$python = "C:\path\to\python.exe"
& $python haikou\policy_update_scan.py --max-pages 1
```

写入账本和审核队列：

```powershell
& $python haikou\policy_update_scan.py --max-pages 1 --apply
```

同一官方 URL 的候选 ID 在重复扫描中保持稳定，已经完成的审核状态不会被普通
重扫清除。只有官方页面或正文哈希实际变化时，旧审核才失效。

## 3. 官方取证

```powershell
& $python haikou\update_workflow.py list
& $python haikou\update_workflow.py stage --max-attachments 5
```

只处理指定候选：

```powershell
& $python haikou\update_workflow.py stage --id CANDIDATE_ID
```

取证阶段写入：

- 官方 HTML 页面；
- 页面 SHA-256；
- 抽取正文及正文 SHA-256；
- 附件原始文件、字节 SHA-256 和可抽取文本；
- 与现有缓存正文的统一差异；
- 页面标题、发布日期和发文机关；
- 重复正文提示和候选发布动作建议。

该阶段不会修改 `_all_documents.json`、`library/` 或任何检索索引。

### 附件无法解析

当前支持 PDF、DOCX 和页面正文。旧式 `.doc`、二进制 `.wps`、OFD 等格式只保存
原文和哈希，候选保持 `stage_error`，不得绕过正文证据直接发布。应在人工核对后
通过同一工作流补充可审计正文，或等待后续格式解析器。

## 4. 人工审核

两种录入方式等价，都必须给出核对人、官方证据句和发布动作。

命令行：

```powershell
& $python haikou\update_workflow.py review `
  --id CANDIDATE_ID `
  --decision verified `
  --by "审核人" `
  --evidence "官方页面中的证据句" `
  --action publish_new
```

CSV：

1. 在 `database/update_review.csv` 填写 `review_status`、`reviewed_by`、
   `evidence`、`action` 和 `notes`；
2. 导入：

```powershell
& $python haikou\update_workflow.py import-review
```

`review_status` 只接受：

- `verified`：通过，`action` 必须为 `publish_new`、`replace_body` 或
  `record_only`；
- `rejected`：拒绝；
- `hold`：继续暂缓。

没有正文快照、官方 URL、核对人或证据的候选不能通过审核。

## 5. 发布

```powershell
& $python haikou\update_workflow.py publish --id CANDIDATE_ID
```

发布事务执行以下步骤：

1. 获取跨进程锁；
2. 备份目录、正文、更新账本、审核队列和全部派生索引；
3. 合并目录并写入新正文；
4. 将已核实的废止通知合并到废止索引；
5. 重建内嵌废止、条文效力、时点、检索、带效力树和文档效力索引；
6. 写入发布日志和候选状态；
7. 任一步失败时恢复全部备份并释放锁。

发布期间，Web 服务拒绝提交新的问答任务，避免查询读取半完成状态。发布完成前
不会把候选显示为 `published`。

## 6. 前端查看

启动服务：

```powershell
& $python haikou\service.py
```

页面中的“最近发现的变化”读取 `GET /api/updates`，只展示公开信息，不返回本地
路径、正文快照文件或核对人个人信息。

## 7. 冻结前验收

```powershell
& $python haikou\policy_update_scan.py --selftest
& $python haikou\update_workflow.py selftest
& $python haikou\audit_update_workflow.py
& $python haikou\audit_service.py
```

更新工作流审计覆盖候选身份、官方来源限制、页面/附件哈希、取证不写库、审核
闸门、CSV 导入、并发锁、全量索引重建、重复发布拒绝和失败回滚。

## 8. 当前真实扫描边界

2026-11-23 对六栏目首页扫描得到 17 个候选。首次真实取证后：

- 16 个候选形成可用正文快照；
- 1 个候选正文只有页面简短通知，官方正文位于不可解析的 `.wps` 附件；
- 2 个候选正文哈希相同，系统将其中一个标记为跨栏目重复；
- 17 个候选均未通过人工证据审核，因此发布数为 0。

这些状态是真实数据事实，不得为了“全部通过”而降低门槛。
