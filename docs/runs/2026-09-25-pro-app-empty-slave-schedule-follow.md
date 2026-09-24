# run: 2026-09-25-pro-app-empty-slave-schedule-follow

## 질문과 범위

사용자가 현장에서 제바오 앱으로 두 Pro의 결속을 풀고 다시 연결할 때, 하단 시간표가 바뀌는지,
하단 시간표를 비운 뒤에는 상단 파형과 하단 Flow가 어떻게 보고되는지 확인했다. 사용자가 앱 조작과
물리 관찰을 맡았고, 에이전트는 기존 수집기와 추가 읽기 전용 raw 수집기만 사용했다. 에이전트의
장비 제어 write, 운영 서비스 교체, source 변경은 0이다. 시작 repository HEAD는 `965256d`다.

실행은 2026-09-24 14:11:45–15:12:43 UTC에 이어진 현장 관측이다. 앱 조작이 여러 번 있어
각 상태 전환을 아래에서 구분한다. 활성 Pro 앱 template은 `c1ed7f76…`이며, 다른 제품의
`zaolang` template을 근거로 쓰지 않았다.

## 보존 증거

| 증거 | UTC span / 결과 | SHA-256 |
|---|---|---|
| raw series `JFS-d2aedbf477925d33e30b7c15eaa6e6db` | 14:11:45–14:38:13, 72/72 pair accepted | `series.json` `7e1be02142640fb9d4f3568e1c7333ea5d992689581a85fe4683d5b312eba1b5` |
| raw series `JFS-d79f826368dc343b82a70e9d12765c29` | 14:39:35–15:12:43, 90/90 pair accepted | `series.json` `ff68af3b3e8f6590fbae0aa094e898aaa611c57e4c762cddbeeced595bc1acd7` |
| 상단 원 432B image | 초기·최종 동일 | `118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9` |
| 하단 원 432B image | 리셋 전 별도 보존, 31개 사용 슬롯 | `aaa0f30824d4e2b15e3eeb9e948e665ba5b63562330e8fc0aa20a2a20af22b6f` |
| 하단 리셋 뒤 432B image | 48개 슬롯 전부 `EE×9` | `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87` |
| 전량 MQTT 변화 event `000888-events.json` | 14:59:54 UTC의 역할·Mode·Flow 변화 | `db47a0cb485be0bf2a77480c3b245199df19a4f5a6ff5822835def9229ac260b` |

두 raw series의 예상 physical identity binding SHA-256은 상단
`97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`, 하단
`4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`다. 실제 MAC·DID·주소,
private 절대경로, raw frame은 저장소에 넣지 않았다. 위 raw는 원본 프레임이 보존된 **(a)**,
전량 MQTT event와 ingress heartbeat는 daemon의 보존 주장인 **(b)**, 사용자의 “하단 모터가 돌고
앱에 Sine/F80/Flow40이 보인다”는 물리·화면 관측인 **(c)** 로 구분한다.

## 앱 조작별 전이

첫 비교에서 앱 결속 해제·재결속만 했을 때 raw ordinal 27의 상단 Master/하단 Async가 ordinal
28·31의 양쪽 Independent를 거쳐 ordinal 33·39의 Master/Async로 돌아왔다. 두 시간표는 각각
31개 사용 슬롯이며 432B가 전 구간 byte-exact였다. Auto와 수동 설정도 역할 외에 바뀌지 않았다.
그 앞의 첫 시도에서는 상단이 잠시 Async로 보고된 ordinal 20이 있어, 이 과도 상태를 깨끗한
두 번째 비교의 결과와 섞지 않았다.

사용자가 하단 시간표를 리셋한 뒤에는 다음 raw가 보존됐다. 관측 간격 안에 여러 앱 동작이
있으므로 개별 버튼 하나에 모든 동시 변경을 귀속하지 않는다.

| raw ordinal / UTC | 상단 | 하단 |
|---|---|---|
| 67 / 14:36:30–41 | Master, 31슬롯 | Async, 31슬롯, 전원·타이머 ON, Auto Constant/Flow40 |
| 68 / 14:36:51–14:37:03 | Independent, 원 image | Independent, **0슬롯/전부 EE**, 전원·타이머 OFF, AutoFlow30 |
| 69 / 14:37:14–25 | Independent, 원 image | Async, 0슬롯, 전원·타이머 ON, Auto Constant/Flow30 |
| 70–71 / 14:37:36–14:38:08 | Master, 원 image | Async, 0슬롯 불변, 전원·타이머 ON |

