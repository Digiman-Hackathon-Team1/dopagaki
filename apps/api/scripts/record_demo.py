"""데모 대본을 실제 Claude로 한 번 돌려 응답을 녹화한다(설계 문서 13장 데모 고정, `api/agent/demo.py`).

실행: `cd apps/api && uv run python scripts/record_demo.py --id hifu-ja --lang ja "첫 메시지" "둘째 메시지" ...`

- 결과는 `src/api/agent/data/demo-replies.json`에 대본 ID별로 저장한다. 같은 ID가 있으면 바꾼다.
- 대본 턴 수만큼 에이전트 턴이 돌고, 턴마다 Claude를 2~4번 부른다. 모델이 Haiku가 아니면 실행하지 않는다.
- 한 턴이라도 시세표 안내(실패)로 답하면 저장하지 않는다. 녹화본을 바꾼 뒤에는 커밋 전에 답을 읽어 확인한다.
- `--lang`은 대본 메시지의 언어이고 화면 언어로도 보낸다. 녹화본은 메시지의 1차 판별 언어로 찾으므로(ADR 0020),
  1차 판별이 `--lang`과 다른 메시지가 있거나 Claude가 다른 언어로 답하면 녹화하지 않는다.
"""

import argparse
import sys
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import messages_to_dict

from api.agent.config import get_settings
from api.agent.demo import DEMO_PATH, DemoScript, DemoTurn, dump_demo, load_demo
from api.agent.lang import turn_lang
from api.agent.service import get_agent
from api.main import app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="대본 ID. 예: hifu-ja")
    parser.add_argument(
        "--lang", required=True, choices=["ja", "ko"], help="대본 메시지의 언어"
    )
    parser.add_argument("messages", nargs="+", help="대본의 사용자 메시지(턴 순서대로)")
    parser.add_argument("--allow-model", help="Haiku가 아닌 모델을 쓸 때 그 모델 이름")
    args = parser.parse_args()

    # 녹화본은 1차 판별 언어로 찾는다. 직전 턴은 --lang으로 답했다고 본다(아래에서 답 언어도 확인한다).
    previous = None
    for message in args.messages:
        if (lang := turn_lang(message, previous, args.lang)) != args.lang:
            print(
                f"메시지 언어가 {lang}로 판별돼 --lang {args.lang} 대본으로 찾을 수 없다: {message}",
                file=sys.stderr,
            )
            return 2
        previous = args.lang

    settings = get_settings()
    if "haiku" not in settings.agent_model and settings.agent_model != args.allow_model:
        print(
            f"AGENT_MODEL={settings.agent_model}: Haiku가 아니라 실행하지 않는다",
            file=sys.stderr,
        )
        return 2
    # 녹화는 실제 Claude로 한다. 이미 녹화한 응답을 다시 녹화하지 않게 캐시를 끈다.
    settings.agent_demo_cache = False
    agent = get_agent(settings)
    if agent is None:
        print("ANTHROPIC_API_KEY가 없다", file=sys.stderr)
        return 2

    print(
        f"모델 {settings.agent_model}, 대본 {args.id}, 에이전트 턴 {len(args.messages)}번"
    )
    client = TestClient(app)
    user_id = str(uuid4())
    thread_id: str | None = None
    seen = 0
    turns: list[DemoTurn] = []
    for message in args.messages:
        response = client.post(
            "/api/agent/chat",
            json={
                "userId": user_id,
                "threadId": thread_id,
                "message": message,
                "lang": args.lang,
            },
        )
        response.raise_for_status()
        body = response.json()
        if body["fallback"] is not None:
            print(f"시세표 안내로 답해 저장하지 않는다: {message}", file=sys.stderr)
            return 1
        if body["lang"] != args.lang:
            print(
                f"{body['lang']}로 답해 저장하지 않는다(대본 언어 {args.lang}): {message}",
                file=sys.stderr,
            )
            return 1
        thread_id = body["threadId"]
        state = agent.get_state(
            {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}
        )
        messages = state.values["messages"]
        turns.append(
            DemoTurn(
                message=message,
                response={k: v for k, v in body.items() if k != "threadId"},
                messages=messages_to_dict(messages[seen:]),
            )
        )
        seen = len(messages)
        print(f"\n### {message}\n{body['answer']}")

    demo = load_demo()
    script = DemoScript(
        id=args.id,
        lang=args.lang,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model=settings.agent_model,
        turns=turns,
    )
    demo.scripts = [s for s in demo.scripts if s.id != args.id] + [script]
    dump_demo(demo)
    print(f"\n{DEMO_PATH}에 저장했다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
