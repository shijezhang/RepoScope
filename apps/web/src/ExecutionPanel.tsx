import { statusLabel } from "./types";
type TestResult = { nodeid: string; status: string; duration?: number };
type Side = { status?: string; results?: TestResult[] };
type Comparison = {
  status?: string;
  comparable?: boolean;
  reason?: string;
  tests?: {
    nodeid: string;
    base_status: string;
    head_status: string;
    finding: string;
  }[];
};
type Execution = {
  status?: string;
  results?: TestResult[];
  base?: Side;
  head?: Side;
  comparison?: Comparison;
};
const findings: Record<string, { label: string; description: string }> = {
  suspected_regression: {
    label: "疑似回归",
    description: "Base 通过、Head 失败；单次结果仍需复跑排除偶发失败。",
  },
  existing_failure: {
    label: "已有失败",
    description: "相同条件下 Base 与 Head 均失败，不能归因为此次改动。",
  },
  new_test_failure: {
    label: "新增测试失败",
    description: "Base 测试目录没有此用例，缺少同用例对照。",
  },
  head_execution_error: {
    label: "Head 执行异常",
    description: "Base 通过，但 Head 执行报错；需检查执行阶段与环境。",
  },
  head_validation_failure: {
    label: "Head 验证失败",
    description: "缺少可比的 Base 结果，尚不能判断是否回归。",
  },
  passed_both: {
    label: "两侧通过",
    description: "此用例在两侧均通过；不代表所有行为均已验证。",
  },
  head_passed: {
    label: "Head 通过",
    description: "Head 此用例通过，缺少可比 Base 结果。",
  },
  inconclusive: {
    label: "证据不足",
    description: "未取得足以判断变更是否引入失败的证据。",
  },
};
export function ExecutionPanel({
  value,
  index,
  expanded = true,
}: {
  value: Record<string, unknown>;
  index: number;
  expanded?: boolean;
}) {
  const execution = value as Execution;
  const comparison = execution.comparison;
  const badge = (status: string) => (
    <span className={`badge ${status}`}>{statusLabel(status)}</span>
  );
  return (
    <details className="execution" open={expanded}>
      <summary>
        执行 {index + 1} ·{" "}
        {statusLabel(comparison?.status || execution.status || "unknown")}
      </summary>
      {comparison ? (
        <>
          <div className="comparison-heading">
            <span>
              Base {badge(execution.base?.status || "not_run")} ·{" "}
              {sideSummary(execution.base)}
            </span>
            <span>
              Head {badge(execution.head?.status || "not_run")} ·{" "}
              {sideSummary(execution.head)}
            </span>
            <span>
              {comparison.comparable ? "环境与测试资产可比" : "缺少可比条件"}
            </span>
          </div>
          <p className="muted">
            以下为单次验证结果。疑似回归需要复跑排除偶发失败，当前证据尚不足以确认回归。
          </p>
          <div className="comparison-scroll">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>测试用例</th>
                  <th>Base</th>
                  <th>Head</th>
                  <th>判断与依据</th>
                </tr>
              </thead>
              <tbody>
                {comparison.tests?.map((row) => (
                  <tr key={row.nodeid}>
                    <td>
                      <code>{row.nodeid}</code>
                    </td>
                    <td>
                      {badge(row.base_status)}
                      <small>{duration(execution.base, row.nodeid)}</small>
                    </td>
                    <td>
                      {badge(row.head_status)}
                      <small>{duration(execution.head, row.nodeid)}</small>
                    </td>
                    <td>
                      <strong>
                        {findings[row.finding]?.label || row.finding}
                      </strong>
                      <p>
                        {findings[row.finding]?.description ||
                          "请核对原始执行结果。"}
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!comparison.tests?.length && (
            <p className="muted">两侧尚无可比的逐测试结果。</p>
          )}
        </>
      ) : execution.results ? (
        <div className="test-list">
          {execution.results.map((row) => (
            <div key={row.nodeid}>
              <code>{row.nodeid}</code>
              {badge(row.status)}
              <span>{row.duration?.toFixed(3)} s</span>
            </div>
          ))}
        </div>
      ) : null}
      <details>
        <summary>执行环境、日志与阶段详情</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    </details>
  );
}
function duration(side: Side | undefined, nodeid: string) {
  const value = side?.results?.find((row) => row.nodeid === nodeid)?.duration;
  return typeof value === "number" ? `${value.toFixed(3)} s` : "";
}

function sideSummary(side: Side | undefined) {
  if (!side?.results?.length) return "无逐测试结果";
  const passed = side.results.filter((row) => row.status === "passed").length;
  const failed = side.results.filter((row) =>
    ["failed", "error"].includes(row.status),
  ).length;
  return `${passed} 通过 · ${failed} 失败`;
}
