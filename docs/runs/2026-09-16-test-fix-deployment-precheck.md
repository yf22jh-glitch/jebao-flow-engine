# run: 2026-09-16-test-fix-deployment-precheck

- 목적: 반복된 다섯 테스트 실패 정정 후 새 제품 코드의 운영 전환이 기존 제어 자격을
  유지할 수 있는지, 현재 서버의 저장 입력만 읽어 확인한다.
- 사용자 요청: “수정하고 해결되면 배포하자”. 새 실장 qualification, 현재 Pro 시간표의
  원복, 미완료 source 종료 또는 기존 제어의 묵시적 비활성화 승인으로 확대하지 않는다.
- 정정 source: `c6e3ce6cfb757a16652f5d737c5de889582d7b4d`.
  실제 Linux 검사/사전 대조는 커밋 직전 같은 source bytes의 archive를 사용했다.
- 종료: **운영 전환 보류 — 배포 미완료**. 상태 조회·미리보기 전용 배포를 수용할지
  사용자에게 선택을 요청했다. 응답 전에 서비스 교체·재기동하지 않았다.
- software 검증: macOS **3152 passed, 1 Linux-only skipped**, Linux **3153 passed**.
  원인·정정·로그 digest는 [시험 정정 기록](../test-baseline-resolution.md)에 있다.

## 저장 입력 대조

서버의 일회용 container에 production data와 hardware-safety volume을 **모두 read-only**로
연결했다. network `none`, read-only root, capability drop ALL이며 candidate source도 read-only다.
장비 세션·broker·HA를 열지 않았고 journal initializer·lease·receipt 발급은 호출하지 않았다.
초기 조회 스크립트는 journal constructor 인자 오류로 종료됐으며, 성공 대조는 생성자 없이
저장 JSON을 읽는다. 검사 오류를 qualification 실패나 장비 실패로 분류하지 않는다.

- 등급: **(b) 보존된 구조화 inventory**. 저장 기록과 software projection의 근거이며
  장비가 지금 어떤 상태인지 증명하는 raw artifact가 아니다.
- opaque artifact id: `five-failure-fix.Vj158I/deployment-precheck`.
- inventory 시각: `2026-09-16T05:43:43.658745Z`. 별도 취득 시작/종료 span은 계측하지 않았다.
- inventory SHA-256: `5dd518945abbd4b721ebf480cafe414f8e49378d86f000f33e5d23c3ffa77f48`.
- 최신 보존 qualification set SHA-256:
  `4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031`.
- binding **11**, receipt references **40**, 이 set의 schema:
  `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`.
- candidate schema:
  `3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b`.
- 기존 manifest의 다른 content binding은 유지하고 candidate schema만 메모리에서 대조했을 때
  catalog의 ignored binding **11**, exposed function **0**. 입력 파일은 수정하지 않았다.
- 일일 운영 source `wm_pro_daily_fdcddc0_01`: **RECOVERY_REQUIRED**, revision **79**.
- journal tree: files **1996**, SHA-256
  `b31ee4182ef92413e35041f4e9229396fbf35bcd4549e996414df3e2ec5bf343`.
- receipt tree: files **58**, SHA-256
  `025af2428b0b38cac79e6bb6cfa0b9489a23b307c9fbb448858a6b418211ef3f`.

tree는 sorted `(relative path, SHA-256(bytes))` compact JSON digest다. 이 inventory는 한 시점의
보존 snapshot이며 전후 불변 대조를 했다고 주장하지 않는다. Docker 조회에서는 기존
HA/MQTT 서비스가 stopped, Pro read-only collector가 running이었다. 그 출력은 별도 파일로
보존하지 않아 (c) 관측으로만 취급하며 현재 장비 역할·시간표의 fresh 확인으로 올리지 않는다.

## 판정과 다음 선택

**새로 확정된 사실:** 다섯 software 실패는 해소됐지만, 서버의 최신 보존 40개 영수증도
candidate writer/compiler schema와 다르므로 그대로는 제어를 열지 못한다. 또한 Pro 미완료
source가 남아 있어 일반 write 차단을 우회할 수 없다. 이번 lock 수정은 schema를 바꾸지 않았고,
schema 차이는 앞선 제품 기능 추가에서 생긴 것이다.

가능한 다음 단계는 현재 장비 운전을 유지한 상태 조회·미리보기 전용 배포를 명시적으로
수용하는 것, 또는 제어를 보존할 수 있을 때까지 운영 전환을 보류하는 것이다.
제어 활성화를 원한다면 미완료 운영의 처리·자기 원복과 새 실장 자격 갱신 범위를 먼저 정해야
하며 이는 장비 설정 변경을 수반한다. 기존 영수증 수정·가짜 발급·journal 종료로 우회하지 않는다.

새 runtime 활성화, 기존 서비스/collector 정지, 장비 command, 앱 조작, qualification 발급은
모두 **0**이다. 소프트웨어 정정 완료와 운영 배포 완료는 별개다.

## 후속

위 보류 보고 뒤 사용자가 조회·미리보기 전용 배포를 수용했다.
[별도 observer 배포 기록](2026-09-16-observer-preview-deployment.md)에 실행 결과를 추가한다.
앞선 보류 시점의 사실과 제어용 schema 불일치·미완료 source 판정은 바꾸지 않는다.
