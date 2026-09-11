# run: 2026-09-11-local-control-reconnect-soak

- 목적(한 문장): persistent qualified control의 broker 연결을 단일 service pause로 실제 끊어 process
  restart를 유도하고, stale command write 없이 상태를 재발행한 뒤 30분 bounded soak 동안 journal과
  장비 상태가 불변인지 확인했습니다.
- 이 실행이 답하려던 질문: current daemon이 broker loss 뒤 in-process reconnect가 아니라 process
  restart를 거쳐 복귀할 때 retained command를 소비하거나 startup write를 만들지 않고, 이어지는
  bounded 운용에서도 단일 authority·fresh observation·unfinished journal `0`을 유지하는가.
- 사용자 승인 범위: Jebao 실기와 필요한 상태 변경은 반복 질문 없이 진행하고 Flow는 항상 `50`
  이하로 제한한다는 승인을 적용했습니다. 이 operation은 device command를 발행하지 않았고 Home
  Assistant Core 업데이트·재부팅·설정 변경과 frozen native ASYNC 하네스는 건드리지 않았습니다.
- 실행한 source commit SHA: persistent service는
  `7ecabbc2422ea5b3875dbed279cb354fa8e9f30c`의 reviewed source image를 계속 사용했습니다. 실행
  당시 저장소 HEAD의 후속 source는 recovery-drill 전용이며 persistent image를 교체하지 않았습니다.
- source·deployment 결속:
  - persistent image binding은 activation 기록과 같은
    `d80bc094ad1875bf52f61d785d0495c62b4a6c5577882de180e43cdebe381748`입니다.
  - control Compose generation은 `a464c646fd6a786ff6262f9d5408af2a`, restart policy는
    `unless-stopped`, network mode는 `host`였습니다.
  - qualified target과 앞선 qualification·MQTT·activation·actual-recovery 기록의 물리 identity
    binding이 같음을 확인했습니다.
- 실행 전 조건: persistent control service exactly `1`, restart `0`, old `jebao-flowd` `0`,
  unfinished local-control journal `0`, retained command `0`, 장비 `6/6` fresh였습니다. selected target은
  ON·TimerOFF·`constant`·`independent`·Flow `40`, command surface는 qualified `manual_flow` 하나,
  group supervisor `1`·armed `0`이었습니다.
- 실행 전 durable inventory: local-control journal은 operations `5`, blobs `12`, files `17`,
  path+content tree SHA-256
  `33482907c163f523b4cb0dacc127cedf4e9d34211cab86884e588c3b87a21384`였습니다. qualification
  directory는 files `4`, receipt `2`, tree SHA-256
  `c626ae6e22ec436a4290f05ced6f1fe7e7cce185a2f93880c37565bd7fccb76a`였습니다.
- 사용한 명령·주요 파라미터 — broker-loss/restart:
  - read-only MQTT monitor가 current availability·group state와 retained command 부재를 확인한 뒤
    control container 하나만 pause했습니다. host network에서 socket kill이나 host network 조작은
    다른 client까지 건드릴 수 있어 사용하지 않았습니다.
  - 고정 sleep을 성공 근거로 쓰지 않고, paused 상태에서 broker가 retained global availability를
    `offline`으로 바꾼 것을 직접 관측한 뒤 즉시 unpause했습니다. pause는 control process 전체를
    동결하므로 pause 구간에는 그 process가 device write를 실행할 수 없습니다.
  - 죽은 broker socket을 만난 daemon process는 종료됐고 `unless-stopped`가 같은 reviewed service를
    다시 시작했습니다. `restart_count 0 -> 1`과 pause 구간의 HA unavailable은 실행 전에 예상된
    관측 결과로 선언했습니다.
- reconnect 관측 결과:
  - monitor ready `2026-09-11T07:35:29.474883Z`, pause
    `2026-09-11T07:35:29.540514Z`, retained `offline`
    `2026-09-11T07:36:53.082850Z`, unpause `2026-09-11T07:36:53.138416Z`, 새 process의 retained
    `online` `2026-09-11T07:36:54.933432Z`, reconnect postcheck 완료
    `2026-09-11T07:37:23.359919Z`였습니다.
  - 복귀 과정에서 device availability `6`, device state `73`, group status `4`개가 재발행됐고,
    retained command와 live command event는 `0`이었습니다.
  - command QoS는 `0`이고 daemon은 기동마다 새 random client id를 사용하므로 persistent MQTT
    session에 command가 쌓였다 재배달되는 경로는 없습니다. 별도로 command topic의 retained
    payload가 `0`임을 확인한 것은 이 구조 밖에서 누군가 남긴 retained command도 없다는
    load-bearing 관측입니다.
  - broker loss를 만난 process의 MQTT 오류는 restart를 유도한 예상 전이였습니다. 새 process가
    안정화된 뒤 warning·error·traceback count는 각각 `0`이었습니다.
- 사용한 명령·주요 파라미터 — bounded soak: reconnect 검증 뒤 별도 read-only MQTT client로
  정확히 1,800초를 관측했습니다. HA command surface는 열려 있었지만 구간 내 사용하지 않았고,
  service나 broker를 추가 조작하지 않았습니다.
