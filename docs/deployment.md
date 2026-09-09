# 部署与执行环境

推荐：宿主机运行 API + 一个 worker，浏览器访问本机 8000；worker 使用宿主 Docker CLI 管理目标测试容器。此方式让源码只读快照与执行临时目录的挂载路径一致。

`uv sync --frozen` 安装 API；前端 `npm ci && npm run build` 产生静态文件。先启动 `reposcope serve`，再启动 `reposcope worker`。用户注册路径必须在 `REPOSCOPE_ALLOWED_ROOTS` 范围内，默认项目 cwd。不要将未配置认证的本地服务直接暴露公网。

## 应用容器

`docker compose up --build` 提供 API 与分析 worker。默认无 Docker socket，支持索引/报告工作台；测试请求会明确显示环境不可用。要执行测试，改用推荐的宿主 worker 和已准备 profile。Compose 不声称嵌套测试容器模式已完成。

仓库从 `./artifacts/repositories` 只读挂载到 `/repositories`，登记容器内路径。状态持久卷在 `/state`；API 和worker共享同一目录。不要同时启动 Compose worker 和宿主 worker 操作同一状态目录。

本机Docker与Colima已安装，宿主API/worker与目标测试容器的模式已实际通过；应用Compose镜像尚未实跑，因此该部署方式仍待验收。

## 取消与恢复

API 只登记任务，worker 通过 SQLite 租约领取，后台心跳续租。取消请求与完成资源回收是不同状态。测试 execution_id 在提交前持久化；worker失联后进入 interrupted，清理已知容器身份，不自动重跑测试。相同计划与attempt重复提交返回原execution_id；显式重试需理由、前一attempt终态，最多3次。授权分析内测试的任务失联后同样进入interrupted，避免重复模型提交。

## 资源与产物

单文件 1 MB、读取总量 80 MB、最多 15,000 个文件；图遍历最多 1,500 节点、8 跳。默认任务 180秒、每次测试执行120秒，受控模型补查最多60秒。首次源码索引与基准准备单独记录；这些是开发上限，不是吞吐承诺。

`artifacts/state` 保存SQLite、覆盖、执行日志；不可在运行中清理。`artifacts/benchmark-replay` 是可重建变异仓库，runner只替换自己case目录。`benchmarks/results` 为提交的精简证据。删除整个 artifacts 意味着删除本地历史，重新重放可生成新历史。
