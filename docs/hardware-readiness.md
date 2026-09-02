# 실기·설치 환경 준비 기준

이 문서는 실제 장비에 쓰기 명령을 보내기 전에 확인할 단일 체크리스트입니다. 제품군별
Capability 문서, 홈서버의 private 설정, 읽기 전용 discovery/probe 결과와 현장 확인을 함께
대조합니다. 어느 한 자료만으로 장비 역할이나 쓰기 권한을 추정하지 않습니다.

공개 저장소에는 MAC, Gizwits device ID, 사설 IP, passcode와 MQTT 비밀번호를 기록하지
않습니다. 실제 값은 홈서버의 gitignored private 설정에만 두고, 이 문서에는 논리 역할과 검증
결과만 남깁니다.

## 보유 장비 기준선

2026-08-28 홈서버에서 격리 IoT 망을 읽기 전용으로 다시 검색한 결과, private 설정의 여섯
물리 identity와 discovery 응답이 6/6 일치했습니다.

| 논리 역할 | 제품군 | 수량 | 현재 범위 | 쓰기 검증 |
|---|---|---:|---|---|
| 메인 수류 A/B | Local Wavemaker Pro | 2 | native Sync/Async와 software group | ASYNC 상태의 staged Flow 차이와 exact restore 확인; Mode·timing 소유권은 UNKNOWN |
| 바형 보조 수류 | Local Wavemaker with AP time-sync | 1 | software group의 보조 위상 | vendor/app상 native Async 없음, 장비 읽기만 확인 |
| 리턴 펌프 | DC Pump Pro | 1 | 개별 UI 후보 | 읽기만 확인 |
| 리턴 펌프 | Aquarium Pump | 1 | 개별 UI 후보 | 읽기만 확인 |
| 도징 펌프 | Dosing Pump | 1 | 개별 UI, 초기 actuator 제외 | 읽기만 확인 |

메인 수류 A/B만 같은 Pro product key와 `master`, `sync_slave`, `async_slave` 역할을
지원합니다. 바형은 `independent`, `master`, `slave`만 지원하므로 Pro native pair에 넣지 않고,
상위 software group에서 gain과 phase를 적용하는 보조 펌프로 취급합니다.

설치된 앱의 vendor 제품 정의와 영문 UI 템플릿을 read-only로 대조한 전체 6대 결과는
[`registered-device-app-analysis.md`](capabilities/registered-device-app-analysis.md)에 있습니다.
앱 정적 선언은 장비 수용이나 물리 효과의 증거가 아니며, 해당 문서의 `V`·`U`·`L`·`?` 구분을
유지합니다.

## 통신 규격 기준선

- [x] UDP 12414 discovery로 여섯 장비 응답 확인
- [x] TCP 12416 연결, passcode 인증과 complete state read 확인
- [x] 다섯 product key별 packet version, status buffer 크기와 데이터 포인트 프로필 분리
- [x] Pro의 `SwitchON`, `TimerON`, `Linkage`, `Mode`, `Flow`, `Frequency` read/write 확인
- [x] Pro의 `independent`, `master`, `sync_slave`, `async_slave` encode/decode 확인
- [x] Pro의 48슬롯, 432-byte schedule image decode·부분 write·전체 exact restore 확인
- [x] control frame은 장비당 durable intent 뒤 한 번만 보내고, ACK 불명 시 write를 재전송하지 않음
- [x] 상태 push나 queued reply를 권한 근거로 삼지 않고 새 인증 세션의 explicit reply로 검증
      — **범위: 동결된 write 하네스의 verification path에 한정됩니다.**
- [!] 일반 `jebao-flowctl probe`는 explicit-only가 아닙니다. `read_raw_state()`의
      `accept_reports` 기본값이 `True`이고 `cli.py`가 기본값으로 호출하므로 unsolicited
      report를 상태로 받아들일 수 있습니다. **따라서 generic probe는 측정용 collector로
      부적합합니다** (읽기 전용 관측 절차 §선행조건 1)
- [x] 전용 write-free collector가 fresh identity binding, explicit reply, exact wire,
      UTC·monotonic timing, pair gap과 fsync series를 보존하고 18 pair / 36 raw-frame
      실장비 pilot 및 오프라인 재검증을 통과
      ([실행 기록](runs/2026-08-28-pilot-2bd1bf97.md))
- [x] 동일 physical binding은 MAC과 vendor device ID의 SHA-256 binding으로 저널에 고정
- [ ] 일반 unsolicited status push의 모델별 전달 주기와 신뢰도 실측
- [ ] 제조사가 보장하는 모델별 최소 출력, 출력 step과 `Flow=0` 의미 확인
- [ ] 펌프 MCU firmware 버전 확인; discovery의 Wi-Fi firmware와 구분
- [ ] 장비 재부팅 뒤 상태 유지와 두 로컬 클라이언트 동시 연결 특성 확인

### 실측된 비원자 상태 갱신

Pro의 complete state frame 하나는 온전한 디코딩 단위지만, 펌웨어가 논리적으로 연관된 데이터
포인트를 같은 시점에 갱신한다는 뜻은 아닙니다. 실측에서는 다음 값이 서로 다른 report에 걸쳐
수렴했습니다.

- `Mode`, `Frequency`, `AutoMode`, `AutoFlow`, `AutoFreq`
- 장비별 `NowTime`; 두 Pro가 약 22~25초 단위의 서로 다른 batch에서 갱신되는 현상

> **증거 등급 (c) reconstructed operator observation.** 위 두 항목은 서로 다른 출처의 관측이고
> (필드 stagger는 retry4, 22~25초는 별도 읽기 전용 capture), **어느 쪽도 보존된 raw
> 메타데이터가 연결돼 있지 않습니다.** 설계의 근거로는 쓰되, 판정 기준의 강제값으로는
> 쓰지 않습니다. 이 legacy 값의 후속 증거와 한계는 바로 아래에 분리해 기록합니다.

후속 write-free pilot은 두 역할에서 장비 시계 값 변화 사이의 host 간격 중앙값을 각각
약 22.48초로 보존했습니다. 장비 시계 값은 등급 (a) raw, host monotonic 간격과 파생 통계는
등급 (b) artifact입니다. 다만 실제 sample 간격 자체가 약 21.9초라 결과가 한두 sample step으로
alias되므로 **장비의 고정 갱신 주기를 확정하거나 그 숫자를 manifest 강제값으로 쓰지 않습니다.**
정확한 범위와 한계는 [pilot 실행 기록](runs/2026-08-28-pilot-2bd1bf97.md)을 따릅니다.

따라서 안전 필드의 변경은 즉시 실패시켜도, 이미 소유한 schedule 전환의 허용된 A→B 필드와
시계 표시는 bounded read-only convergence 또는 한 번 발급한 monotonic clock receipt로
판정해야 합니다. 새 control frame을 반복 전송해 수렴을 만들면 안 됩니다.

## 재연결·페어링 범위

- [x] 읽기 전용 Observer는 identity 기반 재검색, DHCP 주소 변경과 bounded reconnect를 지원
- [ ] 일반 control-mode의 Desired/Actual 재조정과 `restore_on_reconnect` 실행 경로 구현
- [ ] 앱 없이 SSID/passphrase를 넣는 AP provisioning 구현

따라서 일시적인 Wi-Fi 단절이나 DHCP 변경은 장비가 기존 IoT SSID에 다시 붙는 즉시 Observer가
복구할 수 있지만, 공장 초기화·SSID 변경·credential 소실은 현재 Jebao 앱으로 재등록해야 합니다.
앱은 provisioning과 비상 복구용으로 유지하고, 데몬과 동시에 장비를 조작하지 않습니다.

## 설치 환경 기준선

- [x] 홈서버에서 배포 worktree와 Docker 사용 가능
- [x] 홈서버에서 router SSH 접속 가능
- [x] router에 `lan`, `iot` 인터페이스와 `lan → iot` forwarding 존재
- [x] private observer 설정에 격리망용 discovery target 7개 구성
- [x] 읽기 전용 discovery에서 private identity 6/6 재바인딩
- [x] recovery와 one-shot 명령이 고정 이름 `/hardware-safety` 볼륨 공유
- [!] 2026-08-30 collector 종료 시점에는 private 설정의 `dry_run: true`와 모든 장비의
      `allow_hardware_writes: false`를 재확인했습니다(등급 (c) 운영자 관측). 이 값은 현재
      런타임 상태를 보장하지 않으므로 **모든 live operation 직전에 mounted config와 이미
      로드된 프로세스를 다시 확인**합니다. 설정 파일만 잠그고 기존 write 가능 프로세스를
      남겨 두면 통제가 성립하지 않습니다
- [x] 일반 Observer와 실기 write 프로세스를 동시에 실행하지 않음
- [ ] 메인 수류 A/B와 바형의 실제 수조 내 위치·방향을 사진 또는 현장 메모로 확정
- [ ] 세 수류모터의 정확한 물리 모델 라벨 기록
- [ ] 현장에서 즉시 사용할 수 있는 물리 전원 차단 수단 확인

