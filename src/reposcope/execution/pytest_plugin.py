"""Runs inside the isolated target container; never imported by the API."""

import json
from pathlib import Path

import coverage
import pytest

NODEIDS = []
RESULTS = []
ERRORS = []
COV = None


def pytest_sessionstart(session):
    global COV
    if not session.config.option.collectonly:
        COV = coverage.Coverage(data_file="/output/.coverage", branch=True, config_file=False, source=["/work"])
        COV.start()


def pytest_collection_finish(session):
    NODEIDS.extend(item.nodeid for item in session.items)


def pytest_collectreport(report):
    if report.failed:
        ERRORS.append(str(report.longrepr))


def pytest_runtest_logreport(report):
    status = report.outcome
    if hasattr(report, "wasxfail"):
        status = "xfail" if report.skipped else "xpass"
    elif report.failed and str(report.longrepr).startswith("[XPASS(strict)]"):
        status = "xpass"
    elif report.failed and report.when != "call":
        status = "error"
    RESULTS.append(
        {
            "nodeid": report.nodeid,
            "phase": report.when,
            "status": status,
            "duration": report.duration,
            "message": str(report.longrepr)[:20000] if report.longrepr else "",
        }
    )


def phase_context(item, phase):
    if COV:
        COV.switch_context(json.dumps([item.nodeid, phase]))


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    phase_context(item, "setup")
    yield
    if COV:
        COV.switch_context("")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    phase_context(item, "call")
    yield
    if COV:
        COV.switch_context("")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    phase_context(item, "teardown")
    yield
    if COV:
        COV.switch_context("")


def pytest_sessionfinish(session, exitstatus):
    contexts = []
    if COV:
        COV.stop()
        COV.save()
        data = COV.get_data()
        for filename in sorted(data.measured_files()):
            try:
                relative = str(Path(filename).relative_to("/work"))
            except ValueError:
                continue
            grouped = {}
            for line, labels in data.contexts_by_lineno(filename).items():
                for label in labels:
                    grouped.setdefault(label, []).append(line)
            for label, lines in grouped.items():
                nodeid, phase = json.loads(label) if label else (None, "unattributed")
                contexts.append({"path": relative, "nodeid": nodeid, "phase": phase, "lines": lines})
    Path("/output/result.json").write_text(
        json.dumps(
            {
                "nodeids": NODEIDS,
                "phases": RESULTS,
                "collection_errors": ERRORS,
                "exit_code": int(exitstatus),
                "contexts": contexts,
            }
        )
    )
