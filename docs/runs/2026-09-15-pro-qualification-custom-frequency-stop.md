# run: 2026-09-15-pro-qualification-custom-frequency-stop

- 목적: 최종 제품 image에서 두 Pro의 실제 scalar 요청·반환값과 자기 원본 복원으로 새 자격을 발급할 수 있는지 확인한다.
- 승인: `30931ee`의 고정 Pro qualification 및 조건부 후속 Bar·native·배포 범위.
- 실행 source: `28d885c3257f9311e805100c75e1a25a6a777b31`; 실행 당시 HEAD `a25ccab`은 문서 추가만 있다.
- image: `985d5bacbb8c39ef94ec65ed7f3431afc1a993d5a645168c79c393ad7e6c30ac`.
- schema: `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`.
- operation: `pro_qualification_28d885c_01`, 기존 `jebao-flow-local-pro-qualify` CLI 단회.

**결과: custom 주파수 시험값 선정 오류를 발견했고, 반환값 불일치로 exit2·자격 발급0이다.
Pro 두 대는 이번 시작 원본으로 복원됐다. Bar qualification·새 Sync/Async 결속·운영 배포는 실행하지 않았다.**
앱 카탈로그의 custom 주파수 범위는 5..100인데 고정 시험이 1·2를 선택했다. 이것은 우리 계획 결함이다.
장비가 1을 5로 정규화했는지, 적용하지 않았는지, 다른 응답 특성인지는 이 실행으로 판별하지 못했다.
이를 펌프 고장·custom 기능 미지원 또는 native 기능 불가로 일반화하지 않는다.

## Bar 앱 준비와 시작 상태

사용자는 앞선 [준비 경계 보고](2026-09-15-wavemaker-deployment-precheck.md) 뒤
“Constant·Flow40로 변해놨아ㅓ”라고 했다. 기존 앱 준비 경로를 선택한 것으로 받고, 이전 first Pro
TimerOFF 완료나 Bar 최종 원복 완료로 해석하지 않았다. 현장 질문을 반복하지 않았다.

새 authenticated reply를 장비별 두 번 읽었다. UTC `08:03:04.909751`–`08:03:09.797565`,
총6개 모두 action3이며 보고된 fault7개는 전부 false다. Bar는 실제 constant40/F80,
ON/TimerOFF/independent였다. 384B image는 앱 준비 전과 같다. Pro 두 원432B도 같다.
앱 준비 전 classic91/F80 원본과 앱 준비 후 constant40/F80 baseline은 별도 디렉터리에 보존했다.

| 장비 | 이번 시작 controls | 전체 image SHA-256 |
|---|---|---|
| second Pro | ON/TimerON/independent, constant30/F32, Feed10/custom5 | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` |
| first Pro | ON/TimerON/independent, constant40/F34, Feed10/custom5 | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` |
| Bar | ON/TimerOFF/independent, constant40/F80, Feed10, FeedSwitch/PulseTide false | `8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea` |

물리 identity SHA-256:

