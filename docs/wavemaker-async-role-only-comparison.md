# 원복 후 제품 Async 역할 단독 전환 비교

정책 `582a360`, 선행 [원본 복원](runs/2026-09-15-product-async-owned-restored.md).
사용자는 성공했던 시험과 제품의 명령/코드 차이를 확인한 뒤 Async 동작까지 진행하라고 했다.
**이 제품 질문 계열 누적3번째 실기이며, 중단/보고 뒤 새 승인에 따른 단회1개다.** 카운터를
질문 재정의로 리셋하지 않는다. 이 문서는 새 실기 성공이나 원인 확정을 뜻하지 않는다.

## 그대로인 것과 다른 것

- 두 원본·독립 상태 확인 후 같은 두 Pro/같은 시간표/준비값으로 시작한다.
- 준비2·image2·master/Sync3필드2 단계는 그대로다. 마지막 Async만 Linkage 단독 명령이다.
- 마지막 전환 직전 각 endpoint를 새 session으로 읽고 닫아 두 장비의 실제 필드·프레임·시각·
  image digest를 기존 내구성 blob 체인에 남긴다. 새 기대값 게이트는 없고 `complete:false`다.
- 전송 전/후 읽기·ACK 요구·timeout·재전송 금지·실패시 자기 원복 순서는 그대로다.
- 자기 원복 지원은 기존 정확 source3필드 또는 신규Linkage단독 형태만 수용한다.7단계/앞6검증/
  마지막만미확인/slave/child상태/세id/원본/qualification·최종432B/8controls 조건은 불변이다.

이전 실험의 `write_linkage`와 같은 Linkage-only builder 결과를 쓰되 실험 하네스 자체를
재사용하지 않는다. 이전 adapter의 ACK 불명 후 읽기 수습이나 같은 session 재사용까지 동일하다는
주장은 하지 않는다. **payload와 직전 RO session 읽기 두 변수가 바뀌므로 성공하더라도
payload 변경 하나에 원인을 귀속하지 않는다.** 이전 실패의 기전은 여전히 UNKNOWN이다.
Auto/시각은 기록 대상이며 immutable expected가 아니다. 원래 고정 image 및 변경한 필드의
반환값을 확인한다. 성공 범위는 Async 역할 수용+image이며10/15분 경계 전체나 위상은 아니다.

## 검증과 종료

새 계약 시험은 slave 마지막 send가 Linkage 단독인지, pre-Async 페어 raw가 최종 증거의
previous 체인에 남는지, 해당 read 실패시 Async 전송0/자기 원복인지 확인한다. fake는 기존
raw-copy·fault injection뿐이다. 기존 시험 함수/단언은 제거하지 않는다. targeted83 PASS,
7.06초이며 Ruff의 새 단언 줄길이1건만 정정한 뒤 check가 통과했다.

첫 source/image/schema 결속 뒤 새 id로 한 번 전진한다. 원 Async/atomic 실패 id를 재전송하지
않는다. 실패하면 전진을 멈추고 자기 원복부터 닫는다. 첫 slave atomic이 미확인이면 이미 실장된
소유 scalar 지원으로 자기 원본을 복원하며, 새로운 값 탐색이나 전진 재시험은 자동으로 하지 않는다.
성공·실패 모두 증거와 누적 카운터를 기록한다. 기존40receipt·writer·schema 입력9개·frozen
native·protocol·transport·공용guard는 변경하지 않는다. 원복 뒤 운영 배포는 별도 확인한다.

최종 source/test 전체1회는 **2891 PASS/5 FAIL/경고2**, 84.98초다. 이전과 같은5개 이름의
실패이며 전체 PASS나 동일근본원인으로 쓰지 않는다. 기존 test 함수 AST 변경/제거0,
단언124→135다. targeted SHA는 `218f7369d9f540d6b87c40d23885aefc06929fc21c603f9eb36219e2fdd7d9fb`.
정적 reviewer는 변동값 비교 부재·legacy chain 소비·no-resend를 확인했으며 pytest를 재실행하지 않았다.

분석은 `native_final_sha256`의 previous 체인을 따라 `before_async_role_only` stage를 찾아야
한다. 루프의 부분 저장은 master1행 다음 master+slave2행을 남기므로 같은 capture hash를 중복
표본으로 세지 않는다. 최상위 final devices만 읽어 관측이 없다고 결론내리지 않는다.
