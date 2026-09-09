# ADR 008：真实tokenizer约束的分块强检索

2026-09-10夜间实现与本地验证；不执行模型API外发。

Search.strong_query现对每个固定snapshot符号调用chunk_symbol。计数使用Embedding与CrossEncoder实际tokenizer，明确truncation=False；文档预算同时满足Embedding单文本上限和CrossEncoder为query及pair特殊token预留后的上限。默认query预留64 tokens，超长query明确拒绝，不静默截断。

只有ready块进入模型；完整签名/声明或单行超限的块保留在oversized统计中。部分符号无法完整索引时Search返回partial。向量文件本身的ready只表示派生产物可读，不表示全语料无缺口。

Dense按chunk召回后先聚合父符号；每个候选父符号最多选择语义最佳与词法最佳两个不同块重排，再按父符号取最佳结果。最终每父只返回一项，附原snapshot、parent、path、行范围、source与hash，便于核查命中的函数后段。每个query/chunk pair在重排调用前再核对真实token数。模型返回向量/分数数量或有限性异常时不发布缓存。

缓存绑定chunk ID、文本hash、父符号映射、预算/重叠/分块版本及模型/库版本，不能把旧的整符号向量当成分块向量。仍为新snapshot全量编码，并非向量增量；全部索引联合发布尚待实现。

实测固定模型和4个原有英文开发query，结果见strong-retrieval-chunks-0330.json，原strong-retrieval-probe.json保持不变：

| 仓库 | ready块 | oversized块 | 完全覆盖父符号/全部符号 | 首次查询总时长 | 同对象后续query |
|---|---:|---:|---:|---:|---:|
| Click | 3258 | 75 | 1422/1452 | 97.11s | 0.51s |
| HTTPX | 2802 | 85 | 1251/1301 | 51.80s | 0.61s |

两次磁盘重载排名相同、最大分数差0；重载总时长18.11/16.03秒，包含重新加载模型和重建可派生语料。四次查询均有partial语料状态。日志中的超长token警告出现在完整父符号的预算测量阶段；这些超限文本不提交模型。首次准备成本明显增加，不能只报告warm查询时间。

质量未标注：部分首位仍为模块或测试符号，不能宣称分块已改善业务检索质量。新增7项假模型集成测试独立检查所有实际模型输入预算、后段命中、父级去重、快照/hash、partial/全超限、超长query和缓存隔离。

后续：减少分块语料重建成本；核验并绑定实际tokenizer/config文件指纹；处理解析/稀疏/向量联合发布；在独立复核标签上评估粒度与质量，不能用这4个query反复调到看起来更好。