장비 데이터 경로는 `recovery container(host network) → 홈서버 routing → router의 lan→iot
forwarding → 장비 UDP/TCP`입니다. 홈서버와 router의 SSH는 배포·진단 관리 경로일 뿐, 제바오
패킷을 운반하는 SSH tunnel이 아닙니다.

마지막 세 항목은 운영용 group gain/phase를 확정할 때 필수입니다. 동일 Pro 두 대의 슬롯별
출력 의미만 확인하는 현재 저출력 시험은 좌우 위치를 판정 근거로 사용하지 않지만, 현장 전원
차단 확인 없이는 실행하지 않습니다.

생산용 3펌프 그룹에는 추가로 수조 크기, 펌프별 높이·방향, 암석과 사각지대, 리턴 출수 방향,
실측 최소 출력과 펌프별 보정값이 필요합니다. 이 정보 없이 gain·phase를 기본값으로 확정하지
않습니다.

---

# 부록 A — 동결된 write 하네스 계획 (historical, 2026-08-28 동결)

> **아래 "매 실기 직전 GO/NO-GO"부터 "사후 필수 복구"까지는 `jebao-flow` 코드가 장비에
> write하는 네이티브 ASYNC 하네스를 위한 계획이며, 2026-08-28부터 [`AGENTS.md`](../AGENTS.md)
> §1에 따라 동결됐습니다. 현재 유효한 절차가 아니라 보존된 기록입니다.**
>
> 이 하네스로는 새 실기를 실행하지 않습니다. 지금 유효한 내용은 문서 뒤쪽의
> **현재 상태와 유효 절차**입니다. 해제 시에는 이 부록을 다시 검토해
> 갱신해야 하며, 그대로 되살리지 않습니다.

## 매 실기 직전 GO/NO-GO

아래 **사전 실행** 항목은 과거 성공 기록으로 대신하지 않고 매 operation마다 새로 확인합니다.
하나라도 미확인 또는 실패면 전체 판정은 `NO-GO`이며 schedule·TimerON·Linkage write를 보내지
않습니다. 실행 중 성공조건이나 과거 원복 성공은 사전 GO를 대신하지 않습니다.

### 코드·재현 증거

- [ ] 전체 pytest와 Ruff, `git diff --check` 통과
- [ ] stop/cancel/deadline/safety/store drift 회귀에서 첫 역할 write 또는 write 재전송 0회 확인
- [ ] 독립 P0/P1 코드리뷰에서 미해결 안전·복구 결함 없음
  (P1 판정 기준은 [`AGENTS.md`](../AGENTS.md) §3을 따릅니다)

> 시뮬레이터 재현은 GO 전제조건이 아닙니다. 이전 판(2026-08-28)에는 "full composed simulator가
> 35/40 독립 출력 300초를 재현"할 것을 요구하는 항목이 있었으나 삭제했습니다. 그 시뮬레이터의
> fake는 `AutoFlow` 산출에 linkage 역할 항이 없어 원하는 답 외의 결과를 표현할 수 없었고,
> 미검증 질문의 답을 전제조건으로 요구하는 순환이었습니다. 자세한 규칙은
> [`AGENTS.md`](../AGENTS.md) §9.

### 배포와 단일 제어권

- [ ] 검토 완료 commit, Docker image ID와 recovery container image가 서로 일치
- [ ] worktree에 검토하지 않은 source 변경 없음
- [ ] Home Assistant의 기존 Jebao 직접 통합과 Jebao 앱 조작 중지
- [ ] 일반 `jebao-flowd` Observer 중지, recovery supervisor만 실행
- [ ] recovery stop grace가 최악 복구 경로보다 짧지 않음; 시험 중 `compose stop/down` 금지

### identity·망·현재 상태

- [ ] discovery 결과가 정확히 6대이고 private MAC/device ID가 6/6 일치
- [ ] 선택한 두 장비가 서로 다른 physical binding이며 같은 Pro product key
- [ ] 선택한 두 장비만 write-enabled이고 나머지 네 장비는 write-disabled
- [ ] 두 Pro의 fresh probe가 online·무오류이고 schedule image 432-byte를 읽음
- [ ] router 경유 UDP 12414와 TCP 12416 경로 확인
- [ ] MQTT/HA 중단과 무관하게 recovery가 로컬 장비에 접근 가능
- [ ] 현장에서 즉시 사용할 수 있는 물리 전원 차단 수단과 담당자 확인

### 영속 복구 권한

- [ ] outer-control, temporary-schedule, schedule-linkage journal 모두 없음
- [ ] emergency-stop latch 없음, safety interlock epoch가 실행 전체에서 고정
- [ ] 두 Pro의 단일 장비 write qualification receipt가 현재 physical binding에 유효
- [ ] 원 controls와 두 432-byte schedule image를 첫 write 전에 fsync
- [ ] sentinel qualification 또는 유효한 동등 영수증이 exact restore까지 완료

### 이번 ASYNC 슬롯별 출력 시험 계획

- [ ] 고정 계획이 `Constant A: master 31%, slave 32%`임을 확인
- [ ] 고정 계획이 `Sine B: master 35%, slave 40%, frequency 30`임을 확인
- [ ] 모든 planned/manual/transient Flow가 30~45% guarded 범위 안에 있음
- [ ] TimerOFF에서 pair skew ≤2초, lead >180초를 한 절대 30초 read-only budget 안에 확보
- [ ] clock retry 동안 controls와 schedule fingerprint가 변하지 않음
- [ ] receipt가 `earliest = sampled + min(lead) - 30초`, `latest = sampled + max(lead)`를
  모두 보존하고 안정 구간은 latest 이전 sample을 계산하지 않음
- [ ] TimerON 뒤 exact schedule/control/Auto A를 한 fresh pair에서 확인
- [ ] TimerON current-A의 raw `NowTime`을 새 시간 권한으로 사용하지 않음
- [ ] TimerON current-A capture 이후 첫 Linkage intent/write 전 추가 네트워크 read나 임의 settle 없음

## 실행 중 성공 판정

아래 항목은 모두 관찰돼야 기능 성공입니다. 중간에 원복이 성공해도 기능 성공으로 바꾸지
않습니다. native `async_slave` 시험은 software `Anti Phase`와 별개이며, 이번 시험은 슬레이브가
자기 슬롯의 `AutoFlow`를 적용하는지만 판정하고 물리 파형·위상을 증명하지 않습니다.

- [ ] MASTER와 ASYNC_SLAVE role write가 각각 정확히 한 번만 실행
- [ ] B 전환의 다섯 필드가 허용된 A→B 방향으로만 수렴
- [ ] 마스터 35%와 슬레이브 40%가 구분된 채 최소 300초 안정 유지

## 사후 필수 복구

성공·실패·취소와 무관하게 모두 확인합니다. 하나라도 실패하면 `RECOVERY_REQUIRED`로 남기고
journal을 지우거나 Observer·앱을 다시 시작하지 않습니다.

- [ ] 실패·취소·성공 모두 `slave role → master role → TimerOFF → schedule bytes → outer controls` 순서로 원복
- [ ] 세 journal 제거 뒤 독립 fresh session 두 번에서 원 controls와 schedule bytes exact 확인
- [ ] recovery 설정을 `dry_run: true`, 모든 장비 `allow_hardware_writes: false`로 다시 잠금
- [ ] write-ready recovery 구성을 해제하고 일반 Observer 재시작
- [ ] Observer에서 6대 online/healthy, identity 6/6 및 두 Pro 원상태 확인
- [ ] terminal evidence, commit, image ID와 원복 검증 결과 보존

---

# 현재 상태와 유효 절차

> 여기서부터는 부록 A(동결된 write 하네스)가 아니라 **현재 유효한 내용**입니다.

## 반복 실패와 정보 부족의 구분

최근 실패는 ASYNC 대상이나 wire schema를 몰라 같은 지점에서 반복된 것이 아닙니다.

- retry4는 두 역할 write와 경계 관찰까지 도달했지만 `Mode`, `Frequency`, `AutoMode`,
  `AutoFlow`, `AutoFreq`가 서로 다른 report에서 수렴한다는 **재구성된 운영자 관측**을 원자
  상태로 모델링해 fail-closed 됐습니다. 이 실행의 raw artifact는 남아 있지 않습니다.
- retry5는 role write 전 새 세션에서 읽은 두 `NowTime`의 **pair skew가 2초 gate를 초과**해
  `preflight_clock`으로 종료됐습니다. 이 실행이 확정하는 것은 거기까지이며,
  §실측된 비원자 상태 갱신의 22~25초 batch 주기를 재확인한 것이 **아닙니다.**

두 경우 모두 원 controls와 두 432-byte image는 복구됐습니다.

작업트리에는 TimerOFF에서 한 번만 pair-clock 권한을 만들고 TimerON current-A를 의미 상태로만
검증하는 변경이 있으나, 이는 **격리된 미검증 worktree 프로토타입(quarantined unvalidated
worktree prototype)**이며 현재 구현이 아닙니다. 실기에 한 번도 돌지 않았고 병합되지 않았습니다
([`AGENTS.md`](../AGENTS.md) §5).

## 현재 판정

