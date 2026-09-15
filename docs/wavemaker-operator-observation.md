# Async 전환 뒤 사용자 관찰 대기

사용자가 최대2분 후 자동 원복 제안 대신 “아니 걍 원복하지말고 기다리게 해”라고 명시했다.
`cd50c61`의 별도 정책에 따른 관찰 옵션이며, 일반 제품 attach의 실패 복구 정책은 바꾸지 않는다.
아직 실장 전환이나 배포 완료를 뜻하지 않는다.

`attach-wavemaker --await-operator-observation`은 Async 계획에서만 받는다. 옵션은 첫 전송 전에
request intent와 immutable native context에 들어가며, 미설정 시 기존 intent/context bytes는
그대로다. 시간 제한·자동 원복 타이머·반복 명령은 없다. 기존 staged9단계와 각 읽기/ACK 판정,
전체 image 및 명시 controls 최종 대조를 그대로 수행한다.

앞8단계가 끝나고 마지막 Async 명령을 시도한 뒤에는 ACK/read 오류·취소로 자동 detach하지 않는다.
실패한 action의 UNCERTAIN·ACK 부재·진단·증거는 유지한다. 성공 경로도 최종 대조를 저장하되
transaction은 곧장 RECOVERY_REQUIRED로 남긴다. pending은 성공 판정도 원복 완료도 아니다.
공개 결과의 `awaiting_operator_observation=true`, `observation_reason=operator_requested_no_automatic_restore`,
`automatic_restore=false`, `restoration_complete=false`, `next_action=await_operator_instruction`을
기존 action별 ACK/전달/비교와 함께 읽는다. 원 상태의 현재성을 증명하지 않는 재조회는
`current_state_verified=false`다. 기존 `native_stage_unverified`는 실제 실패 action에만 남기며
의도적 대기의 진단으로 쓰지 않는다.

원본 두432B와8controls·신원·자격은 기존 context/journal에 보존한다. 같은 attach id를 재실행하면
resolver/장비 읽기 이전에 보존 결과만 반환하며 제어를 다시 보내지 않는다. 옵션을 빼거나 다른
계획을 같은 id에 넣을 수 없다. status 역시 장비에 접근하지 않는다. 반면 명시적
`recover-wavemaker --request-id <attach-id>`는 읽기와 자기 원복을 실제로 실행하는 명령이다.
이 차이를 “복구 명령도 무접촉”으로 오독하지 않는다.

관찰 중 시험 시간표·TimerON·master와 미확인 slave 역할이 유지된다. 실제 출력은 그 시간표를
따르며 준비 manual 값으로 고정됐다는 뜻이 아니다. 미완료 journal 때문에 기존 공용 gate가
Bar를 포함한 다른 local-control write/자격 작업도 막는다. 사용자에게 이 점을 실행 전에 알린다.
앱의 관찰·조작 가능 여부는 그 gate와 별개다. 사용자 관찰 완료를 원복 지시로 바꾸지 않으며
후속 명시 요청 때만 기존 소유 detach/assist로 복원한다. 앞단계 오류나 Async 미전송이면
이 보류에 들어가지 않고 기존 자동 복구를 유지한다.

## 검증

첫 시험 명령은 작업트리에 .venv가 없어 exit127이었다. 설치하지 않고 직전 실행의 기존 Python
환경을 찾아 재사용했다. 신규8시험은 모두 통과했다. byte-copy transport에서 최종 ACK 유무,
미적용/취소 fault, 원본 보존, 후속 전진 차단, 무접촉 replay와 명시 원복을 검사하며 실제 장비의
지연·전파·파형을 모사하거나 이번 실기 성공을 선취하지 않는다. 최종 검증 수치는 아래에 추가한다.

source 범위는 lifecycle/CLI 두 파일뿐이다. writer/schema9개·공유 timeout/retry·40receipt와 frozen
하네스는 그대로다. 제품 누적6회차로 한 번 전환한 뒤 사용자 관찰을 기다리며, 배포·추가 전진·
자동 원복은 하지 않는다. 장비 상태 유지와 사용자 회신 대기는 운영 서비스 배포 완료가 아니다.

최종 신규9시험 PASS(2.26초), 기존 lifecycle/sequence/CLI를 포함한 targeted110 PASS(20.69초,
그 뒤 CLI 전달 시험1개 추가), Ruff 전체 PASS다. 최종 source/test의 full1회는2919 PASS /
5 FAIL / 경고2(104.47초)로 전체 통과가 아니다. 실패 이름은 직전5회차와 같은 아래5개다.
동일 이름을 근본 원인 동일성으로 주장하지 않고 테스트나 umask(077)를 바꿔 지우지 않았다.

- `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults`
- `test_concurrent_same_receipt_contenders_converge_to_same_exact_success`
- `test_concurrent_conflicting_receipts_have_exactly_one_winner`
- `test_missing_receipt_and_unsafe_output_metadata_fail_closed`
- `test_private_directory_mode_is_required`

schema 직접 재계산은`81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`로
불변이며 기존 자격 currency 유지이지 새 실장 수용 증명은 아니다. 기존 tests 수정0이고
source 삭제2행은 CLI attach 호출과 `_finish`의 transaction 저장을 관찰 옵션 분기로 치환한 것이다.

독립 리뷰는 같은 source에서 targeted80 PASS, full2922 PASS/2 FAIL/경고1(105.20초)을 보고했다.
총2924개는 우리2919/5와 같지만 receipt 동시성 시험의 결과도 환경/실행마다 달랐다. 따라서
이전 실패 이름 목록을 고정 원인 baseline으로 취급하지 않는다. 리뷰어는 실기를 실행하지 않았다.
소유 계획이 손상되면 관찰 술어/status도 예외로 멈출 수 있다. 이를 False로 숨겨 자동 원복 분기로
넘기지 않으며, 정상 보존물에 대한 무접촉 replay와 손상 시 보고 실패는 구분한다.

[관찰 실행](runs/2026-09-15-product-async-operator-observation.md)은 마지막 Async 미확인 뒤
사용자 지시대로 자동 원복 없이 남겼다. 최종 시도 뒤 취소/프로세스 종료도 자동 원복하지 않는다.
향후 원복 뒤 ROLLED_BACK은 복구 회계이며, 실제 수용 여부는 phase가 아닌 final/증거 chain을 본다.
