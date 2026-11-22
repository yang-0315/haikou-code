# 海南自贸港政策效力感知问答

面向海南自由贸易港政策文本的条文级问答与比对系统。项目的核心不是普通
RAG，而是把文档整体效力和条文级效力放进检索、选条和作答回路：

```text
问题
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
- validity-QA：所有者验收 80/80；双人 A/B 同源，不构成独立盲评。
- 条文级部分废止审计：28/28。
- Web 服务审计：22/22。

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
python haikou\spine_check.py
python haikou\audit_router.py
python haikou\audit_reasoning.py
python haikou\audit_article_force.py
python haikou\audit_temporal.py
python haikou\audit_tree_retrieval.py
python haikou\audit_service.py
```

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
