# Jebao 현재 작업 To-do

마지막 갱신: 2026-09-11 18:32 KST

> 이 파일은 진행에 따라 갱신하는 운영 체크리스트이며 `docs/runs/`의 append-only 실기 기록이
> 아닙니다. run record와 별도 커밋으로 관리합니다.

## 목표와 완료 정의

현재 1차 목표는 두 Pro와 바형 보조 수류모터로 구성된 3대 `software_independent` 그룹을
`jebao-flow-local` 단일 authority로 상시 운용하는 것이다. 이것은 수류모터 완성의 중간
milestone이며, 이어서 동일 Pro pair의 native ASYNC 핵심 질문 Q1/Q2까지 판정해야 수류모터 전체를
완료로 판정한다.

- current control service 1개만 실행되고 legacy daemon·old MQTT·custom component authority는 0
- 같은 `topic_prefix`를 쓰는 다른 local-control instance가 0인 operator 불변식 유지
- 등록 장비 6대의 fresh 관측이 유지되고, write 표면은 physical identity별 실기 qualification을
  통과한 기능만 노출
- 3대 수류모터 각각의 `power`·`manual_flow`가 exact rollback 권한과 receipt를 갖고 qualified
- software group의 Constant·Sync·Anti Phase, ON/OFF, manual override·resume가 실장비에서
  bounded write와 exact readback으로 확인
- 급여·offline/degraded·emergency-stop·restart/reconnect 경계가 journal을 잃거나 FAIL을 PASS로
  판단하지 않음
- native ASYNC Q1(manual Flow 독립 유지)과 Q2(master Mode·timing 아래 slave slot Flow 적용)가
  보존 raw와 exact restore를 가진 실기로 각각 `UNKNOWN`이 아닌 판정을 얻음
- HA native MQTT Discovery에는 의도한 장비·그룹 엔티티만 남고 retained command는 0
- 배포·복구·운영 절차와 마지막 실기 증거가 문서화되고 full offline suite가 통과

리턴펌프·도징펌프 확장은 수류모터 완료 뒤의 2차 제품 완료선으로 별도 추적한다. native ASYNC는
software `Sync`/`Anti Phase`와 다른 장비 기능이지만 사용자 요구상 핵심이므로 M6에 고정한다.
현재 frozen 하네스는 그 단계의 별도 해제 commit과 명시적 operation 승인 전까지 건드리지 않는다.

## 범위

- Jebao 작업만 진행한다.
- Home Assistant 업데이트·재부팅·Core 조작은 하지 않는다.
- 사용자 앱 조작이 새로 필요하면 그 지점에서만 요청한다.
- frozen native ASYNC 하네스, old daemon·old MQTT 경로는 건드리지 않는다.
- Claude는 독립 정적 검토 역할을 유지한다.

## 완성까지 전체 로드맵

### M0 — 안전한 기반과 단일 직접 제어

- [x] current observer 배포, 장비 6/6 fresh, legacy authority 0
- [x] stale discovery target 원인 판정·임시 복구·DHCP reservation으로 재발 경계 해소
- [x] active Pro template과 stable role binding을 current manifest에 결속
- [x] Pro 한 대 `manual_flow` qualification: `40 -> 39 -> 40`, write 3·receipt 2
- [x] MQTT 경유 direct control: `40 -> 39 -> 40`, terminal journal·exact readback·observer 복귀

### M1 — qualified control 상시 activation

- [x] current private input에서 별도 control Compose 재유도
- [x] activation 직전 fresh preflight와 rollback Compose 확인
- [x] HA에 write 가능한 retained `number` 엔티티 1개가 상시 노출되는 범위를 사용자에게 공개
- [x] 사용자가 해당 HA 표면을 쓰지 않는 조건으로 상시 노출을 승인
- [x] observer stop-first 후 reviewed control service 1개만 기동
- [x] 시작·안정화 구간 device write 0, journal tree 불변, unfinished 0 확인
- [x] 장비 6/6 fresh·qualified control 1·retained command 0·group armed 0 확인
- [x] HA `number`의 선언 허용범위 전체와 이번 실기 검증범위 `39..40`이 다름을 확인
- [x] 위 범위 차이를 activation run record에 명시
- [x] activation run record 작성 → Claude 독립 검토 → commit `0c64f96`

