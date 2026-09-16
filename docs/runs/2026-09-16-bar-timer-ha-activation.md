# run: 2026-09-16-bar-timer-ha-activation

## 질문·범위

**새 자격으로 Bar 타이머·시간표 표면을 HA에 등록하면서 두 Pro 제어와 기존 복구 기록을
보존하는가**를 확인했다. 사용자는 중단 후 “이어서 계속 진행하자”라고 재개했다.
앞선 [Bar timer](2026-09-16-bar-timer-qualification.md),
[Bar schedule](2026-09-16-bar-schedule-qualification.md),
[Pro renewal](2026-09-16-pro-bar-timer-schema-renewal.md) 결과를 먼저 커밋한 뒤 배포했다.
이번 활성화는 장비 설정 write0이며 리턴·도징·새 Async·시간표 재적용을 포함하지 않는다.

- source `21049eadfbba413b644d9db4408f73db2d367ea0`, clean tree.
- image `5b1ec481dbfe1954794fde5c693fd79f1e74334b6d6b0aae042efb93e37ec95c`.
- schema `0cb054233e2a5691463f0f7b3812cc87f8536e935755f0e3c756f142bb9797fc`.
- generation `eb8512fdd799c2ef97a9a39b2956028b`.
- source와 설치 wheel의 Python156파일 byte 대조: 차이0. 자격 이후12개 지문 입력 불변.
- 새40receipt/20physical-function pair만 병합해11개 binding으로 배포했다. ignored0이며
  구 schema receipt를 새로운 권한으로 재사용하지 않았다.
  같은 Pro 제품의9기능 binding마다 두 실물이 묶이고 Bar2개가 더해져11binding이며,
  물리 장비·기능 pair는18+2=20, 영수증은각2장으로40장이다.
- 실제 mounted 입력은 CONTROL/dry_run=false, write 허용은 Pro2대와 Bar1대뿐이다.
  Pro limits30..100은 기존 수용 범위, Bar limits30..50이다. group control/resume0.
- 입력용 data volume은 새 generation 전용이며, journal·receipt는 기존 external
  `jebao-flow-hardware-safety`를 동일하게 공유한다. 실제 runtime mount와 volume identity 계약을
  확인했다. stage는 network-none·data-only로 유지했다.

## 배포·실제 등록

전환 전 MQTT 구독 감사 `11:15:42.688907–11:16:24.645922 UTC`에서 기존 discovery86개를
실제 보존했고, 이전 expected payload와 base64 decode 후86/86 일치했다. 새 expected와 비교하면
85개 일치·제거 대상1개인 것이 정상이며, 이를 백업 누락으로 세지 않았다. 이때 fresh6/online6,
읽기·쓰기 요청0이다. 이전 Compose·input/data volume·image도 보존했다.
before/after 직접 대조에서도85개 payload는 그대로이며, 나머지 한 엔티티는 Bar
`schedule_enable`의 sensor discovery가 switch discovery로 바뀐 것이다.

Compose 교체는 한 번 실행했고 stage exit0, 새 runtime 시작은
`2026-09-16T11:18:00.928443811Z`다. 최종 running/restart0이다. 전환 중 상태 조회 서비스 공백은
있으며 무중단 배포를 주장하지 않는다. HA Core restart/reload나 dashboard 편집은 하지 않았다.

기동 후 MQTT 감사 창은 `11:19:00.912716–11:19:47.962745 UTC`다.

| 항목 | 실측 |
|---|---|
| retained discovery | expected86/86 payload exact, 제거 대상 잔존0 |
| HA MQTT entity registry | expected86/matched86, missing0, disabled0 |
| 조작 항목 | Pro16 유지 + Bar timer1 =17 |
| 시간표 apply API | Pro2 + Bar1 =3 |
| native lifecycle | 기존 자격을 가진 Pro2대만, 추가 결속 실행0 |
| 상태 | fresh6/online6, live state292건, global online |
| 감사 창 명령 | scalar/native/schedule write 요청0 |

Bar 스위치는 `switch.flow_bar_55e4_schedule_enable`로 등록됐다. Bar 노출 집합은
`schedule_enable`과 `schedule`뿐이며 과거 scalar 자격이 복귀했다고 주장하지 않는다.
시간표는 기존 MQTT request API이며 전용 dashboard 편집기를 새로 만든 것은 아니다.

smoke는 historical native **status**와 도징 **preview** 두 요청뿐이다. status는 원 Async
source의 ROLLED_BACK, `current_state_verified=false`, 마지막 action ACK 없음/UNKNOWN을 그대로
반환했다. 도징은 `hardware_write=false`, `wire_send_available=false`인 오프라인 계획만 반환했다.
응답은 모두 non-retained다. 추가 HA UI→장비 직접 클릭 smoke는 하지 않았으며 앞선 실기 자격을
그 검증으로 바꾸어 서술하지 않는다.

