# run: 2026-09-27-pro-reset-manual-sync-async-observation

## 질문과 범위

시간표 삭제·앱 기기 삭제·Wi-Fi 재등록 뒤, 시간표가 비고 TimerOFF인 두 Pro에서
앱의 Sync→Async 선택이 실제 장비에 적용되는지, 상단 Master를 해제한 뒤 선택하고
다시 Master로 붙이면 Async가 유지되는지 관측했다. 사용자가 현장에서 앱을 조작했고,
에이전트는 읽기 전용 raw collector와 MQTT subscriber만 실행했다.
에이전트 장비 제어 송신·source 수정·운영 서비스 교체는 0이다. 시작 HEAD는 `2d676db`다.
Wi-Fi 재등록을 모든 내부 설정이 지워진 공장 초기화의 증명으로 취급하지 않는다.

아래 판정 창은 2026-09-27 14:43:08.878543–15:01:16.084383 UTC다.
후속 raw 수집기는 40 pair를 계획했으며 이 기록 작성 시 계속 수집 중이었다.
완료된 첫 series와 진행 중인 둘째 series의 확인된 prefix를 구분한다.

## 보존 증거

원본 프레임은 private 볼륨에 보존된 **(a)**, MQTT event는 데몬의 보고를 저장한
**(b)**, 앱을 눌렀다가 Sync로 돌아왔다는 사용자의 화면 관찰은 **(c)**다.
주소·MAC·DID·비밀번호·private 절대경로·raw 파일은 저장소에 넣지 않는다.

| artifact | 확인 범위 | SHA-256 |
|---|---|---|
| `JFS-0ecff5e794f763d765cfdfcbe893d503` | 14:43:08.878543–14:52:49.978953 UTC, 20 pair 모두 accepted | `series.json`: `72fc5474610f8dc138c5c887cb65e2c2d50818f1eed6f99b4cd8a4e4893eebf1` |
| `JFS-ff563a031fe4558935efacdb4c8b8bf8` | ordinal 0–12, 14:55:04.664661–15:01:16.084383 UTC, 확인된 13 pair | `plan.json`: `34e4bce59cc55462d2101b143af88dd572934770875f46b9c2574b40771e60fd` |
| 둘째 series ordinal 5 하단 원본 frame | Async 직접 조회 응답, action 0x03 / Linkage bits=3 | `1c87517d198c6de3ba62086476bbc90d6f11e3b57f4f2263a924455622b36ea2` |
| 둘째 series ordinal 8 상단 원본 frame | 14:59:04.581368 UTC, Master 직접 조회 응답 | `1ca05608c159d9bc2fcba1cbb1329e27a54f835774cd04829e6d8e8ccffbc780` |
| 둘째 series ordinal 12 하단 원본 frame | 15:01:16.084383 UTC, Async 자동 보고 | `e17ead17bd5bfed89f9caea8b2827cd775ba42b8295eef6accc9e1b5c923341d` |
| `pro-pair-reset-linkage-20260927.01` | 새 수신 전용 MQTT 기록, 14:52:01 UTC 시작 / 15:30 UTC 종료 계획 | `plan.json`: `2f39412f01de24754d9223847697e87e2c660434da777d023028d62b1eef856f` |
| 같은 MQTT artifact `000009-events.json` | 상단 Master→Independent 최초 관측 | `636246279ce3c24d78ffe0e24eba4c6146fc99f5c3e5fd5c14d9556281731dc0` |
| 같은 MQTT artifact `000011-events.json` | 하단 Sync→Async 최초 관측 | `5dab5979ca9b92a21986c16e3453b0353ed6c1fd9765307bfdcd04fe432b1cea` |
| 같은 MQTT artifact `000015-events.json` | 상단 Independent→Master 최초 관측 | `24665806471eacec0fc68c6f8edde7dc2a4dca3630948a76f44862b1c38d1080` |

