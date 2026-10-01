# run: 2026-10-01-pro-read-retry-schedule-resume

## 범위와 질문

- 목적: 사용자 `동작시키자` 요청에 따라 일시정지된 기존 GMP 외부 수동 시간표를 재개하고 현재 구간의 보고값을 확인한다.
- 질문: 배포된 실행기로 기존 31행 실행본을 재개하면 두 GMP의 Mode·Flow·고정 역할과 HA 활성 상태가 일치하는가.
- 기반 HEAD: `ae1e4bb`, 실행 image `sha256:e786e245df02c1acb6051538eafc60b1f58bc0808c3470e8fa1fb2094cdb1e4d`.
- UTC span: `2026-10-01T05:25:34.771349Z`–`2026-10-01T05:29:52.114236Z`.
- 대상은 하단 Master·상단 Async 두 GMP뿐이다. MCP·리턴·조명·도징·급여기 write 및 native 실험 하네스 실행은 없다.
- 실기 중 source 변경·재빌드·서비스 재시작은 없다. 결과 기록을 source 수정 전에 커밋한다.

## 기존 실행본 선택과 단회 활성화

초기 실행 상태는 `paused`, 마지막 검증 행은 00:00 Sine였다. fresh raw에서 하단 Master는
Sine/Flow40/F80, 상단 Async는 Sine/Flow35/F80, 양쪽 TimerOFF·전원ON·fault0이었다.
두 Pro의 미완료 child는 0개였다.

편집 화면에는 `일일 패턴 2 · Custom·Nutrient`가 선택되어 있었지만 마지막 실행본은
`기본 시간표` 31행이었다. 다른 표를 임의 활성화하지 않고 기존 실행본을 선택하는
metadata 요청 1회(`device_writes=0`)를 먼저 확인했다. 저장 내용·템플릿 library digest는
변경하지 않았다.

- 기존 template revision: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.
- 선택 요청: `bb37519990ea0e0752a2283e96bf4d7c`, 결과 `selected`.
- 활성화 요청: `1a072dd0b264a0d690569a581f6eb03c`, 발행 `2026-10-01T05:27:06.195158Z`.
- HA의 기존 MQTT publish 경로로 qos0·retain false 활성화 요청을 **1회** 보냈다. 요청 재송신은 0회다.
- 새 실행 epoch: `8a4268b0b495294d1dc07be807fd7d99860d472cccead91087c15e6e05f26f2f`.
  저장 template은 불변이며 새 epoch와 기존 실행본의 전체 row 비교가 동일했다.
- 실행 행: 14:00–14:57, `Tide Rise and Fall`, 상단45/하단40. Tide에는 Frequency 명령이 없다.

## 실행 기록 · 증거 등급 b

부모 `pro_manual_20261001_0840_8a4268b0b495294d`는 `verified`, 아래 child 4개는 모두
`VERIFIED / ack_and_readback`이다. 명령마다 single-send 경로를 사용했으며 원 명령 재전송,
ACK 없는 성공 승격, 자동 복구 명령은 없다.

| 순서 | 대상·변경 | child journal SHA-256 |
|---|---|---|
| 1 | 하단 Master 역할 해제 | `1aefbf1ef563649fbbef5c78e3a37052091bf39c1d3782e58966225ad78eecfa` |
| 2 | 상단 slave 공통 Flow45 | `dd5efb9b6d3945ce7f161c35531125bca95debf94117d6440f7e2813ccbedbe3` |
| 3 | 하단 Mode=Tide | `ce643201d9f2af30f00144fff6269a03bd9a88def3771b8a5029b08311ae0ab0` |
| 4 | 하단 Master 재결속 | `2920522ae17e5e2d421eb98e7f5e2555d3fb0f8606acdb5722e5fa8b85ceca83` |

- child 저널 생성/종결 구간: `2026-10-01T05:27:30.203338Z`–`2026-10-01T05:28:11.783702Z`.
- 기존 control 저널 248개는 모두 digest 불변, 이후 252개. scheduler operation은 47→50개(선택·활성화·현재 행).
- 미완료 Pro child는 0개다. `ACK` body는 기존 계약의 empty body이며 digest를 실제 blob으로 재확인했다.
- artifact `pro-read-retry-resume-20261001T052541Z` 안에 전후 감사·새 child 저널 4개·부모 저널을 보존했다.
  - `before.json`: `ea1f235d8c6ba05f2de9a4d147a49e444041c3ed7d2843c7c881ab0b142a55a1`.
  - `after.json`: `fb285b1c64464e40b32579c7b6e82cdf5158ec29e767aa4cf47c1f2ec9a928b0`.
  - `row-operation.json`: `e9ff4e5acd5e03816a538ce1a1b3aed0b4b1a0179cacef534736d2510a776cad`.

## 재개 이후 fresh 장비 보고 · 증거 등급 a

별도 상태 조회 `2026-10-01T05:29:44.788624Z`–`2026-10-01T05:29:52.114236Z`의 실제
462-byte 원본 frame 두 개를 저장하고 CAS와 파일 digest를 다시 읽어 확인했다.

| 물리 역할 | 보고값 | identity binding SHA-256 | raw SHA-256 |
|---|---|---|---|
| 하단 Master | action03·master·TimerOFF·ON·Tide·Flow40·F80·fault0 | `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea` | `d0ab9c1bcf324b83bfe022127fc5f4406799a24e2c111bd7b9ad9dfd35039d8e` |
| 상단 Async | action04·async_slave·TimerOFF·ON·Tide·Flow45·F80·fault0 | `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c` | `4ad176731576de1b3632e7574c8dd12c0792a64dd7ffbea7abeb2f35e34cde05` |

같은 artifact의 `master-state.bin`·`slave-state.bin`으로 보존했다. action04는 Async 장비의
실제 상태 report이며 explicit03 reply로 바꾸어 기술하지 않는다. F80은 기존 잔여 수동값이고
이번 Tide 행에서 F80을 보낸 것은 아니다. 장비 자체의 두 432-byte schedule image digest는
하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`,
상단 `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`로 전후 동일하다.

## HA·최종 판정

- HA editor: `active`, `current_row_verified=true`, `last_error=null`.
- 공개 상태: `health=normal`, `runner_running=true`, 같은 Tide/45·40 구간과 고정 역할.
- 다음 경계: `2026-10-01T14:57:00+09:00` Feeding. 종료 시각은 기존 표의 15:03이다.
- HA 상태·요청 응답도 private artifact로 보존·재읽음했다(등급 b).
  - `activate-result.json`: `2b1860b8f7edb900d26f26512d48a4162c89b04daa62b40568b40eb6a6ab4962`.
  - `ha-after.json`: `d14fd904d2fff030a837476f1b61008ba6a2f39721b61cace34e2a28c2256b4b`.
- template library·기존31행·장비 image는 불변, 원복 요청은 없으므로 재개한 상태를 유지한다.

**이 실행으로 새로 확정된 사실:** 배포된 실행기로 기존 GMP 31행 시간표가 재개됐고 현재
Tide·상단45/하단40·Master/Async·TimerOFF·fault0 보고 및 HA 정상 표시를 확인했다.

**미검증 범위:** 이후 14:57/15:03 경계와 하루 전체 운전, 실제 ACK 누락 시 재시도 분기,
물리 수류·위상. 이번 활성화의 정상 ACK 4개로 이 범위까지 PASS로 확장하지 않는다.
