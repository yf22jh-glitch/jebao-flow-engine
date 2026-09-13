# Jebao 현재 작업 To-do

마지막 갱신: 2026-09-13 09:05 KST

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

## 현재 대기 지점

- [x] 두 Pro를 D1l 새 schema에서 각각 `40 -> 39 -> 40`으로 재자격하고 receipt `4`장 확보;
  근거: [`first Pro requalification`](runs/2026-09-12-local-manual-flow-requalification-first-pro.md),
  [`second Pro requalification`](runs/2026-09-12-local-manual-flow-requalification-second-pro.md)
- [x] 두 candidate만 fresh immutable set으로 exact merge하고 binding `1`·receipt `4`·qualified pair
  `2`를 재검증
- [x] 한 Compose generation으로 정확히 두 Pro의 persistent `manual_flow` surface를 배포;
  HA range `30..100`, discovery `86/86` exact, startup write `0`, fresh `6/6`, group surface `0`;
  근거: [`two-Pro qualified-control deployment`](runs/2026-09-12-two-pro-qualified-control-deployment.md),
  run commit `c6fe660`, HA surface 승인 경위 append-only 정정 `fe8a99a`
- [x] Bar active template을 current manifest에 write-free로 결속하고 새 generation에 배포;
  기존 discovery·두 Pro control byte 불변, Bar diagnostic `13`·command `0`, startup write `0`;
  근거: [`Bar active-template deployment`](runs/2026-09-12-local-bar-active-template-deployment.md),
  run commit `df6b055`
- [x] Pro 동작을 그대로 보존하는 Bar family precondition source·offline 회귀를 검토·landing;
  commit `d687739`, 전체 `2531` tests·Ruff PASS, source landing 시점의 Bar live write `0`
- [x] 실패 시 OFF로만 수렴하는 `power` qualification source·CLI·offline fault 회귀를 검토·landing;
  source `c242635`, 후속 결속 `c02b8f5`·`045f086`, 전체 `2578` tests·Ruff PASS
- [x] exact x86_64 one-shot image와 observer-mode no-clobber input을 준비하고, current Bar·journal·
  qualification·legacy authority를 read-only preflight
- [x] Bar `manual_flow`를 `45 -> 44 -> 45`로 qualification하고 사용자 앱으로
  `classic`·Flow `91`을 복원; physical write `3`, terminal revision `10`, receipt `2`, recovery `0`;
  근거: [`Bar manual-flow qualification`](runs/2026-09-13-local-bar-manual-flow-qualification.md),
  run commit `1fdb947`
- [x] Bar candidate를 current set에 exact merge하고 단일 generation에 배포; binding `2`·receipt `6`·
  qualified pair `3`, direct control `3`, discovery `86/86` exact, startup write `0`, fresh `6/6`;
  근거: [`Bar qualified-control deployment`](runs/2026-09-13-local-bar-qualified-control-deployment.md),
  run commit `2c2afe1`
- [x] 첫 번째 Pro `power`: 사용자 현장 감시·즉시 차단 수단 아래 ON→OFF→ON physical write `3`,
  terminal revision `10`, receipt `2`, recovery `0`, 최종 ON·Flow `40`; first-send 전 safer-OFF mapping과
  OFF 상태 비전원 필드 유지를 보존; 근거:
  [`first Pro power qualification`](runs/2026-09-13-local-power-qualification-first-pro.md), run
  commit `21370b6`
- [x] 사용자 수행 HA Core process restart 뒤 discovery `86/86` byte exact·manual-flow control `3`·
  availability `7/7 online`·removed Bar diagnostic 미재등장과 journal·receipt tree 불변을 확인해
  restart 창의 local-control device write `0`을 판정; 근거:
  [`Home Assistant Core restart`](runs/2026-09-13-home-assistant-core-restart.md), run commit `022690d`
