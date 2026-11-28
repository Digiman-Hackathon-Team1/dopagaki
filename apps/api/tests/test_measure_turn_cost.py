"""`scripts/measure_turn_cost.py`의 토큰 분해·비용·요약 계산과 가짜 LLM 한 바퀴(이슈 #59)."""

import sys
from pathlib import Path

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_scenarios import Scenario, Turn
from measure_turn_cost import (
    _GraphLogCounter,
    _run_scenario,
    input_cost,
    p90,
    split_usage,
    summarize_rows,
)

from api.agent import service as agent_service
from api.agent.graph import build_graph
from api.agent.service import DailyUsage, get_agent
from api.agent.tools import TOOLS
from api.main import app


def test_split_usage_uses_ttl_breakdown_when_present() -> None:
    # langchain-anthropic의 input_tokens는 캐시 읽기·쓰기를 더한 전체 입력이다.
    usage = {
        "input_tokens": 12_000,
        "output_tokens": 300,
        "input_token_details": {
            "cache_read": 9_000,
            "cache_creation": 0,
            "ephemeral_5m_input_tokens": 2_500,
            "ephemeral_1h_input_tokens": 0,
        },
    }

    assert split_usage(usage) == {
        "input_total": 12_000,
        "uncached": 500,
        "cache_write_5m": 2_500,
        "cache_write_1h": 0,
        "cache_read": 9_000,
        "output": 300,
    }


def test_split_usage_falls_back_to_generic_cache_creation() -> None:
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 10,
        "input_token_details": {"cache_read": 0, "cache_creation": 600},
    }

    split = split_usage(usage)

    assert split["cache_write_5m"] == 600
    assert split["uncached"] == 400


def test_split_usage_without_cache_details() -> None:
    split = split_usage({"input_tokens": 5_000, "output_tokens": 100})

    assert split["uncached"] == 5_000
    assert split["cache_read"] == split["cache_write_5m"] == 0


def test_input_cost_weights_cache_write_and_read() -> None:
    call = {
        "uncached": 1_000_000,
        "cache_write_5m": 1_000_000,
        "cache_write_1h": 1_000_000,
        "cache_read": 1_000_000,
    }

    # $0.10 × (1 + 1.25 + 2 + 0.1)
    assert input_cost(call) == pytest.approx(0.435)


def test_p90_is_nearest_rank() -> None:
    assert p90([float(v) for v in range(1, 11)]) == 9.0
    assert p90([3.0]) == 3.0


def _call(turn: int, call_idx: int, read: int, total: int = 1_000) -> dict:
    return {
        "kind": "call",
        "turn": turn,
        "call_idx": call_idx,
        "input_total": total,
        "uncached": total - read,
        "cache_write_5m": 0,
        "cache_write_1h": 0,
        "cache_read": read,
        "output": 0,
    }


def _turn(ttft: float, total: float) -> dict:
    return {
        "kind": "turn",
        "ttft_s": ttft,
        "total_s": total,
        "has_delta": True,
        "passed": True,
        "fallback": None,
        "unrecorded_retries": 0,
        "unrecorded_failures": 0,
    }


def test_summary_counts_cache_reads_for_completion_criteria() -> None:
    rows = [
        _call(1, 0, read=0),
        _call(1, 1, read=800),
        _turn(2.0, 4.0),
        _call(2, 0, read=900),
        _call(2, 1, read=0),
        _turn(1.0, 3.0),
    ]

    s = summarize_rows(rows)

    assert s["turns"] == 2
    assert s["calls_per_turn"] == 2
    assert s["cache_hit_rate"] == pytest.approx(1_700 / 4_000)
    # 2턴째 첫 호출은 읽었고, 한 턴 안 두 번째 호출은 두 번 중 한 번만 읽었다.
    assert s["next_turn_first_call_read"] == 1.0
    assert s["in_turn_later_call_read"] == 0.5
    assert s["ttft_median"] == 1.5
    assert s["total_p90"] == 4.0


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_run_scenario_records_calls_and_stream_timing(client: TestClient) -> None:
    graph = build_graph(
        fake_model(
            tool_call("search_clinics", {"menu_id": "rejuran-2cc"}),
            reply_message("리쥬란은 이곳이 싸요. 확인해 보세요"),
            reply_message("모레 귀국이면 괜찮아요."),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    app.dependency_overrides[get_agent] = lambda: graph
    scenario = Scenario(
        "t1",
        "테스트",
        "ko",
        "두 턴",
        (Turn("리쥬란 싼 곳"), Turn("귀국은 모레예요")),
    )

    rows = _run_scenario(client, graph, scenario, "x", 1, _GraphLogCounter())

    calls = [r for r in rows if r["kind"] == "call"]
    turns = [r for r in rows if r["kind"] == "turn"]
    assert [(c["turn"], c["call_idx"], c["tools"]) for c in calls] == [
        (1, 0, ["search_clinics"]),
        (1, 1, []),
        (2, 0, []),
    ]
    assert [t["llm_calls"] for t in turns] == [2, 1]
    for t in turns:
        assert t["has_delta"]
        assert t["fallback"] is None
        assert t["failures"] == []
        assert 0 <= t["start_s"] <= t["ttft_s"] <= t["total_s"]
