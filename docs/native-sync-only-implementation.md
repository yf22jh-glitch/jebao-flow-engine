# Native Sync-only 구현·오프라인 검증

상태: 구현·오프라인 검증 완료, 최종 독립 검토 후 커밋 대상으로 결속합니다.
**실제 장비·앱 변경·production 배포는 승인되지 않았고 수행하지 않았습니다.**

## 승인과 질문

maintainer가 네 native source와 직접 tests의 Sync 준비용 한정 해제를 승인했고,
`d81552f`에 사유·정확한 파일 범위·검증과 커밋 후 자동 재동결 조건을 먼저 기록했습니다.
그 뒤에만 source를 수정했습니다. D1w 실기 기록은 앞선 `774ad13`에 이미 커밋됐습니다.

질문은 slave TimerON에서 Sync의 **슬롯 Flow 내용**과 **전환 시점**이 master와 slave 중
어느 쪽을 따르는가입니다. 이번 구현은 측정 준비이며 기능 답은 아직 UNKNOWN입니다.
Sync 관측을 Async PASS나 물리 유량 측정으로 올리지 않습니다. 기존
[재개 대안](native-sync-async-resumption-options.md)과 미사용 종료된
[두 구간 설계](native-sync-schedule-ownership-recheck.md)를 참고하되, 과거 승인·stash·
기존 baseline을 현재 권한이나 장비 상태로 재사용하지 않습니다.

## 변경 계약

| 구분 | 보존된 legacy fixed Async | 새 fixed Sync-only |
|---|---|---|
| signature (master A, slave A, master B, slave B, Sine F) | 40/35/35/47/50 | 35/32/30/40/30 |
| safe manual Flow | master30 / slave35 | master30 / slave31 |
| master slots | Sine40/F50 → Constant35 | Sine35/F30 → Constant30 |
| slave slots | Sine35/F50 → Constant47 | Constant32 → Sine40/F30 |
| boundary | 같은 선언 시각 | master T / slave T+5분 |
| durable role sequence | SYNC_THEN_ASYNC | SYNC_ONLY, staged progress는 빈 tuple |
| attended cap | 47 | 41 |
| post-boundary stable span | 300초 | 30초 |

새 경계와 결과를 기존 레코드에 소급 적용하지 않습니다. 새 Sync signature는 기존 spec의
필드에서 판별합니다. 저장 필드를 추가하지 않으며, legacy signature의 outer role·schedule
bytes·관측 조건을 유지합니다. 새 CLI preflight는 Sync용이고 legacy token을 새 실행에
전용하지 않습니다. 기존 record의 status/recovery는 저장된 spec을 읽습니다.

source 변경은 승인된 네 파일뿐입니다. `local_control/`과 schema digest의 아홉 입력은
변경하지 않았습니다. 특히 `local_control/schedule.py`의 compiler를 변경하거나 clamp해서
feeding Flow0의 projection 거부를 우회하지 않습니다. 일반 Async·identity·Flow 범위/step·
single-write·journal/fsync·복구 권한·transport timeout/retry를 변경하지 않습니다.

## Sync 역할·진단·결과

master intent → master write → slave intent → Sync write 순서를 유지합니다.
slave의 Sync→Async staging은 새 Sync 경로에서 실행하지 않습니다. SYNC_ONLY의 intent 전·
detach 뒤에는 independent만, intent 뒤·detach 전에는 independent/sync_slave만 복구 가능한
role입니다. record를 가진 곳의 role/sample label은 constructor flag가 아니라 record의
sequence에서 도출합니다. forward flag가 없다는 이유만으로 recovery를 거부하지 않습니다.

Sync role 직후 inactive manual Mode/Frequency는 slave에 한해 기존 bounded staged 비교를
쓰고 기대값·실측값을 진단에 남깁니다. 같은 pair의 master는 immutable 검사 그대로입니다.
slave의 manual Flow exact·TimerON·SwitchON·identity·schedule은 유지합니다.
monitor 중에는 기존 bounded 관측 경로를 사용하며, 이를 mirror 원인 증명으로 쓰지 않습니다.

W0는 양쪽 모두 자기 경계보다60초 초과 전, W2는 양쪽 모두 자기 경계보다60초 초과 뒤입니다.
W1은 master 경계보다60초 초과 뒤이면서 slave 경계보다60초 초과 전인 raw/offline timing
구간입니다. W1을 runtime 성공으로 세지 않습니다. device-local 분이 다른 pair는 제외합니다.
W0는 같은 evidence10 pair·120초, W2는2 pair·30초가 필요하고 전체900초를 관측합니다.
준비와 원복 시간은900초 밖입니다. 새 CLI의 master T는 현재 분을 기준으로7분 뒤이고,
실행 직전 lead는380..420초입니다. 이는 실측 소요 시간 보장이 아닙니다.

whole tuple의 W0/W2 비교로 FULL_MASTER_FOLLOW·OWN_SCHEDULE·COMMON_MANUAL_FLOW·
A_SLOT_HOLD·UNEXPECTED_EFFECTIVE_STATE를 기록합니다. OWN_SCHEDULE도 기존 Async용
PER_SLOT_POWER_VERIFIED 또는 schedule_transition_verified=true로 올리지 않습니다.
Constant의 AutoFreq0/5 밖 값이나 field split을 편의상 같은 tuple로 취급하지 않습니다.

## 검증 상황

테스트는 코드의 순서·저장·복구·분류 계약을 검사하며, fake 입력을 실제 Sync/Async
펌웨어 응답으로 주장하지 않습니다. 중간 실패를 최종 PASS로 덮지 않습니다.

