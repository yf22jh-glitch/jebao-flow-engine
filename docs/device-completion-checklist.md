# 등록 장비 완료 체크리스트

2026-09-16 사용자 지시: 장비 여러 대를 동시에 진행하지 않는다. **DC 리턴 한 대**의 구현,
검증, 배포 준비를 먼저 마친다. 메인 Aquarium 리턴은 제외가 아니라 후순위다.

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

## 다음 장비 — DC 소프트웨어·배포 후보 정리 후 한 대씩 진행

- [ ] Pro: 기존 Sync/Async·9기능 보존 확인; 시계 실행, slave common/per-slot 자격 및
  reset 활성화 누락 검토. native 동작 미확정 질문을 코드 완성과 혼동하지 않음.
- [ ] Bar: 기존 시간표+timer 완료 경로 보존; power/flow/mode/frequency/feed-duration
  활성화, feed switch/PulseTide/Linkage 자격, 시계 실행 누락 검토.
- [ ] Aquarium 메인 리턴: 마지막 리턴 순서로 timer/feeding/transport/clock/schedule
  경로 검토, 기존 power/speed/feeding-duration 준비형 통합; 실기도 제외하지 않음.
- [ ] 도징: 기존 사용자 요구(일일량·횟수·분할·평균 interval)를 유지하면서 실제 channel,
  schedule/timer, interval, calibration 및 전원 송신 경로 구현. protocol/schema 동결
  예외 응답 대기. 실제 투여를 과거 값 복원으로 되돌렸다고 보고하지 않음.
- [ ] 그룹 제어: 기존 구현의 활성화 여부를 장비 기능과 별도로 확인.

## 검증 원칙

원장은 실행 기록·중복 송신 방지·자기 복구에 사용한다. 완료된 과거 기록이나 다른 장비의
미완료 기록을 정상 조작의 조건으로 삼지 않는다. 같은 장비에서 아직 실행/복구 중인 변경은
중첩시키지 않는다. 과거 시각·Auto 값·사용자가 바꾼 설정과 같아야 한다는 새 조건은 넣지
않으며, 보내는 명령이 소유하는 필드의 반영과 대상 실물, 출력 한도를 구분해 확인한다.
