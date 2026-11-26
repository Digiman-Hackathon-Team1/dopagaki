"""에이전트 그래프: load_context → safety_check → agent ⇄ tools → finalize (설계 문서 8장).

safety_check는 아직 그대로 통과한다(T6에서 추가).

Sonnet 5.5·Haiku 5.5는 이전 턴의 thinking 블록을 그 블록이 만들어질 때와 요청 앞부분이 같은
대화에서만 받아들인다. 그래서 대화 기록은 덧붙이기만 하고, 시스템 프롬프트는 턴마다 바뀌는
값(응답 언어 등)을 넣지 않은 고정 문장으로 둔다. 턴마다 바뀌는 지시는 그 턴의 사용자 메시지에
텍스트 블록으로 덧붙인다. 대화 중간 시스템 메시지는 쓰지 않는다. 그 턴의 LLM 호출이 실패하면
답 없는 시스템 메시지가 기록에 남아 다음 턴 요청을 만들 수 없게 되기 때문이다.
"""

from collections.abc import Sequence
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from api.agent.schemas import (
    AgentReply,
    Exclusion,
    Lang,
    RecommendationCard,
)
from api.agent.tools.search_clinics import TOOL_NAME as SEARCH_CLINICS
from api.agent.tools.search_clinics import SearchClinicsArtifact

LANG_NAME: dict[Lang, str] = {"ja": "Japanese", "ko": "Korean"}

SYSTEM_PROMPT = """\
You are Dopa, a consultation assistant for Japanese visitors and residents who want \
skincare and dermatology treatments in Seoul at prices cheaper than in Japan.
Help the user decide what treatment to get, when, where, and for how much, up to the \
clinic's booking channel. Do not diagnose and do not book or pay on the user's behalf.
Never state prices or savings that did not come from a tool result.
Clinics returned by search_clinics are shown to the user as cards. Give each card a \
one-line reason in card_reasons, by card_id. The app fills in the card's prices.
Write every field of your reply in the language named by the note at the end of the \
latest user message."""

LANG_NOTE = "[Note from Dopa app] Reply language for this turn: {language}."


class AgentOutputError(Exception):
    """Claude의 마지막 응답이 응답 칸 형식에 맞지 않을 때."""


class AgentState(MessagesState):
    lang: Lang
    # 대화 기억에 저장되므로 모델 객체 대신 일반 데이터로 둔다. 읽을 때는 read_reply를 쓴다.
    reply: dict | None
    cards: list[dict]
    exclusions: list[dict]


def read_reply(state: AgentState) -> AgentReply:
    """그래프 결과에서 이번 턴의 응답 칸을 꺼낸다."""
    return AgentReply.model_validate(state["reply"])


def read_cards(state: AgentState) -> list[RecommendationCard]:
    """그래프 결과에서 이번 턴의 카드를 꺼낸다."""
    return [RecommendationCard.model_validate(c) for c in state.get("cards") or []]


def read_exclusions(state: AgentState) -> list[Exclusion]:
    """그래프 결과에서 이번 턴 검색의 제외 사유를 꺼낸다."""
    return [Exclusion.model_validate(e) for e in state.get("exclusions") or []]


def search_results(messages: Sequence[BaseMessage]) -> list[SearchClinicsArtifact]:
    """이번 턴(마지막 사용자 메시지 뒤)의 성공한 search_clinics 결과(ADR 0009).

    - 같은 시술을 범위만 좁히거나 넓혀 다시 검색하면 앞 결과를 지우고, 지운 자리에 새 결과를 둔다.
    - 지역·기준 위치·지점이 서로 다른 대상을 검색하면 비교로 보고 앞 결과도 남긴다
      (`SearchTarget.compares_with`).
    - 이전 턴 결과, 다른 도구의 결과, 오류 난 호출은 읽지 않는다.
    - 검색 번호(search_id)는 이번 턴의 검색 순서(s1, s2, ...)다. 도구 호출 ID는 응답에 내보내지 않는다.
    """
    start = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
    )
    results: list[SearchClinicsArtifact] = []
    calls = 0
    for message in messages[start + 1 :]:
        if (
            not isinstance(message, ToolMessage)
            or message.name != SEARCH_CLINICS
            or message.status == "error"
        ):
            continue
        calls += 1
        new = SearchClinicsArtifact.model_validate(message.artifact).model_copy(
            update={"search_id": f"s{calls}"}
        )
        replaced = [
            i
            for i, old in enumerate(results)
            if old.menu_id == new.menu_id and not old.target.compares_with(new.target)
        ]
        position = replaced[0] if replaced else len(results)
        results = [r for i, r in enumerate(results) if i not in replaced]
        results.insert(position, new)
    return results


