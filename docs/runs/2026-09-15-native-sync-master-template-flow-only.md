# Native Sync 공통 시간표·슬레이브 Flow 전용 차이

## 실행 중 기록 — 22:29Z 기준

이 절은 실행 도중의 관측이며 완료·원복·기능 PASS를 선언하지 않는다.
질문은 같은 시간·파형·주파수를 가진 시간표에서 slave의 서로 다른 Flow가 어떻게 보고되는가다.
실제 임펠러 파형·유량이나 파형·시각 소유권을 동일 시간표만으로 판정하지 않는다.

정책 `3aa263a`, 구현 source `e993628d16f01e0f2595920feea58045c7afe888`,
image `ab6a33dbffe54432788d389164abebe295341d01d34625a228432253037f3de6`으로 실행했다.
operation SHA256은 `3626c488e1b9ffd1e26d65c5c175d05f9b4c53a3b9e3bbfd5a96a435e73dc54a`다.
승인 artifact SHA256 `4fbc74998c545b6db10383a30cdf502aa8c6b6d4334127bdb02d907064058423`,
wrapper SHA256 `28b12fa0567466efd9eefdbb172c9d5a02fc52ed4e557c78e0a27a727548b430`를 결속했다.
동료의 wrapper 검토는 저장소 밖 바이트 검토가 아니라 서술 검토다.

사용자는 실기까지 진행과 현장 감시를 앞서 지시했고, 에이전트의 현재 현장 질문 직후
"아씨 좀 그만 물어보고 진행해.. 내가 진행하라고 했어 안했어"라고 답했다.
그 질문·답변 및 기존 현장 참여 맥락을 현재 재개 회신으로 해석하여 진행했다.
사용자가 별도로 "예" 또는 "지금 수조 앞이다"라고 새로 말했다고 기록하지 않는다.
동료의 동의를 사용자 승인으로 사용하지 않았으며 동일 질문을 다시 요구하지 않았다.

새 Sync1회만 실행하며 Anti·자동 반복은 없다. 지명 native receipt의 만료 시간만 이번 단회
범위에서 제외했고 나머지 결속 검사는 유지한다. 검증된 park 존재 상태에서 admission했다.
source·timeout·retry·게이트·시계는 실행 중 변경하지 않는다.

## baseline·계획·시작

read-only 사전2 pair는 `2026-09-14T22:14:09.410217Z`–`22:14:43.288858Z`에 보존했다.
두 Pro ON/TimerON/independent, 원432B exact, active problem·invalid slot0이었다.
second/master는 Auto Constant45/F5와 비활성 manual30/F32,
first/slave는 Auto Constant55/F5와 비활성 manual40/F34로 보고됐다.
현재 Auto 값은 시간에 따라 변하므로 특정 슬롯 이름·출력값을 원복 목표로 고정하지 않는다.

container 시작은 `22:22:29.653149175Z`, preflight 저장은 `22:22:38.873384Z`다.
그 preflight에서 다시 두 시작 snapshot과 새 계획 bool·Sync·TimerON을 확인했다.
stdout/stderr를 연결한 `docker start -a`로 시작했고 기존 CLI preflight1/run1만 호출한다.
확정 기기 시각 경계는 **07:32 /07:37**이며 시작 기준은 기기 현재 분이다.

| 구간 | 양쪽 파형·주파수 | master Flow | slave Flow |
|---|---|---|---|
| 초기 | Sine/F30 | 35 | 30 |
| 07:32 이후 | Constant/wire F0 | 32 | 40 |
| 07:37 이후 | Sine/F30 | 38 | 35 |

두 field image VERIFIED는 `22:23:06.308798Z`다. pair2에서 master와 sync_slave 역할 및
Auto Sine35/Sine30이 원본에 보고됐다. slave 별도 Mode/Frequency는 Sine/F30, Flow31이었다.
비활성 Flow31을 현재 스케줄 출력이라고 취급하지 않는다.

원복 의무는 두 가지다. 이 하네스는 **앱 준비 이후 두 TimerON 시작 snapshot**을 소유한다.
이전부터 열린 first의 앱 이전 TimerOFF/manual constant40/F34와 원서비스 복귀는 별개다.
이번 시작이나 하네스 원복만으로 이전 의무가 닫혔다고 쓰지 않는다.

## 감시와 로그 보존

