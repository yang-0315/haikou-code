# validity-QA 80 题双人盲评

本目录用于独立人工盲评。评审者只填写自己的 CSV，不得查看或修改：

- `eval_set.json`
- `eval_set_v3_additions.json`
- `eval_results_v3.json`
- `coordinator_key.json`
- `blind_review_report.*`

`coordinator_key.json` 含原题号、题型、人工真值和自动评分，只由协调者保管。

## 评审分配

- 评审 A：`reviewer_A.csv`
- 评审 B：`reviewer_B.csv`

两份表的题序、盲评号、题面、候选回答和证据完全相同。两位评审应独立完成，
过程中不讨论具体题目，不参考自动评分。

## 每题要做什么

1. 只依据题面、候选回答、列出的引用和原文摘录判断。
2. 选 `expected_behavior`：
   - `answer_with_current_law`：应依据现行有效规则回答；
   - `explain_repealed_or_refuse`：文件已失效，应说明失效，或拒答；
   - `refuse`：问题超出本地语料范围，应拒答；
   - `answer_with_uncertainty`：有可引用内容，但必须提示效力待核。
3. 评 `answer_verdict`：`correct`、`partially_correct`、`incorrect`、
   `uncertain`。
4. 评 `citation_verdict`：引用是否能支持回答。没有要求引用时为
   `not_applicable`。
5. 评 `force_verdict`：是否正确处理了现行有效、已废止、存疑或语料外状态。
6. 填 `confidence`（高／中／低）和必要的 `reviewer_notes`。发现题目本身有歧义、
   引导性或重复，也在 notes 中写明。

评分字段只允许使用表头已有的英文枚举值，不要自行发明新值。

## 协调者流程

```powershell
# 1. 重新生成盲评包（可复现，固定随机种子 20261121）
python haikou\build_blind_review.py

# 2. 将 reviewer_A.csv、reviewer_B.csv 分别交给两位评审

# 3. 收齐两份 CSV 后计算一致性、Cohen κ 和自动评分差异
python haikou\score_blind_review.py
```

报告写入 `blind_review_report.md` 与 `blind_review_report.json`。任何
“自动评分与人审不一致”的题都应回到原文复核，再决定修数据还是修系统。
