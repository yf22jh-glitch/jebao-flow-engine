# DC 앱 기능 구현 및 배포 후보 — 2026-09-16

범위는 DC Pump Pro 한 대의 앱 제어다. Aquarium 메인 리턴·도징·새 native 운전은 포함하지
않았다. 아래는 소프트웨어 구현 및 오프라인 검증 기록이며 실기 성공 기록이 아니다.

## 앱 대조와 실행 경로

활성 dcPro template의 SHA-256은
`bc20fe3bb0723209fb477f64f9e210d973c1588d72cfecff77d4a9ee3716fbaa`다.
제품별 loader가 선택한 사본으로, 파일 이름이나 갱신 시각으로 고르지 않았다.

| 앱 조작 | 송신 및 동작 계약 |
|---|---|
| 전원 | SwitchON 단일 필드. OFF 실패 복구가 자동 ON으로 바뀌지 않음 |
| 수동 유량·모드·주파수·급이 시간 | Flow/Mode/Frequency/FeedTime 각각 단일 필드 |
| Schedule 화면 스위치 | TimerON=원하는 값, SwitchON=같은 값 |
| Manual 화면 스위치 | SwitchON=원하는 manual 상태, TimerON=반대 값 |
| 시간표 저장·편집·삭제 | 48개 8-byte 슬롯. constant=0, pulse=1, sine=2, random=3, feed=4 |
| 전체 초기화 | 48슬롯 전체 EE. 삭제/초기화의 실제 출력 영향은 firmware 미확정 |
| 시각 설정 | 설정 시간대의 YMDData와 zero-prefixed HMSData를 한 번 송신 |

두 타이머 스위치 모두 **OFF는 펌프 정지**를 포함한다. HA 이름에 `Controls Power`를 넣고,
ON 요청은 그룹 소유권 처리에서도 전원 ON으로 분류한다. Auto 필드는 timer payload에 없다.

앱 ScheduleDetail 저장·삭제와 전체 초기화에는 TimerOFF 선행 명령이 없다. DC 일반 시간표
경로도 TimerOFF 조건을 요구하지 않는다. 대신 새 시간표 출력과, 현재 운전에 복원될 원본
시간표 출력을 설정 범위로 검사한다. 빈 시간표의 영향은 이 범위 검사로 증명할 수 없다.

## 검증과 복구

- `dc_prepared_qualification`: 4개 scalar를 준비 출력(예: 40)에서 시험하고 자기 초기값으로
  복귀한다. TimerON인 경우 원 timer를 먼저 확인한 뒤 원 latent Flow를 복원한다.
- `dc_program_qualification`: timer 둘, schedule, schedule_reset을 지원한다. timer 시험은
  bounded 전일 시간표를 먼저 사용하며 실제 OFF 프레임이 포함된다. reset 자격은
  **EE → populated → EE**에서 두 EE 프레임만 사용한다.
- 모든 ordinary rollback은 해당 operation이 직접 읽은 원본에서 만든다. 복구 timer 명령의
  결과가 불명이면 그 뒤 높은 원 Flow를 쓰지 않는다. 미확인 복구 명령을 다시 보내지 않는다.
- clock 명령은 과거 시각으로 복원하지 않는다. 요청한 시각의 경과를 고려해 readback을
  확인하며, 미확인 결과는 읽기 전용으로만 재확인한다. clock 불확실 기록은 다른 펌프 조작의
  차단 조건이 아니다. 시각·Auto 값은 다른 제어의 snapshot 일치 조건에 사용하지 않는다.
  복구 관측으로 종결한 clock 작업에서는 자격 영수증을 만들지 않는다. 해당 request id는
  재송신하지 않으며, 재시험이 필요하면 별도 승인된 새 operation으로 다룬다.
- 다른 물리 장비의 미완 기록은 현재 대상 조작을 막지 않는다. 동일 대상의 진행 중 쓰기와
  자기 복구는 중첩시키지 않고 single-send 및 durable 기록을 유지한다.