### M2 — 상시 direct-control 운영 안정성

- [x] actual-recovery write의 최소 bounded failure-injection 설계·정책 결속·Claude review
- [x] recovery는 current manifest를 바꾸는 일반 CLI가 아니라 mutation-free qualification recovery CLI를
  별도 production process로 사용하도록 고정
- [x] D1j 정책 commit `4689a6e`
- [x] forward-only drill source·CLI·offline regression 구현
- [x] source snapshot Claude 적대적 검토·필수 finding 반영·landing (`a91e4b6`)
- [x] 통제된 failure 1회에서 recovery frame 실제 전송·baseline exact restore·terminal journal 확인
- [x] recovery run record·독립 검토·commit `0a62fb2`; 근거:
  [`local-control actual recovery`](runs/2026-09-11-local-control-actual-recovery.md)
- [x] Jebao service 재시작 뒤 stale command replay 0·control surface 1·6/6 fresh 확인; 근거:
  [`reconnect and bounded soak`](runs/2026-09-11-local-control-reconnect-soak.md)
- [x] MQTT broker disconnect 뒤 process restart·retained command 소비 0·상태/availability 재발행 확인;
  근거: [`reconnect and bounded soak`](runs/2026-09-11-local-control-reconnect-soak.md)
- [x] 외부 state drift를 fresh read가 반영하고 old state를 성공으로 재사용하지 않는지 확인; 근거:
  [`external app drift`](runs/2026-09-11-local-control-external-app-drift.md)
- [x] daemon 자동 recovery가 없음을 운영 계약으로 고정하고 unfinished journal 감시·CLI recover 절차 마련;
  근거: [`local-control 운영 문서`](local-control.md)
- [x] `restore_on_reconnect`가 현재 미참조 설정임을 example에서 제거하고 inert 호환 필드로 명시;
  근거: [`local-control 운영 문서`](local-control.md)
- [x] 1,800초 bounded soak에서 unexpected write·unfinished journal·추가 service restart 0 확인; 근거:
  [`reconnect and bounded soak`](runs/2026-09-11-local-control-reconnect-soak.md)

### M3 — 3대 수류모터의 group-required qualification

- [x] 현재 group의 멤버별 부족 qualification을 논리 역할·기능 단위로 산출
  - Pro 2대 + 바형 1대 중 Pro 1대의 `manual_flow`만 qualified
  - 추가 필요: 나머지 2대의 `manual_flow`와 3대 모두의 `power` receipt pair
- [x] 두 Pro의 current firmware provenance가 모두 존재하고 byte 동일함을 write-free fresh resolution으로 확인
- [x] 바형은 current resolution·firmware provenance가 있으나 manifest에는 아직 Pro active template 1개만
  결속된 상태임을 확인
- [x] current installed app bundle의 Bar product→template URL→exact cache root 경로를 write-free로 재결속해
  active Bar template digest 하나를 positive evidence로 특정; 다른 candidate 1개는 current URL에서
  unreachable임을 확인
- [ ] Pro 두 대와 바형 한 대의 active template/content binding을 positive evidence로 결속
- [ ] 각 멤버 `manual_flow` qualification 계획과 exact scalar restore 가능성을 확정
- [ ] 각 멤버 `power` qualification producer/operation의 안전한 OFF·ON 순서와 rollback 권한 확정
- [ ] 필요한 source는 기존 journal·lease·identity·single-write를 재사용해 offline 검토·landing
- [ ] 새 `power` qualification write가 기존 deployment-wide global lock과
  `physical_identity_key`별 device lock을 그대로 사용하고 새 lock namespace를 만들지 않으며, 기존
  manual-flow 경로와 같은 장비 key로 상호배제됨을 회귀로 고정
- [ ] 3대 모두 `manual_flow`·`power` receipt pair 확보; 다른 장비·기능은 계속 diagnostic
- [ ] group switch 1개와 member resume 3개만 새 command surface로 투영되는지 확인

### M4 — software-independent 3대 그룹 실장비 commissioning

