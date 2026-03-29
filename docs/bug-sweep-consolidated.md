# Bug Sweep — Consolidated Report (Rounds 2–4)

> **编号范围**：BUG-109 – BUG-144（共 36 条）  
> **前置**：`docs/bug-sweep-ledger.md`（BUG-001 – BUG-108，已存在，不包含在此文件中）  
> **约定**：全部为 `confirmed-by-code`，仅记账不修改代码  
> **基线测试**：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` → **2410 passed, 0 failed**

---

## 扫描方法

**Round 2** — 6 路并行代码审查，覆盖：  
MCP 运行时、全部协议提取器、编译工作流/状态机、IR/增强器/注册表/验证器、
编译 API / 认证授权审计网关、生成器 / 部署 / Helm / 脚本

**Round 3** — 6 路深度审查（3 路因 API rate-limit 中断，补发 2 路），覆盖：  
SSE/gRPC stream 异常处理、状态机并发安全、LLM 客户端 / prompt 模板、
Web-UI SSE hook / store / auth token、Migration / ORM 一致性

**Round 4** — 手动逐文件通读，覆盖：  
全部 7 个提取器（GraphQL/gRPC/OpenAPI/OData/SCIM/JSON-RPC/REST）、
增强器子模块（error_normalizer/examples_generator/prompt_generator/resource_generator/tool_grouping/tool_intent）、
验证器（black_box/pre_deploy/post_deploy/drift/capability_matrix/llm_judge）、
Web-UI（api-client/use-sse/workflow-store/approval-workflow/compilation-wizard/ir-editor/dashboard）

所有候选 bug 均已对照源码逐条验证，剔除误报。

---

# Round 2 — BUG-109 – BUG-122（14 条）

## A. 授权策略评估

### BUG-109 — 策略优先级 `_specificity()` 用 `==` 比较 action，与 `_matches()` 的 glob 不一致

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/access_control/authz/service.py` |

- `_matches()` L177: `fnmatchcase(payload.action, policy.action_pattern)` — glob 匹配
- `_specificity()` L192: `policy.action_pattern == payload.action` — 精确 `==`
- glob 模式（如 `getItem*`）永远拿不到 action 匹配加分
- 多策略冲突时选哪条取决于排序偶然性而非策略精确度

---

## B. 审计日志覆盖缺口

### BUG-110 — Artifact registry 的 create/update/delete/activate 无审计日志

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/compiler_api/routes/artifacts.py` |

- `grep -n 'audit_log\|append_entry' apps/compiler_api/routes/artifacts.py` 返回空
- 对比 `apps/access_control/authz/routes.py` 中 policy 写操作均有 `audit_log.append_entry()`
- 服务定义的关键变更（版本创建、激活、删除）无审计跟踪

### BUG-111 — PAT 创建和撤销没有写审计日志

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/access_control/authn/routes.py` |

- `create_pat()`（L58）创建凭证 + 同步网关、`revoke_pat()`（L95）撤销凭证 + 同步网关，均无审计
- 认证凭证生命周期无法追溯

### BUG-112 — 策略评估 `POST /api/v1/authz/evaluate` 不记录评估请求和结果

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/access_control/authz/routes.py` L167-173 |

- `evaluate_policy()` 直接 `return await service.evaluate(payload)`，无审计调用
- 无法事后审计授权决策

---

## C. MCP 运行时

### BUG-113 — WebSocket 发送阶段未捕获 websocket 异常

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/mcp_runtime/proxy.py` |

- L1236: `await websocket.send(message)` — 无 try-except
- L364-388: 外层 except 只有 `httpx.TimeoutException`、`httpx.HTTPError`、`ToolError`
- `websockets.ConnectionClosed` 等异常不属于上述三类，以原始堆栈暴露给 API 客户端

### BUG-114 — gRPC unary 执行器 `channel.close()` 无 grace period

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/mcp_runtime/grpc_unary.py` L92 vs `grpc_stream.py` L134 |

- `grpc_unary.py` L92: `channel.close()` — 无参数，立即关闭
- `grpc_stream.py` L134: `channel.close(grace=5)` — 允许 5 秒优雅关闭
- 正在进行的 gRPC 操作可能被意外中断

---

## D. 协议提取器

### BUG-115 — SQL 提取器多列外键回退映射到 `"id"`

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/sql.py` L241-243 |

```python
referred_column = referred_columns[index] if index < len(referred_columns) else "id"
```

- `constrained_columns=["user_id", "resource_id"]`，`referred_columns=["user"]`
- `resource_id` 被错误描述为 "References table.id"
- 多列外键场景下生成错误的 tool 参数描述

