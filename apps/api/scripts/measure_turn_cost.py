"""상담 한 턴의 입력 비용과 첫 글자 대기 시간을 실제 Claude(Haiku)로 잰다(이슈 #59, 설계 문서 6장·8장).

실행(`apps/api`에서):
- 측정: `uv run python scripts/measure_turn_cost.py run --label before --out <결과.jsonl> [--reps 5] [--yes]`
- 요약: `uv run python scripts/measure_turn_cost.py summary <전.jsonl> <후.jsonl>`

- 대본은 `scripts/eval_scenarios.py`의 시나리오를 쓴다. 기본은 2턴짜리 s1·s2·s3·s5다. 2턴째 첫 호출과
  한 턴 안 두 번째 호출이 모두 생겨, 다음 턴과 도구 반복 양쪽의 캐시 읽기를 볼 수 있다.
- `POST /api/agent/chat/stream`으로 보낸다. 첫 글자 시간은 요청을 보낸 때부터 첫 `delta`까지, 전체 시간은
  `done`까지다. `delta`가 없는 턴(질문 묶음만 보내는 턴 등)은 첫 글자 시간을 전체 시간으로 둔다.
- LLM 호출마다의 토큰 수는 대화 기억에 남은 응답(`AIMessage.usage_metadata`)에서 읽는다. 서버 코드를 바꾸지
  않고 읽으므로, 캐싱을 넣기 전 코드로 기준값을 잴 수 있다. 빈 응답 재요청과 실패한 호출은 기록에 남지 않아
  세지 못하므로, 그런 일이 있었던 턴은 따로 표시한다.
- 비용 규칙: 모델이 Haiku가 아니면 실행하지 않는다. `--yes` 없이 실행하면 예상 턴 수와 비용만 출력한다.
  같은 대본을 반복할 때마다 새 사용자·새 대화로 시작하고, 요청은 하나씩 차례로 보낸다.
"""

import argparse
import json
import logging
import math
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from eval_scenarios import SCENARIOS, Scenario, grade_turn
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from api.agent.config import get_settings
from api.agent.service import get_agent
from api.main import app

DEFAULT_SCENARIOS = ("s1", "s2", "s3", "s5")

# Claude Haiku 5.5 단가(USD, 100만 토큰당, 프롬프트 10만 토큰 이하). 캐시 쓰기는 기본 입력 단가의 1.25배(5분)·
# 2배(1시간), 캐시 읽기는 0.1배다. 실행 전에 가격표(https://claude.com/pricing)를 다시 확인한다.
INPUT_PER_MTOK = 0.10
OUTPUT_PER_MTOK = 0.50
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1

# --yes 없이 보여 주는 예상 비용의 가정(추정). 도구 정의·시스템 프롬프트 약 10K 토큰 + 대화 기록 약 3K 토큰,
# 턴당 LLM 호출 2.5번, 호출당 출력 0.8K 토큰. 캐싱을 넣기 전 값이라 넣은 뒤에는 이보다 적게 든다.
EST_INPUT_PER_CALL = 13_000
EST_OUTPUT_PER_CALL = 800
EST_CALLS_PER_TURN = 2.5

# 대화 기록에 남지 않는 호출을 세기 위해 그래프 로그 문장을 본다(graph.py의 logger.warning·exception).
RETRY_LOG = "빈 응답을 받아 한 번 다시 요청한다"
FAILURE_LOG = "LLM 호출 실패"


class _GraphLogCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.retries = 0
        self.failures = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(RETRY_LOG):
            self.retries += 1
        elif message.startswith(FAILURE_LOG):
            self.failures += 1


