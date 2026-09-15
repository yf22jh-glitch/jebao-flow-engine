# Pro 단계별 활성화: 성공했던 정상 경로와의 대조

이 문서는 제품 누적4회 기록 뒤 사용자의 재개 지시에 따른 소프트웨어 변경이다.
**새 장비 수용·원복·운영 배포 완료를 뜻하지 않는다.** 앞선 하네스의 성공은 입력/응답
일치였고 정상 종료는 아니었다. 기존 종료 실패와 전달 UNKNOWN을 소급 변경하지 않는다.

근거 기록:
[하네스 Async 입력/응답](runs/2026-09-15-native-scenario-async.md),
[제품 role-only 정지](runs/2026-09-15-product-async-role-only-stop.md),
[그 원복](runs/2026-09-15-product-async-role-only-restored.md),
[연결 유지·직접reply 확인 실행](runs/2026-09-15-product-async-reply-reconciliation.md).

## 확인한 차이와 변경

`a4fcd98`의 `schedule_flow_experiment._stage_devices`·`_arm_temporary_schedule`,
`schedule_linkage._link_device`·`_prepare_fixed_q2_slave_sequence`, `lan._apply_changes`를
함수 끝까지 대조했다. frozen 하네스를 실행하거나 import하지 않는다.

| 순서 | 기존 성공 하네스의 정상 경로 | 신규 제품 context |
|---|---|---|
| 1–2 | master/slave 각각 independent·TimerOFF·constant·명시 Flow/Frequency | 같은 순서의 두 준비 action |
| 3–4 | 각자 시험 image 설치 | 기존 full432B writer·직전 reply/독립·TimerOFF 검증 |
| 5–6 | independent에서 ON·TimerON·constant·준비 Flow/Frequency | 역할을 섞지 않은 두 Timer action |
| 7 | Linkage=master 단독 | 같은 단일 필드 |
| 8 | Linkage=sync_slave 단독 | 같은 단일 필드 |
| 9(Async) | Linkage=async_slave 단독 | 같은 단일 필드 |

종전 제품은 5–6을 역할 쓰기와 합쳤다. 이번은 Sync8/Async9개를 첫 send 이전에 기존 journal로
모두 내구화한다. 단순히 마지막 Async payload만 같게 만든 것이 아니다.

역할 후 양쪽 연결을 닫고, 양쪽 인증 완료를 기다린 뒤 pair를 읽는다. 다음 역할은 그 pair read를
한 연결에서 쓴다. 준비 proof는 새 pair 연결에서 대조하며, image 전에는 별도 reply-only pair를
읽는다. TimerON 뒤 pair proof·Auto 관측·역할 preflight·1초 settle·첫 역할 전 fresh pair를 둔다.
Auto/시각을 고정 expected로 만들지 않는다. 최종 attach는 full image와 명시 ON/Timer/역할을 본다.

첫 master 전·master 후·최종 Sync/Async 뒤의 pair는 직접reply-only다. 단, Async 경로의 Sync
중간 proof는 old `_prepare_fixed_q2_slave_sequence`가 report-capable capture를 사용하므로
그대로 reply/report를 받는다. 이를 전부 report 또는 전부 reply-only로 평탄화하지 않는다.
TimerON 두 action의 직전 읽기는 기존 연결에서 각각 갱신해 다른 장비 쓰기 전 capture를 재사용하지 않는다.

`DeviceControlConfig`의 기존 기본인 장비별 send 간격1초, ACK 후0.5초/최대3회 읽기를 native
절차에 명시했다. 값 불일치는 같은 stream에서 다시 읽고, transport 오류는 quarantine/close 후
새 writer에서 남은 읽기만 한다. 제어 재전송은 없다. `ValueError`·취소를 retry하지 않는다.
성공 adapter와 달리 기존 writer가 실패 stream을 재사용하지 못하므로 객체 자체를 교체한다.

**ACK 오류 해소는 의도적으로 같게 만들지 않는다.** old의8회/55초 경로는 report도 수용한다.
제품은 기존대로 마지막 Async에서만 새 직접reply1회로 상태를 확인하고, ACKNone·전달UNKNOWN과
전용 진단을 남긴다. 그 reply가 없거나 불일치하면 자기 원복으로 간다. 읽기 대기 상한·공유
protocol·qualification의 실제 ACK 요구를 바꾸지 않는다. 따라서 old 전체의 완전 복제는 아니다.

