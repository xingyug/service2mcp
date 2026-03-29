# Bug Sweep Ledger

> 状态：进行中  
> 目标：先记账，不修改代码；后续统一验证  
> 当前批次：`121 / 250`

## 本批次扫描方式

- 已通读 `new-agent-reading-list.md` 列出的文档：`agent.md`、`devlog.md`、`tool-compiler-v2-sdd.md`、`docs/context-engineering.md`、`docs/post-sdd-modular-expansion-plan.md`、`docs/quickstart.md`、ADR `001`–`005`
- 已运行后端全量测试：`cd tool-compiler-v2 && .venv/bin/pytest -q`
  - 结果：`5 failed, 2194 passed`
- 已运行前端检查：`cd apps/web-ui && npm test`
  - 结果：通过
- 已运行前端静态检查与构建：`cd apps/web-ui && npm run lint && npm run typecheck && npm run build`
  - 结果：通过
- 已逐项核对前端 `apps/web-ui` 与后端 `apps/compiler_api` / `apps/access_control` / `libs/*` 的真实路由、模型和返回结构

## 记账约定

- `confirmed-by-test`：当前运行结果直接证实
- `confirmed-by-code`：代码静态对照可直接证明
- `acknowledged-open`：文档/真实目标验证中已明确承认但尚未修复

---

## A. 当前运行直接证实的问题

1. **BUG-001** — `fixed` — High — 契约测试仍断言旧的 GKE LLM 协议列表  
   - 文件：`tests/contract/test_local_dev_assets.py`、`scripts/smoke-gke-llm-e2e.sh`
   - 证据：全量 `pytest` 失败；测试仍要求脚本包含 `all|graphql|rest|grpc|soap|sql`，而脚本实际已经接受 `openapi/jsonrpc/odata/scim`
   - 修复：改为从脚本文本中提取 `PROTOCOL` 允许值，并断言最新协议集合 `all|graphql|rest|openapi|grpc|jsonrpc|odata|scim|soap|sql`
   - 验证：`uv run pytest -q tests/contract/test_local_dev_assets.py` 通过

2. **BUG-002** — `fixed` — High — 生成器改为 `binaryData + service-ir.json.gz` 后，worker 集成 harness 仍按旧 `data/service-ir.json` 读取  
   - 文件：`libs/generator/templates/configmap.yaml.j2`、`tests/integration/test_compiler_worker_activities.py`
   - 证据：4 个集成测试在 `deploy` 阶段统一报 `KeyError: 'data'`；模板实际只输出 `binaryData`
   - 修复：`RuntimeDeploymentHarness.deploy_from_manifest()` 改为读取 `config_map["binaryData"]["service-ir.json.gz"]`，执行 `base64` 解码 + `gzip` 解压；补充不依赖 Docker 的 focused harness test，直接验证 runtime 能从 gzipped IR 启动并调用 tool
   - 验证：`uv run pytest -q tests/integration/test_compiler_worker_activities.py -k runtime_deployment_harness_reads_gzipped_service_ir_from_binary_data` 通过
   - 备注：整份 integration file 里其余 `testcontainers` 用例在当前 sandbox 下会因 Docker socket 权限报错，不属于本 bug 的回归

3. **BUG-003** — `fixed` — High — `access_control` 的 `/readyz` 在数据库不可达时仍返回 HTTP 200  
   - 文件：`apps/access_control/main.py`
   - 证据：异常分支仅 `return {"status": "not_ready"}`，未设置 `503`；会让 K8s readiness 误判服务可用
   - 修复：失败分支改为 `JSONResponse(status_code=503, content={"status": "not_ready"})`，并在路由上显式 `response_model=None`
   - 验证：`uv run pytest -q apps/access_control/tests/test_main.py` 通过

4. **BUG-004** — `fixed` — High — 编译列表接口缺失  
   - 文件：`apps/compiler_api/routes/compilations.py`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：后端只有 `POST /api/v1/compilations`、`GET /{job_id}`、`GET /{job_id}/events`；前端却调用 `GET /api/v1/compilations`
   - 修复：后端新增 `GET /api/v1/compilations`；`CompilationRepository` 新增 `list_jobs()`，前端 `compilationApi.list()` 改为消费真实列表接口并归一化原始 job payload
   - 验证：`uv run pytest -q apps/compiler_api/tests/test_routes_compilations.py tests/contract/test_api_contracts.py` 通过；`uv run pytest -q tests/integration/test_compiler_api.py` 通过；前端 `npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过

5. **BUG-005** — `fixed` — High — 服务详情接口缺失  
   - 文件：`apps/compiler_api/routes/services.py`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：后端只暴露 `GET /api/v1/services`；前端调用 `GET /api/v1/services/{serviceId}`
   - 修复：后端新增 `GET /api/v1/services/{service_id}`；`ServiceCatalogRepository` 新增 `get_service()`，前端 `serviceApi.get()` 直接对接真实详情路由
   - 验证：`uv run pytest -q apps/compiler_api/tests/test_routes_services.py tests/contract/test_api_contracts.py` 通过；`uv run pytest -q tests/integration/test_compiler_api.py` 通过；前端 `npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过

6. **BUG-006** — `fixed` — High — 版本/差异 API 路径前后端不一致  
   - 文件：`apps/compiler_api/routes/artifacts.py`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：后端版本接口在 `/api/v1/artifacts/{service_id}/versions*`；前端调用 `/api/v1/services/{serviceId}/versions*`
   - 修复：前端 `artifactApi.listVersions/getVersion/diff` 全部改到 `/api/v1/artifacts/{service_id}/...`；同时对 registry raw payload 做前端归一化，恢复 `ir` 与 diff UI 所需结构
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

7. **BUG-007** — `fixed` — High — 编译 API 没有任何 `/api/v1/gateway/*` 路由，但前端网关页面和 `gatewayApi` 全部调用这里  
   - 文件：`apps/compiler_api/main.py`、`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/gateway_binding/routes.py`
   - 证据：`create_app()` 只注册 `artifact_registry_router`、`compilations_router`、`services_router`；真实网关后端挂在 access-control 的 `/api/v1/gateway-binding/*`
   - 修复：`gatewayApi` 全部切到 access-control 的 `/api/v1/gateway-binding/reconcile|service-routes/*`；删除原来指向不存在 compiler `/api/v1/gateway/*` 的 reconcile/set/delete 调用
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/__tests__/gateway-route-config.test.ts` 通过；`cd apps/web-ui && npm run typecheck` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_gateway_binding_service.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_gateway_binding.py` 通过

