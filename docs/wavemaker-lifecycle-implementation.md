# Pro 결속·해제 실행기 구현

## 완료 범위

정책 `6ed4059`에 따라 **실제 전송 API와 CLI까지 연결했다.** 외부 JSON으로 Sync/Async를
선택하고 master의 전체 시간표·파형·부가 설정을 상속하며 slave마다 활성 슬롯 Flow만 지정한다.
자기 결속 이전 원본을 저장하고, 해제·시간표 복원·8개 제어값 복원·최종 반환값 대조까지 실행한다.

이는 소프트웨어 실행기 구현 완료다. **새 장비 명령 수용, 실장 qualification, 이미지 빌드,
운영 배포, HA 편집 UI 완료를 의미하지 않는다.** Bar native와 미확정 Timer 명령은 지원하지 않는다.
이전 first Pro 앱 OFF·운영 서비스 복귀 의무도 이번 작업으로 닫지 않았다.

## 사용 표면

기존 private 설정·manifest·역할 attestation 옵션을 그대로 사용한다. 아래는 명령 형태이며
실행 기록이 아니다. 설정 경로는 운영자가 사용하는 기존 값을 지정한다.

```text
jebao-flow-local plan-wavemaker --native --file config/examples/wavemaker-pro-native.json
jebao-flow-local attach-wavemaker --file <계획.json> --request-id <새결속id> --qualifications <현재자격.json> --config <설정> --manifest <manifest> --role-attested-by <운영자> --write-enabled
jebao-flow-local detach-wavemaker --attachment-id <결속id> --config <설정> --manifest <manifest> --role-attested-by <운영자> --write-enabled
jebao-flow-local recover-wavemaker --request-id <결속또는해제id> --config <설정> --manifest <manifest> --role-attested-by <운영자> --write-enabled
jebao-flow-local wavemaker-status --request-id <결속또는해제id>
```

`mode`는 `sync`/`async`이며 `preparation_flow`/`preparation_frequency`는 두 장비의 중간 수동
준비값이다(제품 기본 30/20, 과거 실기 승인 승계 아님). 전송에는 기존 CONTROL·dry-run 해제·
장비별 write 허용·고정 안전 root·현재 신원·자격 검사가 모두 필요하다.
예시의 10:00/15:00은 **하루 절대 시각**이지 실행 후 10분/15분이 아니다.
해당 실행 이미지를 준비하면 계획 값만 바꾸는 데 코드 재빌드는 필요 없다.

`accepted=true`는 그 journal 작업의 최종 대조가 완료됐다는 뜻이다. 같은 요청 재호출은
저장 결과만 반환하므로 `current_state_verified=false`다. `wavemaker-status`도 현재 장비를 읽지
않는다. 실패 JSON은 단계별 기대값/실측값, 보존 증거 digest, `next_command`를 제공한다.
예외 메시지·장비 주소·원본 프레임은 공개 출력하지 않는다.

## 내구성과 순서

기존 local-control journal/blob과 배포·장비 lease를 그대로 사용한다. 새 안전 root는 없다.
원본 reply의 전체 status·wire frame·action·UTC 읽기 구간을 private blob으로 보존한다.
context는 plan·schema·장비·원본에 결속되고 journal 교체로 변경할 수 없다.

| 단계 | 결속 | 해제 |
|---|---|---|
| 원본 | 첫 write 전 independent 자기 원본 전량 저장 | 자기 attach context만 사용, 외부 baseline 입력 없음 |
| 준비 | slave 다음 master, 한 frame의 독립·TimerOFF·ON·constant·준비 Flow/Frequency | 같은 순서의 atomic 해제 |
| 시간표 | master 계획, slave Flow만 치환한 전체 432B | 장비별 원본 전체 432B |
| 역할/제어 | master→Sync slave, Async면 이어 Async slave | slave→master 원본 8개 제어값 |
| 최종 확인 | 전체 image·전원·Timer·역할 | 전체 image·원본 8개 제어값, 별도 reply-only 읽기 |

각 작업의 모든 payload와 원본은 첫 전송보다 먼저 fsync된다. 전송 전 attempting을 저장하고
ACK·반환 frame·필드 비교를 뒤에 기록한다. 시간표 전후와 최종 원복은 reply-only다.
역할·제어 단계만 report 수용을 명시한다. 각 image 직전 independent·TimerOFF를 다시 확인하며,
조건 불일치면 image를 보내지 않는다. 기대값을 실제 반환값으로 대신 출판하지 않는다.

