# run: 2026-09-15-native-scenario-sync

## 결론

외부 JSON을 주입한 재사용 이미지의 최초 Sync1회에서 **입력 read-back PASS와 세 구간
response PASS**를 얻었다. 원본96pair와 최종 보고서를 보존했다. 하지만 관측 이후 종료
경로는 `exit2 / experiment_failed_restored`였다. 기능 응답 PASS를 정상 종료로 올리지 않는다.
사후 독립2pair로 원432B·시작 controls exact 복원을 확인했다. 정책에 따라 Async는 실행0이다.

이번 harness의 TimerON baseline 복원과, 과거 앱 이전 first Pro TimerOFF·원 서비스 복귀는
별도 의무다. 후자는 아직 미완료이며 전체 원복 완료라고 쓰지 않는다.

## 질문·권한·실행 결속

목적: 코드 재빌드 없이 선언한 Sync 시간표·파형·Flow·기대 응답이 실제 Pro 반환값과
일치하는지를 원본 프레임과 자동 구간 대조로 확인한다. 사용자 수용 기준은 정확한 입력과
동작 반환값이며 물리 유속계·영상은 요구하지 않는다. 물리 계측·내부 인과 증명은 주장하지 않는다.

- 정책: `9f68f7b`, 최초 Sync1/Async1 최대2회 중 Sync1. 기술 실패·위험·원복 미확인 시 둘째 중단.
- source: `a4fcd987f36313fd032e51e608c211954aee4c67`, 준비 기록 `e0059ea`.
- 이미지: `jebao-native-scenario:a4fcd98`,
  `edbbbdaaed5d92c0e5729b8f5f3e91842655b2c255006dc4611ac7b1262a5c58`.
- 입력: [Sync/Async JSON](../native-scenarios/sync-async.json)의 `sync-wave-and-own-flow`.
- scenario SHA: `bf176447d6e78610caecdba7494f35bbb8022f8117cbd5eb77602796ad942194`.
- bound SHA: `69f5407f3e9bc2c37ccb3705407e8c2c24435f3ac5ce094525354e15fd600cd0`.
- operation SHA: `db0f029e292c840e593d026c248103ac4e96c928066bafb99f90b741010640f9`.
- master/second Pro device ID SHA:
  `72a5d4e77d7330e8d7fc96c9fe27974b1794ec5f6711a661602ff50df5b9d87b`.
- slave/first Pro device ID SHA:
  `85a594d671f80f1e901c66eb5d2624127a77257e9e75a45983b5269ae2422733`.

실행기는 이미지 밖 JSON을 읽기 전용으로 받고 기존 CLI preflight1/run1만 호출했다.
O_EXCL 시작 기록·intent 사본·fsync를 사용했으며 반복 루프나 새 device write 경로는 없다.
wrapper SHA `09f3d472a82424005a5a20d45fd7eb58a0a23f08a44efa23314c541a177c6510`.
독립 검토자는 private wrapper bytes를 읽지 않았고 서술만 검토했다.

native receipt 만료 한정 예외를 이번 operation에 결속했다. 존재·지명 operation·device·
physical binding 검사는 유지했고 receipt 날짜·내용을 고치거나 local-control receipt로
대체하지 않았다. schema 불변은 승인이나 qualification을 새로 부여하지 않는다.

검증된 park가 존재하는 root에서 진입했다. `schedule_flow_verified_park_admission` v1의
`qualified=false`와 다음 해시를 보존했다. 깨끗한 root에서 실행한 것으로 읽지 않는다.

- disposition: `712a725ada86796f112631fdbcfb251350d92f470a4ff076cee5083a9758497f`
- operation: `4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`
- snapshot: `273a7f38638033dd0030a268279bb67b8451b985936051a83f42f605c8486bcf`

## 시작 snapshot과 시간표

