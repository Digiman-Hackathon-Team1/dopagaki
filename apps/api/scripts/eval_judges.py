"""시나리오 평가의 LLM judge(이슈 #63). 코드 채점이 못 보는 문장 오류를 예/아니오와 이유로 판정한다.

- 질문 하나에 judge 하나다. 여러 기준을 한 프롬프트에 몰면 무엇이 틀렸는지 알 수 없다.
- judge는 평가 대상(Haiku)보다 강한 모델로 부른다. 판정에 노이즈가 있어 시나리오 통과 여부에는 넣지 않고 따로 센다.
- 판정 근거는 앱이 화면에 보낸 응답(body)이다. 카드 숫자는 도구 결과를 코드가 채운 값이라 그대로 근거로 쓴다.
"""

import json
from collections.abc import Callable

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field, SecretStr


class Verdict(BaseModel):
    passed: bool = Field(description="기준을 모두 지키면 true")
    explanation: str = Field(
        description=(
            "판정 이유. 틀렸으면 틀린 문장을 그대로 인용하고, 비교한 카드의 이름과 값을 함께 적는다"
        )
    )


# 카드 하나에서 비교에 쓰일 만한 값만 남긴다. 나머지(출처 URL 등)는 판정에 필요 없고 프롬프트만 길어진다.
CARD_FACTS = (
    "clinicNameKo",
    "clinicNameJa",
    "menuNameKo",
    "totalKrw",
    "totalJpy",
    "savingsJpy",
    "savingsPct",
    "rating",
    "japaneseSupport",
    "walkMinutes",
    "distanceMeters",
    "priceConditionKo",
)

CARD_COMPARISON_RUBRIC = """당신은 일본인 관광객용 서울 미용 시술 가격 비교 서비스의 추천 카드를 검수합니다.
사용자는 카드 이유 한 줄만 보고 병원을 고르므로, 이유가 카드 데이터와 다르면 잘못 고르게 됩니다.

<cards>는 한 번의 병원 검색으로 나온 카드들의 실제 데이터이고, 각 카드의 reason이 AI가 쓴 추천 이유입니다.
아래 기준을 모두 지키면 passed=true입니다.
- 비교하는 말("가장 싸요", "가장 가까워요", "평점이 가장 높아요", "3곳 모두 같은 가격")은 <cards>의 모든 카드와 비교해 맞아야 한다
- 공동 1위(같은 값이 둘 이상)를 혼자 1위처럼 쓰지 않는다
- 이유에 나온 숫자·사실(가격, 평점, 일본어 상담 여부, 도보 시간)은 그 카드의 데이터와 같다
비교나 사실 언급이 없는 이유(동선, 분위기 등)는 기준 위반이 아닙니다.
위반이라고 판단하기 전에, 문장이 가리키는 카드를 이름(clinicNameKo 또는 clinicNameJa)으로 찾아 값을 다시 확인하세요.

<cards>
{cards}
</cards>"""

ANSWER_FIRST_RUBRIC = """당신은 일본인 관광객용 서울 미용 시술 상담 AI의 답변을 검수합니다.
이 턴은 사용자가 필요한 정보를 이미 줘서 AI가 바로 추천해야 하는 턴입니다.
답변 아래에는 병원 카드 {card_count}장(병원 이름·가격)이 함께 표시됩니다. 그래서 답변 글에 병원 이름이 없어도 됩니다.

아래 기준을 모두 지키면 passed=true입니다.
- 첫머리는 무엇을 찾았는지로 시작한다. 시술 이름, 일본 시술명과 한국 이름의 대응, 왜 이 시술인지를 한두 문장으로 말하는 것은 괜찮다
- 인사, 되묻기, 사용자가 말한 조건을 되풀이하는 말로 시작하면 위반이다
- 시술 설명(회복 기간, 샷 단위, 일본 병원과의 차이 등)이 세 문장 이상 이어진 뒤에야 추천 결과가 나오면 위반이다
- 답변 안에서 사용자에게 되묻는 질문은 끝에 하나 이하다
이 기준만 판정하세요. 금액·순위·비교 표현이 맞는지는 다른 검사가 따로 보므로 판정에 넣지 않습니다.

<user_message>{message}</user_message>
<answer>{answer}</answer>"""

Judge = Callable[[str], Verdict]


def card_groups(body: dict) -> list[list[dict]]:
    """병원 카드를 검색(searchId)별로 묶는다. 비교 표현은 같은 검색 카드끼리만 따진다."""
    groups: dict[str, list[dict]] = {}
    for card in body["cards"]:
        if card["kind"] == "recommendation":
            groups.setdefault(card["searchId"], []).append(card)
    return list(groups.values())


def card_comparison_prompt(cards: list[dict]) -> str | None:
    """이유가 하나도 없으면 판정할 문장이 없어 None이다."""
    if not any(c.get("reason") for c in cards):
        return None
    facts = [{k: c.get(k) for k in (*CARD_FACTS, "reason")} for c in cards]
    return CARD_COMPARISON_RUBRIC.format(
        cards=json.dumps(facts, ensure_ascii=False, indent=2)
    )


def answer_first_prompt(message: str, body: dict) -> str | None:
    if not body["answer"]:
        return None
    cards = sum(c["kind"] == "recommendation" for c in body["cards"])
    return ANSWER_FIRST_RUBRIC.format(
        card_count=cards, message=message, answer=body["answer"]
    )


def judge_turn(judge: Judge, message: str, shape: str | None, body: dict) -> list[dict]:
    """한 턴에 해당하는 judge를 모두 돌린다. 판정할 대상이 없는 judge는 건너뛴다."""
    prompts: list[tuple[str, str | None]] = [
        ("card_comparison", card_comparison_prompt(cards))
        for cards in card_groups(body)
    ]
    if shape == "answer":
        prompts.append(("answer_first", answer_first_prompt(message, body)))
    results = []
    for name, prompt in prompts:
        if prompt is None:
            continue
        verdict = judge(prompt)
        results.append({"judge": name, **verdict.model_dump()})
    return results


def anthropic_judge(model: str, api_key: SecretStr) -> Judge:
    llm = ChatAnthropic(
        model=model, api_key=api_key, effort="low", max_tokens=4000, max_retries=2
    ).with_structured_output(Verdict, method="json_schema")

    def judge(prompt: str) -> Verdict:
        verdict = llm.invoke(prompt)
        assert isinstance(verdict, Verdict)
        return verdict

    return judge
