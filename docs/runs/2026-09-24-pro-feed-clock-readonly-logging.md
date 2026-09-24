# run: 2026-09-24-pro-feed-clock-readonly-logging

- 목적: 하단 Pro의 Feed 정지·이탈과 상·하단 날짜 보고 차이를 다음 날 원본 기록으로 대조한다.
- 이 실행이 답하려던 질문: Feed에서 나온 사건이 자동복구 명령인지 시간표 경계인지, 날짜 차이가 직접 응답과 자동 보고 중 어디에 나타나는지 구분할 자료가 계속 남는가?
- 실행한 source HEAD: `86533e40a96ece9e6de18e61f8e61049da894d8f`. 새 source build·장비 write는 없다.
- 실행 범위: 기존 MQTT-only 전량 기록기 image·스크립트를 재사용해 별도 artifact `pro-pair-full-state-recorder-20260924.01`을 기존 private control-data volume에 시작했다. 시작 `2026-09-24T07:33:44Z`, 종료 예정 `2026-09-27T08:00:00Z`. 변화 이벤트는 최대 30초마다 fsync하고 전체 토픽 snapshot은 300초마다 보존한다. 요청·제어 MQTT publish, 장비 세션, 장비 write는 모두 0이다.
- 첫 기동 시 image의 기본 entrypoint가 daemon이어서 인자를 거부했다. 그 컨테이너는 artifact 생성 전 중지·제거했고, 명시적 Python entrypoint로 새 컨테이너를 시작했다. 정상 컨테이너는 확인 시 `running`, restart 0이었다.
- 검증: 첫 events 파일에 103개 초기 이벤트, 첫 snapshot에 98개 토픽과 Pro별 16개 function이 있었다. 두 Pro 모두 `manual_mode`와 `current_schedule_state`가 포함됐고 snapshot 파일은 owner-only `0600`, artifact 디렉터리는 `0700`이었다. plan SHA-256 `2d8bba19cc1825dd2b974cab41f757809af124f5dd35a905c498b328ad566e1f`, 첫 snapshot SHA-256 `187f7a68d1af319edaee8e8ee773c0e82e5cedce0487cde72b2aab255ed2babd`, 재사용 스크립트 SHA-256 `dd381b8b7f4c816901804091a068c84eabdd7f8b217552670c1310e8105549e7`.
- 날짜 증거는 MQTT `device_clock_sync` 토픽으로 판정하지 않는다. 첫 snapshot의 두 토픽은 9월 24일 수신됐지만 내부 `observed_at`은 9월 17일 그대로였다. 별도 source-attested raw anchor `JFS-24c71d44825cf62b735237d315c42bf2`가 300초 간격으로 양쪽 462-byte frame과 action을 보존하고, 기존 private PCAP ring은 LAN 12416/12414 프레임을 전속도로 보존한다. PCAP에는 인증 프레임이 있으므로 저장소 반출·커밋하지 않는다.
- raw anchor ordinal 72의 직접 확인: 상단 action `0x03`, YMD `[20,26,9,24]`; 하단 action `0x04`, YMD `[20,26,9,22]`; 둘 다 HMS와 fault byte가 들어 있는 452-byte status였다. 상단 frame SHA-256 `4dfcf7d62e8e36dbe21e6b9ecdb20b4d9071ef1830444555e039b071000f7a66`, 하단 `bfb2076ddbd7555c97e9298dd3d2a4a858ac82ae0e5795642a5ce4f373479d38`. source-attested plan의 expected identity binding SHA-256은 상단 `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`, 하단 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`이다.
- 자동복구 감시기의 private durable 기록은 확인 시 plan 한 건뿐이며 `repair_requested`·`repair_result`가 0건이었다. 기존 local-control operation journal은 별도 보존된다. 감시기를 수정하거나 중지하지 않았다.

## 앞선 Feed 사건의 재확인 (신규 제어 없음)

- 9월 21일 `18:08:09 KST` 하단의 `manual_mode=feed` 변화와 `AutoMode=pulse/AutoFlow45/AutoFreq70` 변화가 같은 MQTT 배치에 보존됐다. `21:00:01 KST` `AutoMode=tidal`, `21:00:15 KST` `manual_mode=tidal`로 바뀌었다. 두 event batch의 SHA-256은 각각 `1f6de0f04ec42a725b8da3bd8a983599e7aa5784b8bb2b4bd9f1eff6ce50e6a8`, `aef6521b158d39fe02566e2996528c7bfcda273a74ed24acdd1de42ce7b1cabe`이다. 증거 등급 (b).
- 종료된 private PCAP segment SHA-256 `23008455251473ff4e1ec1333a38f3c51bf876573c651c4d098bdc680076139f`를 write 없이 파싱했다. `2026-09-21T09:00Z..12:10Z`의 decoded frame 35,591개 중 LAN control action `0x01` 요청은 0개였다. 이것은 **해당 LAN 캡처에서 제어 프레임이 없었다**는 뜻이지, WAN·앱·장치 내부 사건까지 배제한다는 뜻은 아니다. 증거 등급 (a).
- 따라서 해당 시점에 감시기의 자동복구 명령이 Feed 이탈을 만들었다는 근거는 없고, 관측된 이탈은 Tide 시간표 경계와 시간상 일치한다. 물리 출력과 펌웨어 내부 선택 규칙은 이 로그만으로 확정하지 않는다.

## 날짜 출처를 위한 라우터 수동 캡처

- 홈서버의 기존 PCAP은 LAN 12416/12414만 남기므로 외부 시간 신호를 볼 수 없다. 라우터에서 두 Pro가 실제로 붙은 IoT LAN 인터페이스를 read-only로 확인했다. 잘못 고른 다른 LAN 인터페이스의 2-packet 시험은 약 1분간 완료되지 않아 해당 시험 프로세스를 종료·정리했고, 올바른 인터페이스의 같은 시험은 2패킷을 수신했다. 시험 파일은 `/dev/null`이었고 장비 write는 0이다.
- `2026-09-24T07:53:41Z`부터 두 Pro의 private-config MAC만 필터로 쓰는 수동 `tcpdump`를 가동했다. 라우터에서 패킷을 SSH stdout으로 보내고, 홈서버의 `network=none` sink가 **기존 private control-data volume** 안의 새 artifact `pro-router-netcap-20260924-01`에 `0600` PCAP으로 기록한다. 두 MAC 값·사설 주소·인증 프레임은 출력하거나 저장소에 넣지 않는다. 라우터 설정·장비 명령·장비 세션 변경 0. full snaplen, 최대 1 GB, systemd runtime 최대 36시간. 계획 SHA-256 `6ca06543d7d51797ab71f1ac36b420abbd9d92aac1b0898848a9279f311ca250`.
- 시작 검증: systemd unit active, sink `running`·`network=none`·restart 0, PCAP magic `d4 c3 b2 a1`, private 파일 mode `0600`. 245개 완전한 초기 패킷을 오프라인 파싱했을 때 기존 LAN control·discovery 외에 TCP 1883도 기록됐다. **그 포트가 시간 주입이라는 뜻은 아니며**, 내일 날짜 전환 전후의 외부 접속·메시지 시각과 raw YMD/HMS를 대조하기 위한 증거다. PCAP은 진행 중이므로 종료 전 digest를 최종 artifact digest로 주장하지 않는다.

## 이 실행으로 새로 확정된 사실

기존 전량 MQTT 기록은 9월 23일 72시간 한도로 끝났으나 새 artifact가 전량 기록을 재개했고, raw anchor·홈서버 PCAP·라우터의 두 Pro 전용 PCAP·감시기 로그가 동시에 계속된다. 날짜 토픽의 payload는 오래된 관측값이라 raw action별 기록을 써야 한다. MQTT retained 여부는 이 기록기가 보존하지 않아 단정하지 않는다.

## 이 실행으로 확정하지 못한 것

Feed 상태가 다시 발생할지, 하단 MCU의 실제 날짜가 잘못됐는지 또는 action `0x04` 보고 필드만 오래된 것인지, 시간의 최초 주입·자정 갱신·WAN/클라우드 개입이 무엇인지. 새 라우터 PCAP은 **가동 이후** 두 Pro의 라우터 인터페이스 통신을 담지만 장치 내부 MCU↔Wi-Fi 통신과 암호화된 메시지의 의미는 볼 수 없다. 다음 사건은 같은 UTC 창의 MQTT 변화·raw action/날짜·홈서버/라우터 PCAP·watchdog 기록·local-control journal을 함께 대조한다.
