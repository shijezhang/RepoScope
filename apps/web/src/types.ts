export type Repository = { repo_id: string; name: string; path: string };
export type Relation = {
  source_id: string;
  target_id: string;
  relation_type: string;
  resolution?: string;
  path?: string;
  line?: number;
};
export type Symbol = {
  symbol_id: string;
  path: string;
  qualname: string;
  kind: string;
  start: number;
  end: number;
  snapshot_id: string;
};
export type Impact = {
  symbol: Symbol;
  distance: number;
  path: Relation[];
  evidence_id: string;
};
export type Report = {
  run_id: string;
  revision: number;
  base: { snapshot_id: string; commit_sha: string };
  head: { snapshot_id: string; commit_sha: string };
  changes: {
    path: string;
    status: string;
    old_path?: string;
    hunks: unknown[];
  }[];
  impacts: Impact[];
  claims: { text: string; status: string; evidence_ids: string[] }[];
  limitations: string[];
  test_plan: {
    plan_id: string;
    nodeids: string[];
    reasons: unknown;
    status: string;
  };
  executions: Record<string, unknown>[];
  completeness: unknown;
};
export type Run = {
  payload?: {
    repo_id: string;
    base: string;
    head: string;
    mode: "direct" | "pr";
    question?: string;
  };
  created?: number;
  run_id: string;
  status: string;
  report?: Report;
  error?: unknown;
  repo_id?: string;
  created_at?: string;
  base?: string;
  head?: string;
  mode?: "direct" | "pr";
  question?: string;
};
export type Evidence = {
  path: string;
  start: number;
  end: number;
  source: string;
  snapshot_id: string;
};
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : value.message || value.detail?.message || JSON.stringify(value),
    );
  return value;
}
export const terminal = (status: string) =>
  [
    "completed",
    "complete",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "interrupted",
  ].includes(status);
export const statusLabel = (s: string) =>
  ({
    completed: "已完成",
    complete: "已完成",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    canceled: "已取消",
    queued: "排队中",
    running: "分析中",
    verified: "已验证",
    inferred: "推断",
    unknown: "未知",
    pending: "未运行",
    collection_required: "待收集测试",
    collected: "已收集",
    not_run: "未运行",
    test_environment_unavailable: "测试环境不可用",
    timeout: "超时",
    skipped: "已跳过",
    error: "执行错误",
    passed: "已通过",
    cancel_requested: "正在取消",
    interrupted: "已中断",
  })[s] || s;
