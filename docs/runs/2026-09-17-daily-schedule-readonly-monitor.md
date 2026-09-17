# run: 2026-09-17-daily-schedule-readonly-monitor

## 목적·질문

2026-09-17에 적용한 Pro master/Async와 MCP-70 Bar의 31구간 시간표가 하루 동안 장비가
보고하는 역할·전체 schedule image·현재 `Auto*` 값으로 계속 일치하는지 MQTT 구독만으로
관측한다. 동시에 전날 Pro master에서 현장 표시된 `Er03`이 보존 raw에서 어떤 fault로
나타났는지 확인한다.

> 지정 시간표의 미래 경계가 보고값 기준으로 순서대로 적용되는가? 전날 `Er03`은 서비스
> 중단이었는가, 아니면 장비가 계속 통신하면서 보고한 device fault였는가?

동결된 native ASYNC write 하네스는 수정하거나 실행하지 않았다. collector는 MQTT state
topic을 subscribe할 뿐 publish하지 않으며 장비 session과 write 경로를 열지 않는다.

- 실행 source commit: `ce4f80f399adce513aa4ff83437765d6341a0a1c`
- runtime image: `sha256:4ece904bf877b178e9b3662037d69fcde048cb3d3e1635cef884b6f8aff3ee1a`
- protocol schema: `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`
- 관측 주기: 30초
- 본 관측 시작: `2026-09-17T04:41:00.063438Z`
- 예정 종료: `2026-09-18T04:35:00Z`
- `hardware_write=false`, `mqtt_publish_count=0`

## 전날 `Er03` raw 재검토

보존된 전날 collector에서 Pro master는 `2026-09-16T02:18:14.506492Z`까지 fault byte 451이
`0`이었고, 다음 표본 `2026-09-16T02:18:44.475690Z`에 byte 451이 `32`로 바뀌었다. 디코더가
assert한 fault는 정확히 `Fault_no_liveload` 하나였다. 같은 표본에서 통신 응답, power ON,
TimerON, master 역할, 전체 schedule image는 계속 존재했다. 따라서 service가 죽어 응답을
잃은 사건으로 볼 수 없다.

이 fault는 이후 collector가 끝난 `2026-09-16T07:14:52Z` 직전까지 관측된 master 표본
573개에 연속으로 남았다. 다른 Pro에는 같은 fault가 없었다. 제조사 GMP 설명서의 `Er03`도
펌프 공회전/dry-run 계열로 설명하고 펌프가 물로 채워졌는지 확인하도록 안내한다. 보존
`Fault_no_liveload`와 같은 방향이다. 다만 실제 원인이 수위, wet-side 결합, 이물 막힘 중
무엇이었는지는 raw만으로 확정하지 않는다.

- 증거 등급: **(a) preserved raw artifact**
- opaque artifact: `pro-daily-overnight-02`
- clear sample: `overnight-02/00181-flow_pro_d0a4.json`
  - sample SHA-256: `22181dc61a706ed7c04f1c007276603d826d3c1372f6b2cfaf67da5a4c4d6a30`
  - raw status SHA-256: `55a9f8bb283f943fed16e4b4636b7c31d9e0853b95df3d78de2acdf97c5d8583`
- first fault sample: `overnight-02/00182-flow_pro_d0a4.json`
  - sample SHA-256: `eea721e43d8d50ed257662bc757e968e1c50285616d178adb9d86419efae2097`
  - raw status SHA-256: `51d54dd66471acf07bfbf3f68e7f7aec3963f6e021b9a61c60dbfbf260212628`