- [ ] 세 장비를 app에서 `independent`·안전한 Timer 상태와 승인 출력으로 precondition
- [ ] private group config와 example의 pattern `power`·`max_power`를 사용자 승인 상한 `50` 이하로 결속
- [ ] group write 경로가 같은 `/hardware-safety` 전역 lease·비상정지 latch에 참여하고 다른
  physical workflow와 실제로 상호배제되는지 확인
- [ ] Constant 최소 출력 one-shot: plan-atomic journal·멤버별 exact readback·final state 확인
- [ ] Sync 저출력 bounded epoch: gain·limit·동일 위상과 duplicate suppression 확인
- [ ] Anti Phase 저출력 bounded epoch: phase 분리와 전체 exact restore 확인
- [ ] `minimum_command_interval_ms`가 3대 cycle의 실제 device command 빈도를 제한하는지 확인
- [ ] direct member override 후 다음 group cycle이 해당 멤버를 덮지 않는지 확인
- [ ] Resume-to-group 후 해당 멤버만 group ownership으로 복귀하는지 확인
- [ ] group OFF/ON이 canonical OFF·Flow-first/Power-second 순서와 terminal journal을 지키는지 확인

### M5 — 장애·급여·운영 경계

- [ ] 한 멤버 unavailable에서 configured `continue_limited`가 범위를 지키고 DEGRADED로 보이는지 확인
- [ ] STOP_GROUP/failure-stop이 reachable 멤버만 안전 OFF하고 unavailable 멤버를 성공으로 꾸미지 않음
- [ ] feeding 진입·종료가 승인 출력과 이전 desired state를 정확히 복원
- [ ] emergency stop이 자동 해제되지 않고 explicit operator action만 허용
- [ ] command 중단/ACK 불명확 시 forward retry 0·recovery authority 유지
- [ ] service 종료가 새 OFF를 합성하지 않고 accepted mandatory OFF만 drain

### M6 — native ASYNC 핵심 Q1/Q2 판정

시작 시점은 **M2 actual recovery write 검증과 M4 software-independent 그룹 commissioning,
M5 장애·복구 경계 확인이 끝난 직후**다. 그 전에는 과거 정보 0 실기를 반복하지 않는다.
M6는 상시 control service나 HA/MQTT에 native 시험 기능을 노출하는 단계가 아니라, control service를
stop-first로 격리한 **별도 유인 operation**으로만 수행한다.

- [ ] 기존 preserved raw·실패 원인·collector capability를 다시 결속해 Q1/Q2 각각의 최소 판별식을 고정
- [ ] frozen ASYNC 하네스의 필요한 범위만 여는 별도 정책 commit과 repository maintainer 승인을 확보
- [ ] Q2 write가 요구하는 독립 capability `slave_per_slot_flow_extension`을 positive evidence와 receipt로
  qualified 상태로 만들거나, 별도 승인된 operation 전용 경로를 명시적으로 결속
- [ ] 각 live operation 전에 on-site 감시·물리 차단·exact baseline/restore 권한을 독립 확인
- [ ] Q1: 같은 native ASYNC epoch에서 master와 다른 slave manual Flow 유지 여부를 delivery와
  full-state raw readback으로 판정
- [ ] Q2: master Mode·timing을 따르는 동안 slave가 자기 slot별 Flow를 적용하는지 서로 다른 경계의
  preserved raw로 판정
- [ ] 각 operation은 bounded write·900초 이하 지정 epoch·terminal journal·byte-exact restore로 종료
- [ ] Q1/Q2 결과를 `docs/runs/`와 hardware-readiness 현재 판정 표에 반영
- [ ] 두 질문 모두 PASS/FAIL/시험조건 구성 불가 중 하나로 닫음; `UNKNOWN`이 남으면 수류모터 전체
  완료로 선언하지 않음

### M7 — HA 표면·배포·수류모터 마감

- [ ] HA native discovery의 장비/그룹 entity·unique id·availability·command topic 수를 expected set과 대조
- [ ] pre-namespace/legacy retained discovery inventory → owned tombstone → 복구 절차를 완결
- [ ] observer/control 전환과 generation pruning에서 owned stale discovery만 정리되는지 확인
- [ ] D1i가 idle로 인정한 terminal parked exact-restore disposition을 검증된 절차로 finalize하거나
  명시적으로 retire하고, lease 특례의 존속 여부를 결정