준비 중 급식·custom 값은 바꾸지 않는다. 초기 FeedTime=0 준비는 encoder의 1..60 범위에서
거부됐다. 조건을 완화하지 않고 불필요한 준비 필드를 제거했다. 최종 원복은 다른 문제다:
장비가 보고한 **inactive FeedTime=0**도 원본이므로 앱 입력 최솟값으로 정규화하지 않는다.
알려진 8개 control flags만 선택하고 원 bytes를 복사한다. 원본 소유권·알려진 모드·출력/부가값
범위는 첫 write 이전에 검사하며 임의 새 값 입력은 받지 않는다.
원 TimerON 장비는 OFF→원 TimerON 전환과 중간 수동 운전을 거친다. 원래 OFF여도 중간에는
ON이다. 무출력 해제나 앱 Timer bool 단독 명령과 같다고 부르지 않는다.

## 실패·재시작·소유권

해제 id는 `wm_detach_`와 attach id SHA-256으로 결정된다. 미해제 attach는 VERIFIED여도
원본 소유자로 남아 같은 장비의 새 attach를 막는다. 완료한 이전 detach를 재호출해도 새 결속에
옛 원본을 다시 쓰지 않는다. 성공한 attach 기록은 undo로 재실행하거나 변경하지 않는다.

generic reverse `recover`는 native 기록을 전용 복구로 안내한다. 손상된 context로 detach id를
계산할 수 없어도 안내는 남고, CLI main에서도 전용 JSON이 출력된다.
기존 power-off의 `restore_on_recovery=False` 제한은 그대로다. 새 필드 세 개는 None이면
직렬화에서 생략하므로 legacy record bytes·digest·복구 의미를 바꾸지 않는다.

attach 실패는 자기 원본 해제를 시도하고 취소도 원복 task 종료 후 다시 전달한다. 복원 성공과
결속 실패는 별개다. 해제 실패는 RECOVERY_REQUIRED로 남는다. 명시 복구에서 PREPARED는
진행할 수 있지만 attempting/ACK/uncertain은 **재전송하지 않는다**. fresh 값이 목표와 같으면
확인만으로 전진하고 다르면 멈춘다. 모든 해제가 끝나고 최종 reply만 미확인이면 후속 복구는
읽기만 한다. timeout 증가·자동 재결속·초기화는 없다.

단회 만료 예외는 제품 실행기에 추가하지 않았다. attach는 현재 schema·내용·firmware·물리
신원 qualification을 요구한다. 자기 원복은 context의 복원 권한과 동일 schema를 사용한다.
새 계획 실행 권한으로 확대하지 않으며 다른 schema로 옛 원본을 복원하지 않는다.

## 관측 근거와 미검증 범위

보존 series `JFS-1c20ba9f37756bbcd6efc2c0261dd393`의 사후 대조 분석물 SHA는
`2285753695fe47084a97c8aeb978f987395f295d363d5721e4ff6689890ba620`이다.
로컬 보존 네 표본(master2/slave2)에서 SwitchON=true·TimerON=true를 확인했다.
이는 [앞선 원복 기록](runs/2026-09-15-native-sync-master-template-flow-only.md)의 구조화 분석값
재확인이지 이번 raw 재취득이 아니다. 새 atomic 역할 명령의 수용을 보장하지 않는다.
리뷰어는 그 private 파일을 독립 확인하지 않았으며 이 관측은 구현 담당자의 확인이다.

해제 직후 reply의 성립 시점과 새 atomic 명령의 실장 수용은 첫 실기까지 미검증이다.
과거 native disarm 근본 원인을 고쳤다고 주장하지 않는다. 앱 전체 동등성·물리 위상·파형
소유권도 이 소프트웨어 시험의 판정 대상이 아니다.

## 검증 기록

초기 21 FAIL/5 PASS는 FeedTime=0 준비 명령의 기존 encoder 거부였다. 정정 후 25 PASS/1 FAIL은
시험 role 목록에 image 단계 None을 포함한 집계 오류였으며 역할 write만 선택하도록 정정했다.
production 원복·재전송 규칙을 낮추지 않았다. 이후 관련 138 PASS, 전체 제어값 불일치·CLI·
재시작 검사를 더한 155 PASS를 얻었다. Ruff의 import/포맷·미사용 import 지적도 정정했다.

검사는 production API와 writer를 사용하되 메모리 transport에서 **선택된 raw datapoint 복사**와
ACK 손실·취소·읽기 불일치만 주입한다. firmware 연동·해제 수용 가설을 증명하지 않는다.
편집 도구의 문맥 불일치는 적용 없이 거부됐고 다시 정확한 문맥으로 적용했다.
최종 전체 검사와 artifact 결속은 다음과 같다.

### 최종 트리 검사

최종 source/test 트리에서 전체 pytest를 **한 번** 실행했다. 결과는 **2841 PASS / 5 FAIL /
2 warnings, 59.98초**다. 전체 PASS가 아니다. 5개는 1차 구현 시 변경 전 `cf18e2f` archive에서도
같은 macOS·Python·umask 조건으로 재현해 보존한 시험들이다. 이번 로그에서도 각각의 서명이 같다.

