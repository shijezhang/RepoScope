# 部署与执行环境

推荐：宿主机运行 API + 一个 worker，浏览器访问本机 8000；worker 使用宿主 Docker CLI 管理目标测试容器。此方式让源码只读快照与执行临时目录的挂载路径一致。

`uv sync --frozen` 安装 API；前端 `npm ci && npm run build` 产生静态文件。先启动 `reposcope serve`，再启动 `reposcope worker`。用户注册路径必须在 `REPOSCOPE_ALLOWED_ROOTS` 范围内，默认项目 cwd。不要将未配置认证的本地服务直接暴露公网。

## 应用容器

`docker compose build api` 后运行 `docker compose up --no-build`，由同一个本地构建镜像提供 API 与分析 worker。默认无 Docker socket，支持索引/报告工作台；测试请求会明确显示环境不可用。要执行测试，改用推荐的宿主 worker 和已准备 profile。Compose 不声称嵌套测试容器模式已完成。

仓库默认从 `./artifacts/repositories` 只读挂载到 `/repositories`，登记容器内路径。状态持久卷在 `/state`；API 和 worker 共享同一目录。不要同时启动 Compose worker 和宿主 worker 操作同一状态目录。

可通过 `REPOSCOPE_HTTP_PORT` 改端口、`REPOSCOPE_REPOSITORY_DIR` 指定只读仓库父目录、`REPOSCOPE_STATE_MOUNT` 指定独立状态目录，`REPOSCOPE_APP_IMAGE` 指定镜像名。API 有实际 HTTP 健康检查，worker 等待 API 健康后启动；两者默认各限 1 CPU、512 MiB 内存。没有自动透传宿主模型凭据。

应用镜像使用 `npm ci` 和 `uv sync --frozen` 安装锁文件中的依赖，构建后端固定为 setuptools 84.0.0。先用 `--no-install-project` 缓存运行依赖，再复制源码并用 `--no-editable --no-cache` 安装本项目，源码变动不需要重新下载全部依赖。Python 镜像额外安装固定版本 Git；容器内只信任 `/repositories/*` 下的挂载仓库，不更改宿主 Git 配置。基础镜像可通过 `REPOSCOPE_NODE_IMAGE`、`REPOSCOPE_PYTHON_IMAGE` 传入不可变 digest，Git 包版本通过 `REPOSCOPE_GIT_VERSION` 显式指定。更换基础发行版时需要匹配其 Git 包版本。

宿主 API/worker 与目标测试容器、应用 Compose 的 analysis-only 模式已分别完成真实验收。Compose 不包含目标测试执行环境，也不读取宿主 Docker socket。

## 取消与恢复

API 只登记任务，worker 通过 SQLite 租约领取，后台心跳续租。取消请求与完成资源回收是不同状态。测试 execution_id 在提交前持久化；worker失联后进入 interrupted，清理已知容器身份，不自动重跑测试。相同计划与attempt重复提交返回原execution_id；显式重试需理由、前一attempt终态，最多3次。授权分析内测试的任务失联后同样进入interrupted，避免重复模型提交。

## 资源与产物

单文件 1 MB、读取总量 80 MB、最多 15,000 个文件；图遍历最多 1,500 节点、8 跳。默认任务 180秒、每次测试执行120秒，受控模型补查最多60秒。首次源码索引与基准准备单独记录；这些是开发上限，不是吞吐承诺。

`artifacts/state` 保存SQLite、覆盖、执行日志；不可在运行中清理。`artifacts/benchmark-replay` 保存不可变 run 目录和变异仓库；后续重放不会替换旧目录，历史报告仍可引用原 Git 对象。`benchmarks/results` 为提交的精简证据。删除整个 artifacts 意味着删除本地历史，重新重放可生成新历史。


## 独立 Compose 验收

先准备 fixture replay，再运行专用验收脚本。脚本使用新的 Compose project、8081 端口、独立状态目录和只读仓库；验证健康检查、静态前端、API 登记、worker 完成一次固定 SHA 分析，最后自动 down 自己的容器和网络，保留构建镜像与证据。它不会执行目标 pytest，也不会挂载 Docker socket。

```bash
.venv/bin/python benchmarks/runners/replay.py --case fixture-01
.venv/bin/python benchmarks/runners/compose_validation.py \
  --node-image public.ecr.aws/docker/library/node@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 \
  --python-image public.ecr.aws/docker/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  --port 8081
```

构建与应用日志、独立 SQLite 状态保存在 `artifacts/compose-validation/<project>/`；机器可读结果写入 `benchmarks/results/compose-validation.json`。`--skip-build` 仅复用已有验收镜像，结果会明确注明没有重新构建。脚本使用 `--env-file /dev/null`，不读项目 `.env` 中的部署变量。


### 已记录验收（2026-09-10）

[机器结果](../benchmarks/results/compose-validation.json)记录了实际 Compose 5.5.1 / Buildx 0.37.0 验收：API 与分析 worker 启动健康，8081 首页正常返回，固定 fixture 的分析完成并输出 28 个影响条目和 28 条带证据引用的推断。报告为 partial，保留未执行测试与动态行为限制。

依赖层准备好后的最终缓存构建为 11.11 秒，启动健康为 6.71 秒，分析为 1.68 秒。构建输入的 22 个 Python 源文件与容器实际导入的源码逐文件 hash 一致；锁文件 hash、完整环境、挂载信息和构建日志位置均记录在结果中。

成功镜像保留为 `reposcope-compose-acceptance:local`，不可变 ID 为 `sha256:9780d4a88dbfe0ab494add219d7313115d79861b9b0063d6fd4d220b405d803d`。验收 project 的应用容器、网络和临时命名卷已由脚本 down；独立 SQLite 和日志作为证据保留。宿主原来的 8000 服务、默认状态目录未参与这次验收。

容器通过 `REPOSCOPE_WEB_DIR=/app/apps/web/dist` 定位静态前端，支持非 editable 安装。验收未配置模型凭据，未执行目标 pytest，也未验证嵌套容器模式。
