# 开发与验证记录

## 2026-11-13 推送前说明文件地址检查

- 检查当前工作区 README、使用说明书、技术方案、开发约束、Skill、历史报告及验证 JSON、配置示例和插件清单，未发现私域域名、内网 IP 或个人本机绝对路径。官方公开链接、`127.0.0.1` / `localhost` 回环地址和 `D:\RAGWorkspace` 等通用示例目录保留。
- 将初版历史命令中带固定演示账号密码的数据库连接串替换为 `<db-user>`、`<db-password>`、`<db-host>`、`<db-port>`、`<db-name>` 占位符；历史验证结果不变，该命令不是 V2 安装步骤。
- 验证：`git diff --check`、说明文件地址复扫及 Markdown 相对链接检查通过。本轮仅修改本文档，未运行代码测试或真实模型；未读取或修改本地 `.env`、认证、用户资料及运行配置，未提交或推送，也未检查或改写 Git 历史。

## 2026-11-13 V2 文件版实施与 Codex 验证

- 本轮用户明确授权实现新版并补齐说明书；随后指定“先测试 Codex，Claude 预留”。已用文件包、共用 Skill 与 17 个本地 stdio MCP 工具替换初版运行链路。
- 完成建库/挂载/只读/卸载、MD/PDF/DOCX、章节树/关键词/原文证据/图像、版本与哈希校验、交互式派生提交、增量更新、幂等、恢复、取消、原子发布和逻辑删除。MD 相对图片保留原始字节及依赖哈希；恢复可使用暂存副本而无需外部源仍存在。
- 退出 Web/HTTP/PG/向量/图谱/内部模型 CLI、Compose、旧专属测试及依赖；保留用户原有文档修改、.idea、数据、凭据及服务。未提交、推送、迁移或清空用户数据。旧向量运维页改为退出说明，历史成绩仍归初版。
- 同步 README、使用说明书、技术方案和开发约束；提供项目级双客户端安装器、原生分发清单、stdio 诊断、自建样例、关键词评测和显式 --live-model 宿主探针。安装器不覆盖不同的同名配置，不修改全局客户端设置。
- 自动验证：`uv sync --frozen` 通过；`uv run --no-sync pytest -m "not integration and not live_model" -q` 为 **22 passed / 1 skipped**；`uv run --no-sync pytest -m integration -q` 为 **3 passed**；合计 **25 passed / 1 skipped**。跳过的是 Windows 符号链接创建权限，实际 junction 越界测试已通过。Ruff、diff 检查、stdio 17 工具/注解/错误/图像协议检查通过；8 份 Markdown 的围栏与 21 个相对链接、17 个 PowerShell 示例解析、插件 JSON/清单路径检查通过。
- Skill 系统校验器在隔离 uv + PyYAML + UTF-8 模式下通过，未增加项目运行依赖。Claude 插件清单验证通过，有非阻断元数据提示。
- Codex CLI 0.167.0-alpha.3.2 实测自建资料：三文档发布、两张图片实际读取与描述、跨文档“张三→凤凰→星河”、图片编号 RAG-42、无手机号码的拒答、回答前引用校验；管理更新与取消也有真实调用轨迹。
- 明确验收限制：installed 探针被客户端策略阻止读取 Skill 文件，且初始测试目录继承开发 AGENTS，不能据此宣称原生技能加载通过。随后在仓库外临时目录用显式提供的安装后 Skill 正文（inline）验证工作流，三份文档和三个只读负例通过，负例无写调用、manifest 不变。inline 不替代原生加载或长期对话验收。
- Codex 删除工具被“需要审批但 approval_policy=never”拦截；保留 destructiveHint，不绕过审批。删除→卸载→只读重挂载宿主流程尚未完成，业务测试已验证相关函数。Claude 初次实测返回未登录，按用户后续要求停止并预留，不声称双客户端通过。
- 24 题关键词基线在 20 个可回答问题上平均证据组覆盖 0.95，image-004 未覆盖；仅小库词面检索，不作为答案准确率或向量优劣结论。无全量复杂文档/规模/跨平台/断电效果保证；V2.0 不含物理清理工具。
- 验证细节、调用轨迹、未验证项见 [V2 报告](verification/v2-report.md)、[Codex 轨迹](verification/v2-client-traces.json)、[V2 关键词结果](verification/v2-lexical.json)。本节以下保留原有历史记录，旧版本的运行状态和成绩不能当作 V2 结果。

