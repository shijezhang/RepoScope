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
