# [ADR-0006] 단일 도구 호출 루프(LangGraph, apps/api) 채택

* **상태 (Status):** 채택됨(Accepted)
* **날짜 (Date):** 2026-11-26
* **관련 (Related):** T1 #4

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** 에이전트는 추천, 일본 시술명 대응, 예약 연결, 위치 해석을 한 대화 안에서 처리한다. 백엔드는 `apps/api`(Python, FastAPI)이고, LLM은 Claude(Sonnet 5.5 또는 Haiku 5.5)를 쓴다.
* **문제:** 에이전트 구조(단일 루프인지 여러 에이전트인지)와 Claude 호출 방식(LangChain 경유인지 Anthropic SDK 직접 호출인지)을 정해야 이후 도구 작업(T2~T6)이 같은 틀 위에서 진행된다.
* **목표:** P0 일정(11/27 점심) 안에 도구를 붙여 나갈 수 있고, 안전 검사처럼 모델에게 맡기면 안 되는 단계를 항상 실행할 수 있는 구조를 고른다.

## 2. 고려한 옵션들 (Considered Options)

### 1. 단일 도구 호출 루프 (LangGraph)

Claude 하나가 도구를 고르고, 결과를 보고 다음 행동을 정하는 루프 하나로 처리한다. 앞뒤에 코드 노드를 둔다.

```text
load_context → safety_check → agent ⇄ tools → finalize
```

**Pros**
* 흐름이 단순해 디버깅과 테스트가 쉽다. 가짜 LLM으로 노드 흐름을 검사할 수 있다.
* `safety_check`, `finalize` 같은 코드 노드를 모델 선택과 무관하게 항상 실행한다.
* 체크포인터로 대화 ID별 대화 기억을 바로 쓸 수 있다.

**Cons**
* 도구가 많아지면 한 모델이 고를 도구 목록이 길어진다.

### 2. 멀티 에이전트 (기능별 하위 에이전트와 조정자)

추천, 일본 시술명, 일정 등 기능별 에이전트를 두고 조정자가 나눠 맡긴다.

**Pros**
* 기능별 프롬프트를 따로 다듬을 수 있다.

**Cons**
* LLM 호출이 늘어 비용과 응답 시간이 커진다.
* 에이전트 사이에서 조건(예산, 일정)을 넘기는 설계가 추가로 필요하다.
* 도구가 10개 안팎인 지금 규모에서는 나눌 이유가 없다.

## 3. 결정 사항 (Decision Outcome)

* **단일 도구 호출 루프를 LangGraph로 `apps/api/src/api/agent`에 만든다.**
* **멀티 에이전트 구조는 쓰지 않는다.**
* **Claude는 `langchain-anthropic`의 `ChatAnthropic`으로 호출한다.** 아래 확인 결과 필요한 설정이 요청에 그대로 들어가므로 Anthropic SDK를 직접 호출하지 않는다.

**이유**
* 도구 수와 일정에 비해 멀티 에이전트의 비용(호출 수, 조건 전달 설계)이 크다.
* 안전 검사와 응답 칸 조립을 그래프 노드로 고정할 수 있다(설계 문서 8장, 9장).
* `ChatAnthropic`을 쓰면 LangGraph의 도구 실행(`ToolNode`)과 체크포인터를 그대로 쓸 수 있다.

### `langchain-anthropic` 확인 결과

`langchain-anthropic` 1.7.13, `langchain-core` 1.8.5, `anthropic` 2.14.0 기준이다. 실제 요청을 보내지 않고, `ChatAnthropic`이 만드는 요청 본문을 Sonnet 5.5와 Haiku 5.5에 대해 확인했다.

| 확인 항목 | 결과 |
| --- | --- |
| effort | `effort="medium"`이 `output_config.effort`로 들어간다 |
| 구조화 출력 | `method="json_schema"` 또는 `response_format`이 `output_config.format`(json_schema)으로 들어간다 |
| 도구 + 구조화 출력 | `bind_tools(tools, response_format=...)`로 한 요청에 `tools`와 `output_config.format`이 함께 들어간다 |
| 도구 강제 호출 | Sonnet 5.5는 `tool_choice` any/tool을 거절한다. 라이브러리가 이를 알고 있어 Sonnet 5.5에는 보내지 않는다. 기본 구조화 출력 방식(`function_calling`)은 Haiku 5.5에 강제 호출을 보내므로 쓰지 않고 `json_schema`를 쓴다 |
| thinking | Sonnet 5.5에는 `thinking: adaptive`가 자동으로 붙는다. 대화 기록의 thinking 블록(서명 포함)은 바뀌지 않고 다시 전송된다 |
| max_tokens | 기본값이 128000이라 스트리밍이 아닌 요청에 맞게 16000으로 지정한다 |

* 도구가 없을 때 `bind_tools([])`는 빈 `tools` 배열을 보내므로, 응답 형식만 붙이는 `bind(response_format=...)`를 쓴다.
* 실제 API가 이 요청을 받아들이는지는 API 키로 Swagger에서 대화해 확인한다. 거절되는 설정이 있으면 그 노드만 Anthropic SDK 직접 호출로 바꾸고 이 ADR을 고친다.

**후속 작업**
* T2 #5부터 `tools/`에 도구를 추가해 같은 루프에 붙인다
* T6 #9 실행 단계 수 제한, 실패 시 시세표 안내, 응답 거절(`refusal`) 처리
* T7 #10 Sonnet 5.5와 Haiku 5.5 비교 후 기본 모델 결정
