# run: 2026-09-15-native-scenario-async

## 결론

재사용 이미지에 외부 JSON의 Async case를 주입한 단회 시험에서 **입력 read-back PASS,
세 창 response PASS**를 얻었다. 실제 Async 관측95pair 중 적격24pair의192개 필드 비교가
모두 일치했다. 종료는 별도로 **exit2 / experiment_failed_restored**였으며 정상 종료가 아니다.
사후 독립2pair에서 두 원본432B와 시험 시작 controls exact·불일치0을 확인했다.

두 역할에서 선언한 보고 패턴은 재현됐다. 비교 필드에는 위상이 없으므로 Sync와 Async의
물리 동작이 같다는 뜻은 아니다. 최종 first Pro 앱 TimerOFF와 원 서비스 복귀는 여전히
별도 미완료다. 이 기록을 전체 원복 완료나 종료 오류 해결로 읽지 않는다.

## 질문·권한·결속

질문: Async에서 선언한 파형 보고·자기 AutoFlow 전환이 실제 반환값과 일치하는가?
증거는 실제 프레임, 설치 image read-back, 선언한 창별 자동 비교다.
사용자 수용 조건은 정확한 입력과 동작 반환값이며 유속계·영상은 요구하지 않는다.

- 사용자 원문: “async 진행해”.
- 정책: `28b4ef9b7cec785dcddcd045568629567fe60269`. 직전 Sync 기술 실패 후
  중단·보고에 이어 받은 새 Async1회 승인이다. 이전 자동 중단을 소급 취소하지 않았다.
- source: `a4fcd987f36313fd032e51e608c211954aee4c67`; 코드·tests·재빌드 변경0.
- 이미지: `edbbbdaaed5d92c0e5729b8f5f3e91842655b2c255006dc4611ac7b1262a5c58`.
- [외부 JSON](../native-scenarios/sync-async.json)의 `async-wave-and-own-flow`.
- scenario SHA: `32b1f89662110ae1b35f743edc0d3d96e401a7e5e8b351f9ac8e3e2e294acecd`.
- bound SHA: `a3e57c13dfb2289a50f5725dc4cd912825ae1fffaa36316e87c12227c199505f`.
- operation SHA: `5a857944779e1a91a898e75221fff9feb6214352c1469a1880922f5f1dd56301`.
- master/second Pro identity SHA: `72a5d4e77d7330e8d7fc96c9fe27974b1794ec5f6711a661602ff50df5b9d87b`.
- slave/first Pro identity SHA: `85a594d671f80f1e901c66eb5d2624127a77257e9e75a45983b5269ae2422733`.
- 승인 artifact SHA: `5ecc2d46fd0432fc2c660099f0826e60bbe807f129db4016383c3bc39b463717`.
- 기존 wrapper SHA: `09f3d472a82424005a5a20d45fd7eb58a0a23f08a44efa23314c541a177c6510`.

wrapper는 기존 parent-policy 입력 계약을 유지하며 O_EXCL·intent 사본·fsync 후 CLI
preflight1/run1만 호출했다. 새 재개 정책은 승인 artifact의 별도 필드와 이 기록으로 결속했다.
wrapper가 그 새 필드까지 자체 검증한다고 주장하지 않는다. 독립 검토자는 private wrapper의
bytes를 검토하지 않았으며 서술 검토였다.
native receipt 만료 시간만 이번1회 예외로 두고 존재·operation·identity·physical binding,
범위/step·single-write·journal·원복 보호는 유지했다. receipt 변경·새 qualification0이다.
검증된 park 존재 상태의 admission이며 자격을 새로 취득하거나 깨끗한 root에서 실행한 것이 아니다.
이 경로와 schema 불변의 한계는 [직전 Sync 기록](2026-09-15-native-scenario-sync.md)과 같다.

## 시작 snapshot과 선언

시작 fresh2pair는 두 Pro ON/TimerON/independent·원432B exact였다. master manual
constant30/F32, slave manual constant40/F34는 비활성 저장값이다. 당시 Auto는 각각
tidal55/F5·pulse75/F60이었다. 사용자 원 시간표의 현재값과 시험 상한41을 혼동하지 않는다.
시험 safe manual은 master30/slave31·constant/F20이다.

- 원 master432B/14slot: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`.
- 원 slave432B/14slot: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`.
- baseline 요약 SHA: `9582c098e2f43a69a01006d157a8387f529aa0314be681b7af0803e3b0410ff1`.
- device-local anchor 2026-09-15 12:39:00, 경계12:49/12:54.

| 구간 | master Auto 기대/실측 | slave Auto 기대/실측 | slave 별도 Mode/Frequency 기대/실측 |
|---|---|---|---|
| 초기 | Sine35/F30 | Constant30/F5 | Sine/F30 |
| 12:49 이후 | Constant32/F5 | Sine40/F30 | Constant/F5 |
| 12:54 이후 | Constant32/F5 유지 | Sine35/F30 | Constant/F5 유지 |

