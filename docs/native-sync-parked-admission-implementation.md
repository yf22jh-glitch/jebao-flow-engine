# Native Sync 준비 · 보존된 park 기록의 진입 판정 수정

## 질문과 승인 범위

이번에 닫는 질문은 **영구 보관 자료로 확인된 종료 기록을 삭제하지 않고 Sync CLI가
진행 중 복원과 구분할 수 있는가**입니다. 실제 Sync 파형·Flow 소유권 질문의 답은 아닙니다.
선행 [준비 기록](runs/2026-09-14-native-sync-preparation-admission.md)과 `7ba8959`의
별도 구현 승인 이후 작업했습니다. 사용자 퇴근으로 중지했다가 “이어서 진행하자”로 재개했습니다.

변경 source는 `schedule_flow_experiment_cli.py` 한 파일, 테스트는 신규
`test_schedule_flow_parked_admission.py`뿐입니다. 공용 `hardware_guard.py`, 다른 CLI,
장비 writer, journal/archive 구현·모델, local-control, protocol schema 입력은 변경하지 않습니다.
기존 source 동결이나 실기 권한을 범위 밖으로 확대하지 않습니다.

## 판정과 적용 범위

기존 공용 guard는 exact-restore journal의 존재 자체를 차단합니다. Sync 전용 guard는
기존 전역 operation lease를 잡은 뒤 실제 journal reader와
`ExactRestoreReceiptArchive.confirm_parked_tombstone`을 호출합니다.

- 파일 부재는 기존과 같이 통과합니다.
- 존재하면 canonical tombstone과 immutable finalization·disposition·원 journal snapshot의
  결속을 확인합니다. 확인 뒤 journal을 다시 읽어 동일함을 검사합니다.
- nonterminal·형식 불량·누락·변조·경로 불일치·안전하지 않은 파일은 계속 차단합니다.
- phase 문자열만으로 통과하거나 다른 root·면제 callback을 넣지 않습니다.
- journal과 영구 보관 자료를 삭제·이동·수정하지 않습니다. lock 파일은 기존 lease가 사용합니다.

guard 선택은 native intent lease 안에서 보존된 `schedule_flow_spec.is_fixed_sync_plan`을
읽어 결정합니다. 현재 fixed Sync와 intent 부재 상태의 현재 CLI 준비/status만 새 guard를
사용합니다. 기존 Async spec 또는 flow spec 없는 legacy intent는 **공용 guard를 유지**합니다.
과거 operation의 복구 권한이나 Async 동작을 새 승인으로 확장하지 않습니다.
intent가 없다는 것만으로 run·recover가 승인되는 것은 아니며 기존 각 명령 검사는 그대로입니다.
따라서 status도 의도적으로 비대칭입니다. 부재/Sync intent의 검증된 park는 status까지
통과하지만 legacy intent의 status/recover는 이전처럼 BUSY입니다. `_fixed_spec`이 Sync
서명을 유지한다는 전제는 실제 생성 spec을 받아 guard 종류를 단언하는 selector 회귀가 지킵니다.

기본 archive 생성자는 이미 고정 hardware-safety root만 사용하며 root 변경을 자체 검사합니다.
별도의 중복 root 검사를 추가하지 않고 기존 거부를 회귀 테스트로 확인했습니다.
guard에서 받은 descriptor와 reader/archive가 여는 descriptor는 **서로 다릅니다**.
reader/archive는 각각 자기 descriptor를 고정 root에 대조하고, guard는 자신의 descriptor를
진입 시와 park 확인 뒤에 재검증합니다. 같은 descriptor로 모든 내용을 읽거나 하나의 원자적
파일시스템 snapshot을 확보했다고 주장하지 않습니다. 전역 lease와 기존 안전한 파일 접근
규율을 유지하며, 확인 중 root 교체는 회귀에서 거부됐습니다.

이 진입 허용은 qualification 성공도, parked exact-restore operation의 재실행 허가도 아닙니다.
실제 `ExactRestoreController`의 생성·실행·최종화 거부가 admission 이후에도 유지됨을 확인합니다.

## 보존 진단

