# 에이전트 도구 사이 왕복 축소 전후 측정 (Haiku 5.5)

- 작업: T27 [#72](https://github.com/Digiman-Hackathon-Team1/dopagaki/issues/72) 도구 사이 모델 왕복 축소
- 날짜: 2026-11-28
- 모델: `claude-haiku-5-5` (effort `medium`, 프롬프트 캐싱 활성)
- 실행 스크립트: [`apps/api/scripts/measure_turn_cost.py`](../../apps/api/scripts/measure_turn_cost.py)
- 측정 파일: `72-before.jsonl`(커밋 1655ca1), `72-after2.jsonl`(커밋 5a20578), `72-after3.jsonl`(커밋 9a173f9)

## 1. 요약

- **시술명+위치를 한 번에 말한 요청(s8)**: 모델 호출이 3번→2번, 완료 시간 11.1초→9.6초, 비용 25% 감소.
- **고민→위치 순서 요청(s2)**: 모델 호출 수 변화 없음. `resolve_location` 왕복이 `search_clinics(location=...)` 한 번으로 대체됨.
- **고민+위치 첫 턴에 같이 말한 요청(s9)**: `resolve_location` 왕복 제거 확인. 호출 수는 여전히 3번(추천→위치+검색→답변 최소 구조). 제외 시술 언급 품질 50%→90%.
- 기존 s1·s3·s5 채점과 캐시 적중률은 변화 없음.

## 2. 변경 내용

1. **대본·측정 추가**: 시술+위치(s8), 고민+기간+위치 한 턴(s9), 일본 시술명 모호(s10) 시나리오. 호출별·도구별 시간 측정.
2. **프롬프트 정리**: 서로 결과가 필요 없는 도구는 한 응답에서, 위치를 말하면 `search_clinics(location=...)`으로 넘기도록 지시 통일.
3. **`search_clinics`에 `location` 입력 추가**: 도구 안에서 위치를 찾아 거리를 계산. `resolve_location`을 별도 호출하지 않아도 됨.
4. **제외 시술 명시 프롬프트 보강**: `recommend_treatments` 결과의 제외 항목을 이름·이유와 함께 답변에 반드시 쓰도록 명시.

## 3. 시나리오별 측정 결과

### 3.1 s8 — 시술명+위치 (변경 전 vs after2)

| | 호출 수 | 첫 글자 중앙값 | 완료 중앙값 | 비용 |
|---|---|---|---|---|
| before | 3번 | 8.2초 | 11.1초 | $0.00086 |
| after2 | **2번** | 4.6초 | **9.6초** | **$0.00064** |

before 도구 순서: `resolve_location` → (Claude) → `search_clinics` → (Claude) → 답변  
after 도구 순서: `search_clinics(location="강남역")` → (Claude) → 답변

### 3.2 s2 2턴째 — 고민 먼저, 위치 나중 (변경 전 vs after3)

| | 호출 수 | 완료 중앙값 | 도구 목록 |
|---|---|---|---|
| before | 3번 | 9.6초 | recommend + **resolve_location** + search |
| after3 | 3번 | 9.6초 | recommend + search |

호출 수는 같지만 `resolve_location` 왕복이 제거됨. 위치는 `search_clinics(location=...)` 한 번으로 처리.

### 3.3 s9 — 고민+기간+위치 한 턴 (변경 전 vs after3)

| | 호출 수 | 채점 통과 | 도구 목록 |
|---|---|---|---|
| before | 3번 | 1/2 | recommend + resolve + search |
| after3 | 3번 | **9/10** | recommend + resolve + search |

`resolve_location` 왕복은 남아 있으나, 추천(recommend) 뒤 위치+검색이 필요해 최소 3번 구조임.  
품질 개선: 제외 시술 언급 프롬프트 보강으로 50%→90%.

## 4. 채점 실패 항목

남은 실패는 모두 모델 불안정성(가끔 도구를 안 부르거나 항목을 빠뜨림)으로 이번 변경과 무관하다.

| 항목 | before | after | 비고 |
|---|---|---|---|
| s9 인모드 미언급 | 1/2 | 1/10 | 프롬프트 보강으로 10회 중 1회로 감소 |
| s2 도구 미호출 | 0/4 | 2/20 | 10% 수준, 기존부터 있던 불안정성 |
| s5 건강 정보 필터 | 3/4 | - | 이번 범위 외 |
