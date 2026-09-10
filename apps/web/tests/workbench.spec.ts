import { test, expect } from "@playwright/test";
// Contract fixtures only: these tests validate UI behavior, not analysis accuracy.
const report = {
  run_id: "contract-run",
  revision: 1,
  base: { snapshot_id: "base-snapshot", commit_sha: "1111111111" },
  head: { snapshot_id: "head-snapshot", commit_sha: "2222222222" },
  changes: [
    {
      path: "billing.py",
      status: "M",
      hunks: [{ old_start: 1, new_start: 1 }],
    },
  ],
  impacts: [
    {
      symbol: {
        symbol_id: "billing-total",
        path: "billing.py",
        qualname: "total",
        kind: "function",
        start: 1,
        end: 2,
        snapshot_id: "base-snapshot",
      },
      distance: 0,
      path: [],
      evidence_id: "base-evidence",
    },
  ],
  claims: [
    {
      text: "Contract fixture claim",
      status: "inferred",
      evidence_ids: ["base-evidence"],
    },
  ],
  limitations: ["Contract fixture: static inference only"],
  test_plan: {
    plan_id: "plan-1",
    nodeids: ["tests/test_billing.py::test_total"],
    status: "pending",
    reasons: ["Changed function"],
  },
  executions: [],
  completeness: { static: true },
};

test("live detail status replaces stale queued history without manual refresh", async ({ page }) => {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/repositories") return route.fulfill({ json: [] });
    if (path === "/api/analyses") return route.fulfill({ json: [{ run_id: "contract-run", status: "queued" }] });
    if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: "" });
    if (path === "/api/analyses/contract-run") return route.fulfill({ json: { run_id: "contract-run", status: "completed", report } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/?run=contract-run");
  await expect(page.getByRole("heading", { name: /变更影响报告 已完成/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "contract-run 已完成" })).toBeVisible();
  await page.getByRole("button", { name: "刷新历史", exact: true }).click();
  await expect(page.getByRole("button", { name: "contract-run 已完成" })).toBeVisible();
});

test("empty state and failed request have actionable feedback", async ({
  page,
}) => {
  await page.route("**/api/repositories", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/analyses", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByText("暂无分析记录")).toBeVisible();
  await page.getByRole("button", { name: "创建第一次分析" }).click();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByRole("alert")).toContainText("请选择仓库");
});

