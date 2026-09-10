# ADR 007：显式短决策配置与真实工具职责

2026-09-10夜间开发验证，模型名称和所有既有预算保持不变。

02:30的真实诊断证明有一次finish_reason=length且JSON为空。官方DeepSeek文档说明V4默认开启思考，支持显式thinking.type=disabled：[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。我们没有保存推理正文，也不能据此反推旧失败的细因。

新增可选REPOSCOPE_LLM_THINKING，仅接受enabled/disabled且只发送给明确支持的api.deepseek.com/deepseek-v4-*；未设置时保留原请求，不影响其他兼容服务。独立实验显式关闭思考以使用短JSON决策，仍为deepseek-v4-pro、每次600生成token、累计12000、6轮、一次base/head验证。请求选项进入诊断与报告，未修改用户配置文件。

03:00探针没有成功：重复候选查询导致一例停止，另一例缺少必填summary。没有放宽校验。随后修复通用契约：

- system明确给出完整JSON格式，保留summary必填和500字符限制。
- get_test_candidates只读取现有计划，不收集测试；run_tests负责收集及验证，空nodeids加collection_required不是没有测试。
- 候选工具向模型返回有总数/截断标记的ID样例，完整执行计划不被修改。
- run_tests使用一次后，从后续可用schema移除；后端仍拒绝重复执行。
- 单独记录completion_reason和模型是否在真实测试反馈后产生有效决策，不把worker.completed当业务成功。

03:15独立探针两个fixture均完成验证并在反馈后model_finished：Agent耗时11.35/10.52秒，固定流程5.60/4.60秒；分别3/4次模型请求，6353/7679总tokens。预算未提高，旧结果保留。功能链路已跑通，但更慢且测试池相同，不能宣称Agent收益。两例均未复核开发样例，不能推广为正式质量指标；本轮同时改变了模式与契约说明，不做单变量因果归因。

结果：benchmarks/results/agent-night-0300.json（失败）与agent-night-0315.json（功能通过）。范围在运行后再次核验。长函数分块基础另已实现，但尚未接入Search或重模型检索，不能提前称为强检索完整分块验收。
