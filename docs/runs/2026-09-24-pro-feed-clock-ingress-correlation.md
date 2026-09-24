# run: 2026-09-24-pro-feed-clock-ingress-correlation

## 질문과 범위

하단 Pro가 계획된 Feeding 종료 뒤에도 `Mode=feed`를 보고한 원인이 새 제어 요청인지, 저장된 시간표의 경계·Async 결속과 반복적으로 연관되는지, 하단 날짜 필드의 갱신이 어떤 상태에서 멈추는지 기존 원본과 대조했다. `2026-09-24-pro-feed-clock-readonly-logging`의 연속 관측에 더해 로컬 MQTT 제어 입력·결과를 보존하는 **수신 전용** 기록기를 추가했다. 장비 제어·앱 조작·소스 데몬 교체는 0이다.

- 조사 시작 repository HEAD: `78f05fe`.
- 새 기록기: `monitoring/local_mqtt_ingress_audit.py`, SHA-256 `d2eb8c238017cb68340c3c0b20644b2f0980e3a7563271876a1635f731c2f076`.
- private artifact id: `pro-local-mqtt-ingress-20260924-01`; plan SHA-256 `8428e8d4dd9b10f692d140b4d0e542be803293ae965f8fe8e9300caf32a3fb7c`.
- 시작 약 `2026-09-24T10:03:53Z`, 예정 종료 `2026-09-30T00:00:00Z`. owner-only 디렉터리 `0700`, 파일 `0600` 및 파일별 fsync. 구독은 `local/#`이며 `/set`, `/request`, `/preview`, 요청 `/result`, daemon/group `/status`만 보존한다. 수신 시각·topic·원본 payload·SHA-256·retained/dup/QoS를 private 볼륨에 기록한다. 발행·장비 세션·장비 write 0. 시작 직후 상태 메시지의 retained 여부와 이후 수신, 연결 오류 0을 확인했다.
- 기존 전량 MQTT 상태 기록기, 5분 간격 raw anchor, 홈서버 LAN PCAP, 두 Pro 라우터 PCAP, watchdog와 local-control journal은 계속 유지했다. 새 기록기는 **상태 기록기의 누락 축인 로컬 입력·결과**를 보완한다. 앱/클라우드 암호화 명령과 장치 MCU 내부 전이는 직접 읽지 못한다.

## Feeding 뒤 하단 `Mode=feed` 반복

9월 22일 `13:36:30–13:38:22Z`에 적용된 현재 31구간 시간표는 상단에 `12:03–13:27` 및 `13:33–14:00 KST` Pulse를 다시 포함한다. 하단은 같은 경계의 non-feed 슬롯을 Constant carrier로 가진다. 이는 선행 35구간의 Pulse→Tidal 교체 뒤 사용자가 새로 승인한 시간표이며, 이전 교체가 계속 활성이라는 가정은 잘못이다. 현재 raw image SHA-256은 상단 `118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`, 하단 `aaa0f30824d4e2b15e3eeb9e948e665ba5b63562330e8fc0aa20a2a20af22b6f`로 그 31구간 계획과 각각 같다.

- **9월 23일:** 보존된 전량 MQTT 상태 이벤트에서 하단 `manual_mode`는 `02:57:13Z` Tide→Feed, `05:00:14Z` Feed→Tide로 변해 약 **123분** Feed였다. 계획된 Feeding은 `02:57–03:03Z`, `04:27–04:33Z` 각 6분뿐이다. `03:03Z` 이후 상단 Auto는 Pulse, 하단 Auto는 Constant/Flow40인데 하단 수동 Mode는 Feed로 남았다. 이는 등급 (b) 상태 기록이며 물리 출력 자체의 계측은 아니다.
- **9월 24일:** `JFS-24c71d44825cf62b735237d315c42bf2` raw anchor에서 `02:59:56Z` 하단은 Feed/AutoFeed, `03:04:56Z`에는 **Mode=feed·AutoMode=constant·AutoFlow40·TimerON=true·async_slave**, 같은 시각 상단은 Auto Pulse/Flow40이었다. `04:54:56Z`에도 하단 Mode Feed, `04:59:56Z`에는 Tide로 바뀌었다. 즉 같은 31구간의 Pulse 창에서 다음 날도 반복됐다. 원본 하단 raw SHA-256은 ordinal `000018`에서 `5db9232df8f6efcf73bbe4075d5a625f622b79e3882f678c1c1c7bee111a1530`, ordinal `000042`에서 `9529c6f05317e121370745ec52f3c389ec6a9e292b315bae37d92deec5310fbd`이다. 등급 (a).
- 두 날짜의 `02:45–05:10Z` 창에서 local-control operation 파일은 추가되지 않았다(해당 저장소의 최종 수정 `2026-09-22T13:38:46Z`). 보존 LAN PCAP을 오프라인 재조립했을 때 9월 23일 창에 27,248 decoded frame, 9월 24일 창에 27,507 decoded frame이 있었고, **디코드 가능한 action `0x01` 제어 프레임은 둘 다 0**이었다. 파일 시작부터 다시 재조립해도 각각 해당 창의 action `0x01`은 0이었다. 9월 23일 닫힌 PCAP segment SHA-256은 `e37fe83af4677c9feba4693d20100132ed90be0d65eebef43d7698fd1ef6fb96`이다. TCP desync가 있는 캡처와 앱/클라우드 경유 명령 가능성 때문에 “모든 외부 명령 0”이라고 확장하지 않는다.
- 9월 23일 하단 availability가 `04:47:04–04:47:19Z`, `05:05:30–05:05:45Z` 잠시 offline이었다. 모두 `02:57Z` Feed 시작 **뒤**이며 첫 offline만으로 시작을 설명하지 못한다. 현재 근거에서 제어 코드가 그 시간에 Feed 명령을 보냈다는 증거도 없다.