옛 sentinel 자격 write, 날짜/Auto/같은 분 게이트,10/15분 epoch는 재실행하지 않는다. 선언된
현재 시간표와 옛 시험값·절대시각도 동일하다고 주장하지 않는다. 성공해도 어느 차이가 원인이었는지
확정하지 않는다. 목표는 승인된 정상 활성화 순서의 일치이지 펌웨어 원인의 추측 구현이 아니다.

## 소유권·증거·호환

실제 source는 `local_control/wavemaker.py`, `wavemaker_lifecycle.py`, `wavemaker_sequence.py`다.
새 `SequenceDeviceWriter`는 인증/관측 read만 확장한다. `send_once`와 모든 `prepare_*`는 기존
writer에서 그대로 상속하며 payload를 따로 만들지 않는다. `device_writer.py`·schema9개·40receipt·
공용 coordinator/guard·protocol·frozen native6개는 변경하지 않는다.

신규 Pro pair context만 `activation_sequence=staged_roles_v1`을 갖는다. None은 직렬화에서 생략되어
옛 context/plan 바이트와6/7 action 처리 경로를 유지한다. 새 실행은 위치 기반 순서에 맞춰 한 쌍으로
한정한다. 선택적인 `preparation_slave_flow`도 None이면 생략하며 기본 common 준비값은 변하지 않는다.
실기는 master30/slave31/F20을 명시할 수 있고 각 값은 기존 출력/자격 범위로 검증한다.

기존 child6단계와 assist5단계는 유지한다. 신규8/9 action의 지원 허용은 절차 버전·소유 원본·
모든 target/function/expected를 재대조한다. slave 역할 단계 또는 완료 action 뒤 최종 대조 실패만
추가로 받는다. 이전 실패 atomic을 다시 보내지 않으며 원본 외 상태나 재결속으로 rollback하지 않는다.
원본 image+8controls는 최종 fresh reply로 검증한다. App 이전 상태 복원과 이번 자기 baseline 복원은 별개다.
새 절차의 all-done 지원 분기는 ACK/특정 진단까지 제한하지 않는다. 전체 계획/소유 원본이 일치하면
최종 대조 실패를 수습할 수 있게 한 명시적 확대이며, 그 action의 전달 성공을 판정하는 분기가 아니다.

기존 read observer에서 선택/비선택 frame을 메모리에 수집하고 read 반환/오류 뒤 journal blob으로
보존한다. timeout 내부에서 fsync하지 않는다. read별256개 상한을 넘으면 누락 개수를 명시한다.
`frame_observed_at`은 host 수신 관측시각이지 장비 생성시각이 아니다. 진단 rows는 previous 체인으로
남으므로 최신 final만 읽지 않는다. Async 직전 pair 요약은 동일 capture를 재사용하며 추가 읽기가 아니다.
report를 관측했다는 것과 fresh reply를 얻었다는 것은 구분한다. 미선택 report로 ACK 실패를 PASS하지 않는다.
진단 sink 오류는 비밀 없는 stage·예외 클래스만 기록하는 best-effort이며 원 read 오류를 덮지 않는다.
필수 journal action/판정 capture 저장은 별개이며 실패를 무시하지 않는다.

## 소프트웨어 검증 기록

테스트는 byte-copy transport·내부 순서·ACK/read fault injection이다. 장비의 전파·위상·정규화·지연을
fake로 만들어 통과시키지 않는다. 실제 readback 간격 대기는 가상시계로만 빨리 실행한다.

- targeted v1: lifecycle57 PASS/3 FAIL. 연결 식별을 `id()`만 보존해 객체 주소 재사용이 섞인 시험,
  변경된 거부문 regex, 빠진 전환 직전 관측 요약을 확인하고 각각 수정했다. 원 로그는 보존했다.
- targeted v2:175 PASS/3 FAIL. 신규 시험의0.5초 계수에 명령 간격 잔여0.5초를 빠뜨렸고,
  연결하지 않은 payload 준비용 객체까지 close 대상으로 센 시험 두 건을 정정했다.
