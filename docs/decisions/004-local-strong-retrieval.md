# ADR 004：本地强检索模型与测量边界

状态：本地模型与强检索可运行性已验证；正式质量评测尚未完成。

首轮使用两个小型英文模型，在本地 CPU 上运行：

- Embedding：`sentence-transformers/all-MiniLM-L6-v2`，固定 revision
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，复用本机已有权重的独立副本。
- CrossEncoder：`cross-encoder/ms-marco-MiniLM-L6-v2`，固定 revision
  `233902d25c440f23af6f7d6e94d2946bac0bee0a`，下载至项目 `artifacts/models/`。

模型卡均声明 Apache-2.0，分别见
[Embedding 固定版本模型卡](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md) 与
[Reranker 固定版本模型卡](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/blob/233902d25c440f23af6f7d6e94d2946bac0bee0a/README.md)。
原计划 bge-m3 是候选而非已冻结基线；本轮调整为更小的 MiniLM，以先验证单机资源和真实模型 API。
这不是对 bge-m3 的质量对照，也不能据此宣称中文或源码检索质量更好。

模型探针使用独立环境，不修改应用主环境。为复用本机缓存，Torch/NumPy/SciPy/Pydantic 采用与主锁不同的兼容版本，
实际完整版本清单单独保存；不能把它称为主锁完全一致的验收。复现方式：

```bash
uv venv --python 3.12.13 artifacts/environments/models
uv pip install --python artifacts/environments/models/bin/python -r benchmarks/manifests/models-environment.txt
```

模型目录和完整 revision 冻结在 `benchmarks/manifests/models.json`。推理阶段设置离线模式，
不使用 API 密钥，不把用户源码或查询发送至外部服务；联网仅用于公开依赖和公开模型的下载。
不向用户全局 Hugging Face 缓存写入文件。

首次准备固定模型文件（仅此步骤联网，校验固定文件哈希）：

```bash
.venv/bin/python benchmarks/runners/prepare_models.py
```

实际离线运行入口：

```bash
artifacts/environments/models/bin/python benchmarks/runners/strong_probe.py
```

入口对两个固定公开仓库分别运行两个英文自然语言查询，实际调用
`Search.strong_query` 的 Dense、BM25/标识符、RRF 与 CrossEncoder，不以 sparse 降级冒充强基线。
模型首次下载、已有缓存复制、首次查询和同快照后续查询分别记录；已有 embedding 的原始下载耗时不可追溯，
保持 unavailable。RSS 使用进程高水位，包含 Python、模型和索引，并不等于模型权重文件大小。

四个查询全部属于未复核开发探针，没有 relevance 金标。结果只用于检查可运行性和资源消耗，不产生
MRR、Recall、准确率提升或正式 B1 与 B2/B3/B4 对比。MiniLM 为通用英文模型，长符号文本受到
模型 token 上限截断，尚未完成面向源码的长函数分块和质量验证。


实测使用 parser v3，两仓库各两个查询成功，另各重建一次 Search 对象从磁盘缓存查询，排名一致且最大分数差为 0。
Click/HTTPX 的向量构建分别约 8.06/8.38 秒；两个后续不同查询分别约 0.37/0.79 秒。
这是单次开发测量，不是同查询反复统计，也不是稳定加速比。进程 RSS 高水位最高约 837 MB。
详细结果与实际依赖版本见 `benchmarks/results/strong-retrieval-probe.json`、
`benchmarks/results/model-preparation.json` 和 `benchmarks/manifests/models-environment.txt`。

质量局限没有通过运行成功而消失：HTTPX 两个查询的首位分别为 `test_head` 测试与 `_models.py` 模块，
当前结果未证明可以稳定定位目标函数。保留完整 top 5，不为了改善这四个开发查询而修改标签或隐藏结果。
Reranker 输出是未校准排序分数，不是置信概率。

向量缓存按快照、源码/符号文本、两个模型 revision、权重 hash、模型库版本与 device 绑定。
每个新快照仍全量编码，不能称为向量增量更新。Manifest 和向量放在同一 NPZ，临时文件写完并 fsync 后
通过 os.replace 一次发布；仅在本次 embedding 和 reranker 都成功之后发布 ready。校验失败显式报错，
没有 sparse 降级。缓存故障注入验证：失败发布保留旧 ready 文件，残缺 artifact 不会被当成 ready。
此缓存是可选强检索索引，未将 parser 快照与所有检索组件纳入同一个数据库事务；不能宣称全栈联合原子发布。
