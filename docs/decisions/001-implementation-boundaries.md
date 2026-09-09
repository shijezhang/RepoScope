# ADR 001：开发版范围与版本契约

日期：2026-09-09。状态：已实施，实验验收以 delivery-status.md 为准。

原项目基线为 `ec2595991b50419a3a171e513d52f6e5814a645b`；在 `zhangshijie/reposcope-upgrade` 开发。开始时已有 `.claude` 删除项与未跟踪 docs。用户授权升级及清理旧项目，保留本升级计划；旧业务入口由新 CLI 集成检查通过后替换。

## 保留的主线

Python 固定 Git 快照 → 有向有限代码图 → 双侧 diff → 影响证据 → 已收集测试 → 受控执行 → 同一结构化报告 → Web/CLI。缺少证据时降级，不从模型语言创建边或测试通过记录。

## 具体决策

1. 运行环境限定 Python 3.12（本机 3.12.13）。AST 使用 3.12 grammar，超出能力的文件保留 diagnostics，快照标记 partial。Python 语义复杂处优先 unknown/candidate。
2. SQLite 原子保存整个不可变 Snapshot JSON 和任务元数据；NetworkX 视图按快照重建。当前规模无需多表边查询，也不引入图数据库。与计划的逐节点/边表布局不同，但快照隔离与原子发布契约保持。
3. AST 缓存键为路径、源码、解析规则版本。只缓存文件内部语法产物，每个新快照重解全部跨文件引用。这比最小失效传播保守、易验证，结果只能称解析增量。语义模型索引尚未原子持久化，因此不能宣称端到端混合索引完成。
4. 证据 ID 包含快照、源码范围与内容 hash。每份报告保存 revision，测试追加新 revision；不覆盖此前版本。追问用相同 SHA 新建子任务（前端链接），原报告保留。
5. React + TypeScript + Vite + React Flow，采用本地 CSS 与系统字体。当前交互无需 Radix 的复杂控件，也无需 Tailwind；减少依赖不改变产品信息结构。
6. 目标测试严格 Docker。缺少 Docker 不切宿主执行。可信 Click/HTTPX 的本地环境探针单独命名与保存，不是产品模式。
7. Agent 先提供有界只读补查。测试保持显式用户动作；尚未实现与固定流程相同预算的模型驱动测试反馈实验，因此 M4 部分完成。
8. B1 必須固定 Embedding 与 Reranker revision。本机只有旧 MiniLM 缓存、无已验证重排模型；未把稀疏检索假称为强基线。模型资源实验待完成。
9. 正式 120 条目标不以自动变异凑数。当前 12 条仅为 unreviewed 开发探针，未来完成标注与分组后再冻结 holdout。实验不报告无来源准确率。

## 代价与失效边界

全量重解关系可能抵消 AST 缓存收益。静态 Python 图对反射、动态属性、装饰器与未知返回对象没有完备保证，回退全测试率可能较高。证据校验只保证引用与路径结构，不替代业务语义人工复核。SQLite 方案适合单机单worker演示，不承诺多租户部署。

实施时重新核对的官方能力说明：[Python AST](https://docs.python.org/3.12/library/ast.html)、[coverage contexts](https://coverage.readthedocs.io/en/latest/contexts.html)、[pytest usage](https://docs.pytest.org/en/stable/how-to/usage.html)。实际依赖版本由锁文件决定。
