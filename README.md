# dopagaki

Python 백엔드와 TypeScript 프론트엔드를 하나의 레포에서 관리하는 모노레포입니다.

## 구성

| 경로 | 역할 | 기술 |
| --- | --- | --- |
| `apps/api` | 백엔드 API | Python 3.12, FastAPI, uv |
| `apps/web` | 웹 프론트엔드 | TypeScript, React, Vite |
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
| `pnpm generate` | API 변경 후 TypeScript 타입 재생성 |

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