## 2026-11-13 插件调用标记约定（仅文档）

- 根据用户要求，将 `RAGPlugin` 定义为只读入口，`__RAGPlugin` 定义为本次明确管理操作的入口；同步技术方案、调用示例、MCP 工具分类和 AGENTS.md。
- 明确挂载/卸载、入库/恢复/取消、派生提交/发布等也属于管理；权限不从历史、资料或引用中继承，查询不自动写入，不经 shell 绕过；一个已授权导入流程无需每次工具调用再确认。
- 此约定是提示词级操作门槛，不是真实身份鉴权；保留服务端路径、挂载和版本校验。实际 Skill、MCP 和业务代码未改，新增双客户端调用轨迹验收要求，尚未验证运行行为。
- 验证：`git diff --check` 通过；三份文档 UTF-8/围栏、10 个本地链接和 17 个工具分类（9 个只读、8 个管理）检查通过，管理示例已切换标记。未运行代码测试或真实客户端，运行保护尚未实现。

## 2026-11-13 V2 插件式知识库设计（仅文档）

- 按用户确认的新解法重写 `技术方案.md`：Skill + 本地 stdio MCP + 一库一目录，当前 Codex／Claude Code 会话负责推理、图片理解和回答；原实践题的 Web、Compose、三路召回不再作为验收约束。
- 明确版本化文件包、原文/派生证据/位置映射、挂载与卸载、索引失效、交互式导入、幂等恢复、跨进程锁和原子发布；列出拟实现 MCP 工具、错误与预算、分阶段实施和评测。
- 明确前端、REST、内部模型 CLI、QZhou/向量、图谱/RRF、PG、容器及专属依赖/测试的退出条件；初版已由用户上传 Git，不长期维护两套实现。旧资料、认证与运行服务不属于代码删除范围。
- 同步更新 `AGENTS.md`，区分 V2 目标和旧实现过渡约束。`CLAUDE.md` 已引用它，无需重复维护；README、使用说明书及实际 Skill 仍描述初版，待实施阶段同步。
- 本轮只改上述三份文档，不实现或删除代码，不变更依赖、配置、数据库和服务。核对官方插件/MCP 与 PageIndex 资料；未运行模型、容器或新版端到端测试，新版能力均待实现验证。
- 文档检查：UTF-8 解码、代码围栏配对、10 个本地链接目标存在、11 个连续章节和 17 个唯一拟定工具名校验通过；手工核对工具、版本、状态及退出条件。`git diff --check` 首次发现两处 Markdown 换行尾空格，已修正并复查通过；未执行业务测试，新版功能与效果仍待实现验收。

## 2026-11-13 公开部署文档通用化