검증된 park로 진입할 때 stderr에 JSON 한 줄을 명시적으로 flush합니다.
이벤트 이름은 `schedule_flow_verified_park_admission`, `event_version`은1입니다.
필드 집합은 `event`, `event_version`, `message`, `disposition_sha256`, `operation_sha256`,
`journal_snapshot_sha256`, `qualified`입니다. message는 한국어이며 `qualified=false`는
확인된 park가 자격 취득이 아니라는 뜻입니다.

`operation_sha256`은 **tombstone의 정의인 접두어 없는 SHA-256(ASCII operation_id)**입니다.
archive finalization의 같은 이름 필드는 도메인 접두어를 포함하므로 서로 바꿔 쓰지 않습니다.
disposition과 snapshot도 검증된 자료의 digest이며 원 식별자·경로·장비 비밀값은 내보내지 않습니다.
향후 실기에서는 이 stderr를 보존하고 세 해시를 run record에 결속하여, 빈 safety root에서
수행한 것으로 오독되지 않게 합니다. stderr는 보존했을 때의 진단 증거이며 장비 write나
qualification 성공을 증명하지 않습니다.

## 격리 검증 과정

운영 장비·브로커·production journal/receipt volume을 마운트하지 않았습니다.
기존 테스트용 image에 별도 임시 dependency directory를 제공했습니다. 실제 pytest는
network-none·읽기 전용 source/dependency mount·임시 빈 hardware-safety filesystem에서
일반 사용자 권한으로 실행했습니다. 이 빈 시험 root는 운영 volume이 아닙니다.
Python3.12, pytest8.4.2, pytest-asyncio0.26.0, Ruff0.16.7을 사용했습니다.

| 회차 | 결과 | 처리 |
|---|---|---|
| 중지 전 | pytest 없음, 수집 전 종료 | 미검증으로 중지 기록 보존 |
| 재개 v1 | source mount 접근 권한 오류, 수집 전 종료 | 시험 사용자를 mount 소유 uid에 맞춤 |
| v2 | 117 PASS / 2 FAIL, 1.92초 | 기존 CLI 두 테스트가 image 내 safety directory 권한에서 실패; 빈 시험 tmpfs로 격리 |
| v3 | 172 PASS / 1 FAIL, 6.49초 | 새 replay 테스트가 PREPARED가 아닌 기록을 create에 전달; 실제 기존 prepare 함수로 입력 수정 |
| v4 | 178 PASS / 2 FAIL, 6.35초 | 새 진단의 operation 해시 namespace 혼동 수정; Async 회귀 spec의 기존 300초 계약을 올바르게 구성 |
| v5 | 182 PASS, 6.46초 | 기존 guard·CLI·disposition과 신규 admission 회귀 통과 |

중간 실패를 장비 원인이나 펌웨어 동작으로 해석하지 않습니다. 특히 v4 진단 결속 오류는
새 source의 실제 오류였고 tombstone 해시와 직접 비교하는 단언이 잡았습니다.
기존 테스트 파일/단언은 바꾸지 않았으며 신규 테스트는29개입니다. firmware 가설 fake 없이
기존 실제 파일 fixture로 archive와 journal 검증을 실행했습니다.

신규 회귀는 정상/부재, nonterminal, backing 파일 누락·손상, phase/digest/형식/권한/
hardlink/symlink, 전역 lease 경합·긴급중단, 확인 중 journal/root 교체, 경로·archive root 불일치,
재실행 거부, dispatch 실제 연결, guard 선택 시 intent lease 보유, legacy status/recover의
기존 barrier 유지, 진단의 해시 결속 및 원본 불변을 확인합니다.

