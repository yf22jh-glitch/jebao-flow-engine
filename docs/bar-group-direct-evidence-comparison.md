# D1u/D1v — 보존 자료의 group/direct 차이 분석

상태: 2026-09-14, **추가 배포·device write·broker 접촉 없이 수행한 후속 분석**입니다.
새 실기가 아니며 [D1u FAIL](runs/2026-09-14-m4-group-power-commissioning.md)과
[D1v direct PASS](runs/2026-09-14-local-bar-off-flow-diagnostic.md)의 기존 사실은 수정하지 않습니다.
현재 production은 D1v 종료 후 복귀한 exact prior image이며 새 failure event가 없습니다.

## 새로 비교한 보존 bytes

질문: D1u Bar Flow 실패와 D1v 성공에서 저장된 target/payload/baseline이 달랐는가?

기존 journal의 D1u ON record `group_7144de92a67f8e3c106f71725dacbd17` action4와
D1v direct record `mqtt_205ae42c6bf145e2b0c2284f4474b23c` action0을 비교했습니다.
D1u는 recovery 이후rev25/ROLLED_BACK이며 이 분석이 rev14 원본을 새로 확보한 것은 아닙니다.
두 target의 physical identity가 같고 function은 manual_flow입니다. 각 참조 blob을 읽어
SHA-256을 재계산한 뒤 **해시뿐 아니라 실제 bytes 동등성**도 대조했습니다.

| 저장 항목 | 크기 | 두 기록의 동일 SHA-256 |
|---|---|---|
| forward payload | 409B | `4a4e9f0982a9c7334c9e78991670a07f905a3068e36070002a50969b339f60a1` |
| rollback payload | 409B | `d6c7bb8d04ad2b47f2e4499dc74b012cb2c31c6a5d16665cc74cf93f35f9c5d2` |
| expected Flow30 readback | 11B | `6c252c26b75804f62665a9c7f994b76475e4ef2921fb00ba6c55a810668eb724` |
| baseline Flow31 readback | 11B | `9691bbc1354c2ecd9dbf577a6fc00076a068ed09317c1d0cc5c7f61ba22a9ab8` |
| baseline OFF31 status | 401B | `dfa8b131745e2f6d755288d3a13fb12ff918d8372ea580bb78f61d9607a77363` |

다섯 항목 모두 exact 일치합니다. 따라서 **이 저장 payload와 준비 snapshot의 bytes 차이**를
두 결과의 차이로 제시할 수 없습니다. 이는 실제 wire packet 동일성, 전송 순간 장비 상태 동일성,
장비 수신·적용, 세션 내부 상태 동일성의 증명이 아닙니다. 원 예외는 여전히 UNKNOWN입니다.

읽기 전후 journal109 digest는
`a19ba5ccb76769ceeb5268a30ff3d3745359891c792ac199e4ab307cf85a3905`로 동일합니다.
운영 사용자로 저장 파일만 읽었고 장비·broker 호출이나 파일 권한 변경은 없습니다.
private artifact `bar-off-flow-diagnostic-a9c77231316e506777a70d38ef939347`에 후속 출력
`post-run-payload-comparison-v1.stdout`1761B를 보존했습니다. SHA-256은
`0106cabc21e3533276159e8791fdfd5a32cfdabaffafb99fbd1eb4046755a0bb`, stderr는0B입니다.
보존 구조화 출력·journal/blob의(b) 증거이며 wire 원본(a)이 아닙니다.

## 같은 부분과 다른 부분 — 정적 경로

아래는 source `2b31e0b`의 추적이며 실제 wire capture가 아닙니다. D1v source 변경은
coordinator의 진단 호출 추가이며 writer/session은 prior와 같습니다.

| 항목 | D1u group ON | D1v Bar direct Flow |
|---|---|---|
| endpoint/qualification | execute_batch에서 모든 target/function 사전 확인 | execute에서 해당 target/function 확인 |
| baseline 취득 | 세 writer를 열고 각 baseline을 모두 읽은 뒤 record 생성 | Bar writer baseline 읽고 단일 record 생성 |
| Bar 전송 앞의 변경 action | second Flow/power, first Flow/power의4개 | 다른 멤버 action 없음 |
| Bar writer에서 앞선 control | 없음: Bar Flow가 자기 session 첫 control | 없음: 이번 direct의 첫 control |
| send permit | physical lease + generation pre-send guard | physical lease; direct ownership은 호출 전에 claim |
| record/recovery 범위 | 여섯 action 한 record | 한 action 한 record, 이전 OFF terminal은 별도 |

근거: `local_control/coordinator.py`의 execute/execute_batch/_execute_prepared,
`local_control/group.py`의 execute/write_plan/_compile_target,
`local_control/device_writer.py`의 connect_and_read/send_once,
`local_control/lease.py`의 GenerationGuardedLocalControlPermit입니다.
둘 다 같은 prepare_scalar와 _execute_prepared를 사용하지만 group은 baseline 선취득 후
다른 멤버의 send·ACK·readback·journal 저장을 거칩니다. 이 대기가 원인이라는 증거는 아닙니다.

