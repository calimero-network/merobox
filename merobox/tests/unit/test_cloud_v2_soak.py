"""The soak (fuzzy_test) metrics: percentiles, the JSON report and its table.

None of these start a cluster: the fuzzy loop runs against a Runner whose
steps are stubbed.
"""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from merobox.cloud_v2.soak import (
    SoakMetrics,
    append_report,
    latency,
    load_runs,
    markdown,
    percentile,
    step_kind,
)
from merobox.cloud_v2.workflow import Runner, WorkflowError
from merobox.commands.cloud import cloud


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_percentiles_are_nearest_rank():
    values = [float(v) for v in range(100, 0, -1)]  # unsorted on purpose
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile([7.0], 99) == 7
    assert percentile([], 50) == 0


def test_latency_is_in_milliseconds():
    assert latency([0.001, 0.002, 0.003, 0.010]) == {
        "p50": 2.0,
        "p95": 10.0,
        "p99": 10.0,
        "mean": 4.0,
        "max": 10.0,
    }
    assert latency([]) == {"p50": 0, "p95": 0, "p99": 0, "mean": 0.0, "max": 0.0}


def test_step_kind_names_the_method_or_op():
    assert step_kind({"type": "call", "method": "set"}) == "call set"
    assert step_kind({"type": "ops", "op": "status"}) == "ops status"
    assert step_kind({"type": "whoami"}) == "whoami"


def test_a_summary_counts_rates_and_latency_per_operation():
    clock = Clock()
    m = SoakMetrics(clock=clock)
    for ms in (10, 20, 30):
        m.call("write", "call set", ms / 1000, True)
        m.operation("write", ms / 1000, True)
    m.call("read", "call get", 0.005, True)
    m.call("read", "call get", 0.050, False)
    m.operation("read", 0.055, False)
    clock.now += 2.5
    m.stop()
    clock.now += 100  # stopped: later time does not count
    s = m.summary()
    assert s["seconds"] == 2.5
    assert s["operations_total"] == 4
    assert s["operations_passed"] == 3
    assert s["pass_rate"] == 75.0
    assert s["calls"]["count"] == 5
    assert s["calls"]["failed"] == 1
    assert s["calls"]["per_s"] == 2.0
    write = s["operations"]["write"]
    assert (write["attempts"], write["passed"], write["failed"]) == (3, 3, 0)
    assert write["ops_per_s"] == 1.2
    assert write["calls"]["per_s"] == 1.2
    assert write["calls"]["latency_ms"]["p50"] == 20.0
    assert write["latency_ms"]["p99"] == 30.0
    read = s["operations"]["read"]
    assert read["failed"] == 1
    # A failed operation's partial time is not an operation latency.
    assert read["latency_ms"]["p50"] == 0
    assert read["calls"]["latency_ms"]["p99"] == 50.0
    assert set(s["steps"]) == {"call get", "call set"}
    assert s["steps"]["call get"]["failed"] == 1


def test_reports_append_and_survive_a_broken_file(tmp_path):
    path = tmp_path / "sub" / "soak.json"
    append_report(path, {"workflow": "a"})
    append_report(path, {"workflow": "b"})
    assert [r["workflow"] for r in load_runs([path])] == ["a", "b"]
    assert json.loads(path.read_text())["version"] == 1
    path.write_text("not json")
    append_report(path, {"workflow": "c"})
    assert [r["workflow"] for r in load_runs([path])] == ["c"]


def _run(name="KV | soak"):
    clock = Clock()
    m = SoakMetrics(clock=clock)
    m.call("set_and_verify", "call set", 0.004, True)
    m.call("set_and_verify", "call get", 0.002, True)
    m.operation("set_and_verify", 0.006, True)
    clock.now += 1
    m.stop()
    return {"workflow": name, **m.summary()}


def test_the_table_has_a_row_per_operation_and_a_total():
    table = markdown([_run()], title="Nightly soak")
    lines = table.splitlines()
    assert lines[0] == "### Nightly soak"
    rows = [line for line in lines if line.startswith("| KV")]
    assert rows == [
        r"| KV \| soak | set_and_verify | 1 | 100.0 | 1.00 | 2 | 2.00 | 2.0 | 4.0 | 4.0 |",
        r"| KV \| soak | **all** (1s) | 1 | 100.0 | 1.00 | 2 | 2.00 | 2.0 | 4.0 | 4.0 |",
    ]
    assert "No fuzzy_test step ran." in markdown([])


