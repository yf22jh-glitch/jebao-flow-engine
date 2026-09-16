# 등록 장비 완료 체크리스트

2026-09-16 사용자 지시: 장비 여러 대를 동시에 진행하지 않는다. **DC 리턴 → Pro → Bar →
Aquarium 메인 리턴 → 도징** 순서로 구현·검증·배포 준비를 닫는다. 메인 리턴은 제외가 아니라
후순위다.

체크는 해당 단계의 증거가 있을 때만 한다. 미리보기는 송신 구현이 아니며, 오프라인 테스트는
실기 수용 증거가 아니다. 현재 운영 Pro/Bar 배포는 이 작업에서 교체하지 않았다.

## DC 리턴 — 소프트웨어·오프라인 검증·배포 후보 준비 완료

| 기능 | 코드 경로 | 직접 검증 | 배포 준비 |
|---|---|---|---|
| 전원 | 기존 power writer/producer, ON의 활성 출력 bounds | OFF 복구 계약·직접 명령 회귀 통과 | power CLI·HA switch 연결 |
| 수동 유량 | writer + 자동 저출력 준비/원본 복귀 | TimerON/OFF 각각 시험·복귀 테스트 통과 | preparation-flow CLI·HA number 연결 |
| 수동 모드 | writer + 자동 준비/원본 복귀 | pulse/sine enum·TimerON/OFF 테스트 통과 | preparation-flow CLI·HA select 연결 |
| 수동 주파수 | writer + 자동 준비/원본 복귀 | TimerON/OFF·실제 자기 필드 복귀 테스트 통과 | preparation-flow CLI·HA number 연결 |
| 급이 시간 | writer + 자동 준비/원본 복귀 | TimerON/OFF·실제 자기 필드 복귀 테스트 통과 | preparation-flow CLI·HA number 연결 |
| Schedule 화면 타이머 | 두 필드 명령 + bounded image 시험/복귀 | OFF 시 전원도 OFF, 고장 복구 순서 테스트 통과 | program producer·CLI·HA switch 연결 |
| Manual 화면 타이머 | 별도 manual_enable + bounded image 시험/복귀 | OFF 시 전원도 OFF, 고장 복구 순서 테스트 통과 | program producer·CLI·HA switch 연결 |
| 시간표 저장·편집·삭제 | 48-slot image, pulse/sine, TimerON에서도 저장 | A/B/자기 raw 복귀, trial/output bounds 테스트 통과 | program producer·CLI·MQTT apply 연결 |
| 시간표 전체 초기화 | EE → populated → EE + 자기 raw 복귀 | 두 EE 액션만 reset 영수증, 고장 복구 검증 | program producer·CLI·HA button 연결 |
| 장비 시각 설정 | 현재 설정 시간대의 clock command | 진행하는 시간 검증, 실패/관측 복구는 재송신 0 | clock producer·CLI·HA button 연결 |

- [x] 활성 DC 앱 템플릿의 timer 두 화면·mode 0..4·단일 필드 명령 근거 확인.
- [x] DC timer planner/연결: 순수 planner 27 PASS, 관련 묶음 111 PASS.
- [x] 같은 요청 상태 재설정은 no-change, 변동 Auto/시각은 일치 조건에 넣지 않음.
- [x] 타 장비 미완료 기록과 현재 대상의 복구 소유권을 분리. 직접 테스트 포함 8 PASS.
- [x] 순서 지정 복구 + DC 준비형 producer 통합 검증.
- [x] 위 10기능의 송신/CLI/HA 연결 검토. HA 9 controls + schedule API 1.
- [x] Mac 전체 회귀 3368 PASS / Linux 전용 1 SKIP; Ruff·diff 검사 통과.
- [x] 대상 한 대 설정, DC template mount, 새 manifest/image 및 기존 입력·Compose 복귀 자료 준비.
- [x] 최종 후보 Linux 전체 회귀 3371 PASS(193.44초), Ruff·diff 검사 통과.
- [ ] 실기 검증과 활성화: 별도 실행 결과로만 체크. 아직 장비 write 0.