원본 JSONL·durable intent를 주기적으로 읽어 역할·ON/Timer·파형·Flow를 확인하고,
stderr의 실패 진단도 함께 확인한다. 매 줄을 실시간으로 사람이 읽는다는 뜻은 아니다.
로그는 별도로 보존하며 실행 도중 원 로그를 수정하지 않는다.
첫 로그 요약기는 JSON formatter 외피만 보았다. 다음 읽기 전용 요약기에서 message 내부의
JSON까지 파싱하도록 보완했다. 이는 operator 분석기 변경이지 실행 코드·진단·게이트 변경이 아니다.
WARNING 수준에는 정상 비활성 controls 차이와 창 값 출력도 포함되므로 곧바로 실패로 세지 않는다.

27 pair 시점 원본 분석에서 날짜 불일치0·날짜 포함 분 불일치1이었다. master는 action3,
slave는 action4였다. 이 교락은 유지되며 raw 개수만으로 엄격한 관측 자격 count를 재구성하지 않는다.
36 pair까지 초기 값35/30이 유지됐고 새 실패 진단은 없었다. 전환·종료·독립 원복 결과와
최종 artifact digest는 종료 뒤 아래에 별도로 덧붙인다.

## 두 경계의 원본 관측 — 종료 전 추가

첫 경계는 pair51에서 보고됐다. master의 완료 시각은 `22:32:06.150924Z`,
slave는 `22:32:06.444631Z`이며 둘 다 기기 시각 `2026-09-15T07:32:00`이다.
양쪽 AutoMode Constant/AutoFreq5, AutoFlow는 master32/slave40이었다.
slave 별도 Mode/Frequency는 Constant/5, 비활성 Flow는31이었다.

둘째 경계는 pair80에서 보고됐다. master 완료 시각 `22:37:14.078326Z`,
slave `22:37:13.854587Z`, 둘 다 기기 시각 `2026-09-15T07:37:00`이다.
양쪽 AutoMode Sine/AutoFreq30, AutoFlow는 master38/slave35였다.
slave 별도 Mode/Frequency는 Sine/30, 비활성 Flow는31이었다.

따라서 보존 보고값은 master35→32→38, slave30→40→35로 계획과 일치했다.
이는 Sync 역할에서 서로 다른 AutoFlow 보고가 남고 슬롯 경계에 따라 바뀐 관측이다.
실제 임펠러 출력·수류량, 별도 Flow31의 출력 적용, 파형·시각 소유권을 증명하지 않는다.
81 pair 시점 날짜 불일치0·날짜 포함 분 불일치2였다. 종료·구조화 판정·원복은 아직
이 절의 판정에 포함되지 않으며, 최종 파일 해시와 함께 아래에 덧붙인다.

## 종료 판정과 실패 진단

최종 원본은 97 pair·195줄·202,959B다. 취득 UTC span은
`2026-09-14T22:23:18.773753Z`–`22:40:16.534067Z`이며 날짜 불일치0,
날짜 포함 분 불일치3이다. 역할·물리 장비·reply/report 교락은 해소하지 않았다.
기기 identity는 device ID 문자열의 SHA256으로 결속한다.
master `72a5d4e77d7330e8d7fc96c9fe27974b1794ec5f6711a661602ff50df5b9d87b`,
slave `85a594d671f80f1e901c66eb5d2624127a77257e9e75a45983b5269ae2422733`이다.

`22:40:26.539146Z` 로그는 `windows=[0, 1] conflicting=[1] raw_failed=False`다.
세 창의 자동 자격은 완료되지 않았으며, 창1의 충돌 원인은 이 기록에서 단정하지 않는다.
날짜차0이나 두 경계의 Auto 보고 일치를 자동 PASS로 올리지 않는다.
관측 monitor 완료는 `22:40:26.539492Z`이며 이후 종료 처리에서 기술 실패가 발생했다.
container는 `22:41:15.653981001Z`에 exit2로 끝났고 wrapper도 run/exit2다.
terminal intent는 `experiment_failed_restored`, `schedule_flow_outcome=null`이다.

이번 신규 실패 진단은 실제로 발동했다. `temporary_schedule_observation_failure` v1,
site `already_done_failed`, class `TemporaryScheduleObserverUnstoppableError`이며
source `e993628`의 `_disarm_temporary_schedule:2306`을 원본 frame으로 남겼다.
그 지점은 종료 시 fresh disarm 증명의 `safe=False`를 공통으로 처리하는 raise다.
읽기 예외·controls 불일치·physical binding 부재·proof 생성 예외 가운데 어느 항목인지까지는
남기지 않았으므로 근본 원인은 여전히 UNKNOWN이다. 예외 보존 성공이 종료 오류 수정은 아니다.
해당 진단 operation SHA는 하위 temporary operation의
`f797e4b13e74f8cd7cd4ff18eca2c64c95f34e7c4e8ccd9646f4bd7a32416d51`로,
위 outer operation SHA와 구분한다.

