# Bar 앱 기능 구현 및 배포 후보 — 2026-09-17

Pro 후보를 `ad70de5`로 보존한 뒤 Bar만 진행한다. 소프트웨어·오프라인 검증 및 비활성 배포
준비 기록이며, 새 실기 송신·새 영수증 발급·운영 교체를 뜻하지 않는다.

## 기능과 앱 명령의 실제 소유 필드

활성 template SHA-256은
`e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`다.
minified 함수 이름만으로 묶지 않고 callback의 소비자까지 추적했다.

| 기능 | 연결 및 명령 범위 |
|---|---|
| power | SwitchON 한 필드. 기존 OFF 복구 계약 유지 |
| feeding_duration / feed_switch | 각각 FeedTime / FeedSwitch 한 필드 명령 존재 |
| manual_flow / manual_frequency | 기존 자기 scalar writer/자격 경로 유지 |
| manual_mode | Mode 선택과 함께 TimerOFF, FeedSwitchOFF, independent, PulseFalse, 현재 Flow/Frequency |
| pulse_or_tide | MANUAL_SETTING modal의 Flow/Frequency/PulseTide 3필드. ON=Cross-flow, OFF=Pulse |
| linkage_role independent | switchToManual의 6필드. FeedSwitch는 쓰지 않음 |
| linkage_role master | 수동에서는 onEditLinkMode의 7필드; TimerON에서는 onProgramSave의 프로그램 seed 명령 |
| linkage_role slave | 홈 클래스 onEditModeSave의 7필드. Mode classic이면 PulseTrue |
| schedule_enable | 기존 standalone program tab ON / manual OFF. 전원 필드는 쓰지 않음 |
| schedule | 48개 8-byte 슬롯 full image 저장. 빈 slots로 전체 삭제. timer/role 선행 일치 불필요 |
| device_clock_sync | 설정 시간대의 현재 시각; 과거 clock rollback 없이 관측 복구 |

`onEditModeSave`는 두 클래스에 있다. 홈의 slave callback과, modal payload를 그대로 넘기는
manual-setting callback은 서로 다른 명령이다. Mode/Linkage/PulseTide를 bare 단일 필드로
보내던 기존 경로를 위 복합 명령으로 교체한다. HA primary 값은 해당 Mode/Linkage/PulseTide이고
함께 보내는 TimerON 등이 select/switch의 표시값을 대신하지 않는다.

Pulse label은 실제 modal에서 false=Pulse / true=Cross-flow다. 반대로 적힌 클래스의
미사용 `pulseButtons` 배열을 활성 UI 근거로 쓰지 않는다.

Bar의 11개 기능은 HA 10 controls + schedule request topic 1이다. 모드 선택은 타이머·급이를
끄고 independent로 전환한다는 부수 효과를 이름·설명에 남긴다. 실제 보낸 persistent 필드만
반환값과 대조하고 Auto/clock의 과거 일치를 요구하지 않는다. 출력 한도는 함께 쓰는 Flow와
실제로 활성화/복구할 시간표에 적용한다. FeedSwitch는 boolean 설정만 소유하며 장치 내부
급이 경과/잔여시간은 관측·복원되지 않는다.

연동 역할 시험의 범위도 대상 Bar 자신의 복합 필드 수용·복귀다. 다른 펌프와 실제로
동기화됐거나 slave 출력이 독립적이라는 기능 판정을 이 영수증으로 만들지 않는다.

## 추가 자격 시험의 범위

| 기능 | 계획된 송신 순서 | 자격에 쓰는 두 프레임 |
|---|---|---|
| manual_mode | classic → constant → 자기 소유 필드 원복 | 앞의 두 앱 명령 |
| pulse_or_tide | classic 준비 → true → false → 자기 소유 필드 원복 | 가운데 두 PulseTide 명령 |
| linkage_role | manual master → slave → 자기 소유 필드 원복 | 앞의 두 앱 명령 |
| feed_switch | 시작값 반대 → 시작값 | 두 FeedSwitch 명령 |

별도 원복 프레임의 기능명은 `bar_owned_controls`다. 이 프레임과 classic 준비는 실행·복구
기록에 포함하지만 기능 자격으로 세지 않는다. mode/pulse/role은 복합 명령이 함께 변경하는
PulseTide·FeedSwitch도 소유 범위에 포함한다. feed_switch는 두 번째 명령으로 자기 필드가
복귀하므로 세 번째 송신을 추가하지 않는다.

위 시험용 초기 상태는 기존 ON/TimerOFF/independent/constant/Flow≤45 범위를 사용한다.
이 상태 요구를 일반 HA 조작에 옮기지 않는다. 초기 급이가 켜져 있었다면 설정값의 원복과
장치 내부 급이 진행의 원복은 다르다는 진단을 남긴다. 실패 뒤에는 시험값을 재전송하지
않고 해당 operation이 실제로 기록한 자기 원본 필드만 복구한다.

## 프로그램 master의 앱 자체 불명확 구간

onProgramSave는 standalone timer ON과 다르다. 슬롯이 있으면 그 seed와 Linkage=master를
보내고, 슬롯이 전혀 없으면 AutoMode=stopped만 보낸다. 생성된 gap sentinel에는 앱이
AutoMode=-1을 그대로 넣는다. unsigned enum schema로 이 값을 표현할 수 있다는 증거가 없어
다른 모드로 바꿔서 보내지 않는다. 이 구간의 SDK 직렬화/장비 수용은 미확정이다. 전일 유효
시간표의 master program과 이 잘못된 앱 인자를 같은 완료 주장으로 묶지 않는다.