현재 raw sample의 예상 identity binding SHA-256은 상단
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`, 하단
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다.
Async 확인과 재결속 후 원본 frame의 SHA를 sample metadata와 재대조했고,
decode_frame의 status value byte 0에서 Linkage bits 2..3을 별도로 읽어 3을 확인했다.
원본 frame의 action 0x03은 조회 응답, 0x04는 자동 보고이며 제어 ACK의 대체가 아니다.

## 관측 순서

| 단계 / 보존 표본 | 상단 | 하단 |
|---|---|---|
| 첫 series ordinal 0–3 | Independent | Independent |
| 첫 series ordinal 4, 14:45:09–19 UTC | Master | Sync |
| 상단 Master 활성 중 앱 Async 선택 시도 뒤, 첫 series ordinal 5–19 | Master 유지 | sampled raw는 전부 Sync |
| 둘째 series ordinal 2–4, Master 해제 뒤 | Independent | Sync |
| 둘째 series ordinal 5–7, Master 없는 상태에서 Async 재선택 뒤 | Independent | Async, ordinal 5·6은 action 0x03 직접 응답 |
| 둘째 series ordinal 8–12, 상단 Master 재활성화 뒤 | Master | Async 유지, 14:59:15–15:01:16 UTC |

MQTT의 최초 관측은 상단 해제 14:56:03.272650 UTC, 하단 Async 14:57:25.221903 UTC,
상단 Master 재활성화 14:59:04.277112 UTC였다. 이는 수신·관측 시각이지 앱 버튼을 누른
정확한 시각이나 장비 내부 전이 순간이 아니다.

두 기기는 전 구간 SwitchON=true / TimerON=false / Mode=constant / Flow=40을 보고했다.
48슬롯은 전부 EE×9이며 432B SHA
`0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`가 유지됐다.
raw Frequency=80도 유지됐지만 Constant의 활성 주파수 설정으로 해석하지 않는다.
AutoMode=constant / AutoFlow=30 / AutoFreq=5는 비활성 보고 필드이고,
TimerOFF·0슬롯에서 자기 시간표가 실행됐다는 증거가 아니다. fault는 보고되지 않았다.

## 기록기의 실제 범위와 빈틈

기존 전량 MQTT recorder는 컨테이너가 Up이었으나 기록 deadline이 당일 08:00 UTC에
끝나 있었다. 초기 Async 버튼의 아주 짧은 전이를 전량 기록으로 잡았다고 주장하지 않는다.
30초 cadence의 원본 raw에서도 Async 표본이 없었으므로, 그 초기 시도는
“실제 Async 적용 뒤 즉시 Sync 회귀”와 “앱의 낙관적 UI 변경만 발생”을 구분할 수 없다.

14:52:01 UTC에 기존 수신 전용 스크립트를 새 artifact id로 시작했고 새 event·snapshot 파일과
수신 시각을 직접 확인했다. MQTT publish·장비 session·제어 송신 경로는 없는 recorder다.
관측 업데이트보다 짧은 전이는 여전히 놓칠 수 있다. 앱의 cloud/BLE on-wire 명령은
이 수집기의 대상이 아니므로 원인 actor나 모든 app transport를 확정하지 않는다.
사전 확인에서 자동 Timer watchdog은 중지, 반복 linked-flow live 인스턴스는 미기동 상태였다.

## 이 실행으로 새로 확정된 사실

이번 **빈 시간표·TimerOFF·Constant40** 조건에서는, 상단 Master 해제 뒤 하단 Async를
선택하면 실제 Linkage=3 조회 응답이 나왔고, 상단을 다시 Master로 세운 후에도 2분 이상
연속 관측에서 하단 Async가 유지됐다. Master 활성 중의 최초 시도와 이 순서의 관측 결과는
다르다. 이 순서가 모든 펌웨어·운전 조건에서 필요하다는 일반화는 하지 않는다.

이는 역할 적용 순서에 대한 결과다. 두 모터의 실제 반대 위상·유량은 측정하지 않았으며,
Pulse 파형이나 계획된 Feeding을 시험하지 않았으므로 기존 Pulse→Feed 정지의 원인 규명이나
해결 완료로 보고하지 않는다. 다음 파형 시험은 이 기준 상태와 분리한다.

## 부수 관측 — 자정 날짜 보고

KST 자정 직후, 둘째 series ordinal 10 하단과 ordinal 11–12의 **양쪽** raw가
날짜 2026-09-27을 유지한 채 시각을 00:00대로 보고했다. 호스트는 이미
2026-09-28 KST였다. 이번 표본에서는 하단만의 현상으로 기록하면 잘못이다.
상단 ordinal 11은 action 0x03 조회 응답, 하단은 action 0x04 자동 보고였다.
상단 ordinal 11 frame SHA는
`cf59cc71e58dddefbb7a29130b6aeea5c44e5490898b24838d9223380b53fc56`,
하단 ordinal 10 frame SHA는
`dd242c669c43e05df4dd6e5ef69049dc11b4f7a063d3ef699d7e8a2dc5a72a9b`다.
날짜 레지스터의 원천·갱신 경로와 물리 운전 영향은 이 결속 시험으로 판정하지 않는다.
시간 동기화나 시간표 재송신은 실행하지 않았다.

