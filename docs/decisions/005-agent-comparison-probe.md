# ADR 005：真实 Agent 与固定流程的开发对照

状态：两个固定流程已完成；Agent 外发调用等待授权边界确认，尚未发送模型请求。

选择 fixture-01（金额回归）与 fixture-04（动态调用）两个自建开发 case，使用 case_locator 固定当前
可用 Git SHA。对照的两条路径分别为 `agent=false` 与 `agent=true`，都设置 `allow_tests=true`。
同 case 两条路径使用相同源码、测试 profile、问题和最高预算；各自独立 Store，避免固定流程先采集到的
coverage 被 Agent 复用而产生信息泄漏。它们不是正式金标或随机化实验。

任务总预算 180 秒；当前 Controller 另有 60 秒边界、最多 6 次决策、12 次工具调用和 12000 tokens。
每个任务最多触发一次 base/head 验证。固定流程不调用模型，直接按确定性策略执行验证。
Agent 可选择补查、运行已授权测试或停止；不强制它走有利于结果的工具序列。

`benchmarks/runners/agent_validation.py` 保存独立 checkpoint 与完整本地执行记录。
`--fixed-only` 不实例化 Provider，不读取模型配置，不发送任何模型请求。通过 `--resume run-<id>`
继续时，已完成、失败或中断的任务不会被再次提交；不会自动增加预算。

公开结果只保存模型公开名称、预算、用量、实际工具轨迹、测试结果和限制，不保存模型配置、URL、
凭据或请求头。模型配置由 Provider 通过外部环境变量读取，原文件不变。拟发送数据限于自建 fixture
源码和对应图、测试元数据，具体范围见 `docs/examples/agent-data-preview.md` 及对应 JSON。

本次完整 Agent 启动被自动审批审查拒绝：认为使用服务的授权尚未覆盖具体数据向具体目的地外发。
因此只完成了无外发的固定流程部分，不能将其作为真实 Agent 验收或 B4 与 B3 收益比较。
恢复前需要确认这两个自建 fixture 的上下文发送范围；原服务配置不会复制进项目。

结果文件：`benchmarks/results/agent-validation.json`。

固定流程实测：fixture-01与fixture-04均完成4条测试的base/head对照，分别耗时约11.34和5.94秒。
前者观察到3条suspected_regression；后者4条passed_both，仍保留动态调用证据不足，不宣称无风险。

续跑前执行只读范围校验：Provider目的地与模型必须与预览一致；两个case的base/head及完整三文件源码hash必须匹配。
预览JSON摘要封存在checkpoint的review-scope.json，问题、预算或工具schema改变也会停止。
Provider构造时再次校验目的地/模型，防止等待期间配置漂移。范围校验本身不发送模型请求，也不表示已获得授权。