### BUG-116 — SOAP 提取器 `<wsdl:input>` 缺少 `message` 属性时静默丢弃全部输入参数

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/soap.py` L348 |

- `input_message_name = _qname_local(input_tag.attrib.get("message", ""))` → `""`
- `input_fields = _resolve_wsdl_fields("", ...)` → `[]`
- 操作被创建为零参数，API 不可用

### BUG-117 — SOAP 提取器 `<wsdl:output>` 缺少 `message` 属性时静默丢弃输出 schema

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/soap.py` L352 |

- 同 BUG-116 逻辑，`output_element_name` 回退为空字符串
- 输出响应 schema 丢失

---

## E. 编译工作流

### BUG-118 — `CompilationRequest` 两个 source 字段都 Optional，无 fail-fast 验证

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/compiler_worker/models.py` L73-74 |

- `source_url: str | None = None`，`source_content: str | None = None`
- 无 `model_validator` 确保至少提供一个
- 两者都为 None 时工作流在后续 detect_stage 才报不明确错误

### BUG-119 — Celery task `_run_coro()` 的 `future.result()` 无超时

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/compiler_worker/celery_app.py` L96-97 |

```python
future = executor.submit(asyncio.run, coro)
return future.result()  # timeout=None → 永久阻塞
```

- 如果异步协程挂起，Celery worker 线程永久阻塞，无法回收

---

## F. IR 模型

### BUG-120 — `ServiceIR` 三个 ID 唯一性校验器使用 O(n²) 算法

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/ir/models.py` L595, L637, L645 |

```python
duplicates = {x for x in ids if ids.count(x) > 1}
```

- `list.count()` 对每个元素扫全表，重复三处（operations、resources、prompts）
- 大表面服务（>1000 operations）验证性能退化严重

---

## G. 部署配置

### BUG-121 — `docker-compose.yaml` access-control 使用错误的 JWT 环境变量名

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `deploy/docker-compose.yaml` L89 vs `apps/access_control/authn/service.py` L268 |

- docker-compose L89: `JWT_SECRET_KEY: dev-secret-key`
- 代码实际读取: `os.getenv("ACCESS_CONTROL_JWT_SECRET")`
- dev 模式因有 `"dev-secret"` fallback 而碰巧不崩，但密钥值与预期不一致

### BUG-122 — Helm Chart ConfigMap 仍用旧的未压缩格式，生成器已切换到 gzip

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `deploy/helm/tool-compiler/templates/apps.yaml` L8, L344 vs `libs/generator/generic_mode.py` L20 |

- Helm: `service-ir.json: |` + `SERVICE_IR_PATH: /config/service-ir.json`
- 生成器: `DEFAULT_SERVICE_IR_PATH = "/config/service-ir.json.gz"` + `binaryData`
- Helm 部署与生成器部署路径/格式不一致

---

# Round 3 — BUG-123 – BUG-132（10 条）

## A. MCP 运行时 — 流式传输与 field filter

### BUG-123 — gRPC stream 执行器异常退出时不取消 response stream

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/mcp_runtime/grpc_stream.py` L110-111 |

```python
finally:
    if termination_reason == "max_messages" and hasattr(responses, "cancel"):
        responses.cancel()
```

- 异常退出时 `termination_reason` 仍为 `"completed"`，不等于 `"max_messages"`
- finally 不会取消 stream → server 端流持续发送直到超时

### BUG-124 — SSE 事件收集未处理 `httpx.ReadError` 等网络异常

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/mcp_runtime/proxy.py` L1546-1551 |

- 只捕获 `StopAsyncIteration` 和 `TimeoutError`
- `response.aiter_lines()` 在底层网络断开时抛 `httpx.ReadError`
- 该异常绕过所有 handler，以原始异常传播

### BUG-125 — `_apply_field_filter` 对 list payload 传入空 `array_paths={}`

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/mcp_runtime/proxy.py` L1935-1949 |

```python
return [
    _filter_dict(item, all_inner_fields, nested_paths, {})  # ← {} 丢弃 array_paths
    ...
]
```

- list 类 payload 的数组内嵌套字段过滤静默失效
- 可能返回不应暴露的字段（数据泄漏）

---

## B. 编译工作流 — 状态机与并发

### BUG-126 — `_next_sequence_number()` 用 SELECT max+1，并发产生重复序列号

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/compiler_worker/repository.py` L171-177 |

- 经典 TOCTOU：两个并发调用可读到相同 max 值
- DB 有 `UniqueConstraint("job_id", "sequence_number")`
- 冲突时 `IntegrityError`，但 `append_event` 无重试逻辑 → 工作流中断

### BUG-127 — Rollback workflow 恢复时不检查 active 版本是否已被并发修改

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/compiler_worker/workflows/rollback_workflow.py` L84-96 |

