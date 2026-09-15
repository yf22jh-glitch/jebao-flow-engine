# Pro 9종 자격 두 번째 실행 — app 범위·빈 ACK 정정 후 PASS

## 결론

Pro 두 대의 9종 × 실제 명령2개가 모두 반환값과 일치했고, 자기 원본 복원 뒤 자격 후보18건/
영수증36건이 발급됐다. 컨테이너 exit0이다. **제품 Sync/Async 결속·해제 또는 운영 배포 완료는
아니다.** 아직 Bar 재자격·native 제품 실기·운영 generation 교체를 하지 않았다.
첫 실패를 소급 PASS로 바꾸지 않는다. 이 질문은 두 번째 실기로 답했으며 정보0이 아니다.

## 범위·이미지

- 사용자 승인: “될때까지 수정하고 배포해”, 별도 정책 `a1dcd4d`.
- source `bb5405c9e353464b75b6faa7661ed215d1be4fe8`; 독립 diff 검토 차단0.
- image `d5e00203d9daedceca2e331d3c827b215bdd64a38446a7bd5f55dba2cdfcc720`.
- schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1` 불변.
- base `985d5bac…`, source145파일 HEAD exact, 패키지 digest `7dec254f…` 불변.
- build의 `--network none`은 RUN 단계 제한이다. 이미지 검사는 별도 network-none/운영 volume0.
- 새 operation `pro_qualification_bb5405c_02`, 이전 실패 id나 후보 경로 재사용0.
- 컨테이너 `363871ed151bafc5e9e8910485efbc1ec767a1d468e038811094a8b0be01f063`.
- UTC `2026-09-15T08:42:46.048595121Z`–`08:44:51.433951985Z`, exit0.

## 실제 전송·응답·복원

source 준비38개가 모두 VERIFIED이며 ACK38/after raw38/after 불일치0이다. 그중 atomic prep2는
scalar 자격으로 세지 않는다. 나머지36개가 9종×2대×2명령이다. 두 대 custom은 각각 실측
before5→after6→after5로 이동했다. 이는 이번 6/5 명령 수용이지 이전1의 처리 기전 증명이 아니다.
시간표 전체432B, Timer/Switch 결합, sine/constant, F21/20, Feed1/2, Flow39/40, 전원OFF/ON,
master/independent 역할을 행사했다. slave 역할은 활성화하지 않았다.

자기 detach record는 VERIFIED4/NO_CHANGE2, ACK4다. 총42개의 전송/ACK 기록이며 준비44개를
44회 전송으로 세지 않는다. 원복 자체의 reply raw를 재대조했고, 별도 사후6 reply에서도
Pro 원본432B와8controls, Bar 준비 상태의 image/controls가 모두 일치했다.
사후 UTC `08:45:56.486682`–`08:46:00.788263`; fault7 모두false.
Pro 수동 원본은 second constant30/F32, first constant40/F34이며 둘 다 TimerON/independent다.
Bar는 constant40/F80·TimerOFF로 유지됐다. Auto/장비 시각은 원복 고정 목표가 아니다.

source record SHA `f7707ab6b83a2362c213ea1cdeaff353606034cc6cc5f00a2fc91d7c0f5b5571`,
child SHA `342eaf995b01d9d42365c2f53a719e4ae6ddf857b6adc0f6da6d34d11261874a`.
four blocking paths 없음. 운영 config/install/qualification 파일은 직전 digest 그대로이며
원 서비스는 stopped다. first 앱 마지막 TimerOFF와 Bar classic91/F80 앱 복귀 의무는 별도다.

## 사용자 질문과 검증 대상 구분

실행 중 사용자는 변하는 값을 고정 대조하지 말고 불변값과 실제 응답을 구분하라고 지시했다.
현재 verifier는 저장 schedule image 전체 또는 해당 명령의 expected 필드만 비교한다.
AutoFlow/AutoMode/장비 시각은 고정 expected로 넣지 않는다. 명령으로 바꾼 값의 readback 확인은
실제 수용 증거이며, 나머지 변동 정보는 관측으로 보존한다. 이 질문 때문에 새 gate나 시험을
추가하지 않았다. 다음 실기는 추가로 시작하지 않고 진행 중 건의 종료와 원복부터 확인했다.

수동 상태 조회를 실시했으나 연속 감시를 주장하지 않는다. 마지막 조회의 docker exec는
컨테이너가 이미 정상 종료한 뒤여서 실패했다. 컨테이너 exit0·자격 출력과 조회 명령 exit1은
별개이며 원 로그를 보존했다. 같은 장비 명령 재전송0이다.

## 증거 결속

private artifact id `pro-qualification-20260915.51uFgd6n`, 원격 논리 `audit-pro-02`에
316개 content-addressed blob과 두 record, report를 보존했다. wire/status는 (a), journal/
qualification 결과는 (b), 수동 조회는 (c)다. 오프라인 독자는 blob digest를 검증하며 장비 접근0,
운영 safety mount는 RO다. 보고서 SHA `0bb15c6a0a21fa17f803f3cfd13e77586e7d4bd9fef2826cd4c82d49d533ee0e`.

| 산출물 | SHA-256 |
|---|---|
| image check | `c18162448842674792949724096faeace15e3fafac20a4502df833b3d44d906c` |
| pre read | `77d18e8a17b7275dd7d560efc947005d3b2614cc3ffc3191ab1034f0921dd997` |
| post read | `fe42411d434f4ccc7db5bf2e61b90e87cbfd856c78b143117f3e4b95ffc71de4` |
| terminal/query log | `c601581616d0cc9325575666e70fca953396bdd6a26ef90222315886c1ad160d` |
| audit output | `690363bdae2700ba6ab91c804af6e2452c8ad11e2a583dd7042fe98f2d2a4603` |
| CLI wrapper | `85676d893cc3a3874197175c3a6a97d86fe773fffccc0e54468195f12fef6f47` |
| offline auditor | `4744724e14faec0bd37666e461e453280321cc0759330359697ea2752e5f832a` |

**새로 확정:** app 범위6/5 custom과 정상 빈 ACK를 포함해 두 Pro9종의 실제 자격 발급 경로가
완료됐고 전체 원복도 일치했다. 모든 파형/부가값 조합, native slave 해제, 물리 위상·유량,
HA 편집 화면·운영 배포는 이 실행의 답이 아니다. 동결 native/timeout/retry 변경0이다.