후속 `schedule_flow_failure`는 LinkageApplyError → TemporaryScheduleRollbackUnsafeError를
stage `outer_restored`로 남겼다. 이전 실행의 원 예외와 동일한 원인이라고 소급하지 않는다.
진단은 메시지·locals·비밀값 없이 클래스·project frame·분기만 보존했다.

## 사후 원복 확인과 남은 의무

durable stage에서 `22:41:01.285228Z` role_disarmed,
`22:41:04.885159Z` field_restored(2대), `22:41:15.558387Z` outer_restored를 확인했다.
첫 disarm 실패를 이후 성공으로 지우지 않는다. sentinel forward2/restore2를 포함한 정상 경로
logical 회계와 실패 보상 경로의 실제 physical send 수를 같다고 단정하지 않는다.

원격 조회 중 SSH 연결 오류와 응답 대기가 발생했다. 무중단 실시간 감시라고 주장하지 않는다.
종료 직후 즉시 fresh 검증한 것도 아니다. 사후 독립2 pair의 실제 UTC 취득은
**`2026-09-15T00:18:31.464706Z`–`00:19:05.566414Z`**이며, terminal 이후 약97.26분이다.
전체 지연을 한 번의 SSH 오류 탓으로 단정하지 않는다. 서버는 그 사이 자체 실행·원복과
로그 보존을 마쳤고, 사후 조회로 그 결과를 확인했다.

series `JFS-1c20ba9f37756bbcd6efc2c0261dd393`의 두 pair에서 다음을 대조했다.

- 원432B 시간표 두 개가 이번 preflight SHA와 exact, 모든 controls mismatch0.
- master: ON/TimerON/independent/manual constant30/F32, 당시 Auto Sine50/F25.
- slave: ON/TimerON/independent/manual constant40/F34, 당시 Auto Sine70/F80.
- active problem·invalid slot0, 네 blocking journal/latch 경로 부재, receipt18장 유지.

원 시간표 SHA는 master
`60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`, slave
`c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`이다.
복원 판정은 현재 Auto 값 고정이 아니라 저장 image와 controls의 실제 대조에 따른다.

**이번 하네스 소유의 두 TimerON baseline 원복은 확인했다.** 이전부터 열린 first의
앱 이전 TimerOFF/manual constant40/F34 의무는 마지막 사용자 앱 토글이 필요하며 아직 열려 있다.
원서비스도 stopped 그대로다. 이 두 의무를 이번 기술 실패·baseline 원복과 분리해 계상한다.
사용자에게 첫 Pro 스케줄 OFF 한 번이 남았음을 알렸으며 새 앱 완료 회신을 만들지 않았다.

## 최종 산출물 결속

| 산출물 | SHA256 |
|---|---|
| 원본 raw195줄 | `9e1acb4dbd0cb2ff9a575e67c69424b2041f5b3ad7e59b13e44881a2c40baac3` |
| preflight intent 사본 | `8ffecc3881e592a530c159fa1ca4ef4e83be5c201dd7265ace199222d9c9ec67` |
| 종료 intent 사본 | `cecc55c86c10e084dbab249ee3a2dc50a412fee5e8e84a2e6eea3627b4cc5374` |
| exclusive 실행 표식 | `cdafcaca62ef5c36ac6940753f6559941d9777cfba2a770c9e303de9eac627a1` |
| wrapper 종료 결과 | `a876532b82e87918bd4401b85621d6897bef4f2681653299131f72ee7a475173` |
| 연결 stdout / container stdout 각각 | `4182dc6ca6252df672c8662ee3ba58eace83f137dad9356e1185f20794bd57be` |
| 연결 stderr / container stderr 각각 | `9d8ce49e55e2cce5fdf9b0699bf65c621adcd949ec2354671de536489f9c71f4` |
| 사후 독립 series | `cb80f77115da06abc4ba39922cebd5274c9776394b40b0f9e108bee873362377` |

raw·사후 frame은 등급(a), intent·stage·로그는 등급(b), 표·통계는 보존 원본의 파생 분석이다.
최종 metadata 분석기 첫 시도는 보호된 build 부모 경로 읽기 권한으로 실패했다. 보호 root나
운영 UID를 바꾸지 않고 비밀값 없는 script 파일만 직접 mount하여 재검사했고, 로그 해시는
기존 소유자로 별도 계산했다. 이 과정은 장비 접촉·write0이다.

