# dopagaki

Python 백엔드와 TypeScript 프론트엔드를 하나의 레포에서 관리하는 모노레포입니다.

## 구성

| 경로 | 역할 | 기술 |
| --- | --- | --- |
| `apps/api` | 백엔드 API | Python 3.12, FastAPI, uv |
| `apps/web` | 웹 프론트엔드 | TypeScript, React, Vite |
| `apps/worker` | 컨시어지 AI 중계 서버 | TypeScript, Cloudflare Workers |
| `packages/api-client` | API 타입과 클라이언트 | OpenAPI에서 생성 |

JavaScript 의존성은 pnpm workspace, Python 의존성은 uv로 관리하고, 전체 작업은 Turborepo로 실행합니다.

## 시작하기

필요한 도구: Node.js 22.12+, pnpm, uv

```bash
pnpm install
uv sync --project apps/api
pnpm dev
```

- web: http://localhost:5173 (`/api` 요청은 API로 전달)
- API 문서: http://localhost:8000/docs

## 명령

| 명령 | 설명 |
| --- | --- |
| `pnpm dev` | API와 web 개발 서버 실행 |
| `pnpm check` | 린트, 타입 검사, 테스트 |
| `pnpm build` | web 빌드 |
| `pnpm --filter @repo/web fx:update` | 네이버 환율 스냅샷 갱신 (`pnpm build` 때도 실행) |
| `pnpm generate` | API 변경 후 TypeScript 타입 재생성 |

web 환경변수는 `apps/web/.env.example`, Worker 설정은 `apps/worker/README.md`를 참고합니다. Worker 없이도 web은 규칙 기반으로 동작합니다.

병원 가격표 이미지(약 150MB)는 Git에서 제외합니다. 이미지가 필요하면 받아 옵니다. 없으면 가격표 이미지가 보이지 않고 이미지 파일 검사는 건너뜁니다.

```bash
pnpm --filter @repo/web proofs:pull
```

`pnpm install`이 pre-push hook을 설정합니다. 이미 의존성을 설치한 클론에서는 `pnpm run prepare`를 한 번 실행하세요. push할 때 API 타입을 다시 생성하고, 바뀐 결과가 있으면 push를 멈춥니다. 생성된 파일을 커밋한 뒤 다시 push하세요.

한 앱만 실행하려면 `--filter`를 붙입니다.

```bash
pnpm dev --filter=@repo/web
```

## 브랜치와 머지

```text
작업 브랜치 → develop → 테스트 → main
```

1. `develop`에서 작업 브랜치를 만듭니다.
2. `develop` 대상으로 PR을 만들고, CI가 통과하면 머지합니다.
3. `develop`에서 머지된 변경을 테스트합니다.
4. 테스트가 끝나면 `develop`에서 `main`으로 PR을 만들어 머지합니다.

작업 브랜치를 `main`에 바로 머지하지 않습니다.
