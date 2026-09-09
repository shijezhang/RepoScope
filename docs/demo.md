# 3–5分钟演示

先按README启动API和worker；Docker使用独立Colima环境。基准仓库已准备时执行replay.py获得新的不可变run，实际路径从benchmarks/results/index-consistency.json的replay_repository读取。不要删除旧run来刷新演示。

1. **0:00–0:40 固定版本。** 登记Click case的实际路径，profile为click；填写记录的base/head SHA。展示最终SHA，说明未提交工作区不会混入。
2. **0:40–1:40 影响证据。** 展示区间边界变化、两侧源码与潜在调用方。图只证明静态关系，未知和截断仍保留。下载JSON核对revision。
3. **1:40–2:40 真实回归。** 运行登记测试池，或查看同SHA的真实历史：Click base39通过、head4失败；HTTPX base106通过、head1失败。两次观察一致仍不声称排除flaky。显示逐测试两侧与疑似回归。
4. **2:40–3:40 未知与重试。** 使用dynamic-unknown fixture；展示getattr保留未知。历史环境不足报告可以显式重试，实际UI记录已从attempt1环境不足转为attempt2对照成功。任务完成与分析partial分别显示。
5. **3:40–5:00 工程与局限。** 展示12条解析一致性、4个真实强检索query与保存的负面结果；测试选择缩减0%，不把子阶段耗时包装为节省。说明推理模型与正式金标仍待完成。

`benchmarks/runners/examples.py`生成三格式静态分析示例；它们明确没有产品测试执行记录。真实容器结果在public-docker-validation.json，不能把单独宿主探针拼成产品执行。`apps/web/scripts/live-check.mjs`使用真实Chrome，可重放当前API/worker的创建、证据、导出与测试流程，完整参数见脚本。
