// Real local integration smoke check. No network routes are mocked.
import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
const origin = process.env.REPOSCOPE_WEB_URL || "http://127.0.0.1:8000";
const repository = process.env.REPOSCOPE_LIVE_REPO;
if (!repository)
  throw new Error("Set REPOSCOPE_LIVE_REPO to an allowed Git fixture path");
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  if (process.env.REPOSCOPE_LIVE_RUN) {
    await page.goto(`${origin}/?run=${process.env.REPOSCOPE_LIVE_RUN}`);
  } else {
    await page.goto(origin);
    await page.getByRole("button", { name: "新建分析", exact: true }).click();
    await page.getByLabel("或登记本地 Git 仓库").fill(repository);
    await page.getByLabel("Base", { exact: true }).fill("HEAD~1");
    await page.getByLabel("Head", { exact: true }).fill("HEAD");
    await page.getByRole("button", { name: "开始分析", exact: true }).click();
  }
  await page
    .getByRole("heading", { name: "变更影响报告" })
    .waitFor({ timeout: 60000 });
  await page
    .locator(".report-heading .badge.completed")
    .waitFor({ timeout: 60000 });
  const runId = new URL(page.url()).searchParams.get("run");
  await page.locator(".impact-list button").first().click();
  await page.locator(".source code").first().waitFor();
  await page.getByText("导出报告", { exact: true }).click();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "JSON", exact: true }).click();
  const file = await downloaded;
  await mkdir("../../docs/examples", { recursive: true });
  const response = await page.request.get(
    `${origin}/api/analyses/${runId}/export?format=json`,
  );
  const exported = await response.json();
  if (exported.run_id !== runId)
    throw new Error("Export belongs to different run");
  await page.getByText("导出报告", { exact: true }).click();
  const before = await (
    await page.request.get(`${origin}/api/analyses/${runId}`)
  ).json();
  let targetAttempt = Math.max(
    0,
    ...(before.test_attempts || []).map((item) => item.attempt),
  );
  if (process.env.REPOSCOPE_LIVE_RETRY === "1" && targetAttempt === 1) {
    const submitted = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/analyses/${runId}/test-runs`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "重试验证", exact: true }).click();
    const response = await submitted;
    if (!response.ok()) throw new Error(await response.text());
    console.log("Retry submitted", response.request().postData());
    targetAttempt = 2;
  } else if (!exported.executions.length) {
    await page
      .getByRole("button", { name: "运行建议测试", exact: true })
      .click();
    targetAttempt = 1;
  }
  let run;
  for (let i = 0; i < 120; i++) {
    run = await (
      await page.request.get(`${origin}/api/analyses/${runId}`)
    ).json();
    const attempt = run.test_attempts?.find(
      (item) => item.attempt === targetAttempt,
    );
    if (i % 10 === 0)
      console.log(
        "Waiting for real test attempt",
        targetAttempt,
        attempt?.status,
      );
    if (
      attempt &&
      ["completed", "failed", "cancelled", "interrupted"].includes(
        attempt.status,
      )
    )
      break;
    await page.waitForTimeout(1500);
  }
  if (!run.report?.executions?.length)
    throw new Error("Test execution did not reach report");
  await page.waitForTimeout(3000);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../docs/examples/workbench.png",
    fullPage: true,
  });
  const comparison = run.report.executions
    .filter((item) => item.kind === "comparison")
    .at(-1);
  if (comparison) {
    const table = page.getByRole("table").last();
    await table.waitFor();
    if (
      (await table.getByRole("row").count()) !==
      comparison.comparison.tests.length + 1
    )
      throw new Error("UI test count differs from report");
    await page.locator(".execution").last().scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "../../docs/examples/workbench-results.png",
      fullPage: false,
    });
  }
  const finalExport = await (
    await page.request.get(`${origin}/api/analyses/${runId}/export?format=json`)
  ).json();
  if (finalExport.revision !== run.report.revision)
    throw new Error("Final export revision differs");
  const result = {
    run_id: runId,
    url: page.url(),
    source_path: await page.locator(".source-meta strong").innerText(),
    export_filename: file.suggestedFilename(),
    revision: run.report.revision,
    execution_status:
      comparison?.comparison?.status || run.report.executions.at(-1).status,
    test_attempts: run.test_attempts,
    comparison: comparison?.comparison,
    final_export_revision: finalExport.revision,
    console_errors: errors,
  };
  await writeFile(
    "../../docs/examples/workbench-verification.json",
    JSON.stringify(result, null, 2) + "\n",
  );
  console.log(JSON.stringify(result, null, 2));
  if (errors.length) throw new Error("Browser errors");
} finally {
  await browser.close();
}