- `使用说明书.md` 删除个人工作目录、特定机器的双环境与历史端口说明，改用仓库相对路径、客户端目录占位符及部署方式说明。保留通用回环地址并明确其含义；新增 `EMBEDDING_API_KEY`、Base URL 的 `/v1` 规则、主机/容器地址区别与配置生效步骤。修正过时的 512-token 说明，补充按依赖顺序停止服务的方法。
- `docs/向量模型服务运维.md` 从特定服务器操作记录改为 Linux GPU 服务器与本机 Linux/Windows 两种部署指南，移除专属服务器域名、SSH 端口、绝对路径、固定演示密钥及用户文档处理结果。补齐独立环境安装、完整模型下载、模型端密钥、两种 API 接入、Docker 主机映射、共用 Python 探针、日志和停机排错；没有修改服务实现或运行配置。
- 文档依照 `scripts/serve_embeddings.py`、`app/embeddings.py` 与官方 QZhou、PyTorch、Hugging Face、Docker 文档核对；明确当前启动脚本要求 CUDA/BF16，不声称支持 CPU/MPS 自动回退。README 同步替换客户端个人路径、更新运维入口，并给 UUID 占位符加引号以通过 PowerShell 语法解析。
- 验证：`uv run --no-sync pytest -q tests/test_embeddings.py tests/test_embedding_server.py` 为 **14 passed**；`uv run --no-sync ruff check .`、`docker compose config --quiet` 通过。
- 文档静态检查：用 PowerShell Parser 检查 41 个 PowerShell 代码块；用 `bash -n` 检查 7 个 Bash 代码块；用 Python AST 检查 1 个 Python 代码块及 6 段内联 Python；35 个仓库链接及章节锚点通过。公开指南和 README 未再匹配个人路径、旧服务器域名或固定演示密钥。新增 Compose 主机映射示例经 `docker compose --env-file .env.example -f compose.yaml -f - config --quiet` 验证。
- 文档中的 API 探针直接通过现有 `ProbeModel` / FastAPI TestClient 协议替身运行，验证请求路径、认证和维度检查；未调用真实模型，不代表语义效果或 GPU 部署通过。本次未安装/下载模型、连接远端、重启容器或修改 `.env`，新增从零安装流程仍需在目标 GPU 环境实测。历史验证记录保留为记录，不作为通用安装步骤。

## 2026-11-12 远端真实 QZhou 服务修复

- 经用户明确授权通过 SSH 维护远端服务，未保存 SSH 密码。两端演示密钥统一，服务接受模型全名与 `qzhou-embedding-zh` 别名。本地地址保留 `/v1`，编码超时设为 120 秒并重建后端加载配置。
- 原 vLLM 0.12.0 / Transformers 4.56.2 无法导入模型代码中的 `LossKwargs`，原 `qzhou-vllm-model` 目录还缺少 `2_Dense`。因此没有将缺少投影的输出冒充 QZhou 向量：新增独立的 `scripts/serve_embeddings.py`，使用现有完整 `qzhou-embedding-zh` 文件，通过官方 SentenceTransformer 流程执行 Token Prepending、最后 token 池化及 4096→1792 的学习投影。RAG 后端仍统一从远端获取向量并保存归一化的 512 维。
- 远端使用独立 `qzhou-demo-env`（Transformers 4.51.1、Sentence Transformers 3.4.1、FastAPI 0.115.12、Uvicorn 0.34.2），通过其 `.pth` 复用既有环境的 PyTorch 2.9.0+cu128；全局包未改动。初次继承系统 PyTorch 2.3 时遇到 Python 3.12 Dynamo 不兼容，已排除全局包。PyTorch 2.4.1 下载速度过慢，取消该下载后改为复用已有兼容 GPU 库。原 vLLM 目录及脚本备份保留。
- 远端新启动脚本 `/root/autodl-tmp/start_qzhou_demo.sh`、screen 会话 `qzhou-embedding`、日志 `/root/autodl-tmp/qzhou-embedding.log`。实际验证停止后端口关闭，随后成功重启并恢复健康；完整模型约使用 GPU 0 的 15 GiB 显存。
- 新服务有认证、模型名、批量和 token 长度检查；超长输入明确返回 422，不静默截断；GPU 推理串行且内部 batch_size=2。单独协议测试使用明确标记的模型替身验证认证、拒绝非法输入及顺序，不作为语义效果证据。
- 验证命令：`TEST_DATABASE_URL` 指向已有 `127.0.0.1:55432/rag_test`，`uv run --no-sync pytest -m "not live_model"` 为 **48 passed**；`uv run --no-sync ruff check .` 通过。
- 真实探针均显式传入 `--live-model` 并使用自建文本/图片：远端原生输出 1792 维，范数约 1.003；后端经公网编码为 `(2, 512)`，范数均为 1。单个自建示例中相关段落相似度 0.836、无关段落 0.446，仅为冒烟检查，不是准确率评测。容器 `check_runtime --live-model --skip-embedding` 的 Codex 文本和图片均通过。
- 自建“凤凰项目演示”知识库 `b451e004-5243-4246-beef-41cae4f7a210` 的 PDF/DOCX 均 ready。真实 vector / keyword / graph / hybrid 检索分别返回 4 / 4 / 3 / 4 条主证据，graph / hybrid 各有两条持久关系路径。前端和后端健康接口均返回 200。
- 真实 Demo 问答已返回 `answered`：“凤凰项目使用星河数据库（StarRiver DB）”，引用 `[E1]`。入库占用 CLI 时曾返回 `MODEL_BUSY`，槽位释放后调用通过，未绕过并发限制。
- 用户两份 PDF 已通过现有 HTTP 重试接口重新排队，未重新上传、删除或修改原文；向量阶段均通过，随后在图谱阶段遇到 `MODEL_OUTPUT_INVALID`。发现原重试只反馈异常类型，没有具体原因，现补充不含原输入的字段/引用错误反馈与日志，要求 quote 保留连续原文、空格、换行和标点；严格证据校验与两次调用上限不变。相关回归测试验证修复收到具体原因且日志不含输入内容。
- 补充修复后，真实 `rag_test` 上 `uv run --no-sync pytest -m "not live_model"` 为 **50 passed**；Ruff、镜像构建和后端重建通过。第一份原 PDF `c747cb7b-8374-4ad1-a5ae-64b84841495e` 已 ready，实际发布 41 个 512 维分块与 169 条关系；在原知识库执行 hybrid 返回 3 条证据，`degraded=false`。
- 第二份 PDF 的真实日志进一步确认 quote 与来源不匹配；仅提供错误索引不足以针对性修正，因此修复上下文加入上一轮输出，并明确其不是证据、只能依据原始 input 修正。上一轮输出仅传回同一业务任务，不写入日志；Schema 错误仍不记录输入。完整 50 项测试再次通过。真实复验中出现一条 quote 校验错误后，定向修复通过，后续批次完成。
- 最终两份原 PDF 均为 **ready / 100%**：第一份 41 个分块、169 条关系；第二份 `202f47d5-a1d1-49b2-9470-509f1eddbfc5` 为 37 个分块、160 条关系。原知识库实际保存 **78 个 512 维向量、329 条关系**，均由 Codex 完成生成阶段。最终 hybrid 返回 3 条证据，覆盖两份文档，`degraded=false`；后端健康检查 200。没有跳过或放松引用校验，没有人工改成 ready。
- 运维文档同时保存于仓库 `docs/向量模型服务运维.md` 与服务器 `/root/autodl-tmp/QZhou-operations.md`。向量模型留在 `qzhou-embedding` screen 会话运行，SSH 密码未写入文档或代码。
- 操作命令见 [向量模型服务运维](向量模型服务运维.md)。