- L84 读取 `current_active`，L96 恢复时仍用 L84 的快照
- 之间无锁；并发 rollback/deployment 可覆盖已激活的新版本

### BUG-128 — `CompilationJob` 表缺少 tenant/environment 列，跨租户数据访问

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/db_models.py` L46-82 |

- `CompilationJob` 无 tenant/environment 列
- `get_job(job_id)` 仅按 UUID 查询
- 任何知道 UUID 的租户都可访问其他租户的编译任务

---

## C. 数据库 — Migration 缺失

### BUG-129 — `ReviewWorkflow` ORM 模型已定义但无对应 migration

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/db_models.py` L278-299 vs `migrations/versions/001_initial.py` |

- db_models.py L278: `class ReviewWorkflow(Base):`
- `001_initial.py` 不包含此表
- ORM 查询/写入会抛 `ProgrammingError: relation "compiler.review_workflows" does not exist`

---

## D. LLM 增强器

### BUG-130 — `VertexAILLMClient.complete()` 每次调用都执行 `vertexai.init()`

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/enhancer/enhancer.py` L225 |

- 每次 `complete()` 都重新导入模块 + 初始化 SDK + 创建模型实例
- 性能退化 + 可能泄漏 SDK 内部连接/资源

### BUG-131 — LLM prompt 模板直接注入未转义的用户输入，prompt injection 风险

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/enhancer/enhancer.py` L433-438, `libs/enhancer/tool_grouping.py` L101-105 |

- `service_name`、`protocol`、`base_url` 来自用户提交的 spec
- 攻击者可注入恶意指令操控 LLM 输出

---

## E. 前端安全

### BUG-132 — SSE 通过 URL query parameter 传递 auth token

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/web-ui/src/lib/hooks/use-sse.ts` L129, `apps/web-ui/src/lib/api-client.ts` L237 |

```typescript
const authUrl = token ? `${url}?token=${encodeURIComponent(token)}` : url;
const es = new EventSource(authUrl);
```

- Token 暴露在浏览器历史、Referrer header、服务端访问日志、代理/CDN 日志
- 应改用 HttpOnly cookie 或 fetch-based SSE

---

# Round 4 — BUG-133 – BUG-144（12 条）

## A. gRPC 提取器

### BUG-133 — 嵌套 message regex 匹配到第一个 `}`，破坏解析

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/grpc.py` L43-46 (`_MESSAGE_PATTERN`) |

```python
_MESSAGE_PATTERN = re.compile(
    r'message\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}', re.DOTALL
)
```

非贪婪 `.*?` + `re.DOTALL` 匹配到第一个 `}`。嵌套 message 示例：

```proto
message Outer { int32 x = 1; message Inner { int32 y = 1; } int32 z = 2; }
```

实际匹配：`Outer` body 截止到 `Inner` 的第一个 `}`，`z` 和整个 `Inner` 丢失。
同样影响 `_SERVICE_PATTERN` 和 `_ENUM_PATTERN`。

### BUG-134 — 带 option block 的 RPC 被静默丢弃

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/grpc.py` L51-56 (`_RPC_PATTERN`) |

- 正则以 `\)\s*;` 结尾，要求分号
- 带 option block 的 RPC（`{ option (google.api.http) = {...}; }`）使用花括号，不匹配
- 使用 HTTP transcoding 注解或任何 RPC option 的服务会丢失这些 RPC

---

## B. OData 提取器

### BUG-135 — 复合键被截断为仅第一个 key

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/odata.py` ~L318 (`_build_entity_set_operations`) |

- 仅使用 `key_props[0]` 构建路径
- 复合键（如 `OrderId + ItemId`）的 GET/UPDATE/DELETE 路径只包含第一个 key
- 生成的路径无效，运行时会 404

---

## C. SCIM 提取器

### BUG-136 — 朴素 `f"{name}s"` 复数化产生错误路径

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/scim.py` L199 |

- `"Entry"` → `"Entrys"`（应为 `"Entries"`），`"Policy"` → `"Policys"`
- REST 提取器有正确的 `_pluralize_resource_name()`，SCIM 未复用

---

## D. JSON-RPC 提取器

### BUG-137 — 未防护的 `method["name"]` 会抛 KeyError

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/jsonrpc.py` ~L193 (`_method_to_operation`) |

- `method_name: str = method["name"]` — 无 `.get()` 防护
- 调用方 `extract()` 对 `methods` 列表无逐项校验
- 单个畸形 method 条目会导致整个提取崩溃

---

## E. REST 提取器

