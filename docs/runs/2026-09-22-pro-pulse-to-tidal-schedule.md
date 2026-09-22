# run: 2026-09-22-pro-pulse-to-tidal-schedule

## 목적과 승인 범위

native `master`/`async_slave` 결속에서 Pulse 구간마다 하단 Pro가 manual `Mode=feed`를
보고하며 정지하는 현상을 즉시 제거하기 위해, 두 Pro 시간표의 Pulse 구간 열 개를 모두 Tidal로
교체했다. 사용자는 실시간 적용을 승인했고 원격 IoT 전원 차단 수단을 보유했다.

이번 실행은 다음 운영 질문을 닫는다.

> Feeding·Constant 복귀·구간 시각·장비별 Flow와 master/Async 역할을 유지하면서, 두 Pro의
> Pulse 슬롯만 제거한 35구간 시간표를 exact 저장하고 다음 경계에서 실제 적용을 확인할 수 있는가?

MCP-70 Bar, DC return 두 대, Aquarium, Dosing에는 명령을 보내지 않았다. 동결된 native ASYNC
실험 하네스와 `src/`는 수정하거나 실행하지 않았고, 배포된 local-control native API만 사용했다.

- 기록 시점 repository HEAD: `2e826e1d4851c56c9f9fa629fdcf1ce7d0df6664`
- 실행 runtime source: `919bfab`
- runtime image: `sha256:a1733a465f4a509960745f621952f9fc70c848c6fd9ae65ad07dded16e5fa911`
- durable-operation UTC span: `2026-09-22T08:55:57.781467Z`–`2026-09-22T08:59:57.675115Z`
- private artifact id: `pro-pulse-to-tidal-20260922-01`
- artifact file count: 11
- artifact tree-manifest SHA-256: `4d390416e710f50ea2a4ae085105290013d19049d2c5aee9379caef4e9abc7c8`
- identity binding SHA-256:
  - 상단: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - 하단: `7b7be7835ebd264d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`

private artifact 디렉터리는 mode `0700`, 11개 파일은 모두 `0600`이며 O_EXCL 생성과 fsync 뒤
digest를 다시 계산했다. 저장소에는 private 위치·주소·device id를 기록하지 않는다.

## 시간표 변경

기존 운영 source `kaga_daily_async_20260921_01`의 35구간 계획에서 Pulse였던 다음 열 구간만
Tidal로 바꿨다.

| 시작 시각 | 유지한 상단/하단 Flow | 제거한 Pulse Frequency | 새 모드 |
|---|---:|---:|---|
| 09:08 | 40 / 40 | 70 | Tidal |
| 10:38 | 40 / 40 | 70 | Tidal |
| 12:08 | 40 / 35 | 80 | Tidal |
| 13:38 | 40 / 35 | 80 | Tidal |
| 14:00 | 45 / 45 | 60 | Tidal |
| 16:00 | 45 / 45 | 70 | Tidal |
| 16:38 | 45 / 45 | 70 | Tidal |
| 17:10 | 45 / 40 | 70 | Tidal |
| 18:08 | 45 / 45 | 70 | Tidal |
| 19:00 | 40 / 40 | 80 | Tidal |

Tidal의 사용하지 않는 Frequency는 0으로 정규화했다. Feeding 3분 아홉 구간, 각 Feeding 뒤
Constant35 5분, Nutrient Transport 한 구간, 모든 경계 시각과 장비별 Flow는 그대로 유지했다.
새 계획은 두 장비 각각 35개 active slot, invalid slot 0개이며 mode 개수는
`tidal=16`, `feed=9`, `constant=9`, `nutrient_transport=1`, `pulse=0`이다.

preview는 장비 write 없이 다음 432 B image를 만들었다.

| 대상 | 최종 역할 | image SHA-256 |
|---|---|---|
| 상단 Pro | `master` | `4005971159c8e817a0e80c8c0f59b220b5d13151a51979958d5808ff17b97d74` |
| 하단 Pro | `async_slave` | `b9735369a792bcc9e6187aadf5e647529afed4d2f6a34d2eaa1df29fc9bb7667` |

preview 응답 상태 문자열이 wrapper 예상값과 달라 첫 wrapper가 종료됐지만 preview 자체는 성공했고
장비 write는 0이었다. 첫 attach 요청도 기존 source가 소유한 결속 때문에 durable operation 생성
전에 거부됐으며 장비 write는 0이었다.

## 기존 결속 해제와 새 시간표 적용

