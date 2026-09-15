# 미완료 native 해제의 소유 scalar 지원

기준: `31dc0a0`, [제품 Sync 실제 실패](runs/2026-09-15-product-sync-detach-ack-timeout.md).
첫 실패는 값 대조가 아니라 ACK timeout이다. 현재 해제 명령의 적용 여부는 UNKNOWN이다.
사용자는 별도 복구와 배포까지 진행하도록 승인했다. 아직 새 명령을 실행한 기록은 아니다.

닫을 질문: **기존 실패를 지우거나 재전송하지 않고 감사된 별도 scalar 경로로 자기 원본을
복원할 수 있는가.** 소프트웨어 소유권/순서는 durable 시험, 실제 수용은 새 raw로 확인한다.

## 고정된 단회 계획

자기 attach id에서 지원 id를 결정한다. 기존 source·detach 원본과 지원 시작 전 실패 record를
내용 해시로 결속한다. 첫 slave 준비만 UNCERTAIN이고 나머지5개 PREPARED인 Pro pair만 대상이다.
임의 target·값·원본·단계 입력은 받지 않는다. 모든 payload/context는 첫 send 전에 fsync한다.

1. master: `linkage_role=independent` (Timer와 시간표는 이 단계에서 바꾸지 않는다).
2. slave: 기존 앱 계약 `schedule_enable=False`로 TimerOFF·SwitchON 및 slave 해제를 요청한다.
   명령 shape는 실제 준비 read의 역할에 따라 기존 writer가 정하며 원본 rollback은 자기 attach다.
3. slave: `manual_mode=constant`.
4. slave: 자기 계획의 `preparation_flow`.
5. slave: 자기 계획의 `preparation_frequency`.

이것은 실패한 6필드 atomic payload 재전송이 아니다. 새로운 function·제한된 payload이며
기존 실제 자격을 검사한다. 각 단계는 직전 실제값이 일치하면 NO_CHANGE, 시도 후 미확인은
재전송 없이 정지한다. slave가 준비 상태에 도달한다고 가정하지 않는다. master-first의 역할
해제와 slave의 TimerOFF 수동 운전은 실제 부수 효과이며 무출력이라고 부르지 않는다.
master만 먼저 independent인 중간 상태의 slave 거동은 미관측이며 예측하지 않는다.
지원 실패는 자동 rollback하지 않는다. 저장 rollback도 결속 중 상태가 아닌 자기 attach 전
원본에 결속한다. 지원 실패를 처리하기 위해 sync_slave를 재설정하거나 TimerON을 켜지 않는다.

## 기존 의무로 돌아가는 조건

별도 fresh 읽기에서 master independent 및 slave의 준비6필드가 확인돼야 지원 완료다.
그 뒤 같은 lease에서 기존 detach를 이어간다. 첫 UNCERTAIN은 기존 fresh-match 분기가 전송
없이 해소하고 나머지5개 단계가 원 image·원 controls를 복원한다. 지원 완료만으로 원복을
주장하지 않는다. 최종 reply의 전체432B·8controls 일치가 원복 완료 조건이다.

지원 record도 generic reverse recovery 대상이 아니다. 다른 미완료 작업은 여전히 거부한다.
재생은 저장된 결과와 현재 확인을 구분한다. 지원 중 실패하면 source/child/support와 실제
comparison·raw를 남기며 자동 새 id·재전송·다른 대안을 실행하지 않는다.

원 UNCERTAIN의 phase가 기존 기계상 VERIFIED가 되더라도 진단에 지원 경로로 목표가
충족됐음을 남기고 지원 id/증거에 결속한다. **원 timeout 전송의 도달 여부는 여전히 UNKNOWN**이다.
이는 전송 성공으로의 소급 변경이 아니라 현재 목표 상태 도달에 의한 복구 완료다.