def split_usage(usage: dict) -> dict[str, int]:
    """langchain `usage_metadata`를 캐시 없이 처리한 입력·캐시 쓰기·캐시 읽기·출력으로 나눈다.

    langchain-anthropic의 `input_tokens`는 Anthropic `input_tokens`(캐시 제외)에 캐시 읽기와 캐시 쓰기를 더한
    전체 입력이다. 쓰기를 5분·1시간으로 나눈 값이 있으면 `cache_creation`은 0으로 바뀌어 온다.
    """
    details = usage.get("input_token_details") or {}
    total = usage.get("input_tokens", 0) or 0
    read = details.get("cache_read") or 0
    write_5m = details.get("ephemeral_5m_input_tokens") or 0
    write_1h = details.get("ephemeral_1h_input_tokens") or 0
    if not write_5m and not write_1h:
        write_5m = details.get("cache_creation") or 0
    return {
        "input_total": total,
        "uncached": total - read - write_5m - write_1h,
        "cache_write_5m": write_5m,
        "cache_write_1h": write_1h,
        "cache_read": read,
        "output": usage.get("output_tokens", 0) or 0,
    }


def input_cost(call: dict) -> float:
    """호출 하나의 입력 비용(USD)."""
    weighted = (
        call["uncached"]
        + call["cache_write_5m"] * CACHE_WRITE_5M
        + call["cache_write_1h"] * CACHE_WRITE_1H
        + call["cache_read"] * CACHE_READ
    )
    return weighted * INPUT_PER_MTOK / 1_000_000


def output_cost(call: dict) -> float:
    return call["output"] * OUTPUT_PER_MTOK / 1_000_000


def p90(values: list[float]) -> float:
    """nearest-rank 방식의 90번째 백분위수."""
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1]


def _sse_events(response) -> "list[tuple[float, dict]]":
    """SSE 응답에서 (도착 시각, 이벤트) 목록을 읽는다. 서버는 `data:` 줄에 JSON만 보낸다."""
    events = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            events.append((time.perf_counter(), json.loads(line[len("data:") :])))
    return events


def _this_turn_ai(messages: list) -> list[AIMessage]:
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return [m for m in messages[start + 1 :] if isinstance(m, AIMessage)]


def _run_scenario(
    client: TestClient,
    agent,
    scenario: Scenario,
    label: str,
    rep: int,
    log: _GraphLogCounter,
) -> list[dict]:
    rows: list[dict] = []
    user_id = str(uuid4())
    thread_id: str | None = None
    for turn_no, turn in enumerate(scenario.turns, start=1):
        log.retries = log.failures = 0
        sent = time.perf_counter()
        with client.stream(
            "POST",
            "/api/agent/chat/stream",
            json={
                "userId": user_id,
                "threadId": thread_id,
                "message": turn.message,
                "lang": scenario.lang,
            },
        ) as response:
            response.raise_for_status()
            events = _sse_events(response)
        done_at, done = next((t, e) for t, e in events if e["type"] == "done")
        first_delta = next((t for t, e in events if e["type"] == "delta"), None)
        start_at = next((t for t, e in events if e["type"] == "start"), None)
        body = done["response"]
        thread_id = body["threadId"]
        state = agent.get_state(
            {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}
        )
        messages = state.values["messages"]
        ai = _this_turn_ai(messages)
        grade = grade_turn(turn, body, messages)
        key = {"label": label, "rep": rep, "scenario": scenario.id, "turn": turn_no}
        for call_idx, message in enumerate(ai):
            rows.append(
                {
                    "kind": "call",
                    **key,
                    "call_idx": call_idx,
                    "tools": [c["name"] for c in message.tool_calls],
                    **split_usage(message.usage_metadata or {}),
                }
            )
        rows.append(
            {
                "kind": "turn",
                **key,
                "start_s": None if start_at is None else start_at - sent,
                "ttft_s": (done_at if first_delta is None else first_delta) - sent,
                "total_s": done_at - sent,
                "has_delta": first_delta is not None,
                "llm_calls": len(ai),
                "unrecorded_retries": log.retries,
                "unrecorded_failures": log.failures,
                "fallback": body["fallback"],
                "passed": grade["passed"],
                "failures": grade["failures"],
            }
        )
    return rows