- 최종 targeted/full 및 정확한 source/diff 결속은 아래에 덧붙인다. 전체 PASS를 선취하지 않는다.

targeted v3은178 PASS였다. 이후 정적 대조에서 단계별 reply/report 구분·Timer 직전 capture·
진단 sink 실패 시 원 예외 보존을 보완했다. 따라서 v3 수치를 최종 트리의 결과로 인용하지 않는다.

targeted v4는180 PASS(44.11초)다. 그 뒤 경계 검사를 읽으며 생성자의 직접 `control_session`
import를 제거하고 writer 상속 기본값으로 바꿨다. 기존 경계 시험은 수정 없이13 PASS(0.77초)다.
최종 source/test 트리의 full1회는 **2910 PASS/5 FAIL/경고2(95.33초)**이며 전체 PASS가 아니다.
실패한 이름은 이전 full과 같은 다음5개다. 이름 일치로 근본 원인 동일성을 주장하지 않는다.

- `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults`
- `test_concurrent_same_receipt_contenders_converge_to_same_exact_success`
- `test_concurrent_conflicting_receipts_have_exactly_one_winner`
- `test_missing_receipt_and_unsafe_output_metadata_fail_closed`
- `test_private_directory_mode_is_required`

Ruff·diff check는 통과했다. 기존 lifecycle 시험 함수 삭제0, assert150→150이며 9개 함수의
단계/전송 개수·연결 식별·오류 안내 단언을 새8/9 계약에 맞췄다. ACK fault 주입은1..7에서1..9로
확장했고 원복 전체 image/8controls·no-resend·UNKNOWN 단언은 유지했다. 새 sequence 시험에는
7 action legacy 실행·None omit 바이트 roundtrip, paired auth barrier·실제 사용하는 연결,
read-only 재읽기·quarantine·close, 관측 sink 실패, 변동 Auto 비비교,8/9 소유 지원이 포함된다.

src/test diff SHA-256은 `4882f39c21ed53982237ecdecebf92ab6f2a6b50bc4ceb625ad1a63f018905e1`이다.
schema는 `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`로 직접 재계산
일치했다. 이 사실은 기존40receipt의 schema currency가 유지된다는 뜻이지 새 장비 수용 증명이 아니다.

이번 실기는 제품 누적5회차이며 신규 operation1회다. 먼저 실장 수용과 자기 원복을 확인하고,
그 뒤에만 기존6개 제어를 보존하는 새 운영 generation을 설치한다. 실패하면 자기 원복·증거·보고를
먼저 닫는다. 추가 여섯 번째 전진, 동일 payload 재전송, 앱 reset을 자동으로 포함하지 않는다.

## 후속 실기·독립 검토 보완

[제품5회차 결과](runs/2026-09-15-product-async-staged-activation.md)는 마지막Async ACK timeout,
새 직접 확인 창report4/reply0, 소유 원복exact다. 구현 검토 통과를 실장 수용으로 바꾸지 않는다.

독립 검토자는 최종src/tests 동일bytes에서Ruff와targeted2파일71 PASS(19.15초),
full1회2912 PASS/3 FAIL/경고2(96.86초)를 보고했다. 우리2910/5와 총계2915는 같으나,
권한/umask2개는 그 환경에서 재현되지 않았다. 어느 결과도 전체PASS가 아니며 리뷰어는 실기에 접근하지 않았다.
전체landing diff SHA는 `6e1ee237dd4fa15fb214e44df8ac1d89863fd4aac55a3e2359c27a217830896e`다.

삭제 검토 보완: assert150→150, pytest.raises32→32라도 부정 분기 커버리지가 모두 보존된 것은 아니다.
기존2개 부정 시험을staged 거부 기대값으로 옮겨, 옛source형상 거부와공용child첫slave외 거부의
해당 부정 분기를 직접 덮는 시험이 없어졌다. 제품 guard 자체는 유지됐고 새false PASS는 확인되지 않았다.
비차단 커버리지 손실로 기록하며 실기 결과 커밋 전에source/test를 수정해서 덮지 않는다.
