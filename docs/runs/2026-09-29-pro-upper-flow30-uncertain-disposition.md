# run: 2026-09-29-pro-upper-flow30-uncertain-disposition

- 목적: 전날 ACK가 없었던 상단 Async Slave `{Flow:30}` 기록을, 옛 `Flow=37`로
  되돌리는 송신 없이 현재값 채택으로 종결한다.
- 질문: 사용자가 실제 컨트롤러에서 30 미적용을 확인한 이 요청을 증거를 지우지 않고
  안전하게 미종결 목록에서 제외할 수 있는가?
- 실행 source commit: `450f33f`. 운영 이미지는 이전 `81048558…`에 저널·조정기·CLI
  세 파일만 겹친 `c6a7e44f…`; 프로토콜 schema digest `8863840c…`는 교체 전후 같다.
- UTC 실행·raw 수집 구간: 2026-09-29 약 02:26:37–02:26:45.
- 대상 physical identity binding SHA-256:
  `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- request id: `pro_upper_linked_flow30_20260928_01`.

## 실행과 결과

기존 운영 서비스의 세 파일이 패치 전 source와 byte-exact인 것을 먼저 확인했다.
Compose에서 이미지 참조 외 모든 필드가 불변인 overlay로 서비스만 재시작했고,
재시작 뒤 서비스는 running/restart 0이었다. 기존 저널은
`RECOVERY_REQUIRED`/`UNCERTAIN`, 해당 장비의 미종결 1건으로 재확인했다.

`jebao-flow-local abandon-uncertain-slave-flow`에 원 request id, 관측 `Flow=38`,
사용자의 물리 컨트롤러 미적용 확인을 지정했다. 이 명령은 인증된 **상태 조회만** 했고
제어 또는 rollback 프레임을 보내지 않았다. 새 462바이트 raw 보고는 action 4,
상단 `async_slave`·전원 ON·TimerON·`Flow=38`이었다. 기존 baseline 37 및 요청값 30과
모두 다르다. 이 raw는 owner-only 저널 blob으로 보존됐다(등급 a, opaque artifact id는
아래 digest). 실제 모터 유량을 별도 계측한 것은 아니다.

- 원본 uncertain record digest 및 보존 blob digest(등급 b):
  `6f2c5fda60f3afdd4de80c3d44ab36189626a2d53bc44b6e7b254c957c4bb583`.
- 새 raw wire-frame blob digest(등급 a):
  `174bddccc7cd12ea1aa454180d432d0ea1248a34bb9d9f19253db45189625075`.
- 최종 journal record digest(등급 b):
  `92fe77fbecd5afdb327559d961a9aba8d2f452ba61e8be8a85ecf4a4198f2c6b`.
- terminal timestamp: 2026-09-29T02:26:44.803146Z.
- 결과: transaction/action 모두 `ABANDONED`, ACK blob 없음, revision 3,
  `physical_write_count=0`, 해당 physical key 미종결 0건. 서비스 running/restart 0.

## 판정

**이 실행으로 새로 확정된 사실:** 원 ACK 미확인 시도를 재송신·원복하지 않은 채,
사용자 확인과 fresh raw `Flow=38`에 근거해 현재값 채택으로 종결했다. 원래 시도의
`UNCERTAIN` 원문과 새 raw가 모두 digest-bound로 남으며, 그 기록은 더 이상 상단
장비의 새 작업을 차단하지 않는다.

**확정하지 못한 것:** 어제 프레임이 물리 장비에 절대로 도달하지 않았다는 주장,
또는 일반적으로 ACK가 없는 모든 명령이 미적용이라는 규칙. 사용자가 실제
컨트롤러에서 이 건의 30 미적용을 확인한 사실은 운영자 관찰(등급 c)로만 둔다.
추가 제어 시험이나 5분 간격 자동 송신은 이번 실행 범위에 없다.
