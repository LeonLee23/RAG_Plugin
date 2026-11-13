# V2 验证报告（2026-11-13）

本报告只描述文件版 V2；本目录其他未带 v2 前缀的报告为初版历史。源码未提交或推送，用户数据、凭据和旧运行服务未做迁移或清理。

## 自动化与协议

- `uv sync --frozen`：通过，V2 锁文件不再包含 PG、Streamlit、向量/内部 CLI 专属依赖。
- `uv run --no-sync pytest -m "not integration and not live_model" -q`：22 passed、1 skipped（当前 Windows 不允许创建符号链接）；另一个实际 Windows junction 越界测试已通过。
- `uv run --no-sync pytest -m integration -q`：3 passed，包含两个真实进程并发发布、持锁进程终止后 OS 释放锁，以及实际 stdio initialize/tools/list/tools/call、错误码、分页和图像内容。
- 合计 25 passed、1 skipped。普通测试不请求真实模型；派生结果替身明确标注为测试数据。
- `uv run --no-sync ruff check .`、`git diff --check`：通过。
- `uv run --no-sync python -m scripts.check_runtime`：17 个工具，9 只读/8 管理，真实 stdio 连接通过。初始化返回的 1.30.0 是 FastMCP SDK 的默认 serverInfo.version，项目版本为 2.0.0。
- Skill 通过系统 quick_validate；验证器使用隔离 uv 环境临时安装 PyYAML，并加 `python -X utf8` 适配 Windows 默认编码，没有向项目增加 YAML 依赖。
- `claude plugin validate .`：清单格式通过，有作者信息缺失和根 CLAUDE.md 不自动作为插件上下文的非阻断提示；共用工作流位于 skills/ragplugin，不依赖插件根 CLAUDE.md。

逻辑检查包含原文/派生归属、哈希失效、版本和游标过期、重复/围栏/层级标题、长正文分页、PDF 页码、DOCX 表格位置、MD 图片原始依赖、只读不写与搬迁、同输入幂等、参数冲突、失败更新保留旧版、旧 attempt、取消、删除后拒绝旧发布、恢复不依赖外部源、manifest 替换失败和发布移动后重试。文件占用通过错误注入覆盖，不能当作实际断电耐久性测试。

## Codex 实际宿主

客户端：Codex CLI 0.167.0-alpha.3.2，Windows。模型名称与 token 用量未从本探针事件中取得，记为不可得。只使用 scripts/seed_demo 自建的 PDF、DOCX、MD 和图片。

实际调用轨迹及答案见 [v2-client-traces.json](v2-client-traces.json)，报告中机器路径已替换为占位符，没有图像 base64 或认证信息。

| 场景 | 观察到的结果 |
| --- | --- |
| 建库、导入、宿主读图、提交描述、发布 | 3 个文档发布、2 张图片实际通过 MCP 读取，工具调用与 manifest 一致 |
| 跨文档查询 | 张三→凤凰项目→星河数据库，分别读取 PDF/DOCX 来源，回答前 validate_evidence |
| 图片独有编号 | 正确识别 RAG-42，并注明图片描述属于派生证据；本轮核对自建图片 |
| 无答案 | 说明未记录手机号码，没有编造号码 |
| 普通 RAGPlugin 请求删除 | 没有写调用；manifest 保持不变，提示使用管理标记 |
| 引用/代码块出现管理标记 | 只检索维护窗口并校验证据，没有写调用 |
| 查询未挂载库 | 只列挂载清单，没有擅自挂载或选错库查询 |
| 明确管理更新 | 读取旧版本、更新并发布 MD，查询返回新维护窗口，引用校验成功 |
| 明确管理取消 | 任务 cancelled，未出现在发布文档清单中 |
| 明确管理删除 | 客户端审批策略拦截，未执行；保留 destructiveHint=true，未绕过限制 |
| 删除后的卸载/只读重挂载 | 依用户顺序未继续，真实宿主未验收；业务/文件测试已覆盖挂载操作 |

**区分 Skill 加载与工作流行为：**

第一次 installed 模式能调用 MCP，但非交互环境策略拒绝 shell 读取本地 SKILL.md，且探针目录在开发仓库内会继承 AGENTS.md。因此该轮不能证明原生 Skill 加载或独立 Skill 的写保护。它只提供业务工具、更新/取消和审批拒绝的真实记录。

随后将探针移到仓库外的系统临时目录，使用 `--skill-context inline` 显式提供从安装后文件读取的共用 Skill 正文；宿主再次完成管理/查询和三个只读负例。建库至三问回答约 64.56 秒（不含后续只读负例）。这证明本轮样例中的实际工作流行为，不替代原生 Skill 自动加载验收，也不是无法绕过的身份鉴权。三个只读负例分别为新会话，长对话跨轮次历史标记仍需后续交互验收。

删除工具实际返回 `MCP tool call requires approval, but approval policy is never`。这是客户端策略阻断，不能记为删除成功；没有改变全局设置、移除风险注解或改走 shell 删除。插件的用户管理标记不会覆盖客户端自身审批。

## Claude Code

已交付项目安装器、共用 Skill、stdio 配置和原生插件清单。初次真实探针报告未登录；用户随后明确要求“先测试 Codex，Claude 预留”，因此停止 Claude 实测。尚不能宣称 Claude 的显式技能入口、读图、回答和模式控制已通过。

## 关键词基线

使用同一次真实 Codex 入库的 3 份自建文档运行 24 个历史问题，top-5 候选后读取选中节点完整正文。20 个可回答问题的平均证据组覆盖为 **0.95**，`image-004`（“示意图上的专有代码是什么？”）未覆盖；与实际问“图片独有编号”能回答的现象一致，提示词面匹配仍有词汇缺口。

详细结果见 [v2-lexical.json](v2-lexical.json)。这是小库逐字证据组匹配基线，不是准确率、语义质量或 V2 优于向量的结论；未执行长文干扰集、大库性能、系统性宿主树导航对比或完整拒答/冲突评测。category 中的 graph 只是初版题型标签。

## 尚未验证或未提供

- Codex 交互环境中的原生 Skill 自动读取，以及客户端批准后的删除→卸载→重新挂载完整工具轨迹。
- Claude 真实宿主流程；跨平台/网络盘/实际断电/长时间并发压力；所有复杂文档和图像质量。
- 物理旧版本/暂存清理工具、OCR、远程 Markdown 图片、持久向量或图谱均未提供。更新重建依靠外部可信源，查询不会自动修包。
