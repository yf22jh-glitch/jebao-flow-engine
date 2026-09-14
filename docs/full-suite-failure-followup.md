# Full-suite 실패 두 건 후속 조사

2026-09-14. 기준 HEAD `5a377a4973e890a40be20cb8ef0cadd9773e317e`.
상시 진단 배포보다 실패 확인·처리를 먼저 하라는 maintainer 요청에 따라 배포를 보류합니다.
이번 작업은 격리된 simulator 시험이며 실제 장비·브로커·운영 journal/receipt에 접근하지 않습니다.

## 질문과 판정 범위

전체 시험의 두 실패가 어느 코드 경로에서 가능한지 확인하고, 시험 의도와 실시간 기한의
경쟁을 분리할 수 있는지 검사합니다. D1u Bar 예외 원인을 조사하는 작업과는 별개입니다.
원래 전체 시험에는 아래 단계별 시각이 보존되지 않았으므로, 새 강제 지연 재현이 원래 실패의
원인을 증명하지는 않습니다. 원래 full-suite 실패 두 건은 삭제하거나 PASS로 바꾸지 않습니다.

| 원래 실패 | 보존된 결과 | 이번 확인 |
| --- | --- | --- |
| `test_success_journals_before_each_write_and_exactly_restores` | TIMEOUT, `lower_power_applied=False` | 30ms 전체 기한이 출력 변경 전에 소진되는 경로를 강제 지연으로 재현 |
| `test_live_slave_failure_survives_masking_rollback_failure` | 기대한 `primary_failure` 대신 None | monitor 이전 지연으로 같은 실패 재현; 명시적 단일 테스트 예외 뒤 단계 시계 수정안 검증 |

## 격리와 원본 대조

기존 Python3.12 시험 의존성을 read-only로 연결하고 network-none 컨테이너에서 실행했습니다.
실제 장비 설정·운영 볼륨·Docker socket은 연결하지 않았습니다. pytest 임시 파일은 컨테이너의
임시 파일시스템에만 생성했습니다. 시험용 프로세스는 읽기 권한 때문에 root로 실행했으며,
운영 파일의 소유권이나 권한을 바꾸지는 않았습니다.

다섯 관련 파일(verification/linkage/hardware_test source와 두 test module)의 SHA-256이
기준 worktree와 시험 snapshot 사이에서 일치함을 확인했습니다. 수정안 검증은 그 위에
수정한 verification test 파일 하나만 read-only로 덮어 연결했습니다. 다른 source/test는 그대로입니다.

원본 두 테스트를 한 번 실행하며 `sys.setprofile`로 단계와 monotonic 경과를 수집했습니다.
결과는 `2 passed in 1.41s`였습니다. profiler가 실행 시간을 바꿀 수 있고, tmpfs·고립 실행은
원래 전체 시험의 I/O·선행 상태와 다르므로 이 통과로 원래 실패를 배제하지 않습니다.
coroutine 재개도 profile call로 나타나므로 call 개수를 operation 횟수로 세지 않습니다.

## 첫 번째: 성공 경로와 30ms 기한 분리

`FirstPhysicalWriteVerifier.run()`은 연결·준비·journal 생성 전에 monotonic 기한을 잡습니다.
원본 성공 테스트의 `_spec()` 기본 duration은 30ms입니다. SAME_VALUE_VERIFIED를 저장한
다음에도 기한을 검사하므로 이 지점에서 시간이 소진되면 lower write 없이 TIMEOUT으로 복원합니다.
이 안전 중단은 구현의 오류가 아니지만, 성공 테스트는 lower write가 있었다고 단언합니다.

격리된 diagnostic에서 실제 SAME_VALUE_VERIFIED 저장이 끝난 뒤 40ms 지연을 한 번 넣었습니다.
관측 지연은 `40,098,007ns`였고 저장 순서는 SAME_VALUE_PENDING → SAME_VALUE_VERIFIED →
RESTORE_PENDING이었습니다. 원래와 같은 TIMEOUT/`lower_power_applied=False` 단언 실패가
재현됐습니다. 이것은 해당 실패를 만드는 충분한 조건 하나이지, 원래 full run에서 그 지연이
발생했다는 증거가 아닙니다. 펌웨어 동작을 가정한 fake나 production 게이트를 추가하지 않았습니다.

수정안은 이 성공 테스트만 setup 기한을 10초로 두고, LOWER_POWER_ACTIVE가 실제 journal에
저장된 뒤 monotonic 기한을 현재로 설정해 기존 `_monitor()`의 TIMEOUT 경로를 실행합니다.
10초는 성능 합격 기준이 아니며 그 시간을 기다려 통과시키는 방식도 아닙니다. setup이 그 기한을
넘으면 여전히 실패합니다. 성공 경로 도달과 기한 만료 시점을 단계로 명시한 것입니다.

