# Aquarium 메인 리턴 — 구현 및 비활성 후보 준비

2026-09-17, DC → Pro → Bar 이후 Aquarium 한 기종만 진행한다. 이 문서는 소프트웨어
계약이며 실기 기록이 아니다. 이 단계의 장비 송신·새 영수증 발급·운영 교체는 0이다.

## 근거와 남은 활성본 결속

cached shuibeng 두 사본은 12개 명령 호출부의 계약이 같다. SHA-256은 각각
`c5f255bed01d249a39484cfc7245c6544c15b64b200f5261a490f97f7fda3dc9`(151,290 bytes),
`b937f098dec6dfa4ed8564eadf261aa52ab6066a43701e2d84e120a73bff29af`(102,940 bytes)다.
동일 계약은 사용 중인 활성본이라는 증거가 아니다. compiled selected locator의 바이트는
아직 로컬에 없고 이 둘이나 기본 auto renderer를 대신 활성 digest로 쓰지 않는다.

vendor productConfig의 digest는
`7a68558cf0531125198fcab50afaebab1a04c0e3e5887c78a6c4c71e4791aebf`다.
그 안의 Mode 설명은 0=AP Control, 1=Wireless Control이라고 선언한다. 이는 bool의 방향을
확정하지만 통신 경로 전환이나 장비 수용까지 증명하지 않는다. cached 전용 UI에는 Mode를
쓰는 handler가 없으며, productKey 지원 목록 역시 runtime template 귀속 근거가 아니다.

## 기능별 구현 범위

| 기능 | 명령 및 소유 필드 |
|---|---|
| power | SwitchON 단독. OFF 실패의 더 안전한 종료 상태 유지, 복구 중 자동 ON 없음 |
| motor_speed | Motor_Speed; 현재 TimerON일 때만 TimerOFF 동반 |
| feeding_duration | 새 FeedTime + fresh FeedSwitch 복사 |
| feed_switch ON | FeedSwitch=true, TimerOFF, fresh FeedTime |
| feed_switch OFF | 위 세 필드 + fresh Motor_Speed |
| schedule_enable ON | TimerON=true, FeedSwitch=false + 현재 local-time 슬롯에서 AutoMode/AutoGears seed |
| schedule_enable OFF | TimerOFF + fresh Motor_Speed |
| schedule | 전체 48×6-byte image; TimerON이면 별도 두 번째 slot-seed 명령 |
| device_clock_sync | 설정 시간대의 현재 YMD/HMS, 과거 시각 재송신 없이 관측 복구 |
| transport_mode | vendor-declared Mode encoder만 구현; AP/Wireless 인계·원복 경로 미구현으로 실행 노출 안 함 |

앱의 급이 스위치는 TimerOFF를 동반한다. 이름 붙은 한 필드만 복원하면 이 부수 효과를
놓치므로 실제 쓴 persistent 필드의 합집합을 초기 raw에서 복원한다. 급이 경과시간은
관측·복원할 수 없으며 FeedSwitch/FeedTime 복귀를 급이 진행의 복귀로 표현하지 않는다.

복사값의 0은 신규 입력의 0과 구분한다. 신규 속도는 앱 30..100, 신규 FeedTime은 1..60이다.
앱이 현재 FeedTime=0을 복사하는 경우에만 그 초기 raw의 0을 그대로 인코딩한다. 전체 image와
새 출력 및 복구로 활성화될 출력에는 설정 bounds가 적용된다. 과거 Auto/clock 값과 같아야
한다는 조건은 없다.

## 시간표 및 seed의 의미

슬롯은 `[startHour,startMinute,endHour,endMinute,mode,level]`이다. mode=1(auto)일 때만
speed를 쓰고 stopped/feed는 앱 encoder대로 level=0이다. release parser는 EE만 제외하고
all-zero 행은 실제 stopped 행으로 취급한다. app gap-fill 행은 mode=-1/level=100이며,
두 seed 경로 모두 이를 AutoMode=stopped로 바꾸고 AutoGears를 생략한다. 실제 auto 행만
출력 bounds의 대상이다. 끝 시각의 `(endHour || 24)`, 양끝 포함 및 마지막 매치 선택도
cached 앱의 규칙을 따른다. 이는 펌웨어의 slot 처리 방식에 대한 주장이 아니다.

