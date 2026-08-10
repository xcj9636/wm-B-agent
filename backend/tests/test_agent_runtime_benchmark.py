import json

import pytest

from scripts.benchmark_agent_runtime import (
    compare_benchmarks,
    run_benchmarks,
    summarize_samples,
    write_report,
)


def test_benchmark_summary_reports_stable_percentiles():
    summary = summarize_samples([1.0, 2.0, 3.0, 4.0])

    assert summary == {
        "samples": 4,
        "min_ms": 1.0,
        "p50_ms": 2.0,
        "p95_ms": 4.0,
        "p99_ms": 4.0,
        "max_ms": 4.0,
    }


@pytest.mark.asyncio
async def test_all_benchmark_scenarios_emit_machine_comparable_metrics(tmp_path):
    report = await run_benchmarks("all", samples=3)
    output = tmp_path / "candidate.json"

    write_report(output, report)
    persisted = json.loads(output.read_text())

    assert persisted["schema_version"] == 1
    assert persisted["workload"] == "synthetic-fixed-v1"
    assert set(persisted["scenarios"]) == {
        "fast_chat",
        "rag_cache",
        "concurrent_chat",
    }
    assert persisted["scenarios"]["fast_chat"]["e2e_ms"]["samples"] == 3
    assert persisted["scenarios"]["fast_chat"]["ttft_ms"]["p95_ms"] >= 0
    assert persisted["scenarios"]["rag_cache"]["warm_ms"]["samples"] == 3
    assert persisted["scenarios"]["concurrent_chat"]["e2e_ms"]["samples"] == 3


def test_benchmark_comparison_fails_p95_regressions_beyond_budget():
    baseline = {
        "scenarios": {
            "fast_chat": {"e2e_ms": {"p95_ms": 100.0}},
        }
    }
    candidate = {
        "scenarios": {
            "fast_chat": {"e2e_ms": {"p95_ms": 121.0}},
        }
    }

    regressions = compare_benchmarks(
        baseline,
        candidate,
        max_regression_percent=20.0,
    )

    assert regressions == [
        {
            "scenario": "fast_chat",
            "metric": "e2e_ms",
            "baseline_p95_ms": 100.0,
            "candidate_p95_ms": 121.0,
            "regression_percent": 21.0,
        }
    ]
