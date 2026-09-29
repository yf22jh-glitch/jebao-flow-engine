# run: 2026-09-29-pro-external-manual-activation-first-stop

- 목적: 사용자가 마지막으로 지정한 31행 Pro 시간표를 저장하고, 현재 14:00 Tide 행을 단회 활성화한 뒤 반복 실행기로 넘긴다.
- 질문: 현재 하단 Master·상단 Async 결속에서, 상단 TimerON을 끄고 양쪽 수동 운전으로 전환하는 첫 단계가 ACK와 fresh 상태로 검증되는가?
- 승인 범위: 사용자의 시간표 배포·활성화 지시. 물리 Flow나 위상은 계측하지 않았다.
- 실행 source: `ac7c090` (Compose 정의는 후속 `eef137f`), 후보 image `sha256:2d02c549bfbdc47b617a1489e3ba816f35f59dea1e155c5e27492de4cafc9c88`. 기존 운영 제어 서비스의 image·process는 바꾸지 않았다.
- UTC 관측·실행 구간: 2026-09-29 05:05:06–05:12:20.
- physical identity binding SHA-256: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, 상단 Async `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- 계획 digest: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5` (31행, Asia/Seoul). 저장 시 장비 write 0.

## 결과

사전 fresh pair는 하단 Master·TimerOFF·ON·Pulse·Flow36/F100, 상단 Async·TimerON·ON·Pulse·Flow40/F100, 양쪽 fault0이었다. 원 432바이트 image SHA-256은 하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`, 상단 `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`이었다. 권한·출력 범위·현재 결속·미종결 저널 0을 장비 write 없이 확인했다.

활성화가 계획한 첫 child는 상단 `schedule_enable=false`였다. 당시 결속된 Async에 대한 프레임의 기대값은 `TimerON=false, SwitchON=true, Linkage=independent`, baseline은 `TimerON=true, SwitchON=true, Linkage=async_slave`였다. 2026-09-29T05:10:06.876763Z에 해당 child 저널을 만들고 단회 송신을 시도했으나 5초 내 제어 ACK가 없어 `forward_outcome_uncertain`으로 중단됐다. 장비 전송 호출은 이 한 번뿐이며, **다음 child·현재 행·반복 시간표의 송신은 0회**다. Parent `pro_prep_20260929140946_e60eb4dd952dffcc`는 `stopped`, 활성 상태는 `paused`다.

- 미종결 child request id: `pro_prep_20260929140946_e60eb4dd952dffcc_slave_timer_off`.
- forward payload SHA-256: `7cfbbf160e2309e38b81816fc5350fc175f138f3de9c8c0cf77f1ed741bfeedd`.
- 원 미종결 journal digest (등급 b): `5c7d658e697afa7f138139dabb8cb7f628a5d5ff49d9d130c08e9c01c44fdacb`.
- Parent 시작 raw blob SHA-256 (등급 a): 하단 `5d07e37e8ff408d0c3d65749f6322acd977a4be437b4c894a630f33b2d076d91`, 상단 `38aebb561a0cf65fb4465e2f7305d835f6e5535a49ad75e5d93c7fb4c5537d65`.
- 중단 뒤 fresh raw blob SHA-256 (등급 a): 하단 `db2c15b28a77593761fd88cdb69aa26d2629f6c6b09f34f707160b71d9e8d0d1`, 상단 `26fe4c5ad0a72b0033f9f9b0c13258bc421db00873412b1d1238107e8c201138`.

중단 뒤 fresh pair는 위 baseline과 같은 역할·Timer·전원·Mode·Flow·Frequency, fault0 및 양쪽 image SHA exact를 보고했다. 즉 이 관측 시점에 상단 TimerOFF가 **적용된 상태는 아니었다**. 그러나 제어 프레임이 장치에 전혀 도달하지 않았다고 단정하지 않는다. 기존 저널은 지우지 않았고, 복구/재송신 명령도 보내지 않았다. 반복 실행 서비스는 시작하지 않았다.

## 판정과 다음 조건

**새로 확정된 사실:** 결속된 Async에 먼저 `schedule_enable=false`를 보낸 이 경로는 이번 한 번의 시도에서 ACK 검증에 실패했고, 사후 raw는 원 baseline 그대로였다. 따라서 현재 구현의 활성화 순서를 그대로 재시도하거나 시간표를 켜지 않는다.

**미확정:** ACK 부재의 전송·펌웨어 원인, 다른 결속 순서에서 TimerOFF 수용 여부. 다음에는 원 uncertain 기록을 raw 결속의 무송신 절차로 종결하고, Master 해제 후 Slave TimerOFF를 시도하는 별도 operation을 검토한다. 동결된 native 하네스는 사용하지 않는다.