seed는 현재 장치 Auto 상태에서 복사하지 않고 새 시간표에서 계산한다. 타이머 ON의
readback은 TimerON/FeedSwitch만 증명한다. image 저장 뒤 seed 액션은 image 유지와 ACK를
기록하며 Auto seed 전체 수용이나 실제 수류를 증명하지 않는다. 두 송신 모두 첫 write 전에
기록하고 같은 요청 재실행은 재송신하지 않는다. seed 복구는 원본 image만 소유하며 과거
AutoMode/AutoGears를 다시 보내지 않는다. 따라서 비활성 Auto 잔여값까지 원복했다는 주장은
하지 않는다.

빈 시간표·stopped/feed slot의 물리 효과도 별도 실기 질문이다. 빈 image에서 검사할 출력
slot이 없다는 사실은 펌프가 계속 돈다는 증거가 아니다. 시계는 앱과 별개 명령이며 일반
제어의 과거 시각 일치 조건이 아니다.

## 시험 및 원복 범위

기존 speed/feed-time 3프레임 및 선택적인 4프레임 저출력 준비를 compound 필드와 맞췄다.
준비형은 ON/TimerOFF의 초기 speed를 기록해 준비→시험→준비값→실제 원래 speed로 복귀한다.
이 시험용 진입 조건은 일반 속도 슬라이더의 조건이 아니다. 전원 시험은 별도 safer-OFF
계약이므로 실패하면 무조건 원래 ON으로 되돌린다고 약속하지 않는다.
전원 시험의 반환값은 자기 SwitchON과 현재 출력 범위를 본다. 사용자가 바꾼 통신 Mode,
TimerON, 속도가 과거 baseline과 같아야 한다는 조건은 제거했다. 시험 도중 타이머가 켜져
있으면 AutoGears를, 수동이면 Motor_Speed를 범위 검사하며 과거 Auto와 대조하지 않는다.
OFF 명령 뒤 관측은 SwitchON=false만 확인한다. 다시 ON을 보내기 직전에는 아직 OFF라도
켜질 출력을 fresh 상태에서 검사하므로, OFF 구간의 비활성 값과 ON의 출력 검사를 혼동하지 않는다.

feed 시험은 반대값→원래값→실제로 쓴 필드 원복이다. 타이머 시험은 bounded 전일 image→ON
→OFF→초기 persistent 제어 원복→원 image 순서다. 두 app 시험 프레임만 자격에 쓰고 image
준비·자기 원복은 자격으로 세지 않는다. TimerON에서 시작하면 첫 image write가 동작 중인
프로그램을 바꾸며, 원복 도중에도 잠시 trial 프로그램이 남는다. bounds를 만족한다는 코드
검사는 물리 안전 증거를 대신하지 않는다.
새 시험에는 45 상한을 적용하지만 owned_restore와 원 시간표 복원의 관측은 설정 bounds를
쓴다. 원 시간표의 허용 출력 55/config 60을 되돌린 뒤에도 시험 상한 45로 실패시키지 않는다.
균일한 전일 trial image가 이미 저장돼 있어도 ON/OFF 두 명령 시험을 막지 않는다.

## 보류 항목 및 재개 조건

1. **transport 인계·원복**: 코드가 가진 것은 Mode의 vendor 선언과 인코더뿐이다. 현재
   AP측 연결을 열거나 전환 후 재발견·원복하는 절차가 없다. 성공하면 반드시 연결이 끊긴다는
   펌웨어 가설도 확정하지 않는다. (a) 별도 승인된 앱 전환과 read-only capture,
   (b) 앱/SDK의 AP·Wireless 전송 경로 정적 추적 후 명시적 인계 구현이 대안이다. 그 경로와
   현장 복구 수단이 확보되면 왕복 시험을 재개한다. 영향은 transport 한 기능의 실행 노출뿐이다.
2. **활성 template 바이트**: 실제 loader가 읽는 파일 관측 또는 정확히 resolve된 원본
   배포물을 확보하는 두 경로가 있다. 제품 귀속과 실제 바이트가 확인되기 전에는 cached
   사본으로 실제 자격을 발급하지 않는다. 오프라인 구현·테스트·비활성 image 준비는 계속한다.

따라서 전체 선언 8기능 중 실행 표면 후보는 7종(HA 6 controls + schedule API 1)이며,
transport encoder/진단 1종을 모두 구현·수용 완료라고 합산하지 않는다.

## 검증 및 비활성 후보