기존 source를 formal detach하는 첫 하단 control write는 ACK를 받지 못해 `UNCERTAIN`이 됐다.
같은 forward를 재전송하지 않고 기존 `assist_detach` 경로로 하단의 관측 상태를 맞춘 뒤 detach를
계속했다. 원 image와 controls를 복원한 detach child와 assist child는 모두 `VERIFIED`로 끝났다.

| operation | action 결과 | final phase | record SHA-256 |
|---|---|---|---|
| old detach child | ACK 5, delivery-unknown 관측 종결 1 | `VERIFIED` | `f1166e6c63585f9e5721db71a8437cde167a4343930d41f0fc40b2a3aef6397c` |
| old assist child | ACK 4, no-change 1 | `VERIFIED` | `32e6f28a75463ca7428eba04508d6f6db4ed102709b60e2906be9b51422c1627` |
| new attach source | ACK 8, master-cycle 검증 종결 1 | `VERIFIED` | `fa1f565bfeb76cec0dab58587a0ae8b1104e2ed356cff7e92d6c8efa5103b417` |
| new master-cycle child | ACK 2 | `VERIFIED` | `23e4bca533ed83f7c9917bcce3eff7def3f5dd1f6b1c33efad2abc2831237f99` |

journal에 기록된 실제 send attempt는 21회, ACK는 19회다. 두 delivery-unknown action은 같은
명령을 재전송하지 않고 각각 assist 관측과 master-cycle fresh pair로 종결했다.

새 source `kaga_pulse_to_tidal_20260922_02`의 final verification은 다음을 동시에 확인했다.

- 상단: `master`, power ON, TimerON, 35-slot image exact.
- 하단: `async_slave`, power ON, TimerON, 35-slot image exact.
- 두 image 모두 계획 digest와 일치하고 `schedule_match=true`.
- source 9 action과 master-cycle 2 action 모두 terminal `VERIFIED`.

## 다음 시간표 경계 확인

18:00 KST 적용 직후 양쪽이 `feed`를 보고한 것은 계획된 18:00–18:03 Feeding 구간이다.
read-only full-state recorder는 다음 경계에서 두 장비가 모두 `AutoMode=constant`, `AutoFlow=35`,
`AutoFreq=5`로 전환한 것을 보존했다.

| 대상 | 관측 UTC | device local time | 역할 | 현재 Auto | schedule |
|---|---|---|---|---|---|
| 하단 | 09:03:04 | 18:03 | `async_slave` | Constant / Flow35 | ON, 35 slots, Pulse 0 |
| 상단 | 09:03:20 | 18:03 | `master` | Constant / Flow35 | ON, 35 slots, Pulse 0 |
| 하단 | 09:08:11 | 18:08 | `async_slave` | Tidal / Flow45 | ON, 35 slots, Pulse 0 |
| 상단 | 09:08:11 | 18:08 | `master` | Tidal / Flow45 | ON, 35 slots, Pulse 0 |

구조화 증거 파일은 `005423-events.json`
(`sha256:5fae75b36362cc9bfe25346454e4060fe0c92ba4f856aa719bb23b9f31c16bdb`)과
`005424-events.json`
(`sha256:3a0ca8ba5ad1df2202a35ac97066bb1c522492eb649b5fc80e163109ee6654e5`)과
`005433-events.json`
(`sha256:3817e53c80495cc671c44c5ed4848dd7cfa96ba7f59dd7801b6d06721f7bc9eb`)이다.
이는 증거 등급 **(b) preserved structured/durable daemon artifact**이며 물리 유량 계측은 아니다.

기존 full-state recorder, raw anchor, packet capture, timer watchdog와 일일 monitor는 중단하거나
재시작하지 않았다. 이번 작업은 그 read-only 수집을 그대로 유지한다.

## 결론

**이 실행으로 새로 확정된 사실:** native master/Async 역할과 Feeding·Constant 복귀·시간·Flow를
유지하면서 두 Pro의 Pulse 슬롯 열 개만 Tidal로 바꾼 35구간 image를 exact 저장했다. 다음
18:03 경계에서 양쪽이 계획한 Constant35로 전환했고, 18:08 첫 교체 구간에서는 양쪽 모두
Tidal45로 전환했다. schedule ON, master/Async 역할과 fault 0도 유지됐다.

이는 9월 22일 선행 raw 실행에서 확인한 “native master/Async 결속 중 Pulse 구간에서 하단이
manual feed를 보고함”에 대한 운영 완화다. 펌웨어 내부 분기를 새로 증명하거나 Pulse형 native
anti-phase를 성립시킨 실행은 아니다. 이후 Pulse 구간이 없으므로 같은 Pulse→feed 재현 조건을
운영 시간표에서 제거했다.
