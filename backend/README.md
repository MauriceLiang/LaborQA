# LaborQA 后端代码地图

本文档说明 `backend/` 的模块职责、依赖方向、启动生命周期和核心业务流。根目录 [README.md](../README.md) 负责产品、部署和整体架构；本文档面向需要继续开发或排查后端问题的维护者。逐文件函数索引见[《后端逐文件函数导航》](后端函数导航.md)。

## 运行边界

后端是一个 FastAPI 应用，负责：

- 提供 REST、SSE 和健康检查接口。
- 组装 Chat、资料导入、评测和检索实验等应用服务。
- 通过 Repository 访问 SQLite，通过 FAISS 完成向量召回。
- 调用文档解析、Embedding 和 OpenAI-compatible Chat 服务。

后端不把 FAISS 当作业务真源。文件元数据、Chunk、会话、引用、评测和实验记录以 SQLite / 文件存储为准；FAISS 是可以根据成功 Chunk 重建的运行时索引。

## 分层与依赖方向

```text
FastAPI Router
      │  Depends 从 app.state 获取 Service
      ▼
Application Service
      ├──────────► RAG Chain / Retrieval
      ├──────────► Loader / Parser / Splitter / Embedding
      ├──────────► FileStorage / VectorStore
      ▼
Repository
      ▼
SQLite
```

约束：

- `api` 只负责 HTTP / SSE 边界、参数校验、状态码和 Service 调用，不直接写 SQLite。
- `services` 负责业务流程和跨模块编排。
- `repositories` 只做 SQLite CRUD、查询和事务，不反向依赖 Service，也不承载业务流程。
- `rag.chains` 负责问题改写、证据判断和回答编排，不负责会话持久化。
- `RetrievalService` 负责候选召回、重排和证据整理，不负责最终回答生成。
- `VectorStoreService` 负责 FAISS 的加载、兼容性和重建，不负责业务数据真源。

## FastAPI 启动生命周期

应用入口为 `app/main.py`。`lifespan` 的初始化顺序如下：

```text
configure_logging
      ↓
initialize_database
      ↓
DocumentService
      ├─ recover_interrupted_imports
      ├─ EmbeddingService
      └─ production VectorStoreService
      ↓
SessionService
      ↓
RetrievalService（复用 DocumentService 的 Repository、Embedding 和 VectorStore）
      ↓
ChatService
      ↓
EvaluationService.initialize（恢复任务、准备内置评测用例）
      ↓
RetrievalExperimentService.initialize（恢复实验、准备内置策略）
      ↓
写入 app.state，供 FastAPI Depends 使用
```

应用创建阶段还会注册：

- `/api/health` 和业务 Router。
- `AppError` 等异常到统一 API 错误响应的映射。
- CORS 中间件。
- 为每个请求生成 `X-Request-ID` 的 middleware。

`api/dependencies.py` 不创建 Service，而是从 `request.app.state` 读取启动阶段创建的单例。这保证生产问答、资料管理、评测和实验使用同一组运行时依赖。

## 包与模块职责

### `app/api/`

| 文件 | 职责 |
| --- | --- |
| `contracts.py` | 主要 REST / SSE 路由、请求响应契约绑定、BackgroundTasks 启动入口；Chat 流返回 `status`、`tool`、`token`、`sources`、`done` 或终止性 `error` 事件 |
| `dependencies.py` | 从 `app.state` 提供 `DocumentService`、`ChatService`、`EvaluationService` 和 `RetrievalExperimentService` |
| `health.py` | 聚合数据库、Embedding、FAISS、LLM 配置和文档转换器健康状态 |

### `app/core/`

| 文件 | 职责 |
| --- | --- |
| `config.py` | `.env` / 环境变量、路径和配置约束；例如 `RAG_TOP_K <= RAG_CANDIDATE_K`、重排候选不超过 `RAG_CANDIDATE_K` |
| `database.py` | SQLite 连接、初始化和兼容 migration |
| `schema.sql` | 初始数据库 Schema |
| `schemas.py` | 核心数据库 / 迁移使用的结构辅助 |
| `error_codes.py` | 稳定业务错误码 |
| `errors.py` | `AppError` 和 FastAPI 异常响应映射 |
| `logging_config.py` | 日志格式、级别和输出配置 |

