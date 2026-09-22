# run: 2026-09-22-pro-pulse-independent-causal-ab

## 목적과 질문

하단 Pro가 `async_slave`로 결속된 Pulse 구간마다 `Mode=feed`를 보고하며 물리적으로 정지했다는
운영자 관측의 원인을 다음 두 가설로 나눴다.

> 같은 저장 시간표의 Pulse 경계에서 하단을 `independent`로 두면 정상 Pulse 상태를 보고하는가,
> 아니면 결속과 무관하게 같은 장비의 Pulse 경로 자체가 `feed`로 바뀌는가?

사용자는 두 펌프의 IoT 전원을 원격 차단할 수 있는 상태에서 이 단회 비교를 승인했다. 동결된
native ASYNC write 하네스는 수정하거나 실행하지 않았다.
배포된 local-control의 `linkage_role` 단일 필드 경로만 사용했고 앱 조작, schedule image write,
전원·타이머·Flow·Frequency write는 없었다.

- 기록 시점 repository HEAD: `855e3bc03ddfed356b3899673f8a8ed922e0a5f4`
- 실행 runtime source: `919bfab`
- runtime image: `sha256:a1733a465f4a509960745f621952f9fc70c848c6fd9ae65ad07dded16e5fa911`
- private plan artifact: `pulse-independent-ab-20260922-01`
- plan SHA-256: `38e013a296de1dab8db4521320586654ca4fe350a9f1a34c4cb7bee556772ab0`
- raw series: `JFS-7974c178ad369a750f63417429e19f26`
- raw-series plan SHA-256: `7131a1649f53b9cd21816cdf4a3c5ed153f62569c451ac2df6b18b5dc218f536`
- identity binding SHA-256:
  - 상단: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - 하단: `7b7be7835ebd264d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`

## 역할 전환과 durable journal

실제 write는 아래 네 건뿐이다. 모두 action 1개, physical write 1회, 최종 phase `VERIFIED`다.

| UTC | 대상 | 전환 | request id | journal SHA-256 |
|---|---|---|---|---|
| 06:35:28–06:35:30 | 상단 | `master → independent` | `mqtt_b355191065b1d9d411798bb047bd1fdc` | `746c4b898305142742385212cc6653ab64106f3b16cef632273b9258698a7990` |
| 06:36:05–06:36:06 | 하단 | `async_slave → independent` | `mqtt_03b01089f9a20241667d700a1319fb74` | `0474f34d85b4a40ee2db9c8a592a9877c5af6d3267bd6b01759a272134b1b92f` |
| 07:04:54–07:04:55 | 하단 | `independent → async_slave` | `mqtt_7c8cb8dc9c7e6d87ddd271f1b6901ced` | `1f5bdd51c48e6a3b8b16ec8f22f93a5d3214ecef69aa1f9b3594b47711abd26b` |
| 07:05:19–07:05:21 | 상단 | `independent → master` | `mqtt_f26f3194ebc7e57ae24850ba7afd80ea` | `30760a649d68bc3c037b9f6cb1922407dc6e629c1ec74a77af02d2e46191773f` |

각 journal의 `expected_readback`은 `Linkage` 하나뿐이다. 보존된 forward payload SHA-256은
`independent=fe38f339faa2ed5e82e282107c2808ba029e8d82a8354201aa7627aab03f7239`,
`async_slave=c02514f9291162f4b02a76df76188ddcb68d3099b289cdd97e4fd2dc21a3f2c7`,
`master=1cf1b6b41456aa10736f70cd95e2862c4007694155cfd515b35bb40dda47236d`이며, runtime의
`build_control_payload(schema, {"Linkage": value})`를 독립 재계산한 결과와 각각 바이트 단위로
일치했다. 따라서 이 실행에서 우리 코드가 하단에 `Mode=feed`나 `Frequency=70`을 보낸 경로는
없다.

## preserved raw 비교

모든 pair는 원본 wire frame을 보존한 증거 등급 **(a)** 다. 시각은 UTC다.

