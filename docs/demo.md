# 3–5分钟演示脚本

先按README启动API与worker，按benchmarks/README准备仓库并执行replay.py。以下全部为真实代码分析，fixture和人工变异需明确说明。

1. **0:00–0:40 问题与版本。** 登记 `artifacts/benchmark-replay/click-01/repository` 的绝对路径；base `HEAD~1`、head `HEAD`。展示报告实际SHA，说明用户未提交改动不进入本次分析。
2. **0:40–1:40 影响证据。** 查看Click types边界变化、两侧符号、上游潜在调用方；点击base源码，展示原始判断。路径只表示可能影响，截断/未知仍保留。可以下载JSON并核对revision。
3. **1:40–2:40 回归证据。** 打开 `benchmarks/results/regression-probes.json`，展示同测试池base通过/head失败。明确这是可控变异与M0本地探针，产品Docker没有就绪时不声称已容器验证。
4. **2:40–3:40 未知与失败。** 登记fixture变异仓库，用实际动态调用case（见cases/development.json）展示未知项；点击运行测试，无profile时界面准确显示环境不可用。成功、失败、未知均是产品结果。
5. **3:40–5:00 工程与局限。** 展示12条解析增量hash结果、取消/恢复设计与交付状态。说明强基线、正式标注和Agent收益尚未验收。

静态示例：`python benchmarks/runners/examples.py` 基于真实变异仓库生成 `docs/examples/*.json|md|html`。这些报告是deterministic analysis示例，明确tests未执行。真实UI脚本为 `apps/web/scripts/live-check.mjs`，需要API、worker、本机Chrome与已重放fixture。
