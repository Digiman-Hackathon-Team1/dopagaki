# AGENTS.md

Python 백엔드와 TypeScript 프론트엔드로 구성된 모노레포다. 실행 방법은 `README.md`를 참고한다.

## 구조

- `apps/api`: FastAPI 백엔드. 의존성은 uv로 관리한다.
- `apps/web`: React + Vite 프론트엔드. 의존성은 pnpm으로 관리한다.
- `apps/worker`: web 컨시어지용 Cloudflare Worker. Claude API 키는 Worker secret에만 둔다.
- `packages/api-client`: FastAPI의 OpenAPI 스키마에서 생성한 타입과 클라이언트.

## 브랜치와 머지

- 작업 브랜치는 `develop`에서 만들고, PR의 기본 대상은 `develop`이다.
- `develop`에 머지하고 테스트한 뒤 `develop`을 `main`에 머지한다.
- 작업 브랜치를 `main`에 바로 머지하지 않는다.
- `develop`과 `main`에 직접 push하지 않는다.
- 머지는 사용자가 명시적으로 요청했을 때만 한다.

## 작업 규칙

- API 요청·응답을 바꾸면 `pnpm generate`를 실행하고 생성 결과를 같은 PR에 포함한다.
- `packages/api-client/openapi.json`과 `packages/api-client/src/schema.d.ts`는 직접 수정하지 않는다.
- web에서 `apps/api`를 호출할 때는 `@repo/api-client`를 사용한다. API 타입을 web에서 다시 정의하지 않는다.
- API 경로는 `/api`로 시작한다.
- TypeScript는 5.x를 유지한다. `openapi-typescript`가 5.x만 지원한다.

## 의존성 추가

- Python: `cd apps/api && uv add <package>`
- web: `pnpm --filter @repo/web add <package>`
- `pnpm-lock.yaml`과 `apps/api/uv.lock` 변경을 함께 커밋한다.

## 완료 전 확인

- `pnpm check`와 `pnpm build`를 통과해야 한다.
- 새 API 동작에는 `apps/api/tests`에 pytest 테스트를 추가한다.
- 화면을 바꿨으면 브라우저에서 확인한다.

## Git 네이밍

- 브랜치, 커밋, PR 제목은 `docs/git-naming-convention.md`를 따른다.
