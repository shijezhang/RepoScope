# RepoScope 升级交付状态

日期：2026-09-09，版本0.2.0开发版。原计划方向保留，以下区分实现和运行验收，不把文件存在当作能力完成。

| 阶段 | 当前状态 | 已实现/验证 | 未完成门槛 |
|---|---|---|---|
| M0 范围与可行性 | 部分完成 | Python3.12.13、两仓库固定SHA、650/1413测试收集、12条可重放变异、ADR/锁文件 | 模型内存/延迟/费用实测、Docker真实环境 |
| M1 快照与索引 | 部分完成 | Git快照、有限AST图、SQLite、标识符/BM25/RRF、CLI、错误诊断 | 固定Dense/Reranker强基线及索引统一发布 |
| M2 影响分析 | 核心实现已验证，阶段部分完成 | 双侧diff、删除/改名、类头/模块变更、受限图遍历、证据路径与报告 | 30条人工复核开发集及正式B2比较 |
| M3 测试验证 | 实现已完成主要链路，验收部分完成 | 真实collection契约、版本绑定context、选择/回退、Docker runner、base/head结果分类；mock与Git导出测试通过 | 真实Docker E2E、覆盖收益、T-all/T-cov正式对照、稳定性复跑 |
| M4 Agent | 部分完成 | 结构化只读补查、模型adapter、参数/快照校验、预算/去重、持久轨迹、确定性降级 | 模型驱动测试反馈闭环、真实B4/B3公平对照；未证明Agent收益 |
| M5 增量一致性 | 解析增量已验证 | AST复用+全部引用重解；12/12同head规范化hash一致，删除/导入/作用域反例通过 | 最小依赖失效、向量增量、固定检索分数等价、资源统计及稳定加速 |
| M6 工作台 | 核心实现与联调已验证 | React工作台、创建/历史、SSE、图/源码、测试、取消、JSON/MD/HTML；构建与契约E2E | 真实Docker结果与全部中断恢复场景UI验收 |
| M7 评测与交付 | 部分完成 | 两仓库真实回归探针、机器结果、技术报告、面试指南、演示脚本、旧文件清理 | 正式标注/holdout、B0–B4及消融实验、质量/成本置信区间、干净Docker部署 |

## 本轮验证证据

- 后端：`uv run pytest`，38项通过（最终结果若增加测试，以 validation.json 为准）。覆盖固定快照、删除/重命名、循环、作用域、解析失败、路径/源码篡改、API幂等/SSE/取消、Agent去重、runner及失联回收。
- 前端：TypeScript/Vite构建通过；Playwright契约2项通过（空态/错误、创建/证据/测试/导出、640px布局）。契约数据有明确mock标识，不当作后端准确性证据。
- 真实浏览器：fixture仓库登记→创建→base证据→JSON导出→测试请求→环境不可用，保存 [截图](examples/workbench.png)。产品未生成虚假通过记录。
- 两仓库准备：[preparation.json](../benchmarks/results/preparation.json)。
- 真实回归探针：[regression-probes.json](../benchmarks/results/regression-probes.json)。它们在独立宿主环境运行，`product_isolation_verified=false`。
- 全量/解析增量：[index-consistency.json](../benchmarks/results/index-consistency.json)，解析器版本由结果记录。
- 可查看的分析示例：[Click](examples/click-01.json)、[HTTPX](examples/httpx-02.json)。示例未把宿主探针冒充产品执行证据。

## 明确未宣称的效果

没有正式的图召回提升、测试缩减/耗时节省、Agent收益或2倍增量加速结论。12条是unreviewed开发变异，没有独立人工金标与holdout。模型强基线缺失时不把BM25单独称为B1。部分语法与动态调用始终unknown/candidate，测试回退可能较多。

## 继续验收顺序

1. 配置可用Docker及每仓库的不可变镜像profile，完成正常/失败/取消/崩溃恢复真实执行；保留日志与coverage原始数据。
2. 固定Embedding/Reranker revision及资源边界，跑强B1；实现统一检索发布或继续明示仅解析增量。
3. 独立复核12条试验case、扩展开发集、按来源冻结测试集，运行图/覆盖/Agent消融与成本实验。
4. 依据结果决定Agent启用条件与增量投入，更新技术报告和简历素材；不降低标注门槛追目标数值。
