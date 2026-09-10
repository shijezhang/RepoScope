# RepoScope overnight 晨报 · 2026-09-10

实际同步时间：北京时间10:41。原定08:30；当前可见最后开发检查点为05:00，此后本任务直到10:41才收到下一次唤醒。没有05:30–08:30继续执行的记录，不能推断具体中断原因，也不能声称整夜持续运行。

## 已完成

- Agent上下文按完整证据打包、结构化短决策契约、预算耗尽后的工具移除与非敏感错误诊断已落地。03:15独立实验中，fixture-01/fixture-04均在原预算内调用真实Docker测试，消费Base/Head反馈后正常结束。原始失败实验保留；仅使用已批准的两条fixture外发范围。
- 两Agent耗时11.35/10.52秒，同轮固定流程5.60/4.60秒。功能闭环改善已验证，但Agent更慢，尚无正式质量或成本收益证据。
- 长函数完整行分块已接实际tokenizer，保留完整声明/签名，超限源码明确partial。父符号聚合、chunk证据、模型18个文件清单核验及内容变化缓存失效已完成。
- 强检索内部将固定快照、分块、稀疏词项和向量一起原子发布，损坏或失败不静默替换。最终源码实测同进程磁盘复读Click2.22秒、HTTPX1.90秒，此前18.11/16.03秒；排名相同、分数差0。两语料仍partial，冷构建没有改善。
- CLI新增显式本地模型清单入口。真实Click查询排名与Python接口一致；完整进程耗时59.59秒，不把约2秒复读当作CLI冷启动时延。
- 12条固定SHA源码复核包已导出、diff hash已校验；标签留空，全部unreviewed/development，正式评分继续拒绝这些未复核样本。

## 验证与提交

后端从启动基线66项增至115项，全量通过；ruff check/format和diff-check通过。最终源码提交2e9a946已推送zhangshijie/reposcope-upgrade，未合并main，工作区在晨报生成前干净。该源码提交的CI已确认成功：
https://github.com/shijezhang/RepoScope/actions/runs/34430354339

主要证据：

- benchmarks/results/agent-night-0315.json
- benchmarks/results/strong-retrieval-bundle-recheck-0430.json
- benchmarks/results/strong-cli-0500.json
- benchmarks/review/README.md
- docs/validation.json
- docs/overnight-2026-09-10.md

## 尚未完成

产品全局索引默认发布指针、向量增量及稳定增量加速仍未完成；当前原子发布仅覆盖强检索内部。正式人工标注、独立holdout、B0–B4/消融与统计收益未完成；原测试选择对照缩减率仍0%。

夜间源码变化后的wheel、Compose重建，以及API/worker重启和真实UI最终版本核验未执行。此前部署验证只对应旧源码，validation.json中的source_matches_current/wheel_matches_source保持false，不能当作新版本部署验收。

本夜调度停止。下一步应先补最终打包与本机部署验收，再推进产品发布边界和正式评测。