- [ ] HA update·reboot 없이 UI state가 fresh device truth와 일치하는지 확인
- [ ] production Compose 재기동·host reboot 이후 단일 authority와 6/6 fresh 복귀 절차 검증
- [ ] 운영 backup·rollback·secret mode·volume identity 복구 절차 점검
- [ ] README·PROJECT_CONTEXT·hardware-readiness 현재 상태를 run record 링크로 갱신
- [ ] full tests·Ruff 통과, Claude 최종 교차검토, MVP 완료 commit/tag

### M8 — 수류모터 뒤 제품 확장

- [ ] DC Pump Pro/리턴: active template·manual scalar·schedule coupling·restore·qualification
- [ ] Aquarium Pump/바형 확장: transport 방향·motor safe range·schedule restore 근거
- [ ] Dosing Pump: physical volume unit·calibration·interval semantics·write/restore qualification
- [ ] 앱 없는 Wi-Fi provisioning과 control-mode reconnect restore
- [ ] 필요할 때만 HA addon/card-native surface를 별도 제품 slice로 결정

## 현재 상태

- [x] reviewed persistent control service 1개 실행 중, reconnect 실기 뒤 restart 1
- [x] 장비 6/6 관측 상태 유지
- [x] HA 업데이트·재부팅 0
- [x] 선택 Pro `manual_flow` qualification 완료 (`40 -> 39 -> 40`, receipt 2장)
- [x] MQTT 실기 `40 -> 39 -> 40` 완료, 각 command exact readback·terminal journal 확인
- [x] 현재 Flow 40·장비 6/6 fresh·qualified control 1·group armed 0
- [x] M2 recovery·restart/reconnect·bounded soak·external drift 전 항목 완료
- [x] Claude 최종 독립 검토 수신·반영; M3 immutable qualification-set merger가 현재 작업

## M0~M1 작업 이력 (완료)

- [x] startup 경로의 자동 device write 0 정적 확인
- [x] command surface가 qualified physical identity + function 쌍으로 제한됨을 확인
- [x] control config: write 허용 장비 1대, qualified control 1개(`manual_flow`), group command 0
- [x] observer rollback Compose를 별도 generation으로 준비
- [x] control Compose를 별도 generation으로 준비
- [x] control/observer가 같은 data volume과 같은 hardware-safety lock volume을 쓰는지 확인
- [x] 실패 복구값을 명령별로 분리
  - 첫 명령 `40 -> 39` 실패: recovery 기대값 `40`
  - 둘째 명령 `39 -> 40` 실패: recovery 기대값 `39`; 자동 forward retry 없음
- [x] reviewed source image를 Compose tag에 정확히 결속
- [x] 시작 직전 fresh preflight: observer 1, old daemon 0, unfinished journal 0, 6/6 fresh
- [x] 기존 observer stop-first 후 control service 단독 기동
- [x] 시작 직후 startup device write 0과 MQTT command topic 1개 확인
- [x] MQTT command 1: `40 -> 39`, terminal journal + exact readback 확인
- [x] MQTT command 2: `39 -> 40`, terminal journal + exact readback 확인
- [x] M0 단회 MQTT 실기 종료 시 observer-only 복귀 및 Flow 40·6/6 fresh·command surface 0 확인
- [x] 이후 M1에서 persistent qualified control로 전환했으며 현재 상태는 위 `현재 상태`가 단일 출처

## M2 actual recovery 작업 이력 (완료)

- [x] 실제 미검증 분기를 `40 -> 39` forward 적용 뒤 readback 직전 지정 fault로 고정
- [x] production `verify_prepared_readback` 재사용과 fault adapter의 recovery process 격리
- [x] 별도 process의 mutation-free production recovery route를 고정
- [x] 정책·회귀 계약 Claude 독립 검토와 D1j 정책 commit
- [x] forward-only drill source·CLI·offline regression 작성
- [x] targeted 21·full 2497·changed-file Ruff 검증과 Claude source 검토(P1 0)
- [x] reviewed source landing (`a91e4b6`)
- [x] private deployment 결속: source/image Python 133-file tree SHA-256 exact
- [x] fresh preflight → control stop-first → forward 39 → actual recovery 40
- [x] terminal `ROLLED_BACK`·recovery physical write 1·unfinished 0·receipt 증가 0 확인
- [x] persistent control 복귀·6/6 fresh·Flow 40·stale command replay 0 확인
- [x] append-only recovery run record·Claude 검토·commit `0a62fb2`

