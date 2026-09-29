# run: 2026-09-29-pro-manual-idle-discovery-pause

- 목적: 가동 중인 Pro 외부 수동 시간표가 19:09 KST에 정지한 원인을 장비 write 없이 확인한다.
- 이 실행이 답하려던 질문: 한 번의 현재 endpoint 탐색 실패가 영구적인 일정 중단을 일으켰는가?
- 실행한 source: `f9aedc2`; image digest `sha256:11ec428023e0969c6dc81c668fc3a614863b63a61a58d3e0d6e456ce649aad69`.
- 명령·주요 파라미터: 이미 실행 중이던 `run --write-enabled`의 기록, 스케줄러 상태 파일, 대상 한정 host packet capture를 읽기 전용으로 대조했다. 이 조사 중 장비 write는 0회다.
- 종료 지점: 2026-09-29 10:09:49.552 UTC `CurrentEndpointResolutionError`; 직후 Docker 재시작은 `ValueError`로 반복 종료.
- 장비가 실제로 돌려준 값과 증거:
  - 증거 등급 (a): host packet capture `target-only-any.pcap00`, UTC 2026-09-28 11:27:27–2026-09-29 13:22:52, SHA-256 `dda00e31ecf18be3f0d9a9c2ccf8b08c5cfafe76227b7e9395a51f37edd19c52`. 2026-09-29 10:09:44.542 UTC의 대상 한정 UDP discovery 요청 3개 중 하단 Master의 응답은 이 캡처에 없고, 다른 두 장비의 응답은 있다. 10:09:51.281 UTC 다음 요청에는 세 장비 응답이 모두 있다. 이것은 **캡처된 응답의 부재**이지 펌웨어가 응답을 생성하지 않았다는 증명은 아니다.
  - 증거 등급 (b): active-state artifact `pro-external-manual-scheduler/active.json`, SHA-256 `9181f0bf1ce5e52186b7ff2e9984516a16ee6f788149b2e8004fb0a02c89f682`, 2026-09-29 10:09:49.544 UTC 수정. `status=paused`, 마지막 적용 행 `18:03`; 뒤 행의 parent operation은 없다. Docker 로그에는 10:09:49.552 UTC 첫 `CurrentEndpointResolutionError`와 이후의 `ValueError`가 남았다.
  - SHA-256 physical identity binding: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, 상단 Async `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- 원복 검증: 이 조사에서 설정을 변경하지 않았다. 마지막 18:03 수동 상태가 유지됐고 시간표는 paused로 남았다.
- **이 실행으로 새로 확정된 사실:** 정지 시점과 5초짜리 discovery window가 일치한다. `verify_idle()`의 fresh pair 실패는 active state를 영구 paused로 바꾸고, 이후 `run` 재시작은 그 상태에서 즉시 실패한다. 한 번의 누락된 탐색 응답만으로 후속 시간표 행이 실행되지 않는 코드 경로가 있다.
- 이 실행으로 확정하지 못한 것: 하단 장비가 해당 회차에 응답하지 않은 물리·무선 원인, host capture 밖의 프레임 유무, 이후 미실행 행의 물리 효과.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 장비 write 없이 현재 물리 identity를 다시 확인하는 한정된 discovery 재시도와 그 결과의 진단 기록. 여러 번 실패하거나 identity가 불명확하면 기존 정지 동작을 유지한다.
