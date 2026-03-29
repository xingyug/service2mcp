# Bug Sweep Round 2

> 状态：完成  
> 目标：先记账，不修改代码；后续统一验证  
> 编号续前批次（`bug-sweep-ledger.md` 最后为 BUG-108）  
> 当前批次：`BUG-109 – BUG-122`（共 14 条）

## 本批次扫描方式

- 已通读 `new-agent-reading-list.md` 及前批次 `docs/bug-sweep-ledger.md`（108 条）
- 已运行后端全量测试：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q --tb=line`
  - 结果：**2410 passed, 0 failed**
- 分 6 路并行代码审查：
  1. `apps/mcp_runtime/` — MCP 运行时代理、协议执行
  2. `libs/extractors/` — 全部协议提取器
  3. `apps/compiler_worker/` — 编译工作流、状态机、回滚
  4. `libs/ir/` / `libs/enhancer/` / `libs/registry_client/` / `libs/validator/` — IR 模型、增强器、注册表、验证器
  5. `apps/compiler_api/` / `apps/access_control/` — 编译 API、认证/授权/审计/网关
  6. `libs/generator/` / `deploy/` / `scripts/` / `libs/dispatcher/` — 生成器、部署配置、Helm、脚本
- 所有候选 bug 均已对照源码逐条验证，剔除误报

## 记账约定

- `confirmed-by-code`：代码静态对照可直接证明

---

## A. 授权策略评估

109. **BUG-109** — `confirmed-by-code` — High — 策略优先级 `_specificity()` 用 `==` 比较 action，但 `_matches()` 用 `fnmatchcase()`；glob 模式的策略永远拿不到 action 匹配加分，导致多策略冲突时选错策略
   - 文件：`apps/access_control/authz/service.py`
   - 证据：
     - `_matches()` L177: `if not fnmatchcase(payload.action, policy.action_pattern):`
     - `_specificity()` L192: `if policy.action_pattern == payload.action:` — 直接 `==` 不会匹配 glob
   - 后果：当存在两条匹配策略，其中一条 `action_pattern="getItem*"` 另一条 `action_pattern="*"`，两者 specificity 分数相同（action bonus 都是 0），最终选哪条取决于排序偶然性而非策略精确度

---

## B. 审计日志覆盖缺口

110. **BUG-110** — `confirmed-by-code` — Medium — Artifact registry 的 create / update / delete / activate 四个写操作都没有写审计日志
   - 文件：`apps/compiler_api/routes/artifacts.py`
   - 证据：`grep -n 'audit_log\|append_entry' apps/compiler_api/routes/artifacts.py` 返回空；对比 `apps/access_control/authz/routes.py` 中 policy 写操作均有 `audit_log.append_entry()`
   - 后果：服务定义的关键变更（版本创建、激活、删除）无审计跟踪

111. **BUG-111** — `confirmed-by-code` — Medium — PAT 创建和撤销没有写审计日志
   - 文件：`apps/access_control/authn/routes.py`
   - 证据：`grep -n 'audit_log\|append_entry' apps/access_control/authn/routes.py` 返回空；`create_pat()`（L58）创建凭证 + 同步网关、`revoke_pat()`（L95）撤销凭证 + 同步网关，均无审计
   - 后果：认证凭证生命周期无法追溯

112. **BUG-112** — `confirmed-by-code` — Medium — 策略评估 `POST /api/v1/authz/evaluate` 不记录评估请求和结果
   - 文件：`apps/access_control/authz/routes.py` L167-173
   - 证据：`evaluate_policy()` 直接 `return await service.evaluate(payload)`，无审计调用
   - 后果：无法事后审计授权决策，安全监控盲区

---

## C. MCP 运行时

113. **BUG-113** — `confirmed-by-code` — High — WebSocket 发送阶段未捕获 websocket 异常；外层 try-except 只处理 `httpx.TimeoutException`、`httpx.HTTPError`、`ToolError`，websocket 异常会绕过所有错误处理直接传播到调用方
   - 文件：`apps/mcp_runtime/proxy.py`
   - 证据：
     - L1236: `await websocket.send(message)` — 无 try-except 包裹
     - L1227-1241: `async with websockets.connect(...) as websocket:` 内的 send 循环裸奔
     - L364-388: 外层 except 只有 `httpx.TimeoutException`、`httpx.HTTPError`、`ToolError`
     - `websockets.ConnectionClosed` 等异常不属于上述三类，会以原始异常形式暴露给 API 客户端
   - 后果：WebSocket 连接失败时返回内部异常堆栈而非标准 `ToolError`，破坏错误处理契约

114. **BUG-114** — `confirmed-by-code` — Medium — gRPC unary 执行器 `channel.close()` 无 grace period，而 gRPC stream 执行器用 `channel.close(grace=5)`
   - 文件：`apps/mcp_runtime/grpc_unary.py` L92 vs `apps/mcp_runtime/grpc_stream.py` L134
   - 证据：
     - `grpc_unary.py` L92: `channel.close()` — 无参数，立即关闭
     - `grpc_stream.py` L134: `channel.close(grace=5)` — 允许 5 秒优雅关闭
   - 后果：正在进行中的 gRPC 操作可能因 channel 被立即关闭而意外中断

---

## D. 协议提取器

115. **BUG-115** — `confirmed-by-code` — High — SQL 提取器多列外键处理中，当 `constrained_columns` 多于 `referred_columns` 时，多出的列静默回退映射到 `"id"`，生成错误的关系描述
   - 文件：`libs/extractors/sql.py` L241-243
   - 证据：
     ```python
     referred_column = referred_columns[index] if index < len(referred_columns) else "id"
     ```
   - 示例：`constrained_columns=["user_id", "resource_id"]`，`referred_columns=["user"]` → `resource_id` 被错误描述为 "References table.id"
   - 后果：多列外键场景下生成错误的 tool 参数描述

116. **BUG-116** — `confirmed-by-code` — High — SOAP 提取器在 WSDL operation 的 `<wsdl:input>` 缺少 `message` 属性时，静默丢弃全部输入参数
   - 文件：`libs/extractors/soap.py` L348
   - 证据：
     ```python
     input_message_name = _qname_local(input_tag.attrib.get("message", ""))
     # message 缺失 → input_message_name = ""
     input_element_name = messages.get("", "")  # → ""
     input_fields = _resolve_wsdl_fields("", ...)  # → []
     ```
   - 后果：操作被创建为零参数，API 不可用

117. **BUG-117** — `confirmed-by-code` — Medium — SOAP 提取器在 `<wsdl:output>` 缺少 `message` 属性时，静默丢弃输出 schema
   - 文件：`libs/extractors/soap.py` L352
   - 证据：同 BUG-116 逻辑，`output_element_name` 回退为空字符串
   - 后果：输出响应 schema 丢失，无法做响应验证

---

## E. 编译工作流

118. **BUG-118** — `confirmed-by-code` — Medium — `CompilationRequest` 的 `source_url` 和 `source_content` 都是 `Optional`，但没有任何验证确保至少提供一个
   - 文件：`apps/compiler_worker/models.py` L73-74
   - 证据：
     ```python
     source_url: str | None = None
     source_content: str | None = None
     ```
     无 `__post_init__` 或 `model_validator`；两者都为 None 时工作流在后续 detect_stage 才报不明确错误
   - 后果：未能 fail-fast，调试困难

119. **BUG-119** — `confirmed-by-code` — Medium — Celery task 的 `_run_coro()` 在 ThreadPoolExecutor 中调用 `future.result()` 无超时
   - 文件：`apps/compiler_worker/celery_app.py` L96-97
   - 证据：
     ```python
     future = executor.submit(asyncio.run, coro)
     return future.result()  # timeout=None → 永久阻塞
     ```
   - 后果：如果异步协程挂起，Celery worker 线程永久阻塞，无法回收

---

## F. IR 模型

120. **BUG-120** — `confirmed-by-code` — Medium — `ServiceIR` 的三个 ID 唯一性校验器使用 O(n²) 算法
   - 文件：`libs/ir/models.py` L595, L637, L645
   - 证据：
     ```python
     duplicates = {x for x in ids if ids.count(x) > 1}
     ```
     `list.count()` 对每个元素扫全表，重复三处（operations、resources、prompts）
   - 后果：大表面服务（>1000 operations）验证性能退化严重

---

## G. 部署配置

121. **BUG-121** — `confirmed-by-code` — High — `docker-compose.yaml` access-control 服务设置了错误的 JWT 环境变量名
   - 文件：`deploy/docker-compose.yaml` L89 vs `apps/access_control/authn/service.py` L268
   - 证据：
     - docker-compose L89: `JWT_SECRET_KEY: dev-secret-key`
     - 代码实际读取: `os.getenv("ACCESS_CONTROL_JWT_SECRET")`
     - 同文件 L52 (compiler-api) 和 L161 (gateway-admin-mock) 正确使用 `ACCESS_CONTROL_JWT_SECRET`
   - 后果：access-control 永远读不到 docker-compose 配置的密钥；dev 模式因 `load_jwt_settings()` 有 `"dev-secret"` fallback 而碰巧不崩，但密钥值与预期不一致（`"dev-secret"` ≠ `"dev-secret-key"`）

122. **BUG-122** — `confirmed-by-code` — High — Helm Chart 的 ConfigMap 仍用旧的未压缩格式 `data: service-ir.json`，而生成器已切换到 `binaryData: service-ir.json.gz`（压缩二进制）
   - 文件：
     - `deploy/helm/tool-compiler/templates/apps.yaml` L8: `service-ir.json: |`
     - `deploy/helm/tool-compiler/templates/apps.yaml` L344: `SERVICE_IR_PATH: /config/service-ir.json`
     - `libs/generator/generic_mode.py` L20: `DEFAULT_SERVICE_IR_PATH = "/config/service-ir.json.gz"`
     - `libs/generator/templates/configmap.yaml.j2`: 使用 `binaryData` + gzip
   - 证据：生成器在 BUG-002 修复中已切换到压缩格式，但 Helm Chart 未同步更新
   - 后果：通过 Helm 部署的 runtime 使用不同的 IR 格式和路径，与生成器部署路径不一致；如果 runtime 代码只处理一种格式，另一条路径会失败

---

## 当前小结

- 本批新增：**14** 条（BUG-109 – BUG-122）
- 累计（含 `bug-sweep-ledger.md`）：**122** 条
- 严重性分布：
  - High：**7** 条（BUG-109, 113, 115, 116, 121, 122）
  - Medium：**7** 条（BUG-110, 111, 112, 114, 117, 118, 119, 120）
- 全部为 `confirmed-by-code`
- 建议下一轮继续扩展：
  1. `libs/extractors/` 其他提取器（GraphQL、gRPC、OpenAPI、OData、SCIM、JSON-RPC、AsyncAPI）的边界条件
  2. `apps/mcp_runtime/proxy.py` 的 SSE/gRPC stream 异常处理对称性
  3. `apps/compiler_worker/workflows/` 的状态机完整性与 partial rollback 语义
  4. `libs/enhancer/` LLM 客户端超时与重试策略
  5. `tests/` 中与已修复 bug 不对称的测试覆盖缺口
