# RepoScope

**代码变更影响分析与回归验证工作台。** 输入本地 Python Git 仓库的两个提交，查看两侧符号变化、潜在调用方、证据路径和测试验证状态。

当前交付为可运行开发版 `0.2.0`，不是升级计划所有阶段均已验收的正式版本。固定快照分析、CLI/API/工作台、解析增量与正确性测试已落地；容器执行真实验收、强检索模型实测、Agent 对照和正式标注评测仍待完成。逐项状态见 [交付清单](docs/delivery-status.md)。

![真实分析工作台](docs/examples/workbench.png)

## 启动

需要 Python 3.12、uv、Node.js 22+。本次实际环境为 Python 3.12.13、Node 26；Python 与前端依赖分别由 `uv.lock` / `package-lock.json` 固定。

```bash
uv sync --frozen --extra dev
npm --prefix apps/web ci
npm --prefix apps/web run build
# 终端一：默认只监听本机
uv run --frozen reposcope serve
# 终端二：单个持久化 worker
uv run --frozen reposcope worker
```

打开 http://127.0.0.1:8000。先登记仓库，再填写 base/head（例如 `HEAD~1` / `HEAD`）。只有 commit 对比受支持；分析不会使用未提交改动，不修改目标工作区。PR 模式显式比较 merge-base 到 head，报告显示实际 SHA。

默认允许登记当前工作目录下的仓库；其他目录通过 `REPOSCOPE_ALLOWED_ROOTS` 配置，多个路径使用操作系统路径分隔符。状态默认存储于 `artifacts/state`，可通过 `REPOSCOPE_HOME` 指定。环境变量需在启动前导出；`.env.example` 为配置说明，不自动加载。

## CLI

```bash
uv run reposcope register /absolute/path/to/repository
# 使用上一命令返回的 repo_id
uv run reposcope index REPO_ID --commit HEAD
uv run reposcope analyze REPO_ID BASE_SHA --head HEAD_SHA --output artifacts/report.json
uv run reposcope search SNAPSHOT_ID total
uv run reposcope callers SNAPSHOT_ID SYMBOL_ID
uv run reposcope verify RUN_ID
# 提交测试任务，需要 worker 和已准备的 Docker profile
uv run reposcope test RUN_ID
```

API 文档在 `/docs`。分析创建支持 `Idempotency-Key`；SSE 支持 `Last-Event-ID`；JSON、Markdown、HTML 来自同一报告 revision。测试只在显式请求后执行。

## 执行环境

目标代码仅由 Docker runner 执行。没有 Docker、镜像或登记 profile 时，显示 `test_environment_unavailable`，不会偷偷在宿主机运行。构建与登记方式见 [执行配置](docs/profiles/README.md)。测试容器使用固定命令、断网、资源上限和独立 Git 快照目录。

开发机本次没有 Docker，已验证执行器参数、取消与恢复契约，但未验证真实容器。`benchmarks/runners/*probe.py` 是可信公开仓库的 **M0 本地环境探针**，不经过产品 runner，不能视为隔离验收。API/worker 默认在宿主机运行，以便 worker 管理测试容器；应用容器方案见 [部署说明](docs/deployment.md)。

## 实测与重放

[基准说明](benchmarks/README.md) 提供两个固定公开仓库与 12 条开发变异的重放命令。机器结果保存在 `benchmarks/results/`。

| 检查 | 实测范围 |
|---|---|
| pytest 收集 | Click 8.1.8：650；HTTPX 0.28.1：1413 |
| 同池回归探针 | Click：base 38 passed / 1 skipped，变异 head 4 failed；HTTPX：base 106 passed，变异 head 1 failed |
| 全量/解析增量一致性 | 12 条开发变异；以同一 head 的规范化 hash 比较 |
| 标注状态 | unreviewed；不作为正式质量、召回率或泛化结论 |

这些是环境与工程正确性证据。没有将旧 GraphRAG 评测、未执行的模型基线或目标提升数字包装成新项目效果。增量复用未变文件 AST 产物，所有跨文件引用重新解析；称为“解析增量”，不声称端到端向量增量。

## 检索与 Agent

默认结构化 diff 分析不依赖模型。标识符/BM25/RRF 可直接运行；`Search.strong_query` 要求本地存在明确 revision 的 Embedding 与 Reranker，缺失时明确失败。它未被默认为已经完成 B1 实验。

可选 `agent=true` 使用兼容 OpenAI 的结构化决策，通过 `REPOSCOPE_LLM_BASE_URL`、`REPOSCOPE_LLM_MODEL`、`REPOSCOPE_LLM_API_KEY` 配置。当前 Agent 只补查证据，最多 6 轮、12 次工具、60 秒补查预算，不自行执行 shell 或修改代码；相同查询去重，出错保留确定性报告。模型 token 用量保留，但尚无真实 B4/B3 对照或收益结论。

## 开发与文档

```bash
uv run --frozen pytest
uv run --frozen ruff check src/reposcope tests/reposcope benchmarks
uv run --frozen ruff format --check src/reposcope tests/reposcope benchmarks
npm --prefix apps/web run build
# Playwright 使用本机 Google Chrome；契约测试不等于后端精度评测
npm --prefix apps/web run test:e2e
```

- [升级计划](docs/reposcope-upgrade-plan.md) / [交付状态](docs/delivery-status.md)
- [技术报告](docs/reposcope-technical-report.md) / [面试指南](docs/reposcope-interview-guide.md)
- [设计决策](docs/decisions/001-implementation-boundaries.md) / [基准范围](docs/decisions/002-benchmark-scope.md)
- [演示脚本](docs/demo.md) / [部署](docs/deployment.md)

源码在 `src/reposcope/`，前端在 `apps/web/`。旧文档抽取、社区摘要、Gradio、金融/论文数据、旧测试及安装残留已退出当前项目；历史保留于原 Git 提交。项目在 NetworkX、Python AST、FastAPI、pytest、coverage.py、React Flow 等开源组件之上实现快照、有限解析、影响证据与执行协调，不把这些基础算法称为原创。
