# run: 2026-09-11-local-control-actual-recovery

- 목적(한 문장): qualified Pro 한 대의 `manual_flow`에 통제된 post-readback fault를 한 번 만들고,
  별도 production recovery process가 저장된 rollback payload를 실제로 한 번 보내 Flow `40`을 exact
  restore하는지 확인했습니다.
- 이 실행이 답하려던 질문: production coordinator·writer·journal·lease를 완화하지 않고
  `40 -> 39` forward 뒤 남은 recovery 권한이 실제 `39 -> 40` rollback frame과 exact readback으로
  terminal `ROLLED_BACK`을 만들 수 있는가.
- 사용자 승인 범위: Jebao 실기와 필요한 상태 변경은 반복 질문 없이 진행하고 Flow는 항상 `50`
  이하로 제한한다는 승인을 적용했습니다. 이 operation은 더 좁은 고정 `39..40`만 사용했고 Home
  Assistant Core 업데이트·재부팅·설정 변경과 frozen native ASYNC 하네스는 건드리지 않았습니다.
- 실행한 source commit SHA: `a91e4b6f34de9141393c2df1888b4e1ad32c3148`. 실행 당시 저장소
  HEAD `e5d2455bdb84d372a0a1c0ab39ebadd97be0b517`까지의 후속 변경은 mutable To-do뿐이고
  `src/`·`tests/`는 source commit과 동일했습니다.
- source·deployment 결속:
  - local source와 one-shot image의 Python file count는 각각 `133`, relative path+content canonical
    SHA-256은 각각 `f7b1a75268efd0dde9a66b9b1772dc96874a1652be16817419e6aa948e6a4a0a`로
    exact 일치했습니다.
  - drill image id는
    `sha256:a91147cbc23697d021fabe15b195aa4ec17d5be8902998c018bccae19c691819`이고,
    신규 console entry point의 설치도 확인했습니다.
  - 실기 뒤 복귀한 persistent control image binding은 activation 기록과 같은
    `d80bc094ad1875bf52f61d785d0495c62b4a6c5577882de180e43cdebe381748`입니다.
- 실행 전 조건: persistent control service `1`·restart `0`, old daemon `0`, 장비 `6/6` strict
  resolution·fresh, absent·ambiguous `0`, unfinished local-control journal `0`, legacy workflow idle을
  확인했습니다. selected target은 power ON·TimerOFF·`constant`·`independent`·Flow `40`이고,
  write-enabled target·qualified physical identity·qualified `manual_flow`는 각각 `1`이었습니다.
  active template count `1`, stable role binding, current qualification을 exact 확인했습니다.
- 실행 전 artifact inventory: local-control journal은 operations `4`, blobs `11`, path+content tree
  SHA-256 `b6aed217841829dbcc1190b8b1fec4193b599ad3af4522493e1d6758d0be5a22`였습니다.
  qualification receipt artifact는 `2`장이고 qualification directory 전체 path+content tree SHA-256은
  `c626ae6e22ec436a4290f05ced6f1fe7e7cce185a2f93880c37565bd7fccb76a`였습니다.
- 사용한 명령·주요 파라미터: persistent control을 stop-first하고 running instance `0`을 확인한 뒤,
  reviewed `jebao-flow-local-recovery-drill`을 explicit write opt-in·private target·current Compose
  generation·opaque operation id로 실행했습니다. forward process가 완전히 종료된 뒤 같은 id를
  `jebao-flow-local-qualify recover --write-enabled`에 넘겼습니다. 두 프로세스는 같은 current config,
  data volume과 hardware-safety volume을 사용했고 서로 겹치지 않았습니다.
- 실행 전 operator wrapper 정정: 첫 orchestration 시도는 Docker option bundle을 한 scalar argument로
  넘겨 container runtime usage error로 종료됐습니다. 이 시도는 one-shot container 생성, persistent
  service stop, journal 생성, broker·device 접촉과 device write **전**이었습니다. option 경계를 보존하는
  shell function으로 교정한 다음 같은 fresh preflight에서 실행했습니다.
- 종료 지점(stage / failure code): forward는 지정 fault를 확인한 계획된 non-terminal 상태로 exit
  `0`, recovery는 terminal `rolled_back`으로 exit `0`이었습니다. 총 physical write는 `2`이고
  forward retry·recovery retry·추가 command는 `0`입니다.
