# run: before-daily-pro-owned-restored

**기존 앱 보조 Async 관찰 source를 자기 원본으로 복원했다.** 새 master 배치·일일 시간표
적용은 [다음 실행](2026-09-15-pro-daily-async-master-cycle.md)이며 이 복구와 별도다.
[이전 관찰](2026-09-15-async-master-link-app-rearm.md)의 앱 변경 뒤 남아 있던 source를
사용자의 새 Pro 운영 요청에 따라 회수했다. 공장 초기화·Bar write·기존 명령 재전송은0이다.

## 실행과 회계

- source `wm_operator_observe_9860107_02`, 기존9860107 이미지 그대로 사용
- 최초 자기 detach: 2026-09-15T16:50:24.859721Z–16:50:36.928714Z, exit2
  첫 slave atomic의 ACK timeout, 해당 action UNCERTAIN·나머지5 PREPARED
- 별도 기존 scalar assist의 master independent는 ACK/after VERIFIED가 됐다.
  그 다음 slave action1의 BEFORE 읽기는 ProtocolTimeoutError로 종료했다(전송0/PREPARED).
- 같은 assist record/동일 prepared payload에서 미전송 단계만 재개했다.
  재개 UTC16:52:06.694745Z–16:52:38.972153Z, exit0. master action0은 skip했다.
- assist5개는 ACK4/NO_CHANGE1(Flow), child 첫 atomic은 fresh 일치로 ACK 없이 해소,
  나머지 child5개는 ACK5. 이번 복구의 send 경계10/ACK9이며 준비11을 send11로 세지 않는다.
  원 atomic 전달UNKNOWN과 source 원 Async 전달UNKNOWN을 그대로 보존했다.

최종 두 direct reply를 archive에서 decode_frame으로 다시 읽어 action+status 동일성을 확인했다.
각 raw의 원432B와 SwitchON/TimerON/Linkage/Mode/Flow/Frequency/FeedTime/Cust_Wav_Freq
8개를 자기 시작 snapshot과 다시 대조해 전부 일치했다. Auto/시각은 원복 목표가 아니다.

| 이전 역할 | 최종 직접 응답 UTC | 원 image SHA-256 |
|---|---|---|
| old master |16:52:37.627412Z|60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99|
| old slave |16:52:38.856407Z|c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426|

양쪽 independent/ON/TimerON/constant, old master30/F32·old slave40/F34,
FeedTime10/custom5였다. 이 역할 배치의 원복 뒤에만 새 master/slave 배치를 시작했다.

## 보존 결속

private artifact `evidence-before-daily-restored-02`:275blob+3record+1report=279파일.
network-none·안전볼륨RO export 뒤 archive의 모든 blob digest를 대조했다.

- report SHA:2c34b676845ffffb98ff3f6f66f6afff1d1d5d1d7ba3444828888075ddb13e6e
- archive SHA:56f211ae3a88cbd75be7dd11d912373b48ff7fb2d2594f3759a4b74dec71282f
- source revision80/ROLLED_BACK SHA:8346cdfbe9b503086c7023d827267c219a67e675743b23ac24b6aadf6615a8ed
- child revision33/VERIFIED SHA:651f293d16f5a411b6649c504807a7ba1fabd995270dc72ac8a93f20aa9864f3
- assist revision26/VERIFIED SHA:d2c8d6b7dbaa47b3a8ad37b09646a41a9683f320b78aac58fbce38455c0e9de9

중단 구간 export도 별도 보존했지만 움직이는 기록 중간 snapshot이며 최종 증거로 인용하지 않는다.
이번 수습은 새 제품 실험 카운터를 증가시키지 않는다. 원복이 앱 조작 완료·물리 유량·생물 안전·
원서비스 복귀를 증명하지 않으며, 이전 실패나 보류의 사실을 지우지 않는다.
