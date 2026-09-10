# ADR010：强检索语料与向量共同发布

此前只有向量与manifest在一个NPZ里；新Search进程还要对整个快照重新计数分块，真实仓库复读约16–18秒。现在强检索使用版本化的IndexBundle：固定快照的源码、符号、关系、未解析项和诊断，与ready chunks、语料统计、稀疏词项、向量共同写入单个NPZ，fsync临时文件后原子替换。只有实际embedding与reranker成功后才发布；失败保留原版本。

查询绑定包括语义快照hash（排除构建耗时等stats）、实际模型文件指纹、revision、token预算、chunk/sparse/bundle版本和推理库版本。加载校验绑定、分量hash、向量形状/校验和，以及每个chunk的父符号、源码行范围/hash、完整签名/声明/context、稳定ID和已保存预算；重建稀疏词项核对一致。磁盘损坏直接报错，不悄悄覆盖。复读不再重新运行tokenizer分块循环，实际query和reranker pair预算仍检查。

ready表示文件完整可读；语料partial由corpus_stats单独保存和返回。超限源码不会因缓存复读被隐藏。每个版本有独立内容寻址路径，不改历史任务引用。

这完成了强检索内部快照/文本/向量的原子发布，不等于产品全部索引已事务性发布：SQLite parser快照仍独立保存，CLI/Agent默认弱检索仍直接使用它；真实pytest collection/test assets也属于另外的执行边界。后续须把查询profile与发布状态接入Store/任务流程，再验收“失败不能切换默认版本”。当前不能宣称全局联合发布、向量增量或正式质量收益。

真实本地探针 `strong-retrieval-bundle-0430.json` 的四查询与两次磁盘复读完成：Click复读2.05秒、HTTPX1.72秒，原排名相同且分数差0；此前为18.11/16.03秒。冷构建仍100.88/53.53秒，未改善。两语料仍partial，无质量标签。

最终源码独立复核 `strong-retrieval-bundle-recheck-0430.json`：四查询全部memory/disk hit，两次同进程模型重载复读2.22/1.90秒，排名与分数仍相同。新进程首次Click请求包含库导入，耗时16.08秒；不能把约2秒当作完整进程冷启动时延。
