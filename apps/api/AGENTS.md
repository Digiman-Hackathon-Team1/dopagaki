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

## 에이전트 작업 규칙

`apps/api/src/api/agent`의 AI 상담 에이전트를 만들 때 따른다. 설계는 [노션 에이전트 설계 문서 v1.0](https://app.notion.com/p/423bf371be2d803288d0d8e8819739d4)에 있다.

### 기준 문서

1. 노션 설계 문서 v1.0
2. 작업 중인 이슈의 목표·범위·완료 기준
3. 코드

설계 문서와 이슈가 다르면 설계 문서를 따른다. 어느 쪽인지 판단이 서지 않으면 멈추고 사용자에게 묻는다.

### 범위

- 이슈 "범위"에 없는 기능, 리팩터링, 다른 앱(`apps/web`, `apps/worker` 등) 수정은 하지 않는다.
- 필요해 보이면 하지 말고 사용자에게 제안한다.

### 완료 판정

- 이슈의 완료 기준을 하나씩 확인하고, 그 결과를 PR 본문에 적는다.
- 실제 Claude로 확인하는 항목은 실제로 돌려 본 결과(입력과 응답)를 적는다.
- 테스트 통과만으로 완료 처리하지 않는다. 확인하지 못한 항목은 확인하지 못했다고 적는다.

### 설계 원칙

- 금액(가격, 엔화 환산, 절약액)은 도구 결과만 쓴다. LLM이 계산하거나 지어내지 않는다.
- 임신·질환·복용약 같은 건강 정보는 대화 기억과 사용자 기억에 저장하지 않는다.
- 응답 형식은 구조화 출력으로 보장한다. prefill(마지막 assistant 턴 미리 채우기)은 Sonnet 5.5·Haiku 5.5에서 400 오류라 쓰지 않는다.
- API 키 없이도 앱 import, `pnpm generate`, CI가 통과해야 한다. LLM 클라이언트는 요청 때 만든다.
- 도구 설명에는 필수 입력, 기본값, 쓰면 안 되는 경우를 적는다.
- CI 테스트는 가짜 LLM으로 한다. 실제 LLM 테스트는 키가 없으면 건너뛴다.

### PR

- `develop`에서 브랜치를 만들고 `develop` 대상으로 PR을 올린다. 머지는 사용자가 요청할 때만 한다.
- 커밋과 PR 제목 앞에 작업 번호를 붙인다. 예: `[T1] 에이전트 골격 추가`
- PR 본문에 `Closes #이슈번호`, 변경 요약, 완료 기준별 확인 결과를 적는다.

### 관련 문서

- 결정 기록: `docs/adr/`
- 작업 시작: `/todo-task` 스킬 (`.agents/skills/todo-task/SKILL.md`)