| raw attempt | UTC span | 역할 상태 | 활성 Auto 상태 | 하단 manual 상태 | 판정 |
|---|---|---|---|---|---|
| 347 | 06:34:18–06:34:40 | 상단 master / 하단 async | 양쪽 tidal | tidal, Flow40, F5 | 시험 전 baseline |
| 348 | 06:39:18–06:39:40 | 양쪽 independent | 양쪽 tidal | tidal, Flow40, F5 | role 외 변화 없음 |
| 353 | 07:04:18–07:04:40 | 양쪽 independent | 양쪽 **pulse, Flow45, F70** | **tidal, Flow40, F5** | 독립 Pulse 경계 정상 보고 |
| 354 | 07:09:19–07:09:42 | 상단 master / 하단 async | 양쪽 **pulse, Flow45, F70** | **feed, Flow40, F70** | 재결속 뒤 feed 재현 |

원본 frame SHA-256은 다음과 같다.

| attempt | 상단 | 하단 |
|---|---|---|
| 347 | `8405750934decd22316048ab01f611243fddea030e6bc3197f1b596e9dad7b7c` | `3aab3bccc9139d4680d0164ca75f6389a377f742278de1a61c8cc89b7be46f77` |
| 348 | `0961ad071d2060a34f823c71f4abc20e17a259d815fa910f9c65c529b17dfccd` | `9400dad1983c05de41e401de51625bad63e7b9ad32603502971be659d1e4333d` |
| 353 | `2fde00472f63850016294dedfe3b494e1ad645e645a5724a47376a65178c7432` | `8c628dee7f2bb572035f295287798db1956fc6b8c16e7787c16228ab1a75c7ba` |
| 354 | `bdf58f1f10b062115569b17ec5c2b5ec1e549fa34145af7a22697e834ec91f94` | `a293cf2d86b9d7debace3c4871ac9b2c7f5e614af30350eb504c122af76e55ba` |

네 pair 모두 power ON, TimerON, asserted fault 0이었다. 상단 schedule image SHA-256은 모든 pair에서
`73e0f45b5ce883f15dd89c14b5fe2d3bf8dce55a77bd47ab2285e7c60b7d1fec`, 하단은
`2e6a9814c08ff86dc6c5f3a415e1df40c9e878d64d76ebb6fae3b1cae21b9616`로 불변이었다.

보존된 read-only full-state event에서는 전환 시점도 더 좁게 확인된다.

- 07:05:08: 하단 `independent → async_slave`; 하단 Mode·Frequency 변화 없음.
- 07:05:38: 상단 `independent → master`가 처음 관측된 같은 sample에서 하단
  `Mode tidal → feed`, `Frequency 5 → 70`이 함께 관측됨.

이는 30초 구조화 event이므로 정확한 wire 전이 시각은 cadence 범위 안에서만 주장한다. 원본 raw
attempt 353·354가 전후 상태를 별도로 고정한다.

## 결론

**이 실행으로 새로 확정된 사실:** 이 하단 장비는 같은 35-slot image와 TimerON 상태에서
`independent`이면 16:00 Pulse 슬롯의 `AutoMode=pulse`, `AutoFlow=45`, `AutoFreq=70`을 정확히
보고한다. 하단을 async로 둔 뒤 상단을 master로 결속하자, schedule image와 Auto Pulse 값은
그대로인데 하단 manual `Mode`가 `feed`로 바뀌었다. 우리 송신은 role-only였으므로
`Mode=feed`는 schedule 저장기·자동복구·이번 local-control payload가 만든 값이 아니다.

따라서 이번 pair에서 관측된 Pulse 구간 정지의 원인은 **native master→async 결속 경로의
Pulse 전달/변환 동작**으로 좁혀졌다. 내부 펌웨어 코드의 어느 분기가 잘못됐는지는 이 raw만으로
명명하지 않지만, Async 결속이 원인에 필요하고 하단 장비의 independent Pulse 자체는 원인이
아니다.

복구 뒤 상단 master, 하단 async_slave, 양쪽 power ON·TimerON·fault 0, 원 schedule image exact를
확인했다. 다만 복구 상태에서도 Pulse 구간의 하단은 `Mode=feed`를 다시 보고하므로, 원상복귀는
운영 topology 복구이지 결함 제거가 아니다. 물리 파형·유량은 계측하지 않았으며 raw datapoint
판정과 사용자의 기존 물리 정지 관측을 구분한다.
