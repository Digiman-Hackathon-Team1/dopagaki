# API 작업 규칙

FastAPI 백엔드다. 공통 규칙은 루트 `AGENTS.md`를 따른다.

## DTO 공유

API 계약의 원본은 Pydantic 모델이다. web 타입은 OpenAPI에서 생성한다.

```text
Pydantic 모델 → FastAPI OpenAPI → openapi.json → schema.d.ts → @repo/api-client → web
```

1. 요청·응답 DTO를 Pydantic 모델로 정의하거나 수정한다.
2. 레포 루트에서 `pnpm generate`를 실행한다.
3. 생성된 `openapi.json`과 `schema.d.ts`를 같은 PR에 커밋한다. pre-push hook이 다시 생성해 커밋되지 않은 변경이 있으면 push를 막고, CI도 다르면 실패한다.
4. `pnpm check`로 web과 타입이 맞는지 확인한다.

web에서는 생성된 client와 타입을 쓰고 같은 DTO를 다시 정의하지 않는다.

```ts
import { api, type Schemas } from "@repo/api-client";

type Health = Schemas["Health"];
const { data, error } = await api.GET("/api/health");
```

## DTO 작성

- 요청 body와 응답은 이름 있는 `BaseModel`로 정의한다. `dict`와 `Any`를 쓰지 않는다.
- endpoint에는 반환 타입과 고유한 `operation_id`를 지정한다.
- 정해진 값은 `Literal` 또는 `StrEnum`으로 정의한다.
- 요청과 응답의 필드가 다르면 모델을 나눈다. 예: `CreateBookingRequest`, `Booking`.
- JSON key는 camelCase로 통일한다. Python 필드는 snake_case로 쓰고 Pydantic `alias_generator=to_camel`과 `validate_by_name=True`를 사용한다.
- 시각은 시간대가 있는 `AwareDatetime`을 쓴다.
- 비밀키와 내부 전용 필드를 응답 모델에 넣지 않는다.
- 필드 삭제·이름 변경·필수화는 호환이 깨지는 변경이다. web 수정을 같은 PR에 포함한다.

OpenAPI 타입은 컴파일 시점 검사다. 요청 검증은 서버의 Pydantic이 맡는다.

## 구조와 검증

- 경로는 `/api`로 시작한다.
- 새 endpoint에는 `tests/`에 정상·오류 응답 테스트를 추가한다.
- 의존성은 `uv add <package>`로 추가하고 `uv.lock`을 함께 커밋한다.
- 개발 서버의 API 문서(Swagger UI): http://localhost:8000/docs
- 구조 규칙은 문서보다 검사가 먼저다. `pnpm check`가 다음을 막으니, 실패하면 검사를 끄지 말고 코드를 고친다. 규칙을 바꿀 때는 검사도 같은 PR에서 바꾼다.
  - 계층 방향(router → service → graph → tools), router의 graph·tools·domain 직접 호출, domain의 FastAPI·LLM 의존: `uv run lint-imports`(`pyproject.toml`의 `tool.importlinter`)
  - SSE 직접 구현(`StreamingResponse`, `sse_starlette`): ruff `TID251`
  - 서비스의 HTTP import(`Depends` 외), `get_fx_provider()`를 거치지 않는 `FxProvider`, `alembic.ini`의 ASCII 아닌 문자: `tests/test_architecture.py`
  - 스트림 금액 검증: `tests/test_agent_stream.py`

## 에이전트 작업 규칙