Constant 입력 frequency0과 반환5는 별도로 선언했다. 비활성 manual Flow31 고정은
현재 출력이나 자기 Flow 적용 증거로 쓰지 않는다. 미래 경계를 미리 적재했으며,
연동 중 master 저장 image만 재작성하는 시험은 하지 않았다.

## 원본과 판정 범위

논리 묶음 `JNS-20260915-async-01`, 원본 프레임 증거(a):

- 96pair, 194행=메타1+192프레임+최종보고서1, 206439B.
- raw SHA: `2c0d866c12543b75969b79c26a15f8470f310ee02509e41ddfc0a6e6e005fe23`.
- 취득 완료 UTC span 03:40:17.769326–03:57:21.083474.
- 두 계획 image 모두96/96 full432B exact; 날짜 일치96/96.
- master action3 96회, slave action3 1회·action4 95회.
- pair1은 Async 전의 Sync 경유 확인이다. slave는 sync_slave/action3,
  Auto Constant30/F5·별도 Mode Sine/F30이며 시각03:40:18.694070Z다.
  같은 operation 안의 준비 표본으로 원본과 census에 보존하고 계약 판정만 제외한다.
- pair2부터96까지95pair가 실제 Async monitor에 대응한다. slave는 async_slave/action4.
- pair51, 03:49:12.852616–13.120920Z에 양쪽 첫 경계 전환 보고.
- pair78, 03:54:05.895752Z에 slave AutoFlow40→35 최초 보고.

원본96개를 모두 Async 계약으로 비교한 첫 census는 pair1의 sync_slave를 이유로
response FAIL을 냈다. 이는 취득 실패가 아니라 준비 단계까지 넣은 분석 범위 오류다.
첫 산출물은 보존했다. 소스의 Sync 경유 경로·monitor_started 시각과 실제
scenario_pair ordinal2..96 로그로 범위를 확인한 후 v2에서 그95개만 비교했다.
이 재분석의 범위 선택은 원본(a)만이 아니라 보존 로그(b)도 사용한다.

v2는 input/response PASS와 실행 보고서의 나머지 필드를 재현했으며 stable_seconds만
원본 취득 완료 시각 사용에 따른 수 ms 차이가 있다. 원 실행 보고서는 바꾸지 않았다.
pair1은 slave가 항상 action4라는 일반화를 막는 사례지만 역할·시점·phase가 달라,
이 한 표본으로 reply/report 교락이 해소됐다고 주장하지 않는다.

| 창 | 판정 | 적격 pair | 마지막 안정 구간 pair | 안정 시간(실행) |
|---|---|---:|---:|---:|
| initial | PASS | 16 | 10 | 271.055039474초 |
| master-boundary | PASS | 6 | 6 | 151.972402964초 |
| slave-flow-only | PASS | 2 | 2 | 32.460881794초 |

24적격×8필드=192 비교 전부 PASS·discrepancies0. 제외는 clock_repeated58,
minute_mismatch2, outside_declared_window11이며 24+58+2+11=95다.
적격 총수와 마지막 안정 구간 수는 다르다. 반복 clock 제외는 candidate를 리셋하지 않고
건너뛰므로 안정 시간을 표본 주기로 환산하지 않는다.
실제 비교는 master auto_mode/auto_flow/auto_frequency, slave 같은3개+
reported_mode/reported_frequency다. feed/custom·위상은 비교 필드가 아니다.
역위상이나 내부 파형·시계 소유를 증명하지 않으며, 두 역할의 비교 대상 보고 패턴이
일치했다는 범위다. 자동 보고서 PASS와 달리 terminal schedule_flow_outcome은 null이다.

## 종료 오류와 원복

다음은 intent·진단의 persisted claim인 증거(b)다. disarm get_state는 원본 프레임을
보존하지 않으므로 이 단계의 action이나 보고 생성 시각은 알 수 없다.

| UTC | 단계 |
|---|---|
| 03:39:55.667704 / 03:40:00.061952 | sentinel 두 image verified / 원 image restored |
| 03:40:06.430280 | field 두 image verified |
| 03:40:17.158493 / 03:40:20.378194 | slave adapter write 완료 단계2회; Sync 경유 후 Async |
| 03:40:21.991942 | monitor_started |
| 03:57:31.096271 | monitor_completed; 세 창 보고 PASS |
| 03:57:56.722271 | 최초 role_observation 실패; 원인 UNKNOWN |
| 03:58:25.684535 | 첫 composed disarm slave3필드 불일치 |
| 03:58:26.318117 | 같은 pass master8필드 exact |
| 03:58:26.319035 | already_done_failed / TemporaryScheduleObserverUnstoppableError |
| 03:58:28.977561 / 03:58:29.589016 | 보상 pass slave/master8필드 exact |
| 03:58:29.589826 | role_disarmed |
| 03:58:33.163982 | field 원 image 두 개 restored |
| 03:58:43.020977 / .043406 | outer_restored / terminal experiment_failed_restored |