**batch의5번째 action과 Bar sequence5는 다릅니다.** `protocol/session.py`는 session 인스턴스마다
_sequence=0으로 시작하며 authenticate/read_raw_state는 이를 증가시키지 않습니다.
`protocol/control_session.py`의 send_raw_control만 자기 _sequence를 증가시킵니다.
각 endpoint는 별도 writer/session이므로 다른 Pro 전송이 Bar sequence를 증가시키지 않습니다.
표준 코드 경로에서 D1u Bar Flow와 D1v Bar Flow는 모두 자기 첫 control sequence1을 구성합니다.
이는 코드 추론이며 실제 수신 응답의 sequence나 D1u send가 wire에 도달했음을 입증하지 않습니다.
따라서 sequence mismatch 가능성 자체를 배제하거나 그 가설대로 재동기화·retry를 추가하지 않습니다.

## 보존 timestamp로 비교 가능한 창

추가 read-only 재분석에서 두 stream의 digest를 확인하고, 해당 action이 든 모든 보존 revision을
추출했습니다. 다음 값은 **record 생성 이후의 software 창**이며 baseline read→send 유휴시간이
아닙니다. baseline 취득은 record 생성보다 앞이고 그 정확한 완료 시각은 이 표에 없습니다.

| 실행 | 보존 표본 | record.created 이후 updated 경과 |
|---|---|---|
| D1u group | rev12, Bar PREPARED | 10.511030초 |
| D1u group | rev14, Bar UNCERTAIN | 10.594594초 |
| D1v direct | rev1, Bar FORWARD_ATTEMPTING | 0.005600초 |

D1u rev13은 보존되지 않았습니다. serial 코드 순서에서 그 ATTEMPTING save는 rev12와14 사이에
있지만 이를 직접 관측했다고 쓰지 않습니다. realtime clock step이 없다는 가정 아래 record 이후
Bar 시도까지의 창이 서로 다름을 보여줄 뿐, 정확한 세션 idle duration·실패 원인·timeout 배제나
성공/실패 임계값은 아닙니다. 두 실행의 비교이며 역대 모든 Bar 전송을 전수 비교한 것도 아닙니다.
같은 private artifact의 `post-run-timing-proxy-v1.json`3271B, SHA-256
`10519da59c90aa0940186f23a8c0b765307bc72b9f065a40df14645c829bfd5e`로 재분석을 보존했습니다.

## 다음 결정 — 자동 실행하지 않음

진단 계측 image는 아래 새 write 시험의 선행조건입니다. **상시** 배포만이 유일한 선행 경로는
아니며 별도 승인된 임시 diagnostic 배포도 가능합니다. 1번은 계측 운영 결정이고2/3번은 물리
시험 대안이므로 같은 층위의 대체 선택지로 취급하지 않습니다.

1. **진단 상시 배포 결정**: 이미 검토·단회 운영한 exact diagnostic image를 config delta0으로
   배포할지 maintainer에게 확인합니다. 앱 준비·펌프 command 없이 stop-first/readiness/unarmed/
   fresh6/discovery90/journal109/receipt18 불변을 확인하는 범위입니다. 실패하면 자료 보존 후
   exact prior 복귀를 검증하고, 복귀도 실패하면 정지·보고합니다. 이번 문서가 배포 승인은 아닙니다.
   targeted21/Ruff PASS와 full-suite 실패2건 미해결을 함께 공개해야 하며 실전 실패 진단 발동도
   아직 미검증입니다. 상시 배포해도 펌웨어 가설이 검증되는 것은 아닙니다.
2. **동일 group 순서의 진단 관측**: D1u와 가까운 구성으로 새 원예외 관측 기회를 갖지만,
   세 wavemaker OFF·실패 후 혼합/OFF 잔류·현장 복원 부담이 다시 생깁니다. 재현과 원인 규명은
   보장하지 않으며 추가 승인이 필요합니다. 성공해도 그 단회의 group 경로 완료 관측이지
   D1u 원인 해소가 아닙니다. 이전 D1u/D1v 권한으로 반복하지 않습니다.
3. **Bar 우선 순서 등 축소 대조**: 선행 Pro action을 줄일 수 있지만 group 순서·다른 멤버의
   세션 대기 조건도 함께 바뀝니다. 성공해도 D1u 원인 증명이 아니므로 순서 변경을 수정책으로
   채택하지 않습니다. 단독 Bar group도 다중 멤버 대기 조건을 재현하지 못합니다.

우선 결정할 것은1번이며2/3번의 펌프 조작과 분리합니다. source·timeout·retry·gate 변경은
제안하지 않습니다. 추가 관측 없이 park해도 Bar direct PASS는 유효하지만 group power 완료는
미해결로 남습니다. 재개 시에는 어떤 새 질문을 가를지와 최소 두 대안·현장 복원 권한을 다시 고정합니다.
