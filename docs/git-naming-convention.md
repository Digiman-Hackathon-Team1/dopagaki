# Git 네이밍 컨벤션

브랜치, 커밋, PR 제목만 보고 어떤 이슈의 무슨 변경인지 알 수 있게 한다.

## 기본 원칙

- 작업 식별자는 GitHub 이슈 번호다. 브랜치와 PR 제목에는 이슈 번호를 넣는다.
- 커밋 메시지는 Conventional Commits 형식(`<type>(<scope>): <요약>`)을 쓴다.
- 요약은 별도 요청이 없으면 한국어로 쓴다.
- 하나의 PR은 가능한 한 하나의 이슈만 다룬다.
- 머지는 머지 커밋으로 한다(Squash를 쓰지 않는다). PR 안의 작업 단위별 커밋이 `develop`에 그대로 남는다.

## type

| type | 의미 | 예 |
| --- | --- | --- |
| `feat` | 새 기능 | API, 에이전트 도구 추가 |
| `fix` | 버그 수정 | 예외 처리 보완 |
| `refactor` | 동작 변경 없는 구조 개선 | 함수 분리 |
| `docs` | 문서만 수정 | README, ADR, AGENTS.md |
| `test` | 테스트만 추가·수정 | 테스트 케이스 보강 |
| `chore` | 설정, 의존성, CI, 스크립트 | lint 설정, 의존성 업데이트 |
| `hotfix` | 배포 장애 긴급 수정 | 장애 대응 패치 |

- `chore`는 남용하지 않는다. 기능이면 `feat`, 버그면 `fix`, 구조 개선이면 `refactor`를 먼저 고른다.
- 배포 장애 대응이 아니면 `hotfix` 대신 `fix`를 쓴다.

## scope

변경이 일어난 앱이나 모듈을 쓴다.

| scope | 위치 |
| --- | --- |
| `agent` | `apps/api/src/api/agent` |
| `api` | 에이전트 외 `apps/api` |
| `web` | `apps/web` |
| `worker` | `apps/worker` |
| `api-client` | `packages/api-client` |
| `repo` | 레포 전체 설정, 여러 앱에 걸친 변경 |
| `docs` | `docs/` |

## 브랜치

```text
<type>/<이슈번호>-<slug>
```

- `slug`는 영어 소문자와 하이픈만 쓰고, 구현 상세보다 작업 목적을 드러낸다.
- `develop`에서 만든다.

```text
feat/5-search-clinics
fix/9-thread-recovery
docs/13-memory-policy
```

## 커밋

```text
<type>(<scope>): <요약> (#<이슈번호>)
```

- 이슈 번호는 권장이다. 이슈와 관련된 커밋에는 붙인다.
- 요약은 명령형으로 짧게 쓰고, 마침표를 붙이지 않는다.
- 하나의 커밋에는 하나의 의도만 담는다.

```text
feat(agent): search_clinics 도구 추가 (#5)
fix(agent): LLM 호출 실패 뒤에도 대화가 이어지게 수정 (#4)
test(agent): 도구 예외 테스트 추가 (#4)
chore(api): langgraph 의존성 추가 (#4)
```

## PR 제목

```text
<type>(<scope>): <요약> [T<번호> #<이슈번호>]
```

- 작업 번호(T번호)가 있는 이슈는 T번호와 이슈 번호를 함께 쓴다. T번호가 없는 이슈는 `[#<이슈번호>]`만 쓴다.
- 제목만 읽어도 변경 목적이 드러나게 쓴다.

```text
feat(agent): 데이터·가격 계산과 search_clinics 추가 [T2 #5]
feat(agent): 실제 API 키로 대화되는 /api/agent/chat 골격 [T1 #4]
```

## PR 본문

- `Closes #<번호>`, `Fixes #<번호>`, `Refs #<번호>` 중 하나를 넣는다.
- 본문 구성은 각 작업 규칙(예: `todo-task` 스킬)의 양식을 따른다.

## 이슈 없는 작은 작업

문서 오탈자, 규칙 문서 정리처럼 추적할 가치가 낮은 작업만 `no-ticket`을 쓴다. 잦아지면 이슈를 먼저 만든다.
PR 제목에는 이슈 번호 대신 `[no-ticket]`을 쓰고, 본문에는 `Closes` 같은 이슈 연결을 넣지 않는다.

```text
브랜치: docs/no-ticket-git-naming
커밋:   docs(repo): Git 네이밍 컨벤션 추가
PR:     docs(repo): Git 네이밍 컨벤션 추가 [no-ticket]
```
