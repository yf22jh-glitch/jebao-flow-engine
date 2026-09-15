# Async 연결 유지와 ACK 부재 뒤 직접 상태 확인

정책 `1ac5405`/`16dd723`, 증거 append `c6fbcf7`에 결속한다.
**소프트웨어 정정이며 새 실장 수용·배포 완료를 뜻하지 않는다.**

## 근거와 한계

두 실패 실행 모두 복원 중 master independent 확인 뒤, slave 해제 명령 전 BEFORE raw에
async_slave 보고가 있었다. [증거와 정확한 순서](runs/2026-09-15-product-async-role-only-stop.md#2026-09-15-추가--보존-원복-프레임의-async-보고-재발견)를 참조한다.
이는 원 명령 ACK가 아니며 전달·전이 시점·master 해제 영향은 UNKNOWN이다.
old 역할 payload 재구성은 실제 new payload와 동일하지만 old wire 원본은 없다.
old는 Sync 뒤 fresh pair를 읽은 연결에서 Async를 썼고, new는 관측 연결을 닫고 다시 읽었다.
또 new는 ACK 오류 직후 원복해, 역할 확인을 위한 후속 직접 응답을 읽지 않았다.

## 변경

- 마지막 Async 전환에 한해 fresh pair를 함께 읽고 같은 slave writer에서 Linkage 단독 전송.
  그 사이 재연결·중복 BEFORE 읽기를 제거한다. master 연결도 해당 단계 종료까지 유지한다.
  취득한 시각·Auto는 관측용으로만 저장하고 고정 기대값으로 비교하지 않는다.
- Async의 send_once 통신 오류만 기존 UNCERTAIN·오류 진단으로 먼저 저장한다.
  같은 record의 직전 Sync3필드 VERIFIED, pair2/7단계/마지막 exact Linkage payload에 한정한다.
  실패 연결을 닫고 별도 연결의 reply-only 읽기 **정확1회**, 제어 전송0으로 확인한다.
- action3 직접 응답이면서 Linkage가 async_slave면 상태를 VERIFIED로 기록하되
  `native_async_reply_verified_delivery_unknown`, ACKNone을 함께 보존한다.
  public 결과에도 acknowledgement_received=false, delivery=UNKNOWN을 출력한다.
  명령 ACK와 현재 상태 확인은 서로 다른 주장이다. 이전 실행 기록을 수정하지 않는다.
- 직접 응답 없음·불일치·report만 반환·취소는 성공이 아니다. 기존 자기 복구로 간다.
  shared timeout/retry 설정, 다른 명령의 ACK 계약, qualification 발급은 바꾸지 않는다.
- 상태 확인 뒤 최종 전체 image/역할 대조가 실패할 수 있으므로 그 정확한 source 형태도
  기존 owned assist 대상에 포함한다. 원본·child·3-id·5scalar·no-resend는 그대로다.
  원복 후에도 ACK 없는 마지막 역할의 delivery UNKNOWN 진단을 남긴다.

예외의 close는 각 writer 소유 범위에서 수행하고 실패한 stream을 다시 쓰지 않는다.
기존 최종 전체 image/명시제어 검사, independent·TimerOFF 확인 후 image 쓰기와 reply 원복은
유지된다. 새 ACK 규칙은 물리·펌웨어 가설이 아니라 이미 관측한 ACK와 상태 증거의 분리다.
실장 성공이 connection 차이를 근본 원인으로 증명하는 것도 아니다.

## 검증

- 관련 lifecycle/CLI targeted: **89 PASS**. SHA
  `4e73badc8e853772a69512165d9f6792f97b440bcd064e1dd3bed8beeb9c4060`.
- 최종 source/test full1회: **2897 PASS / 5 FAIL / 경고2**, 86.34초. SHA
  `dd74cbb8e644c87ecdbbccce630cda8d45d297bb2771bf0a56dd2a17b5256aca`.
  boot identity1, receipt 동시성2, 권한/umask2의 이전과 같은 이름이다. 동일 근본 원인이나 전체
  PASS로 쓰지 않으며 재실행으로 씻지 않았다. Ruff PASS.
- 기존 ACK-loss 1..7 시험 중 마지막7만 새 계약(직접응답으로 attach 확인→명시 detach)을
  단언한다. 앞6의 실패/자동원복·전체원본·단일전송 단언은 그대로다.
- 신규: 같은 slave connection에서 중복 BEFORE 없이 전송, reply 불일치/timeout/report/취소
  거부·원복, 상태 확인 뒤 final/child 실패에서도 owned assist 완전복원을 검사한다.
  byte-copy transport와 ACK/read fault injection뿐이며 펌웨어 전파나 지연을 모사하지 않는다.
- 초기 import 경로는 실행 전에 정정했고 Ruff line-length6개를 formatting으로 고쳤다.
  targeted 실패는0이다. 파일명 조회 오류2건은 장비·제품 오류가 아니다.

수정 source는 lifecycle1파일뿐이고 writer·schema 입력9개·frozen native·일반 guard 불변이다.
schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`과 40receipt를
그대로 사용한다. 새 실장1회는 제품 누적4번째이며 원복 뒤 배포를 잇는다. 실패 시 새로운
전진을 반복하지 않는다. 기존 first/Bar 앱 이전 복귀와 원 서비스 대체 계정은 분리한다.

## 2026-09-15 실장 결과 추가

`37175c0` 이미지의 [누적4번째 실행과 자기 원복](runs/2026-09-15-product-async-reply-reconciliation.md)은
Async ACK timeout 뒤 새 직접reply도 timeout으로 끝났다. owned assist·child 최종 원복과 별도
6reply exact는 확인했으나 Async 수용·운영 배포는 완료하지 않았다. source/test 추가 수정0이다.

독립 검토는 source/test diff `f71f87f39759c0431c8ced04345ccebe78f18d04fcf6544078a9b2d644091f67`,
전체 diff `2444ebba2d35f5bae88790b225690a090819efdf030062704c3167cf6d78c2d8`와 landing 일치를
재산출했고 차단0이었다. 리뷰어의 별도 환경은 lifecycle58 PASS, full2899 PASS/3 FAIL/경고2였다.
boot identity1·receipt 동시성2만 실패했고 이쪽의 권한/umask2는 재현하지 않았다. 앞 절의
이쪽 full2897/5 FAIL과 구분하며 어느 쪽도 전체 PASS가 아니다. 리뷰어는 실기에 접근하지 않았다.

비차단 진단 한계: 두 endpoint BEFORE가 모두 실패하면 첫 예외만 전파된다. final의 누적
master 행은 chain 전체 합산 때 중복될 수 있다. 이번 reply-only read는 observer 없이 실행돼
제외한 report를 보존하지 않았으므로 직접reply0과 전체무응답을 동일시할 수 없다.