- [ ] 두 번째 Pro `power` 앱 precondition 직전 경계: write-free fresh read는
  `ON`·TimerON·`constant`·`independent`·Flow `40`과 exact target `1`을 확인했습니다. 사용자가 앱에서
  TimerOFF로 바꾸고, 실기 뒤 TimerON schedule ownership을 복원하기 전에는 physical write를 시작하지
  않습니다. 첫 Pro에서 보존 가능한 OFF 체류 상한은 qualification 전체 `18.9`초였습니다.

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
- [x] D1l Flow capability 정정
  - [x] 사용자 미승인 `75`가 catalog·writer·qualification producer·group·local schedule·schedule
    model에 복제됐음을 전수 확인하고, 제품 범위 `30..100`과 agent operation 상한 `50`을 분리
  - [x] 제품 범위는 app catalog에서 유도하고 consumer별 숫자 복제 제거; 더 좁은 config limit와의
    교집합은 group·qualification producer에 유지하고 direct HA/MQTT는 app range 그대로 사용
  - [x] hash 밖 `schedule_model.py`가 hashed `local_control/schedule.py` 동작을 바꾸던 dependency hole을
    protocol-schema digest closure에 포함
  - [x] source·회귀 landing `fe10bd5`: 전체 `2521` tests·Ruff PASS, frozen six·직접 tests 불변,
    Claude staged source review finding `0`
  - [x] 기존 qualification이 stale해지는 설계를 우회하지 않고 두 Pro receipt를 새로 획득
    - [x] 첫 번째 Pro 새-schema receipt pair: `40 -> 39 -> 40`, run `8647f86`
    - [x] 두 번째 Pro 새-schema receipt pair: `40 -> 39 -> 40`, run `24b808a`
- [x] 두 Pro 새-schema candidate를 fresh immutable set으로 merge하고 단일 generation에 배포
  - exact qualified pair `2`, write-allowed member `2`, direct `manual_flow` surface `2`
  - HA retained discovery `86/86` exact, 두 number `30..100`, fresh `6/6`, startup device write `0`
  - run [`two-Pro qualified-control deployment`](runs/2026-09-12-two-pro-qualified-control-deployment.md),
    commit `c6fe660`, 승인 경위 정정 `fe8a99a`
- [x] Pro 두 대와 바형 한 대의 active template/content binding을 positive evidence로 결속
  - template membership `1 -> 2`, 기존 Pro receipt `4` current·ignored `0`, Bar diagnostic
    entity `13`·command `0`, discovery `86/86` byte exact, fresh `6/6`
  - run [`Bar active-template deployment`](runs/2026-09-12-local-bar-active-template-deployment.md),
    commit `df6b055`
- [x] 각 멤버 `manual_flow` qualification 계획과 exact scalar restore 가능성을 확정
  - [x] 두 번째 Pro를 current manifest·fresh resolution에서 정확히 하나로 선택하고 6/6 authenticated
    fresh, firmware 동일, unfinished `0`, legacy idle을 확인
  - [x] 두 번째 Pro 앱 precondition: power ON·`constant`·`independent` 유지, TimerOFF·Flow `40`
    - 사용자 앱 변경 뒤 agent fresh read로 exact state와 장비 `6/6`, unfinished `0`, legacy idle,
      retained command `0`을 재확인
  - [x] 기존 producer 무수정으로 `40 -> 39 -> 40`, receipt pair candidate 생성·exact restore 확인
    - physical write `3`, terminal `VERIFIED` revision `10`, action `3/3` verified, receipt `2`
    - candidate는 아직 running qualification input에 합치지 않아 두 번째 Pro command surface `0`
    - persistent control 복귀 후 availability online·장비 `6/6`·retained command `0`·unfinished `0`
    - run record·Claude 독립검토·commit `ad5681f` 완료
  - [x] 두 번째 Pro의 TimerON schedule ownership 복원과 종료 상태를 authenticated fresh read로 확인
    - 종료 상태는 power ON·TimerON·`independent`; latent manual `Flow 40`이며 원래 latent `Flow 30`과
      byte-exact 동일 상태는 아님
    - 현재 시간대 `18:11–23:59`의 적용 slot은 `sine`·Flow `45`·frequency `40`으로 사용자 앱
      화면과 direct fresh read가 일치; 다른 시간대 slot은 현재 상태 판정에 사용하지 않음
    - schedule decode는 유효 slot `14`, invalid `0`; 이번 agent qualification write는 schedule을
      수정하지 않고 `39`·`40`만 사용
    - 실행 전 schedule image를 보존하지 않아 전체 schedule의 pre/post byte-exact 불변은 주장하지 않음
  - [x] Pro의 4개 send-free state probe와 3-write 계약을 바이트 동일하게 보존하고, Bar의 product
    descriptor·writer scalar mapping과 strict production status decoder로 TimerON을 판정하는 family
    profile source·offline 회귀 landing `d687739`; source landing 시점에는 Bar live write `0`
  - [x] Bar를 `constant`·Flow `45`로 precondition한 뒤 `45 -> 44 -> 45`, physical write `3`, terminal
    revision `10`, receipt `2`, recovery `0`으로 qualification하고 사용자 앱으로 `classic`·Flow `91` 복원;
    run `1fdb947`
  - [x] Bar candidate를 current two-Pro set에 fresh merge하고 같은 identity의 write allow만 추가한 단일
    generation을 배포; binding `2`·receipt `6`·qualified pair `3`, ignored `0`, direct control `3`,
    discovery `86/86` exact, startup device write `0`; run `2c2afe1`