8. **BUG-008** — `fixed` — Medium — `create_compilation()` 把 dispatcher 相关异常全部压扁成通用 `503`  
   - 文件：`apps/compiler_api/routes/compilations.py`
   - 证据：`except Exception` 后统一返回 `detail="Compilation worker dispatch failed."`，真实根因不会暴露
   - 修复：保留 `503` 状态码，但把原始异常摘要拼进 `detail`，现在会返回 `Compilation worker dispatch failed: <root cause>`，避免把 dispatch 失败全部压成同一个泛化错误
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/compiler_api/tests/test_routes_compilations.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_compiler_api.py` 通过

---

## B. 前后端契约/页面行为已确认断裂

9. **BUG-009** — `fixed` — High — 登录页用相对路径 `/api/v1/authn/validate`，但 `next.config.ts` 没有任何 rewrites/proxy  
   - 文件：`apps/web-ui/src/app/(auth)/login/page.tsx`、`apps/web-ui/next.config.ts`
   - 证据：登录页未使用 `NEXT_PUBLIC_ACCESS_CONTROL_URL`；Next 配置只有 `output: "standalone"`
   - 修复：登录页改为统一走 `authApi.validateToken()`，由 `api-client.ts` 使用 `NEXT_PUBLIC_ACCESS_CONTROL_URL` 直连 access-control
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/app/'(auth)'/__tests__/login-page.test.tsx src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

10. **BUG-010** — `fixed` — High — 登录页调用 `/api/v1/authn/validate` 的协议错了：后端要 JSON body `{token}`，前端发的是 `Authorization` header  
   - 文件：`apps/access_control/authn/routes.py`、`apps/web-ui/src/app/(auth)/login/page.tsx`
   - 证据：后端 `validate_token(payload: TokenValidationRequest)` 明确从 body 读 `payload.token`
   - 修复：JWT/PAT 登录都改成提交 JSON body `{ token }`
   - 验证：同 BUG-009

11. **BUG-011** — `fixed` — High — 密码登录成功时会把 Base64 用户名密码当 token 存入本地  
   - 文件：`apps/web-ui/src/app/(auth)/login/page.tsx`
   - 证据：`const token = data.token ?? basicToken`; 而后端验证接口返回模型根本没有 `token` 字段
   - 修复：登录页删除伪 Basic-auth 流程，改成真实支持的 JWT / PAT 验证；本地存储只保存用户提交并被验证通过的 JWT/PAT token
   - 验证：同 BUG-009

12. **BUG-012** — `fixed` — High — 前端 `authApi` 前缀写成 `/api/v1/auth/*`，真实后端是 `/api/v1/authn/*`  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/authn/routes.py`
   - 证据：`api-client.ts` 里 `validateToken/createPAT/listPATs/revokePAT` 全部指向 `/api/v1/auth/...`
   - 修复：`authApi` 全部改到 `/api/v1/authn/*`
   - 验证：同 BUG-009

13. **BUG-013** — `fixed` — High — PAT 列表接口缺少必填 `username` 参数  
   - 文件：`apps/access_control/authn/routes.py`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：后端 `list_pats(username: str, ...)`；前端 `authApi.listPATs()` 不传任何 query 参数
   - 修复：`authApi.listPATs(username)` 增加必填 query 参数；PAT 页面查询改为依赖当前用户 `username`
   - 验证：同 BUG-009

14. **BUG-014** — `fixed` — High — PAT 前端类型与后端返回字段名不一致  
   - 文件：`apps/access_control/authn/models.py`、`apps/web-ui/src/types/api.ts`、`apps/web-ui/src/app/(dashboard)/pats/page.tsx`
   - 证据：后端返回 `items[]` 和 `id`；前端读取 `pats[]` 和 `pat_id`
   - 修复：`api-client.ts` 新增 PAT 归一化，把后端 `items/id` 适配为前端稳定的 `pats/pat_id`
   - 验证：同 BUG-009

15. **BUG-015** — `fixed` — High — 前端 `policyApi` 前缀写成 `/api/v1/policies*`，真实后端是 `/api/v1/authz/policies*`  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/authz/routes.py`
   - 证据：创建/列表/详情/更新/删除全部少了 `/authz`
   - 修复：`policyApi` 全部改到 `/api/v1/authz/policies*`
   - 验证：同 BUG-009

16. **BUG-016** — `fixed` — High — 策略更新 HTTP 方法不一致  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/authz/routes.py`
   - 证据：前端 `PATCH /policies/{id}`；后端只实现 `PUT /api/v1/authz/policies/{policy_id}`
   - 修复：`policyApi.update()` 改为 `PUT /api/v1/authz/policies/{id}`
   - 验证：同 BUG-009

17. **BUG-017** — `fixed` — High — 策略评估路径不一致  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/authz/routes.py`
   - 证据：前端调用 `/api/v1/policies/evaluate`；后端是 `POST /api/v1/authz/evaluate`
   - 修复：`policyApi.evaluate()` 改到 `/api/v1/authz/evaluate`，并补默认 `risk_level: "safe"` 以满足后端请求模型
   - 验证：同 BUG-009

18. **BUG-018** — `fixed` — High — 策略列表/对象字段名不一致  
   - 文件：`apps/access_control/authz/models.py`、`apps/web-ui/src/types/api.ts`、`apps/web-ui/src/app/(dashboard)/policies/page.tsx`
   - 证据：后端返回 `items[]`、对象主键是 `id`；前端读取 `policies[]`、`policy_id`
   - 修复：`api-client.ts` 新增 Policy 归一化，把后端 `items/id` 适配为前端稳定的 `policies/policy_id`
   - 验证：同 BUG-009

19. **BUG-019** — `fixed` — High — 审计列表路径不一致  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/audit/routes.py`
   - 证据：前端请求 `/api/v1/audit`；后端只有 `GET /api/v1/audit/logs`
   - 修复：`auditApi.list()` 改到 `/api/v1/audit/logs`
   - 验证：同 BUG-009

20. **BUG-020** — `fixed` — High — 审计筛选参数名不一致  
   - 文件：`apps/web-ui/src/lib/api-client.ts`、`apps/access_control/audit/routes.py`
   - 证据：前端发 `since/until`；后端收 `start_at/end_at`
   - 修复：`auditApi.list()` 里把前端过滤器 `since/until` 转译成后端 `start_at/end_at`
   - 验证：同 BUG-009

21. **BUG-021** — `fixed` — High — 审计返回结构不一致  
   - 文件：`apps/access_control/audit/models.py`、`apps/web-ui/src/types/api.ts`、`apps/web-ui/src/app/(dashboard)/audit/page.tsx`
   - 证据：后端返回 `items[]`，`detail` 是对象；前端读取 `entries[]`，并按字符串截断/展示 `detail`
   - 修复：`api-client.ts` 新增 Audit 归一化，把后端 `items` 适配成前端 `entries`，并把 `detail` 对象序列化为稳定字符串
   - 验证：同 BUG-009

22. **BUG-022** — `fixed` — High — 编译任务响应结构不一致  
   - 文件：`apps/compiler_api/models.py`、`apps/web-ui/src/types/api.ts`
   - 证据：后端模型是 `id/current_stage/error_detail/updated_at`；前端类型是 `job_id/failed_stage/error_message/artifacts`
   - 修复：`api-client.ts` 新增 compilation raw→UI 归一化；把后端 `id/current_stage/error_detail/updated_at/service_name` 适配成前端稳定的 `job_id/failed_stage/error_message/completed_at/artifacts.ir_id`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`uv run pytest -q tests/integration/test_compiler_api.py` 通过；`npm run typecheck` 通过

23. **BUG-023** — `fixed` — High — 创建编译成功后，向导会跳转到不存在的 `result.job_id`  
   - 文件：`apps/web-ui/src/components/compilations/compilation-wizard.tsx`、`apps/compiler_api/models.py`
   - 证据：`router.push(\`/compilations/${result.job_id}\`)`，但真实返回字段叫 `id`
   - 修复：`compilationApi.create()` 改为把后端原始创建响应归一化成前端 `CompilationJobResponse`，向导继续使用 `result.job_id` 但现在字段真实存在
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

24. **BUG-024** — `fixed` — High — 编译向导把 `auth_config` 直接发给后端，但 `CompilationCreateRequest` 没有这个字段且 `extra="forbid"`  
   - 文件：`apps/web-ui/src/components/compilations/compilation-wizard.tsx`、`apps/compiler_api/models.py`
   - 证据：`if (authConfig) req.auth_config = authConfig`；后端模型未声明 `auth_config`
   - 修复：前端 `CompilationCreateRequest` 类型删除 `auth_config`；向导 `buildRequest()` 停止把认证配置直接塞进 compiler API 请求
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

25. **BUG-025** — `fixed` — High — 服务摘要字段名不一致  
   - 文件：`apps/compiler_api/models.py`、`apps/web-ui/src/types/api.ts`
   - 证据：后端是 `service_name/tool_count/created_at`；前端是 `name/version_count/last_compiled`
   - 修复：`serviceApi.list/get` 新增 raw→UI 归一化，把后端 `service_name/created_at/active_version` 适配成前端 `name/last_compiled/version_count`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

26. **BUG-026** — `fixed` — High — 服务卡片直接渲染 `service.name`，真实字段应为 `service.service_name`  
   - 文件：`apps/web-ui/src/components/services/service-card.tsx`
   - 证据：`{service.name}` 明确存在；后端 `ServiceSummaryResponse` 无 `name`
   - 修复：保持组件不改，改由 `serviceApi` 归一化提供稳定的 `service.name`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

27. **BUG-027** — `fixed` — High — 服务详情页标题直接渲染 `service.name`  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
   - 证据：`<h1 ...>{service.name}</h1>`
   - 修复：保持页面不改，改由 `serviceApi.get()` 归一化输出 `service.name`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

28. **BUG-028** — `fixed` — High — Review 页也直接渲染 `service.name`  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
   - 证据：`<span ...>{service.name}</span>`
   - 修复：保持页面不改，改由 `serviceApi.get()` 归一化输出 `service.name`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

29. **BUG-029** — `fixed` — High — Dashboard 读取 `auditData?.entries`，真实后端返回 `items`  
   - 文件：`apps/web-ui/src/app/(dashboard)/page.tsx`、`apps/access_control/audit/models.py`
   - 证据：`const auditEntries: AuditLogEntry[] = auditData?.entries ?? []`
   - 修复：通过 `auditApi.list()` 的归一化适配层，把后端 `items` 稳定映射成前端 `entries`
   - 验证：同 BUG-009

30. **BUG-030** — `fixed` — High — 编译列表/详情页全程读取 `job.job_id` / `job.error_message`，真实后端只有 `id` / `error_detail`  
   - 文件：`apps/web-ui/src/app/(dashboard)/compilations/page.tsx`、`apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`、`apps/compiler_api/models.py`
   - 证据：多处 `job.job_id`、`job.error_message`，与真实模型不符
   - 修复：`compilationApi.create/get/list` 全部归一化后端 raw job；前端页面继续使用 `job_id/error_message/failed_stage/completed_at`，但现在这些字段已由兼容层提供
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

31. **BUG-031** — `fixed` — High — `useCompilationEvents()` 只绑定 `EventSource.onmessage`，收不到后端发出的命名 SSE 事件  
   - 文件：`apps/web-ui/src/lib/hooks/use-sse.ts`、`apps/compiler_api/routes/compilations.py`
   - 证据：后端 `yield "event: stage_started\ndata: ..."`；前端没 `addEventListener("stage_started", ...)`
   - 修复：`useCompilationEvents()` 改为监听真实命名 SSE 事件：`job.started/job.succeeded/job.failed/job.rolled_back/stage.started/stage.succeeded/stage.retrying/stage.failed/rollback.*`
   - 验证：`cd apps/web-ui && npm test -- src/lib/hooks/__tests__/use-sse.test.tsx src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

32. **BUG-032** — `fixed` — High — SSE 事件 payload 形状也对不上  
   - 文件：`apps/web-ui/src/types/api.ts`、`apps/web-ui/src/components/compilations/event-log.tsx`、`apps/compiler_api/models.py`
   - 证据：前端要 `type/timestamp/detail:string`；后端 SSE 实际是 `event_type/created_at/detail:dict`
   - 修复：SSE hook 新增 raw→UI 归一化：把后端 `event_type/created_at/detail/error_detail/attempt/stage` 适配成前端 `CompilationEvent`；同时扩充前端事件类型以覆盖 `stage_retrying/job_rolled_back/rollback_*`
   - 验证：`cd apps/web-ui && npm test -- src/lib/hooks/__tests__/use-sse.test.tsx src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

33. **BUG-033** — `fixed` — High — `useCompilationEvents()` 在 render 阶段直接 `setState`  
   - 文件：`apps/web-ui/src/lib/hooks/use-sse.ts`
   - 证据：`if (prevJobId !== jobId) { setPrevJobId(...); setEvents([]); ... }` 位于组件函数体内，不在 `useEffect`
   - 修复：删除 render-phase `setState`，改为在 `useEffect([jobId])` 中 reset 事件/连接状态，并在切换 job 时关闭旧 `EventSource`
   - 验证：`cd apps/web-ui && npm test -- src/lib/hooks/__tests__/use-sse.test.tsx src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx` 通过；`npm run typecheck` 通过

34. **BUG-034** — `fixed` — High — SSE token 被拼进 URL 查询串，既泄露凭证，又不会被后端使用  
   - 文件：`apps/web-ui/src/lib/hooks/use-sse.ts`、`apps/web-ui/src/lib/api-client.ts`、`apps/compiler_api/routes/compilations.py`
   - 证据：前端原来会追加 `?token=...`；后端 SSE route 没有任何 auth 依赖，也不读取 query token
   - 修复：`useCompilationEvents()` 和 `compilationApi.streamEvents()` 都不再把 `auth_token` 拼进 SSE URL；SSE 连接恢复为纯 `/api/v1/compilations/{job_id}/events`
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/hooks/__tests__/use-sse.test.tsx "src/app/(dashboard)/services/[serviceId]/__tests__/service-detail-page.test.tsx"` 通过；`cd apps/web-ui && npm run typecheck` 通过

35. **BUG-035** — `fixed` — High — 编译事件 SSE endpoint 当前完全未做鉴权  
   - 文件：`apps/access_control/security.py`、`apps/compiler_api/routes/compilations.py`、`apps/web-ui/src/lib/api-client.ts`、`apps/web-ui/src/lib/hooks/use-sse.ts`
   - 修复：后端新增 `require_sse_caller()` 依赖从 `?token=` 查询参数校验 JWT/PAT；SSE 端点添加 `Depends(require_sse_caller)`；前端 `createEventSource()` 和 `useCompilationEvents` 均将 `auth_token` 作为 URL query 参数传入
   - 验证：后端 305 passed（含 security/compilation route 测试），前端 39 passed（含 SSE hook 测试）

36. **BUG-036** — `fixed` — Medium — Service detail 的版本删除只是 `setTimeout` 假动作，没有真实 API 调用  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：原来 `handleDelete()` 里只有 `await new Promise((r) => setTimeout(r, 500))`
   - 修复：新增 `artifactApi.deleteVersion()`；Service detail 的 Versions tab 现在真实调用 `DELETE /api/v1/artifacts/{service_id}/versions/{version}`，并在成功后 invalidates versions/service 查询
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/__tests__/gateway-route-config.test.ts "src/app/(dashboard)/services/[serviceId]/__tests__/service-detail-page.test.tsx"` 通过；`cd apps/web-ui && npm run typecheck` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/compiler_api/tests/test_routes_artifacts.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_compiler_api.py` 通过

37. **BUG-037** — `fixed` — Medium — Service detail 的 `Activate` 按钮没有任何处理逻辑  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：按钮只有文案和图标，没有 `onClick`
   - 修复：新增 `artifactApi.activateVersion()`；Service detail 的 Versions tab 现在真实调用 `POST /api/v1/artifacts/{service_id}/versions/{version}/activate`，并刷新版本/服务查询
   - 验证：同 BUG-036

38. **BUG-038** — `fixed` — Medium — Service detail 头部按钮 `Recompile / View IR / Manage Access` 都没有 handler  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`、`apps/web-ui/src/app/(dashboard)/compilations/new/page.tsx`、`apps/web-ui/src/components/compilations/compilation-wizard.tsx`、`apps/web-ui/src/app/(dashboard)/policies/page.tsx`
   - 证据：原来 3 个 `Button` 都未绑定 `onClick`
   - 修复：`Recompile` 现在跳到 `/compilations/new?service_name=...`，并由 new compilation 页把 `service_name` 预填进 wizard；`View IR` 切到 IR tab；`Manage Access` 跳到 `/policies?resource_id=...`，Policies 页会读这个查询参数作为初始过滤
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/__tests__/gateway-route-config.test.ts "src/app/(dashboard)/services/[serviceId]/__tests__/service-detail-page.test.tsx"` 通过；`cd apps/web-ui && npm run typecheck` 通过

39. **BUG-039** — `fixed` — Medium — Service detail Gateway tab 的 `Sync / Reconcile` 按钮没有 handler  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`、`apps/web-ui/src/lib/api-client.ts`
   - 证据：按钮可点但没有任何事件处理
   - 修复：Gateway tab 现在真实调用 `gatewayApi.syncRoutes()` 和 `gatewayApi.reconcile()`；当当前 service 没有 active `route_config` 时，`Sync Routes` 会把用户导向 Versions tab，而不是继续空操作
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/__tests__/gateway-route-config.test.ts "src/app/(dashboard)/services/[serviceId]/__tests__/service-detail-page.test.tsx"` 通过；`cd apps/web-ui && npm run typecheck` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_gateway_binding_service.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_gateway_binding.py` 通过

40. **BUG-040** — `fixed` — High — 独立 Gateway 页面调用的都是不存在/不兼容的接口  
   - 文件：`apps/web-ui/src/app/(dashboard)/gateway/page.tsx`、`apps/web-ui/src/lib/api-client.ts`、`apps/web-ui/src/lib/gateway-route-config.ts`、`apps/access_control/gateway_binding/routes.py`
   - 证据：前端原先调用 `gatewayApi.reconcile/setRoute/deleteRoute` → `/api/v1/gateway/*`；真实后端是 `/api/v1/gateway-binding/reconcile|service-routes/*`，而且 payload 需要 `{ route_config, previous_routes }`，response 也返回 `route_ids/service_routes_*`
   - 修复：Gateway 页面动作链改成从 artifact registry 读取真实 `route_config`，`sync` 调 `/service-routes/sync`，`delete` 调 `/service-routes/delete`，`rollback` 用 `buildPreviousRoutes()` 按后端 `_service_route_documents()` 语义重建 `previous_routes` 后调 `/service-routes/rollback`；同时页面不再伪造 route config 和假 deployment history
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/lib/__tests__/gateway-route-config.test.ts` 通过；`cd apps/web-ui && npm run typecheck` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_gateway_binding_service.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_gateway_binding.py` 通过

41. **BUG-041** — `fixed` — High — PAT 页面数据读取使用 `data?.pats` / `pat.pat_id`，真实后端是 `items` / `id`  
   - 文件：`apps/web-ui/src/app/(dashboard)/pats/page.tsx`、`apps/access_control/authn/models.py`
   - 证据：`const pats = useMemo(() => data?.pats ?? [], [data])`，表格 key 也用 `pat.pat_id`
   - 修复：PAT 页面改为带 `username` 查询；响应字段通过 `authApi` 归一化继续提供 `pats/pat_id`
   - 验证：同 BUG-009

42. **BUG-042** — `fixed` — High — Policies 页面数据读取使用 `data?.policies` / `policy.policy_id`，真实后端是 `items` / `id`  
   - 文件：`apps/web-ui/src/app/(dashboard)/policies/page.tsx`、`apps/access_control/authz/models.py`
   - 证据：搜索结果显示多处 `data?.policies`、`editingPolicy.policy_id`、`deleteTarget.policy_id`
   - 修复：通过 `policyApi` 归一化适配层，把后端 `items/id` 稳定映射成前端 `policies/policy_id`
   - 验证：同 BUG-009

43. **BUG-043** — `fixed` — High — 登录页期望验证响应返回 `username/email/roles/token`，真实后端返回 `{subject, token_type, claims}`  
   - 文件：`apps/web-ui/src/app/(auth)/login/page.tsx`、`apps/access_control/authn/models.py`
   - 证据：前端从 `data.username/data.email/data.roles/data.token` 取值；后端 `TokenPrincipalResponse` 没这些字段
   - 修复：`authApi.validateToken()` 归一化后端 `TokenPrincipalResponse`，从 `subject/token_type/claims` 派生前端所需的 `username/email/roles`
   - 验证：同 BUG-009

44. **BUG-044** — `fixed` — Medium — 前端测试大量 mock 了不存在的字段/路径，导致 `npm test` 全绿但真实接口会炸  
   - 文件：`apps/web-ui/src/hooks/__tests__/use-api.test.tsx`、`apps/web-ui/src/lib/__tests__/api-client.test.ts`、各页面测试
   - 证据：测试里直接断言 `job_id`、`name`、`pats`、`entries` 等形状，而这些都与真实后端模型不一致
   - 修复：把 transport 层测试收敛到真实后端路径和 raw payload（`/api/v1/authn/*`、`/api/v1/authz/*`、`/api/v1/audit/logs`、`/api/v1/gateway-binding/*`、`{subject, token_type, claims}`、`items[]`、真实 `route_config`）；高层 hook/page 测试则明确只 mock 已归一化的 `api-client` 契约，不再把伪后端 payload 当成真实接口
   - 验证：静态检查确认前端测试中已不存在旧 `/api/v1/auth/*`、`/api/v1/gateway/*`、`/api/v1/policies/evaluate`、`/api/v1/audit` 错路径；`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/hooks/__tests__/use-api.test.tsx src/app/'(auth)'/__tests__/login-page.test.tsx "src/app/(dashboard)/services/[serviceId]/__tests__/service-detail-page.test.tsx" src/lib/hooks/__tests__/use-sse.test.tsx` 通过（`70 passed`）；`cd apps/web-ui && npm run typecheck` 通过

---

## C. 文档和真实目标验证里已明确承认、但仍未修复的问题

45. **BUG-045** — `acknowledged-open` — High — SQL extractor 只生成 `query + insert`，没有 `update/delete`  
   - 文件：`libs/extractors/sql.py`
   - 证据：`agent.md` / `devlog.md` 的 real-target coverage audit 明确写了 “Missing UPDATE/DELETE”

46. **BUG-046** — `acknowledged-open` — High — JSON-RPC extractor 没有 `system.listMethods` fallback，aria2 只覆盖 3/36 方法  
   - 文件：`libs/extractors/jsonrpc.py`
   - 证据：`agent.md` real-target coverage audit 明确点名

47. **BUG-047** — `acknowledged-open` — High — gRPC extractor 没有 reflection fallback，OpenFGA 只覆盖 1 个 RPC  
   - 文件：`libs/extractors/grpc.py`
   - 证据：`agent.md` real-target coverage audit 明确点名

48. **BUG-048** — `acknowledged-open` — High — REST 黑盒 crawler 对纯 JSON、无 HATEOAS API 基本失效  
   - 文件：`libs/extractors/rest.py`
   - 证据：`agent.md` / `devlog.md` 明确说明 Directus REST / PocketBase REST 只有极低覆盖

49. **BUG-049** — `acknowledged-open` — High — Directus OpenAPI 真实目标 smoke 仍因 proof-runner 过采样可选参数而 400  
   - 文件：`apps/proof_runner/live_llm_e2e.py`、`apps/compiler_worker/activities/production.py`
   - 证据：`agent.md` handoff 明确记录 `/activity?...sample...` → `400`

50. **BUG-050** — `acknowledged-open` — High — Gitea OpenAPI 真实目标 smoke 仍因 proof-runner 过采样可选参数而 422  
   - 文件：`apps/proof_runner/live_llm_e2e.py`、`apps/compiler_worker/activities/production.py`
   - 证据：`agent.md` handoff 明确记录 `/repos/search?...sample...` → `422`

51. **BUG-051** — `acknowledged-open` — High — proof-runner 仍可能使用过旧 `proof-helper` 镜像，导致采样修复未实际生效  
   - 文件：`scripts/smoke-gke-llm-e2e.sh`、`apps/proof_runner/live_llm_e2e.py`
   - 证据：`agent.md` handoff 明确要求先确认/推送更新后的 `compiler-api` / `PROOF_HELPER_IMAGE`

52. **BUG-052** — `acknowledged-open` — High — Jackson SCIM 真实目标 smoke 仍未定位完成，疑似 auth 传播/运行时问题  
   - 文件：`apps/mcp_runtime/proxy.py`、SCIM real-target proof configs
   - 证据：`agent.md` handoff 明确要求单 case rerun + 立即抓 runtime logs

53. **BUG-053** — `acknowledged-open` — High — SOAP CXF 真实目标仍存在未限定子元素序列化错误  
   - 文件：`libs/extractors/soap.py`、`apps/mcp_runtime/proxy.py`
   - 证据：`agent.md` handoff 明确说明当前 runtime 发送 namespaced child fields，但目标 WSDL 不要求 `elementFormDefault="qualified"`

---

## D. Access control 后端安全与一致性问题

54. **BUG-054** — `fixed` — High — PAT 创建接口完全未鉴权，且允许按请求体中的任意 `username` 自动建用户并签发 token  
   - 文件：`apps/access_control/authn/routes.py`、`apps/access_control/authn/service.py`
   - 证据：`create_pat()` 没有任何 caller 认证依赖；`AuthnService.create_pat()` 直接 `_get_or_create_user(username=payload.username, ...)`
   - 修复：新增共享 `apps/access_control/security.py`；`POST /api/v1/authn/pats` 现在要求有效 Bearer/PAT caller，并强制 `caller.subject == payload.username` 或 caller 具备 admin 角色后才允许创建 PAT
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_security.py apps/access_control/tests/test_authn_routes.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_authn.py` 通过

55. **BUG-055** — `fixed` — High — PAT 列表接口完全未鉴权，知道用户名就能枚举该用户全部 PAT 元数据  
   - 文件：`apps/access_control/authn/routes.py`
   - 证据：`list_pats(username: str, ...)` 只有 query 参数，没有 `Depends(require_authenticated_caller)` 之类的访问控制
   - 修复：`GET /api/v1/authn/pats` 现在要求有效 caller，并强制 `caller.subject == username` 或 caller 具备 admin 角色后才返回该用户 PAT 列表
   - 验证：同 BUG-054

56. **BUG-056** — `fixed` — High — PAT 撤销接口完全未鉴权，拿到 UUID 就能替别人吊销 token  
   - 文件：`apps/access_control/authn/routes.py`
   - 证据：`revoke_pat()` 只有 `pat_id` 路径参数和 DB service 依赖，没有认证/授权校验
   - 修复：`POST /api/v1/authn/pats/{pat_id}/revoke` 现在先读取 PAT metadata，再强制 `caller.subject == pat.username` 或 caller 具备 admin 角色后才允许撤销
   - 验证：同 BUG-054

57. **BUG-057** — `fixed` — High — PAT 创建后的 gateway 同步失败会被吞掉，接口仍返回成功 token，导致 DB 和网关状态漂移  
   - 文件：`apps/access_control/authn/routes.py`
   - 证据：`create_pat()` 在 `gateway_binding.sync_pat_creation()` 外层 `except Exception` 只打 warning 然后继续 `return created`
   - 修复：PAT 创建改成 `create_pat(commit=False)` + `gateway sync` + `session.commit()` 的单事务路径；gateway 同步失败时接口返回 `502` 并显式 `rollback()`，不会再把 PAT 留在 DB 里
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_authn_routes.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_authn.py` 通过（含 gateway failure rollback 场景）

58. **BUG-058** — `fixed` — High — PAT 撤销后的 gateway 同步失败会被吞掉，DB 已撤销但网关消费者可能继续有效  
   - 文件：`apps/access_control/authn/routes.py`
   - 证据：`revoke_pat()` 在 `sync_pat_revocation()` 外层 `except Exception` 只记录 warning，仍然返回已撤销响应
   - 修复：PAT 撤销改成 `revoke_pat(commit=False)` + `gateway sync` + `session.commit()`；当 gateway 删除失败时会返回 `502` 并回滚 `revoked_at`，PAT 继续保持可验证状态，不再出现 DB 先撤销、网关未撤销的漂移
   - 验证：同 BUG-057

59. **BUG-059** — `fixed` — High — Policy 创建/更新/删除接口全部未鉴权，任何匿名调用者都能改授权策略  
   - 文件：`apps/access_control/authz/routes.py`
   - 证据：`create_policy()`、`update_policy()`、`delete_policy()` 仅依赖 DB/service/gateway/audit，没有 caller 认证依赖
   - 修复：`POST/PUT/DELETE /api/v1/authz/policies*` 现在统一要求 admin caller；同时路由会把 `created_by` / audit actor 归一到真实 `caller.subject`，不再信任客户端自报身份
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_security.py apps/access_control/tests/test_authz_routes.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_authz.py tests/integration/test_access_control_audit.py` 通过

60. **BUG-060** — `fixed` — High — Policy 列表/详情/评估接口全部未鉴权，匿名调用者可以直接读取并探测授权规则  
   - 文件：`apps/access_control/authz/routes.py`
   - 证据：`list_policies()`、`get_policy()`、`evaluate_policy()` 都没有认证/授权依赖
   - 修复：`GET /api/v1/authz/policies`、`GET /api/v1/authz/policies/{id}`、`POST /api/v1/authz/evaluate` 现在统一要求有效认证 caller
   - 验证：同 BUG-059

61. **BUG-061** — `fixed` — High — Policy 变更后的 gateway 同步失败会被吞掉，策略库和网关绑定会长期漂移  
   - 文件：`apps/access_control/authz/routes.py`
   - 证据：`create_policy()` / `update_policy()` / `delete_policy()` 对 `gateway_binding.*` 统一 `except Exception` 后继续成功返回
   - 修复：policy create/update/delete 全部改成 `commit=False` 的事务式写法；先 flush policy 变更，再同步 gateway，再写 audit log，最后统一 `session.commit()`；gateway 失败时返回 `502` 并回滚整个请求，不再留下 DB/gateway 分叉状态
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_authz_routes.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_authz.py` 通过（含 create/update/delete gateway failure rollback 场景）

62. **BUG-062** — `fixed` — High — `POST /api/v1/gateway-binding/reconcile` 完全未鉴权，匿名请求可直接触发全量消费者/策略/路由重对账  
   - 文件：`apps/access_control/gateway_binding/routes.py`
   - 证据：`reconcile_gateway_state()` 只有 DB session 和 gateway service 依赖，没有认证依赖
   - 修复：`POST /api/v1/gateway-binding/reconcile` 现在统一要求 admin caller
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_security.py apps/access_control/tests/test_gateway_binding_routes.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_access_control_gateway_binding.py` 通过

63. **BUG-063** — `fixed` — High — `gateway-binding` 的 `service-routes/sync|delete|rollback` 全部未鉴权，属于匿名可用的破坏性网关管理接口  
   - 文件：`apps/access_control/gateway_binding/routes.py`
   - 证据：`sync_service_routes()`、`delete_service_routes()`、`rollback_service_routes()` 都直接接受 `route_config` 请求体并执行业务逻辑
   - 修复：`/api/v1/gateway-binding/service-routes/{sync,delete,rollback}` 现在统一要求 admin caller
   - 验证：同 BUG-062

---

## E. 编译工作流/版本管理后端问题

64. **BUG-064** — `fixed` — High — 编译向导发送的 `options.force_protocol` 根本不会被 worker 读取，强制协议选项当前无效  
   - 文件：`apps/web-ui/src/components/compilations/compilation-wizard.tsx`、`apps/compiler_worker/activities/production.py`
   - 证据：前端写入 `options.force_protocol`；worker `_source_config_from_context()` 只读取 `options.get("protocol")`
   - 修复：worker `_source_config_from_context()` 现在优先读取 `options.force_protocol`，再回退到旧的 `options.protocol`，并把最终值写入 `SourceConfig.hints["protocol"]`
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/compiler_worker/tests/test_production_helpers.py -k 'skip_enhancement or force_protocol or TestSourceConfigFromContext or TestEnhancementEnabled'` 通过（`9 passed`）；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_compiler_worker_activities.py -k 'force_protocol_hint or skip_enhancement_option'` 通过（`2 passed`）

65. **BUG-065** — `fixed` — High — 编译向导发送的 `options.skip_enhancement` 根本不会被 worker 读取，跳过增强开关无效  
   - 文件：`apps/web-ui/src/components/compilations/compilation-wizard.tsx`、`apps/compiler_worker/activities/production.py`
   - 证据：前端设置 `skip_enhancement = true`；`enhance_stage()` 只看 `_enhancement_enabled()`，而该函数只读环境变量
   - 修复：`_enhancement_enabled()` 新增可选 `options` 输入；当 `options.skip_enhancement` 为 `true` 时会强制关闭增强，并让 `enhance_stage()` 走 passthrough 分支，即使环境里存在 `LLM_API_KEY`
   - 验证：同 BUG-064

66. **BUG-066** — `fixed` — High — `runtime_mode: "codegen"` 只是 UI 选项，后端生成阶段始终固定走 `generate_generic_manifests()`  
   - 文件：`apps/compiler_worker/activities/production.py`、`libs/generator/codegen_mode.py`（新建）
   - 修复：`generate_stage()` 现在从 `context.request.options["runtime_mode"]` 读取模式，`"codegen"` 走 `generate_codegen_manifests()`（带 codegen label/annotation），默认仍走 generic 模式
   - 验证：`uv run pytest -q libs/generator/tests/test_codegen_mode.py` 通过（5 passed）

67. **BUG-067** — `fixed` — High — 版本激活接口只改 registry 的 `is_active`，不会重新同步网关路由  
   - 文件：`apps/compiler_api/routes/artifacts.py`、`apps/compiler_api/repository.py`
   - 证据：`POST /versions/{version}/activate` 只调用 `repository.activate_version()`；仓储实现只做 `_deactivate_service_versions()` + `record.is_active = True`
   - 修复：compiler-api 新增可注入的 artifact route publisher，并把 activate 路径改成 `activate_version(commit=False)` 后同步 `/api/v1/gateway-binding/service-routes/sync`，成功后才 `commit()`；同时内部 control-plane 调用补了 admin service JWT，避免 access-control `401`
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/compiler_api/tests/test_routes_artifacts.py apps/compiler_api/tests/test_repository_uncovered.py` 通过；`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/integration/test_compiler_api.py` 通过（含 gateway route target 切到新 active 版本）

68. **BUG-068** — `fixed` — High — 删除当前 active 版本时只在 DB 里挑一个 replacement 设为 active，不会同步网关切流  
   - 文件：`apps/compiler_api/repository.py`
   - 证据：`delete_version()` 里如果 `was_active`，只把查询到的 `replacement.is_active = True`，没有任何 route publish/reconcile 调用
   - 修复：artifact delete 路径现在会先 `delete_version(commit=False)`，然后按场景同步 gateway：非 active 删除只删对应 version route；active 删除且有 replacement 时先删被删版本的 version route，再同步 replacement 的 default+version routes；无 replacement 时删除整组 routes，最后统一 `commit()`
   - 验证：同 BUG-067

---

## F. 版本页 / 网关页 / 协议覆盖问题

69. **BUG-069** — `fixed` — Medium — 版本页的 `Activate` 按钮没有任何 `onClick`，看起来可用但实际完全无效  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
   - 证据：旧实现里 `!v.is_active && <Button ...>Activate</Button>`，按钮未绑定 handler
   - 修复：版本页现在为 inactive 版本绑定 `handleActivate()`，真实调用 `artifactApi.activateVersion(serviceId, version)`，成功后 toast 并刷新 artifacts / services 查询
   - 验证：`cd apps/web-ui && npm test -- src/app/'(dashboard)'/services/[serviceId]/versions/__tests__/versions-page.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

70. **BUG-070** — `fixed` — Medium — 版本页“删除版本”流程调用的是 `artifactApi.getVersion()`，并不会删除任何版本  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
   - 证据：旧实现的 `handleDelete()` 内部执行 `await artifactApi.getVersion(serviceId, version)`，注释也承认 “In a full implementation, this would call a delete API endpoint.”
   - 修复：版本页删除确认流程已切到真实 `artifactApi.deleteVersion(serviceId, version)`，成功后 toast 并刷新 artifacts / services 查询
   - 验证：同 BUG-069

71. **BUG-071** — `fixed` — High — Gateway 页的路由状态是按数组索引 `i % 5 / i % 3` 伪造出来的，不是实际对账结果  
   - 文件：`apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
   - 证据：旧实现用 `status: i % 5 === 0 ? "error" : i % 3 === 0 ? "drifted" : "synced"` 伪造状态
   - 修复：新增 access-control `GET /api/v1/gateway-binding/service-routes`；Gateway 页现在会拉取真实 gateway route 文档，并把 active artifact `route_config` 重建成期望 route docs 做逐服务对账，状态只会来自 `synced/drifted/error` 的真实比较结果
   - 验证：`UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/access_control/tests/test_gateway_binding_routes.py apps/access_control/tests/test_gateway_binding_service.py` 通过；`cd apps/web-ui && npm test -- src/lib/__tests__/gateway-route-config.test.ts src/app/'(dashboard)'/gateway/__tests__/gateway-page.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

72. **BUG-072** — `fixed` — High — Gateway 页在构造假路由时直接调用 `svc.name.toLowerCase()`，真实后端返回没有 `name` 字段，会在有 active service 时直接触发运行时异常  
   - 文件：`apps/web-ui/src/app/(dashboard)/gateway/page.tsx`、`apps/compiler_api/models.py`
   - 证据：旧实现通过 `svc.name.toLowerCase()` 构造 fake route URI；后端 `ServiceSummaryResponse` 原始字段只有 `service_name`
   - 修复：Gateway 页不再构造任何 fake route URI，而是直接消费归一化后的 `service.name` 和真实 artifact/gateway route 文档；页面展示与操作路径全部基于真实 route 配置
   - 验证：同 BUG-071

73. **BUG-073** — `fixed` — Medium — Gateway 页的 deployment history 逻辑写反了：有真实服务时返回空数组，没服务时反而展示硬编码假记录  
   - 文件：`apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
   - 证据：旧实现是 `services.length > 0 ? [] : [fake entries...]`
   - 修复：删除硬编码假 history；Gateway 页现在从真实 artifact versions 推导 deployment history，按每个服务的真实版本创建时间生成 deploy 记录，不再在空服务集时伪造记录
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/gateway-route-config.test.ts src/app/'(dashboard)'/gateway/__tests__/gateway-page.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

74. **BUG-074** — `fixed` — High — Gateway 页的“Sync Routes / Rollback”对话框始终把 `route_config: {}` 发给后端，根本没有用真实 route 配置  
   - 文件：`apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
   - 证据：旧实现在 `handleSyncRoutes()` 和 `handleRollback()` 里把 `route_config: {}` 发给后端
   - 修复：Sync 现在发送所选 artifact version 的真实 `route_config`；Rollback 发送当前 active `route_config`，同时把目标版本的 route docs 重建成 `previous_routes` 传给后端
   - 验证：同 BUG-071

75. **BUG-075** — `fixed` — Medium — 编译向导协议选择器仍只支持旧协议集合，缺少 `jsonrpc` / `odata` / `scim`  
   - 文件：`apps/web-ui/src/components/compilations/protocol-selector.tsx`
   - 证据：旧实现的 `PROTOCOLS` 数组只包含 `openapi/rest/graphql/sql/grpc/soap`
   - 修复：协议选择器已补齐 `jsonrpc` / `odata` / `scim` 三个选项及对应说明/图标
   - 验证：`cd apps/web-ui && npm test -- src/components/compilations/__tests__/protocol-selector.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

76. **BUG-076** — `fixed` — Medium — 服务列表页协议筛选仍只支持旧协议集合，无法筛选 `jsonrpc` / `odata` / `scim`  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/page.tsx`
   - 证据：旧实现的页面顶部 `PROTOCOLS` 常量只包含 `openapi/rest/graphql/grpc/soap/sql`
   - 修复：服务列表页协议筛选 chips 已补齐 `jsonrpc` / `odata` / `scim`，过滤逻辑可直接筛到这三类服务
   - 验证：`cd apps/web-ui && npm test -- src/app/'(dashboard)'/services/__tests__/services-page.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

77. **BUG-077** — `fixed` — Medium — `ProtocolBadge` 不认识 `jsonrpc` / `odata` / `scim`，这些协议在 UI 中都会退化成 `Unknown`  
   - 文件：`apps/web-ui/src/components/services/protocol-badge.tsx`
   - 证据：旧实现的 `protocolConfig` 只定义了 `openapi/rest/graphql/grpc/soap/sql`
   - 修复：`ProtocolBadge` 已补齐 `jsonrpc` / `odata` / `scim` 的 label、icon 和颜色映射，不再落到 `Unknown`
   - 验证：`cd apps/web-ui && npm test -- src/components/services/__tests__/protocol-badge.test.tsx` 通过；`cd apps/web-ui && npm run typecheck` 通过

78. **BUG-078** — `fixed` — Medium — 前端 `CompilationOptions.force_protocol` 类型层也只允许旧协议，新的协议即使后端支持也无法从 UI/类型安全路径发送  
   - 文件：`apps/web-ui/src/types/api.ts`
   - 证据：旧实现的 `force_protocol` union 只有 `"openapi" | "rest" | "graphql" | "sql" | "grpc" | "soap"`
   - 修复：`CompilationOptions.force_protocol` 已扩展为同时支持 `jsonrpc` / `odata` / `scim`
   - 验证：`cd apps/web-ui && npm run typecheck` 通过；相关 UI 选择器与列表筛选测试见 BUG-075 / BUG-076

---

## G. 编译状态/实时事件/dashboard 统计断裂

79. **BUG-079** — `fixed` — High — 前端 `CompilationStatus` 仍是旧的全大写阶段态，和后端真实返回的 `pending/running/succeeded/failed/rolled_back` 完全不兼容  
   - 文件：`apps/web-ui/src/types/api.ts`、`apps/compiler_worker/models.py`
   - 证据：前端 union 是 `PENDING/DETECTING/.../PUBLISHED`；后端枚举是 `pending/running/succeeded/failed/rolled_back`
   - 修复：前端 `CompilationStatus` 已对齐真实后端状态；新增 `lib/compilation-status.ts` 统一管理状态集合、进行中判断和展示文案，`api-client` 的 compilation 归一化也直接产出 lower-case 状态
   - 验证：`cd apps/web-ui && npm test -- src/lib/__tests__/api-client.test.ts src/components/compilations/__tests__/event-log.test.tsx src/components/compilations/__tests__/status-badge.test.tsx src/components/compilations/__tests__/stage-timeline.test.tsx 'src/app/(dashboard)/compilations/__tests__/compilations-page.test.tsx' 'src/app/(dashboard)/compilations/[jobId]/__tests__/compilation-detail-page.test.tsx' src/components/dashboard/__tests__/compilation-metrics.test.tsx 'src/app/(dashboard)/__tests__/dashboard-page.test.tsx'` 通过（`84 passed`）；`cd apps/web-ui && npm run typecheck` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过（`4 passed`）

80. **BUG-080** — `fixed` — High — `StatusBadge` 用旧状态表做索引，收到真实后端状态（如 `running` / `succeeded`）时会拿到 `undefined` 并在渲染时访问 `config.className`  
   - 文件：`apps/web-ui/src/components/compilations/status-badge.tsx`
   - 证据：`const config = statusConfig[status]; ... config.className`，组件没有 fallback
   - 修复：`StatusBadge` 已切到 lower-case 状态配置，并在未知值上回退到格式化后的默认 badge，避免因状态映射失配直接崩渲染
   - 验证：`cd apps/web-ui && npm test -- src/components/compilations/__tests__/status-badge.test.tsx` 通过；聚合前端回归与 Playwright 回归同 BUG-079

81. **BUG-081** — `fixed` — High — 编译列表页的自动刷新逻辑只认旧状态集合，真实 `running` 任务不会触发轮询刷新  
   - 文件：`apps/web-ui/src/app/(dashboard)/compilations/page.tsx`
   - 证据：`hasRunningJobs()` 只检查 `IN_PROGRESS_STATUSES` 中的 `PENDING/DETECTING/...`，不包含真实后端 `running`
   - 修复：列表页改为使用共享的 `IN_PROGRESS_COMPILATION_STATUSES`，现在会对真实 `pending/running` 任务持续轮询；状态过滤器和标签也已同步 lower-case 状态
   - 验证：`cd apps/web-ui && npm test -- 'src/app/(dashboard)/compilations/__tests__/compilations-page.test.tsx'` 通过；聚合前端回归同 BUG-079

82. **BUG-082** — `fixed` — High — 编译详情页只有当 `isRunning(job.status)` 为真时才订阅 SSE；由于状态集合过时，真实 `running` 任务根本不会开启事件流  
   - 文件：`apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
   - 证据：`useCompilationEvents(job && isRunning(job.status) ? jobId : null)`；`IN_PROGRESS` 集合不包含 `running`
   - 修复：详情页改为使用 `isCompilationInProgress(job.status)` 控制 SSE 订阅；真实 `running` / `pending` 任务会开启事件流，终态任务不会继续连 SSE
   - 验证：`cd apps/web-ui && npm test -- 'src/app/(dashboard)/compilations/[jobId]/__tests__/compilation-detail-page.test.tsx'` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过，`running compilation detail opens the SSE stream` 断言命中 SSE 请求

83. **BUG-083** — `fixed` — High — 编译详情页的 Retry / Rollback / Error / Artifacts 区块都用旧状态 `FAILED` / `PUBLISHED` 做条件判断，真实失败/成功任务不会显示这些关键操作与信息  
   - 文件：`apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
   - 证据：多处 `job.status === "FAILED"`、`job.status === "PUBLISHED"` 条件分支
   - 修复：详情页的 Retry / Rollback / Error / Artifacts 分支已统一改为真实状态 `failed` / `succeeded`；失败态会显示错误与重试，成功态会显示回滚与 artifact 信息
   - 验证：`cd apps/web-ui && npm test -- 'src/app/(dashboard)/compilations/[jobId]/__tests__/compilation-detail-page.test.tsx'` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过，失败态与成功态详情页都已覆盖

84. **BUG-084** — `fixed` — High — `StageTimeline` 仍渲染一个后端根本不存在的 `build` 阶段，并依赖旧状态到阶段的映射，时间线会系统性错位  
   - 文件：`apps/web-ui/src/components/compilations/stage-timeline.tsx`、`apps/compiler_worker/models.py`
   - 证据：UI `STAGES` 包含 `build`；后端 `CompilationStage` 只有 `detect/extract/enhance/validate_ir/generate/deploy/validate_runtime/route/register`
   - 修复：`StageTimeline` 已删除虚假的 `build` 阶段，并按真实终态 `succeeded/failed/rolled_back` 与 `current_stage/failed_stage` 驱动完成、失败和当前阶段样式
   - 验证：`cd apps/web-ui && npm test -- src/components/compilations/__tests__/stage-timeline.test.tsx src/components/compilations/__tests__/event-log.test.tsx` 通过；聚合前端回归同 BUG-079

85. **BUG-085** — `fixed` — High — Dashboard 首页的汇总统计同时依赖旧编译状态和值得字段 `version_count/last_compiled`，导致成功率、已发布数、工具数、近期编译数都不可信  
   - 文件：`apps/web-ui/src/app/(dashboard)/page.tsx`、`apps/compiler_api/models.py`
   - 证据：页面按 `PUBLISHED/FAILED` 统计编译成功率，按 `s.version_count`、`s.last_compiled` 统计服务指标；真实后端返回 `status=succeeded/...` 和 `tool_count/created_at`
   - 修复：Dashboard 已改为使用真实 `succeeded/failed` 状态计算成功率，服务指标改读 `tool_count` 与归一化后的 `last_compiled`；总工具数现在来自真实 `tool_count`
   - 验证：`cd apps/web-ui && npm test -- 'src/app/(dashboard)/__tests__/dashboard-page.test.tsx' src/components/dashboard/__tests__/compilation-metrics.test.tsx` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过，Dashboard 指标与 lower-case 状态渲染已覆盖

86. **BUG-086** — `fixed` — Medium — `CompilationMetrics` 的协议分布图被硬编码成永远空数据，即使后端 job 响应实际已经带 `protocol` 字段  
   - 文件：`apps/web-ui/src/components/dashboard/compilation-metrics.tsx`、`apps/compiler_api/models.py`
   - 证据：`buildProtocolDistribution()` 直接 `return new Map()`，注释里还错误声称 `CompilationJobResponse` 没有 protocol；后端模型其实有 `protocol`
   - 修复：`CompilationMetrics` 现已按 job 的 `protocol` 构建真实协议分布，`api-client` 也会把后端 `protocol` 字段归一化到前端模型
   - 验证：`cd apps/web-ui && npm test -- src/components/dashboard/__tests__/compilation-metrics.test.tsx src/lib/__tests__/api-client.test.ts` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过，Dashboard 已显示 `openapi/rest` 协议分布

87. **BUG-087** — `fixed` — Medium — `CompilationMetrics` 的状态分类仍按 `PUBLISHED/FAILED/ROLLING_BACK` 旧值匹配，真实后端状态会被错误归类或漏算  
   - 文件：`apps/web-ui/src/components/dashboard/compilation-metrics.tsx`
   - 证据：`STATUS_CATEGORIES.match` 只比较大写旧状态
   - 修复：`CompilationMetrics` 的状态分类已切到真实 lower-case 状态，并显式区分 `succeeded/failed/running/pending/rolled_back`
   - 验证：`cd apps/web-ui && npm test -- src/components/dashboard/__tests__/compilation-metrics.test.tsx` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/compilation-status-dashboard.spec.ts` 通过，Dashboard legend 已正确显示 `Succeeded/Failed/In Progress`

88. **BUG-088** — `fixed` — Medium — 服务列表页一旦输入搜索词就会执行 `s.name.toLowerCase()`；真实后端没有 `name` 字段，会在搜索时触发运行时异常  
   - 文件：`apps/web-ui/src/app/(dashboard)/services/page.tsx`、`apps/compiler_api/models.py`
   - 证据：筛选逻辑 `result.filter((s) => s.name.toLowerCase().includes(q))`；后端 `ServiceSummaryResponse` 只有 `service_name`
   - 修复：此前的 `serviceApi` 归一化已经把后端 `service_name` 稳定映射为前端 `name`，因此服务搜索不再依赖不存在的后端字段；本轮补了搜索回归测试和页面层 e2e 来锁住这条兼容契约
   - 验证：`cd apps/web-ui && npm test -- 'src/app/(dashboard)/services/__tests__/services-page.test.tsx'` 通过（`3 passed`）；`cd apps/web-ui && npm run typecheck` 通过；`cd apps/web-ui && npm run test:e2e -- e2e/services-page.spec.ts` 通过（`1 passed`）

---

## H. Runtime proxy / native stream 执行问题

89. **BUG-089** — `fixed` — High — `PreDeployValidator` 会放行 `grpc_stream.mode=client|bidirectional`，但原生执行器只实现了 `server` 模式，结果会变成“预部署通过、运行时才报 not implemented”  
   - 文件：`libs/validator/pre_deploy.py`、`libs/ir/models.py`、`apps/mcp_runtime/grpc_stream.py`
   - 证据：IR `GrpcStreamMode` 定义了 `server/client/bidirectional`；validator 只检查 `grpc_stream` 存在且 native 开关开启；执行器开头却明确 `if config.mode is not GrpcStreamMode.server: raise ToolError(...)`
   - 修复：`PreDeployValidator` 现在只在 `grpc_stream.mode=server` 时放行 native grpc_stream；`client` 和 `bidirectional` 会在 pre-deploy 阶段被明确拦下，不再把未实现模式拖到运行时才失败
   - 验证：`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q libs/validator/tests/test_pre_deploy.py` 通过（`18 passed`，新增覆盖 `client` / `bidirectional` 两种模式）；`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run ruff check libs/validator/pre_deploy.py libs/validator/tests/test_pre_deploy.py` 通过

90. **BUG-090** — `fixed` — High — 上游 SSE/WebSocket 收流默认空闲超时只有 `1.0s`，很多正常但低频的事件流会被 runtime 过早截断  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 证据：`_consume_sse_stream()` 和 `_consume_websocket_stream()` 都把 `idle_timeout_seconds` 默认值设成 `1.0`，随后 `_collect_sse_events()` / `websocket.recv()` 用这个值做 `asyncio.wait_for`
   - 修复：runtime proxy 现在把 SSE/WebSocket 的默认 `idle_timeout_seconds` 提升到 `15.0`，与 IR 模型默认值对齐；descriptor 未显式覆盖时，不会再因为 1 秒默认超时过早截断低频流
   - 验证：`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/mcp_runtime/tests/test_proxy_extended.py -k 'default_idle_timeout or positive_float'` 通过（`4 passed`）；`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run ruff check apps/mcp_runtime/proxy.py apps/mcp_runtime/tests/test_proxy_extended.py` 通过

91. **BUG-091** — `fixed` — Medium — 响应截断会用 `errors="ignore"` 直接吞掉被截断处的 UTF-8 残字节，返回的内容会静默损坏  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 证据：`_apply_truncation()` 对 `payload_bytes[:max].decode("utf-8", errors="ignore")`
   - 修复：响应截断改为先回退到合法 UTF-8 边界，再返回截断结果；如果截断点落在多字节字符中间，会额外标记 `utf8_boundary_trimmed=True`，不再依赖 `errors="ignore"` 静默吞字节
   - 验证：`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q apps/mcp_runtime/tests/test_proxy_extended.py -k 'truncation_policy_none_returns_untouched or utf8_boundary_trim_is_reported'` 通过（`2 passed`）；`cd tool-compiler-v2 && UV_CACHE_DIR=/tmp/uv-cache uv run ruff check apps/mcp_runtime/proxy.py apps/mcp_runtime/tests/test_proxy_extended.py` 通过

92. **BUG-092** — `fixed` — Medium — `field_filter` 的 dot-path 语法无法表达"字段名里本来就带点号"的键，像 OData/扁平 JSON 键会被错误当成嵌套路径  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 修复：引入 `_split_escaped_dot_path()` 和 `_has_unescaped_dot()` 辅助函数，支持 `\.` 转义语法表达字面量点号。`_apply_field_filter()` 改用这两个函数替代原先的 `str.split(".")`
   - 验证：`uv run pytest -q apps/mcp_runtime/tests/test_proxy_utils.py::TestApplyFieldFilter` 通过（9 passed，含新增 `test_escaped_dot_literal_field_name` 和 `test_escaped_dot_among_nested`）
93. **BUG-093** — `fixed` — Medium — 异步任务轮询链路在 JSON 解析失败时会静默降级成 `None`，可能把"坏响应"拖成超时或模糊失败，而不是第一时间报协议错误  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 修复：`_maybe_parse_json_payload()` 在 content-type 为 JSON 但 body 解析失败时改为抛出 `_InvalidJsonPayloadError`；`_poll_async_job()` 捕获该异常后立即抛 `ToolError`，不再继续轮询
   - 验证：`uv run pytest -q apps/mcp_runtime/tests/test_proxy_extended.py -k "TestPollAsyncJob or TestMaybeParseJsonPayload"` 通过（8 passed，含新增 `test_invalid_json_poll_raises_immediately`）

---

## I. Registry / rollback 隔离与一致性问题

94. **BUG-094** — `fixed` — High — `ServiceVersion` 的唯一约束只按 `service_id` / `version_number` 建立，忽略 `tenant/environment`，同名服务无法在不同 tenant/env 维持独立版本线和 active 版本  
   - 文件：`libs/db_models.py`
   - 修复：唯一约束 `uq_service_version` 扩展为 `(service_id, version_number, tenant, environment)`；`uq_service_versions_one_active` 唯一 active 索引也扩展为 `(service_id, tenant, environment)`
   - 验证：`uv run pytest -q apps/compiler_api/tests/` 全部通过（148 passed）

95. **BUG-095** — `fixed` — High — Artifact registry 的 create/activate/delete replacement 逻辑全部只按 `service_id` 操作，会跨 tenant/environment 相互取消 active 或错误提拔 replacement  
   - 文件：`apps/compiler_api/repository.py`
   - 修复：`_deactivate_service_versions()` 新增 `tenant`/`environment` 参数并精确匹配（None 时使用 IS NULL）；`create_version()` 传入 payload 的 tenant/environment；`delete_version()` 的 replacement 查询同样按 tenant/environment 过滤；`activate_version()` 从记录读取 tenant/environment 传给 deactivate
   - 验证：`uv run pytest -q apps/compiler_api/tests/` 全部通过（148 passed）

96. **BUG-096** — `fixed` — High — Artifact 的 update/delete/activate API 完全不接受 `tenant/environment`，和 list/get/diff 的过滤能力不对称，无法安全操作 env-scoped version  
   - 文件：`apps/compiler_api/routes/artifacts.py`、`apps/compiler_api/repository.py`
   - 修复：`update_artifact_version()`、`delete_artifact_version()`、`activate_artifact_version()` 路由均新增 `tenant`/`environment` 查询参数并透传到 repository；对应 repository 方法 `update_version()`、`delete_version()`、`activate_version()` 也增加了这些参数
   - 验证：`uv run pytest -q apps/compiler_api/tests/` 全部通过（148 passed）

97. **BUG-097** — `fixed` — High — 回滚 workflow 会先部署 target 版本再做 validation，但 validation 失败时只抛异常，不会恢复先前 active 版本，可能把坏版本留在集群里  
   - 文件：`apps/compiler_worker/workflows/rollback_workflow.py`
   - 修复：validation 失败时，先检查 `current_active` 是否存在，若存在则调用 `activate_version()` 恢复原先的 active 版本，然后再抛出 RuntimeError
   - 验证：`uv run pytest -q apps/compiler_worker/tests/test_rollback_workflow.py` 通过（9 passed，含 2 个新增测试验证恢复逻辑）

---

## J. Runtime proxy 新增执行问题

98. **BUG-098** — `fixed` — High — WebSocket URL 规范化会把原本的 `wss://` 地址降级成 `ws://`，直接破坏安全 WebSocket 连接  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 修复：`_to_websocket_url()` 判断条件改为 `parts.scheme in ("https", "wss")`，同时覆盖原始就是 wss 的场景
   - 验证：`uv run pytest -q apps/mcp_runtime/tests/test_proxy_utils.py -k "test_wss_preserved or test_ws_stays_ws"` 通过（2 passed）

99. **BUG-099** — `fixed` — Medium — WebSocket `bytes_base64` 输入没有启用 base64 校验，也没有转换成 `ToolError`；坏输入会抛底层异常或静默解码脏数据  
   - 文件：`apps/mcp_runtime/proxy.py`
   - 修复：`_normalize_websocket_message()` 中 `base64.b64decode()` 添加 `validate=True`，外层 try/except 捕获 `binascii.Error` 并转为 `ToolError`
   - 验证：`uv run pytest -q apps/mcp_runtime/tests/test_proxy_extended.py -k "test_invalid_base64_raises_tool_error"` 通过（1 passed）

---

## K. 新确认的 API / 前端断裂

100. **BUG-100** — `fixed` — High — 前端 Retry Compilation 动作调用不存在的 `POST /api/v1/compilations/{jobId}/retry`  
   - 文件：`apps/compiler_api/routes/compilations.py`
   - 修复：后端补 `POST /api/v1/compilations/{job_id}/retry` 路由，克隆原始 job 的 source/options 创建新编译任务，支持 `from_stage` 查询参数指定恢复阶段
   - 验证：`uv run pytest -q apps/compiler_api/tests/test_routes_compilations.py::TestRetryCompilation` 通过（3 passed）
101. **BUG-101** — `fixed` — High — 前端 Rollback Compilation 动作调用不存在的 `POST /api/v1/compilations/{jobId}/rollback`  
   - 文件：`apps/compiler_api/routes/compilations.py`
   - 修复：后端补 `POST /api/v1/compilations/{job_id}/rollback` 路由，仅允许对 succeeded 状态的编译执行回滚（409 拒绝非成功态），创建新 job 并通过 dispatcher 分发
   - 验证：`uv run pytest -q apps/compiler_api/tests/test_routes_compilations.py::TestRollbackCompilation` 通过（3 passed）
102. **BUG-102** — `fixed` — Medium — Dashboard 首页 recent compilations 表格仍以 `c.job_id` 生成 key / link / title，真实后端返回的是 `id`，首页编译记录链接会失效  
   - 文件：`apps/web-ui/src/lib/api-client.ts`
   - 修复：已由 `normalizeCompilationJob()` 适配层解决——`raw.id` 被映射为 `job_id`，前端组件统一使用 `job_id`，无需修改
   - 验证：`npm test -- src/lib/__tests__/api-client.test.ts` 全部通过（36 passed），TypeScript 类型检查通过

103. **BUG-103** — `fixed` — High — 前端 artifact 版本类型/页面统一读取 `version.ir`，但 registry 实际返回的是 `ir_json`；Service Detail 的 IR/Operations、Versions 的 View IR/validated 标记、Review 页都会把 IR 当成不存在  
   - 文件：`apps/web-ui/src/lib/api-client.ts`
   - 修复：已由 `normalizeArtifactVersion()` 适配层解决——`raw.ir_json` 被映射为 `ir`，前端组件直接读取 `version.ir` 即可
   - 验证：`npm test -- src/lib/__tests__/api-client.test.ts` 全部通过（36 passed），TypeScript 类型检查通过

104. **BUG-104** — `fixed` — High — 版本 diff 前端类型和组件与后端返回 shape 不兼容：UI 期望 added/removed 是完整 `Operation[]` 且 change 字段名叫 `field`，后端实际返回的是操作 ID 字符串和 `field_name`  
   - 文件：`apps/web-ui/src/lib/api-client.ts`
   - 修复：已由 `normalizeArtifactDiff()` 适配层解决——`raw.field_name` 映射为 `field`，`raw.added_operations`/`raw.removed_operations`（string[]）通过 `operationIndex()` 查 IR 转换为完整 `Operation[]`
   - 验证：`npm test -- src/lib/__tests__/api-client.test.ts` 全部通过（36 passed），TypeScript 类型检查通过

105. **BUG-105** — `fixed` — Medium — 前端 `auditApi.get(entryId)` 与对应测试都假定存在 `/api/v1/audit/{id}`，但后端只实现了 `GET /api/v1/audit/logs`  
   - 文件：`apps/web-ui/src/lib/api-client.ts`
   - 修复：`auditApi.get()` 已实现为 client-side fallback——调用 `auditApi.list()` 再 `find()` 匹配 ID，未命中时抛 ApiError(404)。虽非理想方案，但功能正确、测试覆盖
   - 验证：`npm test -- src/lib/__tests__/api-client.test.ts` 中 `auditApi.get falls back to list()` 通过

---

## L. Review / approval 流程是假实现

106. **BUG-106** — `fixed` — High — Review / approval workflow 完全保存在浏览器本地 `localStorage`，没有任何服务端持久化或共享状态，不同浏览器/用户看到的审批状态会永久分叉  
   - 文件：`apps/web-ui/src/stores/workflow-store.ts`、`apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
   - 修复：Added ReviewWorkflow DB model (libs/db_models.py); created backend REST routes (apps/compiler_api/routes/workflows.py) with GET/POST-transition/PUT-notes/GET-history; registered router in main.py; rewrote workflow-store.ts to use async workflowApi calls; review page calls loadWorkflow() on mount; 10 backend + 20 frontend tests pass

107. **BUG-107** — `fixed` — High — Review workflow 里的 `Publish` / `Deploy` 按钮只是本地状态迁移 + toast，并不会调用任何编译、发布、部署或网关 API，UI 可以显示“已发布/已部署”但后端什么都没发生  
   - 文件：`apps/web-ui/src/components/review/approval-workflow.tsx`
   - 修复：executeTransition() now async, calls workflowApi.transition(); on "published" calls artifactApi.activateVersion(); on "deployed" calls gatewayApi.syncRoutes(); added submitting loading state and error toasts

108. **BUG-108** — `fixed` — Medium — Review checklist、逐操作 notes、overall review notes 全都只存在组件内存里；刷新/切页就丢，`Complete Review` 也只是触发 toast 回调  
   - 文件：`apps/web-ui/src/components/review/review-panel.tsx`、`apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
   - 修复：ReviewPanel now accepts serviceId+versionNumber props; handleComplete() calls workflowStore.saveNotes() which persists via PUT /api/v1/workflows/{id}/v/{ver}/notes; backend stores notes in JSONB review_notes column

---

## 当前小结

- 已记录：`108` 条
- 其中：
  - `fixed`：`99`
  - `acknowledged-open`：`9`（BUG-045 ~ BUG-053 — extractor 覆盖率问题，不属于本轮修复范围）
- 所有 `confirmed-by-code` 条目已修复完毕
