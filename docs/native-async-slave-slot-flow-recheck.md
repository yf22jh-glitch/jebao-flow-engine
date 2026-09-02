# Native ASYNC slave 슬롯별 Flow 단회 재검증 계획

## 질문

현재 운전 상태와 두 Pro의 432-byte schedule image를 먼저 보존한 뒤, 앱 UI를 거치지 않고
백엔드 하네스로 같은 `Sine -> Constant` 경계와 서로 다른 장비별 `Flow`를 넣었을 때
`async_slave`가 master의 Mode·경계 timing을 따르면서도 자기 슬롯별 `Flow`를 적용하는가?

이번 질문은 **Mode 소유권을 판정하지 않습니다.** slave가 master의 `Sine -> Constant` Mode와
경계를 따르는 것은 예상 동작입니다. 판별 대상은 slave가 경계 전 `35`, 경계 후 `47`을 각각
보고하는지뿐입니다.

2026-08-30 attempt 05는 같은 Mode 순서에서 master `31 -> 35`, slave `32 -> 40`을 900초 동안
관측하고 원 schedule을 byte-exact 복원했습니다. 이번 실행은 그 결론을 일반화하지 않고,
사용자가 지정한 아래 exact signature와 raw state-frame 보존 범위에서 다시 판정합니다.

## 고정 signature

- master safe manual: `Constant / Flow 30 / Frequency 20`
- slave safe manual: `Constant / Flow 35 / Frequency 20`
- master A: `Sine / Flow 40 / Frequency 50`
- master B: `Constant / Flow 35 / wire Frequency 0`
- slave A: `Sine / Flow 35 / Frequency 50`
- slave B: `Constant / Flow 47 / wire Frequency 0`
- 두 장비의 A/B 경계: 같은 device-local 절대 시각
- native 역할: A=`master`, B=`async_slave`
- observation authority `915초` 안의 complete epoch `900초`
- pre-boundary: T-60초 바깥에서 같은 pair 10개와 120초 안정
- post-boundary: T+60초 바깥에서 같은 pair 2개 이상과 300초 안정
- 이 operation의 guarded Flow 상한: `47`

`slave A=35`는 `master B=35` 및 slave safe manual과 같은 값입니다. 따라서 post-boundary에서
slave가 `35`를 보고하면 "A 유지", "master Flow 추종", "공통 manual Flow"를 서로 구분하지
못합니다. 그러나 세 경우 모두 사용자가 묻는 슬롯별 `35 -> 47` 적용에는 **FAIL**이므로 이번
이진 판정에는 지장이 없습니다.

## 값 admission

- Flow `30, 35, 40, 47`은 이전 실장 write에서 사용·복원된 `30..60` 안이고, 상한 `47`은
  2026-09-01 단회의 상한 `60`보다 낮습니다. 장비별 min/max/step 검사는 그대로 유지합니다.
- [2026-09-01 current-baseline](runs/2026-09-01-current-baseline-78058401.md)의 preserved raw 두
  frame을 current branch와 byte-identical한 schedule decoder로 다시 디코딩한 같은 product pair의
  Sine/Pulse frequency는 `15, 25, 40, 60, 70, 80`입니다(Sine 자체는 `15, 25, 40, 80`).
  `50`은 그 raw-observed waveform-frequency envelope 안이지만 현재 슬롯에 있던 exact 값은
  아닙니다. 따라서 이 문서는 endpoint·step 전체를 검증했다고 주장하지 않고,
  `Frequency=50`을 이 exact one-shot signature에만 허용합니다.
- preflight에서 current raw의 product identity, Flow limits/step, 두 schedule image 또는 위
  frequency envelope가 달라지면 첫 write 전에 `NO-GO`입니다.

## raw 관측 계약

2026-09-01 corrected run의 `async_slave`는 strict explicit read에서 raw를 하나도 남기지
않았습니다. 같은 strict-only 경로를 반복하지 않습니다.

- monitor read는 LAN session이 이미 제공하는 `read_raw_state_capture(accept_reports=True)`를
  한 번 호출해, 그 호출이 선택한 action `0x03` explicit reply 또는 action `0x04` state report의
  **동일 wire frame**에서 decoded state를 만듭니다.
- action 종류, frame digest·길이, device-local `NowTime`, participant와 host read 구간을 private
  raw sink에 보존합니다. raw frame은 저장소에 커밋하지 않습니다.
- `0x04`를 explicit reply라고 부르거나 요청과 상관된 ACK라고 주장하지 않습니다. 이번 판정에는
  실제 장비가 보낸 recognised state frame과 그 frame의 device-local boundary side만 사용합니다.
- master와 slave가 모두 같은 boundary exclusion side에 있는 pair만 후보입니다. 한쪽 raw가
  없거나 decode되지 않으면 그 회차는 판정 sample이 아닙니다.

## 사전 고정 판정

### PASS

아래를 모두 만족합니다.

1. stable pre pair에서 master=`Sine/40/F50`, slave=`Sine/35/F50`
2. stable post pair에서 master=`Constant/35`, slave=`Constant/47`
3. post pair가 300초 안정 조건을 만족하고 900초 epoch 안에 상충하는 valid pair가 없음
4. sample에 사용한 두 state frame과 종료 뒤 restore frame이 private raw로 보존됨
5. 종료 뒤 원 controls와 두 원 schedule image가 byte-exact 복원됨

### FAIL

master의 유효 `Sine/40 -> Constant/35` 경계가 확인됐는데, 같은 유효 pair에서 slave가 stable
`Sine/35 -> Constant/47`을 만들지 않고 `35` 고정, master Flow 추종, 또는 다른 stable Flow를
보고합니다. 원인 가설이 서로 겹치더라도 슬롯별 `35 -> 47` 적용 여부는 `FAIL`입니다.