- [x] 각 멤버 `power` qualification producer/operation의 안전한 OFF·ON 순서와 rollback 권한 확정
  - durable action mapping은 ON/OFF/ON forward와 OFF/ON/OFF recovery target을 첫 send 전 절대 검증
  - 어느 nonterminal failure에서도 recovery ON frame `0`, OFF frame 최대 `1`, terminal 전 실패는
    planned ON readback 뒤라도 safer-OFF로 수렴
- [x] 필요한 source는 기존 journal·lease·identity·single-write를 재사용해 offline 검토·landing
  - source `c242635`, post-create recovery 결속 `c02b8f5`, boolean 방향 결속 `045f086`
  - 전체 `2578` tests·Ruff PASS, 기존 manual-flow producer·recovery CLI·schema-bound writer 불변
- [x] 새 `power` qualification write가 기존 deployment-wide global lock과
  `physical_identity_key`별 device lock을 그대로 사용하고 새 lock namespace를 만들지 않으며, 기존
  manual-flow 경로와 같은 장비 key로 상호배제됨을 회귀로 고정
- [ ] 3대 모두 `manual_flow`·`power` receipt pair 확보; 다른 장비·기능은 계속 diagnostic
  - [x] 첫 번째 Pro `power`: ON→OFF→ON write `3`, revision `10`, receipt `2`, final ON, candidate only;
    run `21370b6`
  - [ ] 두 번째 Pro `power`: current TimerON을 사용자 앱에서 TimerOFF로 precondition하고 실기 뒤
    TimerON 복원
  - [ ] Bar `power`: current `classic`·Flow `91`을 사용자 판단으로 `constant`·Flow `45` 이하로
    precondition하고 실기 뒤 원 운전 상태 복원
- [ ] group switch 1개와 member resume 3개만 새 command surface로 투영되는지 확인

### M4 — software-independent 3대 그룹 실장비 commissioning

- [ ] 세 장비를 app에서 `independent`·안전한 Timer 상태와 승인 출력으로 precondition
- [ ] capability·영구 config 상한을 임의 축소하지 않고, attended commissioning의 목적값·step 크기·
  동시 변경 대수를 수조 충격 방지 관점에서 검산; agent가 보내는 목적값은 `50` 이하로 계획
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
- [ ] D1l에서 정정한 공식 앱 Flow 범위 `30..100`이 최종 HA/MQTT surface와 exact 일치하는지 확인
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

- [x] reviewed persistent control service 1개 실행 중, 최종 generation restart 0
- [x] 장비 6/6 관측 상태 유지
- [x] 사용자 수행 HA Core process restart 뒤 exact deployment surface 복귀·local-control device write `0`
  확인; agent의 HA 업데이트·재기동·설정 변경은 `0`
