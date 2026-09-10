# RepoScope 继续推进结果 · 2026-09-10

本轮补齐夜间未执行的部署验收，新增CLI事务发布和向量内容增量，并按用户要求逐条复核12个开发样例。仍不声称升级计划所有正式质量门槛已满足。

## 已完成与实证

- 后端全量125项通过，前端5项Playwright契约通过，TypeScript/Vite及ruff检查通过。
- 最终wheel在干净锁定依赖环境安装成功，30个Python源码hash一致。最终Compose的30个Python文件及3个前端产物与本机一致；独立API/worker真实分析成功，测试容器和Compose验收容器均清理。
- 本机API/worker已重启，health正常。真实UI提交run `dc8e352bea4143949e7598c7fa864fc1` 完成Docker对照：Base4通过，Head1通过/3失败，报告revision2。发现并修复了报告已完成但历史仍排队的UI问题；新run `b62bc625d38245838a0d4edcc30693af`验证历史自动同步，浏览器未见console error。
- 新增 `publish-index` / `search-published`，按repo/profile维护不可变发布记录和SQLite active指针。完整构建才切换；partial/失败保留旧版，CAS防止慢旧构建覆盖新发布，模型或bundle漂移拒绝查询。真实本地模型验证了完整base发布后，partial head不替换base。
- 新快照按完整分块文本及模型/配置身份复用向量。真实Click-02：3258块复用3251、编码7；与全量排名一致，向量最大差1.313e-7。单次增量49.05秒、全量163.55秒，约3.33倍；同机并行Docker复核、样本仅一条，不作为稳定加速承诺。

## 用户指定的Codex复核

12条固定SHA源码与Docker对照全部完成，原始diff/source hash可追溯。6条变异检出断言失败，2条删除/改名导致ImportError收集失败，4条当前测试池两侧通过。测试池为Click188、HTTPX106、fixture4，均不是全仓库稳定测试池。

关键结论：注释变更的AST忽略位置后相同；fixture-04是动态目标改变但当前函数组合返回值相同；fixture-05新增模块属性，不能等同于注释变更；httpx-02失败测试名虽含raise_for_status，其直接受影响断言是Response.is_error。

逐条结果见[Codex复核报告](../benchmarks/review/2026-09-10/codex-review.md)。复核人标为Codex，原正式标注仍unreviewed；未把机器复核伪称独立人工金标，也未将单次失败升级为排除flaky的结论。

## 证据

- [最终打包](../benchmarks/results/packaging-published.json)
- [最终Compose](../benchmarks/results/compose-validation-published-recheck.json)
- [真实工作台与Docker](../benchmarks/results/workbench-final.json)
- [真实模型发布边界](../benchmarks/results/publication-validation.json) / [CLI发布查询](../benchmarks/results/publication-cli.json)
- [向量增量对照](../benchmarks/results/incremental-retrieval-run-042fc964ccac4749907ff9e8bf5ed71d.json)
- [12条Docker复核](../benchmarks/results/review-execution-run-9337ead858594201929459a903712032.json)

中间一次Compose命令的Python镜像摘要被误截短，构建未开始即失败；失败日志保留，修正为此前机器记录的完整摘要后重新构建并通过。最终结果只引用recheck文件。

## 保留的边界

引用解析仍保守地全量重解，尚未实现最小依赖失效；真实pytest collection/test assets仍在执行profile边界，未与静态索引合并为一个全局事务。强检索真实公开仓库语料仍partial。CLI已有完整版本发布，Web未新增发布管理入口。

没有新增独立holdout、完整B0–B4/消融与置信区间；旧选择工程对照的缩减率0%保持。Agent先前两例已在原预算内完成测试反馈，但更慢；本轮没有额外DeepSeek调用，也没有扩大外发范围。

源码提交：`7dfc3fa`，已推送`zhangshijie/reposcope-upgrade`，未合并main。[GitHub CI通过](https://github.com/shijezhang/RepoScope/actions/runs/34433275946)。测试/lint缓存已清理，历史Git对象、运行目录和全部基准证据保留。