| 질문 | 판정 |
|---|---|
| ASYNC 대상 두 Pro를 정확히 식별했는가 | PASS |
| 필요한 로컬 transport와 Pro wire schema가 있는가 | PASS |
| 동결 write 하네스가 자신이 저장한 schedule image를 exact restore할 수 있는가 | PASS — 기존 하네스 capability 범위 |
| 앱이 만든 제3자 baseline을 standalone으로 복원할 승인된 exact restore 수단과 권한이 있는가 | **BLOCKING / 미확보** — 선행조건 2; integrated harness가 자기 baseline을 소유하는 단회와 구분 |
| native MASTER/SLAVE 역할 write와 Sync 복구가 검증됐는가 | PASS |
| 최소 write-free read-only collector가 실기 검증됐는가 | PASS — 18/18 pair, 36/36 explicit reply, offline verify PASS |
| 세 수류모터를 같은 증거 기준으로 read-only 수집할 수 있는가 | **NOT IMPLEMENTED** — 현재 collector와 predicate는 두 Pro 전용이며 가산적 v3가 필요 |
| 바형 수류모터에 승인된 exact restore 수단이 있는가 | **NOT IMPLEMENTED / WRITE PROHIBITED** — 384-byte schedule snapshot·restore 부재 |
| 출력·주파수의 장비 유효 endpoint와 step이 검증됐는가 | **UNKNOWN / ENDPOINT WRITE PROHIBITED** — schema 범위와 실제 안전 범위를 동일시하지 않음 |
| read-only 관측만으로 전체 native mode와 accepted 범위를 확정할 수 있는가 | **NO / OUT OF READ-ONLY SCOPE** — 현재 값·기존 slot 관측과 전체 열거·허용 범위를 구분 |
| Pro `AutoFeedTime=0`과 profile `1..60` 선언이 정합한가 | **UNKNOWN** — 36/36 raw에서 0, write admission 범위는 별도 승인 없이 완화 금지 |
| 각 native mode 설정값의 물리 유량·파형 효과가 계측됐는가 | **UNKNOWN / OUT OF CURRENT MEASUREMENT SCOPE** |
| (Q2-narrow) ASYNC 상태에서 slave의 staged B Flow가 master와 다르게 보고됐는가 | **CONFIRMED (2026-08-30, 이 Pro pair·고정 계획·하네스 direct `0→3` role 전환 범위)** — slave 32→40, master 31→35와 상이; 900초 epoch와 exact restore 완료 |
| (Q2-target) slave가 master Mode·timing을 따르면서 자기 모드별 Flow를 적용하는가 | **UNKNOWN / 교정 단회 실행 소진 (2026-09-01)** — master 반대 Mode 전환은 raw 91개로 확인했지만 slave pair read가 90회 모두 실패해 slave raw가 없음; [실행 기록](runs/2026-09-01-q2-target-corrected-a3adb738.md) 참조 |
| (Q2-slotflow) 같은 Mode·경계에서 slave가 자기 슬롯별 `Flow 35→47`을 적용하는가 | **UNKNOWN / 앱-sequence 단회 실행 소진 (2026-09-02)** — 앱과 같은 Sync 단계에서 master·sync-slave 지정, 양쪽 `TimerON`, 장비별 temporary schedule image 보존과 bounded active state를 확인했지만 runtime schedule 소유권은 판별하지 못했고 inactive manual `Mode/Frequency` drift gate가 ASYNC write 전에 중단; [실행 기록](runs/2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md) 참조 |
| (Q1) ASYNC에서 slave가 마스터와 다른 manual `Flow`를 유지하는가 | **UNKNOWN (PARKED / OUT OF CURRENT SCOPE, 2026-08-28)** — 아래 §park 참조 |
| 세 펌프의 물리 배치와 운영 gain/phase가 확정됐는가 | BLOCKED FOR PRODUCTION GROUPING |
| 리턴·도징 actuator가 실기 검증됐는가 | NOT IN CURRENT TEST SCOPE |
| 앱 없는 Wi-Fi provisioning과 control-mode reconnect restore가 준비됐는가 | NOT IMPLEMENTED |

현재 **production grouping 전체 판정은 NO-GO**입니다. 2026-08-30 단회 write 실기는 아래의
좁은 Flow 관측과 exact restore를 완료했지만, 사용자가 요구한 master Mode·timing + slave
per-mode Flow 분할 질문은 닫지 못했습니다. 2026-09-01 첫 판별 실기는 반대 Mode 계획을 적용하기
전 기존 동일-topology guard에서 종료됐습니다. topology를 교정한 단회 실행은 field schedule,
TimerON과 900초 epoch에 도달했고 master Mode·Flow 전환을 raw 재분석으로 확인했지만, slave pair read가 90회 모두
실패해 slave raw를 하나도 얻지 못했습니다. 따라서 질문은 계속 `UNKNOWN`이고 교정 단회 승인도
소진됐습니다. 두 실행 모두 종료 후 fresh raw 두 회에서 exact restore를 확인했습니다.

2026-09-02 추가: 동일-Mode 단회는 master의 `40 -> 35` 전환을 확인했지만 slave action `0x04`
411개가 모두 device-local 경계 전 report backlog였고, 앱의
`independent -> sync_slave -> async_slave`와 다른 direct role 전환을 사용했습니다. 따라서
Q2-slotflow도 계속 `UNKNOWN`이고 해당 단회 승인은 소진됐습니다. 이 실행도 종료 후 fresh raw
두 회에서 exact restore를 확인했습니다.

기존 단회 실기는 실제 장비가 서로 다른 B 출력을 300초 이상 유지한 durable sample을 냈습니다.
`master=35`, `slave=40`을 900초 epoch 끝까지 관측했고 상충 sample이 없었습니다. 다만 두
장비에 같은 Mode 순서와 경계를 staged했으므로 Mode 소유권을 판정하지 않습니다. 상세 증거,
정정과 exact-restore raw는
[`2026-08-30 Q2 attempt 05`](runs/2026-08-30-q2-attempt-05.md)에 있습니다.

## 2026-08-30 Q2 단회 실기의 좁은 결론

- 경계 전: master `constant/31`, async-slave `constant/32`
- 비원자 갱신 중 첫 관측: master `constant/31`, async-slave `sine/40`
- 수초 내 완전 수렴: master `sine/35`, async-slave `sine/40`
- 경계 후: 300초 안정 조건 및 900초 전체 epoch 동안 `35/40` 유지, 상충 0
- 복원: 두 역할 `independent`, 원 `TimerON`과 manual controls, 두 432-byte schedule image가
  fresh explicit raw에서 baseline과 byte-exact 일치

따라서 이 두 Local Wavemaker Pro의 현재 펌웨어·설정에서는 `async_slave` 역할 상태에서도
slave에 staged한 TimerON 스케줄과 master와 다른 `AutoFlow`가 유지됐습니다. 그러나 양쪽에
같은 Mode 순서를 넣었으므로 **slave가 master Mode를 따랐는지 자기 Mode 스케줄을 실행했는지는
증명하지 못했습니다.** protocol-reported Flow 외의 물리 유량·파형·위상도 증명하지 않습니다.

## park 및 재개 판정

아래는 2026-08-28 당시의 park 근거입니다. Q1은 현재 범위 밖으로 계속 park합니다. Q2의 좁은
Flow 차이는 관측됐지만, Mode·timing 소유권 질문은 판별력 부족이 확인돼 신규 단회 실기로
재개합니다.

### Q1 (manual `Flow` 독립) — PARKED / OUT OF CURRENT SCOPE

Q1도 `UNKNOWN`입니다. 당시 "유지되지 않음"으로 기록됐다가 delivery/full-state read-back
미증명으로 철회됐습니다([`docs/runs/`](runs/2026-08-26--2026-08-28-native-linkage-history.md)
§철회된 해석).

**Q1을 다시 실기로 시도하지 않습니다.** 재개하려면 두 가지가 모두 필요합니다.

1. software-independent actuator가 완성된 뒤에도 그 결과로 Q1이 답해지지 않음이 확인되고,
2. Q1의 답이 제품에 실제 가치를 준다고 판단되어 repository maintainer가 별도로 승인

이 조건 없이 Q1 실기를 반복하지 않습니다. 슬레이브별 개별 출력이 필요하면 모든 펌프를
`independent`로 두는 소프트웨어 그룹을 사용합니다.

### Q2-target (master Mode·timing + slave per-mode Flow) — UNKNOWN / 단회 실행 소진

2026-08-30 실행은 master와 slave 양쪽에 동일한 Mode 순서와 경계를 넣어 이 질문에
비판별적이었습니다. 2026-09-01에는 반대 Mode와 여섯 개의 겹치지 않는 Flow 값을 가진 첫 단회
판별 실기를 실행했지만, `TemporaryScheduleController`의 기존 동일-topology 전제가 첫 field
schedule write 전에 계획을 거부했습니다. 상세 기록은
[`2026-09-01 Q2-target 판별 시도`](runs/2026-09-01-q2-target-9c982c60.md)입니다.