test("create, fixed base evidence, test plan and exports use the API contract", async ({
  page,
}) => {
  let request: Record<string, unknown> | undefined;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/repositories")
      return route.fulfill({
        json: [
          { repo_id: "repo-1", name: "Contract fixture", path: "/fixture" },
        ],
      });
    if (path === "/api/analyses" && route.request().method() === "POST") {
      request = route.request().postDataJSON();
      return route.fulfill({ json: { run_id: "contract-run" } });
    }
    if (path === "/api/analyses") return route.fulfill({ json: [] });
    if (path.endsWith("/events"))
      return route.fulfill({
        contentType: "text/event-stream",
        body: 'data: {"event_id":"1","state":"completed","message":"Contract complete"}\n\n',
      });
    if (path === "/api/analyses/contract-run")
      return route.fulfill({
        json: {
          run_id: "contract-run",
          repo_id: "repo-1",
          status: "completed",
          report,
        },
      });
    if (path === "/api/evidence/base-evidence")
      return route.fulfill({
        json: {
          snapshot_id: "base-snapshot",
          path: "billing.py",
          start: 1,
          end: 2,
          source: "def total():\n    return 1",
        },
      });
    return route.fulfill({
      status: 404,
      json: { message: "Unhandled contract route" },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "创建第一次分析" }).click();
  await page.getByLabel("Base", { exact: true }).fill("v1");
  await page.getByLabel("Head", { exact: true }).fill("v2");
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(
    page.getByRole("heading", { name: "变更影响报告" }),
  ).toBeVisible();
  expect(request).toMatchObject({
    repo_id: "repo-1",
    base: "v1",
    head: "v2",
    mode: "direct",
    allow_tests: false,
  });
  await expect(page).toHaveURL(/run=contract-run/);
  await page.getByRole("button", { name: "total billing.py" }).click();
  await expect(page.getByText("def total():", { exact: true })).toBeVisible();
  await expect(page.locator(".source-meta .badge")).toHaveText("base");
  await expect(
    page.getByText("tests/test_billing.py::test_total", { exact: true }),
  ).toBeVisible();
  await page.getByText("导出报告", { exact: true }).click();
  await expect(
    page.getByRole("link", { name: "JSON", exact: true }),
  ).toHaveAttribute("href", "/api/analyses/contract-run/export?format=json");
  await page.getByRole("tab", { name: "变更文件" }).click();
  await expect(page.locator(".files")).toContainText("billing.py");
  await page.setViewportSize({ width: 640, height: 900 });
  await expect(
    page.getByRole("button", { name: "运行建议测试" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("comparison contract shows both versions and uncertain regression; retry targets test job", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | undefined;
  let cancelled = "";
  let started = false;
  const comparisonReport = {
    ...report,
    completeness: "inconclusive",
    test_plan: {
      ...report.test_plan,
      nodeids: [],
      status: "test_environment_unavailable",
    },
    executions: [
      {
        kind: "comparison",
        base: {
          status: "completed",
          results: [
            {
              nodeid: "tests/test_total.py::test_boundary",
              status: "passed",
              duration: 0.01,
            },
          ],
        },
        head: {
          status: "failed",
          results: [
            {
              nodeid: "tests/test_total.py::test_boundary",
              status: "failed",
              duration: 0.02,
            },
          ],
        },
        comparison: {
          status: "suspected_regression",
          comparable: true,
          tests: [
            {
              nodeid: "tests/test_total.py::test_boundary",
              base_status: "passed",
              head_status: "failed",
              finding: "suspected_regression",
            },
            {
              nodeid: "tests/test_total.py::test_old",
              base_status: "failed",
              head_status: "failed",
              finding: "existing_failure",
            },
            {
              nodeid: "tests/test_total.py::test_new",
              base_status: "not_run",
              head_status: "failed",
              finding: "new_test_failure",
            },
          ],
        },
      },
    ],
  };
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/repositories" || path === "/api/analyses")
      return route.fulfill({ json: [] });
    if (path.endsWith("/events"))
      return route.fulfill({ contentType: "text/event-stream", body: "" });
    if (path.endsWith("/test-runs")) {
      submitted = route.request().postDataJSON();
      started = true;
      return route.fulfill({
        json: { execution_id: "test-2", status: "running" },
      });
    }
    if (path.endsWith("/cancel")) {
      cancelled = path;
      return route.fulfill({ json: { status: "cancelled" } });
    }
    if (path === "/api/jobs/test-2")
      return route.fulfill({
        json: { run_id: "test-2", status: cancelled ? "cancelled" : "running" },
      });
    return route.fulfill({
      json: {
        run_id: "contract-run",
        status: "completed",
        report: comparisonReport,
        test_attempts: started
          ? [
              { execution_id: "test-1", attempt: 1, status: "completed" },
              {
                execution_id: "test-2",
                attempt: 2,
                status: cancelled ? "cancelled" : "running",
              },
            ]
          : [{ execution_id: "test-1", attempt: 1, status: "completed" }],
      },
    });
  });
  await page.goto("/?run=contract-run");
  await expect(page.locator(".report-heading")).toContainText("证据不足");
  const table = page.getByRole("table");
  await expect(table).toContainText("疑似回归");
  await expect(table).toContainText("已有失败");
  await expect(table).toContainText("新增测试失败");
  await expect(table.getByRole("row").nth(1)).toContainText("已通过");
  await expect(table.getByRole("row").nth(1)).toContainText("失败");
  await page.getByRole("button", { name: "重试验证", exact: true }).click();
  expect(submitted).toMatchObject({
    attempt: 2,
    reason: "Retry requested after environment preparation",
  });
  await page.getByRole("button", { name: "取消测试" }).click();
  expect(cancelled).toBe("/api/analyses/test-2/cancel");
});

test("optional model investigation defaults off and explicitly reports unavailable configuration", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/repositories")
      return route.fulfill({
        json: [
          { repo_id: "repo-1", name: "Contract fixture", path: "/fixture" },
        ],
      });
    if (path === "/api/analyses" && route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      return route.fulfill({ json: { run_id: "contract-run" } });
    }
    if (path === "/api/analyses") return route.fulfill({ json: [] });
    if (path.endsWith("/events"))
      return route.fulfill({ contentType: "text/event-stream", body: "" });
    return route.fulfill({
      json: {
        run_id: "contract-run",
        status: "completed",
        payload: { repo_id: "repo-1", agent: true },
        report: {
          ...report,
          completeness: "partial",
          limitations: [
            "Set REPOSCOPE_LLM_MODEL and REPOSCOPE_LLM_API_KEY for optional Agent",
          ],
        },
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "创建第一次分析" }).click();
  const toggle = page.getByRole("checkbox", { name: "启用模型补查（可选）" });
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  const validation = page.getByRole("checkbox", {
    name: "分析时执行登记测试（Docker）",
  });
  await expect(validation).not.toBeChecked();
  await validation.check();
  await page
    .getByLabel("关注的问题（可选）")
    .fill("Which callers need additional evidence?");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已降级为基础结构分析");
  expect(submitted).toMatchObject({
    agent: true,
    allow_tests: true,
    question: "Which callers need additional evidence?",
  });
  await expect(page.locator(".report-heading")).toContainText("部分分析");
});
