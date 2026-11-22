# 软件著作权申报范围建议

本文用于整理代码仓库和软著材料的边界，不构成法律意见。

## 建议纳入自研申报范围

- `haikou/force_layer.py`
- `haikou/force_protocol.py`
- `haikou/warn.py`
- `haikou/temporal.py`
- `haikou/temporal_evidence.py`
- `haikou/article_force.py`
- `haikou/retrieval.py`
- `haikou/retrieval_router.py`
- `haikou/reasoning_retrieval.py`
- `haikou/tree_index.py`
- `haikou/tree_retrieval.py`
- `haikou/pageindex_force_adapter.py`
- `haikou/llm_provider.py`
- `haikou/catalog_index.py`
- `haikou/service.py`
- `haikou/web/`
- `haikou/audit_*.py`
- `haikou/build_*.py`
- `SPINE.md`、`progress.md` 和本目录中的设计说明

## 建议排除

- `PageIndex/`：MIT 上游项目，由 Git 子模块引用。
- `database/`：政策原文、附件与大型生成索引，不属于本软件源代码。
- `.venv/`、`haikou/_vendor/`：第三方运行时和依赖。
- `.env` 与部署密钥。
- 模型生成的原始回答、第三方人工评审内容以及未作实质修改的自动输出。

## 建议保留的开发证据

- Git 提交历史和版本标签。
- 需求、架构和规则变更记录。
- 自动化审计和人工验收结果。
- 页面与服务端版本快照。
- 第三方许可证和开源代码来源说明。

申请表中的开发者、权利人、开发方式和权利取得方式应按实际情况填写。AI 辅助开发
不等于 AI 成为作者；应保留人在需求定义、架构选择、代码审查、测试验收和最终表达
方面的贡献证据。
