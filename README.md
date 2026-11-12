# RAG 知识库 Demo

完整操作指南：[使用说明书](使用说明书.md)（安装、页面操作、MCP/Skill 接入、演示、维护与排错）。

向量模型服务：[QZhou 服务器/本机部署、API 接入、日志与停止](docs/向量模型服务运维.md)。

Streamlit + FastAPI + PostgreSQL/pgvector。支持 PDF、DOCX（含图片）入库，向量、关键词、图谱三路独立召回和 RRF 融合；回答带证据引用，Claude Code/Codex 通过 MCP + Skill 使用同一套知识库。

生成文本、图片描述和关系抽取使用已登录的 **Codex 或 Claude Code CLI**。文档和查询的向量编码通过 HTTP 调用部署在另一台服务器上的 **QZhou-8B-zh**（`Kingsoft-LLM/QZhou-Embedding-Zh`）。[模型说明](https://huggingface.co/Kingsoft-LLM/QZhou-Embedding-Zh)

## 1. Docker Compose 启动

需要 Docker Desktop 或 Linux Docker Compose。入库和向量检索需要后端能访问 QZhou 向量服务。

在仓库根目录执行（PowerShell）。文中的 `<...>` 为占位符，执行前替换为实际值；本机回环地址和示例域名不代表开发者的部署地址。

```powershell
Copy-Item .env.example .env # 已有 .env 时保留，不重复覆盖
```

配置模板的 `EMBEDDING_BASE_URL` 默认留空。此时仍可管理知识库，也可对已有索引进行关键词和图谱检索。需要向量编码时，在 `.env` 填入实际向量服务地址和服务公开的模型名。Base URL 包含 API 前缀（如 `/v1`），后端自动追加 `/embeddings`。

```dotenv
EMBEDDING_BASE_URL=http://embedding.example.com:8000/v1
EMBEDDING_MODEL=Kingsoft-LLM/QZhou-Embedding-Zh
EMBEDDING_API_KEY=
EMBEDDING_TIMEOUT_SECONDS=30
```

服务须提供 OpenAI 兼容的 `POST /v1/embeddings`，接受 `model` 和字符串数组 `input`，返回按 `data[].index` 标识的浮点向量。远端须加载 QZhou 的完整模型，包括 Token Prepending 和 MRL 投影。RAG 后端添加官方 PromptEOL 模板，接受原生 1792 维或 MRL 512 维输出，并保存归一化后的 512 维向量。[模型使用说明](https://huggingface.co/Kingsoft-LLM/QZhou-Embedding-Zh#usage)

若向量服务启用了 `--api-key`，将同一值写入本地 `.env` 的 `EMBEDDING_API_KEY`；留空则不发送认证头。这仅用于自建向量服务，生成模型仍使用 CLI 登录态。`EMBEDDING_MODEL` 必须匹配服务端公开的模型名称；也可让服务端通过 `--served-model-name` 同时提供部署别名和当前配置的模型名。修改 `.env` 后执行 `docker compose up -d --no-deps backend`。

已有 BGE 知识库可继续用关键词和图谱检索。要用 QZhou 向量或融合检索，请新建知识库并重新上传文档。

```powershell
docker compose build backend
docker compose up -d db
```

默认 `AGENT_PROVIDER_1=codex`、`AGENT_PROVIDER_2=claude`。先在**执行后端的容器环境**完成 Codex 官方账号登录：

```powershell
docker compose run --rm backend codex login --device-auth
docker compose run --rm backend codex login status
docker compose up -d
docker compose ps
docker compose logs --tail 40 backend
docker compose exec backend python -m scripts.check_runtime --live-model
```

根据 CLI 显示的官方登录网址完成设备授权。主机已有登录不会自动复制到容器；登录由用户交互完成，认证文件只保存在 `cli-home` 卷。账号不支持设备授权时，应采用该 CLI 官方支持的登录方式，不将凭据填到 `.env`。

要启用 Claude Code 备用连接，也在同一环境执行 `docker compose run --rm backend claude auth login`。每次模型任务先尝试主执行器，遇到 CLI 缺失、登录失败、额度限制、执行错误或超时，且还有调用预算时，再尝试备用执行器。`AGENT_MODEL` 指定主执行器的模型，备用执行器用自己的默认模型。将 `AGENT_PROVIDER_2` 留空可关闭备用；旧 `AGENT_PROVIDER` 仍可用作主执行器配置。

网页状态区显示执行器顺序和上次成功使用的执行器。真实探针也会报告每项任务实际使用的执行器。可用下面的命令单独验证 Claude 的文本和图片能力。

```powershell
docker compose exec -e AGENT_PROVIDER_1=claude -e AGENT_PROVIDER_2= -e AGENT_MODEL= backend python -m scripts.check_runtime --live-model --skip-embedding
```

- 页面：[http://localhost:8501](http://localhost:8501)
- API 文档：[http://localhost:8000/docs](http://localhost:8000/docs)
- MCP：`http://127.0.0.1:8000/mcp/`
- `/health` 检查进程。`/ready` 检查数据库、向量配置和工作者初始化。用 `check_runtime --live-model` 发起真实向量和 CLI 探针。

修改 `.env` 后用 `docker compose up -d --force-recreate backend` 让后端读取新配置。`docker compose down` 保留数据。`down -v` 会删除数据与认证卷。端口冲突时设置 `.env` 中的 `BACKEND_PORT` / `FRONTEND_PORT`。

容器以非 root 用户运行。默认只绑定本机地址，当前版本是单用户 Demo，没有用户登录和权限管理，不应直接暴露到公网。

## 2. 本地开发

需要 Python 3.12、uv，以及已在本机完成账号登录的 Codex/Claude Code。先按上一节配置 `.env` 中的向量服务地址。所有命令均在仓库根目录运行。

```powershell
uv sync --frozen
docker compose -f compose.yaml -f compose.local.yaml up -d db
$env:DATABASE_URL='postgresql://rag:rag_local_demo@127.0.0.1:55433/rag_demo'
$env:DATA_DIR="$PWD/data/local"
$env:AGENT_PROVIDER_1='codex'
$env:AGENT_PROVIDER_2='claude'
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
$env:BACKEND_URL='http://127.0.0.1:8000'
uv run --no-sync streamlit run ui/streamlit_app.py --server.port 8501
```

另开终端运行数据库相关脚本时，同样设置本地 `DATABASE_URL` 和 `DATA_DIR`。`.env.example` 面向容器，主机启动应使用上述覆盖值。

## 3. 演示与评测

容器内生成自建 PDF/DOCX 并上传；这会真实调用模型处理图片和关系：

```powershell
docker compose exec backend python -m scripts.seed_demo --output /data/demo --upload
```

输出 `kb_id=...`，等待两份文档都为 `ready`。上传期间可以从页面查看阶段和失败原因。相同内容重复上传不会建立重复索引；失败文档可以重试。

本机运行等价命令：`uv run --no-sync python -m scripts.seed_demo --upload`。只生成文件、不调用模型时去掉 `--upload`。

在页面分别选择 `vector`、`keyword`、`graph`、`hybrid`：

1. “谁在管理凤凰项目？”观察语义召回。
2. “RAG-42”验证图片独有文字可检索，展开原图及来源位置。
3. “张三负责的项目使用什么数据库？”查看两跳关系及各边证据；top_k=1 时补充证据单独返回。
4. 在问答页提出同一问题，答案应引用“张三负责凤凰项目”和“凤凰项目使用星河数据库”。
5. “张三的手机号码是什么？”应说明证据不足。
6. 删除文档后再次搜索，验证该文档不再返回。

24 题评测包括关键词、同义表达、图谱、图片和无答案场景：

```powershell
uv run --no-sync python -m scripts.benchmark --kb-id '<实际UUID>' --output data/reports
# 可选：额外执行 24 次真实模型问答
uv run --no-sync python -m scripts.benchmark --kb-id '<实际UUID>' --answers --output data/reports-with-answers
```

容器执行时使用 `docker compose exec backend python -m scripts.benchmark --kb-id '<实际UUID>' --output /data/reports`。报告区分主证据召回、补充后证据组召回、完整覆盖、MRR、延迟。`--answers` 才评测拒答和引用 ID，不用模型裁判替代人工核对。具体基线与局限见 [docs/progress.md](docs/progress.md)。

## 4. Claude Code / Codex 接入

外部编程客户端只调用 MCP 的只读工具，自己生成带引用的回答；后端内部模型子进程隔离 MCP 和 Skill，避免调用回自身。

### Claude Code

进入用于提问的客户端项目目录，注册项目级 HTTP MCP：

```powershell
claude mcp add --transport http --scope project rag-kb http://127.0.0.1:8000/mcp/
```

在本仓库执行，把 Skill 安装到客户端项目的 `.claude/skills/knowledge-base/SKILL.md`：

```powershell
uv run --no-sync python -m scripts.install_skill --client claude --workspace '<客户端项目目录>'
```

重启该项目中的 Claude Code，通过 `/mcp` 确认工具连接后，使用 `/knowledge-base` 或明确要求“查询知识库”。

### Codex

```powershell
codex mcp add rag-kb --url http://127.0.0.1:8000/mcp/
uv run --no-sync python -m scripts.install_skill --client codex --workspace '<客户端项目目录>'
```

上述 `codex mcp add` 会写入当前用户的 Codex 配置，由使用者自行执行。项目不会自动修改全局配置。在目标目录重启 Codex，输入：

```text
使用 $knowledge-base，查询“凤凰项目演示”知识库。
张三负责的项目使用什么数据库？请引用文件和位置。
```

Skill 会要求先列库、选择库、检索证据，并处理无结果、冲突和图片描述。工具列表只有 `list_knowledge_bases`、`search_knowledge_base`；所有检索与 HTTP 复用 `app/retrieval/search.py`。

无需改全局配置的真实 Codex 接入探针：

```powershell
uv run --no-sync python -m scripts.client_demo --kb-id '<实际UUID>'
```

该脚本在临时项目中安装同一 Skill、注入临时 MCP 地址，记录真实工具调用和答案到 `data/reports`。只对该子进程的 localhost/127.0.0.1 绕过代理。手动使用客户端遇到本地 MCP 502 时，检查本机代理是否应将 localhost 加入 `NO_PROXY`。

## 5. 自动化检查

自动化测试使用确定性向量和模拟的远程接口响应。生产向量由配置的 QZhou 服务生成。

```powershell
uv sync --frozen
uv run --no-sync ruff check .
uv run --no-sync pytest -m "not integration and not live_model"
docker compose -f compose.test.yaml up -d --wait
$env:TEST_DATABASE_URL='postgresql://rag:rag_local_demo@127.0.0.1:55432/rag_test'
uv run --no-sync pytest -q
docker compose config --quiet
```

集成测试拒绝操作名称不以 `_test` 结尾的数据库。每个测试只清理自己创建的知识库，不执行全库清空。测试数据库是临时内存卷，停止或重建容器后数据会丢失，不能用于保存正式资料。

真实账号探针显式开启：`uv run --no-sync python -m scripts.check_runtime --live-model`，运行前使用本地连接参数。生产镜像不安装 pytest/dev 依赖，测试在本机开发环境执行。

## 6. 结构与实现边界

| 目录/文件 | 职责 |
| --- | --- |
| `AGENTS.md` / `CLAUDE.md` | 开发约束和入口 |
| `app/api.py` / `app/mcp.py` | HTTP、MCP 协议适配 |
| `app/knowledge.py` / `app/ingestion.py` | 文档生命周期、单工作者、原子发布 |
| `app/parsers/` / `app/chunking.py` | PDF/DOCX 图片与文字解析、token 分块和来源映射 |
| `app/models.py` / `app/embeddings.py` | 隔离 CLI、限时重试、远程向量编码 |
| `app/retrieval/` / `app/answering.py` | 三路召回、RRF、引用和 revision 校验 |
| `app/sql/` | 版本化迁移、pgvector、全文检索与关系表 |
| `ui/` / `skills/` / `scripts/` | Streamlit、Skill 源文件、探针和评测 |

完整策略见 [技术方案.md](技术方案.md)，实际交付状态见 [docs/progress.md](docs/progress.md)。未使用 LangChain/LangGraph，也未包装其他 RAG 产品。

当前限制：仅单用户单后端工作者；文本型 PDF、DOCX，扫描件不做 OCR；表格不还原复杂版式；图谱依赖模型抽取且实体仅做规范化和精确名称匹配，没有自动别名合并；无重排序；向量检索使用小库精确扫描。引用 ID、quote 和位置均可校验，但不能据此保证每个生成结论语义正确。

待实现扩展：大库采用 HNSW 并测过滤召回损失；用标注集评估别名解析与 reranker；引入用户权限与工作者队列前先确定多用户规模。无需在本 Demo 中预先增加 Redis、独立图数据库或复杂编排层。