| 회차 | 결과 | 확인·수정한 내용 |
|---|---|---|
| 초기 관련 tests | 566개 중 540 PASS / 26 FAIL, 9.57초 | CLI의 이전 fixed Async 기대값을 새 Sync 계약에 맞추기 전 결과 |
| CLI 계약 갱신 | 80개 중 79 PASS / 1 FAIL, 0.91초 | recovery 검사 fixture에 저장된 role sequence가 누락되어 SYNC_ONLY를 명시 |
| Sync 계약 추가 | 586개 중 585 PASS / 1 FAIL, 9.92초 | 새 fixture의 transition_not_before=0 때문에 sample이 실제로 비었음; 선언 경계 +420초와 APPLYING phase를 맞춤 |
| 관련 tests 보강 | 588개 중 587 PASS / 1 FAIL, 10.18초 | 새 테스트의 ScheduleAutoEvidence import 누락을 수정 |
| 최종 전체 suite, 단회 | **2,611 PASS, 88.38초, exit0** | 기존 2,589개 + 신규 수집 22개; 성공할 때까지 full을 반복하지 않음 |
| 최종 변경 Python 7파일 Ruff | **PASS, exit0** | 줄 길이·import 순서 수정 후 검사; diff check도 PASS |

전체 suite 뒤의 유일한 Python 변경은 linkage test의 같은 from-import 안에서 두 이름을
정렬한 것입니다. 마지막 Ruff 확인 시 원격 사본에 그 파일을 빠뜨려 I001이 다시 보였고,
해당 파일을 동기화한 뒤 통과했습니다. 전체 suite가 마지막 정렬 이전 snapshot에 대한
결과임을 구분하며, 이를 이유로 full을 다시 실행하지 않았습니다.

신규 계약은 sample이 실제 emit되는지와 SYNC_ONLY label이 sync_slave인지 모두 검사합니다.
intent 저장 선행·Sync 단회 write·ACK-loss 뒤 재전송 없는 ordered recovery·기본 flag의
record 기반 recovery·master immutable·slave의 보호 필드·cap42 거부·W1 판정 제외를
검사합니다. CLI의 기존 단언을 지우지 않고 새 fixed Sync 입력과 결과로 전환했습니다.
legacy signature의 저장·복구와 일반 Async의 기존 tests를 유지했습니다.

보존된 test artifact의 opaque id는 `sync-offline-3cZNU5`입니다. 아래는 논리 파일명과
SHA-256이며 장비 실기 증거가 아닙니다.

| 파일 | SHA-256 |
|---|---|
| targeted-initial.log | c1e8bf12a308093ed5dd8be7635cacb3b2a7480926c02fb70cee7111460b6b0b |
| cli-revised.log | 675874b2d167ed2bc417c262cdb5cf6c5c4770d8a7bf481a3ee664c9fbf79069 |
| targeted-sync.log | ab2b985245a5b2012ba1fedced4ca52c6147e4f316ce36d63de4167d2ed2f87e |
| targeted-final-candidate.log | 78798bc3e2c1bdafae512a5d5348b3324600c10778e10f416d7777c070a08eb7 |
| full-final.log | c0c7c3fa8aca6327deae25452327cf83b6a4141b7696a4874e3abaa671a07648 |
| ruff-final-corrected.log | 82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18 |

Ruff 성공 출력은 실행 간 byte-identical일 수 있으므로 이 digest 자체는 실행 식별자가
아닙니다. 위 artifact id·파일명과 검사 대상 Python 7파일을 함께 구분하며, digest만으로
실행 시점이나 입력 snapshot을 증명하지 않습니다.

production의 exact prior image와 진단 image는 변경·재배포하지 않았습니다. test-only
derivative로 network-none·source/tests read-only mount·temporary filesystem에서 검증합니다.
production journal/receipt·장비 설정·broker 연결은 넘기지 않습니다.

초기 test-only build에서 image ID를 FROM에 직접 넣어 registry 이름으로 해석된 실패가
있었습니다. 보존된 local image에 test 전용 tag를 붙여 다시 구성했고 git·pytest·Ruff만
추가했습니다. 이는 production 배포 재시도가 아닙니다. 로컬 Docker를 기동하지 않았고,
테스트 실행에는 network-none을 적용했습니다. 검증 뒤 미사용 test-only derivative 한 개와
기존 진단 image에 붙인 test 전용 tag만 건별 삭제했습니다. exact prior·진단 image의
identity가 그대로 존재함을 확인했으며 보존 로그·volume·운영 container는 삭제하지 않았습니다.

## 다음 경계

독립 검토는 staged source의 범위·삭제·단언 보존·legacy 복구·Sync label·보호 필드와
검증 문서를 확인하고 landing을 승인했습니다. 전체 AST Assert 수는 experiment235→253,
linkage1064→1093, CLI204→204이며 기존 단언 제거는 없었습니다. 검토가 지적한 Ruff
출력 digest의 비고유성을 위에 명시했습니다. 이는 정적 검토이지 실기 기능 판정이 아닙니다.

구현·검증·독립 검토를 기록하고 커밋하면 이번 구현 예외는 소진되어 재동결됩니다.
실기는 별도 단회 승인과 현재 baseline/복구 수단·source/image 결속·명령 회계·관측/중단/
원복 계획이 필요합니다. 현재 수용된 TimerON의 inactive manual30과 active slot 출력은
구분하며, 옛31과 맞추기 위한 불필요한 사용자 조작을 요구하지 않습니다.
