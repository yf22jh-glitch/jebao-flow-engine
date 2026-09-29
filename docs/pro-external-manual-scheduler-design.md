# Pro 두 대 외부 수동 시간표 — 설계안

상태: 외부 수동 시간표의 파서·CLI·내구성 있는 실행기와 오프라인 검증을 구현했다.
**배포·반복 송신·장비 전환은 아직 하지 않았다.** 마지막 수동 실기와 이번 구현은 별개다.

## 목표와 근거

호스트의 Asia/Seoul 일일 시간표가 두 Pro의 운전 시각을 소유한다. 각 행의 물리적 상단/하단
Flow와 공통 파형을 명시하고, 현재 Master인 기기의 **수동** `Mode`·`Flow`·필요한
`Frequency`, Async Slave의 **수동** `Flow`·필요한 `Frequency`를 제어한다. `AutoMode`·`AutoFlow`·`AutoFreq`
및 432-byte 시간표 이미지는 반복 실행 중 쓰지 않는다. Bar와 리턴 펌프는 이 실행기의 범위 밖이다.

이 설계는 Feed 정지의 근본 원인을 Pulse 한 가지로 단정하지 않는다. 사용자가 비-Pulse 때도
Feed 정지를 관찰했고, 따라서 모든 파형에서 *의도한 수동 Mode와 두 기기의 실제 보고값*을
비교한다. 목표는 원인 가설을 코드에 박는 것이 아니라 장비 자동 시간표와 분리된, 관측 가능한
운전 경로를 만드는 것이다.
Feed로 바뀌는 내부 원인이 무엇인지는 이 전환만으로 확정되지 않으며, 재발하면 계획값과
원본 상태를 나란히 남겨 별도로 판정한다.

보존된 근거:

- [수동 Pulse 원문](runs/2026-09-28-pro-manual-pulse-cloud-command-capture.md):
  `TimerOFF`·Master/Async에서 Master의 `Mode` 한 필드 명령 뒤 Slave도 같은 Mode를 보고했다.
  이는 해당 조건의 한 실행 결과이며 모든 모드·장기간 유지의 증명은 아니다.
- [결속 Slave Flow 단독 시도](runs/2026-09-28-pro-upper-linked-flow30-direct-attempt.md):
  결속 중 `{Flow:30}` 단독 명령은 ACK가 없고 Flow도 바뀌지 않았다.
- [역방향 Slave Flow 변경](runs/2026-09-28-pro-reverse-linked-flow37.md):
  Master 역할 해제 → Slave Flow 변경 → Master 재결속은 세 개의 서로 다른 저널·단회 송신으로
  VERIFIED됐고, 직후 역할·Flow·두 이미지가 일치했다.

## 시간표 데이터

입력은 버전·시간대·원본 표 식별자·상하단 물리 identity SHA-256·행 배열을 갖는 별도 JSON이다.
현재 상단/하단 결속 해시는 마지막 보존 raw 기록의 값이며, CLI에서 상하단을 뒤집어 넣어도
첫 송신 전에 거부한다. 각 행은 `HH:MM`,
공통 `mode`, 물리적 `upper_flow`·`lower_flow`, 파형에 필요한 경우의 `frequency`를 가진다.
Master Flow와 Frequency도 포함하는 이유는 장비 자동 시간표를 끄면 그 값들을 더 이상
슬롯이 바꿔주지 않기 때문이다. 사용자가 지정한 상단/하단 출력과 F값을 그대로 재현하는
데 필요한 필드만 보낸다.
`feed` 행에는 Flow·Frequency 목표를 두지 않고 정확히 6분 뒤 실행할 일반 운전 행을 둔다.
이렇게 해야 현재 Master가 상단에서 하단으로 바뀌어도 상단/하단 Flow가 뒤바뀌지 않는다.
현재 장비의 시험용 2슬롯이나 예전 예시 파일을 운영 표로 자동 채택하지 않는다. 사용할
시간표는 선택한 사용자 표를 별도 파일로 고정하고 digest를 출력한다.

