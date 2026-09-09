# RepoScope 开发基准

固定两个公开 Python 仓库，12 条可控变异用于验证工程路径。所有任务尚未人工复核，不是金标或正式质量评测。

在项目根目录运行：

```bash
# 已安装项目依赖后；只读固定仓库，创建 artifacts 下的变异副本
PYTHONPATH=src .venv/bin/python benchmarks/runners/replay.py
# 单条重放
PYTHONPATH=src .venv/bin/python benchmarks/runners/replay.py --case click-01
```

首次先克隆 manifests/repositories.json 指定的 URL/tag，并校验完整 commit：

```bash
git clone --depth 1 --branch 8.1.8 https://github.com/pallets/click.git artifacts/repositories/click
git clone --depth 1 --branch 0.28.1 https://github.com/encode/httpx.git artifacts/repositories/httpx
```

M0 本地环境探针（不会验证产品容器隔离）另需独立环境：

```bash
uv venv --python 3.12.13 artifacts/environments/click
uv pip install --python artifacts/environments/click/bin/python -e artifacts/repositories/click -r artifacts/repositories/click/requirements/tests.txt
uv venv --python 3.12.13 artifacts/environments/httpx
uv pip install --python artifacts/environments/httpx/bin/python -e 'artifacts/repositories/httpx[brotli,cli,http2,socks,zstd]' pytest==8.3.4 trio==0.27.0 trustme==1.2.0 uvicorn==0.32.1 chardet==5.2.0 cryptography==44.0.0
.venv/bin/python benchmarks/runners/probe.py
```

成功探针会保存实际冻结的版本到 manifests/*-environment.txt。首次命令中的 HTTPX 间接依赖没有预先锁定；
复现该次环境时使用生成的清单，其中 editable 路径相对于项目根目录。

精简机器结果进入 results/，完整日志、Git 变异副本与环境留在 artifacts/。重放会清理并重建自己对应的
artifacts/benchmark-replay/<case_id>，不会修改基准原始克隆或用户其他仓库。
`semantic_equal` 只验证图解析缓存一致性；`unresolved_count` 是保留的未知调用数量，不是错误率。
B1/B4、质量指标、覆盖和容器验证未运行时不会填写虚构数字。

两仓库可控回归的同池 base/head 探针：

```bash
.venv/bin/python benchmarks/runners/regression_probe.py
```

实测结果见 results/preparation.json、results/index-consistency.json 和 results/regression-probes.json。