| 실패 시험 | 이번에도 남은 현상 |
|---|---|
| `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults` | macOS에 Linux boot-id 파일 없음 |
| `test_concurrent_same_receipt_contenders_converge_to_same_exact_success` | receipt lock FileNotFoundError·한 thread 결과 누락 |
| `test_concurrent_conflicting_receipts_have_exactly_one_winner` | 같은 lock 오류·한 thread 결과 누락 |
| `test_missing_receipt_and_unsafe_output_metadata_fail_closed` | private directory 검사에서 DID NOT RAISE |
| `test_private_directory_mode_is_required` | private directory 검사에서 DID NOT RAISE |

두 권한 시험의 0755 요청은 실행 umask 077로 0700이 된다는 앞선 대조와 일치한다.
두 동시성 실패의 근본 원인은 미확정이며 OS 탓으로 단정하지 않는다. 실패를 지우거나 테스트·
조건을 낮추거나 전체 검사를 초록불까지 반복하지 않았다. [앞선 환경 대조](wavemaker-product-implementation.md)를
그대로 보존한다. 신규 lifecycle 시험은 43개이며 기존 원복 순서 시험 1개는 atomic 단계 계약으로
갱신했다. 그 시험의 TimerOFF·독립·SwitchON·전체 복원 단언은 유지하고 Mode·Flow·Frequency를 추가했다.

Ruff PASS, diff check PASS. 이 전체 검사 이후 source/test 변경은 없다.
schema는 `704cff6d7bf15ce166acaa990ec83546c8830c51237a3c7e6b42942df2f64821`에서
`b487319e6479772c120e9c1f48feae918d3e223880d364f4fa64c28251360296`로 변경됐다.
fingerprint 입력 중 변경은 writer 하나이며 기존 자격을 새 내용의 자격으로 쓰지 않는다.

### 보존 산출물

gitignored 논리 경로 `data/wavemaker-lifecycle.r5bQjQ/`에 로그를 보존한다.

| 파일 | SHA-256 |
|---|---|
| `lifecycle-v1.log` | `8af7cb9da8874bbe6e72a8125d6f4b0b645ff2dbd5aac16d825206cb5e38c838` |
| `lifecycle-v2.log` | `517689e8e403aaf40238c8b9dd079a1e811bb51c43473bf9023ace06edffb31a` |
| `lifecycle-targeted-v3.log` | `5b8fd156562a261943aae127b82fe6ede4c085c4a61fa422f2d71503c71c6b4a` |
| `lifecycle-targeted-v4.log` | `49dc9f643cf864e2d4df2968471a44dd23fef3a43614edc11d011bfb9e1f52b3` |
| `lifecycle-full-v1.log` | `8579e147f8786aa0bd47631931f839eb80fc7f5c49eab03a0a642fdc64d34799` |
| `ruff-final.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |

검사 대상 source/test SHA-256:

| 파일 | SHA-256 |
|---|---|
| `local_control/journal.py` | `0a680bb4420bc12db2ef3143bda53778865100f8f1731d72650d4fd14e0e09d6` |
| `local_control/coordinator.py` | `c94c626640e6c46941422c3bb548133fabac5d113ba30dc179233e9ab63b191e` |
| `local_control/device_writer.py` | `84c26fa712e45ee0e20331f8889377a085216473390c3a74848b52176d81d755` |
| `local_control/wavemaker.py` | `6bd06273f03f62ff697f54fc7f333567a27a3c5f96300cd987eebfa16f7c9f09` |
| `local_control/wavemaker_restore.py` | `a031bee9b13ba814a697488709f195fec2062fbb9e72d3c534f46dc07103cb62` |
| `local_control/wavemaker_lifecycle.py` | `51f822407fd05d5c1a3043c05a096055f7c3d540ebf9d5d5ab2e6697513f609d` |
| `local_control_cli.py` | `c9b634e664d0513b3caac7f2e4806e525db4680dd6352061c004f4aaa3cae492` |
| `tests/unit/test_local_control_wavemaker_lifecycle.py` | `709b9fdea39478553fc9b45713dcf11c7334fb9b4a83fdfdca34878b0feff3c4` |
| `tests/unit/test_local_control_wavemaker_restore.py` | `0e305b18564f20e5d5572ba12c02a3229450251ded59c5205649ef7098b00989` |

## 독립 검토와 완료 경계

Claude의 정적 검토에서 자기 원본·전용 복구·no-resend·fsync·최종 대조 경로의 차단 결함은 없었다.
손상 context 안내와 CLI main의 안내 삼킴을 보완했고 원자 명령의 장비 수용은 미검증으로 남겼다.
리뷰어가 pytest를 재실행했다거나 private 분석물을 독립 검증했다고 인용하지 않는다.
완료 커밋으로 `6ed4059`의 실행기 구현 범위를 소진한다. frozen native·protocol·공용 guard·
기존 HA/MQTT는 변경하지 않았고, 장비·앱·운영 서비스·배포 접근은 0이다.