### BUG-138 — `_probe_allowed_methods` OPTIONS 请求不带 auth headers

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/rest.py` ~L710-741 |

```python
response = self._client.options(endpoint.absolute_url)  # 无 auth
```

- 相邻方法 `_probe_and_register` 传递了 `auth_headers`
- 需认证的 API 返回 401/403 → 方法发现静默失效

---

## F. OpenAPI 提取器

### BUG-139 — `$ref` 带 sibling properties 时不解析（OpenAPI 3.1）

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `libs/extractors/openapi.py` ~L185 |

```python
if "$ref" in node and len(node) == 1:  # 只在唯一 key 时解析
```

- OpenAPI 3.1 允许 `{"$ref": "...", "description": "override"}`
- `len(node) == 2` → 不解析 → 下游得到未解引用的 `$ref` 字符串

### BUG-140 — 多个 security scheme 时静默丢弃 ALL auth

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `libs/extractors/openapi.py` L275-281 |

```python
if len(parsed_schemes) > 1:
    return AuthConfig(type=AuthType.none)  # 全部丢弃
```

- 许多真实 API spec 声明 2+ security scheme（如 Bearer + API Key）
- 编译后 tool 使用无认证请求，运行时 401/403

---

## G. Web-UI

### BUG-141 — `auditApi.get()` 全量拉取列表后 client-side 过滤

| Field | Value |
|---|---|
| **Severity** | Low |
| **File** | `apps/web-ui/src/lib/api-client.ts` L697-705 |

```ts
get(entryId: string) {
  return auditApi.list().then((response) => {
    const entry = response.entries.find((item) => item.id === entryId);
    ...
  });
},
```

- 获取单条审计记录需传输全量审计日志，O(n) 网络开销

### BUG-142 — `loadWorkflow` 在 API 失败时缓存假的 "draft" 记录，永久掩盖真实状态

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/web-ui/src/stores/workflow-store.ts` L114-115, L126-138 |

- L114-115: `if (existing) return existing;` — 有缓存直接返回
- L126-138: 任何错误 → 创建 `{ state: "draft" }` 假记录并写入缓存
- 后续调用命中缓存返回假记录，永远不重试 API
- 一次瞬态失败就永久掩盖真实工作流状态（可能是 "published"/"deployed"）

### BUG-143 — Publish/Deploy 副作用 fire-and-forget，状态不一致

| Field | Value |
|---|---|
| **Severity** | Medium |
| **File** | `apps/web-ui/src/components/review/approval-workflow.tsx` L235-249 |

- `transition()` 先成功将状态改为 "published"
- 然后 `artifactApi.activateVersion()` 失败时仅 toast
- 工作流显示 "published" 但 artifact 未激活，无回滚机制
- "deployed" → `gatewayApi.syncRoutes` 同理

### BUG-144 — 编译向导收集 auth 配置但从不发送

| Field | Value |
|---|---|
| **Severity** | High |
| **File** | `apps/web-ui/src/components/compilations/compilation-wizard.tsx` L128-155 |

- Step 3 收集完整 auth 配置（bearer/basic/api_key/custom_header/oauth2）
- `buildRequest()` 不读取任何 auth 字段
- `CompilationCreateRequest`（前后端）均无 auth 字段定义
- 用户填写的所有认证信息在提交时被静默丢弃

---

# 汇总统计

| 批次 | 范围 | 数量 | High | Medium | Low |
|---|---|---|---|---|---|
| Round 2 | BUG-109 – BUG-122 | 14 | 7 | 7 | 0 |
| Round 3 | BUG-123 – BUG-132 | 10 | 5 | 5 | 0 |
| Round 4 | BUG-133 – BUG-144 | 12 | 5 | 6 | 1 |
| **总计** | **BUG-109 – BUG-144** | **36** | **17** | **18** | **1** |

### 按组件分布

| 组件 | 数量 |
|---|---|
| 协议提取器（SQL/SOAP/gRPC/OData/SCIM/JSON-RPC/REST/OpenAPI） | 12 |
| MCP 运行时（proxy/grpc_unary/grpc_stream） | 5 |
| 编译工作流（worker/repository/rollback） | 4 |
| Web-UI（store/api-client/wizard/approval） | 6 |
| 访问控制/审计 | 4 |
| 部署配置（docker-compose/Helm） | 2 |
| IR 模型 | 1 |
| 数据库 migration | 1 |
| LLM 增强器 | 2 |

### 尚未完全覆盖的领域

1. `libs/validator/post_deploy.py` 后半部分边界条件
2. `apps/web-ui/src/components/services/` 组件级逻辑
3. `libs/enhancer/enhancer.py` LLM 重试边界
4. `deploy/helm/` 其余模板
5. `tests/` 与已发现 bug 不对称的测试覆盖缺口
