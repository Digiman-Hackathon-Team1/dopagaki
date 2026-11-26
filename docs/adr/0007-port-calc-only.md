# [ADR-0007] web 계산은 calc.ts 가격 계산만 Python으로 옮기고, 나머지는 스냅샷으로 받기

* **상태 (Status):** 채택됨(Accepted)
* **날짜 (Date):** 2026-11-26
* **관련 (Related):** T2 #5, ADR 0001, 설계 문서 4.7·13장

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** web에는 가격 계산(`apps/web/src/lib/calc.ts`)과 버튼식 상담 로직(`concierge.ts`의 `planConcierge`), 후기 평점 계산(`ai-rating.ts`)이 TypeScript로 있다. 에이전트는 Python(`apps/api`)이다.
* **문제:** 에이전트가 web 순위표와 같은 숫자를 내려면 web의 계산을 어떤 형태로 가져올지 정해야 한다. 회복 기간·바늘 여부(`concierge.ts` 상수)와 후기 평점(`ai-rating.ts`)은 JSON이 아니라 web 코드 안에만 있다.
* **목표:** web 순위표와 숫자가 같고, 같다는 것을 테스트로 확인할 수 있으며, web 파일은 고치지 않는다.

## 2. 고려한 옵션들 (Considered Options)

### 1. calc.ts 가격 계산만 포팅하고, web 코드에만 있는 값은 스냅샷으로 내보냄

`calc.ts`의 가격 계산(부가세, 엔화 환산, 절약액, 거리, 정렬)을 `apps/api/src/api/domain/calc.py`로 옮긴다. 회복 기간·바늘 여부·후기 평점은 Node 스크립트가 web 코드를 import해 스냅샷 JSON에 덧붙인다. 같은 스크립트가 `calc.ts`로 기대값을 만들어 pytest가 대조한다.

**Pros**
* 옮기는 코드가 작다(가격 계산 함수 몇 개).
* `calc.ts` 기대값과 행 단위로 대조해 숫자가 같음을 CI에서 확인한다.
* 후기 평점(635줄)처럼 큰 계산은 옮기지 않고 web 코드가 낸 값을 그대로 쓴다.

**Cons**
* web 데이터나 계산이 바뀌면 스크립트를 다시 돌려야 한다.

### 2. planConcierge를 통째로 포팅

버튼식 상담의 추천 로직(예산·일정 맞춤, 묶음 추천)까지 Python으로 옮긴다.

**Pros**
* 버튼식 상담과 같은 추천 결과가 나온다.

**Cons**
* 버튼식 상담은 에이전트가 대체한다(ADR 0002). 고정 선택지(예산 4단계, 일정 3단계)에 맞춘 로직이라 대화형 조건과 맞지 않는다.
* 옮길 코드와 대조할 경우가 크게 늘어난다.

### 3. API가 실행 중에 web TypeScript를 호출

Node 프로세스나 별도 서비스로 web 계산을 그대로 실행한다.

**Pros**
* 계산 코드가 하나다.

**Cons**
* API 배포에 Node 런타임과 web 소스가 필요하다. 배포 Dockerfile은 `apps/api`만 담는다.
* 호출 경로와 장애 지점이 늘어난다.

## 3. 결정 사항 (Decision Outcome)

* **`calc.ts` 가격 계산만 Python으로 옮기고, web 코드에만 있는 값은 스냅샷으로 내보낸다.**
* **`planConcierge` 통째 포팅과 실행 중 TypeScript 호출은 채택하지 않는다.**

| 파일 | 역할 |
| --- | --- |
| `apps/api/scripts/export-web-snapshot.mts` | web 데이터·코드를 읽어 아래 두 파일을 만든다. 실행: `pnpm --filter @repo/api export:web-data` |
| `apps/api/src/api/domain/data/web-snapshot.json` | `prices.json` + 환율(`naver-fx.json`) + 회복 기간·바늘 여부(`concierge.ts`) + 후기 평점(`ai-rating.ts`) |
| `apps/api/tests/fixtures/calc-expected.json` | `calc.ts buildRankedRows`로 만든 기대값(시술별 전체 행, 정렬·위치·필터 조합별 순서) |
| `apps/api/src/api/domain/catalog.py` | 스냅샷 조회 계층. DB 전환 시 `get_catalog` 구현만 바꾼다(ADR 0001) |
| `apps/api/src/api/domain/calc.py` | 가격 계산 |

* 스냅샷은 `apps/api` 안에 둔다. 배포 Dockerfile이 `apps/api/src`만 복사하므로 실행 중에 `apps/web` 경로를 읽을 수 없다.
* 스냅샷에는 만들 때 읽은 원본(web의 `prices.json`, `calc.ts`, `concierge.ts`, `ai-rating.ts`, 후기 데이터 2개, 그리고 내보내기 스크립트 자신)의 SHA-256을 기록한다. 줄바꿈을 LF로 맞춘 뒤 해시해 Windows에서 CRLF로 받아도 같은 값이 나온다. `tests/test_catalog.py`가 이 해시와 web `prices.json` 내용을 대조해, 원본이 바뀌었는데 다시 내보내지 않았으면 실패한다.
* 환율(`naver-fx.json`)은 `pnpm build`마다 갱신되므로 대조하지 않는다. 스냅샷의 환율은 web을 읽지 못할 때 쓰는 마지막 대체 값이다. 평소에는 배포된 web의 `naver-fx.json`을 읽는다(ADR 0010).
* 반올림은 JavaScript `Math.round`(0.5는 올림)와 같게 하고, 각도 변환도 `calc.ts`와 같은 식을 써 거리의 마지막 자리까지 맞춘다. 병원과 시술의 category 일치 거르기도 옮긴다(지금 데이터는 모두 `beauty`).

**이유**
* 숫자가 web과 같아야 하는 범위는 가격 계산뿐이고, 그 범위만 옮기면 대조 테스트로 일치를 보장할 수 있다.
* 후기 평점과 시술 상수는 web 코드가 원본이므로, 옮겨 적는 것보다 web 코드가 낸 값을 받는 쪽이 어긋나지 않는다.
* API 배포에 web 소스와 Node 런타임을 넣지 않는다.

**후속 작업**
* T5 #8 예약 채널 등 나머지 `.ts` 데이터도 같은 스크립트로 내보낸다
* 가격 계산을 API 공용 모듈로 둘지는 이우진 님이 결정한다(설계 문서 15장)