이후 사용자가 휴대폰에서 다시 결속을 풀고 하단을 Sine과 다른 수동 파형으로 바꿨다. 두 번째
series의 원본 raw와 전량 MQTT 변화 기록은 다음처럼 일치한다.

| raw ordinal / UTC | 상단 Linkage / Auto | 하단 Linkage / TimerON / manual | 하단 image |
|---|---|---|---|
| 51 / 14:58:26–37 | Master / Sine35·F80 | Async / OFF / Sine40·F80 | 0슬롯 |
| 52 / 14:58:48–59 | Independent / Sine35·F80 | Independent / OFF / Sine40·F80 | 0슬롯 |
| 53 / 14:59:10–21 | Independent / Sine35·F80 | Independent / OFF / **Constant45·F80** | 0슬롯 |
| 54 / 14:59:32–43 | Independent / Sine35·F80 | Async / OFF / **Constant40·F80** | 0슬롯 |
| 55 / 14:59:54–15:00:05 | **Master** / Sine35·F80 | Async / OFF / **Sine40·F80** | 0슬롯 |

하단 raw status byte 비교에서도 53→54는 `Linkage`와 `Flow 45→40`, 54→55는 `Mode
constant→sine`(시계 바이트 제외)뿐이었다. `AutoMode=constant`, `AutoFlow=30`, `AutoFreq=5`,
빈 image, 전원 ON, TimerON OFF와 fault 0은 유지됐다. 전량 MQTT event는 14:59:54.047–.058 UTC에
하단 Mode·Flow 변화와 상단 `independent→master`를 같은 관측 주기로 기록했다. **Mode 전환은
자정 6초 전에 이미 기록됐으므로 자정 경계만의 효과로 설명할 수 없다.** 마지막 raw ordinal 89까지
두 역할과 하단 Sine40·F80은 유지됐다. 첫/마지막 raw status의 시계 필드를 제외하면 양쪽 모두
byte-exact 원상태다. 다만 이것은 **리셋 뒤의 실험 기준**에 대한 복귀이며, 하단 원 31슬롯
시간표로 복원했다는 뜻은 아니다.

## 송신·코드 교차 확인

- 14:58–15:01 UTC의 local-control durable journal에는 새/수정 operation 파일이 0개다.
  수신 전용 MQTT ingress에는 status 13건만 있고 `/set`·`/request`는 0건이다. 전후 heartbeat는
  `connected=true`, 오류 0이었다. 따라서 이 창의 하단 Mode·Flow 변화는 운영 local-control
  명령으로 설명되지 않는다.
- 활성 Pro template의 `onSelectLinkage(e)`는 `sendCmd({Linkage:e})` 단독이다. 같은 화면의
  slave Flow 슬라이더만 별도로 `{Flow:e}`를 보낸다. 앱 host/SDK의 실제 cloud·BLE payload는
  보존되지 않았으므로 template 이상에서 app의 모든 내부 경로를 단정하지 않는다. LAN pcap의
  14:58:30–15:00:15 UTC 창에서는 완전한 `0x0093` control frame을 찾지 못했다. 이는 LAN
  경로에 대한 관측이지 cloud·BLE 명령 부재의 증명은 아니다.
- 현재 `wavemaker_lifecycle`의 flow-carrier attach는 하단 432B Constant carrier image를
  먼저 쓰고 하단 `TimerON=true`를 세운다. 이번 앱 관측의 최종 상태는 **하단 0슬롯·TimerON=false**라
  서로 다른 topology다. 역할 명령의 필드 shape는 앱과 맞지만, 이 차이 때문에 **우리 조립
  방식이 Pulse→Feed 문제에 무관하다고 결론낼 수 없다.** 하단 per-slot `AutoFlow`를 이미지에
  넣는 것이 linked 상태의 실제 물리 Flow를 소유하는지도 이 실험으로 증명되지 않는다.
