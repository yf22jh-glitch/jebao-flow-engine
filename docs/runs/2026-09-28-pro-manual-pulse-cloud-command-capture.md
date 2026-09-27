# run: 2026-09-28-pro-manual-pulse-cloud-command-capture

## 질문과 범위

빈 시간표·TimerOFF·상단 Master / 하단 Async 상태에서 사용자가 상단 수동 파형을
Sine에서 Pulse로 바꿀 때, 실제 제어가 어떤 필드를 쓰고 하단이 무엇을 보고하는지 확인했다.
기존 홈서버의 12416/12414 전용 캡처로는 앱의 실제 제어 명령을 확인하지 못했으므로,
두 Pro의 MAC만 필터링한 라우터 인터페이스 전 포트 수동 캡처를 별도로 시작했다.
라우터 설정·source·운영 서비스·시간표·장비 설정을 에이전트가 변경하지 않았다.
에이전트의 장비 제어 송신은 0이며, 기존 읽기 전용 raw collector와 MQTT recorder는 유지했다.
시작 repository HEAD는 `9ad20e4`다. 동결된 devices/* 하네스는 사용하지 않았다.

명령 수신 위치는 기기 측 TCP/1883의 MQTT PUBLISH 안에 든 GAgent 원문이다.
앱→클라우드 상행 TLS 자체나 앱의 조작 화면을 해독했다고 주장하지 않는다.
관측 span은 **2026-09-27T15:26:58.460039+00:00–2026-09-27T15:41:35.566739+00:00 UTC**다.
KST 조작은 9월 28일 00:36대이며, 정확한 앱 버튼 시각 대신 아래 패킷 수신 시각을 사용한다.

## 보존 증거

원본 PCAP와 원본 raw frame은 **(a) preserved raw artifact**다.
파생 분석 JSON은 독립 증거가 아니라 해당 원본을 다시 해독한 결과다.
PCAP에는 주소·인증 정보가 포함될 수 있으므로 원본·토픽·private 절대경로를 저장소에 넣지 않는다.
원본의 종료 시점이 고정된 사본을 O_EXCL/0600 + fsync로 보존하고 실제 파일을 재해시했다.
진행 중인 traffic 전체의 최종 digest와 혼동하지 않는다.

artifact id: `pro-router-manual-wave-20260928.01`.

| 보존 항목 | 크기 | SHA-256 |
|---|---:|---|
| plan.json | 614 B | `f0584031bad00bc51d42d5233c69d03148da413f056cd239d9806ae1e239499f` |
| pulse-command-prefix.pcap | 1104145 B | `dbe14d12c0b875fd3209b36ae603649d36982b4daca35ac2fe78df15c8daffc0` |
| pulse-command-analysis.json | 30268 B | `746c4457387ab38de24bc7fb7ed9dee3fdb5e50bae8634e6322147ae4c19af3c` |
| pulse-command-analyzer.py | 17454 B | `4594091870b0188b5b92f3f1936f677d3b220b89e4a5b0b78b8bfe13d70f6be9` |

별도 원본 조회 series는 `JFS-5d625f0b015a92fb1a2024755b97b0b9`,
plan SHA `9c1fd4bf5e7bcb7f7e6ba894da933f341a50d0c2f0c60eda0e4c474dc609106d`다.
ordinal 38 상단 frame SHA `3f612571fe882244b4e2843e2636ec94fe179eec968209a6ae060bd0f3179d33`,
하단 frame SHA `ce5c9836d80948fa69befbc2548e0b390222caa15ac0a4ebda15b14d4358cad0`를
원본에서 재계산하고 action 0x03/0x04 및 70 datapoint를 디코드했다.

물리 identity binding SHA-256:
상단 `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
하단 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.

## 실제 명령과 반응

| UTC 패킷 시각 | 방향 / 의미 | 해독 결과 |
|---|---|---|
| 15:35:53.430622 | cloud→상단 조회 | 0x0093, sequence 63, action 0x02 |
| 15:35:53.849250 | 상단→cloud 조회 응답 | 0x0094, sequence 63, status 포함; 파형 제어 ACK로 혼동하지 않음 |
| **15:36:03.651899** | **cloud→상단 파형 제어** | **0x0093, sequence 64, action 0x01, 선택 id=[3], Mode=pulse** |
| 15:36:04.154240 | 상단→cloud 제어 응답 | 0x0094, sequence 64, empty ACK body |
| 15:36:04.563858 | 상단 자동 보고 | Mode=pulse / Linkage=master |
| 15:36:05.001842 | 하단 자동 보고 최초 Pulse 관측 | Mode=pulse / Linkage=async_slave |

파형 제어의 전체 GAgent frame은 473 B, payload 464 B, SN을 제외한 P0 control은 460 B다.
선택 flag는 Mode id 3 하나뿐이다. Flow·Frequency·TimerON·SwitchON·Linkage·Auto*·48슬롯의
flag는 없다. Mode의 wire code는 0이며, 미선택 필드의 zero value를 쓰기 명령으로 해석하지 않는다.
P0 control은 현재 `build_control_payload(LOCAL_WAVEMAKER_PRO, {"Mode": "pulse"})`의 결과와
바이트 단위로 동일했다. 따라서 이번 **Mode 단일 필드 명령**의 인코더 동등성은 원문으로 확인했다.

- Mode 명령 frame SHA: `24064831ee4cf1be5e8622238d9df01c5ae2a22625aea270ad5bf18b732fdcb4`.
- P0 control SHA: `912fd7e26fd0aa05ebfc1b505107b195c56853abc1894fe0c94707de0d8ea2a6`.
- sequence 64 응답 frame SHA: `82134a46a1d0d6ebbc30978e433a20d768e7f72b83e2408cdc2070e3e3b62a5b`.

보존 구간에서 하단으로 간 별도 P0 action 0x01은 0개다. 하단 cloud 조회 한 건은 action 0x02라
제어로 세지 않았다. TCP/12416과 TCP/1883를 연속 재조립했고, UDP/12414는 discovery 0x03/0x04
뿐이었다. 분석한 complete packet은 6731개이며 TCP sequence gap 0,
protocol boundary diagnostic 0이다. 새 SYN을 새 연결 세대로 처리한 뒤의 수치다.
이는 이 캡처 범위의 관측이며 RF/BLE·MCU 내부 메시지까지 배제하는 주장은 아니다.

양쪽 상태 원문의 70개 필드를 대조하면 YMDData/HMSData를 제외하고 Sine→Pulse의 **Mode만**
변경됐다. 이후 5분 이상 관측에서도 다음 보고가 유지됐다.

- 상단: SwitchON=true, TimerON=false, master, Mode=pulse, Flow=40, Frequency=80.
- 하단: SwitchON=true, TimerON=false, async_slave, Mode=pulse, Flow=40, Frequency=80.
- AutoMode=constant / AutoFlow=30 / AutoFreq=5는 비활성 값으로 불변.
- 48슬롯은 EE×9, 432 B SHA `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`.
- FeedTime·Cust_Wav_Freq·Auto*·fault 필드는 불변, fault 7개는 모두 false.
- 이 구간에는 Mode=feed 표본이 없다.

명령→응답→상태 보고 시각은 관측 시각이다. 둘의 물리 위상이나 파형 전파 지연을 측정한 값이 아니다.
설정을 되돌리는 시험이 아니며 별도 원복 명령은 보내지 않았다. 사용자가 변경한 Pulse 설정을 유지했다.

## 부수 관측 — 실제 날짜·시간 주입 명령

15:39:15.070565 / .084443 / .110803 UTC에 상단으로 별도 cloud 제어가 세 번 들어왔다.
sequence는 71/72/73이고 선택 id는 [61,62], **YMDData=[20,26,9,28],
HMSData=[0,0,39,14]**뿐이다. 각각 0x0094 응답도 관측됐다.
이 세 명령은 Mode·Linkage·Flow·TimerON을 쓰지 않았으며 에이전트의 명령이 아니다.
반복된 것은 외부 수신 원문의 관측이며 우리 데몬의 재송신으로 분류하지 않는다.

이 사실은 이 시점 상단의 날짜·시간이 외부 cloud 경로로 주입됐다는 직접 증거다.
정확한 앱 client/SDK callback, 평소 자정 갱신 원천, 하단 날짜 정체의 원인까지 확정하지 않는다.
하단으로 같은 날짜 주입 명령은 이 보존 구간에서 관측하지 못했다.

## 이 실행으로 새로 확정된 사실과 한계

**이번 빈 시간표·TimerOFF·수동 Master/Async 조건에서는, 실제 Mode-only Pulse 명령 뒤
하단도 Pulse를 보고했고 5분 이상 Feed로 떨어지지 않았다.** 따라서
“Async slave는 Pulse에서 무조건 Feed가 된다 / native Pulse는 불가능하다”는 일반화는
이 원본 관측과 맞지 않으며 사용하지 않는다.

이전 TimerON·자기 시간표가 있는 상태의 Pulse/Feeding 정지 관측을 지우거나 이번 결과로
그 원인을 해결했다고 선언하지 않는다. 자기 시간표·Timer 상태·Feeding 복귀 조건을
이번 수동 기준 상태와 별도로 비교해야 한다. 실제 물리 반대 위상·유량은 계측하지 않았다.

앱의 명령은 이 시험에서 cloud 경유였으므로 홈서버 NIC의 LAN 포트 전용 캡처만으로는 빠질 수 있다.
기존 Sine 조작의 미보존 명령을 이번 Pulse 원문으로 소급하여 증명하지 않는다.
다음 같은 질문에는 상태만 비교하지 말고 이 라우터 전 포트 원본과 원본 상태를 함께 사용한다.

