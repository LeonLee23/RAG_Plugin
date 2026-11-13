# RAGPlugin V2

为 **Codex／Claude Code 当前会话**提供本地知识库的 Skill + stdio MCP 插件。知识库是一份可搬迁目录，检索使用章节目录、中文关键词和精确匹配；答案、图片理解由宿主会话完成。

无需 PostgreSQL、Docker、向量服务、独立 Web 服务或新增模型 API Key。支持 UTF-8 Markdown、文本型 PDF、DOCX，保留原文、图片、位置、发布版本与可验证引用。

- `RAGPlugin`：只读查询。
- `__RAGPlugin`：执行本次明确要求的建库、挂载、导入、更新、删除等管理流程。

这是提示词级操作约定；服务端另行检查只读挂载、路径、归属、版本和哈希，不声称通过标记验证用户身份。

## 开始使用

在仓库目录执行：

```powershell
uv sync --frozen
uv run --no-sync python -m scripts.check_runtime
uv run --no-sync python -m scripts.install_skill --client codex --workspace D:\RAGWorkspace --state-dir D:\RAGLocalState --allow-root D:\RAGWorkspace
```

打开 `D:\RAGWorkspace` 的新 Codex 会话，使用 `$ragplugin`，例如：

```text
$ragplugin 使用 __RAGPlugin 在 D:\RAGWorkspace\知识库 创建名为“项目资料”的知识库，导入 D:\RAGWorkspace\资料\说明.md 并发布。
```

```text
$ragplugin 使用 RAGPlugin 查询“项目资料”：维护窗口是什么时候？请引用原文位置。
```

Claude Code 的安装命令只需将 `--client codex` 换成 `--client claude`，项目技能入口为 `/ragplugin`。本轮按用户要求仅验收 Codex；Claude 保留适配，真实宿主流程待验收。

完整步骤、原生插件目录加载、挂载搬迁、图片、更新与故障处理见 **[使用说明书](使用说明书.md)**。实现边界见 [技术方案](技术方案.md)，实际检查和待验证项见 [开发进度](docs/progress.md)。

## 开发与验证

```powershell
uv sync --frozen
uv run --no-sync pytest -m "not integration and not live_model"
uv run --no-sync pytest -m integration
uv run --no-sync ruff check .
```

`integration` 现在是实际文件、多进程锁和 stdio MCP 测试，不连接 PG。真实宿主验收必须显式启用，只用自建资料：

```powershell
uv run --no-sync python -m scripts.client_demo --live-model --client codex --skill-context inline
```

inline 探针显式提供已安装的 Skill 正文；本机非交互策略阻止原生 Skill 文件读取和删除审批，详情与未验收项见进度文档。

`app/` 为共用业务核心；`skills/ragplugin/` 为共用工作流；`scripts/` 提供安装、诊断、自建样例和评测。原初版运行代码及依赖已退出，历史结果保留在 `docs/verification/`，不代表 V2 成绩。现有数据库、用户文件、`.env`、凭据和运行服务不会自动迁移或清理。
