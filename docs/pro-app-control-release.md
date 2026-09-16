# Pro 앱 기능 완성 및 배포 후보 — 2026-09-17

DC의 소프트웨어 후보를 `f9c74ff`로 보존한 뒤 Pro만 진행한다. 이 문서는 소프트웨어·오프라인
검증 기록이다. 기존 운영 서비스 교체, 장비 write, 실기 영수증 발급을 뜻하지 않는다.

## 빠진 기능과 기존 경로

| 기능 | 구현 및 연결 |
|---|---|
| power, schedule_enable, linkage_role | 기존 writer·Pro 9종 producer·HA switch/select 유지 |
| manual_mode, manual_flow, manual_frequency | 기존 writer·Pro 9종 producer·HA select/number 유지 |
| custom_wave_frequency, feeding_duration | 기존 writer·Pro 9종 producer·HA number 유지 |
| schedule | 기존 48개 9-byte 슬롯 image·자격·MQTT apply 경로 유지 |
| schedule_reset | 기존 EE → populated → EE → 자기 raw 원본 복귀 producer·CLI·HA button 확인 |
| device_clock_sync | Pro writer·clock producer·CLI·HA button 연결. 과거 시각 재송신 없는 관측 복구 |
| slave_common_flow | 앱의 공통 Flow scalar와 HA number. 별도 same/하향 1/원본 복귀 자격 경로 |

이 12개 앱 기능은 identity별로 **HA controls 11 + schedule request topic 1**로 나뉜다.
native Sync/Async의 preview/attach/detach/recover/status/assist/cycle 경로는 기존 별도 API를
유지한다. Pro 9종 자격의 원본 복귀는 source만으로 완료되지 않으며 own-detach child가
원 controls와 432-byte image를 복원해야 한다.

시각 설정은 설정된 시간대의 현재 시각을 보낸다. 이 명령의 readback은 흐른 시간을 반영해
검사하고, 다른 제어에 과거 시각/Auto snapshot 일치를 요구하지 않는다. 실패 복구는 관측만
하며 과거 시각을 보내지 않는다. 복구 관측만으로 clock qualification 영수증을 발급하지 않는다.

공통 Flow 자격은 **Flow 명령 바이트와 해당 필드 readback/원본 복귀**를 검증한다. 시험은
기존 저출력 independent envelope에서 진행한다. 이 결과로 Async slave의 물리 출력이
마스터와 독립적으로 유지된다는 Q1 답을 만들지 않는다. 원본 시계나 Auto 값은 그 조건이 아니다.

## 앱 기능이 아닌 slave 슬롯별 확장

`slave_per_slot_flow_extension`은 활성 Pro 앱에 없는 엔진 확장이다. 기존 compiler, 432-byte
image writer, coordinator의 schedule/schedule-flow 경로와 계획별 48개 HA 슬롯 number
구현은 이미 있다. 빠진 앱 인코더로 분류하지 않는다.

하지만 slave가 자기 슬롯 출력을 적용하는지는 Q2의 `UNKNOWN`이다. 일반 시간표 A/B 저장
성공을 이 확장의 기능 증명으로 바꿔 영수증을 발급하지 않는다. 그 영수증은 catalog에서
UNKNOWN을 qualified로 바꾸고 실행 경로를 여므로, 이름만 바꾼 자격으로 대신할 수 없다.

이 의미 판정은 다음 두 경로로 분리해 park한다.

1. 이미 보존된 raw 관측에서 independent/Async epoch와 실제 슬롯 경계 표본을 다시 대조한다.
   새 장비 write 없이 답할 수 있는 범위만 판정한다.
2. 필요한 구간이 없으면 현장 승인된 native 쌍 관측에서 independent 대조군과 Async 경계를
   수집한다. 별도 exact restore 수단·권한을 확보한 기존 실기 절차로만 재개한다.

park 영향은 **확장용 슬롯 number/실행의 자격이 열리지 않는 것**이다. 앱 시간표 저장,
공통 Flow 명령, 기존 native Sync/Async 코드 구현을 제거하거나 미구현이라고 바꾸지 않는다.
재개 조건은 해당 실물·역할·시간표의 raw 표본으로 Q2를 답하거나 사용자에게 실험 결과를
별도로 보고할 수 있을 때다. 오프라인 테스트는 이 조건을 대신하지 않는다.

## 배포 준비 원칙

writer/preview 지문이 바뀌므로 DC 후보와 기존 운영 0cb 지문의 영수증을 재사용하지 않는다.
최종 source를 고정한 후보 image와 새 manifest를 만들며, Pro 12개 앱 기능은 기기별 정확한
집합으로 확인한다. 기존 Pro 9종 + common Flow + reset + clock의 실제 영수증이 필요하다.
12개씩 두 실물의 목표는 24 pair / 48 receipt이며, 아직 발급된 숫자가 아니다.

시험용 qualification config와 실행 계획은 운영 입력과 분리하고 실행 비활성으로 보존한다.
새 runtime 전환은 기존 Pro/Bar 기능을 유지할 current 영수증이 준비되기 전에는 하지 않는다.
같은 hardware-safety 저장소에서 구 runtime과 새 qualifier를 동시에 실행하지 않는다.

## 오프라인 검증