## 새로 확인한 사실과 다음 경계

Sync에서 공통 시간표에 Flow3바이트만 다른 두 image를 적용한 뒤 두 경계에서
master35→32→38와 slave30→40→35가 각각 Auto 필드에 보고됐다. 보고값 질문은 CONFIRMED다.
실제 수류 세기 적용·파형 소유권·자동 세 창 자격 완료는 미확정이며, 전체 시험 PASS는 아니다.
종료 오류는 관측 이후 disarm 증명 실패 지점까지 새로 좁혔다. 따라서 정보0 실행은 아니다.

추가 실기·Anti·자동 반복·source/test 수정은 실행하지 않았다. 이 단회는 실행됐으며 같은
승인으로 다시 호출하지 않는다. 후속 선택지는 (1) 보존 raw·기존 종료 경로를 오프라인 분석하여
기능 보고와 종료 실패를 각각 좁히거나 (2) 별도 승인 범위에서 disarm 판정 항목 진단을 보강한
후 추가 실기의 필요성을 판단하는 것이다. park하면 보고값 관측은 남지만 물리 출력 질문과
종료 오류는 열린다. 앱 마지막 원복·원서비스 복귀는 이 선택과 무관한 미완료 의무다.

## 보존 로그 추가 대조

`wave-flow invalid sample window=1`은 없고, window2에만 네 건이 있다.
시각은 `22:38:39.153956Z`, `22:39:11.717048Z`, `22:39:44.181024Z`,
`22:40:16.536821Z`이며 모두 `ScheduleLinkageApplyError`다. 이 사실을 slave의
실제 Flow 변경 실패로 해석하지 않는다. 창1충돌도 실제 값 변동 외에 제외 뒤 previous 리셋과
재진입으로 생길 수 있어, 원본·상태기계 재구성 없이 이유를 하나로 단정하지 않는다.

disarm 예외는 안전 상태를 증명하지 못했을 때 staged image·복구 증거를 유지하기 위한
의도된 거부 경로다. 그 거부를 장비 고장이나 새 시간표 자체의 결함이라고 부르지 않는다.
반대로 거부가 의도됐다는 이유로 정상 종료라고도 쓰지 않는다.

## 사용자 검증 기준 확정과 원본 전체 image 추가 대조

사용자는 후속 제안에 대해 "유속계를 내가 어케 알아 정확한 값이 들어가고 그게 다시 이렇게
동작하고 있어라는 리턴 값이 나오면 거기서 검증이 끝나는거야"라고 기준을 명확히 했다.
따라서 기능 수용 기준은 **정확한 시간표 입력·read-back과 장비 동작 보고값의 예약 전환**이다.
이 기준에서 이번 Sync 슬롯 Flow 전환 검증은 PASS이며 완료로 처리한다.
실제 유속 측정은 이번 수용 조건이 아니며 영상·유속계를 추가로 요구하지 않는다.
물리 계측을 했다고 주장하거나 기존 자동 창 미달·exit2·disarm 오류를 PASS로 바꾸는 것은 아니다.

그 기준을 보강하여 기존 raw97 pair만 오프라인으로 재분석했다. 실행 source/image의
저장된 spec으로 생성한 full432B와 각 원본 frame의 full432B를 비교해 **master97/97,
slave97/97 exact, mismatch0**을 확인했다. raw frame별 기존 SHA도 검증했다.
master field image SHA `88174cf5471d8b84b81624eecad0c3e8e90642d06605f32a3afce5dcbc1e561e`,
slave `b3c04094e5dc088aa45265ac6e489e24cbbbace2b12e9ed5030d2a35bd251526`이다.
이 대조와 앞선 Auto35→32→38 / Auto30→40→35 응답이 사용자 수용 기준의 근거다.
오프라인 대조 출력 SHA `a775d6248542178fc4e0203ae7f2155bd2fa28eaf13f1f27cb57a1362c4a02f2`,
분석기 SHA `077bc11428e213119765a555bc1a5138a8a145de2ed51088cb9ebb9cf4637f98`을 보존했다.

유속 비교용 Constant35 고정·slave30→40→30/independent 대조는 준비 문서 초안만 작성했으며,
위 최신 기준에 따라 폐기했다. 새 구현·배포·실기는0이고 기존 승인 권한을 재사용하지 않았다.
첫 Pro 앱 마지막 TimerOFF·원서비스 복귀 의무와 종료 오류는 여전히 별도로 남는다.