## M2 reconnect·drift·soak 작업 이력 (완료)

- [x] control service 단일 인스턴스·host network·restart policy `unless-stopped`·restart `0` read-only 확인
- [x] control container만 pause하고 broker retained availability `offline`을 관측한 뒤 unpause
- [x] process restart `0 -> 1`, availability `online`, state 재발행·6/6 fresh 확인
- [x] reconnect 전후 journal·receipt tree 불변, unfinished `0`, retained command 소비·device write `0` 확인
- [x] 30분 bounded soak에서 restart 증가·unexpected write·unfinished journal `0`과 fresh cycle 수 확인
  - 1,800초 동안 restart 추가·command·device/global offline event `0`, fresh cycle `+127`
- [x] 외부 app state drift를 별도 agent-write-free observer evidence로 확인하고 exact baseline 복원
  - [x] 조작 전 direct authenticated fresh read: `6/6`, 대상 `Flow 40`·power ON
  - [x] 조작 전 durable baseline: journal operations `5`·blobs `12`·unfinished `0`, receipt `2`,
    canonical tree digest 두 종류 보존
  - [x] on-site app에서 해당 qualified Pro의 `Flow 40 -> 41`만 변경
  - [x] direct fresh read와 HA/MQTT state가 `41`을 반영하고 journal·receipt가 불변임을 확인
  - [x] on-site app에서 `41 -> 40` 복원 후 direct fresh read·HA/MQTT·durable baseline 재확인
- [x] reconnect·soak append-only run record·Claude v2 검토·commit `5ffbfec`
  - run snapshot digest `388dfb9a…`, 삭제 `0`, redaction `0`, P1 `0`
- [x] external drift를 agent device write `0`의 별도 run record로 기록·Claude v2 검토·commit
  `0508de5`
  - final snapshot digest `cff21722…`, 삭제 `0`, redaction `0`, P1 `0`

## M3 선행 정적 조사

- [x] qualification binding은 `(product_key, function)`별 하나지만 receipt는 2장 이상을 허용하고,
  physical identity별 2장을 세므로 같은 firmware의 두 Pro receipt 4장을 한 binding에 합칠 수 있음을 확인
- [x] 기존 producer의 receipt 파일은 content-addressed additive이고, 병목은 no-clobber 단일 binding 파일
  writer뿐임을 확인
- [x] 기존 binding 전수 검증 → 새 receipt 병합 → 새 immutable output no-clobber 생성 → 자기 출력 재검증의
  최소 offline qualification-set merger 정책 결속·Claude 검토·commit `5c3317d`
- [x] in-place 교체 대신 기존+candidate를 검증해 새 owner-only immutable qualification-set 파일을
  no-clobber로 만들고, 다음 Compose generation이 이를 재정규화하는 구조를 선택
- [x] 병합 결과 binding 집합과 `(product, function, physical identity)` surface를 운영자가 선언한
  정확한 union으로 양방향 검증하고, binding과 내부 receipt digest를 결정적으로 정렬하는 계약을
  정책에 고정
- [x] immutable qualification-set merger source·CLI·29개 전용/경계 회귀 구현, 전체 Ruff·pytest
  `2516 passed`, Claude v2 적대적 검토 P1·blocker `0`, commit `5619ee9`
- [ ] Pro 경로는 동작 불변으로 유지하고 Bar precondition만 family별로 추가하는 최소 범위를 결속
- [ ] `power`의 OFF 실패는 safer-OFF 유지, planned ON 실패는 rollback OFF이며 자동 재기동하지 않는
  물리 결과와 현장 복원 절차를 정책에 명시

## 실패 시 행동

- persistent command 실패: forward retry·다른 command 없이 unfinished journal을 확인하고 같은 request의
  reviewed CLI recovery만 수행
