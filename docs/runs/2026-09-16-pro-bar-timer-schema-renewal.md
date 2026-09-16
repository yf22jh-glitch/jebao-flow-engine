# run: 2026-09-16-pro-bar-timer-schema-renewal

## 승인·질문

Bar timer writer와 planner 지문 변경 뒤에도 기존 두 Pro 제어를 유지할 수 있도록 사용자가
재자격을 승인했다(`d1f020c`). 질문은 **같은 실물 두 Pro에서 새 source의 기존9기능 자격과
자기 원본 복원이 다시 성립하는가**다. Async 새 결속·리턴·도징 조작은 포함하지 않는다.

- source `d1f020c34b9c70513cbfe249b59bab39fdb2ce45`.
- image `2fe95ce8a277b5fd7cad7bf661edbd5e5fb972307251c06ea784dec8541a9a08`.
- schema `0cb054233e2a5691463f0f7b3812cc87f8536e935755f0e3c756f142bb9797fc`.
- operation `pro_control_qualification_d1f020c_01`, 별도 OBSERVER·두 Pro만 write 허용.
- 실행 구간: 2026-09-16 09:18:00.814472–09:20:00.072123 UTC.

## 실제 결과

기존 고정9기능 producer를 한 번 실행했다. 준비40/F20·시간표40/39·TimerON/OFF·
sine/constant·Frequency21/20·Feed1/2·custom6/5·Flow39/40·OFF/ON·master/independent 범위다.
source38 actions 모두 VERIFIED/ACK, 자기 detach6 actions는4 VERIFIED/ACK+2 NO_CHANGE다.
실제 전송42/ACK42, source VERIFIED/rev191, child VERIFIED/rev27, exit0.
두 실물×9기능=18pair/36개의 실제 receipt를 후보로 발급했다. 재시도·보조 복구0이다.

최종 direct reply는 아래 Pro 09:19:58.921027, 위 Pro 09:20:00.047785 UTC다.
wire frame을 decode하여 action3/status 바이트를 확인하고, 보존 original status와 각8controls·
432B image를 직접 대조했다. 두 장비 모두 exact다. TimerON/ON/independent/constant로 돌아왔고
아래 Flow30/F32, 위 Flow40/F34, Feed10/custom5다. 시각·Auto·현재 슬롯은 비교 대상이 아니다.

- 아래 image SHA `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`.
- 위 image SHA `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`.
- source SHA `110964f09bb307d5dc7eac689ced5b5bacd5cffad29a27525f8cfa48e8806af0`.
- child SHA `40b1f87fcbb9d87ee89c83c4bc31b63f87968f10d1b74dcf545ac055286fd134`.
- identity SHA: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`,
  `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

## 보존·현재 상태

opaque artifact `bar-timer-renewal-20260916-01/all-qualification-evidence`, archive SHA
`2ae2ca43a1600c8c7c8ba088b425169cc9757fe1ee68bf0c6e37a0aeac20cbfa`.
Pro 원본/단계별/최종 wire·status는(a), journal 및 Pro36+앞선 Bar4 receipt 실체는(b)다.
고유 blob332개 SHA를 전수 대조했고, Pro receipt36장이 정확히 두 identity·18pair에 묶였다.
export 보고서의 native child `intent_sha256` blob 부재 표시는 별도 해석이 필요하다:
기존 native lifecycle은 intent digest를 기록할 뿐 그 intent를 blob으로 설치하지 않는다.
context·action payload·baseline·final raw blob은 보존되어 있다.

09:22:57 UTC 전체 operation73개를 읽고, 사전 확인 이후 변경된 것이 Bar timer·Bar schedule·
Pro qualification·그 자기 detach의4개뿐임을 확인했다. 예상 밖 변경·unfinished0이다.
이 조회는 기존 모든 operation의 과거 byte 불변까지 증명하는 snapshot 비교는 아니다.
09:23:11.558931 UTC 기존 Pro 서비스를 원래 image/config/receipts로 다시 시작했다.
새 generation과 Bar HA 노출은 아직 배포하지 않았다.

**새로 확정된 사실:** 새 지문에서 두 Pro9기능의 실제 전달/readback·원본 복원과36receipt
발급이 성립했다. ACK body는 빈 바이트이며 수용은 readback 계약 범위다. 물리 유량·생물 안전·
Q1/Q2 답이나 새 Async 성공은 이 시험으로 주장하지 않는다.
