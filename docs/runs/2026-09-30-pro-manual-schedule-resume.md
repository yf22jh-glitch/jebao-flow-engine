# run: 2026-09-30-pro-manual-schedule-resume

- 목적: [한 번의 discovery 누락으로 일시정지된 Pro 외부 수동 시간표](2026-09-29-pro-manual-idle-discovery-pause.md)를 수정된 실행기로 현재 행부터 재가동한다.
- 이 실행이 답하려던 질문: 보존된 31행 계획을 재사용해 현재 행을 정확히 한 번 적용하고 Master/Async·TimerOFF 상태로 반복 실행기를 유지할 수 있는가?
- 사용자 승인: “일정 돌려”. 현재 local-control의 원격 IoT 전원 차단·사용자 중단 우선 운용 기준을 적용했다.
- 실행 source `0b5eed3`, 배포 pin `53a9bff`, image `sha256:5f2b25420916870ea3348b0c1b5dcc8461af7093b222c4b16e3cd83780bbd626`, 계획 SHA-256 `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.
- UTC 실행·검증 구간: 2026-09-29 22:30:24–22:32:17. 부모 저널 생성 22:30:44.030711, terminal 수정 22:31:26.675466 UTC.
- 명령·주요 파라미터: 새 이미지에서 `activate --replace-paused`로 현재 `07:03` 행을 1회 적용한 뒤 `run` 서비스를 시작했다. 하단 Master·상단 Async 역할을 유지했고 MCP Bar에는 송신하지 않았다.
- 종료 지점: parent `pro_manual_20260930_0423_e60eb4dd952dffcc` **verified**, 3/3 child verified, 실행기 `runner_running=true`, 다음 경계 `08:40 KST`.
- 장비가 실제로 돌려준 값:
  - 증거 등급 (b): parent 저널 SHA-256 `3403f771a222621e35370015dc08f476d6aa7af06da3154bf4b82cbf2b7cbd17`. 순서대로 `Master→independent`, `Slave common Flow→35`, `Master→master`를 각각 1회 송신·검증했다. child journal digest는 `f547a50dd4d1ffbb450d5b40932b90868c4d8e2e87042d238f96214148c43287`, `94c4b0fad9e63b57ba2d55e829998923cdfeb63740aede8bd8fdef2129090a05`, `ee959211c08b5062cb9efb5f62c2ec7b6e1d6c04d049a5f5e5cfb3574658df6f`.
  - 증거 등급 (a): 실제 blob 462B씩을 다시 읽어 digest를 확인했다. 마지막 fresh pair raw SHA-256은 하단 Master `ba74806c70622076ab4bdee09a0cf9531a0ad9c29b3653c305a1201366fb583b`, 상단 Async `5ee4961cf84fb4f68a55c17bce18e9678cdbbf20280f322c86a73709f1d22b3a`. 보고는 하단 `master·TimerOFF·전원ON·Sine·Flow40·F80·fault0`, 상단 `async_slave·TimerOFF·전원ON·Sine·Flow35·F80·fault0`. 원 432B image digest는 각각 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`, `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`로 불변.
  - 증거 등급 (b): 후속 MQTT 진단 `00007964-event.json`, UTC 22:32:17, SHA-256 `2d5d3bc8f143ebf64be578e0eb682c81ae997a7f58a958a2210198eed7d2797e`은 같은 역할·모드·Flow·Frequency를 보고했고 telemetry는 fresh였다. 이는 물리 유량·위상의 직접 측정은 아니다.
  - SHA-256 physical identity binding: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, 상단 Async `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- 원복 검증: 의도적으로 원복하지 않고 현재 행 운전을 유지했다. 별도 schedule image write는 0회였고, 양쪽 image digest 불변을 fresh pair에서 확인했다.
- **이 실행으로 새로 확정된 사실:** 일시정지 뒤 같은 계획의 현재 Sine/F80/상단35/하단40 행을 새 이미지에서 3개의 검증된 child로 적용하고, Master/Async·TimerOFF 상태의 반복 실행기를 기동했다.
- 이 실행으로 확정하지 못한 것: 08:40 이후 경계 실행의 성공 여부, 장기 운전 안정성, 실제 물리 유량·위상.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 새 discovery 재시도 결과와 08:40 경계의 durable operation·fresh report를 대조해 반복 실행 안정성을 확인한다.

## 후속 관측 정정 — 반복 실행 유지 실패

- UTC 2026-09-29 22:34:36.146에 반복 실행기가 `ProtocolTimeoutError`로 종료되고 `active.status=paused`가 됐다. 위의 `runner_running=true`는 22:32:17 시점의 관측이며 장기 유지 결과가 아니다. Docker 재시작은 paused 상태에서 `ValueError`로 반복 종료되어 실행기를 정지했다. 다음 `08:40 KST` 행은 아직 시작되지 않았고 장비 제어 재전송은 없었다.
- 증거 등급 (a): 대상 한정 host packet capture의 UTC 22:34:30–22:34:36 구간에서 하단 Master의 인증·462B 상태 응답은 완료됐다. 이어 상단 Async의 새 TCP 연결에서 passcode 교환 뒤 login 요청을 보냈으나, 462B 비동기 상태 보고만 수신하고 login 응답은 5초 안에 관측되지 않았다. 연결 종료 시각은 오류 로그와 일치한다. 이 자료는 캡처된 인증 응답의 부재를 보일 뿐 장비가 응답을 생성하지 않았다는 증명은 아니다.
- 코드 대조: `LiveBackend.fresh_pair()`가 순차로 두 장비를 인증·조회하며, `GizwitsSession.authenticate()`의 login 응답 timeout이 `ProtocolTimeoutError`를 낸다. `ExternalManualScheduler.verify_idle()`은 읽기 실패도 즉시 영구 paused로 바꾸므로 한 차례 실패만으로 다음 행을 잃는다. 이는 제어 송신 재시도 문제가 아니라 읽기 전용 idle 확인의 실패 처리 결함이다.
- 후속 조치 전 판정: 현재 행의 부모 operation은 이미 `verified`이므로 같은 행에 `activate --replace-paused`를 다시 실행하면 안 된다. 재송신 없는 현재 행 검증·재개 경로와 한정된 읽기 전용 재시도를 마련한 뒤 별도 결과를 덧붙인다.

## 후속 조치 결과 — 완료 행의 무송신 재개

- 보존 raw artifact id `idle-auth-timeout-20260929T2234Z.pcapng`: UTC 2026-09-29 22:34:26–22:34:40, 152패킷, SHA-256 `a9a98f211dde0b7bdd98a82787b203b42cd81f421f41c854e310a8f552523a26`. 홈서버 private capture에만 보관하고 저장소에는 넣지 않았다.
- source `640e253`, image `sha256:76b90f503384351b8c25b0530793a30a306b36e6b9d69a40580d88ea787c3d7e`에서 2026-09-29 22:45:23–22:45:46 UTC `resume-verified`로 `07:03` 행을 재개했다. 같은 물리 binding·계획·verified parent와 3개 child digest·fresh roles/TimerOFF/power/mode/Flow/Frequency/image를 확인한 뒤 `writes=0`으로 active 상태를 복구했다. 기존 parent journal SHA-256은 `3403f771a222621e35370015dc08f476d6aa7af06da3154bf4b82cbf2b7cbd17`로 불변이었다.
- 22:49:02 UTC 재확인에서 실행기는 시작 22:45:46 이후 restart 0, `runner_running=true`, `status=active`, 마지막 `07:03` 행, 다음 `08:40 KST` 경계를 유지했다. active-state artifact SHA-256 `d0b80c27ef9ee4036720e2d5aeecacc52de67b2807492cc6e696c01040d605f3`; 보고 기대값은 하단 Master Sine40/F80, 상단 Async Sine35/F80이다. 이 3분여 관측은 **08:40 자동 경계 성공이나 장기 안정성을 아직 증명하지 않는다.**
- 이 후속 조치에서 새로 확인한 것: 읽기 전용 idle 인증 timeout과 이미 완료한 물리 child 재전송은 분리할 수 있다. 실패 회차의 장비 제어 송신은 없었고, 같은 행의 완료된 child를 재실행하지 않고 현재 상태를 검증해 반복 실행을 다시 시작했다.
