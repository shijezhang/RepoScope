# RepoScope Web

本地 React + TypeScript 工作台，所有结果来自 `/api`，没有内置成功数据。

```bash
npm ci
npm run dev
```

开发服务默认通过 Vite 将 `/api` 转发至 `http://127.0.0.1:8000`。生产构建运行 `npm run build`，由 FastAPI 或同源静态服务提供 `dist`；SSE 路由需要关闭反向代理缓冲。

分析链接保留 `?run=<run_id>`，刷新可恢复状态。SSE 断线会显示重连提示，轮询作为状态回退。历史报告、源码证据与导出均从后端固定快照读取。测试仅在用户点击“运行建议测试”后提交，后端校验计划与执行环境。

样式使用本地 CSS 与系统字体，图使用 React Flow，同时提供等价的文本路径。第一版按实际复杂度未引入 Tailwind / Radix，以降低依赖和维护开销。

## 界面验证

`npm run test:e2e` 启动 Vite 与本机 Google Chrome（需已安装），执行 Playwright 契约测试。测试通过显式 mock 数据验证空态、输入错误、创建请求、base 证据、测试列表、导出链接与窄屏布局；这不构成后端分析准确性或真实集成成功的证明。
