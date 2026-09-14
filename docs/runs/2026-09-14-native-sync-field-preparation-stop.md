# Native Sync · field 준비 중단과 원복 관측

## 상태와 범위

기록 시점 상태: 실기 단회는 종료됐고 새 실기는 하지 않았다. 하네스가 소유한 앱 준비 후
시간표·controls 복원은 fresh 2 pair에서 확인했다. 앱 이전 first Pro TimerOFF와 원 서비스
재시작은 미완료다. 이를 전체 원복 완료나 Sync 기능 판정으로 올리지 않는다.

이번 결과는 사용자 목표인 파형 주도권·슬레이브 슬롯 Flow 변경에 답하지 못한다.
Sync 역할 진입과 field 시간표 write 단계에 도달하지 않았다. 다만 sentinel 시간표
forward2·restore2는 완료됐으므로 "schedule write0"이라고 쓰지 않는다.

## 권한과 실행 결속

실행은 `f0cced4`의 두 Pro Sync 단회·원복 및 native receipt 시간 유효성 한정 예외로 수행했다.
사용자는 퇴근 후 재개했고 현장 확인, 두 대 스케줄ON, 즉시 시작을 직접 지시했다.
10분 뒤 전환 요청은 코드를 바꾸지 않고 시작 시점 조절로 반영했다.
소스 `6ebd802a8b18195813ed719cf6c213b6cbf5c70a`, 이미지
`a6ad7c71fb0ebd94ac6454bdb04441ba56e08f5ddfe0630d4dc4161b2ea06c4c`를 사용했다.
실행 전 schema fingerprint는
`fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`으로 결속됐다.
writer/collector는 정정된 검색 설정을 사용했다. second Pro가 master, first Pro가 slave다.

검증된 park admission이 두 CLI 호출에서 기록됐고 `qualified=false`다.
disposition `712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`,
operation `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`,
snapshot `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`로 묶인다.
이는 빈 safety root에서의 실행이 아니며 qualification 신규 발급이나 park 재실행도 아니다.

## 준비 오류를 포함한 경과

앞선 준비에서 실행 UID와 script 읽기 권한 오류·그에 따른 미생성 config 접근 오류를 보존했다.
운영 UID100/101과 읽을 수 있는 operator script로 정정했으며 receipt 보호 검사를 완화하지 않았다.
첫 앱 후 collector는 실제 first TimerOFF를 두 표본에 보존했다. 이후 사용자 ON 회신 뒤
새 수집 root가 없어 `pilot_root_missing`으로 시작 전 거부됐다. 해당 root를 생성한 뒤
동일 수집기로 fresh 2 pair를 보존했고 두 대 TimerON·전원ON·independent·원 시간표 일치였다.

실기 명령의 첫 shell 제출은 호스트가 container UID 소유 로그 폴더에 쓸 수 없어
redirect 단계에서 거부됐다. 이 제출에서는 Docker나 하네스가 시작되지 않았다.
기존 호스트 기록 폴더로 출력 위치만 정정한 뒤 실제 하네스는 한 번 실행했다.
operator wrapper는 승인 artifact 확인과 exclusive 실행 표식을 남긴 뒤 preflight1회/run1회를
수행했다. 이 오류들을 하네스의 재실행 횟수로 합치거나 성공 기록에서 지우지 않는다.

## 보존된 진행과 결과

| UTC | 보존된 사건 |
|---|---|
| 13:20:27.838036930 | 임시 실행 container 시작 관측(c) |
| 13:20:37.633529 | preflight intent 생성, device-local boundary22:27 |
| 13:20:50.225108 | outer safe pause 두 대상 완료 |
| 13:20:56.149818 | sentinel 두 대상 verified |
| 13:20:59.830285 | sentinel 두 대상 restored |
| 13:21:00.954540 | field snapshot 두 대상 완료 |
| 13:21:00.970956 | outer rollback 시작 |
| 13:21:11.126035 | outer rollback 완료 |
| 13:21:11.133231 | terminal intent 최종 갱신 |

종결은 `phase=terminal`, `outcome=experiment_failed_restored`, CLI exit2다.
stderr는 park JSON2줄과 `schedule-flow test failed safely (LinkageApplyError)` 한 줄이다.
`LinkageApplyError`는 감싸인 종결 예외이며 원인 자체가 아니다. 연쇄 예외는 보존되지 않았다.
stage17개에서 `temporary_error_code`·`failure_category`는 모두 null이고,
별도 outer evidence의 `forward_failure`는 `unexpected_failure`다. 두 층을 혼동하지 않는다.
role stage·active entry·verified sample은0이며 rollback failures는 빈 집합이다.

terminal intent 별도 보존 SHA-256:
`c7b30a85e676d6bf3e52f8582bae85059be2eee6691f8d9d949fbcebfb9b5e98`.
stdout494B SHA-256: `694b13aee08d15d5f8626ffc49f78a73a61f30f40b89123f3539b0c0304589bb`.
stderr965B SHA-256: `2911e7a51011070e40ccc652c45810c47581432f3d9c955cc8284fea7da9833c`.
논리 artifact 묶음은 `native-sync-20260914T1320Z`다. 위 파일은 private에 보존돼 있으며
terminal/log는 등급(b), 컨테이너 상태 등 직접 운영 관측은(c)다.

## 원복의 확인 범위와 미완료

사후 fresh2 pair의 두 대상은 전원ON·TimerON·independent이며 오류 목록이 비어 있다.
first의 inactive manual은 Constant40/F34, second는 Constant30/F32다.
현재 Auto 보고는 각각 Sine65/F80·Sine45/F40이다. TimerON의 inactive manual을 실제 출력으로
쓰지 않는다. 두 시간표는 각432B·14slot이며 앱 이전 보존값과 SHA-256이 일치한다.

- first: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`.
- second: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`.

후속 fresh2 pair에서도 TimerON 둘 다 유지됐다. 따라서 first 앱 이전 TimerOFF는 아직
복원됐다고 할 수 없다. 원 서비스는 정지 상태로 보존 중이며 원 이미지
`785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`도 유지한다.
최종 사용자 원복·서비스 복귀 결과는 이 기록에 덧붙인다. 새 계획 준비가 이 항목을 닫지 않는다.

## 새로 확인한 것과 남은 질문

검증된 park 진입 뒤 실제 sentinel 두 대상 write·readback·복원이 완료됐고,
새 field 준비는 snapshot 완료와 첫 field write 사이에서 종료됐다.
현재 보존된 런타임 자료만으로 원 예외를 판별하지 못한다. 정적 결함이나 오프라인 재현을
추가 확보하면 별도 근거로 기록하며, 그것을 감싸인 원 예외를 복원한 것으로 쓰지 않는다.
Sync·Anti-Sync 파형 및 Flow 기능 질문은 이번 실행으로 판정하지 못했다.
사용자는 후속 목표를 두 질문을 함께 구분하는 10분·15분 시나리오로 정정했다.