Auto·clock은 원복 고정 목표가 아니다. 설정 image와 명령으로 변경한 필드만 검증한다.
writer·schema81ee699d·40영수증·일반 guard·timeout/retry는 불변이다. 이전 실패도 불변이다.
현재 snapshot 복원과 first 앱 이전 OFF·Bar classic91/F80 복귀는 별도 의무다.

## 검증 범위

기존 fake는 byte-copy + ACK/read fault injection만 사용한다. 펌웨어 역할 전파·앱 해제
성공 가설을 추가하지 않는다. 결정적 id·원본 결속·다른 미완료 차단·첫 write 전 내구화·
각 단계 no-resend·재생 무접촉·최종 전체 원복·CLI 자격 전달과 실패 출력이 대상이다.
실장 전 최종 source/image/schema를 결속하고 지원1회만 수행한다. 실패하면 새 실행 없이 보고한다.

## 소프트웨어 구현·검증 (2026-09-15)

정책 `8094cc4` 뒤 source2/test2를 변경했다. **실제 장비 복원·배포는 아직 완료가 아니다.**
`assist-wavemaker-detach --request-id <attach-id> --qualifications <current-file> --write-enabled`
진입점은 지원 뒤 같은 lease에서 기존 detach를 이어 실행한다. **exit0은 지원만의 성공이 아니라
child 최종 원복 완료**다. 지원 실패 출력에는 source/support/child 상태와 다음 명령을 함께 둔다.
설정·manifest·template·운영 mode 등 기존 설치 입력도 필요하며 observer/dry-run은 거부한다.

지원 id는 attach에서 결정되고 context에 지원 전 실패 record의 immutable blob을 결속한다.
새 context 필드는 None일 때 생략하여 기존 native context 직렬화 바이트를 보존한다.
다른 미완료 작업의 면제는 없고 자기 attach/child/assist 정확 세 id만 허용한다. 이후의 일반
recovery·power-retain·writer·transport·frozen native 파일은 변경하지 않았다.

초기 targeted v1은 74 PASS/3 FAIL이었다. 구현에서 datetime을 JSON mode 없이 hash한 오류2개와
시험 helper가 revision-zero PREPARED가 아닌 record를 create한 오류1개다. 두 부분을 정정했다.
Ruff 길이 오류1건도 정정했고, 빈 patch의 context 불일치1건은 파일 변경 없이 종료됐다.
v2는 77 PASS, CLI 자격 전달·write lock 회귀를 더한 v3는 **128 PASS**다.
기존 test 함수 제거0·AST 변경0이다. CLI 시험의 삭제4행은 포맷 정리이며 단언 제거가 아니다.

최종 source/test 트리의 전체 시험은 **2887 PASS / 5 FAIL / 경고2**다. 전체 PASS가 아니다.
실패 이름은 직전 전체 시험과 동일한 boot identity1, receipt 동시성2, umask 관련2다.
이름 일치로 근본 원인 동일성을 주장하지 않으며 재실행으로 이 실패를 씻지 않았다.
Ruff는 통과했다. reviewer는 소유권/재전송/출처 보존의 정적 경로를 검토했으며 pytest·실기는
재실행하지 않았다. 성공이 지원까지만 반환된다는 지적은 함수가 child 결과를 반환함을 확인하고
철회됐다. 최초 잘못된 시험/진단 로그를 보존한다.

| 보존 검사 로그 | SHA-256 |
|---|---|
| targeted v1 | `29aea050046943fe4808f128d3ba254ced49c68d791231cdf1440d1788de2508` |
| targeted v2 | `4c47c452ff98db4dc283a3c40ecd189bb734e8ac976793e8c16e5391f56f18bd` |
| targeted v3 | `fae692e4a58fc9ef213189261a592f29c9c1eab1716785b1f09e9e3b0300b4f2` |

schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`은 불변이며
발급된 40영수증을 수정하거나 다시 발급하지 않는다. 첫 실제 지원에 쓸 원본은 기존 attach가
포착한 두432B·8controls다. source 구현 커밋으로 이번 구현 범위를 소진하고 실제 단회 지원과
후속 배포의 결과는 각각 별도 기록한다.