## 2026-11-12 向量服务演示密钥

- 用户提供的 vLLM 启动参数确认启用了 `--api-key "$API_KEY"`，并明确要求配置简单演示密钥；按本次明确要求新增可选 `EMBEDDING_API_KEY`，仅对向量请求发送 Bearer 认证，留空保持匿名请求。使用 `SecretStr` 避免配置展示泄露密钥；401/403 提示检查两端密钥，不回显远端响应正文。生成模型继续使用已登录 CLI。
- 本地 `.env` 已设置演示密钥；模板仅提供空配置项。已构建镜像并重建 backend。服务端尚需同步演示密钥、重启旧进程；其 `--served-model-name qzhou-embedding-zh` 还应改为当前客户端使用的 `Kingsoft-LLM/QZhou-Embedding-Zh`（或同时提供该别名），避免改动已有知识库的模型配置。
- 验证：`uv sync --frozen`、`uv run --no-sync pytest -m "not integration and not live_model"`（32 passed）、`uv run --no-sync ruff check .`、`docker compose config --quiet`、`docker compose build backend`、`docker compose up -d --no-deps backend` 均通过。`TEST_DATABASE_URL` 指向已有 `127.0.0.1:55432/rag_test` 的真实 PG，`uv run --no-sync pytest -m integration` 为 15 passed；测试向量与生成模型为替身，不代表真实模型成功。
- 测试 PG 启动尝试因 55432 已被占用而失败，随后复用已有测试数据库，并移除本次创建但未启动的 db-test 容器，未清空数据库。
- 容器内 `python -m scripts.check_runtime` 配置/版本/数据库检查通过；以 `python - --live-model` 显式运行的独立编码探针仅发送自建文本，仍返回 HTTP 401。真实向量验证及用户文档重试待服务端同步配置后进行。