- second Pro: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
- first Pro: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
- Bar: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`

기존 운영 서비스는 stopped, 새 source/image/schema는 사전 검토한 것 그대로다. 이번에 재빌드하지 않았다.
기존 observer 사본·새 manifest·두 template root·현재 보호 volume identity·명시적 write opt-in과
두 Pro target을 기존 CLI에 전달했다. 별도 후보 출력 위치를 사용하고 운영 자격 파일을 덮지 않았다.
이름이 고정된 컨테이너 하나를 detached로 시작했고 동일 operation을 다시 실행하지 않았다.

## 실행 순서와 실제 회계

컨테이너 UTC span: `08:03:48.022244821`–`08:04:43.771560765`, exit2.
source journal 생성 `08:03:56.438768`, 최종 `08:04:43.675415`, revision66, `rolled_back`.
첫 action before capture는 `08:03:56.717854`; custom after는 `08:04:33.347552`다.

| source action | 대상·요청 | 실제 결과 |
|---|---|---|
| 00–01 | first→second atomic constant40/F20·ON·TimerOFF·independent | 두 단계 반환값 일치 |
| 02–03 | second 하루 constant40 → constant39 full432B | 두 image exact |
| 04–05 | second TimerON → OFF 및 SwitchON 반전 | 두 결합 응답 일치 |
| 06–07 | second sine → constant | 두 Mode 응답 일치 |
| 08–09 | second Frequency21 →20 | 두 응답 일치 |
| 10–11 | second FeedTime1 →2 | 두 응답 일치 |
| 12 | second Cust_Wav_Freq1 | after actual5≠expected1, 전진 중단 |
| 13–37 | 나머지 scalar 계획 | before/after capture·ACK 없음, 전송 안 함 |

source38개는 전송 전 내구화됐지만 실제 ACK가 기록된 것은13개다. after 일치12개와 불일치1개,
미시작25개를 구분한다. 최종 `rolled_back`이38개 모두에 기록되는 것은 **원본 복원으로 종결된 상태**이지
38개를 전송하거나 개별 rollback38회를 했다는 뜻이 아니다. first Pro는 준비 명령 외 scalar 시험0이다.

자동 own detach는 별도6개 action이다: preparation2개와 first image1개는 `NO_CHANGE`,
second 원 image1개·first 원 controls1개·second 원 controls1개는 ACK와 exact read-back으로 VERIFIED다.
따라서 **전진13 + 복원3 = 전송/ACK 기록16개**, NO_CHANGE3개다. 이는 durable 송신 회계이며,
16개 전부가 요청대로 적용됐다는 뜻이나 별도 물리 계측 수가 아니다. 후속 Bar 계획6개는 실행0이다.

## 실패 원본과 해석

실패 직전 capture: `08:04:30.882960`–`08:04:31.345782`,
실패 직후 capture: `08:04:32.955533`–`08:04:33.347552`. 양쪽 모두 explicit reply action3이다.
둘 다 constant40/F20, TimerOFF/independent, Feed2/custom5이며 status와 wire bytes도 각각 동일하다.

- status SHA: `40f686707f8eaa7c5dc694cd8f186bf03b048b8dab0628162ddd8a61791a7240`
- wire SHA: `c1d235f307ef7cc3eba00306f5a44447bc8ecc71e64cacd52a23eb38b51d7e72`
- forward payload SHA: `886f0388879a8e6cf5793f7cad007cf37c06ef2bcc8cdedb905be1bbe6fc1724`

보존460B control payload는 공식 builder에 `Cust_Wav_Freq=1`을 넣은 결과와 exact다.
선택 flag는 attribute6 하나이고 value offset4의 byte는1이다. 다른 필드 요청으로 잘못 인코딩됐다는
증거는 없다. 최초 `baseline_readback`과 실제 action 직전 capture는 서로 다른 자료이며 구분했다.

`operator_capture/app_catalog.py`는 custom 범위5..100, protocol profile은0..100이다.
`pro_qualification.py`의 두 분기는 모두1·2를 선택해 **app 범위 밖 값이 실제 전송에 도달**했다.
그러므로 “범위 검사에서 전송 전 차단했다”라고 쓰지 않는다. 차단은 **전송 후 응답 불일치**에서 일어났다.
before5/after5는 정규화와 미적용을 구분하지 못하며, 다른 AutoFreq 필드의0→5 선례도 이 원인이 아니다.

실제 오류 진단은 `local_control_action_failure`, action12, function custom_wave_frequency,
`LocalControlExecutionError`, `_advance:320`이다. 예외 메시지는 출력하지 않았지만 기대/실측·단계·
원본 프레임이 남아 국소화됐다. 현재 source에 대한 시험값 결함은 확정이고 펌웨어 내부 기전은 UNKNOWN이다.

### 별도 정적 결함 — ACK body truthiness

16개 acknowledgement blob은 모두0B이고 SHA가
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`다.
`control_session.send_raw_control`은 응답 command와4B sequence 일치를 검사한 뒤 나머지 **body만** 반환한다.
이는 [2026-09-11 성공 기록](2026-09-11-local-manual-flow-qualification-success.md)의 empty-body 계약과 같다.
ACK 전체 wire/sequence 원본은 이번에도 보존하지 않았으므로 이 ACK 주장은 (b) durable evidence다.

그런데 새 producer `_finalize`는 `if not load_blob(ack_digest)`로 empty body를 거부한다.
이번 실행은 거기까지 도달하지 않았으므로 **이번 exit2 원인은 아니며**, 향후 유효 시험값 응답이
모두 일치해도 이 gate가 발급을 막을 수 있는 별도 코드 계약 결함이다. 이번에 고치거나 receipt를 발급하지 않았다.
기존 producer는 digest 존재·내용 무결성과 VERIFIED/실제 반환값을 확인하며 body 비어 있음을 거부하지 않는다.

## 원복 증거와 현재 의무

own detach 생성/종료 UTC `08:04:33.397586` / `08:04:43.663319`, revision24, VERIFIED.
최종 별도 session reply 두 개는 `08:04:42.559131`–`08:04:43.645880` 구간이다.
원432B 전체와 SwitchON/TimerON/Mode/Flow/Frequency/FeedTime/Cust_Wav_Freq/Linkage8개를
보존 raw에서 재계산해 두 대상 모두 exact를 확인했다. final chain의 누적 snapshot은 중복 표본으로 세지 않았다.

별도 사후 수집 UTC `08:05:39.540176`–`08:05:43.824602`, 세 장비 각2회 reply6개도 전부 일치했다.
Pro는 자기 시작 원본, Bar는 앱 준비 후 baseline과 비교했다. 세 장비 fault7개가 모두 false다.
이번 복원은 **처음부터 independent였던 qualification 준비의 복원**이며 Sync/Async 역할 해제 수용 증명이 아니다.