v5 뒤 신규 테스트 파일만 formatter로 정리했고 Ruff는 변경 Python 두 파일을 명시해 통과했습니다.
전체 suite 첫 실행은 `2571 PASS / 45 FAIL / 24 ERROR`, 89.96초였습니다.
이때 시험 묶음이 src/tests/pyproject뿐이어서 예시 설정·컨테이너 구성·UI 파일이 누락됐습니다.
오류 줄 전수는 그 누락 파일의 FileNotFoundError였으며 시험 포장의 잘못입니다.
개별 failure/error block69건도 각각 분류하여 누락 입력69·그 외0을 확인하고 보존했습니다.
source/test 수정 없이 `7ba8959`의 전체 추적 파일을 새 빈 작업영역에 풀고 변경 두 파일만
덮어쓴 입력으로 바로잡았습니다. 이전 Python 캐시도 가져오지 않았습니다.
정정된 입력의 전체 실행은 **2640 PASS / 95.39초**, exit0입니다. 기존2611+신규29로 닫힙니다.
전체 실행은 입력 누락1회와 입력 정정1회, 총2회이며 동일 조건을 반복해 green을 얻은 것이 아닙니다.
향후 전체 검증의 입력은 pin된 commit의 전체 git archive와 명시적 변경 파일로 구성합니다.
이 구성만으로 git 실행 파일 부재나 디스크 공간 부족까지 예방한다고 주장하지 않습니다.

## 검증 artifact

논리 묶음 `parked-admission-offline-20260914`, 각 파일은 private 시험 작업영역에 보존했고
최종 로그는 로컬 gitignored 자료 영역에도 복사해 digest를 확인했습니다.
이것은 오프라인 코드 테스트 산출물이며 장비 증거 등급(a)/(b)나 실기 PASS가 아닙니다.

| 파일 | SHA-256 |
|---|---|
| targeted-v1.log | `be90e09cb5b875db4a130d183e3f569b9e37bf5d6f13426a252ce137a00f7d8d` |
| targeted-v2.log | `26cca9d453bd6ec2c5a93cefd31660b56a3820065d90ea7056d0102fb39a5fd9` |
| targeted-v3.log | `e10c3985dfc4c4f41608023c77e08478079fe5ce377d7f595ac0bf3e749be94d` |
| targeted-v4.log | `1ec7e8b8ea414d20cfb553f28ceea733357d1256b203193fafc26105b351526f` |
| targeted-v5.log | `a05e253e62e6d1b9e8cccde2b8ad2e3600ab8b29a58a15347421af637f907b38` |
| ruff-v5.log | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| full-v1.log (입력 누락) | `43591dd41b1f64d4e0ece9f488e49672d13617aff8b10e45d3a70f2bc84b1c9b` |
| full-v1-classification.txt | `497134d25ab0662dd4e77df97e2376bc571de8f127be1b02509b467e62e2a6c6` |
| full-v2.log (전체 입력) | `4c7044ebe481d8831b2b8bb983a4a6c2492d9fc66b64c618d8939e584998b380` |

Ruff 성공 출력은 실행 간 byte-identical일 수 있어 이 digest만으로 실행을 식별하지 않습니다.
검사 대상은 위 source/test 두 파일이며, 새 진단 field와 guard 선택을 포함한 snapshot입니다.
전체 검증한 두 변경 파일과 현재 작업트리의 SHA-256이 각각 일치함도 확인했습니다.

- CLI: `0fcdecb7e83fd07c071deb8a6e9d6fca1e82284b2dc82d586848a3f11883e1cb`
- 신규 테스트: `fa32533a8bf74248a6f1fb1626083528cef6ea35494f314a40c7a55a435123f4`

## 실기와 다음 경계

이번 구현·격리 검증 중 device write·앱 조작·배포는0입니다. 운영 서비스는 이 작업으로
변경하지 않았으며 현재 물리 상태를 새로 확인했다는 주장도 하지 않습니다.
두 Pro의 기존432-byte 시간표와 앱 이전 control 보존은 선행 준비 기록을 따릅니다.
first TimerOFF 시간표를 누락하거나 second inactive manual30을 옛31로 바꾸지 않습니다.

수정·검증 결과를 커밋하면 `7ba8959`의 한정 구현 예외가 소진·재동결됩니다.
이 결과는 새 실기 승인·현장 감시·현재 identity와 baseline 확인을 대신하지 않습니다.
후속 준비는 새 source/image 결속, 원 서비스 exact 복귀 경로, 진단 보존과 현재 조건 재확인입니다.
실제 Sync 시험은 앱 준비와 기존 단회 승인 조건이 성립한 뒤에만 진행하고 원복 전에 완료로
보고하지 않습니다. Sync/Async 파형별 Flow의 실제 적용 여부는 아직 미확정입니다.
