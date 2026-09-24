# run: 2026-09-24-pro-feed-date-logging-code-audit

- 목적: 하단 Pro의 계획 밖 `Mode=feed`와 linked 날짜 불일치가 다시 생길 때, 상태 전이·로컬 명령 입력·기록기 공백을 같은 UTC 창에서 구분할 수 있게 읽기 전용 증거를 보강한다.
- 미확정 질문: 하단 Feed 진입·이탈이 우리 로컬 제어 명령, 외부 명령, 또는 저장된 시간표/Async 결속 상태 전이 중 무엇과 동시 발생하는가? 하단의 오래된 날짜는 어떤 raw action·역할·외부 통신 조건에서만 나타나는가?
- 시작 HEAD: `d5a05c4`. 장비 제어 source image 교체, 제어 MQTT publish, 앱 조작, 장비 write는 모두 0. 기존 운영 시간표·역할은 바꾸지 않았다.
- 적용한 진단 코드는 `monitoring/local_mqtt_ingress_audit.py`의 생존 heartbeat와 새 `monitoring/pro_pair_diagnostic_audit.py`다. 두 코드는 기존 private control-data volume에만 기록한다. 로컬 모듈 변경이 운영 제어 daemon에 들어간 것은 아니며, 수신 전용 별도 컨테이너로 실행했다.
- 원복 대상: 장비 설정·시간표를 변경하지 않아 없음. 구버전 수집 컨테이너만 대체했고 그 산출물은 보존했다.

## 로그 공백과 조치

1. 이전 MQTT ingress 기록기는 요청이 전혀 없으면 새 파일도 없어서, 기록기가 살아 있는지 파일 시각으로 판단할 수 없었다. 새 버전은 구독 연결 상태·마지막 메시지 수·오류 종류를 60초 heartbeat로 별도 fsync한다. 연결 실패 heartbeat는 `connected=false`이며, 조용한 정상 구간에도 heartbeat는 계속된다. 메시지 본문은 기존대로 수신한 `/set`·`/request`·`/preview`·`/result`와 status만 private 파일에 보존한다.
2. 전량 상태 기록기는 변동을 남기지만 Feed·날짜 사건을 바로 골라 보여 주지 않고, 72시간 만료 후 프로세스가 살아 있는 것처럼 보일 수 있다. 새 Pro pair index는 두 Pro의 power·TimerON·Linkage·manual Mode/Flow/Frequency·Auto 상태·시간표 날짜·fault를 묶어 `slave_manual_feed`, `feed_outside_master_feeding`, `reported_date_mismatch`, `telemetry_fresh`의 진입·이탈을 즉시 fsync한다. `schedule/state`의 fresh `device_local_time`을 사용하고, 오래된 retained `device_clock_sync` 버튼 상태는 날짜 근거로 사용하지 않는다. 받은 payload의 digest와 원본 base64는 private artifact에만 둔다.
3. Pro index heartbeat는 전량 상태 기록기의 최근 snapshot과 ingress 기록기의 최근 **연결 heartbeat**를 검사한다. 최근 오류 파일이나 우연히 수신된 명령 파일을 생존 증거로 오인하지 않는다. 반면 raw-anchor·LAN/라우터 PCAP은 별도 볼륨/프로세스이므로 이 heartbeat가 그들의 생존을 증명한다고 주장하지 않는다; 이번 실행에서 별도로 상태와 최신 기록 시각을 직접 확인했다.

## 시작·검증 증거

