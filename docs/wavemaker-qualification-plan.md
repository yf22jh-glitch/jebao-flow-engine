# 수류모터 제품 자격 발급과 배포

사용자는 기존 제어 소실을 막기 위한 새 장비 검증·자격 갱신과 원복까지 승인했다.
대상은 Pro 두 대 및 기존 Bar Flow/power이며 초기화·Bar 시간표 write는 아니다.
배포 준비에서 발견한 결손은 native 실행기 자체가 아니라 7종의 first-write 자격 발급 경로다.
이 작업은 qualification 검사 우회나 synthetic receipt가 아니라 실제 ACK/raw 판정의 발급 연결이다.

## Pro 묶음 — 실제 scalar 명령 18개씩

기존 epoch source로 현재 observer config·manifest·template·schema·물리 신원·firmware를 결속한다.
기존 전역/장비 lease 안에서 두 Pro의 독립 상태 원본 reply와 432B/8개 controls를 보존한다.
먼저 모든 action과 복원 원본을 기존 journal에 내구화한다. 각 단계 실제 before/after raw는
기존 native evidence 체인에 보존한다. 모든 action의 encoder baseline은 **최초 원본**이며
각 단계 직전 관측으로 인용하지 않는다. 실제 직전 값은 별도 before capture다. 복구는
context의 독립 reply만 사용하고 action별 일반 역순 복원을 사용하지 않는다.

| 순서 | 각 Pro의 실제 변경 | 자격 근거 |
|---|---|---|
| 준비 | atomic independent·TimerOFF·ON·constant40/F20 | 준비일 뿐 scalar receipt로 세지 않음 |
| 시간표 | 하루 constant40 → 하루 constant39, 나머지 EE | schedule 두 full432B ACK/read-back |
| Timer | ON → OFF, 앱과 같은 SwitchON 반전 | schedule_enable 두 결합 응답 |
| Mode | sine → constant | manual_mode 두 응답 |
| Frequency | 21 → 20 | manual_frequency 두 응답 |
| Feed/custom | 각각 1→2, 원래 1이면 2→1 | 각 선택 필드 두 응답; 새 입력 0 금지 |
| Flow | 39 → 40 | manual_flow 두 응답 |
| Power | OFF → ON, TimerOFF 유지 | power 두 응답 |
| Role | master → independent | linkage_role 두 응답; Sync/Async 수용 증거 아님 |
| 원복 | 기존 native ordered 해제→원432B→원8controls→별도 reply 대조 | 자기 원본 exact proof |

최대 logical action은 Pro별 준비1+기능18, 두 대38과 자기 원복6의 합44다.
Bar는 별도 manual_flow 3 + power 3의 planned logical6으로, 자격 작업 합은 최대50이다.
Bar의 추가 준비가 필요한지는 실제 baseline을 읽고 별도 공개한다. 이 숫자에 후속 Sync/Async
결속·해제나 실패 시 추가 원복을 포함했다고 쓰지 않으며 실행별로 실제 회계를 남긴다.
NO_CHANGE는 실제 전송이 아니므로 receipt를 발급하지 않는다. 실제 전송 수는 journal의
attempt/ACK와 별도로 계상하며 계획 수를 physical 성공 수로 부르지 않는다.
새 시간표를 먼저 넣으므로 TimerON 구간에 활성화되는 시험 Flow도39다. 시작/최종 원본 Auto는
사용자 저장 시간표이며 고정된 현재 slot으로 원복하지 않는다. 준비와 원복 중 출력 변화는 있다.

파형별 모든 값의 펌웨어 유효성을 두 명령으로 증명했다고 쓰지 않는다. 이 qualification은
기존 catalog가 정의한 두 요청·ACK·반환값 기준의 해당 binding이고, 실제 시험한 값은 기록한다.
Pro linkage scalar와 compound native 명령은 다르므로 자격 뒤 Sync/Async lifecycle을 별도로
한 번씩 실행해 새 atomic 명령과 최종 원복을 확인한다. 기존 기능 질문을 반복 측정하려고
10/15분 epoch를 다시 수행하는 것이 아니라 제품 결속·해제 경로 검증이다.

## 발급과 재시작

**writer 수정 landing → 최종 schema 확정 → 이미지 결속 → 그 뒤 첫 자격 write** 순서다.
그 이후 작은 변경도 writer에 하지 않는다. 필요하면 원본 복원·기록 후 해당 자격 currency를
재평가한다. landing 이전 자격을 새 schema에 옮겨 적지 않는다.

발급은 source의 실제 기능 action 두 개가 VERIFIED이고 각각 ACK와 after raw가 존재하며,
새 session 최종 원복 child가 VERIFIED일 때만 한다. raw를 다시 decode해 기대값과 비교한다.
실패/NO_CHANGE/예상값만 존재하는 경우는 자격이 아니다. immutable 후보 파일을 기존 receipt
root에 설치하고 운영 qualification 파일은 덮지 않는다. 중단 후에는 기존 recover-wavemaker로
자기 원본 복원부터 한다. 이미 시도한 frame은 다시 보내지 않는다. 성공 기록 재호출은 오프라인
재검증/발급만 하며 하드웨어 반복이 아니다.

## Bar와 운영 전환

Bar는 기존 두 producer의 ON·TimerOFF·independent·constant·허용 Flow 전제가 실제로 맞는지
읽은 뒤 한 번씩 사용한다. 전제가 다르면 앱 Timer 명령을 추정해 보내지 않고 결과를 보고한다.
필요한 준비/원복은 기존 qualified scalar가 지원하는 경우만 그 원본과 변경을 결속한다.
기존 6개 direct 제어와 새 native 자격을 최종 schema 후보로 병합한 뒤 새 image/generation을
기존 exact image/설정/저널을 보존한 stop-first 방식으로 배포한다. HA 편집 UI가 이번 자격
발급으로 구현됐다고 쓰지 않는다. startup에서 device command를 만들지 않으며 별도로 확인한다.

이전 first Pro 앱 TimerOFF 의무는 이번 실기 **이후**에 남긴다. 지금 OFF→ON 왕복을 만들지
않으며 현재 실제 Timer 상태로 자기 baseline을 잡고 그 상태까지만 복원한다. 이전 앱 원본과
이번 시작 원본을 분리 계상한다. 새 generation 기동을 이전 exact 서비스 복귀로 쓰지 않는다.