def test_soak_report_prints_the_table(tmp_path):
    path = tmp_path / "soak.json"
    append_report(path, _run("kv"))
    out = CliRunner().invoke(cloud, ["soak-report", str(path), "--title", "T"])
    assert out.exit_code == 0, out.output
    assert out.output.startswith("### T\n")
    assert "| kv | set_and_verify |" in out.output


class _StubRunner(Runner):
    """A Runner whose steps answer without a cluster."""

    def __init__(self, wf):
        self.wf = wf
        self.cluster = SimpleNamespace(size=3)
        self.variables = {"env": {}}
        self.sent = []

    def _step(self, step, label):
        self.sent.append((label, step["method"]))
        if step["method"] == "boom":
            raise WorkflowError(f"{label}: refused")
        return ""


def test_a_fuzzy_run_writes_its_metrics(tmp_path, monkeypatch):
    report = tmp_path / "soak.json"
    monkeypatch.setenv("MEROBOX_FUZZY_SECONDS", "0.2")
    monkeypatch.setenv("MEROBOX_SOAK_REPORT", str(report))
    wf = {"name": "stub soak", "_path": "wf.yml", "accounts": ["alice"]}
    raw = {
        "type": "fuzzy_test",
        "success_threshold": 0,
        "operations": [
            {
                "name": "ok",
                "steps": [
                    {"type": "call", "app": "a", "method": "set", "account": "x"},
                    {"type": "wait", "seconds": 0},
                    {"type": "call", "app": "a", "method": "get", "account": "x"},
                ],
            },
            {
                "name": "bad",
                "steps": [{"type": "call", "app": "a", "method": "boom"}],
            },
        ],
    }
    runner = _StubRunner(wf)
    runner._fuzzy(raw, "soak")
    (run,) = load_runs([report])
    assert run["workflow"] == "stub soak"
    assert run["file"] == "wf.yml"
    assert run["step"] == "soak"
    assert run["nodes"] == 3
    assert run["duration_requested"] == 0.2
    ops = run["operations"]
    assert set(ops) == {"ok", "bad"}
    sent = dict.fromkeys(ops, 0)
    for label, _method in runner.sent:
        sent[label] += 1
    assert ops["ok"]["calls"]["count"] == sent["ok"] == 2 * ops["ok"]["attempts"]
    assert ops["bad"]["failed"] == ops["bad"]["attempts"] == sent["bad"]
    assert ops["bad"]["calls"]["failed"] == sent["bad"]
    assert run["calls"]["count"] == len(runner.sent)
    assert set(run["steps"]) <= {"call set", "call get", "call boom"}
    assert run["operations_total"] == ops["ok"]["attempts"] + ops["bad"]["attempts"]


def test_a_fuzzy_run_without_the_variable_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MEROBOX_FUZZY_SECONDS", "0.05")
    monkeypatch.delenv("MEROBOX_SOAK_REPORT", raising=False)
    monkeypatch.chdir(tmp_path)
    raw = {
        "type": "fuzzy_test",
        "operations": [{"name": "ok", "steps": [{"type": "call", "method": "set"}]}],
    }
    _StubRunner({"accounts": ["alice"]})._fuzzy(raw, "soak")
    assert list(tmp_path.iterdir()) == []


def test_the_threshold_still_fails_a_run(monkeypatch, tmp_path):
    monkeypatch.setenv("MEROBOX_FUZZY_SECONDS", "0.05")
    monkeypatch.setenv("MEROBOX_SOAK_REPORT", str(tmp_path / "soak.json"))
    raw = {
        "type": "fuzzy_test",
        "operations": [{"name": "bad", "steps": [{"type": "call", "method": "boom"}]}],
    }
    with pytest.raises(WorkflowError, match="under 95.0%"):
        _StubRunner({"accounts": ["alice"]})._fuzzy(raw, "soak")
    # The report is written before the threshold fails the step.
    assert load_runs([tmp_path / "soak.json"])[0]["pass_rate"] == 0
