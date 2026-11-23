# 目录、时点与文号数据债手册

本手册记录 V1.0 冻结前的三类数据债处理口径。自动化只修复能从官方源中明确
读取的事实；源文件没有权威日期或文号时，进入人工补证队列。

## 1. 三版执法事项目录

官方入口和 PDF 附件均已收录：

| 版本 | PDF 页数 | 事项数 | 条款直引 |
|---|---:|---:|---:|
| 2022 年版 | 1,931 | 4,056 | 4,056/4,056 |
| 2023 年版 | 1,625 | 3,877 | 3,877/3,877 |
| 2024 年版 | 1,249 | 3,669 | 3,669/3,669 |

三版合计 **11,602 个事项**。事项检索默认按年份降序，同一问题优先返回最新版本；
每版分别审计事项形态、条款可直引率和法定条号起点。

```powershell
$python = "C:\path\to\python.exe"
& $python haikou\catalog_index.py --stats
& $python haikou\catalog_index.py --build --only "2024年版"
& $python haikou\catalog_index.py --build --only "2022年版"
& $python haikou\audit_catalog.py
```

不同版本禁止并行构建。目录状态使用 `_catalog_items.lock`，每 25 页原子落盘。
如果旧版本因写入中断留下有效 JSON 前缀和无效尾部，可显式恢复：

```powershell
& $python haikou\catalog_index.py --repair-state
```

## 2. 时点日期

时点证据从“需施行日但缺失 156 份”降至 **83 份**，
明确施行日由 635 份提升到 **752 份**。

本次自动修复只使用以下证据：

- 正文明确写出“自某年某月某日起施行”；
- 正文写明“自公布／发布／印发之日起施行”，且同一文件有明确文头日期或公布日；
- 允许 PDF 在“公布之\n日起施行”处折行；
- 仅 URL 月份、无正文日期时不构成有效证据。

仍缺 83 份的分布：

| 原因 | 份数 |
|---|---:|
| 正文无施行语句，但有文头/公布日 | 42 |
| 正文写“自公布之日起”，但没有可用日期 | 27 |
| 只有 URL 月份 | 12 |
| 没有任何日期证据 | 2 |

人工队列：

```powershell
& $python haikou\build_temporal_review_queue.py
& $python haikou\import_temporal_review.py
```

队列位于 `haikou/temporal_review_v1/review_queue.csv`。缺少日期时系统仍保守降为
“效力存疑”，不会把 URL 月份伪装成施行日。

## 3. 文号

正文文号提取由 536 份提升到 **733 份**，待补证队列为 **515 份**。

提取器支持：

- 海南省政府及部门文号，如 `琼府办〔2021〕71号`；
- 政府令，如 `海南省人民政府令第329号`；
- 地方人大公告，如 `三亚市人民代表大会常务委员会公告(2025)第4号`；
- 其他带年份、机关简称和序号的正规文号。

以下内容不得当成本文件文号：

- 书名号后的引用依据文号；
- “根据／依据”引导的参考文件文号；
- 正文中提到的上位法或规划文号。

生成索引和人工队列：

```powershell
& $python haikou\data_debt.py audit --samples 12
& $python haikou\data_debt.py build-numbers
& $python haikou\data_debt.py build-number-review
```

输出：

- `database/_document_numbers.json`：可提取文号、证据句和置信度；
- `haikou/document_number_review_v1/review_queue.csv`：515 份缺失文号补证队列；
- `haikou/document_number_review_v1/manifest.json`：优先数和缺失原因统计。

“缺文号”并不等于所有文件都应有文号。队列中的通知、答复、规划或公开材料需由
审核人确认是否确实存在正式文号；不得从标题推造。

## 4. 验收

```powershell
& $python haikou\audit_catalog.py
& $python haikou\audit_temporal.py
& $python haikou\audit_temporal_coverage.py
& $python haikou\audit_data_debt.py
```

`audit_data_debt.py` 同时检查三版目录、PDF 折行日期、URL 月份降级规则、引文
文号排除、目录状态恢复与并发锁。