### UNKNOWN

slave raw 부재, decode 실패, 유효 pair 부족, 경계·안정 시간 부족, 또는 상충 sample 때문에
PASS/FAIL 어느 쪽도 사전 기준으로 분류할 수 없습니다. `UNKNOWN`도 원복을 생략하거나 같은
operation을 재시도할 근거가 되지 않습니다.

## write와 복원 순서

1. fresh explicit baseline에서 physical binding, 원 controls, `TimerON / independent`, 두
   432-byte schedule image를 fsync하고 offline round-trip과 digest를 확인
2. journal 3종과 emergency latch 부재, write 가능한 다른 프로세스 부재, 현장 차단 수단과
   approver를 확인
3. 장비별로 `SwitchON + TimerOFF + independent + Constant + safe Flow + Frequency 20`을
   **하나의 control frame으로 정확히 한 번** 적용하고 fresh session에서 확인
4. 기존 sentinel qualification 뒤 위 두 temporary schedule image를 각각 정확히 한 번 적용하고
   exact image read-back 확인
5. safe manual control을 유지한 채 `TimerON`을 장비별 합성 frame으로 한 번 적용
6. master role, async-slave role을 각각 single-write journal 아래 한 번 적용
7. 900초 observation epoch를 수행하고 private raw와 durable sample을 보존
8. 성공·실패·UNKNOWN과 무관하게 slave role detach, master role detach, 두 장비 safe
   `TimerOFF + independent + Constant + safe Flow + Frequency 20` 합성 frame 순서로 disarm
9. slave와 master의 원 432-byte schedule image를 byte-exact 복원
10. slave와 master의 원 여섯 control 필드를 기존 audited outer-control 합성 frame으로 복원
11. writer 종료 뒤 서로 다른 두 fresh source-attested collector session에서 physical binding,
    원 controls와 두 schedule image digest의 exact 일치를 확인

역할 변경 또는 `TimerOFF`를 단독 frame으로 보내지 않습니다. 역할 B의 latent manual
`Flow=89`는 시작 시 safe Flow `35`와 `TimerOFF`를 같은 frame으로 적용해 노출하지 않고, 종료
시에는 원 schedule을 먼저 되돌린 뒤 원 `TimerON`과 `Flow=89`를 같은 audited outer-control
frame으로 복원합니다. 자동 rollback이 terminal로 끝나지 않으면 새 실험이나 임의 재전송을 하지
않고, 남은 journal을 소유한 기존 `recover_experiment()` attended recovery를 운영자가 다시
호출해 exact restore를 끝냅니다.

## 조기 ordered recovery 조건

첫 hardware write 뒤 아래 다섯 조건에서만 즉시 recovery로 전환합니다.

1. physical identity binding 불일치
2. temporary schedule authority 아래에서 실제 또는 보고된 Flow가 `47` 초과. 원 schedule과
   원 outer-control/`TimerON` 복원을 확인한 뒤에는 preserved schedule의 역할별 active 범위
   (A `30..60`, B `50..80`) 초과
3. 펌프·수조의 위험한 물리 동작
4. durable journal·복구 권한·현장 물리 차단 수단 상실
5. 현장 감시자 또는 사용자의 명시적 비상 정지

read timeout, `0x03` 대신 `0x04` 수신, 예상 밖 안전 범위 내 tuple은 기록 대상이지 자동 재시도나
중복 write 사유가 아닙니다.

## 구현·권한 범위

허용 source 변경은 exact signature와 report-capable raw 관측에 필요한 아래 slice뿐입니다.

- `devices/schedule_flow_experiment.py`: exact five-value signature, same-Mode 두 schedule,
  `slave A == master B == 35`의 이번 signature 전용 admission, Flow 상한 `47`, 배타 classifier
- `devices/schedule_linkage.py`: exact A/B 상수와 Frequency allowlist, guarded Flow 상한 `47`,
  monitor capture가 recognised `0x03` 또는 `0x04` raw frame을 보존하도록 지정 축소
- `devices/lan.py`: 기존 `_io_lock` 안에서 `read_raw_state_capture(accept_reports=True)` 한 번의
  decoded state와 `RawStateCapture`를 함께 반환하는 가산적
  `get_report_capable_state_capture()` 메서드 1개
- `schedule_flow_experiment_cli.py`: 임의 값을 받는 옵션 없이 위 exact signature만 구성
- 위 변경에 직접 대응하는 기존 unit/fault-injection tests

`schedule_transaction.py`의 topology 예외는 사용하지 않습니다. 두 장비 schedule은 동일한
`Sine -> Constant` topology이므로 기존 동일-topology 검증을 그대로 통과해야 합니다.
`linkage.py`, `schedule_linkage_cli.py`, 일반 daemon·MQTT 경로와 LAN write path는 변경하지
않습니다.

기존 qualification receipt는 exact physical binding과 qualification operation이 모두 일치할 때
만료 시각만 이번 한 operation에서 무시할 수 있습니다. receipt 부재, identity·operation 불일치,
single-write, journal fsync 또는 rollback authority는 우회하지 않습니다. 첫 write 전 preflight가
출력하는 confirmation token과 exact-commit image가 일치해야 합니다.

## 종료 조건

결과는 성공·실패와 무관하게 새 `docs/runs/` 파일에 append-only로 기록합니다. terminal
write-side restore와 서로 다른 두 fresh collector의 exact 검증이 끝나면 단회 해제가 자동
소진됩니다. 복구가 남으면 새 실험은 금지하고 해당 operation의 ordered recovery와 read-only
검증만 허용합니다.
