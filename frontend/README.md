# LaborQA 前端代码地图

本文档说明 `frontend/` 的页面、组件、Store、API Client 和类型体系。根目录 [README.md](../README.md) 负责整体产品与部署说明；本文档面向需要继续开发页面、状态或 SSE 交互的维护者。

## 技术边界

前端使用 Vue 3 + TypeScript + Vite，页面通过 Vue Router 组织，跨页面状态使用 Pinia，视觉组件使用 Element Plus。

```text
View / Component
      │
      ├── Pinia Store（跨页面状态）
      └── API Client
              ├── Axios：普通 JSON API
              └── fetch + ReadableStream：Chat SSE
                        │
                        ▼
                    FastAPI /api
```

页面和组件不直接拼接后端地址，也不直接操作 SQLite 或浏览器之外的运行时数据。普通 HTTP 请求集中在 `src/api/`，全局交互状态集中在 `src/stores/`。

## App Shell 与初始化

### `src/main.ts`

初始化 Vue 应用、Pinia、Vue Router 和全局样式，然后挂载 `App.vue`。

### `src/App.vue`

全局 App Shell 负责：

- 左侧品牌区、主导航和最近会话列表。
- 顶部栏、新建对话入口、知识库提示和全局 `AiModeSelector`。
- Chat 路由上的会话状态提示。
- `RouterView` 页面承载。
- 挂载时刷新最近会话。

App Shell 不负责具体 Chat 流解析；它只组合全局导航和 Store。

## 路由与页面

路由定义在 `src/router/index.ts`：

| 路径 | 组件 | 职责 |
| --- | --- | --- |
| `/` | `ChatView.vue` | 多轮问答、流式回答、来源、工具和回答风格 |
| `/system` | `HomeView.vue` | API、数据库、Embedding、索引和文档转换器状态 |
| `/documents` | `DocumentsView.vue` | 系统资料导入、上传、筛选、重导入、删除和 Chunk 查看 |
| `/missing-knowledge` | `MissingKnowledgeView.vue` | 查看和维护被证据门控拒答的知识缺口 |
| `/evaluations` | `EvaluationView.vue` | 评测用例、运行批次、逐题结果和指标 |
| `/retrieval-experiments` | `RetrievalExperimentView.vue` | 检索策略、Preview、隔离实验和结果比较 |

页面负责页面级编排和交互反馈；可复用的聊天、资料组件放在 `src/components/`。

## Pinia Stores

### `src/stores/session.ts`

管理 Chat 会话状态：

- 当前 `sessionId`，持久化到 `localStorage` 的 `labor-rights-qa.session-id`。
- 回答风格 `plain` / `legal`。
- 最近会话列表及刷新中的错误。
- `streaming` 和 `historyLoading` 生命周期状态。

### `src/stores/preferences.ts`

管理全局 AI Mode：

- `speed`、`balanced`、`quality` 三个值。
- 默认值为 `balanced`。
- 持久化键为 `labor-rights-qa.ai-mode`。
- 通过 `AiModeSelector.vue` 修改，不通过 `.env` 或全局后端 Settings 修改单次请求的模式。

### `src/stores/system.ts`

管理 `/api/health` 返回的系统健康状态、加载状态和错误信息。

## API Client

| 文件 | 职责 |
| --- | --- |
| `api/http.ts` | Axios 基础客户端、API 错误解析和健康检查 |
| `api/chat.ts` | Chat 请求和 SSE `ReadableStream` 解析 |
| `api/documents.ts` | 资料、Chunk、系统资料状态和一键导入 |
| `api/evaluations.ts` | 评测用例、运行批次和结果 |
| `api/experiments.ts` | 检索策略、Preview、实验和结果 |
| `api/sessions.ts` | 会话与消息历史 |
| `api/missingKnowledge.ts` | 知识缺口查询和维护 |

普通接口使用 Axios JSON 请求。Chat 使用 `fetch` 而不是普通 Axios，是因为需要读取 `ReadableStream`，逐帧解析 `text/event-stream`，及时更新界面。

### Chat SSE 事件

`api/chat.ts` 将事件分发给 `ChatStreamHandlers`：

| 事件 | 前端用途 |
| --- | --- |
| `status` | 更新 `understanding`、`retrieving`、`verifying`、`generating` 阶段提示 |
| `tool` | 展示材料清单等工具执行结果 |
| `token` | 追加回答文本增量 |
| `sources` | 更新引用和来源面板 |
| `done` | 结束流、记录 message id、回答是否拒答和性能数据 |
| `error` | 处理终止性错误 |

`done` 或 `error` 是终止事件；如果响应流在终止事件前意外结束，Client 会报告流异常。

## Components

### 全局组件

| 组件 | 职责 |
| --- | --- |
| `components/AiModeSelector.vue` | 选择全局 AI Mode，并写入 `preferences` Store |

### Chat 组件

| 组件 | 职责 |
| --- | --- |
| `AnswerStyleSwitch.vue` | 通俗解读 / 严谨条款切换 |
| `MarkdownContent.vue` | 渲染回答 Markdown |
| `MarkdownInline.vue` | 渲染行内 Markdown 内容 |
| `MessageList.vue` | 展示会话消息、流式状态和引用关联 |
| `QuestionInput.vue` | 问题输入、提交和中止交互 |
| `SessionHeader.vue` | 当前会话标题和状态 |
| `SourcePanel.vue` | 展示来源文件、Chunk 和证据片段 |
| `ToolPanel.vue` | 展示材料清单等工具执行详情 |

### Documents 组件

| 组件 | 职责 |
| --- | --- |
| `DocumentUpload.vue` | 选择并上传 PDF、DOC、DOCX、MD、TXT |
| `DocumentList.vue` | 资料状态、归属、筛选、重导入和删除 |
| `ChunkList.vue` | 展示指定资料的文本分块 |
| `ChunkDetail.vue` | 查看单个分块的原文和元数据 |

## Types 与工具

| 路径 | 规则 |
| --- | --- |
| `types/api.generated.ts` | 从后端 OpenAPI 生成的 API 类型；禁止手工修改，应从契约重新生成 |
| `types/sse.ts` | Chat 请求、SSE 事件、阶段和计时类型 |
| `types/preferences.ts` | AI Mode、默认值、标签和校验 |
| `types/chat.ts` | `UiMessage` 等前端展示状态类型 |
| `utils/markdown.ts` | Markdown 解析与展示辅助逻辑 |

后端契约变化时，先更新后端 `schemas/contracts.py`，再更新 OpenAPI 生成类型；不要只在 `api.generated.ts` 中补字段来绕过契约。

## AI Mode 的前端流转

```text
AiModeSelector
→ preferences Store
→ localStorage
→ ChatView 读取当前 aiMode
→ api/chat.ts 请求体 ai_mode
→ backend ChatService / runtime snapshot
```

AI Mode 是产品级请求参数，回答风格是独立的 `answer_style` 参数；两者不要混为一个 UI 状态。

## 开发与验证

在仓库根目录执行：

```bash
cd frontend
npm ci
npm run dev
npm test
npm run build
```

修改 Chat、SSE 或 Store 时，至少运行相关测试：

```bash
npm test -- --run tests/ChatView.spec.ts tests/chat.spec.ts tests/AiModeSelector.spec.ts
```

修改资料列表或上传流程时，运行：

```bash
npm test -- --run tests/DocumentList.spec.ts tests/DocumentUpload.spec.ts tests/DocumentsView.spec.ts
```

类型检查由项目配置提供的 `vue-tsc --noEmit` 完成；生产构建前确认 `VITE_API_BASE_URL` 使用同源 `/api` 或明确的开发后端地址。