DC 구현·시험 상세와 배포 후보는 [DC 앱 기능 구현 및 배포 준비](dc-app-control-release.md)를
따른다. 전원 자격의 기존 저출력/manual 진입 조건은 유지되며, **전원 자격까지 자동으로
준비·원복하는 wrapper는 없다**. 4개 scalar와 timer/schedule wrapper의 자동 준비와 혼동하지
않는다. 정상 power 조작 자체는 TimerOFF/Flow45를 요구하지 않는다.

## Pro — 앱 기능 12종, 별도 native 확장 의미는 분리

DC 소프트웨어 변경은 `f9c74ff`로 보존했다. Pro는 기존 native Sync/Async와 9종 경로를
유지하면서 아래 누락의 구현·오프라인 검증·별도 배포 후보 준비를 마쳤다.

- [x] schedule_reset: 기존 EE/populated/EE/자기 원본 복귀 producer·CLI 경로와 Pro 테스트 확인.
- [x] device_clock_sync: Pro writer·자격 producer·CLI·HA button 연결 및 read-only 복구 검증.
- [x] slave_common_flow: 기존 scalar 송신의 전용 자격·복구·UI 연결, ACK 뒤 실패 포함 26 PASS.
- [x] slave_per_slot_flow_extension: 기존 compiler/full-image/coordinator/48개 슬롯 UI 확인.
  앱 기능이 아니므로 일반 image 왕복을 확장 자격으로 바꾸지 않음. Q2 의미 검증은 아래 문서로 park.
- [x] 앱 12기능 exact 집합·HA 11 control + schedule topic 1·실물 귀속 직접 테스트.
- [x] 기존 native/시간표 관련 회귀 및 Mac 전체 3417 PASS / Linux 전용 1 SKIP.
- [x] 별도 Pro 후보 image와 pair/기기별 비활성 입력·fresh manifest 준비, 실제 영수증 0.
- [x] Pro 전체 기능 집합·기존 native 회귀·배포 후보 검증: Linux 3418 PASS / 0 FAIL.
- [ ] 새 Pro 실기 수용·운영 활성화. 현재 운영을 바꾸지 않았으며 새 영수증 발급 0.

[Pro 앱 기능 및 배포 후보](pro-app-control-release.md)에 기능별 경로, Q1/Q2와 wire 자격의
차이, 확장 의미 판정의 두 대안·park 영향·재개 조건을 기록했다. 실기와 운영 활성화는 아직
체크하지 않는다.

## Bar — 앱 기능 11종, 소프트웨어·배포 후보 준비 완료

Pro 후보를 보존한 뒤 Bar만 진행했다. 프로그램 master의 잘못된 gap 인자와 실제 펌프 간
연동 의미는 [Bar 구현·후보 기록](bar-app-control-release.md)에 별도 한계로 남겼다.

- [x] Bar 시각 writer/producer 연결, Pro/DC/Bar clock 관련 직접 회귀 52 PASS.
- [x] Bar feed switch / PulseTide / Linkage의 활성 앱 handler 형식과 복원 범위 대조.
- [x] 위 3기능 및 compound mode 자격 producer·CLI·기존 scalar 경로 통합, 직접 40 PASS.
- [x] Bar 앱 11기능 exact 집합·실물별 HA 10 control + schedule API 1 검증.
  compound 명령·시간표·HA 관련 묶음 122 PASS. 전체 회귀와 자격 producer 완료는 별도다.
- [x] Mac 전체 3605 PASS / Linux 전용 1 SKIP, Linux 전체 3606 PASS(194.66초).
- [x] 별도 image·Bar 한 대만 허용한 비활성 11-job 입력·fresh manifest 준비.
  image 내 schema 일치, 예상 영수증 22장/실제 발급 0, 기존 운영 입력 불변 확인.
- [x] Bar 앱 11기능의 송신·자격 경로·HA 연결·오프라인 검증·배포 후보 준비.
- [ ] Bar 새 실기 수용·전체 기능 활성화. 이번 단계의 장비 write·운영 교체는 0.

