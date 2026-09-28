# run: 2026-09-28-pro-reverse-linked-flow37

## 질문과 승인 범위

하단 Pro가 Master, 상단 Pro가 Async Slave인 현재 쌍에서 하단 Master 역할을 해제하고
상단의 별도 `Flow`를 45에서 37로 바꾼 뒤 하단 Master 역할을 재결속할 수 있는가.
사용자는 정확히 이 세 명령을 순서대로 보내라고 지시했다. `OFF/ON`은 전원이 아닌
Master `Linkage` 해제/재결속으로 해석했다. 두 펌프의 전원이나 시간표를 쓰는 명령은
승인 범위에 넣지 않았다.

이번 operation 직전에 사용자의 현장 감시·즉시 전원 차단 가능 여부를 별도로
재확인하지 않았다. 사용자가 직전 앱 역할 교체 결과를 직접 보고했고 이 대화에서
이전에 현장 감시와 IoT 전원 차단 수단을 알렸다는 사실에 의존했다. 이는
`AGENTS.md`의 operation별 현장 조건을 충분히 확인했다고 주장할 근거가 아니며,
이 절차상 빈틈은 다음 live-write에 이월하지 않는다.

## 실행 코드와 신원

- UTC 실행 구간: 2026-09-28 14:48:32–14:49:03.
- repository source HEAD: `50584bfecd84f40a7b7580832b05d5300e4a6ec6`.
- 운영 컨테이너 image: `sha256:8104855832dff72c9b93de0bceadebcaa5a98d551f3f23ed6f7fa194b013daa5`.
- 로컬 `local_control_linked_flow_cli.py`를 stdin으로 실행했으며, 최대 변화량 5를
  거부하는 두 비핵심 CLI 조건만 메모리 내 스트림에서 제거했다. 실행된 스트림의
  SHA-256은 `8825c2238fde59b7231ce1fb4434028e55dfc6e4fbb5de5102330d68b3267a73`.
  소스 파일과 운영 image는 수정하지 않았다. identity·물리 binding, 설정 범위,
  45 상한, durable journal, 단일 송신, ACK/readback·원본 image 검증은 유지했다.
- physical identity binding SHA-256: 하단 Master
  `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`,
  상단 Async Slave `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

## 결과

첫 write 전 읽기 전용 preview에서 하단 Master·상단 Async, 상단 `Flow=45`,
미완료 pair operation 0건, 요청 `Flow=37` 및 write 0을 확인했다.
세 child는 서로 다른 durable journal을 첫 송신 전에 만들고 각자 한 번만 송신했다.

| child | 실제 필드 변경 | durable phase / ACK | journal SHA-256 |
|---|---|---|---|
| release | 하단 `{Linkage: independent}` | VERIFIED / 있음 | `ef688cba86def2ae0318a868d45b88689cfd9cd73172df981879f0892495972d` |
| flow | 상단 `{Flow: 37}` | VERIFIED / 있음 | `bb18f2c414d876204a0680210062831409b52c026b9cfaf2048d61f3a1456be8` |
| relink | 하단 `{Linkage: master}` | VERIFIED / 있음 | `809678b34007c86c47e9f8f982aa20a53a429135b4946223bd143e995ab71c1a` |

CLI 종료 결과는 `verified`, 실제 장비 write 3회, 하단 Master·상단 Async,
상단 `Flow=37`, 두 432-byte 시간표 image 불변이었다. 이후 별도 읽기 전용
fresh pair에서 하단은 직접 reply action 3, power ON·TimerON·Master,
`AutoMode=tidal`/`AutoFlow=30`/fault 0을 보고했다. 상단은 report action 4,
power ON·TimerOFF·Async, `Mode=tidal`/`Flow=37`/fault 0을 보고했다.
하단 image SHA-256은
`24f14867eabc0fbab787842eb0f23a0fdca6db0308855257cb5ca3a077413ed6`,
상단은 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`이었다.

세 journal record의 생성·종결 시각, VERIFIED action 및 ACK digest 존재와 record
digest를 운영 안전 볼륨에서 다시 읽어 일치시켰다(등급 b). 종료 fresh pair의
원문은 이 실행용 별도 고정 raw artifact로 보존하지 않았으므로 그 조회 요약은
등급 c이며, 실제 물리 유량이나 장기 유지의 증명은 아니다.

## 새로 확인한 사실

이 역방향 쌍에서 Master 역할 해제 → Slave `Flow 45→37` → Master 재결속은
각각 한 번의 명령으로 검증됐고, 직후에는 두 시간표와 역할을 유지하며
상단이 Feed가 아닌 Tide를 보고했다. 기존 Pulse 시간표에서 Slave가 Feed로
바뀐 원인은 이 실행으로 해결되거나 판정되지 않았다.