- 종료 지점(stage / failure code): `soak_complete`와 strict direct-device/broker postcheck가 모두
  PASS했습니다. service는 exactly `1`, running, restart `1` 상태로 계속 실행 중입니다. container를
  다시 만들기 전까지 후속 operation이 기대할 restart baseline은 `1`입니다.
- 장비·broker·daemon이 만든 결과:
  - 증거 등급: 실행 전후 fresh process에서 다시 읽은 journal·qualification file inventory와
    path+content tree digest는 **(b) preserved structured/durable daemon artifact**입니다. MQTT event
    monitor, service state·logs와 independent authenticated device read는
    **(c) reconstructed operator observation**입니다. 원본 device·broker frame을 파일로 보존하지
    않았으므로 (a)를 주장하지 않습니다.
  - opaque artifact id: control Compose generation `a464c646fd6a786ff6262f9d5408af2a`.
  - UTC span: broker-loss monitor ready `2026-09-11T07:35:29.474883Z`, 최종 strict postcheck
    `2026-09-11T08:14:41.560623Z` 이전. 30분 soak의 exact span은
    `2026-09-11T07:41:49.125594Z`부터 `2026-09-11T08:11:49.126197Z`까지였습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. private physical
    identity 원문이 아닌 `qualification-target:` domain-separated binding이며 앞선 first-write,
    qualification success, MQTT control, persistent activation, actual-recovery 기록과 같은 물리
    개체입니다.
  - artifact digest (SHA-256): journal tree
    `33482907c163f523b4cb0dacc127cedf4e9d34211cab86884e588c3b87a21384`, qualification tree
    `c626ae6e22ec436a4290f05ced6f1fe7e7cce185a2f93880c37565bd7fccb76a`.
  - 1,800초 soak에서 fresh cycle은 `20 -> 147`, delta `127`이었습니다. device state event
    `9,198`, group status event `126`, device online identity count `6`을 관측했고 command event,
    retained command, global offline event, device offline event는 모두 `0`이었습니다.
    이 구간의 실측 fresh-cycle 유효 주기는 약 `14.2`초입니다. configured poll interval `5`초에
    6대의 순차 authenticated sweep 시간이 더해진 current deployment 기준선이며, 장시간 latency
    보장으로 일반화하지 않습니다.
  - service running sample 전부에서 not-running·paused count는 각각 `0`, restart는 시작·최대·종료
    모두 `1`이었습니다.
  - 종료 durable inventory는 journal operations `5`·blobs `12`·files `17`과 위 tree digest,
    qualification files `4`·receipt `2`와 위 tree digest로 byte-exact 불변이고 unfinished journal은
    `0`입니다. current local-control은 모든 control frame보다 먼저 `FORWARD_ATTEMPTING` journal을
    durable 저장하므로 이 경로의 reconnect·soak device write는 `0`으로 판정합니다. 이 추론을
    frozen/native 또는 다른 write 하네스에 일반화하지 않습니다.
  - 종료 strict read에서 장비 `6/6` 각각 새 authenticated state read를 완료했습니다. selected
    target은 ON·TimerOFF·`constant`·`independent`·Flow `40`이었습니다.
  - 종료 broker postcheck는 global online, device online `6`, retained device state `73`, control
    discovery `1`, selected control state `40`, retained command `0`, group supervisor `1`·armed `0`,
    fresh cycle `155`, old daemon `0`이었습니다.
- 원복 검증 결과: 이 operation은 device command를 보내지 않았고 selected target의 independent
  fresh Flow가 시작·종료 모두 `40`이어서 device restore frame은 필요하지 않았습니다. pause는
  해제됐고 service는 restart `1`의 running state로 복귀했습니다.
- **이 실행으로 새로 확정된 사실:** current persistent control은 실제 broker keepalive loss 뒤
  in-process reconnect가 아니라 supervised process restart로 복귀하며, 이 경계에서 stale·retained
  command를 소비하거나 startup device write를 만들지 않고 availability·device/group state를
  재발행할 수 있습니다. 이어진 30분 bounded 관측에서도 unexpected command, offline 전이,
  journal 변화와 추가 restart 없이 장비 `6/6` fresh를 유지했습니다.
- 이 실행으로 확정하지 못한 것: 30분을 넘는 장시간 안정성, 반복 broker-loss storm,
  restart policy가 없는 배포, external app state drift 반영, 다른 qualified target·function,
  software group actuation, Q1·Q2·native ASYNC capability는 증명하지 않습니다. 이 결과를
  in-process reconnect 구현이나 일반적인 host-network 장애 격리 증거로 승격하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: broker-loss 시험은 service restart 증가를
  사전 선언하고 fixed timer가 아니라 retained `offline` will을 관측해 단절을 판정합니다. 시작 전
  restart policy와 unfinished journal `0`, 종료 후 journal tree·retained command·6/6 authenticated
  read를 대조합니다. 다음 M2 항목인 external app drift는 agent device write `0`의 별도 실행으로
  분리하고, 그 기록이 랜딩된 뒤 M3 qualification 확대를 진행합니다.