- actual-recovery 실기 실패: 계획된 baseline 외 값을 임의로 보내지 않고 journal 권한을 보존한 채 중단
- identity·범위·journal·rollback 관련 실제 P1 또는 물리 위험이면 즉시 중단
- 단순 진단/비안전 과도 조건은 새 gate로 키우지 않는다

## 사용자 조작 필요 여부

- 현재: 없음. 상시 HA `number` 엔티티 1개 노출과 Jebao 실기·필요 상태 변경의 에이전트 실행은
  사용자 승인 완료
- 실기 공통 상한: Flow `<=50`; 각 operation의 더 낮은 고정 envelope이 있으면 그 값을 우선
- 위 `50`은 현재 에이전트 실기의 승인 경계이며 global runtime gate는 아님. 현재 direct HA/MQTT
  number의 기술적 범위 `30..75`는 사용자가 쓰지 않고, 범위 축소는 M7 surface 항목에서 별도 처리
- HA Core 재기동: 사용자가 추후 직접 수행 예정이며 agent는 업데이트·재기동·설정 변경을 실행하지 않음
- 사용자가 HA Core 재기동 직전에 알림; retained command·journal baseline을
  먼저 잡고 재기동 후 unexpected Jebao write `0`을 read-only로 확인

## 진행 로그