## Aquarium 메인 리턴 — 일반 앱 7기능 후보 준비, 통신 인계는 별도 보류

Bar 후보는 `dccb17d`로 보존했다. cached shuibeng 두 사본의 같은 명령 형식을 대조하되,
실제 selected locator의 활성 template 바이트를 확보한 것으로 승격하지 않는다.

- [x] 앱 compound payload 목록과 6-byte/48-slot geometry 대조.
- [x] 앱 동봉 productConfig의 통신 Mode 선언 확인: 0=AP, 1=Wireless.
  전용 cached UI에는 전환 handler가 없으며 네트워크 전환 수용 증거와는 별개다.
- [x] 시각 writer/producer/CLI 연결, clock·CLI·preview 관련 108 PASS.
- [x] 전원·속도·급이 시간: compound fresh-copy 및 기존 준비/복구 통합 직접 검증.
- [x] timer ON/OFF: slot seed와 persistent 자기필드 복원, 시험 경로 연결.
- [x] feeding ON/OFF: TimerOFF 동반 명령과 자기필드 복원, 급이 경과시간 제외 명시.
  두 기능 producer 직접 32 PASS; ACK 뒤 실패·실제 coordinator 원복·ON 재전송 0 포함.
- [x] transport: vendor-declared encoder 방향 검증. AP/Wireless 인계·복귀는 미구현으로
  실행 표면에서 제외, [두 대안·재개 조건](aquarium-app-control-release.md)에 따라 park.
- [x] 시간표 저장·편집·삭제: full image + TimerON일 때 별도 seed, 실제 coordinator 복구 검증.
- [x] 8개 선언 중 실행 후보 7종의 exact 집합·HA 6 controls + schedule API 1 검증.
- [x] CLI 시험 경로·전체 회귀 및 별도 비활성 배포 준비.
  Mac 3759 PASS / Linux 전용 1 SKIP, Linux 3760 PASS, Ruff·diff 검사 통과.
  활성 template 미확보이므로 observer/dry_run=true/쓰기 허용 0대, 7 jobs 실행 비활성.
  [후보 image·schema·산출물 digest](aquarium-app-control-release.md)를 보존했으며 실기 준비 완료와 구분한다.
- [ ] 활성 template 바이트 확보, 실기 수용·활성화. 현재 device write 0.

## 이후 장비 — 앞 장비를 닫은 뒤 시작

다음 대상은 **도징 — protocol/schema 수정 승인 대기**다. DC/Pro/Bar/Aquarium의 비활성
소프트웨어 후보를 보존하고 한 기종씩 진행한다. 앞 후보의 바이트를 새 schema 입력과 혼용하지 않는다.

- [x] Aquarium 메인 리턴 일반 7기능의 구현·오프라인 검증·비활성 후보 보존.
  transport 인계·원복 및 활성 template 결속은 사유·두 대안·재개 조건을 기록해 보류했다.
  실기는 제외가 아니라 아직 실행하지 않은 별도 단계다.
- [ ] 도징: 기존 사용자 요구(일일량·횟수·분할·평균 interval)를 유지하면서 실제 channel,
  schedule/timer, interval, calibration 및 전원 송신 경로 구현. protocol/schema 동결
  예외 응답 대기. 실제 투여를 과거 값 복원으로 되돌렸다고 보고하지 않음.
- [ ] 그룹 제어: 기존 구현의 활성화 여부를 장비 기능과 별도로 확인.

## 검증 원칙

원장은 실행 기록·중복 송신 방지·자기 복구에 사용한다. 완료된 과거 기록이나 다른 장비의
미완료 기록을 정상 조작의 조건으로 삼지 않는다. 같은 장비에서 아직 실행/복구 중인 변경은
중첩시키지 않는다. 과거 시각·Auto 값·사용자가 바꾼 설정과 같아야 한다는 새 조건은 넣지
않으며, 보내는 명령이 소유하는 필드의 반영과 대상 실물, 출력 한도를 구분해 확인한다.
