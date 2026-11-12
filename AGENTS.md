# RAG Plugin 开发约束

本文件适用于整个仓库。先阅读 `技术方案.md`；用户后续明确要求优先于文档。采用简单函数与模块分工，不使用 LangChain/LangGraph，不包装现成 RAG 产品。

## 结构与边界

- `app/main.py`：应用生命周期与健康状态；`api.py` / `mcp.py`：协议适配，不写检索算法。
- `app/knowledge.py`：知识库、文档生命周期；`ingestion.py`：单工作者状态机与原子发布。
- `app/parsers/`：PDF/DOCX 与图片来源；`chunking.py`：分块和位置映射。
- `app/retrieval/`：向量、关键词、关系与 RRF；HTTP/MCP 必须复用同一入口。
- `app/models.py`：唯一生成模型调用入口，使用已登录的 Claude Code/Codex CLI；不增加模型 API Key。
- `app/embeddings.py`：唯一向量编码入口，经 `EMBEDDING_BASE_URL` 调用远程 QZhou-Embedding-Zh，保存 512 维；禁止用随机/哈希向量冒充语义模型。
- `app/db.py`、`app/sql/`：PostgreSQL + pgvector，参数化 SQL、版本化迁移。
- `ui/`：Streamlit，只通过 HTTP 调用后端。重运行不能重复提交任务。
- `tests/`：纯逻辑、真实 PG 集成与协议测试；`scripts/`：运行检查、演示和评测。

## 数据与模型规则

1. 所有查询和资源下载必须限定 `kb_id`，跨库关系由复合外键拒绝。
2. 只有 ready 文档参与召回。解析、CLI、Embedding 都在数据库事务外执行。
3. 发布/删除固定按 KB → document 顺序加锁；发布校验 attempt，防止旧任务和已删除任务写回。
4. 图谱必须沿已持久化的关系返回真实证据；保留每条边的来源，不能把关键词搜索命名为图召回。
5. 分块保存证据原文和位置；图片描述明确标为派生证据，不伪造页码。
6. CLI 使用 argv/stdin，无 shell 拼接；隔离任务目录/MCP/会话，超时回收进程树。不得读取或打印登录凭据。
7. `.env`、模型缓存、文件、认证、运行日志不提交 Git。不得修改用户机器全局配置来隐藏故障。

## 开发与检查

```powershell
uv sync --frozen
uv run --no-sync pytest -m "not integration and not live_model"
uv run --no-sync ruff check .
docker compose config --quiet
docker compose up -d db
# 集成测试只允许连接名称以 _test 结尾的数据库
$env:TEST_DATABASE_URL = "postgresql://rag:rag_local_demo@127.0.0.1:55432/rag_test"
uv run --no-sync pytest -m integration
uv run --no-sync python -m scripts.check_runtime
```

测试 PG 宿主机端口由 `compose.test.yaml` 提供。普通测试不得发起真实 CLI 模型请求；真实模型探针必须显式传 `--live-model`，只使用自建数据。

## 工作方式

先完成可验证的最小阶段，再推进下一阶段。非平凡逻辑有对应测试，异常和边界与正常流程同等重要。保留用户已有文件和修改，不自动提交、推送或清空数据库。更新 `docs/progress.md` 记录实际完成、验证命令与阻塞项；不把 Mock 通过说成真实模型/容器通过。开发中发现方案不可行时修正最小范围并说明原因，不默默降低题目要求。