### `app/rag/`

| 文件 | 职责 |
| --- | --- |
| `ai_modes.py` | `speed`、`balanced`、`quality` 三档 AI Profile；默认是 `balanced` |
| `chains.py` | Rewrite、Evidence Judge、Answer 的 LangChain 编排 |
| `decision.py` | `EvidenceDecision`、`EvidenceJudgement`、`AnswerMode` 等证据决策对象 |
| `embeddings.py` | 本地 / API Embedding facade、模型签名和批量调用 |
| `providers.py` | Chat / Embedding Provider 构建以及模型参数适配 |
| `loaders.py` | 将 PDF、DOC、DOCX、MD、TXT 加载为 LangChain Document |
| `splitters.py` | `LegalTextSplitter` 和分块元数据 |
| `runtime_config.py` | 评测 / 实验使用的运行配置快照，包括模型、Embedding、分块、索引和 Prompt 版本 |
| `constants.py` | 分块器版本等稳定常量 |
| `errors.py` | RAG、模型和 Embedding 层异常 |

### `app/repositories/`

Repository 是 SQLite 的持久化边界，只负责 CRUD、筛选、分页、事务和恢复查询：

| 文件 | 负责的数据 |
| --- | --- |
| `document_repository.py` | document、chunk 及资料来源 / 导入状态 |
| `session_repository.py` | session、message、citation、tool execution |
| `evaluation_repository.py` | evaluation case、run、result |
| `missing_knowledge_repository.py` | 知识缺口记录 |
| `retrieval_strategy_repository.py` | 检索策略及版本 |
| `retrieval_experiment_repository.py` | 检索实验及运行结果 |

Service 可以组合多个 Repository，但 Repository 之间不通过 Service 互相调用。

### `app/schemas/`

| 文件 | 职责 |
| --- | --- |
| `common.py` | `ApiModel`、统一响应、分页和时间类型 |
| `contracts.py` | Document、Chat、AI Mode、Evaluation、Experiment、Health 等请求响应契约和枚举 |

### `app/services/`

| 文件 | 职责 |
| --- | --- |
| `chat_service.py` | 生产 Chat 主流程、AI Mode 路由、SSE、消息 / 引用持久化和性能埋点；`answer_once` 供评测和实验复用 |
| `retrieval.py` | 候选召回、查询扩展、轻量重排、可选模型重排、邻居扩展和 evidence limit |
| `vector_store.py` | FAISS 加载、签名兼容性、持久化、删除向量和从成功 Chunk 重建 |
| `document_service.py` | 普通资料和系统资料的导入、删除、重导入、Chunk 写入和生产索引更新 |
| `builtin_documents.py` | 扫描 `reference-data/documents/`，生成 `source_key`、SHA-256 和系统资料清单 |
| `file_storage.py` | 文件类型 / 大小 / 路径安全校验、工作副本写入和删除 |
| `document_parser.py` | package / LibreOffice 文档解析和转换能力检查 |
| `lightweight_rerank.py` | 本地查询扩展和轻量重排 |
| `rerank.py` | 可选模型重排 |
| `material_checklist.py` | 材料清单工具和 `TOOL_ONLY` 行为 |
| `compliance.py` | 固定合规提示规则 |
| `missing_knowledge.py` | 拒答原因和知识缺口记录 / 维护 |
| `evaluation_cases.py` | 内置 baseline 评测用例 |
| `evaluation_service.py` | 评测运行、真实 RAG 回答、Evaluator、指标和逐题结果 |
| `retrieval_experiment_service.py` | 检索策略、Preview、隔离实验索引和结果比较 |
| `session_service.py` | Session、Message、Citation 和 Tool Execution 的业务操作 |
| `text.py` | 文本清洗和辅助逻辑 |

### `app/prompts/`

| 文件 | 调用位置 | 作用 |
| --- | --- | --- |
| `question_rewrite.txt` | Rewrite Chain | 将多轮问题改写为独立问题 |
| `evidence_judge.txt` | Evidence Chain | 判断当前证据是否足以支持回答 |
| `answer_system.txt` | Answer Chain | 约束回答只使用提供的证据 |
| `evaluation_judge.txt` | `EvaluationService` | 对评测回答进行离线判断 |