같은 날 별도 승인과 해제 커밋으로 topology만 교정해 한 번 더 실행했습니다. 이번에는 field
schedule과 TimerON을 적용하고 900초 epoch를 완주했으며, master explicit raw 91개의 오프라인
재분석에서 `Sine/AutoFlow50 -> Constant/AutoFlow55` Mode·Flow 전환과 399.464초 유지가
확인됐습니다. Constant 슬롯의 wire `Frequency=0`과 보고 `AutoFreq=5` 차이는 해석하지 않습니다.
그러나 slave가 포함된 pair read는 90회 모두 `monitor_state_read`로 실패해 slave raw가
0개였습니다. 종료 후 fresh source-attested collector 두 회에서는 원 control과 두 432-byte
schedule image의 exact restore가
다시 확인됐습니다. 상세 기록은
[`2026-09-01 Q2-target topology 교정 실행`](runs/2026-09-01-q2-target-corrected-a3adb738.md)입니다.

이 질문은 계속 `UNKNOWN`입니다. 교정 단회 승인도 소진됐습니다. 같은 실기를 반복하기 전에
보존 raw와 exact commit으로 live ASYNC slave의 role/state-dependent explicit reply와 pair-read
경로를 write 없이 분석해야 합니다. 그 뒤에도 실기가 필요하면 repository maintainer의 새로운
명시적 승인과
별도 동결 해제 커밋이 필요합니다. 승인 전 기본 경로는 질문을 park하고 software-independent
actuator와 그룹 런타임을 진행하는 것입니다.

### Q2-slotflow (master Mode·경계 + slave 슬롯별 Flow) — UNKNOWN / 단회 실행 소진

2026-09-02에는 사용자가 지정한 동일-Mode 계획으로 master
`Sine/F50/Flow40 -> Constant/Flow35`, slave
`Sine/F50/Flow35 -> Constant/Flow47`을 같은 device-local 경계에 넣고 900초를 관측했습니다.
master raw는 `40 -> 35` 경계를 확인했습니다. slave raw sink에도 411개의 action `0x04` report가
남았지만 frame-local `NowTime`은 첫 `12:12:00`부터 마지막 `12:19:00`까지 모두 `12:20:00`
경계 전이었습니다. host 수신 대비 지연은 약 16.7초에서 491.1초로 커졌으므로, 경계 후 수신된
`Sine/35`를 경계 후 상태로 읽지 않습니다. valid post-boundary slave sample은 0개이고 판정은
`UNKNOWN`입니다. 상세 raw·복원 증거는
[`2026-09-02 Q2-slotflow 재검증`](runs/2026-09-02-q2-slotflow-a00f4b1.md)에 있습니다.

같은 411개 raw를 exact `a00f4b1` decoder로 전수 재도출하면 모두 `TimerON=true`,
`Linkage=async_slave`이고 temporary schedule image 안의 B entry도 `Constant/47/F0`로
존재합니다. 따라서 role 진입이 Timer를 끄거나 B slot write가 사라졌다고 볼 근거는 없습니다.
다만 이 frame들이 모두 경계 전이므로 B 전이가 실제 일어났는지는 여전히 판정하지 못합니다.

실행 후 cached Pro app template의 Linkage UI dataflow를 다시 추적했습니다. 앱은 independent
slave에서 먼저 `Linkage=2`를 보내 Slave/Sync 상태를 만들고, 그 뒤 활성화되는 Async 선택이
별도 `Linkage=3`을 보냅니다. role write는 partial object이며 Flow·Timer·schedule을 함께 보내지
않습니다. 반면 이번 하네스는 `Linkage=3` 한 번으로 직접 전환했습니다. final datapoint는 같지만
앱 UI와 sequence가 동등하지 않으며, intermediate `sync_slave` 전이가 펌웨어 동작에 필요한지는
아직 측정하지 않았습니다. 이전 attempt 05는 같은 direct `0 -> 3` 경로에서도 slave
`32 -> 40`을 관측했으므로 이 차이를 이번 미관측의 원인으로 확정하지도 않습니다. 따라서 이번
결과를 native capability `FAIL`로 쓰지 않습니다.

write-side rollback은 terminal로 끝났고 서로 다른 두 fresh collector에서 원 control 여섯 필드와
두 432-byte schedule image digest가 pre-write baseline과 byte-exact하게 일치했습니다. 이 단회는
소진됐으며 자동 재시도하지 않습니다. corrected run은 새 명시 승인과 별도 동결 해제 커밋으로
앱과 같은 `0 -> 2 -> 3` role sequence, stale report를 배제하는 fresh post-boundary evidence,
기존 identity·single-write·journal·rollback 권한을 함께 고정할 때만 검토합니다.

#### 2026-09-02 앱 role sequence 교정 재개 결정 — 계획만 승인, write 권한 미생성

repository maintainer는 위 `UNKNOWN` 뒤 앱과 같은 `independent -> sync_slave -> async_slave`
순서를 한 번 적용하고, `sync_slave` write 뒤 새 paired session에서 master=`master`,
slave=`sync_slave`, 양쪽 `TimerON`, temporary schedule image와 Flow 상한을 적극 확인한 경우에만
`async_slave`로 진행하라고 지시했습니다. stale backlog를 배제하기 위해 boundary monitor도
pair마다 transport를 새로 만듭니다. 질문·판정·write·복원 범위는
[`앱-sequence 교정 단회 계획`](native-async-slave-slot-flow-app-sequence-recheck.md)에 고정합니다.

repository maintainer 겸 on-site hardware approver는 물리 차단 수단의 구체적 열거를 면제하고
현장에 수습 가능한 인원이 있다고 확인했습니다(등급 (c) reconstructed operator observation).
이 operation별 결정은 위 설치 환경 기준선과 매 실기 직전 GO/NO-GO의 물리 전원 차단 체크박스를
충족으로 바꾸지 않습니다. 계획 문서 커밋만으로 동결이 해제되거나 장비 write 권한이 생기지
않습니다. 실제 실행에는 [`AGENTS.md`](../AGENTS.md) §1의 별도 단회 해제 커밋과 첫 write 직전
유효한 물리 차단 확인이 계속 필요합니다.

#### 2026-09-02 앱 role sequence 교정 실행 — Sync 지정 확인, Q2-slotflow UNKNOWN

별도 해제와 exact `ced43a9` image로 앱과 같은 첫 단계인 `independent -> sync_slave`를 한 번
적용했습니다. 직후 fresh raw pair에서 master=`master`, slave=`sync_slave`, 양쪽 `TimerON`,
서로 다른 temporary schedule image가 장비별로 그대로 남아 있고 active Flow `40`·`35`인 bounded
state가 확인됐습니다. 따라서 앱 sequence의 Sync 역할 지정, TimerON, 장비별 schedule image
보존은 실제 장비에서 성립합니다. 다만 양쪽 A의 mode·frequency·boundary가 같았으므로 이 raw는
master schedule 추종과 slave 로컬 schedule 실행을 구분하지 않습니다.

같은 slave raw에서 inactive manual tuple은 staged safe manual `Constant/35/F20`이 아니라 active
A와 같은 `Sine/35/F50`이었습니다. 기존 audited pair assertion은 saved snapshot과 다른
`Mode/Frequency`를 drift로 거부했고, durable contract에 따라 `async_slave` write는 0회였습니다.
이 변화가 firmware의 active-slot mirror인지 다른 role side effect인지는 확정하지 않습니다.
ASYNC와 900초 boundary epoch에 도달하지 않았으므로 slave의 `35 -> 47` 적용은 계속
`UNKNOWN`입니다. 상세 raw·진단·복원 증거는
[`2026-09-02 앱-sequence 교정 실행`](runs/2026-09-02-q2-slotflow-appseq-sync-gate-ced43a9.md)에
있습니다.

automatic rollback은 terminal `outer_restored`로 끝났고, 서로 다른 두 source-attested fresh
collector가 모두 `2/2 pair`, `4/4 action 0x03`, offline verify PASS를 냈습니다. 원 control 여섯
필드와 두 432-byte schedule image digest는 pre-write baseline과 byte-exact하게 같았습니다.
Home Assistant는 재시작하거나 조작하지 않았고 locked recovery runtime만 실행 전후 stop/start
했습니다.

이 실행은 같은 질문에 대한 오늘 두 번째 live operation이므로 §7에 따라 세 번째 실기를 하지
않고 다시 park합니다. 대안은 (1) Q2와 무관하게 software-independent actuator·그룹 런타임을
진행하거나, (2) native 최적화 가치가 남는다고 새로 판단할 때 먼저 master/slave staged
mode·frequency 또는 boundary를 다르게 한 최소 Sync 관측으로 runtime schedule 소유권을 구분한
뒤, 그 결과 위에서 inactive manual `Mode/Frequency` drift와 core Sync 권한을 분리한 ASYNC
계획·새 승인·별도 단회 해제를 만드는 것입니다. park해도 제품 경로에는 영향이 없습니다.

### 2026-08-28 당시 park 사유

- 실기 13회 중 이 질문(슬롯별 `AutoFlow` 독립 적용)을 **직접 시도한 것은 5회**이고, 그중
  측정 지점(A→B 경계 관찰)에 도달한 것은 1회(retry4)뿐입니다. 그 1회도 5개 필드의 비원자
  수렴을 원자 전환으로 모델링해 fail-closed 됐습니다. 전체 이력은
  [`docs/runs/`](runs/README.md).
