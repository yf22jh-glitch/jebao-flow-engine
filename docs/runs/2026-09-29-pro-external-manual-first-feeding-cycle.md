# run: 2026-09-29-pro-external-manual-first-feeding-cycle

- 목적: [활성화된 31행 외부 수동 시간표](2026-09-29-pro-external-manual-schedule-activation.md)의 첫 자동 Feeding 진입과 6분 뒤 Tide 복귀를 관측한다.
- 질문: 반복 실행기가 14:57과 15:03 경계에서 두 Pro를 계획한 보고 상태로 전환하고 활성 상태를 유지하는가?
- 사용자 승인: 앞서 요청한 시간표의 반복 자동 실행. 이번 확인은 추가 명령 없이 저널·서비스 상태·보존 raw를 읽었다.
- UTC 실행 구간: 2026-09-29 05:57:07–06:03:40. 실행 source `f9aedc2`, image `sha256:11ec428023e0969c6dc81c668fc3a614863b63a61a58d3e0d6e456ce649aad69`.
- 계획 digest: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.
- physical identity binding SHA-256: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, 상단 Async `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

## 관측 결과

14:57 Feeding operation `pro_manual_20260929_0897_e60eb4dd952dffcc`은 05:57:07 UTC에 시작했다. 하단 `manual_mode=feed` child 1회가 `VERIFIED`이고 parent도 `VERIFIED`다. child 저널 digest(등급 b)는 `99ef836f1b3a3f0f31abb69d825987702d8b6bf98e1f475762aabef403fec5b9`이다. 최종 fresh pair는 하단 `master·TimerOFF·전원ON·feed·Flow40·fault0`, 상단 `async_slave·TimerOFF·전원ON·feed·Flow45·fault0`으로 보고했다. raw blob SHA-256(등급 a)은 하단 `b703ba0c91db7e326fdc6403cb65d0e7d227c1148f69a8409f3af4b6c186a3ed`, 상단 `00f70754b6369d25ae0741587b89b081bf870a02ced4605c980d9dc6e1d052ae`이다.

15:03 복귀 operation `pro_manual_20260929_0903_e60eb4dd952dffcc`은 06:03:07 UTC에 시작했다. 하단 역할 해제 → 하단 `manual_mode=tidal` → 하단 Master 재결속의 3 child가 각각 1회 송신·`VERIFIED`이고 parent도 `VERIFIED`다. child 저널 digest(등급 b)는 순서대로 `414f13561ab87683d49774b651a54b52613f2f787939bc226e7970ac9bba8ea0`, `06bcb93b34cf66f2418de999914ffb0883d575ecc81b2c8a1329a48e45d2c4cd`, `7902de53d843c377c849629804b8d01a04224aaa27ecd8fd5772c4efe859be75`이다. 최종 fresh pair는 하단 `master·TimerOFF·전원ON·tidal·Flow40·fault0`, 상단 `async_slave·TimerOFF·전원ON·tidal·Flow45·fault0`이었다. raw blob SHA-256(등급 a)은 하단 `cb827b0b08b81b12b89bd73ff287a5d0d34c67255258e8151231bb2e8df5773f`, 상단 `9f3a793f215e48c4f52518e843c4dbb05ccb9279b309543206d3176a65151420`이다.

두 장비의 432B image SHA-256은 계속 하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`, 상단 `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`였다. 반복 실행 서비스는 `running`, 재시작 0, `runner_running=true`, 계획 상태 `active`, 다음 경계 16:27 Feeding으로 조회됐다. 이번 두 경계에서 MCP·리턴·조명·도징 명령은 없었다.

**새로 확정된 사실:** 첫 자동 Feeding 진입과 6분 뒤 Tide 복귀가 각각 계획 경계의 7초 뒤 시작해 장비 보고와 내구성 저널에서 검증됐다. 이번 표본은 장비가 보고한 모드·Flow의 확인이며, 실제 물리 유량이나 Async 위상 측정은 아니다. 이후 하루 전체 경계의 성공을 이 두 경계에서 추정하지 않는다.