### `app/scripts/`

| 文件 | 用途 |
| --- | --- |
| `verify_embedding.py` | 验证本地或远程 Embedding 是否可用 |
| `audit_evaluation_baseline.py` | 审计内置评测 baseline 的完整性和结果 |

## 关键业务流

### Chat / SSE

```text
POST /api/chat/stream
→ ChatService.stream_chat
→ 读取会话历史并 Rewrite
→ RetrievalService 召回和整理证据
→ Evidence Judge 决策
→ Material Checklist Tool 或 Answer Chain
→ 发送 status / tool / token / sources / done
→ 保存 Message、Citation 和 Tool Execution
```

`status` 的阶段为 `understanding`、`retrieving`、`verifying`、`generating`。生产链路的流式接口在 `stream_chat`，非流式 `answer_once` 供 Evaluation 和 Retrieval Experiment 复用，避免离线任务另写一套 RAG。

### 普通资料导入

```text
FileStorage
→ document=PROCESSING
→ Loader / Parser
→ LegalTextSplitter
→ Embedding
→ document_repository 写入 Chunk
→ VectorStoreService 写入 production FAISS
→ document=SUCCESS
```

任一步失败都会记录 `FAILED` 和错误说明；删除或重导入前会清理旧 Chunk 与对应向量，索引需要时从 SQLite 的成功 Chunk 重建。

### 系统资料一键导入

`builtin_documents.py` 以相对路径生成 `source_key=builtin:<path>`，并计算源文件 SHA-256。`DocumentService.get_builtin_status()` 将源清单与 SQLite 中的 `source_type=BUILTIN` 资料比较，得到 `MISSING`、`PROCESSING`、`SUCCESS`、`FAILED` 或 `OUTDATED`。

真正导入时，系统资料先复制到 `uploads/` 工作副本，再由 `import_builtin_batch()` 按顺序调用普通 `import_document()`。因此系统资料与用户上传资料共享解析、分块、向量化和索引更新逻辑。

### Evaluation 与 Retrieval Experiment

- `EvaluationService` 为每次运行保存 AI Mode、模型、Embedding、Splitter、Prompt 等运行配置快照，然后调用 `ChatService.answer_once`，再由 Evaluation Judge 计算正确性、拒答和引用相关指标。
- `RetrievalExperimentService` 根据策略快照创建 `data/faiss/experiments/experiment_<id>/` 隔离索引，执行真实检索链路并比较结果；实验索引的生命周期不覆盖 `data/faiss/production/`。

## 数据真源和持久化边界

```text
SQLite
├── document / chunk
├── session / message / citation / tool_execution
├── missing_knowledge
└── evaluation / retrieval experiment records

FAISS
└── chunk.id → embedding 的可重建索引
```

`citation` 通过 Chunk id 回到 SQLite 获取原文和来源元数据；系统资料的 `source_key` 和 `source_hash` 用于识别源文件版本；`uploads/` 只是运行时文件副本，不替代 SQLite 元数据。

## 开发与验证

以下命令从仓库根目录执行。主流程直接调用虚拟环境中的 Python，不要求先激活虚拟环境。

### Windows PowerShell

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
```

### macOS / Linux

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check app tests
.venv/bin/python -m ruff format --check app tests
```

涉及资料、Embedding 或索引的改动，建议补充对应测试并执行：

### Windows PowerShell

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_builtin_documents.py tests/test_document_api.py tests/test_vector_store.py -q
.\.venv\Scripts\python.exe -m app.scripts.audit_evaluation_baseline
```

### macOS / Linux

```bash
cd backend
.venv/bin/python -m pytest tests/test_builtin_documents.py tests/test_document_api.py tests/test_vector_store.py -q
.venv/bin/python -m app.scripts.audit_evaluation_baseline
```

配置或索引兼容性发生变化时，先确认 `/api/health`，再重新导入资料和运行 baseline；不要只用单个 Chat 请求判断改动是否正确。
