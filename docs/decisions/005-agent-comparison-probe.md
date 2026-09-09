# ADR 005：真实 Agent 与固定流程的开发对照

状态：真实服务对照探针已运行完毕，保留负面结果；两个 Agent 任务均未完成测试验证。

选择 fixture-01（金额回归）与 fixture-04（动态调用）两个自建开发 case，使用 case_locator 固定可用
Git SHA。对照路径为 `agent=false` 与 `agent=true`，均设置 `allow_tests=true`。同 case 使用相同
源码、测试 profile、问题和最高预算；各自独立 Store，避免先执行的固定流程 coverage 泄漏给 Agent。
它们不是正式金标、随机化实验或任务质量评测。

任务总预算180秒；Controller另有60秒边界、最多6次决策、12次工具调用和12000 tokens。
每个任务最多触发一次base/head验证。固定流程不调用模型，直接执行确定性验证；Agent自行选择补查、
调用已授权测试或停止，不强制其走有利于结果的工具序列，不在失败后自动扩额或补请求。

| 开发 case | 固定流程 | 真实 Agent | 停止原因 |
|---|---|---|---|
| fixture-01 | 11.34秒；4条测试完成base/head对照，3条suspected_regression | 13.59秒；2次read_evidence，未执行测试 | 本地context预算守卫停止 |
| fixture-04 | 5.94秒；4条测试均passed_both，仍保留动态行为限制 | 23.45秒；1次read_evidence成功，未执行测试 | 第二次模型输出未通过Decision结构校验，ValidationError |

模型为 `deepseek-v4-pro`，共4次请求，8658 input tokens、1497 output tokens；其中1次模型输出
校验失败。两条Agent路径的test_plan仍为collection_required。任务记录的completed表示worker已结束，
不能解释为Agent完成验证。固定流程结果在恢复时直接读取，没有重复执行。

这两次观察未显示验证收益：固定流程完成了测试对照，Agent增加调用成本后仍缺少测试证据。
因此保留固定流程为默认，Agent保持可选调查能力，不能将该探针描述成完整的Agent测试反馈闭环验收。
样本仅两个开发fixture，不能推广为通用模型优劣结论，也不产生正式B4/B3质量指标。

诊断边界：fixture-01累计实际用量尚未达到12000，仍被下一轮context的保守UTF-8字节上界估算阻止。
fixture-04的适配器仅保存ValidationError类型，未保存finish_reason、原始输出或字段级错误，
目前不能断言为输出截断、具体字段超长或网络问题。后续可改进预算内上下文压缩和失败元数据，
但本次不修改失败结果、不增发模型请求。

最初的Agent命令曾被自动审批审查拒绝，之后用户对具体预览回复“同意，仅按预览范围发送”。
批准范围为预览中的两个fixture源码及分析/测试摘要，目的地与模型均明确；审批记录单独保存在
`benchmarks/results/agent-approval.json`。预览JSON保持原样，不用修改其status来记录审批。

续跑前执行只读范围校验：Provider目的地/模型、两个case的base/head、完整三文件源码hash、问题、预算
及工具schema必须匹配。预览JSON摘要封存在checkpoint的review-scope.json。Provider构造时再次复核
目的地/模型；本次运行后再次核验预览未变，成功read_evidence结果均来自批准的源码。
配置文件未复制或修改，密钥和请求头没有写入结果。审批记录仅包含已明确批准的公开目的地。

`benchmarks/runners/agent_validation.py`保存独立checkpoint与本地执行记录；`--fixed-only`不实例化
Provider、不读取模型配置、不发送请求；`--resume run-<id>`不会再次提交已完成、失败或中断的任务。

结果与证据：

- `benchmarks/results/agent-validation.json`：实际工具轨迹、用量、测试状态及限制。
- `benchmarks/results/agent-validation-assessment.json`：任务完成与失败分类，区别worker状态与业务结果。
- `benchmarks/results/agent-approval.json`：独立的明确授权与范围核验记录。
- `docs/examples/agent-data-preview.md`及对应JSON：保持不变的具体外发预览。
