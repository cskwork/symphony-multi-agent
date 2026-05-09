---
id: PIPELINE-DEMO
identifier: PIPELINE-DEMO
title: Reference ticket showing the Plan / Review / QA / Done shape
state: Done
priority: 3
labels:
- demo
- docs
created_at: '2026-05-09T20:00:00Z'
updated_at: '2026-05-09T20:00:00Z'
---

This ticket is a worked example. It illustrates the artefacts the agent
should produce as it walks the production pipeline. Real tickets follow
this same shape but with project-specific content.

## Plan

- 작업 범위: `/api/v1/refresh` 엔드포인트의 응답 캐시 헤더를 `Cache-Control: no-store`로 변경.
- 변경 파일: `src/symphony/server.py` (1 곳), `tests/test_server.py` (테스트 1건 추가).
- 가장 먼저 추가할 실패 테스트: `test_refresh_sets_no_store_cache_header`.

## Implementation

- `src/symphony/server.py:142` — `_handle_refresh()` 응답에 `Cache-Control: no-store` 헤더 추가.
- `tests/test_server.py:88` — 응답 헤더를 검증하는 회귀 테스트 추가.
- 그 외 변경 없음. 다른 핸들러는 영향받지 않도록 의도적으로 좁게 수정.

## Review

- LOW | `src/symphony/server.py:142` | 헤더 키를 상수로 빼는 것이 깔끔하나, 단일 사용처라 인라인 유지 (간결성 우선). 액션 없음.
- HIGH | 없음.
- CRITICAL | 없음.

## QA Evidence

```
$ pytest -q tests/test_server.py
....                                                                     [100%]
4 passed in 0.42s
exit code: 0

$ curl -i -X POST http://127.0.0.1:9999/api/v1/refresh
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
...
exit code: 0

$ python scripts/diff_refresh_response.py --baseline main --candidate HEAD
- Cache-Control: max-age=0
+ Cache-Control: no-store
exit code: 0
```

artefacts: `qa-artifacts/refresh-response-tobe.json`,
            `qa-artifacts/refresh-response-asis.json`.

## As-Is -> To-Be Report

### As-Is
- `/api/v1/refresh` 응답이 `Cache-Control: max-age=0`으로 내려가 일부 프록시
  계층(특히 사내 NGINX)이 짧게 캐시. 운영자가 강제 새로고침해도 같은 페이로드를
  수 초간 재사용하는 사례가 보고됨 (log/symphony.log 2026-05-08 14:22 부근).

### To-Be
- 동일 엔드포인트가 `Cache-Control: no-store`를 반환. 프록시 캐시 우회가
  보장되며 강제 새로고침 시 항상 최신 폴링 결과가 반영됨. 위 QA 단계의
  curl 출력으로 확인.

### Reasoning
- 가장 좁은 변경으로 문제 해결 (헤더 한 줄). 캐시 정책 전반을 손대지 않은 것은
  다른 엔드포인트가 의도적으로 캐시 가능 상태이기 때문.
- 대안으로 `Pragma: no-cache` 동시 부착도 검토했으나, HTTP/1.1 환경에서는
  `Cache-Control`만으로 충분하고 헤더 중복은 디버깅을 흐림.
- 후속: `/api/v1/state`도 동일 이슈 가능성이 있으나 별도 티켓에서 다룸
  (TASK-NEXT 에 기록 예정).

### Evidence
- 명령: `pytest -q tests/test_server.py` (rc=0),
  `curl -i -X POST http://127.0.0.1:9999/api/v1/refresh` (rc=0),
  `python scripts/diff_refresh_response.py --baseline main --candidate HEAD` (rc=0).
- 테스트: `tests/test_server.py::test_refresh_sets_no_store_cache_header`.
- 아티팩트: `qa-artifacts/refresh-response-asis.json`,
              `qa-artifacts/refresh-response-tobe.json`.
- 관련 로그: `log/symphony.log` 2026-05-08 14:22:11Z 라인.
