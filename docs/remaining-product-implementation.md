# 남은 제품 기능 구현 — 2026-09-16

사용자는 Aquarium 속도 제어로 공용 writer가 바뀌면 기존 Pro/Bar 자격을 새 버전에서
갱신해야 한다는 설명에 동의했고, 이어서 "죄다 구현해"라고 요청했다. Claude token limit으로
Codex가 단독 진행한다. 이는 코드 구현·오프라인 검증 범위이며 새 실기·배포·자격 발급은 아니다.

질문: 앱에서 정적으로 확인된 기능의 제품 실행/HA 연결 공백을 실제 없는 필드나 펌웨어
동작을 발명하지 않고 채울 수 있는가. 근거는 기존 앱 분석, vendor schema, writer·journal
계약 및 오프라인 byte/fault-injection 테스트다. 장비 수용·물리 효과를 증명하지 않는다.

## 구현 순서와 경계

1. Aquarium `motor_speed`: 기존 Motor_Speed 필드에 앱 30..100 범위 encoder를 연결한다.
   기존 scalar readback·자기 baseline 복구·single-send를 재사용한다. 제품별 qualification
   경로와 CLI를 추가하되 없는 Flow/Linkage를 만들지 않는다. 자격 없는 HA 제어는 닫힌다.
2. 도징: 이전 요청인 하루 총량/횟수 자동 배분, 1 mL 미만 목표의 여러 날 평균 계획을
   유지하고 HA/MQTT에서 이용 가능한 비전송 계획 API를 연결한다. 확인된 채널 schedule
   이미지는 preview만 제공한다. 실제 투여량과 interval 의미를 추정하지 않는다.
3. 나머지 제품 기능은 현재 실행기·자격 producer·HA 연결을 대조해, 확정된 byte 계약의
   소프트웨어 공백을 채운다. 아래 UNKNOWN을 일반 bool이나 같은 이름의 다른 제품 명령으로
   대체하지 않는다. 기능별 남은 범위와 재개 조건을 이 문서에 남긴다.

허용 범위는 local_control writer/catalog/qualification/dosing/HA/MQTT, 해당 CLI와 직접
tests·제품 문서다. `protocol/`, 기존 frozen native 하네스·직접 tests, 공유 hardware guard는
수정하지 않는다. writer 변경에 따른 schema digest를 공개하며 계산식·기존 receipt를 변조하지
않는다. 구 버전 서비스는 이번 로컬 코드 변경만으로 교체되지 않는다.

## 근거 부족으로 별도 유지할 항목

- Bar/DC/Aquarium timer의 복합 필드·화면별 선택: 현재 앱 분석에는 복수 shape 또는 Auto*
  coupling만 확인됐다. 단순 TimerON bool로 축소할 근거가 없다.
- DC schedule mode 1/2와 Aquarium transport bool 방향: 이름 매핑이 미확정이다.
- clock sync: byte builder는 있으나 계속 흐르는 시각을 무엇으로 검증할지, 과거 시각으로
  되돌리지 않는 복구 계약이 미정이다. 기존 exact scalar 복구로 위장하지 않는다.
- 도징: protocol schema의 control_supported=False와 미확정 물리 단위·interval semantics는
  그대로다. 캘리브레이션 10 mL는 0.5 mL wire scale의 근거가 아니다.

대안은 (1) 추가 앱 정적 자료·기존 raw를 read-only로 확보하여 shape/의미를 확정하거나,
(2) 별도 승인된 최소 앱 조작과 raw 관측으로 두 가설을 구분하는 것이다. park 중에는 해당
live 기능이 닫히고 계획/관측 기능은 유지된다. 재개에는 부족한 근거 및 기능별 검증·복구
계약이 필요하며 실제 write에는 별도 현장 승인이 필요하다. 이번 요청으로 기존 미완료
native source를 복구·삭제하거나 전역 unfinished 차단을 우회하지 않는다.
