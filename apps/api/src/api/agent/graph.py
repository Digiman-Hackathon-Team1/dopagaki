"""에이전트 그래프: load_context → safety_check → agent ⇄ tools → finalize (설계 문서 8장).

T1에서는 safety_check가 그대로 통과하고, 도구는 T2부터 추가한다.

Sonnet 5.5·Haiku 5.5는 이전 턴의 thinking 블록을 그 블록이 만들어질 때와 앞부분이 같은
대화에서만 받아들인다. 그래서 대화 기록은 덧붙이기만 하고, 시스템 프롬프트는 턴마다 바뀌는
값(응답 언어 등)을 넣지 않은 고정 문장으로 둔다. 턴마다 바뀌는 지시는 그 턴의 사용자 메시지
뒤에 시스템 메시지로 덧붙여 기록에 남긴다.
"""

from collections.abc import Sequence
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from api.agent.schemas import AgentReply, Lang

LANG_NAME: dict[Lang, str] = {"ja": "Japanese", "ko": "Korean"}

SYSTEM_PROMPT = """\
You are Dopa, a consultation assistant for Japanese visitors and residents who want \
skincare and dermatology treatments in Seoul at prices cheaper than in Japan.
Help the user decide what treatment to get, when, where, and for how much, up to the \
clinic's booking channel. Do not diagnose and do not book or pay on the user's behalf.
Never state prices or savings that did not come from a tool result.
Write every field of your reply in the language named by the latest system note."""

LANG_NOTE = "Reply language for this turn: {language}."


class AgentOutputError(Exception):
    """Claude의 마지막 응답이 응답 칸 형식에 맞지 않을 때."""


class AgentState(MessagesState):
    lang: Lang
    # 대화 기억에 저장되므로 모델 객체 대신 일반 데이터로 둔다. 읽을 때는 read_reply를 쓴다.
    reply: dict | None


def read_reply(state: AgentState) -> AgentReply:
    """그래프 결과에서 이번 턴의 응답 칸을 꺼낸다."""
    return AgentReply.model_validate(state["reply"])


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
        # 이번 턴의 응답 언어를 사용자 메시지 뒤에 덧붙인다. 사용자 기억(프로필) 읽기는 P1에서 추가한다.
        note = LANG_NOTE.format(language=LANG_NAME[state["lang"]])
        return {"messages": [SystemMessage(note)]}

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
        return {"reply": reply.model_dump()}

    graph = StateGraph(AgentState)
    graph.add_node("load_context", load_context)
    graph.add_node("safety_check", safety_check)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(list(tools)))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "safety_check")
    graph.add_edge("safety_check", "agent")
    graph.add_conditional_edges("agent", route_after_agent)
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