직접 HA 집합/라우팅 시험은 12개 앱 기능과 바인딩된 한 실물만 노출하는지 확인한다. clock은
Pro/DC 각각 잘못된 제품 첫 write 전 거부, ACK 뒤 readback 실패, 증거 변조 거부, 재실행
0 I/O, 읽기 전용 recovery를 확인한다. native 기능 및 전체 회귀 결과와 후보 digest는 검증
완료 후 아래에 덧붙인다. 이 테스트들은 펌웨어의 Sync/Async 동작을 증명하지 않는다.

Mac 전체 회귀는 **3417 PASS / Linux 전용 1 SKIP, 110.75초**다. 공통 Flow 전용 시험은
26 PASS이며 ACK 뒤 readback 실패에서 UNCERTAIN/ACK를 보존하고, generic recover가 Flow 원본
한 번만 쓰며 trial을 재송신하지 않는 사례를 포함한다. catalog의 옛 clock 미노출 기대값 두
건은 새 실행 경로에 맞춰 바꿨으며, 무자격 clock 및 다른 기능의 자격 오용 거부는 유지한다.

Linux 첫 실행은 시험 파일 묶음에 예제 설정·Compose·HA 구성요소를 빠뜨려 **35 FAIL /
1597 PASS에서 중단**했다(85.64초). 전부 누락 파일 오류이며 XML을 보존했다. 시험용 root
자료만 보완했고 제품 검사·기존 동결 테스트·생산 image는 바꾸지 않았다. 두 번째 실행은
**3380 PASS / 38 FAIL, 194.84초**로 끝났다. 37건은 시간표 예제·native scenario JSON 부재였고,
1건은 같은 누락으로 CLI가 2를 반환한 단언이었다. 해당 XML도 보존했다. 최종 시험은 base
commit의 추적 파일 전체 archive에 고정한 새 source/tests를 얹어 구성한다.

그 최종 Linux 전체 실행은 **3418 PASS / 0 FAIL / 0 SKIP, 195.12초**다. JUnit digest는
`a3e2d74aeb9914a30ce9ff631759d1d1b06061005f7b2eeb8509362aea6ad08c`다. base fixture archive는
`3bcceb55e023085851652e3d391226d80b5046d2356e7d61775705e2053b34e4`이며 source/tests archive
digest도 로컬과 host 양쪽에서 대조했다. Ruff·diff 검사를 통과했고 동결 protocol/legacy 코드
변경은 0이다. **Pro 앱 기능의 소프트웨어·오프라인 검증·배포 후보 준비**를 마치며, 실제 장비
수용이나 새 운영 활성화를 완료했다고 보고하지 않는다.

## 고정한 후보와 입력

- 생산 image: `sha256:01407f87a6a5a3b8df12dd77c480349ec2838900b019df429de1d5636891e16b`.
- protocol schema: `211e514a88fa95a83b9bd05d0353e66e3c69df0e4dcbb5b46b94a95559cb3516`.
- source archive: `d11c2ba44ac333a7ed0c61bf78a74e6c6ef9d616f8c893c5e373f91fd485e771`.
  source 163파일을 현재 고정 source와 비교해 차이 0을 확인했다.
- tests archive: `02d6fe174800d42e3ee98307f065dd6b08de4b8ca9ce571a320cab085f0ae457`.
- 추가 시험 root 자료: `3cad804eaaa5cb75352abd3a0e50529f9bd731aa36c26679b41685e8f102635c`.
- 입력 준비 v2 report: `7b992ad379c4531d0c8d6fc612f46107b6eb7bc80c280ae1cda448af761934db`.
- Mac JUnit: `f7268519671a64d42cf2b470849b1d8117030abdf70364c93f4f1b720b55705e`.

private artifact id는 `pro-release-preparation-20260917`이다. Pro pair의 기존 9종 입력과
각 실물별 common/reset/clock 입력을 별도 생성했다. 추가 세 기능은 한 실물씩 실행할
계획이며 모두 disabled다. common/reset의 기존 시험 envelope 준비는 별도 필요하고,
그 준비가 자동화됐다고 보고하지 않는다. reset populated image는 00:00~23:59 Constant40이며
전일 커버라고 부르지 않는다. 신규 native 연결 또는 extension qualification 실행은 없다.

준비 v1의 `operating_config_unchanged` 리터럴은 별도 증거가 아니므로 v2에서 입력 사본의
전후 digest 비교로 대체했다. Pro template root 자체도 selected `c1ed7f76…`와 대조한다.
운영 컨테이너의 config·manifest·qualifications를 별도로 읽어 확인한 SHA는 각각
`7ecb0115e4b3c87339ae8ef4eb429890ca56121983ff18e0daf08df68130928a`,
`108474526f5dd65831896f9e120764e7737e9ce972e46322551ebcdde7e4c5ed`,
`0e6fca9aeb70b676a263e94f5c09994ad7bce7e806c81ffa7d144814edcd21fb`로 DC 작업 때의 보존본과
동일하다. 기존 image `5b1ec481…`는 running/restart 0을 유지했다. 새 준비와 검사는 network
none이며 운영 hardware-safety volume·장비 접속·MQTT 명령·서비스 교체는 모두 0이다.