운영 상태의 직접적인 반복 조건은 **Feeding 6분 뒤 상단 Pulse + 하단 Async/Constant carrier**다. 이때 하단 보고의 `AutoMode=constant`와 `Mode=feed`가 갈라진다. 두 필드 중 펌웨어가 실제 모터 구동에 어느 것을 적용하는지는 raw만으로 판별되지 않는다. 사용자의 “컨트롤러 Feed·모터 정지” 현장 관측은 별도 (c)이며, 이 구분을 유지한다. 펌웨어 내부 원인이나 모든 Pulse의 일반 법칙은 이 자료만으로 확정하지 않는다.

## 날짜 필드의 반복 양상

두 series의 5분 raw anchor를 전수 비교했다. 상단 action `0x03` 날짜는 KST 자정 뒤 `09-21→22→23→24`로 매일 증가했다. 하단은 linked action `0x04`에서 `09-21`을 9월 22일 `06:34:35Z`까지 유지하다, `06:39:35Z` independent/action `0x03` 표본에서 `09-22`를 보고했다. 9월 22일 `07:09:37Z` 다시 async/action `0x04`로 관측된 뒤 **9월 24일 `10:10:07Z`까지 날짜 `09-22`를 유지**했다. 그 사이 하단 HH:MM:SS는 계속 증가하고 시간표 경계도 전환했다. 즉 날짜와 시각/시간표를 같은 “시계 정상/고장” 한 단어로 묶을 수 없다.

하단 날짜가 linked `0x04` 보고 레지스터에서만 오래되는지, 실제 내부 RTC 날짜도 멈추는지, 최초/자정 동기화에 Wi-Fi/클라우드가 개입하는지는 미확정이다. 9월 24일 `07:53Z` 시작한 라우터 수동 PCAP에서는 조사 시점까지 UDP/123 NTP 패킷 0개였지만, 시작 이전·다른 전송·암호화된 시간 주입·내부 버스는 배제하지 못한다. 다음 KST 자정의 raw action별 YMD/HMS·라우터 통신·MQTT 입력을 같은 UTC 창에서 대조한다.

## 다음 판별과 기록 한계

1. 계획된 6분 Feeding과 **계획 밖 장시간 Feed**를 별개 사건으로 분류한다. 새 상태 기록과 raw anchor로 시작/이탈, 상단 Auto, 하단 Auto·수동 Mode, 역할, TimerON, image digest와 날짜를 결합한다.
2. 같은 창의 로컬 MQTT 요청/결과·저널·watchdog·LAN/라우터 패킷을 대조해 **우리 제어, 앱/외부 경유, 명령 없이 일어난 상태 전이**를 분리한다. 새 MQTT 기록기는 설치 이후만 소급 가능하다.
3. physical motor 구동, MCU↔Wi-Fi 내부 메시지, 암호화된 클라우드 payload는 이 기록 세트로 직접 증명할 수 없다. 그런 부분은 UNKNOWN으로 남긴다.

## 이 실행으로 새로 확정된 사실

현재 31구간 계획이 상단 Pulse를 다시 포함했고, 같은 계획이 활성인 이틀 연속 하단은 정해진 급여 종료 뒤에도 상단 Pulse 창 동안 `Mode=feed`를 보고했다. 9월 24일에는 같은 창의 원본 raw에서 하단 `AutoMode=constant/Flow40`, TimerON·Async와 image exact가 함께 보존됐다. 하단 날짜 필드는 Independent/action `0x03`에서 날짜를 갱신한 뒤 Async/action `0x04`에서 다음 두 자정을 지나도 그대로였다. 로컬 MQTT 입력·결과 기록 공백은 새 수신 전용 기록기로 닫았다. 다만 펌웨어 내부 선택 규칙과 날짜의 최종 시간원은 아직 확정하지 않았다.

## 후속 원본 전수 대조 (같은 읽기 전용 실행)

두 raw-anchor series의 유효한 master/async_slave·양쪽 TimerON pair를 전수 교차했다. 상단 AutoMode가 Pulse인 218개에서 하단 manual `Mode=feed`는 **217개**였다. 그 217개 중 하단 자기 AutoMode가 Pulse인 표본은 172개, **Constant인 표본도 45개**였다. 나머지 한 표본은 9월 24일 `04:59:56Z`의 14:00 경계 직전으로, 하단 보고 Mode가 Tide로 먼저 바뀌고 상단 AutoMode는 다음 5분 표본에서 Tide가 됐다. 같은 조건의 상단 Sine 314개에서는 하단 Mode=Sine, 상단 Tide 369개에서는 하단 Mode=Tide였다. 이 교차표는 **하단 시간표를 Constant carrier로 만드는 것만으로 상단 Pulse 중의 Feed 보고가 없어지지 않는다**는 관측 근거다. 실제 위상·유량 계측이나 펌웨어 내부 분기 증명은 아니다.