`apps/api/src/api/agent`의 AI 상담 에이전트를 만들 때 따른다. 설계는 [노션 에이전트 설계 문서](https://app.notion.com/p/423bf371be2d803288d0d8e8819739d4)에 있다.

### 기준 문서

1. 노션 설계 문서
2. 작업 중인 이슈의 목표·범위·완료 기준
3. 코드

설계 문서와 이슈가 다르면 설계 문서를 따른다. 어느 쪽인지 판단이 서지 않으면 멈추고 사용자에게 묻는다.

설계를 바꿔야 하면 사용자에게 알린다. 승인받아 설계 문서를 고치면 하위 페이지 [설계 변경 기록](https://app.notion.com/p/424bf371be2d81dda111e3a9b4583311)에 변경 전, 변경 후, 이유, 근거, 관련(ADR·PR·이슈)을 한 건 남긴다. 선택지를 비교한 큰 결정은 `docs/adr/`에도 남긴다.

### 범위

- 이슈 "범위"에 없는 기능은 구현하지 않는다. 설계 문서에 있는데 작업을 나눌 때 빠져 어느 이슈에도 없는 항목은, 해당 이슈 "범위"에 추가한 뒤 작업한다.
- 범위에 없는 리팩터링은 하지 않고 사용자에게 제안한다.

### 완료 판정

- 이슈의 완료 기준을 하나씩 확인하고, 그 결과를 PR 본문에 적는다.
- 실제 Claude로 확인하는 항목은 실제로 돌려 본 결과(입력과 응답)를 적는다.
- 테스트 통과만으로 완료 처리하지 않는다. 확인하지 못한 항목은 확인하지 못했다고 적는다.

### 설계 원칙

- 금액(가격, 엔화 환산, 절약액)은 도구 결과만 쓴다. LLM이 계산하거나 지어내지 않는다.
- 임신·질환·복용약 같은 건강 정보와 안전 판정 결과는 API 응답(조건 요약, 주의사항, 카드 등)에 담지 않고, 사용자 기억(프로필)에 저장하지 않는다. 추천을 멈춘 이유와 이번 여행에서 할 일은 답변에 쓴다. 안전 정지 턴의 다음 질문 제안은 사용자가 이어 물을 문장이라 건강 단어를 허용한다. 대화 기억에는 안전 정지를 유지하기 위해 남는다.
- 응답 형식은 구조화 출력으로 보장한다. prefill(마지막 assistant 턴 미리 채우기)은 Sonnet 5.5·Haiku 5.5에서 400 오류라 쓰지 않는다.
- API 키 없이도 앱 import, `pnpm generate`, CI가 통과해야 한다. LLM 클라이언트는 요청 때 만든다.
- 도구 설명에는 필수 입력, 기본값, 쓰면 안 되는 경우를 적는다.
- CI 테스트는 가짜 LLM으로 한다. 실제 LLM 테스트는 키가 있고 `AGENT_LIVE_TESTS=1`일 때만 실행한다.

### PR

- `develop`에서 브랜치를 만들고 `develop` 대상으로 PR을 올린다. 머지는 사용자가 요청할 때만 한다.
- 브랜치, 커밋, PR 제목은 `docs/git-naming-convention.md`를 따른다. 예: PR 제목 `feat(agent): 데이터·가격 계산과 search_clinics 추가 [T2 #5]`
- PR 본문에 `Closes #이슈번호`, 변경 요약, 완료 기준별 확인 결과를 적는다.

### 관련 문서

- 결정 기록: `docs/adr/`
- 작업 시작: `/todo-task` 스킬 (`.agents/skills/todo-task/SKILL.md`)

## 백엔드 구조 규칙

`apps/api` 전체(에이전트, 가격 조회, DB)에 적용한다.

### 계층

- 라우터(`router.py`)는 요청을 받아 서비스를 부르고 응답을 돌려주기만 한다. 대화 흐름, 데이터 조회, 응답 변환은 같은 폴더의 `service.py`에 둔다. 예: `agent/service.py`의 `ChatService`, `pricing/service.py`의 `PricingService`.
- 서비스는 `Depends(get_<이름>_service)`로 라우터에 넣는다. 테스트는 이 함수나 서비스가 쓰는 값을 바꿔 끼운다.
- 금액·거리 계산은 `api/domain`에 둔다(`calc.py`는 web `calc.ts`와 같은 식). 서비스와 도구는 계산식을 새로 쓰지 않고 domain 함수를 부른다.
- 서비스는 HTTP를 모른다. 없는 대상은 서비스 고유 예외로 알리고, 라우터가 HTTP 상태(404 등)로 바꾼다.

### 응답 스트리밍

- SSE는 직접 구현하지 않는다. FastAPI 내장 `fastapi.sse.EventSourceResponse`(`response_class`)를 쓰고, LLM 토큰은 LangGraph `stream(stream_mode="messages")`로 받는다.
- 이벤트는 `type`으로 구분하는 Pydantic 모델이다(`start` → `delta` 0개 이상 → `done`). `done`에는 일반 응답과 같은 모델을 담는다. 실패는 HTTP 오류가 아니라 `done`의 시세표 안내(fallback)로 보낸다.
- 금액 검증은 스트림에도 적용한다. 도구 결과에 없는 금액이 든 문장은 `delta`로 나가지 않는다.

### 병원·가격 데이터

- 병원·가격 데이터는 `get_catalog()`로만 읽는다. 도구와 서비스는 `Catalog`를 받고 DB나 JSON 파일을 직접 읽지 않는다(ADR 0001).
- 데이터 출처는 `CATALOG_SOURCE`로 고른다. `snapshot`(기본)은 web 스냅샷 JSON, `db`는 `DATABASE_URL`의 DB다. DB로 쓰기 전에 `uv run python -m api.db.seed`로 마이그레이션과 데이터를 넣는다. 크롤링하지 않는다.
- 테이블을 바꾸면 Alembic 마이그레이션을 같은 PR에 추가한다(`uv run alembic revision --autogenerate -m "<설명>"`). `tests/test_db_models.py`의 모델·마이그레이션 비교가 통과해야 한다.
- `alembic.ini`에는 ASCII만 쓴다. Windows에서 Alembic이 OS 인코딩(cp949)으로 읽어 한글이 있으면 깨진다.
- 환율은 `get_fx_provider()` 하나로 읽는다. 에이전트 답변과 가격 조회 API의 금액이 같아야 한다.