- 제조사 설명서: [GMP Series Smart Water Pump manual](https://ae-pic-a1.aliexpress-media.com/kf/S9944733feada489aabc31134b0ee45678.pdf)

## 하루 관측 시작 상태

첫 collector `daily-three-pump-monitor-20260917.01`은 보존했지만, Constant 구간에서 의미 없는
frequency까지 비교하고 Bar의 갱신되지 않는 clock register를 실시간 시각으로 취급해 정상
상태를 mismatch로 표시했다. 장비 write 없이 중지했고 파일은 덮어쓰지 않았다.

그 두 판정만 제거한 `daily-three-pump-monitor-20260917.02`를 별도 artifact로 시작했다.
Constant에서도 mode와 flow는 계속 비교하고, 전체 31구간 image, 역할, power, schedule enable,
모든 fault bit는 매 표본 검사한다. Pro clock은 계속 검사하며 Bar clock register는 정보로만
보존한다.

첫 여섯 표본(`2026-09-17T04:41:00.063438Z`–`2026-09-17T04:43:30.091429Z`)은 모두 일치했다.

- Pro master: role `master`, `Pulse / Flow 40 / Frequency 80`, fault 0
- Pro Async: role `async_slave`, `Pulse / Flow 35 / Frequency 80`, fault 0
- Bar: role `independent`, `Sine / Flow 45 / Frequency 70`, fault 0
- 세 장비 모두 power ON, schedule ON, 31구간 exact

보존 증거는 원본 frame이 아니라 MQTT 상태를 30초마다 구조화해 fsync한 것이므로 등급은
**(b) preserved structured/durable daemon artifact**다.

- opaque artifact id: `daily-three-pump-monitor-20260917.02`
- collector SHA-256: `aa70639fbd43846c73ef02682f1cedc69f65dea83989e82485fe740d419eb5c1`
- `plan.json` SHA-256: `62f2b85de209e247bed9fbb1092c2ec9d814605c79484213bb945360152d72e2`
- first sample SHA-256: `b556d5f4594c15d0badd5ffd3186f8c48f84fb8fd37978528d6d3806c2cdcf05`
- identity binding SHA-256:
  - master: `92a6c3d20af3692f5bcec93475c142d0261e7b66112b39f35e3464a8a01021b7`
  - Async: `7b7be7835ebd264d3d5df1b58fa3ff487fcfd815ec03a7e700e054a85e1b84`
  - Bar: `6597ee042d85c7a9f438d270e9bff9bf443e6719ef8748fa919359aea892d840`

## 현재 판정 — 진행 중

**이 실행으로 지금까지 새로 확정된 사실:** 전날 master의 `Er03` 시점에는 service 통신과
시간표 보고가 계속됐고, raw fault가 `Fault_no_liveload`로 clear→assert 전환했다. 오늘 첫
표본들에서는 그 fault가 해제돼 있으며 세 장비가 현재 구간과 요청 역할을 모두 일치시켰다.

하루 전체 경계의 연속 동작과 fault 재발 여부는 아직 확정하지 않는다. 예정 종료 뒤 sample 수,
mismatch/fault episode, 각 경계 전환과 종료 artifact digest를 이 파일의 새 절로 덧붙인다.

## 진행 중 추가 — 13:38 KST 첫 경계

판정식이 과도했던 첫 collector도 원래 MQTT 관측값은 보존했으므로 13:38 경계를 재검토했다.
`2026-09-17T04:37:43.350483Z`에는 세 장비 모두 직전 `Constant / Flow 35` 구간이었다.
경계 뒤 첫 30초 표본 `04:38:13.356662Z`에서는 master가 먼저
`Pulse / Flow 40 / Frequency 80`으로 바뀌었고, Async와 Bar는 아직 직전 값을 보고했다.
다음 표본 `04:38:43.364431Z`에는 Async가 `Pulse / Flow 35 / Frequency 80`, Bar가
`Sine / Flow 45 / Frequency 70 / PulseTide false`로 합류했다. 이후 pilot 종료까지 같은 값이
유지됐고 세 장비 fault는 전 표본 0이었다.

따라서 13:38 전환은 세 장비가 한 표본에서 원자적으로 바뀐 것이 아니라, 30초 cadence에서
master 선행 후 다음 표본까지 모두 수렴한 것으로 보존됐다. 이는 물리 파형 동시성을 증명하지
않으며 MQTT가 보고한 `Auto*` 전환 순서만 기술한다.

- opaque artifact id: `daily-three-pump-monitor-20260917.01`
- `plan.json` SHA-256: `3843b8f518ad4b8ab7ece54ede5257d77498d85f3718812c42155ed60c07ab05`
- 경계 전 sample SHA-256: `223a4d2d7007fb11877ec714be11b330fdcc3099557a00cb8ec88eb453b0fb98`
- master 선행 sample SHA-256: `ca083d52d7e0937ddc0fd685d8cd899ac2e7de4eb7d57233a6f01fdcf1a286ba`
- 전체 수렴 sample SHA-256: `0d9ada63ddf00dae48771243a65d25e5d3020c7162b946b739a856b5b5ec1394`

## 진행 중 추가 — 14:00 KST 경계

`daily-three-pump-monitor-20260917.01`과 `.02`의 보존 표본을 함께 읽는 interim analyzer가
14:00 경계도 확인했다. 경계 뒤 `2026-09-17T05:00:30.329465Z` 표본에서 세 장비가 모두
slot 19에 수렴했다. 경계 기준 수렴 시간은 30.329초였다.

- Pro master: `Sine / Flow 45 / Frequency 60`
- Pro Async: `Sine / Flow 40 / Frequency 60`
- Bar: `Pulse / Flow 45 / Frequency 80 / PulseTide false`

분석 시점까지 pilot 8개와 main 41개, 합계 49개 표본이 연속 ordinal이었고 65초를 넘는
cadence gap은 0개였다. 세 장비 모두 role mismatch, power OFF, schedule OFF, 31구간 image
mismatch, missing/stale sample, fault episode가 0개였다. sample의 hardware write와 MQTT
publish도 각각 0개다.

이는 진행 중인 중간 판정이다. `complete_counts_match=false`와 `deadline_covered=false`는 계획된
24시간 종료 시각 전이라 정상이며, 최종 성공으로 읽지 않는다. 종료 뒤 같은 analyzer로 전체
31경계와 fault 재발 여부를 다시 판정한다.

- analyzer SHA-256: `3cda036a6c1391e619f56055a647c1aff21dbc031fb25a8eecf641e16b2ddbc5`
- analyzed at: `2026-09-17T05:01:13.317641Z`
- opaque artifacts: `daily-three-pump-monitor-20260917.01`,
  `daily-three-pump-monitor-20260917.02`

## 조기 종료 — 사용자 시간표 교체

사용자가 35구간의 새 시간표로 교체하고 적용 뒤부터 새 24시간 관측을 시작하도록 요청했다.
따라서 이 31구간 관측은 계획한 24시간 판정을 완료하지 않고
`2026-09-17T05:22:30.807654Z` 마지막 표본에서 종료했다. 기존 파일은 덮어쓰지 않았고 종료 직전
interim 결과를 `superseded-analysis.json`으로 별도 보존했다.

pilot 8개와 main 84개, 합계 92개 표본의 ordinal은 연속이었고 65초 초과 cadence gap은 0개였다.
관측한 13:38·14:00 두 경계는 모두 수렴했다. 세 장비의 role mismatch, power OFF, schedule OFF,
31구간 image mismatch, missing/stale sample, fault episode는 전부 0개였다. collector의 장비 write와
MQTT publish도 0개다.

이 결과는 조기 종료된 구간만 설명하며 나머지 29개 미래 경계를 판정하지 않는다. 새 35구간
시간표와 새 24시간 관측은 별도 run·artifact로 기록한다.

- opaque artifact id: `daily-three-pump-monitor-20260917.02`
- partial UTC span: `2026-09-17T04:41:00.063438Z`–`2026-09-17T05:22:30.807654Z`
- `superseded-analysis.json` SHA-256:
  `36747b53d4fb9a6d9895564001ae07a90f090b3db990459d97d965301fc09c78`
- 종료 사유: 사용자 요청에 따른 새 시간표 교체; fault나 collector 실패가 아님

## append-only 정정 — 종료 직후 마지막 표본 포함

위 `superseded-analysis.json`을 만든 뒤 collector가 종료되기 전에 main ordinal 85 한 건을
추가로 fsync했다. 기존 파일은 수정하지 않고, 보존된 pilot·main 전체를 같은 analyzer로 다시
읽어 별도 최종 감사 산출물을 만들었다.

- 재분석 시각: `2026-09-17T07:13:59.812341Z`
- 실제 마지막 표본: `2026-09-17T05:23:00.814531Z`
- 표본: pilot 8 + main 85 = 합계 93
- ordinal: pilot·main 각각 연속
- 65초 초과 cadence gap: 0
- 관측된 경계: 2, 수렴한 경계: 2, 미수렴: 0
- 세 장비 role mismatch·power OFF·schedule OFF·31구간 image mismatch·missing/stale: 0
- 세 장비 fault episode: 0
- sample hardware write: 0, MQTT publish: 0
- analyzer SHA-256: `3cda036a6c1391e619f56055a647c1aff21dbc031fb25a8eecf641e16b2ddbc5`
- opaque artifact:
  `daily-three-pump-monitor-20260917.02/superseded-analysis-final-audit.json`
- artifact SHA-256: `365e04ce26206e856ccd07f6a0aa669b886c6c842b524cb01595a31be8132b05`

마지막 한 표본을 포함해도 조기 종료 판정은 바뀌지 않는다. 13:38·14:00 두 경계만 관측했고
나머지 29개 미래 경계와 원 예정 종료 시각은 덮지 못했다. 따라서 이 산출물을 31구간의 하루
완료 판정으로 승격하지 않는다. 이후 실행 중인 관측은 사용자가 교체한 35구간 시간표의 별도
run·artifact다.