## 기동·smoke 기록 불변

`11:15:42.933690`, `11:19:01.245021`, `11:20:33.686629 UTC` 세 번의 전체 byte inventory가 같다.

- journal2638파일: `32c77355c90a315ce391f3a29a6f8cad5a5da90fc2b44bdb94548ba48786c2ad`.
- receipts138파일: `4dbd7755ab5909186123fb837a210fac78c8ff0adc9b4ec21c0027b2b97fcb9c`.
- 이전 runtime input5파일: `49943ad0b93389c5711323303a5b37adbfd9f29842397155ee31bfba4511472e`.
- operation73개, unfinished0.

journal-first 쓰기 계약과 전체 byte 불변, 읽기 전용 smoke를 함께 근거로 이 활성화 구간의 추가
장비 설정 write0을 확인했다. 장비 시각·현재 슬롯·Auto*나 사용자가 바꾸는 값의 과거 snapshot
일치를 배포 gate로 삼지 않았다.

## 제한·검증 이력

**마지막 보존 Bar 시간표는 Random100이다.** 새 bounds30..50에서 timer ON은 준비 단계에서
거부된다. 먼저 사용자가 선택한 bounded 시간표를 apply해야 켤 수 있다. 이번 배포는 원 시간표를
덮어쓰거나 ON하지 않았다. Time/Auto residual의 현재값은 이번 감사로 새 실측한 것이 아니다.
Bar manual_mode의 이전 UNKNOWN도 재시험하거나 PASS로 바꾸지 않았다.

최종 Linux 전체3192 PASS, Ruff/src/tests clean, macOS3191 PASS/1Linux-only skip이다.
앞선 macOS 실행의 실패3개 중 Bar 미승격을 기대한 stale assertion1개만 고쳤다. 나머지 두 legacy
short-deadline 테스트는 변경 없이 격리 실행과 최종 전체 실행에서 통과했다. Claude는 부하에서
같은 파일의 다른 deadline 테스트가 실패하는 현상을 재현했다. 타이밍 문제를 제품 코드 수정으로
해결했다고 주장하지 않으며 동결된 legacy code/tests는 수정하지 않았다.
중단 전 최종 이미지 시도는 short/full commit SHA 비교로 실패했고, 재개 후 fullSHA를 전달해
같은 검사를 통과했다. digest 검사나 guard를 완화하지 않았다.

## 증거

opaque artifact `bar-timer-live-20260916.12BfJR`, 이번 배포 감사의 등급은 **(b) 보존된 구조화
출력**이다. 새 원 wire frame이나 물리 유량 계측을 추가하지 않았다.

| 산출물 | SHA-256 |
|---|---|
| `activation-mqtt-before.json` | `75aa1e274f5fa1fde71813c941dd019612af7518e9a8484b45c53762f90a19d6` |
| `activation-mqtt-after.json` | `b24e7926260a3b95b12fba41b68f332409319b3523f542090cfff791f904d8b2` |
| `activation-ha-registry.json` | `2eff4d83c9b78f54a3fc58c560e1f520c44a4b89e35cfcddcf93ee5d175dcb0c` |
| `activation-integrity-before.json` | `d062d2ff2ab98ed47b902e620442fb7bc5ad8396ad8d307d55990eed2036f01d` |
| `activation-integrity-final.json` | `43944d1088aa8b903273c617f83630b1e94f77c2f971d60898e49bc3b60b437c` |
| `activation-runtime-inputs.json` | `57c517985d056676fe277a353dcc928e6559b2bafea65960d8e7fb93abdd5de3` |
| `activation-image-check.json` | `acad66853bcc38493f86e133042d0d5ec90f33134a9b199feb471d7c90312b04` |
| `activation-linux-final.log` | `e6e7e1af4571599c1d9b0abdf7c8a5764de5018fc2990f369edad720bdda3de4` |

**새로 확정된 사실:** source-matched 자격으로 Bar timer/schedule 표면이 실제 HA/MQTT 운영에
등록됐고, 기존 Pro 제어·기록 보존 및6장비 상태 수신이 성립했다. Bar의 원하는 운영 시간표 적용,
Async 재결속, 리턴·도징 실기 제어와 물리 출력 검증은 완료 범위가 아니다.

Claude가 로컬 보존본의 실제 discovery payload·registry 기능 집합·런타임 mount·세 시점
inventory·테스트 결과를 독립 대조했다. 배포 차단 소견0이며 위 표면 전환과11binding 산술을
재확인했다. preview 응답은 `summary.results.preview.result.result`의 계획 및
`hardware_write=false`/`wire_send_available=false`를 본 에이전트가 별도로 직접 확인했다.
