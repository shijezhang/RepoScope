# RepoScope 升级交付状态

更新：2026-09-10，0.2.0开发版。升级主线已实现并形成真实验证链路；正式效果评测仍未完成。实现与验收分别列明，不把运行成功当作业务质量提升。

| 阶段 | 当前状态 | 已实现/验证 | 剩余门槛 |
|---|---|---|---|
| M0 | 主要可行性已验证 | Python3.12、两仓库固定SHA、650/1413测试收集、12条变异、Docker、两本地模型revision与资源实测、锁文件/ADR | 首次历史下载及首轮镜像构建成本未完整采集 |
| M1 | 主要实现已验证 | 固定Git快照、有限AST图、SQLite、标识符/BM25/RRF、Dense/CrossEncoder真实探针、版本绑定向量cache、CLI | 长函数分块质量，解析/所有检索组件联合发布，正式B1质量基线 |
| M2 | 核心实现已验证 | 双侧diff、删除/改名、类头/模块变更、作用域与重复定义反例、受限图遍历、连续路径与源码证据 | 30条复核开发集、正式图贡献比较 |
| M3 | 真实链路已验证 | fixture及Click/HTTPX固定池Docker、nodeid/context、覆盖失效、选择/回退、两侧结果与复跑、实际取消/超时回收 | 全仓库稳定池扩展，T-cov完整对照，正式检出率与成本实验 |
| M4 | 真实探针已执行，验证未达标 | 结构化工具、快照校验、预算/去重、持久轨迹、降级；默认只读，显式授权后一次验证及反馈；固定流程同执行器 | 03:15两个开发fixture均完成模型测试反馈收尾，但比fixed慢；正式对照与更多场景仍待完成 |
| M5 | 解析增量已验证 | AST复用+引用全重解；12/12 parser v3同head hash一致；向量文件与manifest原子发布及缓存复读 | 最小依赖失效、向量增量、联合发布、稳定加速；当前仅解析增量 |
| M6 | 工作台主流程已验证 | 创建/历史、SSE、图/源码、任务与完整度分开、逐测试Base/Head、显式重试/取消、导出；真实Docker UI联调 | 真实模型交互体验、更多中断组合场景 |
| M7 | 开发证据与文档已交付，正式评测待完成 | 两仓库真实回归、机器结果、选择无收益观察、强检索局限、Compose analysis-only实测、报告/面试/演示/清理；指标脚本拒绝未复核金标 | 正式标注/holdout、B0–B4与消融、置信区间 |

## 验证入口

- 最终命令与数量：[validation.json](validation.json)。后端测试覆盖版本/作用域/删除/路径篡改、API幂等/SSE/取消、Agent预算/授权/反馈、runner回收、重放保留历史、向量cache与指标边界。
- 前端：TypeScript/Vite与4项Playwright契约通过；契约mock不等于后端精度证明。
- 真实UI：环境不足报告显式attempt2重试，Docker完成后revision3；Base4通过、Head1通过3失败，展示疑似回归，最终JSON一致，浏览器0错误。[记录](examples/workbench-verification.json) / [截图](examples/workbench.png) / [对照](examples/workbench-results.png)。
- 两仓库准备：[preparation.json](../benchmarks/results/preparation.json)，650/1413是宿主完整collection数。
- 应用Compose：[compose-validation.json](../benchmarks/results/compose-validation.json)，独立端口/state、首页200、异步分析、22个源码hash一致；analysis-only模式。
- 自建样例Docker：[docker-validation.json](../benchmarks/results/docker-validation.json)，包含实际运行中取消、timeout、隔离参数和清理。
- 两真实仓库Docker：[public-docker-validation.json](../benchmarks/results/public-docker-validation.json)，Click39/HTTPX106是各登记固定池，非全仓库套件。
- 选择工程对照：[public-selection-engineering.json](../benchmarks/results/public-selection-engineering.json)，39/39、106/106，缩减率0%；未将head失败清单送入选择器。
- 03:15 Agent功能：[agent-night-0315.json](../benchmarks/results/agent-night-0315.json)，两例验证及反馈后结束均完成；更慢，不宣称收益。见ADR007。
- 夜间Agent进展：[agent-night-0230.json](../benchmarks/results/agent-night-0230.json)，原预算内一例已触发测试；另一例明确length/空JSON。见ADR006，不覆盖原失败。
- 真实Agent：[agent-validation-assessment.json](../benchmarks/results/agent-validation-assessment.json)，4次请求、8658输入/1497输出tokens，两case均未验证测试；固定流程完成了同池对照。
- 强检索：[strong-retrieval-probe.json](../benchmarks/results/strong-retrieval-probe.json)，4个英文开发query；2次磁盘复读排名相同。质量未标注，HTTPX有不理想结果。
- 全量/解析增量：[index-consistency.json](../benchmarks/results/index-consistency.json)，12条v3全部ready/equal；每次重放保留独立run目录及Git对象。
- 历史对象恢复：[recovered-snapshot-objects.json](../benchmarks/results/recovered-snapshot-objects.json)，源码、commit与tree精确核对后恢复，未修改历史报告。

## 尚未证明的效果

没有正式的图召回提升、测试节省、Agent收益或2倍增量结论。12条仍是unreviewed开发变异，没有独立人工金标和holdout。强检索真实运行不代表质量达标。首次准备成本有明确缺口，未用在线或pytest阶段耗时掩盖它。

## 继续验收

1. 依据已保留的真实失败改进上下文预算打包与结构化输出诊断，继续验证Agent反馈；不得将当前两次未完成结果计为成功。
2. 独立复核12条、扩展开发集、按变更来源冻结测试集；`benchmarks/runners/metrics.py`会拒绝未复核或跨split同源样本。
3. 运行强检索、图/覆盖/Agent消融与成本实验，保留负面结果；依据证据决定启用条件与增量投入。
4. 完成长函数分块、全部索引联合发布与Compose已实际验收analysis-only模式，按实际收益更新简历素材。
