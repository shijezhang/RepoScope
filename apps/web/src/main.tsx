import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import {
  Activity,
  ArrowRight,
  ChevronRight,
  FileCode2,
  GitBranch,
  Plus,
  RefreshCw,
  Search,
  X,
  Download,
} from "lucide-react";
import {
  api,
  terminal,
  statusLabel,
  type Evidence,
  type Impact,
  type Repository,
  type Run,
} from "./types";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { ExecutionPanel } from "./ExecutionPanel";

function Badge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{statusLabel(status)}</span>;
}
function App() {
  const [repositories, setRepositories] = useState<Repository[]>([]),
    [runs, setRuns] = useState<Run[]>([]),
    [run, setRun] = useState<Run | null>(null);
  const [runId, setRunId] = useState(
      new URLSearchParams(location.search).get("run") || "",
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [loaded, setLoaded] = useState(false),
    [creating, setCreating] = useState(false);
  const [repoId, setRepoId] = useState(""),
    [repoPath, setRepoPath] = useState(""),
    [base, setBase] = useState("HEAD~1"),
    [head, setHead] = useState("HEAD"),
    [mode, setMode] = useState<"direct" | "pr">("direct"),
    [question, setQuestion] = useState("");
  const [events, setEvents] = useState<
      { event_id: string; state: string; message: string }[]
    >([]),
    [connection, setConnection] = useState(""),
    [selected, setSelected] = useState<Impact | null>(null),
    [tab, setTab] = useState("source"),
    [evidence, setEvidence] = useState<Evidence | null>(null),
    [evidenceError, setEvidenceError] = useState(""),
    [evidenceLoading, setEvidenceLoading] = useState(false),
    [filter, setFilter] = useState("");
  const report = run?.report;
  const [testJob, setTestJob] = useState<Run | null>(null);
  const [showAllClaims, setShowAllClaims] = useState(false);
  const [agent, setAgent] = useState(false);
  const [allowTests, setAllowTests] = useState(false);
  const attempt = Math.max(
    0,
    ...(run?.test_attempts || []).map((item) => item.attempt),
  );
  const latestAttempt = run?.test_attempts?.find(
    (item) => item.attempt === attempt,
  );
  const testRunning = !!latestAttempt && !terminal(latestAttempt.status);
  useEffect(() => {
    if (latestAttempt)
      setTestJob({
        run_id: latestAttempt.execution_id,
        status: latestAttempt.status,
      });
  }, [latestAttempt?.execution_id, latestAttempt?.status]);
  useEffect(() => {
    if (!testJob || terminal(testJob.status)) return;
    const timer = setInterval(() => {
      api<Run>(`/jobs/${testJob.run_id}`)
        .then(setTestJob)
        .catch((e) => setError(String(e)));
    }, 1500);
    return () => clearInterval(timer);
  }, [testJob]);
  async function refresh() {
    try {
      const [repos, history] = await Promise.all([
        api<Repository[]>("/repositories"),
        api<Run[]>("/analyses"),
      ]);
      setRepositories(repos);
      setRuns(history);
      setRepoId((id) => id || repos[0]?.repo_id || "");
      setLoaded(true);
    } catch (e) {
      setError(String(e));
      setLoaded(true);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let finished = false;
    setRun(null);
    setTestJob(null);
    setSelected(null);
    setEvidence(null);
    setEvents([]);
    setError("");
    setConnection("连接进度中");
    const reload = () =>
      api<Run>(`/analyses/${runId}`)
        .then((r) => {
          if (alive) {
            setRun(r);
            if (terminal(r.status)) {
              setConnection("");
              finished = true;
            }
          }
        })
        .catch((e) => {
          if (alive) setError(String(e));
        });
    const source = new EventSource(`/api/analyses/${runId}/events`);
    source.onopen = () => setConnection("实时进度已连接");
    source.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        setEvents((old) =>
          old.some((item) => item.event_id === event.event_id)
            ? old
            : [...old, event].slice(-100),
        );
        void reload();
      } catch {
        setConnection("进度格式无效，定时刷新状态");
      }
    };
    source.onerror = () => {
      if (finished) {
        source.close();
        setConnection("");
      } else setConnection("进度连接中断，自动重连中；状态持续刷新");
    };
    void reload();
    const poll = setInterval(reload, 2500);
    return () => {
      alive = false;
      source.close();
      clearInterval(poll);
    };
  }, [runId]);
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setEvidence(null);
    setEvidenceError("");
    setEvidenceLoading(true);
    api<Evidence>(`/evidence/${selected.evidence_id}`)
      .then((value) => {
        if (alive) setEvidence(value);
      })
      .catch((e) => {
        if (alive) setEvidenceError(String(e));
      })
      .finally(() => {
        if (alive) setEvidenceLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);
  function open(id: string) {
    setRunId(id);
    setCreating(false);
    history.replaceState(null, "", `?run=${encodeURIComponent(id)}`);
  }
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function create() {
    await act(async () => {
      let id = repoId;
      if (repoPath.trim()) {
        const repo = await api<Repository>("/repositories", {
          path: repoPath.trim(),
        });
        id = repo.repo_id;
      }
      if (!id) throw new Error("请选择仓库或填写本地 Git 仓库路径");
      const result = await api<{ run_id: string }>("/analyses", {
        repo_id: id,
        base,
        head,
        mode,
        question: question || undefined,
        agent,
        allow_tests: allowTests,
      });
      open(result.run_id);
      await refresh();
    });
  }
  function newAnalysis() {
    setCreating(true);
    setError("");
  }
  async function showEvidence(id: string) {
    setEvidence(null);
    setEvidenceError("");
    setEvidenceLoading(true);
    try {
      setEvidence(await api<Evidence>(`/evidence/${id}`));
      setTab("source");
    } catch (e) {
      setEvidenceError(String(e));
    } finally {
      setEvidenceLoading(false);
    }
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="RepoScope 首页">
          <span className="brand-icon">
            <GitBranch size={21} />
          </span>
          RepoScope<span className="version">V1</span>
        </a>
        <div className="workspace-label">代码变更工作台</div>
        <button className="primary new" onClick={newAnalysis}>
          <Plus size={17} />
          新建分析
        </button>
        <div className="nav-title">
          已登记仓库 <span>{repositories.length}</span>
        </div>
        {repositories.map((repo) => (
          <button
            className="repo"
            key={repo.repo_id}
            onClick={() => {
              setRepoId(repo.repo_id);
              setRepoPath("");
              newAnalysis();
            }}
            title={repo.path}
          >
            <GitBranch size={16} />
            <span>{repo.name || repo.path.split("/").pop()}</span>
            <ChevronRight size={14} />
          </button>
        ))}
        {loaded && !repositories.length && (
          <p className="muted side-empty">
            添加本地 Git 仓库，开始比较两个提交。
          </p>
        )}
        <div className="nav-title">
          分析历史{" "}
          <button
            className="icon-button"
            onClick={() => void refresh()}
            aria-label="刷新历史"
          >
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="history">
          {runs.map((item) => (
            <button
              key={item.run_id}
              className={`history-item ${runId === item.run_id ? "active" : ""}`}
              onClick={() => open(item.run_id)}
            >
              <span>
                <Activity size={14} />
                {item.run_id.slice(0, 12)}
              </span>
              <small>
                {statusLabel(item.status)}
                {item.created_at
                  ? ` · ${new Date(item.created_at).toLocaleDateString()}`
                  : ""}
              </small>
            </button>
          ))}
          {loaded && !runs.length && (
            <p className="muted side-empty">暂无分析记录</p>
          )}
        </div>
        <div className="sidebar-foot">
          <span className="dot" />
          固定快照 · 可核查证据
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            工作空间 <ChevronRight size={14} />{" "}
            {creating ? "新建分析" : runId ? "分析报告" : "概览"}
          </span>
          <span className="muted">Python / pytest</span>
        </header>
        <div className="content">
          {error && (
            <div role="alert" className="error">
              {error}
              <button onClick={() => setError("")} aria-label="关闭错误">
                <X size={16} />
              </button>
            </div>
          )}
          {creating ? (
            <section className="panel create-panel">
              <div className="eyebrow">NEW ANALYSIS</div>
              <h1>从一组变更开始</h1>
              <p className="muted">
                比较固定 Git 提交，追踪潜在影响，并生成可执行的回归测试计划。
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void create();
                }}
              >
                <label>
                  已登记仓库
                  <select
                    value={repoId}
                    onChange={(e) => {
                      setRepoId(e.target.value);
                      setRepoPath("");
                    }}
                  >
                    <option value="">选择仓库</option>
                    {repositories.map((r) => (
                      <option value={r.repo_id} key={r.repo_id}>
                        {r.name || r.path}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  或登记本地 Git 仓库
                  <input
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder="/absolute/path/to/repository"
                  />
                </label>
                <div className="form-grid">
                  <label>
                    Base
                    <input
                      required
                      value={base}
                      onChange={(e) => setBase(e.target.value)}
                    />
                  </label>
                  <ArrowRight size={18} />
                  <label>
                    Head
                    <input
                      required
                      value={head}
                      onChange={(e) => setHead(e.target.value)}
                    />
                  </label>
                </div>
                <label>
                  比较方式
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value as "direct" | "pr")}
                  >
                    <option value="direct">直接比较 · base → head</option>
                    <option value="pr">PR 比较 · merge-base → head</option>
                  </select>
                </label>
                <label>
                  关注的问题（可选）
                  <textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="例如：哪些调用方可能受影响，需要验证哪些边界？"
                  />
                </label>
                <label className="agent-toggle">
                  <input
                    type="checkbox"
                    checked={agent}
                    onChange={(e) => setAgent(e.target.checked)}
                  />
                  <span>启用模型补查（可选）</span>
                </label>
                <p className="muted">
                  默认仅进行结构分析。模型补查根据问题查找证据；服务端未配置模型时会保留基础报告并明确降级。
                </p>
                <label className="agent-toggle">
                  <input
                    type="checkbox"
                    checked={allowTests}
                    onChange={(e) => setAllowTests(e.target.checked)}
                  />
                  <span>分析时执行登记测试（Docker）</span>
                </label>
                <p className="muted">
                  使用已登记的执行配置，最多进行一次 Base / Head
                  对照，结果可能仍为部分验证。关闭模型补查时由固定流程验证；开启后由模型在预算内选择是否验证。
                </p>
                <div className="form-actions">
                  <button type="button" onClick={() => setCreating(false)}>
                    返回
                  </button>
                  <button className="primary" disabled={busy}>
                    {busy ? "正在创建…" : "开始分析"}
                    <ArrowRight size={16} />
                  </button>
                </div>
              </form>
            </section>
          ) : !runId ? (
            <section className="welcome">
              <div className="eyebrow">CHANGE WITH CONTEXT</div>
              <h1>看清变更，验证影响。</h1>
              <p>
                从代码差异到调用路径，再到回归测试。
                <br />
                每一个判断，都能回到对应版本的证据。
              </p>
              <button className="primary" onClick={newAnalysis}>
                <Plus size={17} />
                创建第一次分析
              </button>
              <div className="welcome-steps">
                {[
                  "比较两个代码快照",
                  "追踪变更与潜在影响",
                  "核对证据并执行测试",
                ].map((s, i) => (
                  <div key={s}>
                    <span>0{i + 1}</span>
                    {s}
                  </div>
                ))}
              </div>
            </section>
          ) : !run ? (
            <div className="empty">正在加载分析报告…</div>
          ) : (
            <>
              <div className="report-heading">
                <div>
                  <div className="eyebrow">
                    IMPACT ANALYSIS / {run.run_id.slice(0, 12)}
                  </div>
                  <h1>
                    变更影响报告 <Badge status={run.status} />
                    {report && (
                      <Badge
                        status={
                          typeof report.completeness === "string"
                            ? report.completeness
                            : "unknown"
                        }
                      />
                    )}
                  </h1>
                  <p className="muted">
                    {report
                      ? `${report.base.commit_sha.slice(0, 10)} → ${report.head.commit_sha.slice(0, 10)} · 报告版本 ${report.revision}`
                      : "正在冻结快照并分析代码关系"}
                  </p>
                </div>
                <div className="actions">
                  {!terminal(run.status) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await api(`/analyses/${runId}/cancel`, {});
                          setRun(await api<Run>(`/analyses/${runId}`));
                        })
                      }
                    >
                      取消分析
                    </button>
                  )}
                  {report && (
                    <details className="export">
                      <summary>
                        <Download size={15} />
                        导出报告
                      </summary>
                      <div>
                        {["json", "markdown", "html"].map((f) => (
                          <a
                            key={f}
                            href={`/api/analyses/${runId}/export?format=${f}`}
                            download
                          >
                            {f.toUpperCase()}
                          </a>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              </div>
              {run.payload?.agent &&
                report?.limitations.some((item) =>
                  /model_unavailable|REPOSCOPE_LLM_MODEL|REPOSCOPE_LLM_API_KEY/.test(
                    item,
                  ),
                ) && (
                  <div className="progress" role="status">
                    模型补查未配置，本报告已降级为基础结构分析；未产生模型补查结论。
                  </div>
                )}
              {run.error != null && (
                <div className="error" role="alert">
                  {typeof run.error === "string"
                    ? run.error
                    : JSON.stringify(run.error)}
                </div>
              )}
              {!terminal(run.status) && (
                <div className="progress">
                  <span className="dot" />
                  {events.at(-1)?.message || statusLabel(run.status)}
                  <small>{connection}</small>
                </div>
              )}
              {report ? (
                <>
                  <div className="metrics">
                    <div>
                      <span>变更文件</span>
                      <strong>{report.changes.length}</strong>
                    </div>
                    <div>
                      <span>潜在影响</span>
                      <strong>{report.impacts.length}</strong>
                    </div>
                    <div>
                      <span>建议测试</span>
                      <strong>{report.test_plan.nodeids.length}</strong>
                    </div>
                    <div>
                      <span>执行记录</span>
                      <strong>{report.executions.length}</strong>
                    </div>
                  </div>
                  <section className="panel conclusions">
                    <h2>结论概览</h2>
                    {report.claims.length ? (
                      report.claims
                        .slice(0, showAllClaims ? undefined : 4)
                        .map((claim, i) => (
                          <div className="claim" key={i}>
                            <Badge status={claim.status} />
                            <p>{claim.text}</p>
                            {claim.evidence_ids.map((id, j) => (
                              <button
                                key={id}
                                className="evidence-link"
                                onClick={() => void showEvidence(id)}
                              >
                                证据 {j + 1}
                              </button>
                            ))}
                          </div>
                        ))
                    ) : (
                      <p className="muted">
                        当前报告没有生成结论，请查看影响列表和限制。
                      </p>
                    )}
                    {report.claims.length > 4 && (
                      <button
                        className="evidence-link"
                        onClick={() => setShowAllClaims(!showAllClaims)}
                      >
                        {showAllClaims
                          ? "收起结论"
                          : `展开全部 ${report.claims.length} 条结论`}
                      </button>
                    )}
                    {report.limitations.length > 0 && (
                      <details className="limitations" open>
                        <summary>
                          分析边界与未验证项 · {report.limitations.length}
                        </summary>
                        <ul>
                          {report.limitations.map((l, i) => (
                            <li key={i}>{l}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </section>
                  <div className="analysis-grid">
                    <section className="panel impact-panel">
                      <h2>
                        影响列表{" "}
                        <span className="count">{report.impacts.length}</span>
                      </h2>
                      <label className="search">
                        <Search size={15} />
                        <input
                          aria-label="筛选影响"
                          value={filter}
                          onChange={(e) => setFilter(e.target.value)}
                          placeholder="搜索符号或文件…"
                        />
                      </label>
                      <div className="impact-list">
                        {report.impacts
                          .filter((x) =>
                            `${x.symbol.qualname} ${x.symbol.path}`
                              .toLowerCase()
                              .includes(filter.toLowerCase()),
                          )
                          .map((item) => (
                            <button
                              className={`impact ${selected?.symbol.symbol_id === item.symbol.symbol_id ? "selected" : ""}`}
                              key={item.symbol.symbol_id}
                              onClick={() => setSelected(item)}
                            >
                              <strong>
                                <FileCode2 size={15} />
                                {item.symbol.qualname || "<module>"}
                              </strong>
                              <small>{item.symbol.path}</small>
                              <span>
                                {item.distance === 0
                                  ? "直接变更"
                                  : `${item.distance} 层关联`}{" "}
                                ·{" "}
                                {item.symbol.snapshot_id ===
                                report.base.snapshot_id
                                  ? "base"
                                  : "head"}
                              </span>
                            </button>
                          ))}
                        {!report.impacts.length && (
                          <p className="empty">
                            未发现静态影响候选。请结合分析边界判断，不代表没有风险。
                          </p>
                        )}
                      </div>
                    </section>
                    <section className="panel detail-panel">
                      <div className="tabs" role="tablist">
                        {[
                          ["source", "源码证据"],
                          ["path", "依赖路径"],
                          ["diff", "变更文件"],
                        ].map(([id, label]) => (
                          <button
                            key={id}
                            role="tab"
                            aria-selected={tab === id}
                            onClick={() => setTab(id)}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                      {tab === "source" ? (
                        evidenceLoading ? (
                          <p className="empty">正在读取固定快照源码…</p>
                        ) : evidenceError ? (
                          <p className="error">{evidenceError}</p>
                        ) : evidence ? (
                          <>
                            <div className="source-meta">
                              <Badge
                                status={
                                  evidence.snapshot_id ===
                                  report.base.snapshot_id
                                    ? "base"
                                    : "head"
                                }
                              />
                              <strong>
                                {evidence.path}:{evidence.start}–{evidence.end}
                              </strong>
                              <small title={evidence.snapshot_id}>
                                快照 {evidence.snapshot_id.slice(0, 16)}
                              </small>
                            </div>
                            <pre className="source">
                              {evidence.source.split("\n").map((line, i) => (
                                <div key={i}>
                                  <span>{evidence.start + i}</span>
                                  <code>{line || " "}</code>
                                </div>
                              ))}
                            </pre>
                          </>
                        ) : (
                          <div className="empty">
                            <FileCode2 size={28} />
                            <p>
                              选择左侧影响条目或结论中的证据，核对对应快照的源码。
                            </p>
                          </div>
                        )
                      ) : tab === "path" ? (
                        selected ? (
                          <PathGraph
                            impact={selected}
                            impacts={report.impacts}
                          />
                        ) : (
                          <p className="empty">
                            选择一个影响条目查看关联路径。
                          </p>
                        )
                      ) : (
                        <div className="files">
                          {report.changes.map((file, i) => (
                            <details key={i}>
                              <summary>
                                <Badge status={file.status} />
                                <span>
                                  {file.old_path ? `${file.old_path} → ` : ""}
                                  {file.path}
                                </span>
                              </summary>
                              <pre>
                                {file.hunks.length
                                  ? JSON.stringify(file.hunks, null, 2)
                                  : "无文本差异块（可能为二进制文件或纯重命名）"}
                              </pre>
                            </details>
                          ))}
                        </div>
                      )}
                    </section>
                  </div>
                  <section className="panel tests">
                    <div className="section-heading">
                      <div>
                        <h2>回归测试计划</h2>
                        <p className="muted">
                          建议运行与执行结果分别记录；通过所选测试不代表所有行为都已验证。
                        </p>
                      </div>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          (!report.test_plan.nodeids.length &&
                            report.test_plan.status !== "collection_required" &&
                            attempt === 0) ||
                          !terminal(run.status) ||
                          testRunning ||
                          attempt >= 3
                        }
                        onClick={() =>
                          void act(async () => {
                            const submitted = await api<{
                              execution_id: string;
                              status: string;
                            }>(`/analyses/${runId}/test-runs`, {
                              plan_id: report.test_plan.plan_id,
                              attempt: attempt + 1,
                              ...(attempt > 0
                                ? {
                                    reason:
                                      "Retry requested after environment preparation",
                                  }
                                : {}),
                            });
                            setTestJob({
                              run_id: submitted.execution_id,
                              status: submitted.status,
                            });
                            setRun(await api<Run>(`/analyses/${runId}`));
                          })
                        }
                      >
                        {busy
                          ? "正在提交…"
                          : attempt >= 3
                            ? "已达 3 次验证上限"
                            : attempt > 0
                              ? "重试验证"
                              : "运行建议测试"}
                      </button>
                    </div>
                    <Badge status={report.test_plan.status} />
                    {testJob && (
                      <div className="progress">
                        <span>测试任务</span>
                        <Badge status={testJob.status} />
                        {!terminal(testJob.status) && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                await api(
                                  `/analyses/${testJob.run_id}/cancel`,
                                  {},
                                );
                                setTestJob(
                                  await api<Run>(`/jobs/${testJob.run_id}`),
                                );
                              })
                            }
                          >
                            取消测试
                          </button>
                        )}
                      </div>
                    )}
                    <div className="test-list">
                      {report.test_plan.nodeids.map((id) => (
                        <div key={id}>
                          <code>{id}</code>
                          <span className="muted">已选择</span>
                        </div>
                      ))}
                    </div>
                    {!report.test_plan.nodeids.length && (
                      <p className="empty">
                        {report.test_plan.status === "collection_required"
                          ? "测试目录尚待收集。运行计划将先在隔离环境收集测试，再按保守范围执行。"
                          : "没有可执行的测试候选，请查看选择理由与回退范围。"}
                      </p>
                    )}
                    <details>
                      <summary>选择理由与回退范围</summary>
                      <pre>
                        {JSON.stringify(report.test_plan.reasons, null, 2)}
                      </pre>
                    </details>
                    {report.executions.map((execution, i) => (
                      <ExecutionPanel
                        key={i}
                        value={execution}
                        index={i}
                        expanded={i === report.executions.length - 1}
                      />
                    ))}
                  </section>
                  <section className="panel followup">
                    <h2>继续分析</h2>
                    <p className="muted">
                      沿用本报告的固定提交创建新分析，保留原报告与证据。
                    </p>
                    <button
                      onClick={() => {
                        setRepoId(
                          run.payload?.repo_id || run.repo_id || repoId,
                        );
                        setBase(report.base.commit_sha);
                        setHead(report.head.commit_sha);
                        setMode("direct");
                        setRepoPath("");
                        setQuestion("");
                        newAnalysis();
                      }}
                    >
                      以相同快照提出问题 <ArrowRight size={15} />
                    </button>
                  </section>
                </>
              ) : terminal(run.status) ? (
                <div className="panel empty">
                  本次分析没有报告。可以查看运行过程，或新建分析重试。
                  <button onClick={newAnalysis}>新建分析</button>
                </div>
              ) : (
                <div className="panel empty">
                  分析进行中，报告准备完成后会自动显示。
                </div>
              )}
              <details className="panel trace">
                <summary>
                  分析过程与工具详情{" "}
                  <span className="muted">{events.length} 条进度事件</span>
                </summary>
                {events.length ? (
                  events.map((event, i) => (
                    <div key={`${event.event_id}-${i}`}>
                      <Badge status={event.state} />
                      <span>{event.message}</span>
                    </div>
                  ))
                ) : (
                  <p className="muted">暂无进度事件</p>
                )}
                {report && (
                  <pre>
                    报告完整度：{JSON.stringify(report.completeness, null, 2)}
                  </pre>
                )}
              </details>
            </>
          )}
          <footer>
            RepoScope <span>代码变更影响分析与回归验证</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
function PathGraph({ impact, impacts }: { impact: Impact; impacts: Impact[] }) {
  const symbols = new Map(
    impacts.map((item) => [item.symbol.symbol_id, item.symbol]),
  );
  const label = (id: string) =>
    symbols.get(id)?.qualname || (symbols.has(id) ? "<module>" : id);
  const relations = impact.path.slice(0, 12);
  const ids = [
    ...new Set(relations.flatMap((r) => [r.source_id, r.target_id])),
  ];
  if (!ids.length)
    return <div className="empty">此条目为直接变更，暂无依赖路径。</div>;
  return (
    <>
      <div className="graph">
        <ReactFlow
          nodes={ids.map((id, i) => ({
            id,
            position: { x: (i % 3) * 250, y: Math.floor(i / 3) * 140 },
            data: { label: label(id) },
            style: { width: 210, fontSize: 12 },
          }))}
          edges={relations.map((r, i) => ({
            id: String(i),
            source: r.source_id,
            target: r.target_id,
            label: r.relation_type,
            markerEnd: { type: MarkerType.ArrowClosed },
            style: {
              strokeDasharray:
                r.resolution && r.resolution !== "resolved" ? "5 4" : undefined,
            },
          }))}
          fitView
          nodesDraggable={false}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <ol className="path-text">
        {relations.map((r, i) => (
          <li key={i}>
            <code title={r.source_id}>{label(r.source_id)}</code>
            <strong>
              {r.relation_type} · {r.resolution || "静态关系"}
            </strong>
            <code title={r.target_id}>{label(r.target_id)}</code>
            {(r.path || symbols.get(r.source_id)?.path) && (
              <small>
                {r.path || symbols.get(r.source_id)?.path}:{r.line}
              </small>
            )}
          </li>
        ))}
      </ol>
      {impact.path.length > 12 && (
        <p className="muted">当前展示前 12 条关系；完整路径见 JSON 导出。</p>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