- 답이 PASS든 FAIL이든 다음 개발 작업이 같습니다. PASS여도 `native_linked`는
  `src/jebao_flow/groups/calculator.py`에서 거부되고 `src/jebao_flow/mqtt/service.py`가
  `native_linkage_not_qualified`를 반환하며, `src/jebao_flow/app.py`는 실제 command executor
  없이 서비스를 만듭니다. FAIL이어도 software-independent actuator와 그룹 런타임이 필요합니다.
  즉 이 질문은 **개발을 막지 않습니다.**
- 따라서 당시에는 이 `UNKNOWN`을 park했습니다. park는 실패가 아니었습니다.

### 당시 재개 조건 (2026-08-30 단회 승인으로 충족)

다음 중 하나가 성립하면 재개를 검토합니다.

- 읽기 전용 관측이 **실제로 수행된 뒤** 결론을 내지 못했고, 그 이유가 **`jebao-flow` write
  없이는 원리적으로 구성할 수 없는 조건** 때문임이 기록으로 확인된 경우
  (관측이 선행조건 미충족으로 실행되지 못한 것은 이 조건에 해당하지 않습니다)
- software-independent actuator와 그룹 런타임이 동작하고, native pair가 그 위에서 실제로
  추가 가치를 준다고 판단되는 경우

재개는 [`AGENTS.md`](../AGENTS.md) §1의 동결 해제 절차(repository maintainer의 명시적 승인 +
별도 커밋)를 따릅니다. 장비에 대한 실제 write는 on-site hardware approver가 명시적으로
승인하고 현장에서 감시하며, 그 승인 범위 안에서 에이전트가 원격으로 실행할 수 있습니다
(앱 조작처럼 물리적 접근이 필요한 단계는 approver가 수행합니다).

2026-08-30에는 두 역할의 별도 승인과 단회 해제 커밋으로 좁은 Flow 관측을 완료했습니다. 현재
판별 실기는 그 실행의 반복이 아니라 Mode 소유권을 가르는 새 질문이며, repository maintainer의
새 명시적 승인과 별도 동결 해제 커밋을 요구합니다.



## 읽기 전용 관측 절차 — 현재 판정 **NO-GO / BLOCKED**

Q2를 `jebao-flow` write 없이 판정하려는 절차입니다. **남은 선행조건 2가 충족되기 전에는
앱 live-write와 measurement epoch를 실행하지 않습니다.** 선행조건 1의 collector만 현재
실행 가능하며, 그 transport·timing pilot은 완료됐습니다.

이 역사적 절차는 양쪽 Mode를 같게 두므로 Q2-narrow의 Flow 차이만 판정하며,
Q2-target(master Mode·timing + slave per-mode Flow)에는 비판별적입니다. Q2-target의 현재
실행 기준은 [`신규 판별 실기 계획`](native-async-per-mode-flow-test-plan.md)을 따릅니다.

### "write 0회"의 정확한 범위

**measurement epoch — 관측 시작부터 종료까지 — 동안 `jebao-flow` 코드는 장비에 write를
보내지 않습니다.** 이 구간에는 저널, 리스, 안전 epoch, receipt가 필요 없습니다.

**시험 조건 구성(제바오 앱으로 역할과 시간표를 설정하는 것)은 write 0회가 아닙니다.**
그것은 on-site hardware approver가 **명시적으로 승인하고 현장에서 감시하는 별도의
live-write operation**이며, 그 승인 범위 안에서 에이전트가 원격으로 실행할 수 있습니다
(앱 UI 조작처럼 물리적 접근이 필요한 단계는 approver가 수행합니다). 아래 안전 순서를 따릅니다. 앱 조작은 [`AGENTS.md`](../AGENTS.md) §1 동결의 대상이 아니지만,
장비 상태를 실제로 바꾸는 행위이므로 baseline 보존과 **검증된 복원 수단** 없이 시작하지
않습니다.

### 왜 관측으로 답할 수 있는가

판정에 쓰는 값의 출처가 write 하네스와 같습니다.

- `_observed_auto()`는 `state.observed_attributes["AutoFlow"]`를 읽습니다
  (`src/jebao_flow/devices/schedule_linkage.py`)
- LAN 드라이버가 `Auto*`와 `Linkage`를 그 `observed_attributes`에 넣습니다
  (`src/jebao_flow/devices/lan.py`)
- 48슬롯 시간표와 장비 자체 시계는 같은 452-byte 상태 프레임에서 디코딩됩니다
  (`src/jebao_flow/protocol/schedule.py`)
- collector와 판정 로직은 `AutoMode`/`AutoFlow`/`AutoFreq`를 **관측값으로만** 사용합니다.
  현재 Pro schema에서는 세 필드도 writable kind로 선언돼 있으므로, 이 사실을 장비가 쓰기를
  지원한다는 근거로 사용하지 않고 복원·actuator의 write allowlist에도 넣지 않습니다

`AutoFlow`는 펌웨어가 보고한 유효 출력입니다. 물리 유량까지 증명하지는 않지만, 동결된 write
하네스도 그 범위 이상은 증명하지 못합니다.

## 선행조건 1 — 최소 read-only collector (완료, 2026-08-28)

**기존 `jebao-flowctl probe`로는 이 관측을 실행할 수 없습니다.** 세 가지가 이 문서의
원칙과 충돌합니다.

- **IP로만 대상을 지정하고 expected physical binding을 검증하지 않습니다.** 주소가 바뀌거나
  잘못 지정되면 다른 장비를 측정하고도 알 수 없습니다.
- **`read_raw_state()`의 `accept_reports` 기본값이 `True`이고 `cli.py`가 기본값으로
  호출합니다.** 즉 unsolicited push나 queued reply를 상태로 받아들일 수 있습니다. 이는
  §통신 규격 기준선의 "상태 push나 queued reply를 권한 근거로 삼지 않고 새 인증 세션의
  explicit reply로 검증" 원칙과 정면으로 충돌합니다.
- **출력에 수집 시각이 없습니다.** 경계 판정에 필요한 시간축을 만들 수 없습니다.

따라서 전용 최소 read-only collector를 구현했습니다. 요건과 실기 확인 결과는 다음과 같습니다.
(동결된 write 하네스와 무관한 별도의 읽기 전용 도구입니다.)

- [x] **매 fresh session과 매 sample마다 expected identity binding 검증** — private baseline의
      물리 바인딩과 일치하지 않으면 그 sample을 무효로 처리하고 기록합니다. 세션 시작 시
      한 번만 확인하는 것으로는 부족합니다(주소 재할당·재연결 중 대상이 바뀔 수 있음)
- [x] **`accept_reports=False`**로 explicit reply만 상태로 인정
- [x] 샘플마다 **UTC 시각과 monotonic clock을 함께** 기록
- [x] 샘플의 **시작·종료 시각**을 각각 기록 (읽기에 걸린 시간을 알 수 있어야 함)
- [x] **장비별 완료 시각과 pair 간 gap** 기록 — 순차 읽기이므로 A와 B는 같은 시각이 아님
- [x] atomic write + fsync **manifest**와 각 raw 산출물의 **digest**

실기 근거는 [`docs/runs/2026-08-28-pilot-2bd1bf97.md`](runs/2026-08-28-pilot-2bd1bf97.md)입니다.
두 역할의 18/18 pair와 36/36 explicit reply가 accepted였고 같은 commit의 offline verifier가
원본 프레임, ordinal·attempt 집합, digest, identity binding과 terminal marker를 재검증했습니다.
이 완료는 **collector 선행조건만** 닫으며, Q2나 선행조건 2를 닫지 않습니다.

manifest와 raw 산출물은 gitignored private 경로에 둡니다. 공개 문서·`docs/runs/`에는
**실제 MAC이나 device ID를 적지 않고**, opaque artifact id 또는 안전한 상대 논리 경로,
UTC span, SHA-256 identity binding, digest만 남깁니다.

## 선행조건 2 — 검증된 복원 수단 (미확보, BLOCKING)

**"앱으로 되돌린 뒤 exact 비교"는 복구 경로가 아닙니다.** 제바오 앱이 원래의 432-byte
schedule image를 byte-exact로 재현한다는 증거가 없습니다. 비교는 복원이 성공했는지
확인하는 수단일 뿐, 복원 자체를 보장하지 않습니다.

첫 앱 write **이전에** 다음이 실제로 준비·검증돼야 합니다.

- [ ] 원 controls / `Linkage` / `TimerON` / 두 432-byte schedule image를 exact로 되돌릴 수 있는
      **승인된 수단**과 그 수단을 쓸 **권한**이 존재
- [ ] 그 수단이 실제로 exact 복원을 만든다는 것이 **사전에 확인**됨
- [ ] 복원에 걸리는 시간과 실패 시 남는 상태를 미리 파악

### 복원 순서 (수정 후보, 아직 실장 qualification 전)

