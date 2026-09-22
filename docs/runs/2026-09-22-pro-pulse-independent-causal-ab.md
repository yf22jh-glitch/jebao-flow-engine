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

## 추가 대조 — 이전 기록과 apparent recovery

위 실행을 커밋한 뒤 기존 durable 기록을 write 없이 다시 대조했다.

9월 21일 운영 attach `kaga_daily_async_20260921_01`은 이번 복구와 반대 순서였다. action 6은
상단 `Linkage=master`, action 7은 하단 `sync_slave`, action 8은 하단 `async_slave`였고, 모두
최종 readback `VERIFIED`다. 즉 **master-first 앱 순서에서도** 02:11 UTC 구조화 sample에서 하단
manual `Mode constant → feed`, `Frequency 32 → 70`과 `AutoMode=pulse/Flow40/F70`이 함께
관측됐다. 이번 async-first 복구에서도 같은 결과였으므로 역할 적용 순서가 원인은 아니다.

full-state recorder의 9월 21일 02:11–12:00 UTC 구간을 전수 재계산하면 하단이 `Mode=feed`를
보고한 누적은 27,726.895초(약 7.70시간)다. 의도한 3분 Feeding 구간을 제외한 모든 feed 진입은
Pulse 구간 시작과 일치했고, Constant·Tidal·Nutrient Transport 경계에서는 해당 모드로
빠져나왔다. 12:00:20 UTC(21:00 KST)에는 같은 sample에서
`AutoMode pulse → tidal`, manual `Mode feed → tidal`, `Frequency 80 → 5`가 함께 바뀌었다.

따라서 운영자가 본 “자동 복구”는 별도 복구 write가 아니라 **21:00 schedule 경계에서 Pulse가
Tidal로 끝나며 native link가 다음 모드를 다시 전달한 것**이다. slave timer watchdog의 보존
디렉터리에는 plan 한 개뿐이고 실행 산출물은 0개였으며, 이 구간에서 watchdog write는 없었다.

2026-09-20 선행 실기는 linked slave의 manual Mode를 세 위치에서 고치려 했지만 모두 배제했다.
결속 중 write는 적용되지 않았고, 하단을 independent로 풀어 `constant`를 적용해도 상단이 master를
다시 잡은 1.5초 뒤 `feed`로 되돌아갔다. 그러므로 Pulse 구간마다 단순히 하단 `Mode=pulse`를
재전송하는 것은 지속 가능한 수정이 아니다. 당시 코드는 “manual feed는 latent이고 TimerON을
잃을 때만 정지한다”고 해석했지만, 이번 운영자 관측과 TimerON=true raw는 그 해석이 이 pair의
Pulse 구간에는 성립하지 않음을 새로 보여준다. 9월 18일의 `TimerON=false` 장시간 정지는 별개의
사건으로 유지하며 이번 원인에 합치지 않는다.

## 추가 대조 — 활성 앱의 Linkage wire 계약

활성 Pro template(`com.jiebao.rn.aquariumPumpPro.1`, 배포 결속 digest `c1ed7f76…`)의 모든
`sendCmd` 호출을 write 없이 정적 추적했다. master, sync slave, async slave와 independent를
선택하는 경로는 모두 공통 handler `onSelectLinkage(e)`를 거쳐 정확히 `{Linkage:e}` 한 필드만
보낸다. 특히 async 선택은 `{Linkage:3}` 뒤에 `Mode`, `TimerON`, `AutoMode`, `AutoFlow` 또는
`AutoFreq`를 추가로 보내지 않는다.

이 활성 template에는 `Linkage+TimerON+Auto*` 형태의 program-save compound 명령도 없다.
시간표 저장은 `AutoTime00..47`을 별도 `dataPoint.update` 경로로 갱신하며 Linkage 명령과 결합되지
않는다. 따라서 이번 실행의 role-only payload는 활성 앱의 wire 계약과 동일하며, 우리 코드가 앱의
필수 동반 필드를 빠뜨려 하단을 `feed`로 만든 가능성은 배제된다.

보존 raw가 증명하는 동작 경계는 다음과 같다. 같은 schedule image와 TimerON에서 하단은
independent Pulse를 정상 보고하지만, native master/async 결속이 성립하면 Auto Pulse 값은 유지된
채 하단 manual Mode가 `feed`로 바뀌고 Frequency가 활성 Pulse 슬롯 값으로 바뀐다. 이는 앱도 같은
role-only 명령으로 유발하는 장치 결속 동작이다. 내부 펌웨어의 정확한 코드 분기는 확보하지
않았으므로 그 구현 세부까지 단정하지 않지만, 역할 적용 순서·watchdog·schedule writer·누락된 앱
compound·이번 local-control payload는 원인 후보에서 제외한다.
