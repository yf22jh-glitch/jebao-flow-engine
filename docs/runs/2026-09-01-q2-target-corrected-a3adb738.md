# run: 2026-09-01-q2-target-corrected-a3adb738

- 목적(한 문장): 반대 Mode를 가진 master/slave 고정 스케줄을 실제로 적용하고 900초를 끝까지
  관측해, `async_slave`가 master Mode를 따르면서 자기 모드별 Flow `45 -> 60`을 적용하는지
  판별한다.
- 이 실행이 답하려던 질문: "master가 `Sine/50/F40 -> Constant/55`로 전환할 때 slave가
  master의 `Sine -> Constant` Mode를 따르면서 자기 Flow `45 -> 60`을 적용하는가?"
- 실행한 commit SHA: `8d64209dea0bc7b96537d23045fb5f83d1f0e60e`
- 사용한 명령·주요 파라미터:
  - exact commit의 clean Linux/amd64 Docker image와 이전 실행의 원 baseline·qualification을 사용
  - master manual `30`, slave manual `35`, 승인 출력 범위 `30..60`
  - master A=`Sine/50/F40`, B=`Constant/55/wire F0`
  - slave A=`Constant/45/wire F0`, B=`Sine/60/F35`
  - 동일 device-local 경계, master=`master`, slave=`async_slave`, observation epoch `900초`
  - 첫 write 뒤 진단 read 오류만으로 조기 원복하지 않고 전체 epoch를 완주하는 고정 규칙 적용
- 종료 지점(stage / failure code):
  - durable verdict는 `verified_sample_count=0`, 최종 outcome
    `terminal / experiment_failed_restored`다. 따라서 이 실행 자체의 Q2-target 판정은 `UNKNOWN`이다.
  - outer safe pause, sentinel qualification·restore, field schedule write·verify와 `TimerON` arm 완료
  - native role observation phase 진입 뒤 slave가 포함된 유효 pair를 한 번도 만들지 못함
  - 900초 observation epoch는 끝까지 수행했으며 monitor failure는 `monitor_state_read` 90회
  - ordered rollback: `2026-09-01T02:25:51.975824Z` 시작,
    `2026-09-01T02:26:10.108312Z` 완료; rollback failure·recovery reason `0`