기존에 적혀 있던 `slave detach → master detach → TimerOFF` 순서는 안전 순서로 확정할 수
없습니다. `Linkage` 역할 변경이나 `TimerOFF`가 예약 운전 뒤의 latent manual
`Mode`/`Flow`/`Frequency`를 활성화할 수 있기 때문입니다. 2026-08-27 운영 기록에도 TimerON
상태의 한 장비가 manual `Flow=89`를 보존한 사례가 있습니다(등급 (c),
[`native-linkage.md`](native-linkage.md),
[`pre-hardware-test-checklist.md`](pre-hardware-test-checklist.md)). 이는 현재 baseline 값의
증거가 아니므로, 현재 값은 보존된 2026-08-30 raw series에서 오프라인으로 재도출하고 live
operation 직전 fresh baseline으로 다시 확인합니다.

standalone 복원 수단은 아래 후보 순서를 구현하고 fault injection·실장 qualification을 거쳐야
합니다. qualification 전에는 이 순서를 **검증된 수단이라고 부르지 않으며 관측은 계속
`NO-GO`**입니다.

1. 앱을 열기 전에 fresh explicit state에서 원 controls·roles·두 432-byte image를 durable
   baseline에 fsync하고, restore payload의 byte-exact round trip을 오프라인으로 검증
2. baseline이 `ON + TimerON + independent`인지, manual Flow와 active schedule Flow가 승인된
   operation ceiling·장비 범위·step 안인지 확인. 실패하면 write 0회로 `NO-GO`
3. **slave**에 `{SwitchON=true, TimerON=false, Linkage=independent, Mode=constant,
   Flow=safe, Frequency=safe}`를 **하나의 control frame**으로 정확히 한 번 전송
4. **master**에도 같은 atomic safe-fallback frame을 정확히 한 번 전송
5. 두 장비의 **exact 432-byte schedule image** 복원
6. **slave**, 이어서 **master**에 원 `{SwitchON, TimerON, Linkage, Mode, Flow, Frequency}`를
   각각 **하나의 atomic frame**으로 정확히 한 번 복원
7. writer를 종료한 뒤 별도 fresh session의 bounded read-only convergence로 원 controls,
   `Linkage`, `TimerON`, 두 432-byte image의 exact 일치를 검증

연결된 `async_slave`에 manual Flow만 먼저 staging하는 것은 parked Q1을 다시 실행하는 것이므로
금지합니다. 역할 해제와 `TimerOFF`를 별도 frame으로 나누는 것도 latent Flow 노출 구간을 만들기
때문에 금지합니다. 어느 action의 ACK나 결과가 불명확해도 같은 control frame을 재전송하지 않고,
fresh explicit state가 exact target을 증명할 때만 완료로 기록합니다. 그렇지 않으면 durable
journal을 유지한 채 `RECOVERY_REQUIRED`로 중단합니다.

각 단계는 operation manifest에 기록된 승인 범위 안에서만 수행하고, 어느 단계든 실패하면
아래 escalation으로 넘어갑니다. 다음 단계로 진행하지 않습니다.

### 2026-08-30 baseline의 오프라인 admission 재검증

보존된 series `JFS-a2f44ded609b34adab1425c1dcc40c0e`(artifact digest
`df6803b99548ffd68b910518cce33d4277859213a238b1ae4e1381671d29ddf2`)의 ordinal 0 raw
explicit-reply frame을 다시 디코딩했습니다. transport header 뒤 452-byte status에서 manual
controls와 48개 9-byte slot을 직접 재도출했으므로 아래 장비 상태 값은 등급 (a)입니다.

| 논리 역할 | latent manual | active non-feed schedule Flow 범위 | admission |
|---|---|---:|---|
| A | `constant`, `Flow=30`, `Frequency=32` | 30~60 | 범위 관점 PASS |
| B | `random`, `Flow=89`, `Frequency=34` | 50~80 | **FAIL (`89 > 80`)** |

따라서 이 preserved baseline을 그대로 사용한 standalone restore qualification은 `NO-GO`입니다.
이 결론은 현재 live state가 아직 같다는 뜻이 아닙니다. 실기를 재개하려면 on-site approver가
별도의 승인된 앱 operation으로 B의 latent manual Flow를 먼저 안전 범위로 정규화하고, 앱을
완전히 종료한 뒤 fresh explicit baseline을 새로 보존해야 합니다. 에이전트가 이 값을 우회하거나
restore 경로에서 clamp하지 않습니다.

### 2026-09-01 current live admission 재확인

exact `89119bc`의 source-attested write-free collector가
[`JFS-780584017739a4b193c5bb157d557802`](runs/2026-09-01-current-baseline-78058401.md)
에서 2/2 pair와 4/4 explicit-reply raw frame을 보존했고 offline verify를 통과했습니다. 두 fresh
sample 모두 역할 A는 latent manual `constant / Flow=30 / Frequency=32`, 역할 B는
`random / Flow=89 / Frequency=34`였으며 `TimerON / independent`였습니다. 두 schedule
image digest도 값이 커밋된 2026-09-01 corrected-run restore 기록과 byte-exact하게
같았습니다.

따라서 위 표의 역할 B `Flow=89`와 non-feed schedule `Flow=50..80`은
`2026-09-01T14:19:19Z..14:20:11Z` current raw에서도 재도출됐고, 두 값을 비교한
`89 > 80` admission FAIL이 유지됩니다. exact explicit-reply frame은 등급 (a)이고 이 raw에서
두 값이 오프라인 재도출됐습니다. persisted decoded summary는 등급 (b)이며 raw 재계산과
일치했습니다.

이 artifact는 **pre-normalization baseline**입니다. 앱 정규화가 역할 B의 manual controls를
바꾸므로 정규화 직후부터 이 artifact를 복원 기준으로 쓸 수 없고 선행조건 2를 해소하지도
않습니다. 앱 또는 standalone restore 수단에 의존하는 ASYNC role operation은 앱 정규화와
정규화 후 fresh baseline 재보존 전까지 `NO-GO`입니다.

### integrated harness가 자기 baseline을 소유하는 단회와의 구분

위 standalone `NO-GO`는 동결 write 하네스가 자기 operation 시작 시 snapshot한 baseline과 exact
rollback을 직접 소유하는 capability까지 철회하지 않습니다. 이 capability는 2026-08-30 attempt
05와 2026-09-01 corrected run에서 원 controls와 두 432-byte image의 byte-exact 복원을 완료했고,
현재 판정 표에서도 별도 PASS로 유지합니다.

repository maintainer가 2026-09-02에 요청한
[`동일-Mode slave 슬롯별 Flow 단회 재검증`](native-async-slave-slot-flow-recheck.md)은 앱이나
standalone restore를 사용하지 않습니다. 이 한 operation은 아래 조건을 모두 만족할 때만 기존
integrated capability 안에서 admission을 검토합니다.

- fresh preflight가 원 controls와 두 image를 새 operation journal에 직접 fsync
- 시작 시 B의 latent `Flow=89`를 그대로 둔 채 `TimerOFF`를 보내지 않고,
  `TimerOFF + independent + Constant + Flow 35 + Frequency 20`을 한 frame으로 적용
- 종료 시 role detach와 safe TimerOFF 뒤 원 image를 먼저 복원하고, 원 `TimerON + Flow=89`를
  나머지 원 controls와 같은 기존 audited outer-control frame으로 복원. 자동 rollback이
  terminal이 아니면 기존 attended recovery만 수동 호출하고 새 실험·임의 write는 금지
- fixed plan의 Flow 상한 `47`, exact Frequency `50`, single-write, identity, journal, rollback
  권한을 별도 동결 해제 커밋에 고정
- 현장 감시자와 즉시 사용 가능한 물리 전원 차단 수단을 첫 write 전에 확인

이 지정 구분은 standalone restore 선행조건 2를 PASS로 바꾸거나, 다른 ASYNC role write를
허용하거나, B의 `89 > 80` admission FAIL을 지우지 않습니다. 위 exact integrated operation이
아니면 기존 `NO-GO`를 그대로 적용합니다.

**이 조건을 만족하지 못하면 관측은 `NO-GO`로 남깁니다.** 이 항목은 동결된 ASYNC 실험
하네스를 재가동하라는 뜻이 **아닙니다.** 하네스 재가동은 [`AGENTS.md`](../AGENTS.md) §1의
해제 절차를 따로 거쳐야 합니다.

위 exact integrated operation은 2026-09-02에 한 번 실행되고 terminal restore와 두 fresh
collector 검증까지 끝나 **소진**됐습니다. 위 목록은 당시 admission 근거이지 현재 살아 있는
write 권한이 아닙니다. 결과와 새로 발견된 앱 role sequence 차이는
[`Q2-slotflow 실행 기록`](runs/2026-09-02-q2-slotflow-a00f4b1.md)을 따릅니다.

## 앱 live-write의 대체 통제

**앱을 통한 write는 코드 경로의 single-write 보장과 durable journal을 제공하지 못합니다.**
([`AGENTS.md`](../AGENTS.md) §2의 보호 규칙은 `jebao-flow` code-write path에 적용됩니다.)
따라서 앱 조작 phase에는 다음 대체 통제를 적용합니다.