- `wavemaker_sequence.staged_controls`의 주석은 하단 정지 원인을 `TimerON` 상실로만 적는다.
  선행 9월 22일 A/B와 9월 24일 raw에는 `TimerON=true`인 채 하단 manual `Mode=feed`가
  보존됐고 사용자는 모터 정지를 관찰했다. 이 주석은 관측된 모든 정지 상황을 설명하지 못한다.

## 판정과 남은 질문

**이 실행으로 새로 확정된 사실:** 앱 결속만으로 저장된 두 432B 시간표는 바뀌지 않는다.
하단 image가 완전히 비고 TimerON이 OFF여도 Async 결속 뒤 펌프는 사용자 관측상 회전했고,
상단 Master 결속 구간에서 하단의 manual Mode 보고가 Constant→Sine으로 바뀌었다. 하단 Flow는
상단 활성 슬롯 Flow35가 아닌 40으로 보고됐고, Async 진입 시 독립 상태의 Flow45가 40으로
되돌아갔다. 다만 40은 상단 manual Flow와 하단의 이전 manual Flow가 같아 어느 값을 가져왔는지
분리되지 않는다.

선행 [Pulse independent A/B](2026-09-22-pro-pulse-independent-causal-ab.md)와
[31구간 raw 전수 대조](2026-09-24-pro-feed-clock-ingress-correlation.md)에서는 결속된 상단
Pulse 218표본 중 하단 manual Feed가 217개였고, 그중 하단 자기 AutoMode가 Constant인 45개도
포함됐다. 이번 Sine 결과는 linked manual Mode 전달을 재현하지만, **하단 빈 image·TimerOFF에서
상단 Pulse가 Feed가 되는지**, 실제 유량·180° 위상, linked slave의 별도 Flow 지속성은 답하지
못한다. 따라서 “펌웨어가 항상 Pulse를 Feed로 만든다”와 “우리 topology는 무관하다” 둘 다
이번 증거만으로 선언하지 않는다. 다음 판별은 다른 조건을 고정한 짧은 Pulse 관측이어야 하며,
새 장비 조작은 이 기록에 포함되지 않는다.

현재 하단은 **0슬롯·TimerOFF**이므로 원래 9개 Feeding 시간표가 활성이라고 말할 수 없다.
보존한 원 432B image의 자동 재송신이나 원복은 하지 않았다. 사용자의 물리 관찰 외에 실제
회전수·유량·위상 측정도 없다.

관측 종료 뒤 2026-09-24 15:19:56–15:20:07 UTC의 기존 5분 raw anchor
`JFS-24c71d44825cf62b735237d315c42bf2` ordinal `000165`도 accepted였다. 상단은
Master·TimerON·Auto Sine35/F80·31슬롯·fault 0, 하단은 Async·TimerOFF·manual
Sine40/F80·0슬롯·fault 0으로 유지됐다. 원본 frame SHA-256은 상단
`7a50070bf0d1f033710117dc973d325910b15f2855207e65489aa755fd509780`, 하단
`b31dee2804977a489ded0f8cf18d1f9b228f5f1d5ee2513fb8778067d323a2d4`다.

## 추가 판독 — 리셋 전 31슬롯 raw 전수

기존 raw anchor 두 series의 2026-09-22 13:39:24–09-24 14:34:56 UTC에서, 상단 Master·하단
Async이고 하단 image가 원 31슬롯 SHA `aaa0f308…`인 accepted pair **589개**를 다시 셌다.
상단 `AutoMode`와 하단 manual `Mode`의 교차표는 Sine→Sine 366, Tidal→Tidal 159,
Pulse→Feed 45, 의도된 Feeding→Feed 18, Pulse→Tidal 1이었다. 마지막 한 건은 선행 기록의
14:00 슬롯 경계 직전 표본이다. 상단 `AutoFlow`는 Sine 구간에 35·37·38·40, Tidal 구간에
40·42·43·45, Pulse 구간에 40·42·43으로 달라졌지만, 같은 pair들에서 하단 manual `Flow`와
non-feed `AutoFlow` 보고는 40으로 유지됐다. 이 표는 **상단 파형 변화와 하단 manual Mode
보고의 반복 관계**를 보여준다. 하단 물리 유량의 슬롯별 독립 적용이나 Pulse→Feed의 내부
펌웨어 분기까지 증명하지는 않는다.