- [x] 세 수류모터 `manual_flow` qualification 완료: 두 Pro는 각 `40 -> 39 -> 40`, Bar는
  `45 -> 44 -> 45`; current authority binding `2`·receipt `6`·qualified pair `3`
- [x] MQTT 실기 `40 -> 39 -> 40` 완료, 각 command exact readback·terminal journal 확인
- [x] 장비 6/6 fresh·qualified control 3·HA Flow range `30..100`·group armed 0
- [x] M2 recovery·restart/reconnect·bounded soak·external drift 전 항목 완료
- [x] D1l source와 세 장비 재자격·merge·deployment 완료; direct `manual_flow` control `3`, HA Flow
  range `30..100`, startup write `0`
- [x] Bar template membership `2`와 current qualification binding `2`·receipt `6`·qualified pair `3` 배포;
  discovery `86/86` byte exact, Bar number `1`, fresh `6/6`, startup write `0`
- [x] Pro 동작을 보존하는 Bar family precondition source·offline 검토·landing `d687739` 완료
- [x] safer-OFF power qualification source·CLI·fault 회귀 landing과 exact one-shot image 준비 완료
- [x] current post-deploy audit: service `1`, 장비 `6/6`, unfinished `0`, legacy idle, receipt `6`,
  direct control `3`, group command `0`, retained command `0`, journal·receipt tree byte 불변
- [x] read-only `power` preflight: 첫 Pro는 `ON`·TimerOFF·`constant`·`independent`·Flow `40`으로 admission
  준비 완료; 두 번째 Pro는 TimerON, Bar는 `classic`·Flow `91`이라 각각 이후 사용자 앱 precondition 필요
- [ ] M3의 다음 작업은 두 번째 Pro `power` qualification이며, 사용자가 앱에서 TimerON→OFF로
  ownership을 넘기고 fresh admission을 확인한 뒤에만 physical write를 시작; 실기 뒤 TimerON 복원 필요

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
- [x] Bar positive template bytes를 append-only static record로 재결속하고 별도 durable root·두-template
  manifest로 배포; 기존 Pro control·전체 discovery byte 불변, journal·receipt tree 불변, commit
  `df6b055`
- [x] Pro 경로는 동작 불변으로 유지하고 Bar precondition만 family별로 추가하는 최소 범위를 결속;
  source commit `d687739`, Bar physical write `0`
- [x] `power`의 OFF 실패는 safer-OFF 유지, planned ON 실패는 rollback OFF이며 자동 재기동하지 않는
  물리 결과와 현장 복원 절차를 정책·source·reload journal 회귀에 결속

## 실패 시 행동

- persistent command 실패: forward retry·다른 command 없이 unfinished journal을 확인하고 같은 request의
  reviewed CLI recovery만 수행
- actual-recovery 실기 실패: 계획된 baseline 외 값을 임의로 보내지 않고 journal 권한을 보존한 채 중단
- identity·범위·journal·rollback 관련 실제 P1 또는 물리 위험이면 즉시 중단
- 단순 진단/비안전 과도 조건은 새 gate로 키우지 않는다

## 사용자 조작 필요 여부

- 현재: **두 번째 Pro는 TimerON→OFF 사용자 앱 precondition이 필요합니다.** 직전 read-only preflight는
  power ON·TimerON·`constant`·`independent`·Flow `40`을 확인했습니다. `power` qualification은
  ON→OFF→ON physical write `3`이고 어떤 nonterminal failure에서도 펌프가 OFF로 남을 수 있으므로,
  현장 감시와 즉시 전원 차단/앱 복원 수단을 유지한 채 실행하고 실기 뒤 TimerON을 복원합니다.
- 이후 Bar는 `classic`·Flow `91`→`constant`·Flow `45` 이하의 사용자 앱
  precondition이 별도로 필요합니다. Bar의 `91 -> 45`는 46p 하강이므로 한 번에 바꾸라고 가정하지 않고
  현장 수조 상태에 맞춘 단계적 하향 여부와 사후 `classic`·Flow `91` 복원 결정을 다시 확인합니다.