def assemble_cards(
    results: Sequence[SearchClinicsArtifact], reply: AgentReply
) -> list[RecommendationCard]:
    """검색 결과로 카드를 만들고 LLM이 쓴 추천 이유를 붙인다(ADR 0009).

    - 금액과 순서는 도구 결과 그대로다. LLM은 카드마다 이유만 쓴다.
    - 검색 결과의 카드는 모두 내보낸다(설계 문서 4.7). 다른 시술 검색 결과는 함께 나간다.
    - 비교 검색 두 곳에 같은 병원이 나오면 검색마다 한 장씩 낸다. 기준 위치가 다르면 거리가
      다르고, 검색별로 묶어 보여 줄 때 빠지면 안 되기 때문이다. 이유는 card_id로 함께 쓴다.
    - 이유가 없는 카드는 reason이 null이다. 도구 결과에 없는 card_id의 이유는 버린다.
    """
    reasons: dict[str, str] = {}
    for r in reply.card_reasons:
        reasons.setdefault(r.card_id, r.reason)
    return [
        RecommendationCard(
            **card.model_dump(),
            kind="recommendation",
            search_id=result.search_id,
            reason=reasons.get(card.card_id),
        )
        for result in results
        for card in result.cards
    ]


def assemble_exclusions(results: Sequence[SearchClinicsArtifact]) -> list[Exclusion]:
    return [
        Exclusion(**e.model_dump(), search_id=r.search_id)
        for r in results
        for e in r.exclusions
    ]


def build_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool] = (),
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """그래프를 만든다. 모델과 도구를 주입받아 테스트에서 가짜 LLM으로 바꿀 수 있다."""
    if tools:
        bound = model.bind_tools(tools, response_format=AgentReply)
    else:
        # 빈 tools 배열을 보내지 않도록 도구가 없으면 응답 형식만 붙인다.
        bound = model.bind(response_format=AgentReply)

    def load_context(state: AgentState) -> dict:
        # 이번 턴의 응답 언어를 사용자 메시지 끝에 텍스트 블록으로 붙인다.
        # 같은 id로 돌려주면 add_messages가 그 메시지를 교체한다(아직 LLM에 보내기 전이라 덧붙이기 조건을 해치지 않는다).
        # 사용자 기억(프로필) 읽기는 P1에서 추가한다.
        last = state["messages"][-1]
        if not isinstance(last, HumanMessage):
            return {}
        note = LANG_NOTE.format(language=LANG_NAME[state["lang"]])
        blocks = [{"type": "text", "text": last.text}, {"type": "text", "text": note}]
        return {"messages": [HumanMessage(content=blocks, id=last.id)]}

    def safety_check(state: AgentState) -> dict:
        # T1: 통과. 임신·수유·질환 언급 검사는 T6에서 추가한다.
        return {}

    def agent(state: AgentState) -> dict:
        response = bound.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    def route_after_agent(state: AgentState) -> Literal["tools", "finalize"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "finalize"

    def finalize(state: AgentState) -> dict:
        last = state["messages"][-1]
        try:
            reply = AgentReply.model_validate_json(last.text)
        except ValueError as error:
            raise AgentOutputError("응답이 응답 칸 형식에 맞지 않습니다") from error
        results = search_results(state["messages"])
        cards = assemble_cards(results, reply)
        exclusions = assemble_exclusions(results)
        return {
            "reply": reply.model_dump(),
            "cards": [c.model_dump() for c in cards],
            "exclusions": [e.model_dump() for e in exclusions],
        }

    graph = StateGraph(AgentState)
    graph.add_node("load_context", load_context)
    graph.add_node("safety_check", safety_check)
    graph.add_node("agent", agent)
    # 도구 예외는 모두 오류 메시지로 모델에 돌려준다(설계 문서 12장). 기본값은 일부 예외만 잡고
    # 나머지를 다시 던져, 결과 없는 tool_use가 기록에 남고 다음 턴이 400이 된다.
    graph.add_node("tools", ToolNode(list(tools), handle_tool_errors=True))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "safety_check")
    graph.add_edge("safety_check", "agent")
    graph.add_conditional_edges("agent", route_after_agent)
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
