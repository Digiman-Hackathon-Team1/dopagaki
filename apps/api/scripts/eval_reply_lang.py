"""실제 Claude로 응답 언어를 맞히는지 잰다(설계 문서 2장 응답 언어, 이슈 #76, ADR 0020).

실행: `cd apps/api && uv run python scripts/eval_reply_lang.py --out <결과.json> [--runs 3] [--mode app|code] [--set tuning|holdout|all]`

- `--mode app`(기본, 지금 구현): 1차 판별 언어로 답하라고 지시하되, 사용자가 다른 언어를 직접 요청하면 그 언어로.
- `--mode code`(비교용): 1차 판별 언어로만 답하라고 지시한다(이슈 #76 이전 안내 문장).
- `--set tuning`: 언어 안내를 고칠 때 본 입력. `--set holdout`: 안내를 고칠 때 보지 않은 입력(과적합 확인용).
  새로 고칠 때는 holdout을 보고 고치지 말고, 고친 뒤 새 holdout을 더한다.
- 케이스마다 마지막 메시지의 기대 언어와 응답의 lang을 비교한다. 답변·조건 요약·질문·다음 질문 제안 중
  응답 언어와 다른 언어로 쓴 칸이 있으면 "섞임"으로 센다. 판단이 갈릴 수 있는 케이스는 정답률에서 따로 센다.
- 비용 규칙: 모델이 Haiku가 아니면 실행하지 않는다(`--allow-model`로만 바꿀 수 있다). 실행 전에 턴 수를 출력한다.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.agent import graph as agent_graph
from api.agent.config import get_settings
from api.agent.lang import detect_lang, turn_lang
from api.agent.service import get_agent
from api.main import app

# --mode code에서 쓰는 언어 안내(이슈 #76 이전 문장과 같다)
CODE_NOTE = "[Note from Dopa app] Reply language for this turn: {language}."


@dataclass(frozen=True)
class Case:
    id: str
    screen: str
    # 앞 메시지는 맥락이다. 기대 언어는 마지막 메시지에 대한 것이다.
    messages: tuple[str, ...]
    expected: str
    # 사람이 봐도 갈릴 수 있으면 True. 정답률에서 따로 센다.
    ambiguous: bool = False


TUNING = (
    Case("ja-plain", "ko", ("リジュランはいくらですか",), "ja"),
    Case("ko-plain", "ja", ("리쥬란 얼마예요?",), "ko"),
    Case("ko-katakana-name", "ja", ("リジュラン 얼마?",), "ko"),
    Case("ko-katakana-name-long", "ja", ("リジュランアイ 얼마예요?",), "ko"),
    Case("ko-jp-word", "ja", ("일본에서 ハイフ 받았는데 서울에서 얼마예요?",), "ko"),
    Case("ja-korean-place", "ko", ("リジュラン 강남 価格",), "ja"),
    Case("ja-kanji-only", "ko", ("明洞 皮膚科",), "ja"),
    Case("ja-yen", "ko", ("3万円でできる施術は?",), "ja"),
    Case("ko-request-in-mixed", "ja", ("한국어로 말해줘. リジュランの価格",), "ko"),
    Case("ko-request-in-ja", "ja", ("韓国語で答えて。リジュランはいくら?",), "ko"),
    Case("ja-request-in-ko", "ko", ("일본어로 알려줘. 리쥬란 가격",), "ja"),
    Case("ja-korean-clinic-name", "ja", ("밴스의원 홍대점 どう?",), "ja"),
    Case("ja-korean-names-only", "ja", ("강남 リジュラン",), "ja", ambiguous=True),
    Case("ko-followup-undetected", "ja", ("리쥬란 얼마예요?", "HIFU 300?"), "ko"),
    Case("ja-followup-undetected", "ko", ("リジュランはいくらですか", "2cc?"), "ja"),
    Case("ko-switch", "ja", ("リジュランはいくらですか", "강남에서는요?"), "ko"),
)

HOLDOUT = (
    # 일본어 화면, 한국어 문장 + 일본 시술명
    Case("h-ko-dermapen", "ja", ("ダーマペン 4 홍대에서 얼마?",), "ko"),
    Case("h-ko-ulthera", "ja", ("ウルセラ 받고 싶은데 회복 기간은요?",), "ko"),
    Case("h-ko-botox", "ja", ("ボトックス 사각턱 제일 싼 데 알려줘",), "ko"),
    Case("h-ko-shimi", "ja", ("シミ取りレーザー 같은 거 서울에도 있어요?",), "ko"),
    Case("h-ko-pico", "ja", ("ピコトーニング 몇 번 받아야 효과 있어요?",), "ko"),
    # 한국어 화면, 일본어 문장 + 한국 지명·시술명
    Case("h-ja-apgujeong", "ko", ("압구정 クリニックで予約したい",), "ja"),
    Case("h-ja-sinsa", "ko", ("신사역の近くでインモード安いところ",), "ja"),
    Case("h-ja-rejuran-ko", "ko", ("리쥬란 힐러って痛いですか",), "ja"),
    Case("h-ja-myeongdong", "ko", ("명동역から歩いて行ける皮膚科ある?",), "ja"),
    Case("h-ja-plain", "ko", ("ハイフとウルセラの違いは?",), "ja"),
    # 언어 요청
    Case("h-req-ja-in-ko", "ko", ("일본어로 답해줘. 슈링크 가격 알려줘",), "ja"),
    Case(
        "h-req-ko-in-ja",
        "ja",
        ("한국어로 부탁해요。ボトックスの値段を教えてください",),
        "ko",
    ),
    Case("h-req-ko-written-ja", "ja", ("韓国語で教えてもらえますか？ 리쥬란",), "ko"),
    # 판별 못 하는 다음 턴
    Case("h-follow-ja", "ko", ("ボトックスはいくら?", "500?"), "ja"),
    Case("h-follow-ko", "ja", ("인모드 가격 알려줘", "OK, Hongdae?"), "ko"),
)

SETS = {"tuning": TUNING, "holdout": HOLDOUT, "all": TUNING + HOLDOUT}


def field_texts(body: dict) -> dict[str, list[str]]:
    return {
        "answer": [body["answer"]],
        "condition_summary": [body["conditionSummary"]],
        "questions": [
            t for q in body["questions"] for t in (q["question"], *q["options"])
        ],
        "suggestions": body["suggestions"],
    }


def mixed_fields(body: dict) -> list[str]:
    """응답 언어와 다른 언어로 쓴 칸(글자로 정할 수 없는 칸은 세지 않는다)."""
    return [
        name
        for name, texts in field_texts(body).items()
        if any(detect_lang(t) not in (None, body["lang"]) for t in texts)
    ]


def first_guess(case: Case) -> str:
    """1차 판별(--mode code의 응답 언어). 앞 메시지는 그 1차 판별 언어로 답했다고 본다."""
    previous = None
    for message in case.messages:
        previous = turn_lang(message, previous, case.screen)
    assert previous is not None
    return previous


def run_case(client: TestClient, case: Case) -> dict:
    user_id, thread_id, body = str(uuid4()), None, {}
    for message in case.messages:
        response = client.post(
            "/api/agent/chat",
            json={
                "userId": user_id,
                "threadId": thread_id,
                "message": message,
                "lang": case.screen,
            },
        )
        response.raise_for_status()
        body = response.json()
        thread_id = body["threadId"]
    return {
        "lang": body["lang"],
        "fallback": body["fallback"],
        "correct": body["fallback"] is None and body["lang"] == case.expected,
        "mixed": mixed_fields(body),
        "answer": body["answer"],
        "suggestions": body["suggestions"],
    }


def summarize(results: list[dict]) -> dict:
    clear = [r for r in results if not r["ambiguous"]]
    runs = [run for r in clear for run in r["runs"]]
    return {
        "cases": len(clear),
        "ambiguous_cases": len(results) - len(clear),
        "runs_correct": sum(run["correct"] for run in runs),
        "runs": len(runs),
        "cases_all_correct": sum(
            all(run["correct"] for run in r["runs"]) for r in clear
        ),
        "runs_mixed": sum(bool(run["mixed"]) for r in results for run in r["runs"]),
        "fallbacks": sum(
            run["fallback"] is not None for r in results for run in r["runs"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--mode", choices=["app", "code"], default="app")
    parser.add_argument("--set", choices=list(SETS), default="all")
    parser.add_argument("--only", help="쉼표로 구분한 케이스 ID")
    parser.add_argument("--allow-model", help="Haiku가 아닌 모델을 쓸 때 그 모델 이름")
    args = parser.parse_args()

    settings = get_settings()
    if "haiku" not in settings.agent_model and settings.agent_model != args.allow_model:
        print(
            f"AGENT_MODEL={settings.agent_model}: Haiku가 아니라 실행하지 않는다",
            file=sys.stderr,
        )
        return 2
    settings.agent_demo_cache = False
    if get_agent(settings) is None:
        print("ANTHROPIC_API_KEY가 없다", file=sys.stderr)
        return 2
    if args.mode == "code":
        agent_graph.LANG_NOTE = CODE_NOTE

    only = set(args.only.split(",")) if args.only else None
    cases = [c for c in SETS[args.set] if only is None or c.id in only]
    turns = sum(len(c.messages) for c in cases) * args.runs
    print(
        f"모델 {settings.agent_model}, 모드 {args.mode}, 케이스 {len(cases)}개 × {args.runs}회, 에이전트 턴 {turns}번"
    )
    client = TestClient(app)
    results = []
    for case in cases:
        runs = [run_case(client, case) for _ in range(args.runs)]
        results.append(
            {
                "id": case.id,
                "screen": case.screen,
                "messages": list(case.messages),
                "expected": case.expected,
                "first_guess": first_guess(case),
                "ambiguous": case.ambiguous,
                "runs": runs,
            }
        )
        marks = " ".join(
            ("O" if run["correct"] else "X") + ("(섞임)" if run["mixed"] else "")
            for run in runs
        )
        print(
            f"{case.id:<26} 기대 {case.expected} 1차 {first_guess(case)} 응답 {[r['lang'] for r in runs]} {marks}"
        )

    summary = summarize(results)
    summary.update(
        model=settings.agent_model,
        mode=args.mode,
        set=args.set,
        runs_per_case=args.runs,
    )
    print(json.dumps(summary, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"summary": summary, "results": results}, ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
