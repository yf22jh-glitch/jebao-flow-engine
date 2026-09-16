# 나머지 장비 제어 활성화 전 읽기 전용 확인

사용자가 Pro HA 활성화 뒤 "나머지도 전부 활성화 하자"고 요청했다. 이번 질문은 **남은 장비의
현재 구현과 운전 상태에서 어떤 실기 준비·권한이 추가로 필요한가**다. 기존 제어 runtime의
MQTT publication만 구독했고 명령 publish, 장비 직접 접속, 장비 write, 서비스 변경은0이다.
제품 source/test·writer/schema·qualification 파일도 변경하지 않았다.

## 확인한 현재 상태

구독 구간은 `2026-09-16T07:58:07.593775Z–07:58:42.612784Z`다. 아래 값은 과거 snapshot과
일치해야 하는 영구 조건이 아니라, 이번 준비 범위를 결정하기 위한 시점 관측이다.

| 논리 장비 | 기존 runtime이 새로 발행한 상태 | 기존 producer 진입까지 필요한 준비 |
|---|---|---|
| Bar | ON, TimerOFF, independent, Classic, Flow40/F80, FeedTime10 | 앱에서 Constant 모드 준비 |
| DC 리턴 | ON, TimerON, manual Constant/Flow81/F5, FeedTime10 | 현장에서 TimerOFF·저출력 준비; 현재 AutoFlow0과 manual81은 다른 값 |
| Aquarium 리턴 | ON, TimerOFF, Motor_Speed60, FeedTime10 | 현장에서 저출력 준비 |
| 도징 | ON, timer1/2 ON, timer3/4 OFF, manual4채널 OFF | 투여 실행기·검증 계약 부재; 기존 타이머를 임의 변경하지 않음 |

기존 scalar producer는 앱 범위·config 범위와 first-write 상한45의 교집합을 요구하며,
Bar/DC는 Constant/TimerOFF를 요구한다. 그 상태를 스스로 만들지는 않는다. 현재값을 과거값과
맞추는 새 gate를 추가하지 않았고, 준비를 건너뛰려고 기존 출력·복구 제한을 풀지도 않았다.
앱 준비는 운전 변경이므로 현장 판단을 필요로 한다. 특히 DC는 현재 timer 경로와 manual
레지스터가 달라, TimerOFF를 단순 조회 옵션 변경으로 취급하면 안 된다.

## 구현·물리효과의 분리

Claude가 HEAD `75dc699`의 최신 코드를 읽기 전용으로 독립 대조했다.

- 현재 producer와 executable이 모두 있는 기능: Bar5(power/flow/mode/frequency/feed-duration),
  DC6(power/flow/mode/frequency/feed-duration/schedule-reset), Aquarium3(power/speed/feed-duration).
  실제 자격은 아직 발급하지 않았다.
- 리턴 power 자격은 **ON→OFF→ON**이다. 중간 실패 시 기존 복구 계약은 OFF로 수렴하고
  자동 ON 복구를 금지한다. 메인 순환 펌프에서는 OFF 지속이 더 안전한 상태라고 단정할 수
  없으므로 **현장 감시와 수동 재가동 수단을 확인하기 전에는 실행하지 않는다**.
- DC reset은 시간표를 실제로 지웠다가 자기 원본 raw image로 복원한다. 일반 scalar 시험과
  분리해 승인해야 하며 이번에는 실행하지 않았다.
- Bar/DC/Aquarium schedule producer는 있으나 catalog executable 승격은 아직 없다.
- Bar feed-switch/pulse-or-tide/linkage, Aquarium feed-switch는 현재 자격 producer가 없다.
- 나머지 timer coupling·clock 검증 계약·Aquarium transport 의미의 공백은
  [남은 구현 범위](../remaining-product-implementation.md)에 기록된 그대로다.

도징의 기존 요청은 하루 총량/횟수 자동 분배 및 소량 목표의 여러 날 평균 계획이다.
계획 API는 이미 있지만 실제 투여 executor/qualification은 없다. 앱 정수mL 표시만으로 물리
단위·interval 의미를 검증한 것으로 보지 않는다. protocol의 control_supported=False와 동결을
우회하거나 추측 sender를 만들지 않았다. 호스를 수조에서 분리한 물 테스트 준비/채널을
질문했지만, 이 기록 시점에는 답을 받지 않았다. 실제 투여 승인이나 protocol 동결 해제로
해석하지 않는다.

## 다음 선택과 증거

1. 현장에서 Bar/리턴을 기존 저출력 시험 조건으로 준비한 뒤 이미 구현된 비-power 기능부터
   자기 원본 보존·복원 자격을 수행한다. power/reset은 정지·원복 실패 효과를 별도로 확인한다.
2. 운전을 유지해야 한다면 현재 Pro 제어와 나머지 조회/계획을 유지하고 실기는 보류한다.
   도징은 수조와 분리한 채널의 앱/raw 근거 및 실행·복구 계약부터 별도로 준비한다.

park의 영향은 남은 장비 live 제어가 아직 열리지 않는 것이다. 재개 조건은 실제 현장 준비와
operation별 승인, 도징의 경우 별도 구현/동결 범위 결정까지 포함한다. 전체 활성화를 완료했다고
보고하지 않는다. 현재 Pro 제어 서비스는 정지하지 않았으며 장비 운전을 바꾸지 않았다.

증거 등급 **(b)**: 기존 runtime에서 받은 구조화 publication이며 원 wire frame은 아니다.
opaque artifact `remaining-control-activation-20260916.8G2l0Z/current-states.json`, SHA-256
`3901efbbdea96da6c65e793149cd251d0a18add648cd04e8efba4c4e9de5f2d9`.

**새로 확정된 사실:** 세 모터 모두 현재 상태 그대로는 기존 저출력 자격에 진입하지 못하며,
리턴 전원 시험에는 실패 후 OFF 유지라는 현장 복구 의무가 있다. 도징은 단순 활성화 대기가
아니라 실제 투여 실행기 부재다. 이는 장비 자체의 동작 FAIL이나 firmware 가설이 아니다.