기존 TIMEOUT, lower 적용 여부, write `[40,35,40]`, 각 write 이전 journal 저장, 마지막 read와
journal 삭제, 전체 선언 필드의 exact restore 단언은 모두 유지합니다. LOWER_POWER_ACTIVE
저장 확인과 monitor 진입 단언을 추가했습니다. production 시간 제한·write·복원 로직은
수정하지 않습니다. 기존 preflight 기한 만료/zero-write 시험도 변경하지 않습니다.

같은 지연 diagnostic을 수정안에 한 번 적용한 결과 PASS였습니다. 관측 지연 `40,076,785ns`
뒤 LOWER_POWER_PENDING → LOWER_POWER_ACTIVE → RESTORE_PENDING까지 확인됐습니다.
해당 test module `51 passed in 0.86s`, Ruff PASS입니다. 전체 suite를 재실행하거나 전체 PASS를
주장한 결과는 아닙니다.

## 두 번째: 동결 경계

hardware test는 operation 전체 duration80ms를 주지만 live 변경의20ms 지연은
`_monitor_until_stop()`에 진입한 뒤부터 셉니다. 연결·준비·fsync가 먼저 기한을 소비합니다.
변경 전에 기한이 소진되면 의도한 discarded-write/full-state mismatch를 주입하기 전에
종료될 수 있습니다. 원래 실패에서 실제로 그 경로를 탔는지는 이번 고립 PASS로 확정하지 못합니다.

`AGENTS.md`의 2026-09-03 제한 해제에는 `hardware_test.py`와 그 직접 테스트가 포함됐고,
뒤의 미사용 종료 절이 다시 동결했습니다. 따라서 처음에는 수정·새 fault injection 없이
maintainer에게 이 실패 테스트 하나만 수정하는 예외를 요청했습니다. “ㅇㅇ 진행하자” 승인 뒤
별도 정책 커밋 `312a047`을 먼저 만들고, 그 뒤 해당 함수 내부만 수정했습니다.
native source 변경이나 실기 권한은 예외에 포함하지 않습니다.

예외 승인 후 원본 테스트의 ACTIVE monitor 진입 직전에 90ms 지연을 한 번 넣었습니다.
관측 `90,089,977ns` 뒤 `LinkageTransactionError`가 발생하고 원래처럼 primary failure가
None인 단언 실패가 재현됐습니다. 앞선 verification 지연 진단과 마찬가지로 충분 조건을
검사한 것이며 원래 full run의 실제 지연을 복원한 결과가 아닙니다.

수정안은 해당 함수의 setup 기한을10초로 두고 실제 monitor에 진입한 뒤 첫 loop 시계 읽기는
실시간을, 이후는 실시간+예약 지연20ms를 반환해 live 변경을 due로 만듭니다. 시간이 멈추는
fake가 아니라 이후에도 실시간 진행과 timeout이 유지됩니다. 변경 시점까지 실제20ms를
기다릴 필요를 없앴고, 원래 monitor·write·readback·실패 분류를 그대로 실행합니다.
시험 clock override는 `monkeypatch.context()`가 성공·예외 어느 쪽에서도 복구합니다.
첫 읽기가 현재 구현의 monitor_started 대입이라는 호출 순서에 결속돼 있습니다.
오프셋은 그 비교뿐 아니라 기존 operation monotonic deadline 비교에도20ms만큼 작용합니다.
패치 종료 시 오프셋은 제거되므로 모든 시계 소비자의 전 구간 단조성을 증명하는 fake는 아닙니다.
기존 discarded write와 masking rollback 주입은 유지하고 ACTIVE monitor1회·discard38
정확히1회 단언을 추가했습니다. 다른 함수·공용 fixture·production source는 변경하지 않습니다.

같은 지연을 수정안에 한 번 적용하면 `90,099,367ns` 뒤 의도한
`LinkageLiveSlavePowerVerificationError` 경로가 실행되고 테스트는 PASS였습니다.
두 관련 module은 `153 passed in 4.23s`, Ruff PASS입니다. 이는 기존 전체 실패의 원인이
밝혀졌다거나 모든 시간 민감 테스트의 안정성이 입증됐다는 뜻은 아닙니다.

AST 대조에서 verification은 기존 단언13개 모두 보존·3개 추가, hardware는 기존21개
모두 보존·4개 추가입니다. 각 파일의 대상 함수 밖 AST는 그대로입니다.

## 보존 자료와 남은 상태