fresh baseline 두 pair에서 두 Pro online/ON/TimerON/independent·원432B exact를 확인했다.
master는 manual constant30/F32·당시 Auto Sine50/F25, slave는 manual constant40/F34·
당시 Auto Sine70/F80이었다. 비활성 manual과 현재 Auto를 혼동하지 않았다.
시험은 safe manual master30/slave31·constant/F20을 거쳤고 상한41·장비 범위/step을 유지했다.

원 시간표(각432B/14slot)의 SHA:

- master: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
- slave: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`

preflight 장비-local anchor는 2026-09-15 10:49:00, 경계는 10:59와 11:04다.
절대 시각과 전체 선언을 intent/raw에 결속했고 실행 중 코드·값·기대값·판정 조건을 바꾸지 않았다.

| 구간 | master Auto 기대/실측 | slave Auto 기대/실측 | slave 별도 Mode/Frequency 기대/실측 |
|---|---|---|---|
| 초기 | Sine35/F30 | Constant30/F5 | Sine/F30 |
| 10:59 이후 | Constant32/F5 | Sine40/F30 | Constant/F5 |
| 11:04 이후 | Constant32/F5 유지 | Sine35/F30 | Constant/F5 유지 |

Constant 입력 frequency0과 반환5는 시나리오에 각각 명시했다. 마스터가 안 바뀌는 두 번째
경계에서도 slave 자기 AutoFlow40→35가 보고됐다. slave의 비활성 manual Flow31은 전 구간
유지됐으며 이를 현재 출력이나 자기 Flow 적용의 증거로 쓰지 않는다.

## 실제 raw·자동 응답 대조

증거 (a), 논리 묶음 `JNS-20260915-sync-01`:

- 원본: 96pair, 194행=메타데이터1+192프레임+최종보고서1, 206393B.
- raw SHA: `1021c44c0aee71e5c72617faf966ff59cdfa7bbc1e94fb5fcdc8d8e1158cc995`.
- 프레임 취득 완료 UTC span: 01:49:58.998143–02:06:51.252509.
- 두 이미지 모두96/96 full432B exact, 역할도 전 표본 결속 일치.
- master action3 96회, slave action4 96회; 양쪽 날짜 일치96/96.
- pair53, 01:59:13.394218–.418881Z에서 첫 전환 보고.
- pair81, 02:04:10.580277Z에서 slave-only AutoFlow40→35 최초 보고.

실제로 비교한 필드는 master의 auto_mode/auto_flow/auto_frequency, slave의 같은3개와
reported_mode/reported_frequency다. feed/custom 반환 필드는 이 case의 비교 대상이 아니며
`compared_fields`가 이를 명시한다. 전체 이미지 exact와 옵션별 반환 검증을 구분한다.

| 창 | 판정 | 안정 pair 수 | 안정 시간(실행 보고서) |
|---|---|---:|---:|
| initial | PASS | 17 | 468.519934647초 |
| master-boundary | PASS | 6 | 159.639707794초 |
| slave-flow-only | PASS | 2 | 32.338535644초 |

25개 적격 pair×8필드=200개 비교가 전부 PASS, discrepancies0이다. 나머지는
clock_repeated61·outside_declared_window10으로 25+61+10=96이다. 제외된 원본도 남겼다.
`input_readback=PASS / response=PASS`, 세 창의 표본·안정 요건 모두 충족했다.
단 terminal `schedule_flow_outcome`은 null이다. 종료 오류 뒤 intent에
`SCENARIO_RESPONSES_OBSERVED`가 기록됐다고 주장하지 않는다.

보존 프레임을 네트워크 없는 동일 이미지에서 다시 decode/대조해도 PASS였다. 실행 보고서와
다른 필드는 stable_seconds 세 개뿐이다: raw 취득 완료 시각을 쓰는 재분석은 각각
468.524622829·159.636803190·32.334231602초다. 실행 비교 시각과 수 ms 차이가 나므로
보고서를 바이트 동일하게 재현했다고 쓰지 않는다. 원 실행 보고서는 수정하지 않았다.

기대값은 이전 Sync의 보고 패턴을 근거로 미리 선언한 반증 가능한 계약이다. 이번 PASS는
이 입력에서 같은 보고 패턴이 재현됐다는 뜻이다. 역할·물리 장비·reply/report가 교차 통제되지
않았으므로 내부 파형/시계 소유, master-only image 재작성의 자동 전파, 물리 역위상까지
확정하지 않는다. Async의 동일 기대값도 가설이며 부정 응답 자체는 기술 실패가 아니다.

## 종료 기술실패와 후속 원복 — 기능 응답과 별개

아래 단계는 (b) 보존 intent·JSON 진단의 주장이다. disarm의 구조화 읽기값은 원본 프레임이
아니며 action이나 보고 생성 시각을 보존하지 않는다. 관측 raw의 slave action4를 이 단계에
그대로 적용할 수 없다.

| UTC | 단계 / 증거 |
|---|---|
| 01:49:35.967466 | sentinel 두 대상 verified |
| 01:49:39.653514 | sentinel 두 원 image restored |
| 01:49:45.115157 | field 두 대상 verified |
| 01:49:57.597640 | monitor_started |
| 02:07:01.263341 | monitor_completed, 세 창 보고서 PASS |
| 02:07:25.767012 | role_observation failure; 이 최초 오류의 원인은 UNKNOWN |
| 02:07:53.293389 | composed disarm 첫 slave 확인에서 Frequency·Linkage·Timer 불일치 |
| 02:07:53.794339 | 같은 pass의 master8필드 exact |
| 02:07:53.795370 | already_done_failed / TemporaryScheduleObserverUnstoppableError |
| 02:07:56.253441 / .661581 | 뒤 보상 pass의 slave/master 각각8필드 exact |
| 02:07:56.661864 | role_disarmed |
| 02:08:00.263420 | field 두 원 image restored |
| 02:08:11.451167 | outer_restored |
| 02:08:11.474856 | terminal experiment_failed_restored, wrapper exit2 |

첫 composed disarm slave의 불일치: Frequency 실제5/기대20, Linkage 실제sync_slave/
기대independent, Timer 실제ON/기대OFF. online·no-error·enabled·power31·Modeconstant는
일치했다. 이 자료는 `safe=False`의 항목을 좁히지만 명령 미적용·적용 지연·오래된 보고를
가르지 못한다. 앞서 발생한 role_observation 오류까지 이 세 항목으로 설명하지 않는다.
예외 frame은 `_disarm_temporary_schedule:2461`이다. 과거 계측 없는 실패를 같은 원인으로
소급하지 않으며, 의도된 안전 거부라는 이유로 정상 종료로 바꾸지도 않는다.

정상 경로 logical 예상22(전진10+보상12)는 sentinel4를 포함한 정적 회계였다. 실제는
기술 실패 후 composed disarm 보상 pass가 추가된 분기다. 위 단계의 완료 수와 네 disarm
읽기 진단을 보존했지만 전체 physical send 수나 성공 write22회로 단정하지 않는다.
새 recovery 명령·재시도 루프는 없고 기존 바깥 복원 경로가 수행됐다.

## 사후 독립 복원 확인과 보존

종료 후 fresh2pair(device-local11:09:00/30)를 별도 collector로 보존했다. 각 raw의 시작이
terminal 이후이고, 두 원432B SHA·각 preflight controls exact, 불일치0, 오류/invalid slot0이다.
master는 constant30/F32, slave는 constant40/F34; 둘 다 ON/TimerON/independent다.
현재 Auto50/70은 진행 중인 원 시간표의 그 시점 값이지 고정 복원 목표가 아니다.
네 blocking path 부재와 receipt18 내용 불변도 확인했다.

사후 checker를 collector 완료 전에 먼저 시작한 운영 도구 순서 오류가 있었다. 첫 결과는
2개 raw 요구에 대한 AssertionError이며 원 로그를 보존했다. collector exit0 이후 같은
완성 산출물에 checker만 v2로 다시 실행해 PASS였다. 새 collector·write·단언 변경은 없다.
intent 요약 v1도 잘못된 stage 키로 빈 배열을 냈다. v2에서 실제
schedule_flow_stage_events를 읽어 위 전수 타임라인을 확인했다. 두 요약을 모두 보존했다.
private 분석 script는 기존 소유자가 stdin으로 전달해 UID100에서 읽기 전용 수행했고
보호 root·script·receipt 권한을 완화하지 않았다.

추가 결속:

- stdout: `bc2730bd14aebfd66668f403635839a4079802707270ed5cc674e58b0b22a811`
- stderr: `d082246f3b36a41c8c96fe4261d98b6adabca2a6c92976aeab7c44f7f9638a2b`
- raw 재분석: `0dc6f4a93f60444ea3a6af9db59c80bea6e696e172d3e341f554c35a87d1c6d5`
- preflight intent 사본: `69fcced1ef9b3bf06957555144613a92b28023879c34676cf1d42bfa5ca2172b`
- terminal intent 사본: `9764ab368554eed8b0fdc967198e265d68a6565ead2eb857b97be579a09693d3`
- root terminal intent: `f1b7f871ca29db326aac961e9856083e734614db150f4c2dfe130b43e19ead29`
- 사후 exact 대조 v2: `eb0160dccf4e0dce6123f5a5d09d55cb21b3b9c6af4ec7ba9fdfcfe3e2054025`

root intent와 wrapper 사본은 직렬화 파일이 달라 digest가 다르며 같은 파일이라고 쓰지 않는다.
주기적으로 원격 상태/로그를 읽었고 원본은 장비 측 실행 컨테이너에서 별도로 지속 보존했다.
poll 사이까지 끊김 없이 사람이 모든 로그를 봤다는 연속 감시 주장은 하지 않는다.
컨테이너가 exit2로 종료된 뒤에도 남은 로그 attach 대기는 해당 로컬 SSH만 종료했다.
SSH 종료값255와 장비 시험 exit2는 별개이며 컨테이너 종료 상태·stdout/stderr digest 불변을 확인했다.

## 새로 확정한 것·남은 것

새 이미지1개+외부 JSON으로 실제 Sync 입력과 세 구간 응답을 자동 비교해 PASS를 얻었다.
이는 소프트웨어 테스트만의 결과나 정보0이 아니다. 첫 composed disarm의 실패 필드3개와
뒤 보상 pass의 exact 상태도 처음 보존했다. 종료 전체의 근본 원인은 아직 UNKNOWN이다.

Async는 정책대로0회이며 이 실행에서 기능 판정을 만들지 않는다. 재개 대안은 (1) 보존된
단계·코드로 종료 오류를 더 분류하되 부족한 disarm frame/write 결과 진단의 별도 범위를
결정한 뒤 새 실기를 승인하거나, (2) Sync의 반환값 결과만 제품 근거로 사용하고 Async는
park하는 것이다. 추가 무인 반복·timeout/retry 변경은 어느 쪽에도 자동 포함되지 않는다.
park하면 Async 보고 패턴·역위상 관련 질문은 열려 있지만 재사용 이미지와 JSON 검사기는
계속 사용할 수 있다. 두 역할 통합 시험을 모두 완료했다고 보고하지 않는다.

최종 first Pro 앱 TimerOFF/manual constant40/F34와 원 서비스 복귀는 여전히 열려 있다.
이번 자기 snapshot 원복은 완료됐지만 과거 앱 전 원복 의무를 흡수해 닫지 않았다.
사용자에게 최종 토글을 안내했으며 회신·fresh 확인·동일 prior container 재시작 뒤 각 의무의
종결을 별도 append한다. 추가 실행은 중단했고 source/test는 재동결 상태를 유지한다.