- 장비와 daemon이 만든 결과:
  - 증거 등급: forward·terminal journal과 referenced blob을 서로 다른 fresh process에서 재독한 범위와
    journal/receipt tree inventory는 **(b) preserved structured/durable daemon artifact**입니다. 임시
    CLI result JSON은 실행 종료 뒤 별도 artifact로 보존하지 않았으므로, 그 출력과 independent fresh
    device state, service·broker postcheck는 **(c) reconstructed operator observation**입니다. 원본
    request/response wire frame은 보존하지 않았으므로 (a)를 주장하지 않습니다.
  - opaque artifact id: `LCDR-8dcc4b46b4fbb853c5f4e59d14cca3fb`.
  - UTC span: control stop 직전 `2026-09-11T07:09:07.608479Z`, terminal recovery 확인
    `2026-09-11T07:09:32.383409Z`. persistent control 재기동·fresh/broker 후속 확인은
    `2026-09-11T07:16:10.019815Z` 이전에 끝났습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 앞선 first-write,
    qualification success, MQTT control, persistent activation 기록과 같은 물리 개체입니다. forward
    CLI가 별도로 출력한 drill-scoped target digest는
    `8b48fb485b98a93ffb97dff443f907a7b82e9fc92968be163173ec8369c17a72`였습니다.
  - forward 결과: physical write `1`, ACK body SHA-256
    `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, production exact
    readback predicate를 통과한 Flow-39 readback SHA-256
    `9ef2678a28be58b2603bc4202972c9a0729de529fb4ca069f412cf326b34cf37`입니다. 이후
    transaction/action은 `recovery_required` / `uncertain`, diagnostic
    `forward_readback_unavailable`, revision `3`이었습니다.
  - forward non-terminal journal SHA-256:
    `ffb932ca7dfc32cc295f1d7f0770737a2aa4b723394437993db3f4ce15c13892`.
    revision `3`은 create `0` → `FORWARD_ATTEMPTING` `1` → `FORWARD_ACKNOWLEDGED` `2` →
    `UNCERTAIN` `3`의 current source 전이와 일치합니다.
  - recovery 결과: stored Flow-40 rollback payload physical write `1`, action·transaction
    `rolled_back`, revision `6`, unfinished journal `0`입니다. `physical_write_count=1`을 별도 recovery
    CLI result에서 확인했습니다. terminal artifact도 action diagnostic `null`과 revision `6`을
    보존했습니다. 이는 `ROLLBACK_ATTEMPTING` `4` → action `ROLLED_BACK` `5` → transaction
    `ROLLED_BACK` `6`의 frame-send 분기와 일치하며, diagnostic `baseline_already_present`와 revision
    `5`로 끝나는 zero-write 분기가 아닙니다.
  - terminal journal SHA-256:
    `cef0214527b4434db96274cef885474bb5fa60113db808251b9a9e3b7b014a96`.
  - 종료 journal은 operations `5`, blobs `12`, files `17`, path+content tree SHA-256
    `33482907c163f523b4cb0dacc127cedf4e9d34211cab86884e588c3b87a21384`였습니다.
    operation은 `+1`, content-addressed blob은 `+1`입니다. forward·rollback payload, expected·baseline
    readback과 empty ACK digest는 앞선 같은-device `40↔39` journal에 이미 있었고, 이번에 새로 생긴
    blob은 current full baseline-status snapshot 하나뿐인 것과 일치합니다.
    persistent control 재기동 후에도 이 tree가 byte-exact 불변이어서 stale/startup command write는
    `0`으로 판정했습니다. 이 추론은 모든 local-control frame 앞에 journal-first 저장이 있다는 현재
    경로에만 적용합니다.
  - receipt artifact `2`장과 qualification directory tree SHA-256
    `c626ae6e22ec436a4290f05ced6f1fe7e7cce185a2f93880c37565bd7fccb76a`는 실행 전후
    exact 불변입니다. source의 `created_receipt_count=0` 상수가 아니라 이 외부 inventory를 receipt
    증가 `0`의 근거로 사용합니다.
  - persistent 재기동 뒤 별도 strict fresh process가 장비 `6/6` 각각에 새 authenticated state read를
    완료했고, selected target은 power ON·TimerOFF·`constant`·`independent`·Flow `40`이었습니다.
  - persistent postcheck는 control service `1`·running·restart `0`, command surface `1`, retained command
    `0`, group control·resume `0`, old daemon `0`이었습니다.
- 원복 검증 결과: actual recovery writer가 fresh Flow `39`를 확인한 뒤 stored rollback payload를
  정확히 한 번 전송했고 ACK와 production exact baseline readback을 함께 만족해 Flow `40`과 terminal
  `ROLLED_BACK`을 만들었습니다. recovery 뒤와 persistent control 재기동 뒤의 independent fresh read가
  모두 같은 exact baseline을 확인했습니다.
- **이 실행으로 새로 확정된 사실:** current production local-control recovery 경로는 한 번 적용되고
  exact readback까지 확인된 Flow-39 forward가 `UNCERTAIN`으로 남았을 때, 저장된 rollback payload를
  physical write `1`회로 보내 selected Pro `manual_flow`를 Flow `40`으로 exact restore하고 journal을
  terminal `ROLLED_BACK`으로 닫을 수 있습니다. 기존 zero-write recovery 분기와 actual write recovery
  분기 모두 실장비 evidence를 갖게 됐습니다.
- 이 실행으로 확정하지 못한 것: transport outcome 자체가 불명확한 fault, rollback ACK/readback 실패,
  다른 장비·product·function, 여러 action의 reverse recovery, service/broker reconnect와 장시간 운용,
  software group actuation, Q1·Q2·native ASYNC capability는 증명하지 않습니다. raw wire frame을
  보존하지 않았으므로 empty ACK body의 protocol-level 내용을 새로 증명하지도 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 actual-recovery drill을 반복하지 않고 M2의
  남은 service restart, broker reconnect, external drift, unfinished-journal 운영 절차와 bounded soak를
  진행합니다. persistent control에서 unfinished journal이 생기면 forward를 자동 retry하지 않고 같은
  reviewed mutation-free recovery CLI를 사용하며, Flow `<=50`, 단일 authority와 receipt hygiene를
  유지합니다.