- 복구 timer가 끝난 뒤 다른 복구 단계가 실패해도 완료된 timer를 재송신하지 않는다.
  이미 복구된 값이 이후 달라졌다면 현재 raw digest와 `rollback_replay_refused`를 남긴다.
  이 경로는 큐에 넣은 raw 응답과 I/O 고장 주입으로 재현했으며 펌웨어 가설이 아니다.

기존 power qualifier는 ON/OFF/ON과 실패 시 OFF 계약을 유지한다. 자격의 TimerOFF·constant·
Flow≤45 준비 조건은 별도이며, **power 자격용 자동 준비 wrapper는 이 변경에 없다**.
일반 HA power 명령은 그 시험용 조건을 요구하지 않고 켤 출력의 설정 한도를 확인한다.

## CLI 연결

아래는 인자 형식이며 실행 승인이나 실행 기록이 아니다. 공통 인자는 현재 대상, config,
manifest, 실제 DC template, output 경로 및 명시적 write opt-in이다.

```text
jebao-flow-local-qualify run --function manual_flow --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function manual_mode --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function manual_frequency --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function feeding_duration --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function schedule_enable --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function manual_enable --preparation-flow 40 <공통 인자>
jebao-flow-local-qualify run --function schedule --preparation-flow 40 --schedule-a A.json --schedule-b B.json <공통 인자>
jebao-flow-local-qualify run --function schedule_reset --preparation-flow 40 --schedule-a A.json <공통 인자>
jebao-flow-local-qualify run --function device_clock_sync <공통 인자>
jebao-flow-local-power-qualify <공통 인자>
```

A/B는 `family: dc_pro` 선언 모델이다. 다른 제품 모델은 연결 단계에서 거부한다. reset의
A는 비어 있지 않은 bounded 시간표이고, EE 두 시험 사이에서만 쓴다.

## 배포 준비 상태와 전환 순서

현재 운영 컨테이너는 교체하지 않았다. 기존 Pro/Bar image를 남기고 새 DC 후보 이미지만
별도 빌드했다. 이번 writer/encoder 변경의 schema SHA-256은
`6a211fc6514bdaad284ea0de28e95527a2ee3c7287ebc9b5dc4b26da873cf400`다.
기존 `0cb05423…` 영수증은 이 이미지에서 current가 아니므로 그대로 옮겨 활성화하지 않는다.

1. 현재 config를 복사해 **선택한 DC 한 대**의 qualification 입력을 준비한다. 운영 config는
   덮지 않는다. Pro/Bar template에 실제 DC template root를 추가하고 새 manifest를 만든다.
2. 승인된 실기 항목만 한 건씩 실행하고 복원 결과를 확인한다. timer·power 시험은 실제 정지,
   schedule/reset 시험은 시간표 교체/삭제, clock은 실제 시간 변경을 포함한다.
3. 실제 결과의 기능/물리 identity와 영수증을 병합한다. DC 예상 집합은 정확히 10기능,
   HA controls 9 + schedule topic 1이다. 테스트에서 만든 영수증을 운영에 쓰지 않는다.
4. 기존 제어를 유지할 전환에서는 Pro/Bar의 새 이미지 기준 자격도 함께 준비해야 한다.
   그 전에는 현재 운영 서비스를 내리지 않는다. 새 journal 필드가 있으므로 구 runtime과
   새 qualification writer를 같은 journal에서 동시에 운용하지 않는다.
5. 새 data generation만 만들고 기존 external hardware-safety volume을 유지한다. 이전 image,
   Compose/input generation은 복귀 자료로 보존한다. 실제 배포 직전 manifest를 다시 도출한다.