순서가 중요합니다. **manifest fsync → 앱 열기 → UI 대상 대조 → write 승인·실행**입니다.
대상 대조는 write 전이어야 하지만, 대조할 기준(manifest의 binding)이 먼저 있어야 하므로
manifest 뒤에 옵니다.

- [ ] **operation manifest를 먼저 fsync** — 앱을 열기 **전에** 이번 operation의 의도,
      대상 장비(논리 역할과 SHA-256 identity binding), 그리고 **각 phase에서 허용되는 UI
      action을 순서대로 열거한 목록**을 기록
- [ ] 그 다음 **앱을 엽니다**
- [ ] **approver가 앱 UI에서 선택한 두 장비를 manifest의 binding과 대조**합니다.
      불일치하면 어떤 write도 하지 않고 중단합니다
- [ ] 대조 통과 후에만 **write를 승인·실행**합니다
- [ ] **manifest에 사전 열거된 각 UI action을 정해진 순서로 정확히 한 번씩** 수행합니다.
      manifest에 없는 조작을 하지 않습니다. (phase 1은 역할·`TimerON`·시간표처럼 복수 조작이
      필요하며, 그것들이 모두 manifest에 열거돼 있어야 합니다)
- [ ] **응답이 불명확하면 그 action을 반복 탭·재시도하지 않습니다.** 같은 조작을 다시 하는
      것이 코드 경로의 write 재전송에 해당합니다
- [ ] 응답 불명 시 즉시 **`UNKNOWN`으로 기록하고 복원 경로로 전환**합니다
- [ ] approver의 추가 승인 없이 재조작하지 않습니다

### 복원 실패 시 escalation

- 즉시 중단하고 추가 write·재시도를 하지 않습니다.
- 현재 상태와 baseline의 차이를 기록하고, `docs/runs/`에 복원 실패로 남깁니다.
- Observer·앱·다른 컨트롤러를 임의로 재시작하지 않습니다.
- on-site hardware approver와 repository maintainer 모두에게 보고하고, 다음 조치는 사람이
  결정합니다. 에이전트가 복원을 계속 시도하지 않습니다.

## 실행 순서 (두 epoch, 순서 고정)

최종 판정에는 **independent 대조군과 ASYNC 양쪽이 각각** 필요합니다. 따라서 epoch가 둘입니다.
각 epoch 시작 전에 **제바오 앱을 완전히 종료**합니다(백그라운드도 아님). epoch 동안 앱과
다른 컨트롤러를 사용하지 않습니다.

### 구현 상태 (2026-08-30, 실장 qualification 전)

- standalone exact-restore 후보는 sentinel qualification → baseline exact restore → phase 5용
  `PREPARED` journal staging을 구현했습니다. **fault-injection과 자동 테스트만 통과했으며 실장
  qualification은 아직 없습니다.**
- control/ASYNC 두 collector plan과 판정 manifest는 `prepare-pair` 한 번으로 앱 write 전에
  함께 fsync되고, 둘 다 같은 phase-5 operation id와 staged-record digest에 묶입니다.
- phase 5 성공으로 live journal이 정상 삭제된 뒤에도 immutable Q2 manifest와 qualified
  bundle의 고정 timestamp에서 당시 staged record를 재구성해 오프라인 판정할 수 있습니다.
  manifest를 처음 만들 때는 여전히 실제 `PREPARED` journal이 존재해야 합니다.
- 30분 슬롯과 300초 안정 구간을 쓰는 v1은 epoch당 최소 5,700초를 강제합니다. 20초 cadence면
  최소 286 pair(권장은 여유를 둔 더 큰 값), 30초 cadence면 최소 191 pair입니다. 이보다 짧은
  spec은 collector plan을 durable하게 만들기 전에 거부됩니다.
- 현재 source attestation은 exact commit의 **clean Git checkout**, 사용 가능한 `git` 실행 파일,
  source tree 안의 cached bytecode 부재를 요구합니다. 일반 daemon Docker image는 `.git`을
  포함하지 않으므로 패키징·CLI 확인용이며, Q2 수집 명령은 source를 read-only mount한 별도
  실행 환경에서 해당 조건을 충족해야 합니다. embedded image provenance가 구현되기 전까지
  image 단독 실행을 source-attested 수집으로 부르지 않습니다.

위 항목은 구현 후보의 정적 상태일 뿐 `NO-GO`를 해제하지 않습니다. 현재 preserved baseline의
B manual Flow admission 실패와 on-site 조건을 먼저 해결해야 합니다.

### phase 0 — 앱 write 이전 준비

- [x] 선행조건 1(collector) 완료 — [pilot 실행 기록](runs/2026-08-28-pilot-2bd1bf97.md)
- [ ] 선행조건 2(검증된 복원 수단) 충족
- [ ] private 설정을 `dry_run: true`, 모든 장비 `allow_hardware_writes: false`로 잠금
- [ ] **설정 파일 변경만으로는 부족합니다.** 이미 로드된 프로세스는 멈추지 않으므로,
      write 가능한 프로세스가 실제로 떠 있지 않은지 확인합니다
  - [ ] `jebao-flowd` Observer 중지 (로컬 클라이언트를 하나로 유지 — 두 로컬 클라이언트
        동시 연결은 아직 미검증 항목)
  - [ ] Home Assistant의 기존 Jebao 직접 통합 중지
  - [ ] **recovery supervisor 미실행 확인**
  - [ ] **one-shot writer 프로세스 미실행 확인**
- [ ] **미완료 legacy journal 없음 확인** — outer-control, temporary-schedule,
      schedule-linkage. exact-restore journal은 qualification 시작 전에는 없어야 하고, 완료 뒤 앱
      write를 시작할 때는 phase 5용 `PREPARED` 하나가 반드시 있어야 합니다
- [ ] **emergency-stop latch 없음 확인**
- [ ] 두 장비의 원 controls, `Linkage`, `TimerON`, 432-byte schedule image 전체를
      private 경로에 fsync로 보존. 이것이 복원 기준이며 이후 어떤 단계도 덮어쓰지 않습니다
- [ ] 물리 전원 차단 수단과 담당자 확인
- [ ] on-site hardware approver의 승인 확인
- [ ] **operation manifest fsync** — private baseline의 expected binding과 각 phase에서
      허용되는 UI action 목록을 순서대로 기록 (§앱 live-write의 대체 통제)
- [ ] fresh baseline으로 standalone exact-restore `prepare`와 두 attended qualification cycle을
      완료하고, qualified bundle과 phase 5용 `PREPARED` journal을 확인
- [ ] 앱을 열기 전에 `jebao-flow-q2-epoch prepare-pair`로 control/ASYNC 두 manifest를 함께
      만들고, 두 manifest가 같은 phase-5 record digest를 가리키는지 확인
- [ ] 그 다음 **앱을 엽니다**
- [ ] **대상 장비 대조** — approver가 **앱 UI에서 선택한 두 장비**가 manifest에 기록된
      physical binding과 같은 장비인지 대조합니다. 앱은 논리 이름으로 표시되고 collector는
      바인딩으로 확인하므로, 이 대조를 사람이 해야 서로 다른 장비를 설정하고 측정하는 경로가
      닫힙니다. **불일치하면 어떤 write도 하지 않고 중단합니다.** 실제 MAC·device ID는 공개
      문서에 적지 않습니다

### phase 1 — 대조군 설정 (앱, live-write)

**순서를 지킵니다. 앱을 닫은 뒤에 collector가 읽습니다. 앱과 collector를 동시에 연결하지
않습니다.**

- [ ] 두 장비를 `independent` + `TimerON`으로 두고 판별 설계대로 시간표 설정
- [ ] **앱 완전 종료** (백그라운드도 아님)
- [ ] collector가 fresh session으로 raw schedule과 state를 읽어 의도대로 들어갔는지 검증

### phase 2 — independent control epoch (`jebao-flow` write 0회)

- [ ] 정해진 cadence로 collector 실행, raw 보존
- [ ] **유효한 경계 3회**를 얻을 때까지 관측 (판정 조건은 아래)
- [ ] 슬레이브 시간표가 `independent` 상태에서 그 자체로 동작하는지 확인

### phase 3 — ASYNC 전환과 재검증 (앱, live-write)

- [ ] ASYNC로 전환 (A=`master`, B=`async_slave`), `TimerON` 유지
- [ ] **앱 완전 종료** (백그라운드도 아님)
- [ ] collector가 fresh session으로 raw schedule을 읽어 **슬레이브 슬롯 출력이 그대로 남아
      있는지 검증**

전환 후 슬레이브 슬롯 출력이 덮어써졌다면, 그 실행은 펌웨어 FAIL이 아니라 **"앱으로 시험
조건 구성 불가"**입니다. 그 자체를 `docs/runs/`에 기록하고, 제품 레벨에서 슬레이브의 독립
시간표가 허용되지 않는다는 신호로 다룹니다. 이 경우 phase 4로 넘어가지 않고 phase 5로 갑니다.

### phase 4 — ASYNC epoch (`jebao-flow` write 0회)

- [ ] 같은 cadence로 collector 실행, raw 보존
- [ ] **유효한 경계 3회**를 얻을 때까지 관측

### phase 5 — 복원과 검증