## 2026-11-12 向量服务 404 排查

- 远端 `/openapi.json` 确认编码接口为 `/v1/embeddings`；本地 `.env` 原先只配置服务根地址，后端追加 `/embeddings` 后命中不存在的路径。已为 `EMBEDDING_BASE_URL` 补充 `/v1`，执行 `docker compose up -d --no-deps backend` 让配置生效，未改变模型或知识库数据。
- 在后端容器内通过 `python - --live-model` 运行独立 Embeddings 探针，仅发送自建文本“凤凰项目使用星河数据库。”。404 已消除，但真实请求返回 HTTP 401；`GET /v1/models` 同样返回 `{"error":"Unauthorized"}`。尚未拿到真实向量，不能宣称向量化成功。
- `docker compose exec -T backend python -m scripts.check_runtime` 通过 CLI 版本、PG/pgvector 和向量地址配置检查；该命令未执行生成模型或验证远端编码成功。
- 阻塞：需确认向量服务启动参数或网关鉴权方式（隐藏密钥）。本次仅修正项目本地配置，不读取登录凭据，不变更远端或用户全局配置，不自动重试用户文档。

## 2026-11-12 生成模型主备执行器

- `.env` 和 `.env.example` 配置 `AGENT_PROVIDER_1=codex`、`AGENT_PROVIDER_2=claude`。每个生成任务优先 Codex。CLI 缺失、登录失败、额度限制、执行失败或超时时，在剩余预算内尝试 Claude。输出修复和主备切换共享两次生成预算。网页显示配置顺序和最近一次成功的执行器，文档记录实际参与的提供方。
- 在真实 `rag_test` 上运行 `uv run --no-sync pytest -q -m "not live_model"`，43 项通过。主备测试用受控子进程验证优先级、错误切换和输入传递。`uv run --no-sync ruff check .`、`docker compose config --quiet`、`docker compose build backend` 均通过。
- 临时容器的 `check_runtime --skip-embedding` 检查确认新版配置顺序、两个 CLI 版本和 pgvector 可用。两套 CLI 均返回 `MODEL_AUTH_REQUIRED`，真实模型切换待登录后验证。
- 正在运行的旧后端仍使用 BGE 向量服务。本工作区的 QZhou 地址为空，保留现有服务，避免重建时中断向量检索。

## 2026-11-12 远程 QZhou 向量服务

