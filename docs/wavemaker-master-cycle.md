# Pro 일일 시간표와 master 링크 cycle

사용자 앱 절차의 근거는 [직접 관찰 기록](runs/2026-09-15-async-master-link-app-rearm.md)이다.
이 문서는 구현 계약이며 새 코드의 실장 수용이나 배포 완료 주장이 아니다.

`attach-wavemaker --master-cycle-on-async-failure`는 마지막 Async 시도 뒤 미확인일 때만
선택적 fallback을 수행한다. 기본 staged9, 기존 context, 원복6/지원5는 그대로다.
취소는 cycle을 시작하지 않는다. 시작한 cycle에서의 취소도 자동 원복/재전송을 하지 않는다.

- 별도 결정적 `wm_cycle_` 요청/kind가 자기 source context·보류 snapshot·원본을 소유한다.
- master Linkage independent→master 두 scalar만 첫 cycle 전송 전 전량 내구화한다.
  현재 scalar 자격/owned payload 바이트 동일성을 확인한다. slave Async 전송은 추가하지 않는다.
- master 해제 후 새 reply-only pair로 slave Async·TimerON/ON·전체 432B를 확인한다.
  master 재설정 후 pair는 report 수용이며 frame 출처와 전체 image·명시 역할을 보존한다.
  시각/Auto/Mode를 불변값 게이트로 사용하지 않는다.
- cycle 성공은 상태 도달 증거다. source 마지막 action·ACK·진단·UNCERTAIN은 바꾸지 않는다.
  공개 cycle 결과의 `original_async_delivery`와 `source_phase`는 그 차이를 유지한다.
- 실패는 같은 요청으로도 attempted 명령을 반복하지 않는다. 완료 replay는 장비 무접촉이다.
  `recover-wavemaker`/기존 scalar assist는 정확히 자기 cycle id만 함께 허용하고 원본을 복원한다.
  해제 뒤 pair 확인이 실패하면 master는 independent인 채 재설정 전 정지할 수 있다.
  이 경우 Async 운영 완료라고 보고하지 않는다.
  원복 뒤 source/cycle ROLLED_BACK은 복구 회계이며 이전 수용/전달 증거를 소급 수정하지 않는다.
- source 미완료 동안 기존 전역 write 차단은 유지된다. cycle 성공으로 이를 우회하지 않는다.

## 사용자 일일 시간표

[예시 JSON](../config/examples/pro-pair-daily-async.json)은 upper/lower 논리 이름만 갖는다.
현장 private identity 대조 뒤 위 Pro를 master, 아래 Pro를 Async slave로 배치한다.
16구간이 00:00–24:00을 덮고 나머지 32개 슬롯은 EE 미사용이다. 각 image는 432B다.
slave는 같은 모드/시각/주파수에서 지정된 Flow만 다르다. 15:00–15:10 선택지는 Cross-flow
(`circulation`)로 정했다. 주파수 미지정 구간은 비활성 저장값0이며 현재 Auto 값을 고정하지 않는다.
Bar 설정은 이 요청에서 제외됐으며 변경하지 않는다.

기존 관찰 보류 source를 자기 원본으로 복원한 뒤 새 역할 배치의 baseline을 포착한다.
설정 완료 후 사용자 시간표를 유지하며 시간 제한으로 자동 원복하지 않는다.
별도 24시간 read-only collector가 30초 간격으로 device time/Auto/역할/image/raw wire와
reply/report 출처를 private 영속 볼륨에 기록한다. 읽기 실패도 기록하고 corrective write는 없다.
보고된 Flow와 실제 물리 유량은 다르며, 경계에서의 일치를 아침에 보존 자료로 판정한다.

## 검증 — 전체 PASS 아님

초기 신규 시험은15 PASS/1 FAIL이었다. 실패는 final의 `previous` 한 단계가 누적 master 행인데
그것을 release pair로 오인한 시험 오류였다. 체인을 더 따라 실제 release pair를 대조하도록
수정했다. source/test 형식 검사 오류도 정리했다. targeted5파일118 PASS 후 실패·지원2건을
추가한 cycle 전용9건 모두 PASS. 기존 tests 변경/삭제는0이다.

최종 source와 추가9건을 포함한 전체 실행1회: **2928 PASS / 5 FAIL / 경고2** (101.22초).
다섯 실패 이름은 아래와 같고 이전 이름 일치로 원인 동일성을 주장하지 않는다.

- `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults`
- `test_concurrent_same_receipt_contenders_converge_to_same_exact_success`
- `test_concurrent_conflicting_receipts_have_exactly_one_winner`
- `test_missing_receipt_and_unsafe_output_metadata_fail_closed`
- `test_private_directory_mode_is_required`

writer/schema9개 불변, schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`.
이것은 기존40 receipt의 currency 유지이지 새 순서의 실장 수용 증명이 아니다.

정적 검토는 소유권·source 무수정·재전송 경로를 대조했고 차단 결함0이었다. 빌드v1은 FROM에
로컬 image id를 직접 써서 registry 이름으로 해석된 오류로 종료했다. 장비 접근 없이 기존
이미지의 로컬 tag를 만들고 id 동일성을 대조하는 것으로 수정한다. 첫 tar 전달의 macOS xattr
경고도 있어 최종 build context는 커밋의 git archive로 새로 고정한다.