유효성 검사: 00:00 행, 시각 오름차순·중복 없음, 24시간 전체 커버, 명시된 Feed 종료,
모드 enum, 파형별 Frequency 사용 여부, 두 장비의 설정 출력 범위·wire step·45 상한.
Tide Rise and Fall에는 Frequency를 보내지 않는다. 같은 Mode라도 Flow만 변하는 행은
유효하며, 변화 없는 필드는 물리 송신을 생략한다.

## 장비 상태와 1회 전환

반복 실행 전 두 기기의 현재 identity/물리 바인딩과 432-byte image·수동 controls를 보존한다.
**전환은 시간표 실행기와 별개의 1회 operation**이다. 목표 상태는 전원 ON,
`Master/async_slave`, 양쪽 `TimerON=false`다. 이미지 바이트는 보존하되 자동 시간표는
비활성으로 둔다. 앱형 `schedule_enable`이 Slave를 independent로 해제할 수 있으므로,
`TimerOFF`를 한 번에 누르는 것으로 간주하지 않고 fresh pair로 순서를 설계·검증한다.
전환이 끝나기 전에는 시간표 송신을 시작하지 않는다.

각 행 직전에도 fresh pair에서 신원·역할·TimerOFF·전원·fault·현재 Flow 범위를 확인한다.
기존 Mode가 계획과 달라도 이를 Feed 원인으로 분류하지 않고 원문과 기대값을 남긴다.
반면 신원·역할·Timer 상태가 달라진 경우에는 다른 토폴로지로 쓰지 않는다.

## 경계 한 건의 송신 순서

1. 날짜 + 행 시각 + 시간표 digest로 고유 operation ID를 만들고, 이번 행의 목표와 현재
   두 장비 값을 보존한다. 다른 미완료 장비 operation이 있으면 먼저 그 결과를 처리한다.
2. 바꿔야 할 필드가 있으면 Master `{Linkage:independent}`로 해제하고, 필요한 Slave
   `Flow`·`Frequency`, Master `Flow`·`Frequency`·`Mode`를 각각 한 번씩 변경한 뒤 Master
   `{Linkage:master}`로 재결속한다. 값이 이미 목표라면 해당 필드는 건너뛰며, 변화가
   전혀 없는 행은 장비 송신 0회로 기록한다. 각 child는 기존 coordinator의
   identity/bounds, durable journal+fsync, 단일 송신, ACK/readback 및 복구 권한을
   사용한다. 중간 child가 불확실하면 남은 child를 자동 송신하지 않고 정지한다.
3. 이 순서는 마지막 수동 실기의 해제→Slave Flow→Master Mode→재결속을 확장한 것이다.
   **Master/Slave Frequency 변경과 양쪽 TimerOFF 전환은 아직 실기 검증되지
   않았다.** 전체 자동 실행의 검증으로 오해하지 않는다.
4. 마지막 fresh pair에서 Master/Async, 양쪽 TimerOFF·전원·fault, Master 목표 Mode/Flow/
   해당 Frequency, Slave 목표 Mode/Flow/해당 Frequency, 양쪽 image 불변을 확인해야만 그 행을 성공으로
   기록한다. Slave가 Feed 등 다른 Mode로 남으면 **실패로 기록하고 다음 행 송신을 중지**한다.

Feeding 행은 Flow를 0으로 쓰지 않고 Master `Mode=feed`만 명령한다. 6분 뒤 행에서
정상 파형을 명시적으로 다시 명령한다. 기기 `FeedTime` 카운트다운이 종료됐을 것이라고
가정하지 않는다. Feed 종료 예정 시각은 Feed 시작 전 내구성 있게 기록한다. 재시작 시
예정 종료를 넘겼고 실제 Mode가 Feed라면, fresh pair 검증 후 현재 일반 운전 행으로
**한 번만** 복귀를 시도한다. 전달이 불확실하면 재송신하지 않고 알린다.

## 실행·중단 규칙

