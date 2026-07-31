import pytest

from train.benchmark import distribution, percentile, summarize_records


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([1, 2, 3, 4], 0.95) == pytest.approx(3.85)
    assert percentile([], 0.5) is None


def test_summarize_records_excludes_errors_from_latency() -> None:
    records = [
        {
            "error": None,
            "input_tokens": 10,
            "output_tokens": 2,
            "tokenization_ms": 1,
            "ttft_ms": 3,
            "inter_token_ms": [2],
            "generation_ms": 5,
            "end_to_end_ms": 6,
        },
        {
            "error": "OOM",
            "input_tokens": 0,
            "output_tokens": 0,
            "tokenization_ms": 0,
            "ttft_ms": None,
            "inter_token_ms": [],
            "generation_ms": 0,
            "end_to_end_ms": 0,
        },
    ]

    summary = summarize_records(records)

    assert summary["successful_requests"] == 1
    assert summary["error_rate"] == 0.5
    assert summary["output_tokens_per_second"] == pytest.approx(400)
    assert summary["generation_ms"]["median"] == 5


def test_distribution_handles_empty_input() -> None:
    assert distribution([]) == {
        "median": None,
        "p90": None,
        "p95": None,
        "p99": None,
    }