- 入库和查询使用 `EMBEDDING_BASE_URL` 访问 OpenAI 兼容的 `/embeddings`。默认模型为 `Kingsoft-LLM/QZhou-Embedding-Zh`，按官方模板组装输入。支持原生 1792 维及 MRL 512 维响应，统一保存归一化的 512 维向量。
- 移除了本地推理依赖和模型缓存卷。分词配置来自模型仓库 revision `a26c27aff5756ca78402258440ae20f09e9219bd`，原始文件 SHA-256 为 `b326efa3b3cb974b258836e76f8a992c77a1fa93b9d9126a1632a416bf663a20`，随应用压缩分发。[分词配置来源](https://huggingface.co/Kingsoft-LLM/QZhou-Embedding-Zh/blob/a26c27aff5756ca78402258440ae20f09e9219bd/tokenizer.json)
- `uv sync --frozen --offline` 在独立 Python 环境通过。使用真实 `rag_test` 运行 `uv run --no-sync pytest -q -m "not live_model"`，**36 项通过**。覆盖模拟远程接口、pgvector 存储和召回、旧 BGE 知识库配置隔离及空地址行为。
- `uv run --no-sync ruff check .`、`uv run --no-sync python -m compileall -q app ui scripts`、`docker compose config --quiet` 及 `docker build -t rag-plugin:qzhou-check .` 通过。容器内通过本机 HTTP 测试服务验证了请求路径和 1792 维响应处理。
- 按用户要求留空 `EMBEDDING_BASE_URL`。真实 QZhou 推理待配置地址后验证。旧 BGE 知识库使用 QZhou 向量检索前需重新导入。

## 2026-11-11 验证记录

以下为切换远程向量服务前的历史结果。

## 分阶段结果

| 阶段 | 已实现 | 验证情况 |
| --- | --- | --- |
| 基础工程 | AGENTS.md、CLAUDE.md、Python 3.12、uv.lock、模块结构、测试环境 | 已完成；未引入 RAG 编排框架 |
| M0 能力 | CLI 结构化输出/图片、进程隔离、本地 BGE | 主机 Codex 真实文本/图片通过；BGE 512 维、范数约 1；Claude 当前账号实际任务认证失败 |
| M1 服务数据 | Compose 三服务、PG 迁移、CRUD、去重、Streamlit | 镜像实际构建；三个容器启动；后端和 PG healthy；迁移重复运行通过 |
| M2 入库 | PDF/DOCX/图片、位置、分块、向量、关系与原子发布 | 自建 PDF 和 DOCX 经真实 Codex+BGE 处理，均 ready；表格和浮动图片有测试 |
| M3 检索 | pgvector、PG 全文、持久关系两跳召回 | 三路独立接口、隔离、真实 PG 排序、关系证据通过 |
| M4 融合问答 | RRF、补充证据、claims、revision | top_k=1 两跳补齐、未知引用/过期证据拒绝；真实回答与拒答通过 |
| M5 Agent 接入 | HTTP MCP、Skill、安装器、临时客户端探针 | 实际 Codex 使用 Skill，完成列库、检索和带来源回答；不改全局配置 |
| M6 交付 | README、24 题评测、脚本、版本与记录 | 本地交付完成；容器账号登录及登录后模型复验、GitHub 发布尚未执行 |

## 自动化验证

```powershell
uv sync --frozen --extra models
uv run --no-sync ruff check .
$env:TEST_DATABASE_URL='postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>'
uv run --no-sync pytest -q
uv run --no-sync python -m compileall -q app ui scripts
docker compose config --quiet
docker compose build backend
```

结果：**26 passed**，Ruff、编译、Compose 配置检查通过。覆盖：

- 分块完整覆盖、来源偏移、PDF 图片、DOCX 表格/行内/浮动图片。
- CLI 真子进程畸形 JSON 修复、两次上限、认证错误、输出上限、超时父子进程回收和信号量释放。
- 真实 PG 四路召回、向量距离排序、跨库隔离和外键、并发去重、删除后不可召回、旧 attempt 无法发布、事务中途失败全回滚。
- 工作者图谱阶段失败、原文件保留、重试成功、启动恢复和单工作者锁。
- hybrid 部分失败、查询操作符、无证据不调用模型、未知引用、回答期间证据删除。
- MCP 真实 HTTP 握手、工具发现、正常/错误调用；Streamlit AppTest 验证重运行不重复 POST。
- 额外通过 AppTest 连接真实主机后端：三个标签页正常，检索展示 4 个证据面板，无页面异常。
- 容器实际 HTTP 检查：建库/上传/删除正常；未登录时文档快速进入 failed/MODEL_AUTH_REQUIRED，没有半份索引；四路检索为空；前端返回 HTTP 200。

普通测试使用确定性向量和模型替身，不衡量语义准确率；真实账号与端到端结果单独列在下方。

## 真实模型与客户端

运行 `python -m scripts.check_runtime --live-model`（已配置主机数据库）：

| 检查 | 实测 |
| --- | --- |
| 主机 Codex | `0.167.0-alpha.3.2`，文本 JSON、图片 RAG-42 识别通过 |
| Embedding | `BAAI/bge-small-zh-v1.5`，revision `7999e1d3359715c523056ef9478215996d62a620`，输出 `[1,512]`，范数 `1.000000119` |
| 数据库 | PostgreSQL 17 / pgvector 0.8.2 |
| 主机环境 | Windows x86_64、Python 3.12.13、CPU 编码 |
| 容器 CLI | Codex `0.167.1` / Claude Code `2.1.153`；安装及版本命令通过，尚未登录 |
| 主要依赖 | MCP 1.30.0、Streamlit 1.67.0、psycopg 3.3.8、Sentence Transformers 5.7.0、CPU Torch 2.15.0；完整版本见 uv.lock |

两份自建样例包含中文正文、DOCX 表格和图片独有编号，经过实际 CLI 图片描述/关系抽取、BGE 编码后 ready。真实问答：

| 问题 | 结果 |
| --- | --- |
| 张三负责的项目使用什么数据库？ | 星河数据库（StarRiver DB），引用两份文档 E1/E2 |
| 架构图中的图片独有编号是什么？ | RAG-42，引用图片描述证据 |
| 张三的手机号码是什么？ | `insufficient_evidence`，没有编造号码 |

原始记录：[问答探针](verification/answer-probes.json)、[Codex 答案](verification/client-answer.md)、[MCP 工具调用](verification/client-tools.json)。仅含自建公开演示资料。

客户端探针确实观察到 `list_knowledge_bases` 和 `search_knowledge_base` 完成事件。首次主机代理转发 localhost 导致 502，已在探针子进程中追加 NO_PROXY，未修改全局代理配置。

## Benchmark 基线

24 题：关键词 6、同义表达 6、图谱 4、图片 4、无答案 4，共 96 次检索。本轮没有运行完整 `--answers`，只做上面 3 个真实问答探针。

报告：[检索基线](verification/report.md)，文件哈希/文档映射：[manifest.json](verification/manifest.json)。完整检索记录在 `data/reports/results.jsonl`，可通过脚本重建。

- 20 个可回答问题上，vector/keyword/hybrid 证据组召回 1.000，graph 为 0.850；hybrid MRR 为 0.942。
- 图谱 3 个漏召回均为未包含明确实体名的图片问题，`no_seed_entity`，符合精确名称选种子的边界；没有偷偷回退其他模式。
- 两份文档只有少量分块，top_k=5 容易覆盖全库，满召回不能代表生产质量。后续应加入相似干扰资料、别名与长文档，比较 top_k=1/3/5，再决定是否引入别名治理或重排序。
- 引用 ID 合法、quote 存在不等于结论必然被语义支持，仍需人工核对。

## 当前环境和待办

- 主机演示：API `http://127.0.0.1:8000`，页面 `http://127.0.0.1:8501`，库 ID `134b70ab-25c7-48e7-ad48-beac5adc5a5c`。本轮使用测试 PG 容器里的独立 `rag_demo` 数据库，与 `rag_test` 分开；该容器临时卷在停止后会丢失，样例可重建。
- Compose 冒烟：为避免冲突，本轮 API **8001**、页面 **8502**；数据/模型/认证卷持久化。README 默认仍为 8000/8501，切回前先停止对应主机开发进程。
- 容器登录需用户执行 `docker compose run --rm backend codex login --device-auth` 完成官方授权，再运行容器真实探针。未复制主机认证文件，未声称容器已通过真实生成验证。
- Claude 主机账号曾报告登录，但实际隔离图片任务返回认证错误；当前主链路是已验证 Codex。切换前需重新登录并通过探针。
- 未指定 GitHub 仓库/发布操作，本轮未提交、推送或创建远端仓库。

## 与设计的实现取舍

- 使用 pyproject.toml + uv.lock，不重复维护 requirements。生产镜像不安装 pytest，测试在开发环境执行。
- 同标题短段落优先合并，长段落拆分保留约 50-token 重叠；短段落组之间不强制复制重叠内容。
- 单后台线程加 PG 会话锁；processing 启动恢复，attempt 阻止旧任务写回。失去锁连接后停止领任务，重启恢复。
- 业务日志为 JSON，依赖库保留自身日志格式；未引入日志平台。
- 多用户权限、别名合并、HNSW、重排序与分布式工作者仍为待实现扩展。