- ingress v2 private artifact id: `pro-local-mqtt-ingress-20260924-02`. source SHA-256 `dabcf4e65304161b7e17020a054c563709682bc5c89a344a3c5779328200c22b`; plan SHA-256 `9b9b839a40ebc7e82c50bc9daf7ad55d970d3dbdd6151880cab6fc4ab05d0c05`. 첫 수집 `2026-09-24T11:10Z` 무렵, 종료 예정 `2026-09-30T00:00Z`. 시작 확인: `running`, restart 0, 연결 heartbeat `connected=true`, 메시지·heartbeat 파일 증가, 연결 오류 0.
- Pro pair index v3 private artifact id: `pro-pair-diagnostic-20260924-03`. source SHA-256 `fe5b41fa0ec1c0151510ba39d9c47482877cc1de4b98e207a7e2888811fb0606`; plan SHA-256 `4d5639a780a181bd55f1faefdb40411678d1bec60bed7cd4900e436a3f3e1e55`. 첫 수집 `2026-09-24T11:10Z` 무렵, 종료 예정 `2026-09-30T00:00Z`. `11:11:40Z` heartbeat에서 `telemetry_fresh=true`, `reported_date_mismatch=true`, `slave_manual_feed=false`, 두 companion source health 모두 fresh였고 restart 0이었다. 이는 MQTT 발행 증거 (b)이며 raw frame·MCU 내부 날짜·실제 로터 회전의 증거는 아니다.
- 구버전 진단 index 2개와 ingress 1개는 새 버전의 수집을 먼저 확인한 뒤 **중지**했다. 구버전 private 산출물과 컨테이너는 삭제하지 않았다. 운영 daemon·watchdog·전량 상태 기록기·raw-anchor·LAN/라우터 PCAP은 그대로 유지했다.
- 확인 당시 raw-anchor 최근 JSON은 약 118초 전, 라우터 PCAP 최근 packet file은 약 1초 전 갱신됐다. raw-anchor와 LAN PCAP은 `running`, 라우터 sink도 `running`이었다. PCAP 원본은 인증·사설 주소를 포함하므로 저장소로 반출하지 않았다.
- 새 MQTT index는 논리 멤버 이름을 구독할 뿐 물리 identity를 독립 검증하지 않는다. 실제 장비의 SHA-256 identity binding은 [앞선 raw-anchor 실행](2026-09-24-pro-feed-clock-readonly-logging.md)에 귀속된다. 계속 쓰이는 artifact의 최종 digest는 종료 후에만 계산할 수 있어 이번 시작 기록에는 계획 digest만 싣는다.
- 로컬 순수 테스트 14건 PASS, 변경 4파일 Ruff clean, `py_compile`·`git diff --check` PASS. 테스트는 기록 분류·파일 생존 판단·무명령 heartbeat를 검증하며, 펌웨어 동작을 가정한 fake 실기는 아니다.

## 운영 코드 리뷰

- `wavemaker.compile_flow_carrier_images`는 하단 non-feed 슬롯을 Constant로 인코딩했고 활성 하단 raw image도 그 결과와 일치한다. 따라서 이번 계획 밖 Feed가 **하단 이미지에 Pulse를 잘못 복사해서** 생겼다는 가설은 현재 image와 맞지 않는다. 그러나 상단 Pulse 동안 하단 `AutoMode=constant`와 manual `Mode=feed`가 공존한 원인은 아직 펌웨어 내부·외부 경로 중 하나로 확정할 수 없다.
- `wavemaker_sequence.staged_controls`의 주석은 “하단 TimerON 상실이 펌프를 세운다”는 과거 단일 장애 해석을 담고 있다. 현재 보존 raw는 하단 `TimerON=true`에서도 Feed를 보고하므로 그 주석을 현재 일반 원인 설명으로 쓰면 안 된다. 실제 TimerOFF만 보는 별도 watchdog 역시 이번 Feed 사건을 자동복구했다고 볼 근거가 없다. 제어 코드·동결 하네스를 이 읽기 전용 실행에서 변경하지 않았다.
- `local_control/state.py`는 available 버튼 상태를 매 poll 재발행하지 않아 `device_clock_sync/state`에 과거 진단값이 retained로 남을 수 있다. 날짜 분석에서는 `schedule/state`와 action이 보존된 raw를 사용한다. 이것은 화면의 증거 선택 문제이지, 하단 raw 날짜가 낡은 원인으로 입증된 것은 아니다.
- `local_control/schedule.py`의 범용 slave Flow 갱신기는 master image의 Mode 등 non-Flow 바이트를 복사한다. 장래 그 별도 경로가 활성화되면 Constant carrier를 Pulse로 되돌릴 수 있으나, 현재 배포 설정에는 해당 per-slot schedule pair 라우트가 없어서 이번 사건의 실행 경로로 보지 않는다. 활성화 전에 경로 충돌 검토가 필요하다.

## 이 실행으로 새로 확정된 사실과 한계

명령이 없는 구간의 MQTT ingress 기록기 생존 여부와 Feed·날짜 전이 직전후 MQTT 표본의 상호 관계를 별도 private 기록으로 확인할 수 있게 됐다. 시작 시 하단 manual Feed는 아니고 날짜 불일치는 지속 중이었다. **Feed·날짜의 최종 원인은 아직 확정되지 않았다.** `schedule/state`는 장비 상태의 데몬 발행이며 raw action 구분이 없고, LAN/라우터 PCAP도 암호화된 클라우드 payload와 MCU↔Wi-Fi 내부 메시지는 해석하지 못한다. 다음 재발 시 동일 UTC 창의 index event, 전체 MQTT 상태, 로컬 ingress, durable operation journal/watchdog, raw-anchor action, LAN·라우터 PCAP을 교차해야 한다. 전량 상태 기록기·raw-anchor는 9월 27일경 종료 예정이고 라우터 PCAP은 36시간 한도이므로 그 뒤에는 같은 증거 범위를 주장하지 않는다.
