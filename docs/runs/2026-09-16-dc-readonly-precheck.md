# run: 2026-09-16-dc-readonly-precheck

## 질문과 범위

DC 리턴의 남은 기능을 구현하기 전에 현재 운영이 보고하는 수동/시간표 상태를 구분했다.
이후 사용자는 메인 Aquarium 리턴을 제외하는 것이 아니라 **나중 순서에 구현·실기**하라고
정정했고, 등록 장비 전체의 누락 기능 구현을 요청했다. 이번 기록은 DC MQTT 구독뿐이다.
장비 직접 연결·설정 전송·서비스 변경·자격 발급·앱 조작은 모두0이다.

- source: `2181f5e` (읽기 당시 clean tree).
- 증거 등급: **(b) 보존된 구조화 MQTT 출력**. 원 wire나 물리 유량 측정이 아니다.
- opaque artifact: `dc-live-20260916.VRlZRz/current-dc-state.json`.
- UTC span: `2026-09-16T13:26:57.519658Z`–`2026-09-16T13:27:32.533890Z`.
- fresh publication 시각: `2026-09-16T13:27:26.307676Z`.
- identity SHA-256: `c01f4ffd40a3741508550ff0d6f6c609989ee2d98f19620ee4559e1f3a50cde8`.
- artifact SHA-256: `6030c8ccc0f2c3da16ac806c183906a0db0ae7008e108292454b0baf984f34ce`.

## 관측

online, SwitchON=true, TimerON=true. 수동 Mode=constant, Flow=81, Frequency=5,
FeedTime=10이며 현재 AutoMode=constant, AutoFlow=60, AutoFreq=5, AutoFeedTime=0이다.
수동81을 현재 실제 운전 출력이라고 해석하지 않는다. Auto60 역시 보고값이지 물리 계측값이 아니다.
저장된 세 구간은 00:00–08:01 constant60, 08:01–22:00 feed0, 22:00–23:59 constant60이다.
관측된 FeedTime0을 새 입력의 유효성 근거로 쓰거나 임의 정규화하지 않는다.

운영 입력에서 해당 장비 write 허용은 false, limits는30..100이다. 현재 Pro/Bar 운영은 유지했다.
구독만 했으므로 원복할 쓰기는 없다. 전체 journal 무변경을 새로 감사한 실행은 아니다.

**새로 확정된 사실:** 운영 publication에서 활성 시간표 값과 비활성 수동 값이 다르며,
기존 TimerOFF·저출력 사전준비형 qualifier만으로 현재 DC를 자동 준비할 수 없다.
이는 DC 타이머와 자동 준비/소유 원복 구현의 공백이지 장비 고장의 근거가 아니다.
시각·Auto·사용자 변경값을 이 관측과 같아야 하는 미래 admission gate로 사용하지 않는다.

DC active template의 보존 사본은 content SHA-256
`bc20fe3bb0723209fb477f64f9e210d973c1588d72cfecff77d4a9ee3716fbaa`,173273bytes다.
독립 정적 검토에서 Manual 화면은 TimerON과 inverse SwitchON, Schedule 화면은 같은
SwitchON을 쓰는 두 동작이 확인됐다. 실제 firmware 수용은 이 관측으로 주장하지 않는다.

다음은 앱 동작별 구분을 보존한 소프트웨어 구현이다. 실기를 반복하거나 다른 제품 명령을
대용으로 보내지 않는다. 처음 관측한81/60은 예시이지 고정 통과 조건이 아니다.
