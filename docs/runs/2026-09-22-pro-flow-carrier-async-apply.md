# run: 2026-09-22-pro-flow-carrier-async-apply

## 목적과 승인 범위

상단 Pro는 요청한 35-slot 파형·Flow를 그대로 사용하고, 하단 Pro는 같은 슬롯 경계와 하단별
Flow를 보존하되 non-feed 슬롯을 constant carrier로 운전하도록 Master/Async 결속을 적용했다.
사용자는 원격 실행을 승인했고 IoT 전원 차단 수단을 보유했다.

- 실행 commit: `5febb10`
- runtime image: `sha256:8104855832dff72c9b93de0bceadebcaa5a98d551f3f23ed6f7fa194b013daa5`
- schema SHA-256: `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`
- 계획 artifact id: `pro-flow-carrier-20260922-01`
- attach artifact id: `kaga_flow_carrier_async_20260922_01`
- attach artifact SHA-256: `5c5616212650edafd8f245d11022b4fa09c08b1aeb60aa42190e1f38f6e36113`
- master-cycle artifact id: `wm_cycle_000001f540de5238d8a0527279f8f9341c48f1dee559965495565e83363306ab`
- master-cycle artifact SHA-256: `cdff6dca9e1c68a9a83a12840320f44a1369e2889e7b331da2533992596245c2`

MCP-70 Bar, return pump, Aquarium, Dosing에는 명령을 보내지 않았다. 기존 full-state recorder,
raw anchor, packet capture, timer watchdog와 daily monitor는 계속 실행했다.

## 적용 결과

attach는 2026-09-22 13:09:52–13:10:39 UTC에 수행됐다. action 0–7은 ACK와 readback으로
VERIFIED됐다. 마지막 Async role action은 ACK를 받지 못했지만 재전송하지 않았고, 승인된
master link cycle의 role-only 두 action이 각각 ACK된 뒤 fresh pair에서 master/async_slave를
확인했다. 그 증거로 원 action 8을
`native_async_role_verified_by_master_cycle_delivery_unknown`으로 VERIFIED 처리했다.

- attach phase: `VERIFIED`, action 9개 전부 VERIFIED
- master-cycle phase: `VERIFIED`, action 2개 전부 ACK
- 미완료 journal: 0개
- 기존 UNKNOWN forward 재전송: 0회

attach durable final은 다음 두 432-byte image와 역할·전원·TimerON을 exact 확인했다.

| 대상 | 역할 / power / timer | image SHA-256 | 계획 |
|---|---|---|---|
| 상단 | master / ON / ON | `73e0f45b5ce883f15dd89c14b5fe2d3bf8dce55a77bd47ab2285e7c60b7d1fec` | 요청 파형·상단 Flow 원형 |
| 하단 | async_slave / ON / ON | `eb239c93241a175c188c7959955f6e08713caa340df1ea68d18f638a314cafdc` | 같은 경계·하단 Flow, non-feed constant carrier |

## 독립 raw 재확인

적용 직후 journal raw capture와 4분 뒤 별도 raw-anchor attempt `000427`을 모두 확인했다.
attempt `000427`은 13:14:18–13:14:40 UTC의 등급 (a) raw pair이고 acquisition outcome은
accepted다.

| 대상 | identity binding SHA-256 | raw frame SHA-256 | 현재 reported 상태 | schedule SHA-256 | fault |
|---|---|---|---|---|---|
| 상단 | `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c` | `a91bdfd332c0c2c5d099b6e519b035d270f5a731b63f4e6f5aa60971b40a05d7` | master, TimerON, AutoMode tidal, AutoFlow 40 | `73e0f45b5ce883f15dd89c14b5fe2d3bf8dce55a77bd47ab2285e7c60b7d1fec` | 없음 |
| 하단 | `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea` | `b0c1057938c262b153cc38b588433bdd0e1eb5828cde7bdab23dec9592838823` | async_slave, TimerON, AutoMode constant, AutoFlow 40 | `eb239c93241a175c188c7959955f6e08713caa340df1ea68d18f638a314cafdc` | 없음 |

관측 시각은 장비 현지 22:14로, 사용자의 21:00–24:00 구간은 상단 Tidal40 / 하단 Flow40이다.
하단 manual Mode는 결속 펌웨어 반응으로 tidal을 보고하지만, 활성 TimerON block은
`AutoMode=constant`, `AutoFlow=40`이며 이전 문제의 feed 값이 아니다. 두 시간표 모두 active
slot 35개이고 invalid slot은 0개다.

## 이 실행으로 새로 확정된 사실

1. compiler가 만든 상단 원 파형 image와 하단 per-slot Flow constant-carrier image를 두 장비가
   exact 수용했고 Master/Async 역할과 TimerON을 함께 유지했다.
2. Pulse/Tidal 같은 상단 파형을 하단 schedule에 복제하지 않아도 하단의 슬롯별 Flow는 유지된다.
3. 현재 구간에서 하단은 feed 정지가 아니라 constant carrier로 보고됐고, 적용 직후와 4분 뒤
   raw에서 같은 image·역할·활성 Auto 값을 유지했다.
4. 마지막 Async role의 ACK 부재는 master cycle의 별도 ACK 두 건과 fresh pair로 닫혔으며,
   source와 child 모두 VERIFIED이고 미완료 기록은 없다.

이 raw는 장치가 보고한 역할·schedule·활성 mode/Flow를 증명한다. 두 펌프의 물리적인 180도
위상은 telemetry 필드가 없으므로 이번 실행으로 주장하지 않는다. 장기 recorder와 packet capture는
중단하지 않고 계속 보존한다.