- 상시 HA `number` 엔티티 추가 노출 자체와 Jebao 실기·필요 상태 변경의 에이전트 실행은 사용자 승인 완료.
  승인 없던 `75`는 D1l에서 app-declared `30..100`으로 정정·배포 완료
- 현재 attended 실기 가이드: agent 목적값은 Flow `<=50`로 계획하고 갑작스러운 step·여러 펌프 동시
  변화를 피함. 이는 capability·영구 상한·사람의 HA 입력 제한이 아니며, direct HA/MQTT는 제품 범위
  `30..100`을 공개·수용
- HA Core process restart는 사용자가 직접 수행했고 exact discovery·registry·availability 복귀와
  journal·receipt tree 불변을 read-only로 확인했습니다. agent의 HA 업데이트·재기동·설정 변경은 `0`입니다.

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
- 18:38 KST — 두 번째 Pro read-only preflight PASS: manifest/fresh `6/6`, Pro `2`, unqualified Pro
  `1`, Pro firmware distinct `1`, qualification surface `1`, unfinished `0`, legacy idle. 대상이 TimerON·
  Flow `30`이라 1-step qualifier는 29 하한 밖이고 Timer 조건도 불충족하므로 write 전에 정지.
  사용자 앱 precondition TimerOFF·Flow `40`을 요청하고 Claude live-plan 검토를 병행.
- 23:04 KST — 두 번째 Pro candidate와 current qualification을 offline no-clobber source로 exact merge.
  binding `1`, receipt `4`, qualified physical pair `2`, output SHA-256 `daf69b9a…`; current config·manifest·
  qualification·candidate·running service와 device/broker 접촉은 불변.
- 23:18 KST — 사용자 이의로 새 generation·deploy를 직전에서 일시정지. 공식 app catalog는
  `manual_flow 30..100`이지만 local catalog의 `_SAFE_FLOW_MAXIMUM=75`는 agent-authored policy commit
  `c40e6b3`과 source commit `a58da60e`에서 추가됐고, 사용자의 직접 `75` 지정은 저장소 이력에서
  확인되지 않음. Claude에 승인 해석과 최소 정정 경로의 독립 검토를 요청.
- 23:27 KST — `75` 전수 감사에서 catalog/HA뿐 아니라 scalar writer·qualification encoder·group·
  local schedule·read-only schedule model의 같은 제한을 확인. 다른 app capability override는 없고,
  `45` 계열은 attended/first-write 실기 전용 상한으로 제품 범위와 분리됨. 사용자의 `30..100` 명시
  정정에 따라 전체 계열을 한 번에 고치고, schema-bound receipt를 우회하지 않고 두 Pro를 새로
  자격 검증한 뒤 한 번만 배포하는 D1l 순서를 Claude와 교차검토.
- 23:49 KST — D1l 정책을 Claude final GO 뒤 commit `0add7c7`로 랜딩. source의 Flow consumer를
  app-declared range에 결속하고 schedule-model dependency를 schema digest에 포함했으며, 잘못된
  `75% safety ceiling` 주석도 configured per-device limit 표현으로 정정. Ruff와 영향 회귀 `141`,
  전체 `2521` tests PASS; frozen six·직접 tests delta `0`. staged source snapshot을 Claude에게
  적대적 검토 요청하고, old generation·running surface `1`·장비 상태는 변경하지 않음.
- 00:00 KST — Claude source review finding·P1 `0` 뒤 D1l source를 commit `fe10bd5`로 랜딩. old
  generation을 유지한 채 새 one-shot image와 manifest/schema 결속을 준비하고, 첫 번째 Pro의
  unexplained 시작 Flow `41`을 series last-recorded `40`으로 current qualified write 1회 복귀시킴.
- 00:00 KST — persistent control stop-first 뒤 첫 번째 Pro를 새 schema에서 `40 -> 39 -> 40`으로
  재자격. qualifier physical write `3`, exact readback `3`, receipt `2`, final Flow `40`, unfinished
  journal `0`; old service·surface `1` 정상 복귀. append-only 기록을 Claude final GO 뒤 commit
  `8647f86`으로 랜딩하고 두 번째 Pro 앱 precondition 대기로 이동.