첫 slave 불일치는 Frequency5≠20, Linkage async_slave≠independent, TimerON≠OFF다.
나머지 online/no-error/enabled/power31/Modeconstant는 일치했다.
직전 Sync와 같은 예외 경로·동일 실패 필드 집합이며 Linkage 실제 역할명은 다르다.
명령 미적용·적용 지연·이전 상태 보고 중 원인은 구분되지 않는다. 앞선 role 오류도
이 뒤의 세 필드로 설명하지 않는다. frame은 _disarm_temporary_schedule:2461이다.

컨테이너03:39:27.928350261–03:58:43.133327942Z, exit2. 정상 경로 logical 예상23은
Sync 경유1과 sentinel4를 포함한다. 실제는 보상 pass가 추가된 실패 분기이며
physical send23회나 성공 write23회라고 주장하지 않는다. 새 recovery 명령·자동 재실행0.

사후 collector exit0 뒤 새2pair(device-local13:06:00/30)로 각 raw가 terminal 이후임과
두 원432B·preflight controls exact를 대조했다. 불일치0·오류0, 양쪽 ON/TimerON/independent,
master constant30/F32·slave constant40/F34다. 네 blocking path 부재·receipt18 내용 불변도
확인했다. 이 원복은 앱 전 상태가 아니라 이번 시작 snapshot까지다.

## 보존·운영 도구 한계

- stdout SHA: `422e2cc0259259fec22e234c8ca273b505936cb0a6a731adfd5d6d236e8cfae5`.
- stderr SHA: `99342368b50a93075b863bacdf80c4d0ce2aeb97e93e778d1a4be2385aac023b`.
- 첫 census SHA: `b62452655067269a5fa1af0d397e5bd9604bb763eae5c07821ae5b4a9d27614c`.
- 범위 결속 census v2 SHA: `50a9aa3be62919baea46b531b398842e5aa280081f4962211ab3d8b149f94a1f`.
- preflight intent SHA: `bc0389557ddf40547bf27d43d5aee6d0b1028e57391b72069d3fa8b60930453f`.
- terminal 사본 SHA: `d72107d9f23d5c5fce4f48f93d66042ad377ef651c5a160580ed828fc7513930`.
- root terminal SHA: `97759e671431c1f07d748d8ab1091512ddd1e181cd28fa12fa6434fa7d980fb8`.
- 사후 exact checker SHA: `77ffc8b6f264884402a87f9c0ba05fa2fb145aa26f42bd498355ef79a7784531`.

초기 SSH255 뒤 원격 실행·산출물 존재를 확인해 중복 기동하지 않았다. 작업 전용 SSH
공유 연결로 다시 접속했으며 전역 설정·호스트 검증은 바꾸지 않았다. host 사용자의 보호
디렉터리 읽기 거부를 빈 디렉터리 증거로 쓰지 않고 기존 UID100으로 확인했다.
요약 script 경로 오류·파일 hash 읽기 거부는 대상 정정과 기존 소유자로 해소했으며 권한
완화는 없었다. 사후 intent 요약 첫 시도는 상대 mount 경로 때문에 Docker125로 끝났고,
절대 경로를 지정한 v2로 완료했다. 실패 stderr도 보존했다. 원격 rg 부재는 grep으로 조회했다.

자동 프레임 보존·응답 비교는 실행 컨테이너에서 이뤄졌다. 에이전트 수동 상태 조회는
03:39:29, 03:54:16, 04:02:44Z였으며 긴 공백이 있다. 그 사이를 연속 감시했다고 주장하지
않고 관측·종료 결과를 보존 산출물로 사후 확인했다. 사용자 반복 질문 거부를 새로운 현장
선언이나 앱 OFF 완료로 각색하지 않았다.

## 새로 확정한 것과 남은 의무

같은 이미지·다른 JSON case만으로 Async95pair에서 입력과 세 창 기대 반환값이 일치했다.
Sync 경유 reply1개도 보존했고, 종료 실패는 직전 Sync와 같은 필드 집합까지 비교 가능해졌다.
정보0이 아니다. 다만 최초 role 오류·composed disarm 근본 원인·위상은 미확정이다.

후속 선택지는 (1) 보존 상태·코드와 사용자 앱 해제 관측을 비교해 읽기/쓰기 순서의 증거를
정리하고 필요한 계측·수정 범위를 별도로 결정하거나, (2) 반환값 결과는 사용하되 종료 오류
수정과 추가 실기를 park하는 것이다. 둘 다 timeout/retry 변경·무인 반복 권한은 아니다.
이 결과 커밋으로 이번 Async1회 권한은 소진되고 source/tests 동결은 유지한다.

사용자는 앱에서도 결속 해제 동작을 언급하고 지정값 덮어쓰기를 제안했다. 앱의 실제 패킷
순서는 확보하지 않았으며, 이 발언을 새 조작 완료나 원인 증명으로 쓰지 않는다.
first Pro 최종 앱 TimerOFF/manual constant40/F34와 동일 원 서비스 복귀는 별도로 열려 있다.
새 앱 완료 회신·fresh 대조 전에는 닫지 않으며, 기존 서비스는 여전히 stopped다.
