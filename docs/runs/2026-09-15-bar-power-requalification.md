# Bar power 새 schema 재자격 — PASS

Flow 실행의 원복 확인 후 기존 power producer만 사용했다. source `bb5405c`, image
`d5e00203…`, schema `81ee699d…`; operation `bar_power_qualification_bb5405c_01`.
UTC `2026-09-15T08:51:23.473041599`–`08:51:41.354000054`, exit0. ON 동값 확인→OFF→ON
기존3-frame 절차, VERIFIED3/ACK3/영수증2다. 자동 시간표나 다른 모드를 시험하지 않았다.

후보 SHA `fdf4f374d04ee1cd1cc433360aa394de5cd9e80f58f0f0f27d736fd4dc3e2c79`,
journal SHA `e517f7da16e0b85dfacf530383353ae3407a36038e8d57bd32908cb9a32bcff5`.
별도 두 reply에서 [Flow 기록](2026-09-15-bar-flow-requalification.md)의 준비 controls 전부와
384B image가 일치했다. Bar 표본 종료 `08:52:39.754891`, `08:52:41.977868` UTC, fault7 false.
사용자 앱 이전 classic91/F80이 아니라 준비 constant40/F80까지의 원복이다.

private artifact id `pro-qualification-20260915.51uFgd6n`, 결과 로그 SHA
`ddc04b815dccd1d0c33abd6f18ddb2ccb7b41de4bba3fd3cdade8a21c397cea9`,
사후 수집 로그 SHA `f3268858008f34124603110d1303d07f01b178db10a521829daf6004d5ca28e5`.
journal/candidate (b), 사후 wire/status (a), 수동 조회 (c)이며 연속 감시 미주장.
**새 사실:** Bar power 새 자격과 원복이 완료됐다. 두 Bar 실기 합 전송/ACK6/영수증4다.

이후 기존 immutable QualificationSetMerger로 Pro18후보와 Bar2후보를 순차 병합했다.
위조/기존 receipt 수정0, 각 receipt 존재·canonical·digest·binding을 검증했다. 결과11bindings/
40receipts/20device-function pairs, 이전6pairs 전부 포함, 현재 catalog ignored0.
새 set SHA `4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031`,
준비 report SHA `2d2987b4e3840befe61d24936cc0f0faa74601cb50cf0f8c96db3f3e93d4832c`.
이는 실제 기존6개 보존 가능한 후보 확보이지 운영 generation 교체 완료가 아니다.