이전 first Pro 앱 최종 TimerOFF/manual constant40/F34와 Bar 앱 전 classic91/F80 복원은 각각 남아 있다.
둘의 현재 상태는 Pro ON/TimerON 및 Bar constant40/F80이다. 자기 qualification 원복이 그 앱 의무를
흡수하지 않는다. 원 서비스 역시 같은 exact image의 exited0 상태이며 새 generation 교체0이다.

## 보존·무결성·도구 오류

private artifact id `pro-qualification-20260915.51uFgd6n`에 launch·진행 조회·종료 로그와 분석기를 보존했다.
원격 논리 artifact `pro-qualification-28d885c/audit-pro-01-v3`는179파일(검증한 blob176+record2+report1),
tree SHA `026b82e328224ea72a867e8e7f03450a98a38dd17aa00420c8f8009c5a643c5d`다.
raw reply/capture는 (a), ACK body·journal 종결과 발급 상태는 (b)로 구분한다.

| 산출물 | SHA-256 |
|---|---|
| source journal | `5466be930270e7be8a5d3d124c0f28a988d9bbe96c2859190e85ce229d2bbe7f` |
| own detach journal | `a050309b407f9920c03d62f47d2b1cf147ca6b2016da8c416e6ea42d366d6e11` |
| audit report | `8cebe615d8900facd81fc35865ea03c6cc030210db2a9cbf51d5872b9e94924e` |
| after-app result | `dd34a9087cd4d531eb5c73fdbc1248a90a74f8d2b4bb80bee810d553c8f3aa15` |
| post-Pro result | `319f52ea9bda4331d7e594c40f353daec7a06fc18a2dbe47253ebb9c8d88d06b` |
| launch log | `361735dbc33d579a1f540d7759c31b5b2e032cc20c2cabc142c66368f38d3e8c` |
| in-progress status | `ad2790d9144200a60953bc911e97f26b25183eba59d308d79764a3c200919592` |
| terminal inspect/log | `01d61a80c05fcdd9a28b4f9c2ca0d86d2d0d7071a7cbafc60a2705129491e2a8` |
| audit stdout v3 | `4300cc6105bcc7e1841536e5adb483c25c0050b6dcd9898570745747daadd014` |

08:12:07.584330의 read-only census에서 후보0, receipt18파일의 tree
`9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`는 실행 전과 동일했다.
운영 config·manifest·qualification 파일의 세 digest도 이전과 같다. 네 blocking path는 모두 부재다.
local-control journal은 이번 source/child·증거가 추가되어 바뀌었고, 그 변경을 불변이라고 쓰지 않는다.

분석기 v1은 read-only safety mount에서 production store의 write-probe가 거부돼 실패했다.
v2는 detach의 intent identity hash를 별도 blob으로 잘못 읽어 실패했다. v3는 원본 모델·blob digest를
검증하는 오프라인 파일 독자로 조회했고 detach identity와 stored qualification intent를 구분했다.
세 버전·두 원 traceback·부분 export 모두 보존했다. safety mount read-only·운영 UID·기존 receipt 권한은
완화하지 않았다. 분석 컨테이너는 network-none이며 장비 접촉0이다. 수동 status 조회를 연속 감시로 주장하지 않는다.

## 새 사실·재개 대안·보류 영향

새로 확정된 것은 (1) Bar 앱 준비가 actual reply에 반영됨, (2) Pro 고정 시험의 app 범위 밖 custom값
전송과5반환, (3) 앞선10개 scalar 응답 및 두 atomic 준비의 일치, (4) 실패 후 자기 원본 복원,
(5) 별도 empty-ACK 발급 gate 충돌이다. 정보0이 아니며 기존 Sync/Async 기능 판정을 뒤집지 않는다.

재개 대안은 두 가지다. (1) 기존 app 범위 helper를 사용한 시험값 선정과 empty-ACK 계약을
producer·직접 tests의 한정 범위로 정정·검토한 뒤 새 단회를 승인한다. producer는 schema 입력9개 밖이므로
writer/protocol을 바꾸지 않는 이 정정 자체는 schema 변경을 요구하지 않는다. 기존 receipt를 고쳐 쓰지 않는다.
(2) 새 제품 배포를 park하고 사용자 앱 원복 및 보존한 prior 서비스 복귀 경로를 선택한다.
어느 쪽도 미완료 native 자격을 빼고 배포하거나 실패를 PASS로 바꾸는 선택이 아니다.

이번 실행 기록으로 단회 실행 권한을 소진한다. source/test 변경0, frozen native/protocol/shared guard
불변이다. 재개 전 필요한 것은 **우리 두 소프트웨어 계약 결함의 정정 범위와 새 실행 결정**이지
같은 현장 질문 반복이 아니다. 보류 중 새 native 제품 기능·배포는 미완료이고 기존 서비스 stopped·앱 의무가 남는다.
