# run: 2026-09-16-bar-qualification-mode-stop

- 목적: Bar 기능별 자격을 생산하고 자기 원본 복원 뒤 HA 제어를 열 수 있는지 확인.
- 실행 source: `d79289714dc90e08cfeabfb1646922d673b9f8a0`; image
  `85a387b10671556fac3c8c2684e32a79f0c431ac50a64369c6ae54d65d3a78e5`.
- schema: `3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b`.
- 범위: 사용자 앱 Constant 준비 뒤 Bar만. 리턴·도징·Pro 장비 write 없음.
  Pro 서비스는 경합 방지를 위해 08:11:50 UTC 중지했다.

## 실행과 중단

`jebao-flow-local-qualify run`의 기존 producer를 기능별로 사용했다.
operation id는 `bar_control_qualification_d792897_<function>_01`이다.

| 기능 | 요청 | durable 결과 | receipt |
|---|---|---|---|
| manual_flow | 40 → 39 → 40 | VERIFIED, 3 send / 3 ACK | 2 |
| manual_frequency | 80 → 5 → 80 | VERIFIED, 3 send / 3 ACK | 2 |
| manual_mode | Constant → Classic → Constant | 두 번째 ACK 뒤 UNCERTAIN, 마지막 복귀 미전송 | 0 |

모드 source는 08:14:46.577196–08:14:52.255272 UTC, RECOVERY_REQUIRED/rev6이었다.
same 액션은 VERIFIED, step 액션은 ACK를 보존한 UNCERTAIN, restore는 PREPARED였다.
CLI exit2는 `current_operation_failed_closed`, 액션 진단은
`qualification_phase_uncertain`만 남아 원 예외·즉시 readback 원본은 보존되지 않았다.
readback 예외·목표 불일치·후속 envelope 거부 중 어느 것인지는 UNKNOWN이다.
정적 호출 추적상 step의 완화 Mode 검사는 정확한 `phase="step"`을 받는다.
펌웨어 지연이나 Flow 자동 변경을 원인으로 단정하거나 이를 가정한 fake를 만들지 않는다.

08:15:53 UTC의 별도 explicit reply는 Classic/Flow40/F80/ON/TimerOFF/independent를
보고했다. 이는 그 시각의 실제 응답이며 앞선 즉시 readback을 소급 증명하지 않는다.
feeding_duration·power·schedule 및 TimerON은 실행하지 않았다. 전진은 총8 send/8 ACK다.

## 자기 원본 복구

실패 rev6와 연결 blob을 먼저 별도 보존한 뒤 동일 operation의 기존 generic `recover`를
한 번 실행했다. native context 없는 scalar 자기 복구이며 trial 재시도가 아니다.
복구는 물리 write1, ROLLED_BACK/rev11로 끝났다. 저널 digest는
`28c8afbf259e6cca3ee5fe7df84e622cb8dd9af7042d3b7f1c3ebb35db026769`이다.

08:22:15.801659 UTC fresh explicit reply에서 원9controls 모두 일치했다:
SwitchON=true, TimerON=false, Linkage=independent, Mode=constant, Flow=40,
Frequency=80, FeedTime=10, PulseTide=false, FeedSwitch=false.
원384B image도 byte-exact이며 SHA-256은
`8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea`다.
시각·Auto 값·현재 슬롯의 과거 일치는 복구 판정에 사용하지 않았다.
08:22:44 UTC 전역 unfinished=0을 확인했고, 보존된 Pro-only 서비스를
08:23:03.301149 UTC 같은 입력/image로 다시 시작했다. Bar 새 제어 배포는 하지 않았다.

## 보존 근거와 한계

- opaque artifact id: `bar-stop-restored-evidence-20260916-01`.
- archive SHA-256: `78917b05454fbb2e22a16469e1e7001e9cd17ed31a458eeee616fe5bb5ebdee5`.
- identity binding: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- raw UTC span: 08:12:14–08:22:15, 2026-09-16.
- (a) before/failure/after explicit reply 원본 wire·status·384B image.
- (b) 성공 두 source, 실패 rev6/복구 rev11, 연결 blob, candidate4receipt.
  archive의 blob digest를 재계산하고 before/after controls·image를 직접 대조했다.
  중간 ACK/VERIFIED는 durable 기록의 주장이지 별도 보존 raw 프레임은 아니다.
- 초기 archive 수집은 서비스 출력이 tar 앞에 섞여 유효 archive로 쓰지 않았다.
  위 digest는 tar 단독 명령으로 다시 읽어 정상 파싱·내용 대조한 별도 파일이다.

**새로 확정된 사실:** 두 기능은 기존 producer의 terminal 자격4장을 발급했고,
모드 시험은 ACK 이후 검증 단계에서 멈췄으며, 기존 generic 자기 복구로 원본에 돌아왔다.
모드 수용 지연·원 예외, 나머지 기능 및 timer는 확정하지 못했다.
같은 모드 시험을 새 id로 반복하지 않는다. 다음 코드 변경은 원인 분류와 판정 값 보존을
개선할 수 있지만 검증 gate를 가설로 추가하거나 UNKNOWN을 PASS로 바꾸지 않는다.

사용자는 중단 도중 Bar TimerON/OFF 구현·실기 활성화를 추가 요청했다. 이 기록은 그
후속 operation의 성공·출력100 시간표 활성화 또는 Pro 재자격 재실행의 승인이 아니다.
