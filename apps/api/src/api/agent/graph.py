"""에이전트 그래프: load_context → safety_check → agent ⇄ tools → finalize (설계 문서 8장).

T1에서는 load_context와 safety_check가 그대로 통과하고, 도구는 T2부터 추가한다.
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
Write every field of your reply in {language}."""


class AgentOutputError(Exception):
    """Claude의 마지막 응답이 응답 칸 형식에 맞지 않을 때."""


class AgentState(MessagesState):
    lang: Lang
    reply: AgentReply | None


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
        # T1: 통과. 사용자 기억(프로필) 읽기는 P1에서 추가한다.
        return {}

    def safety_check(state: AgentState) -> dict:
        # T1: 통과. 임신·수유·질환 언급 검사는 T6에서 추가한다.
        return {}

    def agent(state: AgentState) -> dict:
        system = SystemMessage(SYSTEM_PROMPT.format(language=LANG_NAME[state["lang"]]))
        response = bound.invoke([system, *state["messages"]])
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
        return {"reply": reply}

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