- 기본은 preview; 반복 실행은 명시적 활성화가 필요하다. 시작 시 지난 행을 전부 재생하지
  않는다. 경계를 놓쳤다면 그 사실을 기록하고, Feed 종료처럼 정지 해소에 필요한 현재 행
  복귀만 별도로 다룬다.
- 행별 parent 진행 기록과 child 저널을 첫 송신 전에 내구성 있게 남긴다. 재시작 시 이미
  시도된 child를 다시 보내지 않는다. 한 행이 미완료면 다음 행을 시작하지 않는다.
- 두 장비 상태 raw와 행별 기대/관측 Mode·Flow·Frequency·역할·Timer·image digest,
  ACK·저널 ID를 기존 Jebao 전용 private 로깅 영역에 남긴다. 주소·ID·원문 raw는 Git에
  넣지 않는다.
- 기존 `linked-flow-schedule`은 `TimerON` Master와 Flow만을 전제로 하므로 이 실행기와
  동시에 활성화하지 않는다. 새 경로는 동결된 `devices/schedule_*`·`devices/linkage.py`
  및 해당 CLI/테스트를 수정하거나 호출하지 않는다.

## 구현 확인 기준

오프라인 단위 테스트는 31행의 시각·Feed 6분·Tide의 Frequency 미송신, 물리적 상단/하단
Flow 매핑, 한 행의 고유 ID, 중복·지연 경계 무송신, 중간 실패 후 child 재송신 없음,
예상과 다른 Slave Mode를 성공으로 오판하지 않음을 확인한다. 통합 검증은 기존
coordinator의 저널/ACK 경로를 사용하고, 실제 장비 효과는 별도 승인된 짧은 실기로
확인한다. 이 문서는 실기 성공을 선결론으로 쓰지 않는다.

## 구현된 조작 경계

새 CLI `jebao-flow-pro-manual-schedule`는 다음 네 단계를 분리한다. 명령 예시의 경로와
멤버 이름은 운영 환경에서 확인한 값으로 치환한다.

```text
jebao-flow-pro-manual-schedule preview --file <31행 JSON>
jebao-flow-pro-manual-schedule save --file <31행 JSON>
jebao-flow-pro-manual-schedule status

jebao-flow-pro-manual-schedule activate-and-run \
  --schedule-sha256 <save 결과 digest> \
  --master-member <현재 하단 Master> --slave-member <현재 상단 Async> \
  --upper-member <물리 상단> --lower-member <물리 하단> --write-enabled
```

`preview`는 파일만 읽고, `save`는 owner-only 공유 안전 볼륨에 정규화한 계획만 저장하며
장비 명령을 만들지 않는다. `activate-and-run`은 현재 행을 한 번 적용하고 다음 경계까지
프로세스로 대기한다. 재시작 뒤에는 같은 역할·물리 바인딩과 저장된 digest로 `run`
명령을 사용한다. 이미 시도한 행·child는 재송신하지 않는다. 기존 `linked-flow-schedule`과
**동시 실행하지 않는다.** `pause`는 실행 중 프로세스의 단독 잠금이 풀린 뒤에만 사용할 수
있다. 실행 중단·상태 파일만 `paused`로 바꾸는 것은 장비 상태를 원복하지 않는다.
표의 HH:MM은 해당 행의 **명령 시작 시각**이며, 여러 장비에 대한 child 송신과 확인이
그 시각에 동시에 끝난다는 뜻은 아니다.

예시 계획은 [`pro-external-manual-daily.json`](../config/examples/pro-external-manual-daily.json)이다.
MCP-70은 여기에 넣지 않는다. JSON은 이미지에 내장되지 않으므로 시간표 편집만으로
이미지를 다시 빌드할 필요는 없다. 새 실행 코드의 첫 배포에는 물론 새 이미지가 필요하다.

현재 구현에서 `active.json`의 `paused`는 자동 재활성화하지 않는다. Parent operation과
기존 child journal을 읽고 fresh pair를 확인한 뒤 다음 계획 활성화·복구를 별도 operation으로
결정한다. 별도 일정 편집 화면과 서비스 상시 배치는 아직 구현·활성화하지 않았다.