- 22:43 KST — 두 번째 Pro도 새 schema에서 `40 -> 39 -> 40` physical write `3`, exact readback `3`,
  receipt `2`로 재자격. TimerON과 현재 `sine`·Flow `45` schedule ownership을 복원하고 run
  `24b808a`로 랜딩.
- 23:09 KST — 두 새 candidate를 immutable set으로 fresh merge하고 exact 두 member만 write-enabled로
  만든 단일 D1l generation 배포 완료. persistent control `1`, direct control `2`, HA range
  `30..100`, discovery `86/86` exact, authenticated fresh `6/6`, startup write·group surface `0`,
  journal·receipt tree byte 동일을 확인. run record commit `c6fe660`, 승인 경위 정정 `fe8a99a`.
- 23:57 KST — current app loader에서 재결속한 Bar template을 별도 durable root와 새 immutable
  generation에 배포. template membership `1 -> 2`, 기존 Pro receipt `4` current·control `2`, Bar
  diagnostic entity `13`·command `0`, discovery `86/86` pre/post byte 동일, fresh `6/6`, startup write·
  journal·receipt 변화 `0`을 확인하고 append-only run `df6b055`로 랜딩.
- 00:26 KST — Pro의 기존 state probe·`40 -> 39 -> 40` 계약을 보존하면서 Bar의 product-scoped
  scalar mapping과 strict production status decoder로 TimerON을 판정하는 D1n source를 commit
  `d687739`로 랜딩. 영향 회귀 `90`, 전체 `2531` tests·Ruff PASS이며 Bar app 조작·physical write는 `0`.
- 01:13 KST — nonterminal power qualification 실패가 unattended ON을 만들지 않고 OFF로만 수렴하는
  D1o source·CLI·fault 회귀를 commit `c242635`로 랜딩. journal-create 뒤 validation fault도
  `RECOVERY_REQUIRED`로 남기는 `c02b8f5`, product field 문자열을 하드코딩하지 않으면서 canonical
  one-field bool을 열어 ON/OFF 방향을 절대 결속하는 `045f086`까지 반영. 전체 `2578` tests·Ruff PASS.
- 01:36 KST — remote x86_64 one-shot image의 installed Python tree `137` files를 reviewed source와
  exact 결속하고, current deployment를 바꾸지 않은 read-only preflight에서 service `1`·restart `0`,
  fresh `6/6`, unfinished `0`, legacy idle, qualification receipt `4`, direct control `2`, Bar command·
  group command·retained command `0`을 확인. 새 owner-only observer-mode input과 no-clobber candidate
  경로·opaque operation id를 준비했으며 candidate와 journal operation은 아직 없음. current Bar는
  `ON`·TimerOFF·`classic`·`independent`·Flow `91`; 첫 Bar physical write 전 사용자 앱 조작 경계에서 정지.
- 05:52 KST — 사용자 앱 precondition 뒤 Bar를 `45 -> 44 -> 45`로 qualification. physical write `3`,
  terminal revision `10`, receipt `2`, recovery `0`, Bar command surface `0`을 확인하고 사용자가 앱에서
  `classic`·Flow `91`로 복원. append-only run record를 commit `1fdb947`로 랜딩.
- 06:40 KST — Bar candidate를 current two-Pro qualification에 exact merge하고 Bar 한 대만
  write-enabled로 바꾼 단일 generation 배포 완료. binding `2`·receipt `6`·qualified pair `3`, direct
  control `3`, discovery `86/86` byte exact, fresh `6/6`, startup write `0`, journal·receipt tree 불변을
  확인하고 append-only run record를 commit `2c2afe1`로 랜딩.
- 06:41 KST — read-only `power` preflight에서 첫 Pro는 `ON`·TimerOFF·`constant`·`independent`·Flow `40`으로
  앱 조작 없이 admission 가능한 상태임을 확인. 두 번째 Pro는 TimerON, Bar는 `classic`·Flow `91`이라
  이후 별도 앱 precondition이 필요함을 분리하고, 첫 Pro physical write 직전 현장 승인 경계에서 정지.