- 13:32 KST — 최초 To-do를 저장소 밖 숨김 경로에 생성.
- 13:36 KST — 두 Compose image의 installed Python 131-file tree가 reviewed source와 exact 일치함을 확인.
- 13:50 KST — fresh preflight PASS: device 6/6, absent·ambiguous 0, target Flow 40, identity·template·role·qualification exact, unfinished journal 0, group supervisor armed 0.
- 13:53 KST — observer stop-first 후 reviewed control service 1개 기동. startup 전후 journal tree byte 동일로 startup write 0 확인.
- 13:54 KST — broker read-only 확인 PASS: global online, device 6/6, target state 40, command topic 1, retained command 0, group armed 0.
- 13:56 KST — MQTT command 1 PASS: 40→39, physical write 1, transaction/action VERIFIED, fresh readback 39, unfinished 0.
- 13:58 KST — MQTT command 2 PASS: 39→40, physical write 1, transaction/action VERIFIED, fresh readback 40, unfinished 0.
- 14:01 KST — control 종료·observer 복귀 전후 journal byte 동일. reviewed observer-only 1개, restart 0, write flag·qualification 0.
- 14:02 KST — 최종 observer 확인 PASS: device 6/6, command surface 0, strict fresh read Flow 40, absent·ambiguous 0, unfinished 0.
- 14:08 KST — 사용자 요청에 따라 To-do를 저장소의 `docs/current-todo.md`로 이전. 이후 이 파일만 갱신.
- 14:12 KST — 두 terminal journal과 referenced blob을 fresh process에서 재확인하고 append-only MQTT control run record 초안 작성.
- 14:18 KST — Claude 독립 검토 완료: P1·물리 위험 0. journal-tree 증거등급과 recovery write 미검증 문언을 보강하고 run record와 To-do 커밋을 분리.
- 14:21 KST — 보강본을 Claude가 재계산해 필수 finding 0·랜딩 GO 확인. run record와 index를 commit `2f23343`으로 랜딩.
- 14:22 KST — 현재 MQTT control 실기 slice 마감. 다음 Jebao-only 단계의 contract·precondition 확인으로 이동.
- 14:36 KST — 다음 단계는 한 대·`manual_flow` 한정 상시 control activation으로 결속. 기존 control Compose는 보존되지 않아 current private input과 기존 qualification으로 새 generation을 재유도하며, 현 observer는 그 전까지 유지.
- 14:42 KST — 새 control generation 재유도 PASS: write-enabled target 1, qualification/template/role exact, device 6, group 1, observer 계속 실행, device·broker 접촉 0. 첫 read-only extractor는 writable tmpfs 부재로 입력 읽기 전에 fail-closed했고 tmpfs를 명시해 재실행.
- 14:50 KST — fresh activation preflight PASS: observer 1/restart 0, old daemon 0, unfinished journal 0, legacy idle, device 6/6 fresh, target Flow 40·TimerOFF·constant·independent, qualified control 1, group supervisor 1/armed 0. stop·write는 아직 0.
- 14:50 KST — Claude 교차검토에서 상시 control discovery가 HA Core 변경 없이 write 가능한 retained `number` 엔티티 1개를 계속 노출한다는 범위 차이를 확인. 실행 전 사용자에게 이 한 가지를 투명하게 확인하는 상태.
- 14:55 KST — 사용자가 HA `number` 1개 상시 노출을 승인하고 사용하지 않겠다고 확인. 3대 수류모터 MVP와 후속 제품 확장을 분리한 M0~M7 전체 완료 로드맵을 To-do에 고정하고 activation을 재개.
- 14:58 KST — Claude 로드맵 교차검토 반영: exact-recovery 실기는 qualification 확대보다 먼저, `topic_prefix` 유일성·legacy retained discovery 정리·자동 recovery 부재·미참조 reconnect 설정을 완료선에 추가. activation 추가 P1·물리 blocker 0 확인.
- 14:59 KST — observer stop-first·중첩 0으로 reviewed control service 1개 기동. restart 0, old daemon 0, 동일 image 확인. 시작 전후 journal operations 4·blobs 11·tree digest byte 동일, unfinished 0으로 startup device write 0 확인.
- 14:59 KST — broker read-only postcheck PASS: global online, device 6/6 online, fresh cycle 양수, target Flow 40, qualified command topic 1, retained command 0, group supervisor armed 0.
- 15:01 KST — 약 2분 17초 안정화 뒤 최종 postcheck PASS: control 1/running/restart 0, reviewed image·Compose exact, old daemon 0, journal operations 4·blobs 11·tree digest 불변, unfinished 0.
- 15:02 KST — HA discovery read-only 확인 PASS: retained `number` 1개, projected payload exact, non-optimistic, command topic 존재, 선언 범위 30..75/step 1. 실제 write 검증범위는 39..40뿐으로 분리.
- 15:06 KST — 사용자 지적을 반영해 native ASYNC Q1/Q2를 비필수 park에서 수류모터 전체 완료의 필수 M6로 교정. M2 recovery → M3 qualification → M4 group → M5 장애 경계 직후 수행하고, 두 질문이 `UNKNOWN`이면 전체 완료로 선언하지 않는 순서를 고정.
- 15:07 KST — persistent activation append-only run record 초안 작성. HA retained number 1·선언 범위 30..75와 실측 39..40의 차이, startup write 0, 자동 recovery 부재, reconnect 설정 미참조를 명시하고 Claude review 준비.
- 15:10 KST — Claude 독립 검토: P1 0·commit GO. 물리 target identity anchor를 앞선 세 실기와 같은 digest로 연결하고, HA 미사용은 기술적 차단이 아닌 행위적 통제임을 보강.
- 15:13 KST — 최종 snapshot을 Claude가 재계산해 필수 0·GO 확인. activation run record와 index를 `0c64f96`으로 commit하고 M1을 완료 처리. M2 actual recovery write 설계로 이동.
- 15:18 KST — Claude의 M0~M8 독립 검토를 반영해 전역 safety lease·latch, native slot-flow capability 자격,
  parked exact-restore disposition 종결, command interval 실측을 완료선에 추가. M6는 상시 service가 아닌
  별도 유인 operation임을 고정하고, M2 actual-recovery fault 경로의 source contract를 분석 중.
- 15:29 KST — 사용자가 Flow 50 이하의 Jebao 실기와 필요한 상태 변경을 에이전트가 반복 승인 없이
  진행하도록 명시 승인. Claude에게 승인 경계를 공유하고 M2는 고정 `40 -> 39 -> 40`으로 유지.
- 15:37 KST — actual recovery drill을 forward-only injected fault와 별도 mutation-free production recovery
  process로 결속한 D1j 정책을 Claude 검토 결과에 맞춰 commit `4689a6e`로 랜딩. source 구현 시작.
- 15:57 KST — M2 forward-only recovery drill source·CLI·offline 회귀 완료. targeted 21·full 2497·
  changed-file Ruff PASS와 Claude P1 0·landing GO를 확인하고 commit `a91e4b6`으로 랜딩.