private artifact `bar-off-flow-diagnostic-a9c77231316e506777a70d38ef939347`에 diagnostic
script, 다섯 파일 결속과 stdout/stderr를 별도 이름으로 보존했습니다. 아래는 structured test
output이며 실제 장비 raw 증거가 아닙니다.

| 산출물 | stdout SHA-256 |
| --- | --- |
| 원본 두 테스트 단계 관측 | `ee0034b5ba8c5507c2ce64edd3c8c2962390b6a0d222a837e34802b8d2ed1dfa` |
| 원본 verification 강제 지연 FAIL | `f8ca5cc8f3464a1c02a5502ebe1a0535a52e32a5053fd401fe19048bd457feb6` |
| 수정안 같은 강제 지연 PASS | `29729d0148866050740d60fb5d124917b5c00fa7d8aca1c1cbaf3082b7b10b75` |
| 수정안 module51 PASS | `a54beddf999cf030255ea36da614a22e25a1e45a020151b039266208fcd83bf4` |
| 수정안 Ruff PASS | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| 원본 hardware 강제 지연 FAIL | `77e15152c56fd377a443112c3f961712cfb1299884b65cf82d3126b32fa247b7` |
| hardware 수정안 같은 강제 지연 PASS | `7605bb58f6a76e1649c7ec0e508f33061605cdbbc0127a8fc5ca7c84cf1c6b8c` |
| 두 module153 PASS | `cf07792a7ffdfeea65c9b2e74e206e82e984e7ceb24a08ea90710bb5e15d91a2` |

위 여덟 stderr는 모두 빈 파일이며 SHA-256은
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`입니다.
## 최종 검증과 종료

두 수정안을 적용한 최종 full suite는 **2589 passed in106.82s**, exit0입니다. 최종 full은
한 번만 실행했으며 성공할 때까지 반복하지 않았습니다. 기존 Git 필요 테스트도 포함했습니다.
stdout2913bytes의 SHA-256은
`dfcb0bb9450bd60a918d83ad45330998f88bb177ec7ad10bf5cea5bddde4ae75`, stderr는 빈 파일입니다.
수집 개수2589는 수정 전 refined suite와 같습니다. 테스트 삭제나 skip 추가는 없습니다.

실행에는 exact prior image에 Git만 설치한 시험 전용 derivative를 사용했습니다.
시험 source는 정책 HEAD312a047 archive와 두 수정 test 파일이며 생산 source delta는0입니다.
source archive SHA-256:
`a644dd6e355e7fdbd6c5b4afde38a5ad1f757fe3942c7cc84b24919b345a7d6b`.
Git 설치 image는 첫 build exit0이며 실제 시험은 network-none/read-only source와 tmpfs에서
수행했습니다. 이 환경의 단회 전체 PASS는 원래 두 실패의 환경 원인이나 향후 모든 실행의
안정성을 증명하지 않습니다.

전체 실행 중 검토 요청에 따라 두 주석만 추가했습니다. 최종 파일과 실제 full 실행 파일의
AST가 각각 exact 동일함을 확인하고 최종 Ruff를 다시 검사했습니다. 이때 macOS tar가
추가한 AppleDouble metadata 두 파일까지 디렉터리 단위로 스캔하여 최초 Ruff가 UTF-8 오류로
실패했습니다. 그 출력도 보존했습니다. 실제 변경 Python 파일 두 개를 명시한 Ruff는 exit0이며
소스·단언·Ruff 규칙을 바꾸지 않았습니다. 이는 archive 포장 파일의 검사 범위 정정이고,
전체 시험 재실행은 아닙니다. 최초 Ruff stdout SHA-256:
`8879e8ce97d5506de41981523bb5bfb505b5963e4ee38a11c690cf080e2edd18`.

정적 독립 검토에서 두 수정 모두 범위 위반·단언 완화 없음으로 승인됐습니다. 시험 전용
Git image 하나는 사용 중인 컨테이너가 없음을 확인한 뒤 개별 삭제했고, prior/candidate
운영 image 둘은 보존됐음을 확인했습니다. 시험 자료는 삭제하지 않았으며, 시험 image는
보존한 Dockerfile로 재빌드할 수 있지만 같은 digest 재생성을 보장하지 않습니다.

이 커밋은 두 테스트의 의도와 초단기 실시간 기한 경쟁을 분리한 수정·검증을 완료하고,
`312a047`의 단일 함수 예외를 소진합니다. 해당 hardware 테스트는 다시 동결됩니다.
원래 full-suite 두 실패의 실제 원인과 진단 변경과의 상호작용은 여전히 미확정입니다.
이는 현재 수정본의 전체 PASS와 별개의 역사적 한계이며 D1u 예외 원인도 바꾸지 않습니다.
이번 작업에서 application 배포·device command는0입니다.
