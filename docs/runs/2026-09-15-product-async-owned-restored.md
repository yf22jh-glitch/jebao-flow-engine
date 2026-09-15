# 제품 Async 미확인 전환 뒤 자기 원본 복원

**두 Pro의 이번 attach 전 원본으로 복원 완료. Async 전환 실패의 원인은 아직 UNKNOWN이며
운영 배포는 미완료다.** 앞선 실패를 PASS로 바꾸지 않는다.

실행 source `31a6e119ceeb51650cae60bcb4b37f71db3df5e0`, image
`f710ef13e43d3b6ff9b6f740bc7fe88bafd1e8270db8da0a26ba1375acb976fa`.
schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`, 기존40receipt 유지.
145개 설치 source bytes와 package digest를 오프라인 대조했고 writer/transport 변경0이다.
사용자가 앱에서 Sync로 보인다고 보고한 뒤 재개를 승인했다. 앱 보고는 raw 증거가 아니다.

## 실제 소유 복구

`wm_product_async_5fbf58d_01`의 결정적 assist를 한 번 실행했다.
UTC `12:02:32.960041–12:03:11.595017`, exit0.

- assist5단계 중 master independent·slave schedule_enable=False만 ACK/after VERIFIED2.
  slave Mode/Flow/Frequency는 직전 실제 일치로 NO_CHANGE3이며 새 전송0이다.
- 기존 child의 첫 atomic은 fresh6필드 일치로 전송 없이 VERIFIED, ACK는 None을 유지했다.
  `assisted_target_observed_delivery_unknown` 진단과 assist 증거 결속이 남는다.
- child 나머지5단계는 ACK/after VERIFIED다. 이번 복구 ACK는2+5=7이며 준비11을 전송11로 세지 않는다.
- 원 source는 ROLLED_BACK으로 닫혔다. 마지막 Async action의 ACK 부재·기존 evidence는
  유지되고 `native_own_baseline_restored_delivery_unknown` 진단을 남긴다. 원 전송 성공 미주장.
- 최종 별도 reply에서 두 원432B와8controls exact를 확인했다. source/child의 미확인 명령 재전송0.

| 보존 record | phase | SHA-256 |
|---|---|---|
| source | rolled_back | `3ad9af91b17601a1c836e186c07128971d01fe1f3dca2fe86da5efa220cba180` |
| child | verified | `87923d9791f6faf0b78a99f11e09e21dd6d782e078da14e1b4f64ff3647d2c8e` |
| assist | verified | `0add1ea400e168ea97d5d3e1fe3260ff602bba57d2e09e0cb3e0b8ccba45ddd9` |

private `evidence-async-assistance-01`: record3/blob161/report1. report SHA
`8229d2a9828c6574a722547ea2ab9dd29e7187d30fb11fa9dbdc5e1cdd0c17ba`.
보호 root RO·원 UID로 복사/해시 대조했고 기존 산출물은 덮어쓰지 않았다.

## 추가 fresh 읽기와 열린 의무

command0의6개 reply(action3) UTC `12:03:46.715838–12:03:51.177302`를 따로 보존했다.
각 Pro2표본 모두 원432B exact, second ON/TimerON/independent/manual constant30/F32,
first ON/TimerON/independent/manual constant40/F34·양쪽 Feed10/custom5다.
Bar는 원384B exact·ON/TimerOFF/independent/classic40/F80이다. Auto/clock은 고정 원복 목표가 아니다.
원 운영 config/manifest/qualification 세 digest는 불변이며 서비스는 stopped다.
first 앱 이전 TimerOFF 및 Bar 앱 이전 classic91/F80, 원 서비스 의무는 이번 원복과 별개다.

새로 확정된 사실은 실패한 attach도 자기 원본을 소유하고 있으면 같은 scalar 지원으로 최종
exact 복원이 실제 성립한다는 것이다. 이번 수습은 새 Async 제품 실기가 아니며 기능 시험 횟수와
섞지 않는다. 제품 질문은 Sync/Async2회 뒤 §7 보고 대상이다. 대안은 (1) 성공했던 실기와 다른
역할 payload·세션 흐름을 대조하고 좁은 새 승인 아래 재개, (2) 제품 Async를 park하고 자격 있는
개별 기능 배포를 진행하는 것이다. park해도 현재 두 Pro의 시험 원복 의무는 남지 않는다.
사용자는 차이 설명 후 “이어서 계속 진행하자 async 될때까지”라고 요청했다. 새 전진은 별도
범위 결정을 기록한 뒤에만 수행하며, 실패한 원 payload 반복·timeout 증가·factory reset은 없다.

## 도구 오류와 검토 한계

build v1은 Dockerfile FROM에 로컬 image ID를 넣어 registry 이름으로 오인됐고 실패했다.
그 결과 이미지가 없는 상태의 verification v1도 실패했다. 두 원 로그를 보존했다.
v2는 기존 local tag의 정확한 ID를 먼저 확인하고 source-only build했다. RUN의 network none을
전체 build/metadata의 네트워크 격리로 과장하지 않는다. verification v2는 volume0/network none,
source145/package/schema 일치를 확인했다. pycache 환경변수 이름 오타는 이 검사에만 있었고
실행 operator는 정상 이름을 썼다. 검사/실행 어느 쪽도 보호 root 권한을 완화하지 않았다.
로깅은 자동 보존이며 수동 상태 조회 사이를 연속 감시했다고 주장하지 않는다.