def _commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "src"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if "haiku" not in settings.agent_model:
        print(
            f"AGENT_MODEL={settings.agent_model}: Haiku가 아니라 실행하지 않는다",
            file=sys.stderr,
        )
        return 2
    # 녹화 응답이 아니라 실제 Claude로 잰다. 환율은 스냅샷 값으로 고정해 도구 결과를 전후 같게 한다.
    settings.agent_demo_cache = False
    settings.web_fx_url = None

    only = args.only.split(",")
    scenarios = [s for s in SCENARIOS if s.id in only]
    missing = set(only) - {s.id for s in scenarios}
    if missing:
        print(f"없는 시나리오: {sorted(missing)}", file=sys.stderr)
        return 2
    turns = args.reps * sum(len(s.turns) for s in scenarios)
    calls = turns * EST_CALLS_PER_TURN
    estimate = (
        calls
        * (EST_INPUT_PER_CALL * INPUT_PER_MTOK + EST_OUTPUT_PER_CALL * OUTPUT_PER_MTOK)
        / 1_000_000
    )
    print(
        f"모델 {settings.agent_model}(effort {settings.agent_effort}), 시나리오 {','.join(only)}, "
        f"반복 {args.reps}번, 에이전트 턴 {turns}번, 예상 LLM 호출 약 {calls:.0f}번, "
        f"예상 비용(캐싱 없음 기준 추정) 약 ${estimate:.2f}"
    )
    if not args.yes:
        print("--yes를 붙여야 실제로 보낸다")
        return 0
    agent = get_agent(settings)
    if agent is None:
        print("ANTHROPIC_API_KEY가 없다", file=sys.stderr)
        return 2

    log = _GraphLogCounter()
    logging.getLogger("api.agent.graph").addHandler(log)
    client = TestClient(app)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as out:
        header = {
            "kind": "run",
            "label": args.label,
            "model": settings.agent_model,
            "effort": settings.agent_effort,
            "prompt_cache": getattr(settings, "agent_prompt_cache", None),
            "scenarios": only,
            "reps": args.reps,
            "commit": _commit(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        out.write(json.dumps(header, ensure_ascii=False) + "\n")
        for rep in range(1, args.reps + 1):
            for scenario in scenarios:
                rows = _run_scenario(client, agent, scenario, args.label, rep, log)
                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                done = [r for r in rows if r["kind"] == "turn"]
                print(
                    f"[{rep}/{args.reps}] {scenario.id}: "
                    + " / ".join(
                        f"{r['ttft_s']:.1f}s·{r['total_s']:.1f}s·호출{r['llm_calls']}"
                        for r in done
                    )
                )
    print(f"기록: {args.out}")
    return 0


def summarize_rows(rows: list[dict]) -> dict:
    """한 라벨의 기록을 표 한 줄로 줄인다."""
    calls = [r for r in rows if r["kind"] == "call"]
    turns = [r for r in rows if r["kind"] == "turn"]
    input_total = sum(c["input_total"] for c in calls)
    next_turn_first = [c for c in calls if c["turn"] >= 2 and c["call_idx"] == 0]
    in_turn_later = [c for c in calls if c["call_idx"] >= 1]
    ttft = [t["ttft_s"] for t in turns]
    total = [t["total_s"] for t in turns]

    def hit_ratio(group: list[dict]) -> float | None:
        return sum(c["cache_read"] > 0 for c in group) / len(group) if group else None

    return {
        "turns": len(turns),
        "calls": len(calls),
        "calls_per_turn": len(calls) / len(turns),
        "input_cost_per_turn": sum(input_cost(c) for c in calls) / len(turns),
        "output_cost_per_turn": sum(output_cost(c) for c in calls) / len(turns),
        "input_tokens_per_turn": input_total / len(turns),
        "cache_hit_rate": sum(c["cache_read"] for c in calls) / input_total
        if input_total
        else 0.0,
        "next_turn_first_call_read": hit_ratio(next_turn_first),
        "in_turn_later_call_read": hit_ratio(in_turn_later),
        "ttft_median": statistics.median(ttft),
        "ttft_p90": p90(ttft),
        "total_median": statistics.median(total),
        "total_p90": p90(total),
        "passed": sum(t["passed"] for t in turns),
        "fallbacks": sum(t["fallback"] is not None for t in turns),
        "no_delta": sum(not t["has_delta"] for t in turns),
        "unrecorded": sum(
            t["unrecorded_retries"] + t["unrecorded_failures"] for t in turns
        ),
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _table(groups: dict[str, list[dict]]) -> list[str]:
    lines = [
        (
            "| | 턴 | 턴당 입력 비용 | 턴당 출력 비용 | 턴당 입력 토큰 | 캐시 적중률 "
            "| 첫 글자 중앙값 / p90 | 전체 중앙값 / p90 | 턴당 LLM 호출 | 채점 통과 |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, rows in groups.items():
        s = summarize_rows(rows)
        lines.append(
            f"| {name} | {s['turns']} | ${s['input_cost_per_turn']:.5f} | ${s['output_cost_per_turn']:.5f} "
            f"| {s['input_tokens_per_turn']:,.0f} | {_pct(s['cache_hit_rate'])} "
            f"| {s['ttft_median']:.1f}초 / {s['ttft_p90']:.1f}초 | {s['total_median']:.1f}초 / {s['total_p90']:.1f}초 "
            f"| {s['calls_per_turn']:.1f} | {s['passed']}/{s['turns']} |"
        )
    return lines


def summary(args: argparse.Namespace) -> int:
    by_label: dict[str, list[dict]] = defaultdict(list)
    headers: list[dict] = []
    for path in args.files:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["kind"] == "run":
                headers.append(row)
            else:
                by_label[row["label"]].append(row)

    for h in headers:
        print(
            f"- {h['label']}: 모델 {h['model']}(effort {h['effort']}), 캐싱 설정 {h['prompt_cache']}, "
            f"시나리오 {','.join(h['scenarios'])} × {h['reps']}번, 커밋 {h['commit']}, 시작 {h['started_at']}"
        )
    print("\n전체\n")
    print("\n".join(_table(by_label)))

    print("\n완료 기준(캐시 읽기가 0보다 큰 호출의 비율)\n")
    print("| | 2턴째 이후 첫 호출 | 한 턴 안 두 번째 이후 호출 |")
    print("| --- | --- | --- |")
    for label, rows in by_label.items():
        s = summarize_rows(rows)
        print(
            f"| {label} | {_pct(s['next_turn_first_call_read'])} | {_pct(s['in_turn_later_call_read'])} |"
        )

    print("\n시나리오별\n")
    per_scenario: dict[str, list[dict]] = defaultdict(list)
    for label, rows in by_label.items():
        for row in rows:
            per_scenario[f"{row['scenario']} {label}"].append(row)
    print("\n".join(_table(dict(sorted(per_scenario.items())))))

    for label, rows in by_label.items():
        s = summarize_rows(rows)
        notes = []
        if s["fallbacks"]:
            notes.append(f"시세표 안내 {s['fallbacks']}턴")
        if s["no_delta"]:
            notes.append(f"delta 없는 턴 {s['no_delta']}개(첫 글자 = 전체 시간)")
        if s["unrecorded"]:
            notes.append(
                f"기록에 없는 재요청·실패 호출 {s['unrecorded']}번(토큰 수에서 빠짐)"
            )
        if notes:
            print(f"\n- {label}: " + ", ".join(notes))
        # 앞 버전 기록에는 failures가 없다.
        failed = [r for r in rows if r["kind"] == "turn" and r.get("failures")]
        for r in failed:
            print(
                f"  - 채점 실패 {r['scenario']} {r['turn']}턴(반복 {r['rep']}): "
                + "; ".join(r["failures"])
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="실제 Claude로 대본을 보내 기록한다")
    run_parser.add_argument("--label", required=True, help="예: before, after")
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--reps", type=int, default=5)
    run_parser.add_argument("--only", default=",".join(DEFAULT_SCENARIOS))
    run_parser.add_argument(
        "--yes", action="store_true", help="예상 비용을 확인했으면 붙인다"
    )
    summary_parser = sub.add_parser("summary", help="기록 파일들을 표로 요약한다")
    summary_parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    return run(args) if args.command == "run" else summary(args)


if __name__ == "__main__":
    sys.exit(main())
