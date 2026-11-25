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
