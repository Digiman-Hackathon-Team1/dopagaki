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
| `pnpm --filter @repo/web fx:update` | 네이버 환율 스냅샷 갱신 (`pnpm build` 때도 실행) |
| `pnpm generate` | API 변경 후 TypeScript 타입 재생성 |

web 환경변수는 `apps/web/.env.example`을 참고합니다. web의 AI 상담 탭은 api의 `/api/agent/chat`으로 상담하고, api가 답하지 못하면(키 없음·오류·연결 실패) 시세표로 안내합니다.

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

## 배포 (Railway)

Railway 프로젝트 `dopagaki`에 서비스 두 개가 있다.

- web: https://web-production-841b18da.up.railway.app
- api: https://api-production-952f3404.up.railway.app (API 문서 `/docs`)

서비스 설정은 Railway에 직접 넣었다. Railway는 `railway.json` 설정 파일을 더 이상 받지 않는다.

| 서비스 | Dockerfile Path | Healthcheck | 변수 |
| --- | --- | --- | --- |
| `api` | `apps/api/Dockerfile` | `/api/health` | `PORT=8000`, `ANTHROPIC_API_KEY` |
| `web` | `apps/web/Dockerfile` | `/` | `API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` |

- 두 서비스 모두 Root Directory는 레포 루트다. Watch Paths로 해당 앱이 바뀔 때만 배포한다.
- web은 `/api` 요청을 내부 네트워크로 api 서비스에 넘긴다. 브라우저는 web 주소 하나만 쓰므로 CORS 설정이 필요 없다.
- web 빌드는 병원 가격표 이미지를 원본 레포에서 받아 포함한다.
- 선택 변수: web의 `VITE_POSTHOG_KEY`, `VITE_POSTHOG_HOST`, `VITE_AGENT_DEV` (빌드 때 들어간다).
- `VITE_AGENT_DEV=true`(소문자)로 web을 빌드하면 배포 사이트의 `/agent-dev` 주소가 실제 앱을 AI 상담 탭으로 연다(열린 뒤 주소는 `/`로 바뀌어서, 새로고침하면 "순위" 탭으로 열린다. AI 상담 탭으로 다시 열려면 `/agent-dev`로 들어온다). 이 값은 이 주소만 켜고 끈다. `/api/agent/chat`은 이 값과 상관없이 공개되어 있고 api의 `ANTHROPIC_API_KEY`로 Claude를 부르므로, 지출 한도를 건 별도 키를 쓴다. Claude 호출을 멈추려면 api의 `ANTHROPIC_API_KEY`를 지운다.

로컬에서 배포 이미지를 확인하려면:

```bash
docker build -f apps/api/Dockerfile -t dopagaki-api .
docker build -f apps/web/Dockerfile -t dopagaki-web .
```
