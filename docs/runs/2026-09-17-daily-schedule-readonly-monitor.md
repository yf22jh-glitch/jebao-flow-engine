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