- 장비가 실제로 돌려준 값:
  - live private raw sink에는 header 1개와 exact wire frame 91개가 보존됐다. 91개 모두
    `participant=master`, explicit reply action `0x03`, wire length `462`였다. slave frame은 `0`개다.
  - master raw 50개는 `Linkage=master`, `AutoMode=Sine`, `AutoFlow=50`, `AutoFreq=40`이었다.
  - 마지막 Sine capture `2026-09-01T02:17:59.773628Z`와 첫 Constant capture
    `2026-09-01T02:18:08.888686Z` 사이 9.115초 구간에서 Mode·Flow 전환이 관측됐다. 이후 master
    raw 41개는 `AutoMode=Constant`, `AutoFlow=55`였고 마지막 capture
    `2026-09-01T02:24:48.352552Z`까지 399.464초 유지됐다.
  - Constant 슬롯에 쓴 wire `Frequency=0`과 장비가 보고한 `AutoFreq=5`는 일치하지 않는다.
    원인은 해석하지 않고 `AutoFreq=5`를 관측값으로 보존했으며 Q2-target 판정에는 사용하지 않았다.
  - master 수신 raw의 오프라인 재분석은 계획한 A→B **Mode·Flow** 전환을 확인하지만, slave raw가
    없으므로 master Mode 추종과 slave per-mode Flow를 판정하는 pair는 없다.
  - 실행 종료 뒤 서로 다른 프로세스·fresh session의 source-attested collector 2회를 수행했고,
    두 회 모두 `1/1 pair`, `2/2 explicit reply sample` accepted였다.
  - 두 collector raw의 오프라인 재검증 결과 두 회 모두 원 control과 일치했다.
    - master: `ON / TimerON / independent / constant / Flow 30 / Frequency 32`
    - slave: `ON / TimerON / independent / random / Flow 89 / Frequency 34`
  - 두 회 모두 원 schedule image digest와 일치했다.
    - master: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
    - slave: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`
  - 증거 등급:
    - live·post-restore reply action과 exact wire frame 및 여기서 재디코딩한 control·schedule:
      **(a) preserved raw artifact**
    - terminal intent, stage event, rollback evidence, collector plan·series·host timing·identity
      binding: **(b) preserved structured/durable daemon artifact**
    - config relock, recovery process·TCP connection 확인과 현장 관측:
      **(c) reconstructed operator observation**
  - opaque artifact id와 digest:
    - live raw `JFR-a3adb738c660`: capture `91`,
      `1ed6e942ce3625b7b1dabc2c6d11daef9f6bce8ade21a2e2967404115b3b8372`
    - terminal intent logical id `native-linkage-intent`:
      `afe9859bc3c668b8d1d64170118198436d5fc2324483afc0d548ff3402643ef9`
    - CLI log logical id `q2-target-corrected-cli-log`:
      `c7dea9a0ba1149bb19e185fd2a22591f8730667bf28b995a67be5350004afc39`
    - verify 1 plan `JFP-6e6d2373c1c0d781a96d37945415ba8f`:
      `e26d173942db4f1f09b4099537e24133706954e232a3c44cf57f75937d0dd4ad`
    - verify 1 series `JFS-b01f91c7dab916f22b0886303a2a4135`:
      `d0d7b0fd963ca3097fa46e97f53ad4d45c355b7e66050f0ddb19de6b82049eb6`
    - verify 2 plan `JFP-484b34b1316233040a63a0fac08e685d`:
      `b79b7b52aad93b922e8bc30e3c2a970257d0e40302bc9f0248fadf069d8c91c1`
    - verify 2 series `JFS-7575669e89ee2e826702ba012eed7f18`:
      `358a099694be4240347fa610ba512fed5d557bde39ea7374884ad814ebe244c9`
  - UTC span (시작 / 종료):
    - live intent: `2026-09-01T02:08:56.577099Z` / `2026-09-01T02:26:10.118752Z`
    - live raw: `2026-09-01T02:09:04.466028Z` / `2026-09-01T02:24:48.352552Z`
    - verify 1: `2026-09-01T02:36:59.221476Z` / `2026-09-01T02:37:33.521725Z`
    - verify 2: `2026-09-01T02:37:50.042373Z` / `2026-09-01T02:38:23.500264Z`
  - SHA-256 identity binding(두 verify 공통):
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
  - raw frame digest (SHA-256):
    - verify 1: `de65996927d340830d0aebb2b0b3fff1d2e402b06cd89b6ae13ed881ba5fcc2c`,
      `067e7f3f76e5397784a6e6939909907299445efb4c6bf13f356bf60b1da71706`
    - verify 2: `cb1e61fbe62cf8970a0bd4276af619f45d17721cc83303efd082413d299588de`,
      `67a46f5ebb0373df34c3fc3dba533e18bb1d8cbb5f298b77d4de926e8efec29c`
  - pair completion gap: verify 1 `16,890.849 ms`, verify 2 `16,802.109 ms`
- 원복 검증 결과(어떤 수준의 확인이었는지 명시):
  - write-side durable intent가 ordered rollback 완료, rollback failure `0`, recovery reason `0`을
    기록했다: **(b)**.
  - 별도 fresh collector 두 회에서 identity, explicit reply, 원 control 여섯 필드와 두
    432-byte schedule digest가 모두 일치했다: 장비 상태·schedule은 **(a)**, host timing과
    source attestation은 **(b)**.
  - private config는 원 digest로 relock됐고 `dry_run=true`, observer disabled, write-enabled
    device `0`, recovery container restart `0`, established TCP `12416` connection `0`을 확인했다:
    **(c)**.
- **이 실행으로 새로 확정된 사실:**
  - topology 교정 뒤 고정 field schedule과 TimerON arm이 실장비에 도달했다. durable run verdict와
    별개인 preserved-raw 오프라인 재분석에서 master의 `Sine/AutoFlow50 -> Constant/AutoFlow55`
    Mode·Flow 전환과 Constant 구간 399.464초 유지가 확인됐다. B 슬롯의 wire Frequency와 보고
    AutoFreq 차이는 설명하지 않는다.
  - 같은 epoch에서 master explicit read는 91회 성공했지만 slave가 포함된 pair read는 90회
    모두 `monitor_state_read`로 실패했다. 종료 후 fresh session에서는 slave가 두 번 정상
    응답했으므로 장비 영구 오프라인은 아니다. exact code trace에서 transport retry는 paired
    sessions를 refresh한 뒤 다시 읽고, raw ordinal도 모두 짝수 `2..182`였다. 따라서 하나의 stale
    persistent socket보다는 live ASYNC slave의 role/state-dependent explicit reply 또는 paired-read
    경로로 범위가 좁아졌다. 정확한 원인은 아직 `UNKNOWN`이다.
  - 900초를 끝까지 관측하고도 slave raw가 0개라 목표 질문을 PASS나 FAIL로 분류할 수 없다.
    이 실행은 정보 0은 아니지만 Q2-target 판정은 계속 `UNKNOWN`이다.
  - 90회의 `monitor_state_read` 뒤에도 계속 관측한 것은 승인된 절차다. read 오류는 고정된 다섯
    조기 중단 조건에 포함되지 않았고, 그 결과 master의 경계 전후 raw와 300초 안정 구간을 보존했다.
  - ordered rollback과 독립 raw 검증 두 회에서 원 controls와 두 schedule image의 exact restore가
    확인됐다.
- 이 실행으로 확정하지 못한 것:
  - slave가 master Mode·timing을 따르는지, 그 상태에서 자기 Flow `45 -> 60`을 적용하는지는
    slave raw가 없어 계속 `UNKNOWN`이다.
  - slave role write의 실제 보고 상태, 물리 유량·파형·위상, 장기 반복 신뢰도는 판정하지 않았다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
  - topology 교정 단회 write 승인은 이 실행으로 소진한다. 자동 재시도하지 않는다.
  - 보존 raw와 exact commit을 사용해 paired-session refresh 뒤에도 live ASYNC slave explicit
    reply가 실패하고 restore 뒤 independent 상태에서는 성공한 원인을 장비 write 없이 먼저 분석한다.
  - Q2-target의 추가 실기가 꼭 필요하다고 repository maintainer가 다시 판단할 때만, 원인과
    최소 변경을 별도 동결 해제 커밋으로 고정하고 새 승인을 받는다. 기본 제품 경로는
    software-independent actuator와 그룹 런타임이다.
