# ADR009：本地模型文件与缓存绑定

强检索不能仅依赖仓库 revision 与权重 hash：tokenizer、配置及池化参数变化也会改变分块预算或向量语义。

每次强检索在复用内存模型或磁盘向量前，流式计算本地两套模型的完整文件 SHA256 和大小。声明文件 manifest 时要求文件集合、hash、可选大小全部匹配；新增、丢失、修改或符号链接都拒绝。旧配置未声明完整 manifest 时仍计算全部文件指纹，但明确标记 `local-content-fingerprint`，不能称为固定清单核验。

实际文件指纹加入内存运行时 key 和向量 binding；即使 revision 不变，内容变化也重新加载模型、重建语料和向量。文件未变化时仍保留正常 memory/disk hit。固定清单发生漂移时直接失败，不悄悄更新清单。

现有真实模型 11 个 embedding 文件与 7 个 reranker 文件均通过核验，本机一次完整流式校验约 0.194 秒，结果见 `benchmarks/results/model-file-verification-0400.json`。此成本每次查询都会发生，未沿用先前不含该成本的 warm 耗时作为新版本延迟。

这只补全模型文件身份边界。图、文本、语料、测试目录与向量仍未形成全部组件联合发布；corpus partial 仍明确保留，不因向量文件可用而变成完整语料。
