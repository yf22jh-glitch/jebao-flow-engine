# run: 2026-10-01-control-ack-readonly-audit

- 목적: 보존된 제어 ACK의 최대 지연과 현재 ACK 누락 처리의 차이를 확인한다.
- 실행 범위: 기존 Jebao 대상 전용 PCAP의 오프라인 분석. 장비 제어·서비스 재시작 0회.
- 원본 조회 span: `2026-09-28T11:27:20.064872Z`–`2026-10-01T03:41:08.400209Z`.
- 증거 등급: (a) preserved raw artifact. 종료된 rotation 2개와 당시 append-only 파일의
  47,107,971-byte prefix를 읽었다. 마지막 파일 전체의 immutable digest를 주장하지 않는다.

## 분석 방법과 관측

TCP port 12416의 stream을 방향·connection별로 재조립했다. SYN으로 connection을 나누고,
동일 TCP sequence의 중복/재전송을 제외했다. Gizwits `0x0093`의 action 1 요청과 같은
connection·4-byte command sequence를 가진 `0x0094`를 대조했다. TCP ACK, login 응답,
MQTT ACK는 수치에 포함하지 않았다. 최초 요청 bytes부터 완성된 응답까지의 PCAP 시각이다.

- 요청 108개, matched control ACK 106개, 관측 ACK 부재 2개.
- 중복 application sequence 0개.
- 최대 `1.791572초`, 중앙값 `0.392792초`.
- 최대 요청: `2026-09-30T03:03:25.906894Z`.
- 최대 ACK: `2026-09-30T03:03:27.698466Z`.
- 이 표본 밖 과거 전체 이력의 최대값이나 펌웨어 최대 응답시간은 주장하지 않는다.

최대 sample이 포함된 종료 rotation:

- opaque artifact id: `target-only-any-rotation-01`.
- digest: `57da72de93bdb529e18c29abdd09c5490c3058eb7fef84a0b004c4a20d0018dc`.

최대 sample의 7초 창을 별도 private raw artifact로 보존했다:

- opaque artifact id: `ack-max-20260930T030325Z`.
- selected UTC window: `2026-09-30T03:03:23Z`–`2026-09-30T03:03:30Z`.
- digest: `b8e947dceda66803803245206a38763b09af6aa4188d4079c5f8d5caeb472a7a`.
- manifest binding SHA-256: Pro A
  `ab311068d7799581ef4daaba151ad071009d7013ca70a6048a1ddf7af6b5a62e`, Pro B
  `6f9253c2b3055326aafa70b5ecfdc5b197b37d52b0b980dd0c5e1385377bd7ec`, Bar
  `120fcfaf2316728fc094b2315d83a1c9f206777b4cf56b86f740dc7faede1645`.
  これは private manifest binding の canonical JSON の digest で、最大 sample の
  physical role を特定したという主張ではない。

## コード監査 / 현재 처리

- LAN session 기본 response timeout은 5초다. ACK 최대 관측값과 별개다.
- Coordinator의 send exception은 `UNCERTAIN / RECOVERY_REQUIRED`を保存し、再送しない。
- 前日の Flow30 drop は指定 action の手動 abandonment であり、一般的な timeout→조회→drop
  경로는 아니었다.
- 현 idle 경로는 ProtocolTimeoutError 1회 재시도 후 멈추며 ConnectionResetError는 즉시
  일시정지한다. 이는 제어 ACK 누락과 다른, 조회/인증 실패 경로다.

## 이 실행으로 새로 확정된 사실

위 보존 표본의 실제 control ACK 최대 지연은 1.791572초다. 일반 외부 시간표 경로에는
사용자가 요청한 ACK 제한시간 뒤 상태조회·드롭 처리가 아직 없다. 조회 오류 재시도도
ProtocolTimeoutError 하나에 한정되어 있다.

## 다음 변경 / 확정하지 못한 것

[사용자 지정 처리 계약](../pro-command-readback-policy.md)에 따라 외부 시간표 scalar child를
수정한다. 변경 명령 재송신, qualification 계약 변경, 운영 일정 재활성화는 하지 않는다.
ACK 부재만으로 물리 명령이 실행되지 않았다고 증명하지 않으며, 장비 응답이 없다는 사실만으로
장비 전원/물리 고장을 판정하지 않는다.
