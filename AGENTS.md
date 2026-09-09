# RepoScope project guidance

面向用户使用简体中文。当前目标和阶段验收以 docs/reposcope-upgrade-plan.md 与 docs/delivery-status.md 为准；实际实现契约见 docs/decisions/001-implementation-boundaries.md。

- 当前包为 src/reposcope；不要恢复旧 GraphRAG / Gradio 路径。
- 所有分析固定 commit SHA、snapshot 和 evidence hash。未知动态关系必须明确呈现，不补造确定边。
- 目标测试只用服务端配置的 Docker profile；M0 本地环境探针不是产品执行模式。
- partial 报告与 completed 任务是不同维度；未执行测试不能标 passed，单次 base pass/head fail 不能声称已排除 flaky。
- 解析规则修改需升级 parser version，并重跑全量/解析增量一致性。不得使用模型输出自标成正式金标。
- 验证：uv run --frozen pytest；ruff check/format；前端 npm ci/build 和相称的契约/真实UI测试。
- artifacts 默认不提交；运行报告和基准证据不能当临时文件删除。新产生且不再需要的缓存、测试输出和安装探针及时清理。
