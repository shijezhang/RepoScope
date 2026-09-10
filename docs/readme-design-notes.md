# README 重写说明

本轮仅调整项目入口文档与配图，不改变产品行为。参考时间：2026-09-10。

## 参考项目

| README | 借鉴点 | 在 RepoScope 中的应用 |
| --- | --- | --- |
| [Aider](https://github.com/Aider-AI/aider/blob/main/README.md) | 一句话定位、按用户收益介绍能力、简短 Getting Started | 首屏回答用途；能力用用户问题组织；首次运行直接给可执行命令。 |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent/blob/main/README.md) | 说明 Agent 用途及适用范围，将安装与深入使用导向文档 | Agent 作为可选模块；首页呈现边界，详细实验移至证据入口。该 README 当前提示开发重心迁向 mini-swe-agent，此处仅参考写法。 |
| [Continue](https://github.com/continuedev/continue/blob/main/README.md) | 清晰的产品标题、短介绍、按入口组织使用路径 | 分开 CLI 首次分析与 Web 工作台；避免首页堆叠所有命令。该仓库当前声明只读、不再活跃维护，此处不作选型推荐。 |
| [SCIP](https://github.com/scip-code/scip/blob/main/README.md) | 先交代代码索引用途，再分流 CLI、设计与开发文档 | 将用途、运行方法、实现契约和开发检查分层，避免把实现日志放在介绍之前。 |

以上仅参考信息组织方式；正文与流程图为 RepoScope 原创，没有复制参考项目的文案、图片、徽章或效果声明。

## 本轮取舍

- 用一张横向 SVG 解释“快照 → 变化 → 影响 → 可选验证”，在 GitHub 正文宽度下保留清楚的文字；显式标注它是工作流程，不伪装成产品截图。
- 首页移除旧的整屏工作台截图引用。原截图仍为历史演示证据，保留原文件。
- 安装路径默认无模型、无 Docker；首次 CLI 分析使用本仓库，无需额外下载示例项目。明确文档提交可能没有 Python 符号变化。
- 将模型、Docker 和 Agent 折叠为按需启用项，减少首次使用的配置负担。
- 修正旧的“所有跨文件引用重新解析”“无端到端向量增量”“Agent 两例均未完成”等陈旧表述。以当前代码、validation.json 和 v5 验收说明核对。
- 不用单例加速、开发样本或 mock 契约测试推导质量收益。工程验证与尚未完成的效果评测分开陈述。
- 不把面试材料、个人配置或历史 GraphRAG 清理过程带入公开项目首页。