- [ ] 선행조건 2의 **복원 순서**대로 phase 0 baseline 상태로 복원
- [ ] **복원에 사용한 클라이언트(앱 또는 복원 도구)를 완전히 종료**
- [ ] 그 뒤 collector가 독립된 fresh session으로 원 controls, `Linkage`, `TimerON`, 두
      432-byte schedule image가 baseline과 **exact 일치**하는지 검증
- [ ] 불일치가 있으면 위 escalation을 따름
- [ ] 검증 완료 후에만 일반 Observer 재시작
- [ ] phase 5가 journal을 정상 정리한 뒤 control/ASYNC series를 오프라인 분류하고 두 receipt를
      결합. 이 단계는 장비 write를 하지 않으며 성공한 원복 journal의 잔존을 요구하지 않습니다

### 소요 시간

경계 간격이 30분이면 epoch당 유효 경계 3회에 최소 **1.5시간 + 마지막 경계 이후 안정 구간**이
필요하고, 무효 경계가 나오면 그만큼 늘어납니다. 두 epoch와 phase 0·1·3·5를 합치면
**최소 4시간 이상, 무효 경계를 감안하면 하룻밤 규모**로 계획합니다. 경계 간격을 바꾸면
이 값도 함께 바뀌므로 manifest에 실제 값을 고정해 기록합니다.

## 판별 설계

- MASTER(A): 하루 종일 변하지 않는 한 슬롯. Constant, **35%**
- SLAVE(B): 정해진 간격으로 교대하는 슬롯. Constant, **32% / 40%**
- 두 슬레이브 값이 모두 마스터 값과 다르므로 **모든 샘플이 판별력을 가집니다.**
  (마스터와 같은 값을 쓰면 그 구간에서 "마스터를 따라감"과 "자기 슬롯을 적용함"이
  구분되지 않습니다.)
- 모든 값이 설정된 power range/step과 안전 범위 안에 있어야 합니다. 안전 상한이 확인되지 않은
  값은 쓰지 않습니다.

## 판정 기준 — manifest에 사전 고정

**cherry-pick을 막기 위해, 아래 기준은 관측을 시작하기 전에 manifest에 기록하고 그 뒤로
바꾸지 않습니다.** 관측 후에 기준을 고르면 어떤 데이터로도 원하는 결론을 만들 수 있습니다.

사전에 고정할 값:

- **cadence** — 샘플 간격
- **최대 허용 pair gap** — 한 경계 판정에 쓰는 A·B 읽기 사이의 최대 시간차
- **freshness 기준** — 경계 전후 샘플이 얼마나 가까워야 하는지
- **boundary exclusion 규칙** — 어떤 경계를 무효로 버리는지
- **경계 이후 안정 구간 길이** — 단, `max(300초, pilot 유도 하한)` 아래로는 정할 수 없습니다

**값의 성격을 구분합니다. 전부 "하한"이 아닙니다.**

| 값 | 성격 |
|---|---|
| boundary exclusion 기준 | **하한** — 이보다 느슨하게 잡지 않음 |
| 경계 이후 안정 구간 | **하한** — 이보다 짧게 잡지 않음 |
| cadence | 목적에 맞는 **상한**(이보다 성기게 샘플링하지 않음) |
| 최대 허용 pair gap | **상한** — 이보다 큰 gap의 경계는 무효 |
| freshness | **허용 범위** — 경계 전후 샘플이 들어와야 하는 창 |

**근거 없이 정하지 않습니다.** 각 값은 **collector pilot artifact bundle의 등급 (a) raw와
등급 (b) host timing·manifest**에서 도출하고, 그 bundle의 **opaque id와 digest를 manifest에
연결**합니다. 첫 pilot은
요청 cadence 20초에서 실제 pair 시작 간격 21.2~22.5초, host-side pair completion gap
10.6~11.2초를 보존했습니다. 단일 395.191초 실행이라 tail margin과 300초보다 큰 안정 구간
하한을 제공하지 않으므로 숫자를 그대로 manifest 상한·하한으로 복사하지 않습니다.

§실측된 비원자 상태 갱신의 legacy 22~25초는 등급 (c)이지만 후속 pilot이 장비 시계 값과
host timing을 각각 등급 (a)/(b)로 보존했습니다. 다만 약 21.9초 sample cadence에 alias돼
실제 장비 주기를 분해하지 못하므로 **22.48초를 고정 주기나 강제값으로 쓰지 않습니다.**
임의의 숫자를 발명하지 않습니다.

### 유효한 경계의 조건

아래를 모두 만족하는 경계만 판정에 사용합니다. 버린 경계와 사유를 모두 기록합니다.

- **freshness** — 사전 고정한 기준을 만족
- **pair gap** — 사전 고정한 최대치 이내
- **identity** — 두 장비의 physical binding이 private baseline과 일치. 그 sample을 만든
  fresh session에서 검증됐어야 합니다
- **liveness** — 두 장비 모두 online, 오류 없음, 상태 프레임이 정상 디코딩됨
- **explicit reply** — 해당 샘플이 unsolicited report가 아닌 explicit reply로 얻어짐
- **role invariant** — 해당 epoch의 역할(대조군은 둘 다 `independent`, ASYNC epoch는
  A=`master`/B=`async_slave`)이 경계 전후 내내 유지됨
- **schedule invariant** — 두 장비의 48슬롯 시간표와 `TimerON`이 경계 전후로 변하지 않음

### 경계별 분류

분류는 **배타적**이며 아래 순서로 판정합니다. 위에서 걸리면 아래는 보지 않습니다.

| 순서 | 조건 | 분류 |
|---|---|---|
| 1 | 슬레이브 `AutoFlow`가 마스터 `AutoFlow`와 **동일한 값으로 고정** | **마스터 추종** |
| 2 | 마스터와는 다르지만 **기대한 슬롯 전환(32% ↔ 40%)을 하지 않음** | **슬롯 비적용** |
| 3 | 슬레이브가 자기 슬롯 값을 따라 바뀌고 마스터는 35% 유지 | **독립 적용** |
| 4 | `Auto*`가 유효하지 않거나 위 어디에도 맞지 않음 | **UNEXPECTED** — raw를 보존하고 분류 규칙을 오프라인에서 재검토 |

1번을 2번보다 먼저 보는 이유: 마스터와 값이 같으면 "따라간 것"과 "우연히 자기 슬롯 값이
같은 것"을 구분할 수 없으므로, 보수적으로 마스터 추종으로 처리합니다. 판별 설계가 두
슬레이브 값을 모두 마스터와 다르게 잡는 이유이기도 합니다.

### epoch 성립 조건

**두 epoch 각각에서** 다음 네 가지를 모두 만족해야 그 epoch의 분류가 성립합니다.

1. 같은 분류의 **연속된 유효 경계 3회**. 띄엄띄엄 고른 3회는 인정하지 않습니다
2. 해당 epoch 안에 **상충하는 유효 경계 0회**. 하나라도 다른 분류가 나오면 성립하지 않습니다
3. 3번째 경계 이후 **안정 구간** 동안 그 상태가 유지됨
4. 모든 경계가 §유효한 경계의 조건을 만족

**안정 구간 = `max(300초, pilot artifact bundle에서 유도한 하한)`.** manifest가 이 값을
300초 아래로 줄일 수 없습니다. pilot에서 유도한 하한이 300초보다 크면 그 값을 씁니다.

### 최종 판정 매핑

**control epoch가 "독립 적용"으로 성립해야 전체 관측이 유효합니다.** 대조군에서 슬레이브
시간표가 그 자체로 동작하지 않으면, ASYNC epoch에서 무엇이 나오든 원인을 역할에 귀속시킬
수 없습니다.

| control epoch | ASYNC epoch | 최종 판정 |
|---|---|---|
| 독립 적용 | 독립 적용 | **YES** — 슬레이브가 자기 슬롯 출력을 독립 적용 |
| 독립 적용 | 마스터 추종 | **NO** |
| 독립 적용 | 슬롯 비적용 | **NO** |
| 독립 적용 | UNEXPECTED 또는 미성립 | **UNKNOWN** |
| 독립 적용이 아님 | (무관) | **UNKNOWN** — 대조군 미성립. 시험 조건 자체를 신뢰할 수 없음 |
| 미성립 | (무관) | **UNKNOWN** |

혼합 결과, 불완전한 epoch, UNEXPECTED가 하나라도 남은 경우는 **항상 `UNKNOWN`**입니다.
"대체로 그런 것 같다"로 YES나 NO를 만들지 않습니다.

raw를 보존하므로 **분류 규칙은 실기를 다시 태우지 않고 다시 적용할 수 있습니다.** 다만
사전 고정한 판정 기준을 사후에 바꿔서 결론을 만들지는 않습니다.

## 결과 기록

`docs/runs/`에 실행 파일을 만들고 위 "현재 판정" 표의 해당 행을 바꿉니다. manifest에 고정한
기준, 유효/무효 경계 수와 버린 사유, opaque artifact id, UTC span, SHA-256 identity binding,
digest를 함께 남깁니다. 관측으로도 결론이 나지 않으면 그 사실과 이유를 기록하고 park를
유지합니다.