순수 planner 68 PASS, feed/timer producer 32 PASS. writer/실제 coordinator의 image+seed
기록·고장 복구, 기존 준비형·scalar·clock·CLI·HA를 통합했다. 전원 관련 직접 회귀는 기존
power producer까지 67 PASS다. 최종 Mac 전체는 **3759 PASS / Linux 전용 1 SKIP**
(111.43초), Linux 전체는 **3760 PASS / 0 FAIL**(198.72초)다. Ruff `src tests`와 diff
검사가 통과했다. Linux 검증 컨테이너는 network=none이고 운영 safety volume을 붙이지 않았다.

후보의 schema는
`3efe95cbe697a5ac60d2a274b94c2d3f4c26210330a84960a6d946578a20d3b7`이다.
후보 설치 패키지의 import·schema smoke를 네트워크 없이 확인했다. Linux 전체 실행과
최종 비활성 입력 생성은 동일한 고정 소스의 image에서 성공했다. 최종 image는
`sha256:ab76f8075bf7d32510734eb9ce79f0c031e42e485b0945d62b2f7cff790a73cb`다.

이 후보는 **OBSERVER / dry_run=true / 쓰기 허용 0대**다. 7개 job도 모두 실행 비활성이며
active Aquarium template digest는 null이다. manifest에 들어가는 세 template digest는
Pro/Bar/DC 바이트일 뿐 Aquarium의 활성본을 증명하지 않는다. 예상 7 pair / 14 receipt는
실제로 자격을 얻는 경우의 수이고 현재 발급은 0이다. feeding_duration의 preparation_speed=40은
급이 시간값이 아니라 기존 저출력 준비 프레임의 속도다.

준비 스크립트에는 writer/session/journal/CLI/소켓 호출이 없고, 새 입력만 O_EXCL/fsync로
쓴다. report의 hardware_access/device_writes=0 상수 자체를 증거라고 해석하지 않는다.
기존 입력 3개를 전후 해시로 대조하고 대상의 physical identity·role을 유지한다.

DC/Pro/Bar의 앞선 후보들은 각자의 image와 schema에 고정된 중간 산출물이다. 이 새 schema와
앞선 manifest/영수증을 혼용하지 않는다. 실제 운영 전환에는 최종 공통 image의 바이트로
전체 대상 입력·manifest·자격을 다시 맞춰야 한다. 운영은 여전히 이전 Pro/Bar image이며,
이번 단계에서 교체·재시작·장비 송신은 하지 않았다.

운영 image/상태/restart count와 config·manifest·qualifications 해시는 기존 값과 같았다.
별도의 host Compose 파일은 현재 계정의 읽기 권한이 없어 이번에 해시를 재확인하지 못했으며,
권한을 바꾸거나 다른 계정으로 우회하지 않았다. 이 확인 한계를 입력 불변 주장과 구분한다.

보존 artifact id는 `aquarium-release-preparation-20260917.0Ujh8j`다. 아래 파일명은 그 묶음
내의 논리 이름이며 private 설정이나 raw 장비 산출물을 저장소에 포함하지 않는다.

| 산출물 | SHA-256 |
|---|---|
| source-final.tar.gz | `f504df25168da8efadfbb855e2e5099cad7e33f2abca88b6e51e7976398b0c19` |
| tests-final.tar.gz | `35a4e335fb211b705432bea1035c56dfbfad4616a45716233ebd6b917717d29f` |
| fixture-base.tar | `b7dabf3ad64eb77d23061d6a3b37e56789e9e428084a59a716681533910ed30f` |
| mac-final-results.xml | `97f332034ce8b2672482a755ed968ab369ecce507bc9347e91f97fd0ab172d2d` |
| linux-final-results.xml | `bbd87599025de32b2f622ec6bf979bf0b41449ba8a35570d7570b2b3e018fc6c` |
| preparation-report.json | `52dab266712c339548953ff8c456d2bda2e5f0f19364e3985ff195647204fb09` |

report는 7 jobs 비활성, 쓰기 허용 0, 실제 영수증 0, 입력 3개 전후 해시 일치를 보존한다.
후보 config와 manifest digest는 각각
`561bef3e90ab2db507976fdd5b00e63510fe59d5a0fffd61be72cac866567a7f`,
`853ceb982469c66a130fa04cc3f10a9d2e24b466db40ab3d830e13024f524645`다.
대상 identity binding은
`72c689a3ea84e2a4a967ba4241e1074d3503a5c913b60934b219253cd3e4c4f8`이며 실제 식별자는
옮기지 않았다. 후보 입력 digest와 운영 입력 digest가 다른 것은 분리된 설정을 만든 결과다.
