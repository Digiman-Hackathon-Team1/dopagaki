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
from pydantic import TypeAdapter

from api.agent.schemas import (
    AgentReply,
    BookingCard,
    BookingCardData,
    Card,
    Exclusion,
    JpMatchCard,
    JpMatchCardData,
    Lang,
    LocationCard,
    LocationCardData,
    RecommendationCard,
)
from api.agent.tools.get_booking_options import TOOL_NAME as GET_BOOKING_OPTIONS
from api.agent.tools.get_booking_options import GetBookingOptionsArtifact
from api.agent.tools.match_jp_treatment import TOOL_NAME as MATCH_JP_TREATMENT
from api.agent.tools.match_jp_treatment import MatchJpTreatmentArtifact
from api.agent.tools.resolve_location import TOOL_NAME as RESOLVE_LOCATION
from api.agent.tools.resolve_location import ResolveLocationArtifact, location_card
from api.agent.tools.search_clinics import TOOL_NAME as SEARCH_CLINICS
from api.agent.tools.search_clinics import SearchClinicsArtifact
from api.domain.locations import get_locations

LANG_NAME: dict[Lang, str] = {"ja": "Japanese", "ko": "Korean"}

SYSTEM_PROMPT = """\
You are Dopa, a consultation assistant for Japanese visitors and residents who want \
skincare and dermatology treatments in Seoul at prices cheaper than in Japan.
Help the user decide what treatment to get, when, where, and for how much, up to the \
clinic's booking channel. Do not diagnose and do not book or pay on the user's behalf.
Never state prices or savings that did not come from a tool result.
Clinics returned by search_clinics are shown to the user as cards. Give each card a \
one-line reason in card_reasons, by card_id. The app fills in the card's prices.
When the user names a concern instead of a treatment, call recommend_treatments first, \
explain the choice and any exclusions, then call search_clinics. When the user names a \
treatment by a Japanese name that is not in the search_clinics list (ハイフ, HIFU, ブナジュ, or \
its Korean spelling such as 하이후), call match_jp_treatment first and never map the name yourself. If it finds a match, \
say the Korean name and why the names differ before showing clinics. If it is ambiguous, ask \
which one; if there is no match, say so instead of offering a different treatment as the same. \
When the user names the Japanese clinic they went to, pass it as jp_clinic. When the user mentions \
where they stay or live, call resolve_location first and pass its location_id as origin_id. \
A location it finds is shown as a card; mention it in condition_summary.
When the user asks how to book a clinic (for example "the first clinic"), call \
get_booking_options with the place_id from that clinic's card_id in the earlier search_clinics \
result. Its booking channels and inquiry messages are shown as a card, so never write booking \
links yourself. You cannot book or pay; tell the user to book through the channel themselves.
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


CARDS = TypeAdapter(list[Card])


def read_cards(state: AgentState) -> list[Card]:
    """그래프 결과에서 이번 턴의 카드를 꺼낸다. 종류는 kind로 구분한다."""
    return CARDS.validate_python(state.get("cards") or [])


def _turn_start(messages: Sequence[BaseMessage]) -> int:
    return max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
    )


def _tool_results(messages: Sequence[BaseMessage], tool_name: str) -> list[ToolMessage]:
    return [
        m
        for m in messages
        if isinstance(m, ToolMessage) and m.name == tool_name and m.status != "error"
    ]


def _this_turn(messages: Sequence[BaseMessage], tool_name: str) -> list[ToolMessage]:
    """이번 턴(마지막 사용자 메시지 뒤)의 성공한 도구 결과."""
    return _tool_results(messages[_turn_start(messages) + 1 :], tool_name)


def _found_locations(messages: Sequence[BaseMessage]) -> list[LocationCardData]:
    found: list[LocationCardData] = []
    for message in _tool_results(messages, RESOLVE_LOCATION):
        location = ResolveLocationArtifact.model_validate(message.artifact).location
        if location:
            found.append(location)
    return found


def location_cards(
    messages: Sequence[BaseMessage], results: Sequence[SearchClinicsArtifact]
) -> list[LocationCardData]:
    """이번 턴에 보여 줄 기준 위치(설계 문서 4.3, ADR 0011).

    - 이번 턴에 병원을 검색했으면, 낸 검색 결과(`results`)가 쓴 기준 위치만 낸다. 위치 카드가 있는데
      병원 카드에 거리가 없는 일이 없게 하고, 앞 턴에 정한 위치로 검색해도 카드가 나오게 하기 위해서다.
    - 검색이 없었으면 이번 턴에 resolve_location이 찾은 위치를 낸다.
    - 같은 위치는 한 장이다. 사용자 표현(query)은 이번 턴, 없으면 가장 최근 턴에 찾은 결과의 것을 쓰고,
      어느 턴에서도 찾지 않은 ID면 null이다.
    """
    this_turn = _found_locations(messages[_turn_start(messages) + 1 :])
    if results:
        ids = [r.target.origin_id for r in results if r.target.origin_id]
    else:
        ids = [loc.location_id for loc in this_turn]
    earlier = _found_locations(messages)
    cards: list[LocationCardData] = []
    for location_id in dict.fromkeys(ids):
        card = next(
            (c for c in this_turn if c.location_id == location_id),
            next((c for c in reversed(earlier) if c.location_id == location_id), None),
        )
        location = get_locations().get(location_id)
        if card is None and location is not None:
            card = location_card(location, query=None)
        if card is not None:
            cards.append(card)
    return cards


def jp_match_cards(messages: Sequence[BaseMessage]) -> list[JpMatchCardData]:
    """이번 턴에 match_jp_treatment가 찾은 대응(설계 문서 4.3, 9장). 확인 필요·대응 없음은 카드가 없다.

    같은 시술을 두 번 찾으면(예: 일본 병원 이름을 붙여 다시 부름) 한 장만 내고, 마지막 결과를 쓴다.
    다만 일본 병원 가격이 있는 결과 뒤에 병원 없이 다시 찾으면 앞 결과를 둔다. 병원 카드의 직접 비교와
    대응 카드의 일본 병원 가격이 어긋나지 않게 하기 위해서다(Sonnet이 이렇게 다시 부르는 것을 확인했다).
    """
    found: dict[str, JpMatchCardData] = {}
    for message in _this_turn(messages, MATCH_JP_TREATMENT):
        match = MatchJpTreatmentArtifact.model_validate(message.artifact).match
        if match is None:
            continue
        earlier = found.get(match.menu_id)
        if earlier and earlier.jp_clinic and match.jp_clinic is None:
            continue
        found[match.menu_id] = match
    return list(found.values())


def booking_cards(messages: Sequence[BaseMessage]) -> list[BookingCardData]:
    """이번 턴에 get_booking_options가 만든 예약 안내(설계 문서 9장, 11.2).

    같은 병원을 두 번 부르면(예: 날짜를 넣어 다시 부름) 한 장만 내고 마지막 결과를 쓴다. 자리는 처음
    부른 순서를 지킨다. 병원이 다르면 모두 낸다.
    """
    found: dict[str, BookingCardData] = {}
    for message in _this_turn(messages, GET_BOOKING_OPTIONS):
        booking = GetBookingOptionsArtifact.model_validate(message.artifact).booking
        found[booking.place_id] = booking
    return list(found.values())


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
    results: list[SearchClinicsArtifact] = []
    for calls, message in enumerate(_this_turn(messages, SEARCH_CLINICS), start=1):
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
        locations = [
            LocationCard(**loc.model_dump(), kind="location")
            for loc in location_cards(state["messages"], results)
        ]
        jp_matches = [
            JpMatchCard(**m.model_dump(), kind="jp_match")
            for m in jp_match_cards(state["messages"])
        ]
        bookings = [
            BookingCard(**b.model_dump(), kind="booking")
            for b in booking_cards(state["messages"])
        ]
        cards = [*jp_matches, *locations, *assemble_cards(results, reply), *bookings]
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