1번은 network none·hardware-safety mount 없는 별도 컨테이너에서 완료했다. 쓰기 허용 대상은
DC 한 대, template digest는 Pro/Bar/DC 세 개이며, 실행 목록의 10항목은 모두 비활성이다.
기존 입력 5파일과 Compose는 별도 private 사본으로 보존했다. 2~5번의 실기·전환은 실행하지
않았다. 입력 준비 결과는 다음과 같다.

| 보존 항목 | SHA-256 |
|---|---|
| qualification config | `c3e858a8171a38ade31af53539f03420aa5dc544437f587c0eeb16cc1a8e3a97` |
| qualification manifest | `fd3681bad7b50973b8dc5b756d4208e56ae7194209d284ce4792f2d9a836ef48` |
| 기존 운영 config | `7ecb0115e4b3c87339ae8ef4eb429890ca56121983ff18e0daf08df68130928a` |
| 기존 운영 manifest | `108474526f5dd65831896f9e120764e7737e9ce972e46322551ebcdde7e4c5ed` |
| 기존 qualification set | `0e6fca9aeb70b676a263e94f5c09994ad7bce7e806c81ffa7d144814edcd21fb` |
| 기존 Compose | `cc7d5c0763b56fc7eac866d1fe00ffb9dbb96c56978509023ce802404db23cf2` |

## 최종 후보와 시험 이력

- 생산 이미지: `sha256:ad51094f9127cc94ffdc7e87f67cf1273c68f85caa9c927293069b8411e71bd6`.
- 소스 archive: `a3c9b1822ae5cf0c12743a5b6356227649048fe93998a16e98055a0e22067c3b`.
  그 안의 source 162파일을 최종 worktree와 비교했으며 차이 0이다.
- 추가 producer 복구 회귀까지 포함한 최종 tests archive:
  `aa7defd81031bbba2efb9943a6a1f962f9bf32f9bd175cf5d1029093c41928bb`.
- 입력 준비 report: `aa69289bdc4b0168a452a3737cbf0884912324922cc1c1607a2b193079cd8431`.

Mac 첫 전체 시험은 3368 PASS / Linux 전용 1 SKIP, 중복 복구 수정 후 전체 시험은
3369 PASS / 1 SKIP(110.45초)다. 이후 추가된 producer 회귀를 포함한 직접 파일 시험은
34 PASS다. Mac 최종 JUnit digest는
`d8b51c397010a09680be90832353111735a895e56626c047fed7994f55d7e2dc`다.

Linux 시도 이력도 보존한다. 첫 수집은 Mac resource-fork 파일과 검사 계정/파일 소유권
불일치로 실패했다. 이를 정정한 전체 실행은 3363 PASS / 6 FAIL(436.94초), 최종 소스와
추가 회귀의 실행은 3365 PASS / 같은 6 FAIL(201.74초)이었다. 두 건은 시험 이미지에 Git이
없었고 네 건은 고정 안전 디렉터리가 시험 UID 소유가 아니어서 발생했다. 시험 이미지에만
Git과 올바른 디렉터리 소유권을 준비한 뒤 해당 6건이 모두 통과했다. 실제 운영 안전 volume을
시험에 mount하지 않았으며, 시험은 network none에서 수행한다.

최종 Linux 전체 실행은 **3371 PASS / 0 FAIL / 0 SKIP, 193.44초**다. 시험용 image만 필요한
도구를 추가했으며 생산 image·source는 바꾸지 않았다. 최종 JUnit digest는
`76b3bead19c6715ce5753475b21091ca6c55d916364ef9b7ff5f299d5457abe0`다. 소스/시험 archive와
JUnit은 private artifact `dc-release-preparation-20260916`으로 보존한다. Ruff·diff 검사 통과,
동결 protocol 및 legacy 하네스 변경 0이다.

제품 검사를 지우거나 완화하지 않았다. 오프라인 테스트는 장비 수용·물리 안전의 근거가
아니며, 이번 단계에서 DC 실기 영수증 발급·장비 write·운영 배포 교체는 0이다.