- 16:02 KST — reviewed drill image build와 source 결속 PASS: local/remote Python 133-file path+content
  SHA-256 exact, CLI entry point 설치 확인. persistent control은 계속 실행되고 device write 0.
- 16:05 KST — live 직전 fresh preflight PASS: 6/6 fresh, target ON·TimerOFF·constant·independent·Flow
  40, qualified target 1, receipt 2, unfinished 0, legacy idle, template·role binding exact.
- 16:09 KST — control stop-first 뒤 forward 40→39 physical write 1과 지정 readback fault를 확인하고,
  별도 production recovery process가 rollback 39→40을 physical write 1회로 수행. journal은
  `ROLLED_BACK` revision 6, unfinished 0, receipt 2 불변, independent fresh Flow 40으로 종료.
- 16:10 KST — persistent control 재기동 PASS: service 1/restart 0, 6/6 fresh, Flow 40, journal tree
  불변, command surface 1, retained command 0, group control·resume 0, old daemon 0.
- 16:16 KST — 별도 strict process가 endpoint resolution에 그치지 않고 장비 6대 모두 새 authenticated
  state read를 완료. selected target exact Flow 40·unfinished journal 0 재확인.
- 16:20 KST — actual-recovery run record v2를 Claude가 독립 재계산해 P1·필수 finding 0·GO 확인.
  append-only record와 index를 commit `0a62fb2`로 랜딩하고 M2 핵심 recovery 질문을 완료 처리.
- 16:24 KST — `restore_on_reconnect`를 동작하는 토글로 오해하지 않도록 example에서 제거하고 model
  호환 필드는 유지. daemon 자동 recovery 없음·fresh unfinished inventory·별도 mutation-free recovery
  절차를 `docs/local-control.md`에 고정해 commit `cbebf50`으로 랜딩.
- 16:27 KST — reconnect preflight에서 control service 1·Compose managed·host network·restart policy
  `unless-stopped`·restart 0을 확인. shared broker 대신 control container만 pause하고 retained `offline`
  will을 실제 관측한 뒤 unpause하는 단일-client 격리 절차를 Claude와 확정(P1·물리 blocker 0).
- 16:35~16:37 KST — control container만 pause. broker retained `offline`을 실제 관측한 뒤 unpause했고
  1.8초 안에 새 process `online`을 관측. restart 0→1, device availability 6·state 73·group event 4
  재발행, retained command 0, journal·qualification tree 불변, unfinished 0, receipt 2를 확인.
- 16:39 KST — 별도 strict postcheck PASS: 장비 6/6 authenticated fresh, target ON·TimerOFF·constant·
  independent·Flow 40, command surface 1, group supervisor 1/armed 0, old daemon 0. 새 process 안정화
  뒤 warning/error 0.
- 17:35~17:50 KST — 사용자가 Jebao app에서 qualified Pro의 Flow를 `40 -> 41 -> 40`으로 변경·복원.
  두 경계에서 separate authenticated fresh read와 retained state가 각각 `41`·`40`을 반영했고,
  agent device write·journal/qualification 변화는 `0`임을 확인.
- 18:00 KST — external drift run record를 Claude가 최종 재계산해 P1·필수 finding `0`, landing GO.
  append-only record와 index를 commit `0508de5`로 랜딩해 M2 전 항목을 완료하고 M3 merger로 이동.
- 18:09 KST — M3 immutable qualification-set merger D1k 정책을 Claude가 두 snapshot 모두 재계산해
  finding·P1 `0`, GO 확인. raw physical key 대신 current manifest의 logical member에서 기대 surface를
  유도하고 receipt 이동 0·기존 generation 무변경을 고정해 commit `5c3317d`로 랜딩. source 구현 시작.
- 18:32 KST — D1k merger source v2를 network-none에서 전용/경계 29개와 전체 `2516` tests, Ruff로
  검증. Claude가 staged digest `d384dc09…`를 재계산해 P1·blocker `0`, final GO. current 배포 입력·
  receipt mutation 0과 exact surface/no-clobber를 유지한 commit `5619ee9`로 랜딩. 두 번째 Pro
  `manual_flow` preflight로 이동.