이를 푸는 경로는 (1) SDK의 실제 negative-enum 직렬화 정적 추적, (2) 별도 승인된 앱 raw
capture다. 현재 영향은 gap 시각의 master-program 명령 한 경로에 국한된다. 일반 시간표
저장과 standalone timer, manual role 명령을 이 질문 때문에 막지 않는다.

## 오프라인 검증과 배포 준비

시각 설정 관련 묶음 52 PASS, compound 명령·시간표·catalog·HA 묶음 122 PASS.
planner/writer/시간표/노출/지문/import 경계 묶음 189 PASS, CLI 및 기존 scalar 호환 66 PASS.
새 자격 producer 40 PASS에는 ACK 뒤 readback I/O 실패의 UNCERTAIN/ACK 보존, 실제 generic
recover의 자기 원본 복귀·trial 재송신 0, 급이 시험 중 비소유 값 변경 허용, 보존 readback
바꿔치기의 영수증 발급 거부가 포함된다. 마지막으로 기록된 원본 응답과 실제 송신 필드만
대조하며 도달 불가능한 중복 최종-state 가드는 추가하지 않았다.

고정한 소스로 Mac 전체 `tests`를 실행하여 **3605 PASS / Linux 전용 1 SKIP**(111.19초)를
확인했다. **Linux 전체 3606 PASS / 0 FAIL**(194.66초), 시험 container exit 0도 확인했다.
Ruff 전체 검사와 변경 파일 형식·diff 검사도 통과했다.
오프라인 ACK/byte-copy 테스트는 펌웨어 수용이나 실제 수류를 증명하지 않는다.

writer 및 새 byte planner는 protocol fingerprint에 포함한다. 기존 운영/Pro/DC 후보의
영수증을 새 지문으로 바꾸거나 재사용하지 않는다. 운영 Pro/Bar 서비스는 계속 보존하며,
새 자격과 배포 입력이 함께 준비되기 전에는 교체하지 않는다.

비활성 입력 준비는 완료했다. artifact id는 `bar-release-preparation-20260917.9l9Ygu`다.
대상 identity SHA-256은
`d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`이며,
쓰기 허용 대상은 Bar 한 대뿐이다. 11개의 job은 모두 `enabled_for_execution=false`이고,
22장은 향후 예상 영수증 수일 뿐 실제 발급은 0이다. Pro/Bar/DC template root는 읽기 전용으로
확인했고 장비·브로커·공용 hardware-safety volume에는 접근하지 않았다.
`hardware_access=false` 등의 보고서 상수 자체를 실행 0의 증거로 삼지는 않는다. 준비 스크립트에
writer/session/송신 경로가 없고, 네트워크 차단 및 공용 safety mount 부재를 따로 확인했다.

후보 config는 OBSERVER, dry_run=false, Bar 한 대만 write 허용으로 준비했다. jobs의 실행 비활성
표시가 config의 쓰기 허용을 없애는 것은 아니며, 실제 실행은 별도 승인 단계다. 보고서의
`config_sha256`·`manifest_sha256`는 이 새 후보 파일의 digest다. 운영 원본은 별도의
`input_copies_before_sha256`·`input_copies_after_sha256`와 대조한다.

준비 보고서 SHA-256:
`e0d31d5cc635e14f60714850a6dee4308bd27d704e51a56043af8724ee10513d`.
운영 config/install/qualification의 사본 세 개를 준비 전후 재해시하여 모두 동일함을 확인했다.
첫 준비 컨테이너는 입력 소유자와 실행 UID가 달라 읽기 단계에서 종료됐고, 입력 권한을 바꾸지
않고 실제 소유자 UID로 실행한 두 번째 컨테이너가 완료했다. 이 준비는 새 빌드 image 검증과
별도이며, 최종 image에서도 동일한 schema를 확인한다.

## 고정 배포 후보와 산출물

후보 tag는 `jebao-flow:bar-candidate-20260917`, image ID는
`sha256:c7301ce87bc426a4fb5965c0240cc30d495cfc2750b08109ee3b18559984ac0c`다.
설치된 wheel에서 재계산한 schema가 준비 자료의
`0f97b053002c036dbed891dc80b8068d9fa4893819b958501314287a645f69be`와 동일하다.

| 산출물 | SHA-256 |
|---|---|
| source archive | `b2b8e6c3d1c3f6432ece1bfc142e209a34cec1bc5a2c28b5abca160af27c0bd7` |
| tests/docs overlay | `2cca7e61c805e878c04ddb966ebe307efdc01e3312c984aa4be234920f67aeaa` |
| 전체 repository fixture base | `bf11e44c5dbc425c4d5efeb27ffef057493a64975e47cb54e44802060de4e68d` |
| Mac JUnit | `f6bf75e0ba830c624b9a27ddb58d2adbfc4ed37c2f6f6d5dc91248681b3d19d5` |
| Linux JUnit | `743ef576d88b7844db171a4d9350ab7a75fc85d4fb024ccfdff0f1b501d0d335` |

source/tests archive의 로컬·빌드 호스트 digest는 일치한다. Linux 시험에는 production 후보에서
파생한 테스트 이미지와 읽기 전용 전체 fixture를 쓰며 네트워크는 차단한다. 공용 safety
volume은 붙이지 않는다.

후보 빌드 뒤 운영 container는 기존 `5b1ec481…` image로 running/restart 0을 유지하고,
config/install/qualification의 직접 재해시 값도 준비 전 세 값과 동일했다. 이번 후보를 운영
서비스로 교체하거나 현재 영수증을 수정한 일은 없다.
