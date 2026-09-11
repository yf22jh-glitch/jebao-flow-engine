# run: 2026-09-11-local-manual-flow-qualification-first-write

- 목적(한 문장): 선택한 Local Wavemaker Pro 한 대의 `manual_flow`에 대해 reviewed
  local-control qualification producer가 동일값 도달, 1%p 하향 변화와 exact planned restore를
  실제 ACK와 fresh readback으로 증명할 수 있는지 확인했습니다.
- 이 실행이 답하려던 질문: app에서 `constant 40%`, TimerOFF, `independent`로 준비한 한 장비가
  strict current identity와 bounded first-write envelope 아래에서 `40 -> 39 -> 40` 세 frame을
  수행하고 하향·복원 receipt 두 장을 만들 수 있는가.
- 실행한 commit SHA: `2cc192ead7923f8329c6b4f8e451bda63bd2c060`
- 사용한 명령·주요 파라미터: D1h producer image에 위 D1i commit의 exact `lease.py`를 반영한
  one-shot image를 사용했습니다. Python source file count는 `131`, canonical source-tree SHA-256은
  `5f66cd860cdf0c5ce76c2299a4fc0615b19018b601318258d0e5b2b06cda0c06`, image id의 SHA-256은
  `8a0bc676d85158a883dd4275191340271f74da991635288b3e0289590488d6d6`이었습니다. 최종 source
  파일 mode는 Git의 regular non-executable mode를 반영한 `0644`로 다시 읽어 확인했습니다.
- 사용한 명령·주요 파라미터(계속): 실행 직전 `runtime.mode=observer`, `dry_run=true`, observer
  instance 1개, `--write-enabled` 없음, qualification input 없음, old `jebao-flowd` 0개, custom
  component config entry 0개, configured·enabled·manifest device 각 6개, fresh device read 6개,
  absent·ambiguous 0개, unfinished local-control journal 0개와 D1i terminal tombstone `idle=true`를
  확인했습니다. active-template SHA-256은
  `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`, stable role-binding
  SHA-256은 `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`로 current
  manifest와 일치했습니다. app precondition과 일치하는 Pro target은 정확히 한 대였습니다.
- 실행 전 정정: 첫 source overlay는 host 파일의 `0600` mode가 따라와 non-root image import에서
  거부됐고, 두 번째 archive는 Git archive 기본 group-write mode `0664`가 exact-mode 단정에서
  거부됐습니다. 두 경우 모두 image 검증 중단으로 observer·broker·device에 접촉하지 않았고,
  잘못 만든 one-shot image만 정확한 tag로 제거한 뒤 Git blob bytes와 `0644` mode로 다시
  만들었습니다. 이어 live wrapper의 `RestartCount == 0` 단정은 이미 건강하게 실행 중인
  container의 과거 누계가 `1`이라는 이유로 observer stop 전에 종료됐습니다. 현재 6/6 fresh와
  무관한 비안전 절대값 gate로 판단해, 이후에는 stop/start 전후 현재 상태를 직접 확인했습니다.
- 실행 전 정정(계속): 첫 stop-first 호출은 one-shot overlay가 새 console-script installer를
  포함하지 않아 executable lookup exit `127`로 끝났습니다. qualification module은 호출되지 않아
  journal·receipt·device frame이 모두 0개였고, fresh readback `40`을 확인한 뒤 observer를
  재기동했습니다. 같은 reviewed module을 `python -m`으로 직접 호출하는 방식으로 다시 시작했고,
  아래 결과만 실제 qualification operation입니다.
- 종료 지점(stage / failure code): observer를 완전히 정지한 뒤 opaque operation
  `LCQ-764e68c124550c9cd09f8c73708f2319`으로 `downward_step=1`과 explicit write opt-in을 주어
  producer를 실행했습니다. producer는 첫 `same_value` action을 durable
  `FORWARD_ATTEMPTING`으로 만든 뒤 generic `current_operation_failed_closed`, exit `2`로
  종료했습니다. forward를 재개하지 않고 같은 operation id의 승인된 `recover`만 실행했고,
  transaction은 revision `6`, phase `rolled_back`으로 terminal 종료했습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: **(b) preserved structured/durable daemon artifact**. local-control journal과 그
    referenced blobs를 fresh process에서 다시 읽어 phase·action·digest를 확인했습니다. 원본
    request/response frame은 보존하지 않았으므로 (a)로 올리지 않습니다. 종료 후 exact state와
    observer 6/6은 **(c) reconstructed operator observation**입니다.
  - opaque artifact id 또는 안전한 상대 논리 경로:
    `LCQ-764e68c124550c9cd09f8c73708f2319`
  - UTC span (시작 / 종료): `2026-09-11T02:14:43.145908767Z` /
    `2026-09-11T02:15:34.540557066Z`. 이는 stop-first부터 recovery, observer 재기동과 final
    6/6 fresh 확인까지의 전체 구간입니다. journal 자체의 created/updated span은
    `2026-09-11T02:14:50.325397Z` / `2026-09-11T02:15:01.223794Z`였습니다.
  - SHA-256 identity binding:
    `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`. 이는 private physical
    identity key 원문이 아니라 producer와 같은 `qualification-target:` domain-separated SHA-256
    결속입니다.
  - artifact digest (SHA-256): terminal local-control journal
    `231d63e253a904686fbf5e8aa7f5a9468ac1d9fd7493389fb0e4ecfc16af7db6`.
  - `same_value`는 recovery 전 PREPARED를 벗어나 send boundary까지 진행했지만 terminal journal에
    acknowledgement digest가 없습니다. 따라서 실제 wire delivery는 `0`인지 `1`인지 확정하지
    않으며, acknowledged frame은 `0`입니다.
  - `lower_power`와 `planned_restore`는 둘 다 `forward_never_attempted`, acknowledgement 없음으로
    terminal 기록됐습니다. 즉 `39%` 하향 frame과 계획된 `40%` 복원 frame은 보내지 않았습니다.
  - same-value·planned-restore payload digest는
    `e99c8539a7316069cc2c403d755d82cf8567f130944c06924505299783e61ae3`, lower-power payload
    digest는 `56ab9d309cba1c69d66600d3b0bd98272fa8197d9398f04f1ada14891e9ee79b`였습니다.
  - 새 qualification binding은 생성되지 않았고 fixed receipt root entry count는 실행 전후 `2`로
    같았습니다. 따라서 이 operation이 발급한 receipt는 `0`장입니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): reverse recovery에서 미시도 두 action은
  `forward_never_attempted`, same-value action은 fresh read가 이미 baseline임을 확인한
  `baseline_already_present`로 닫혔습니다. 따라서 recovery control frame은 보내지 않았습니다.
  별도 fresh strict read에서 선택 장비는 `40%`, ON, `constant`, TimerOFF, `independent`였고,
  그 뒤 observer 한 instance를 재기동해 장비 6/6 fresh, absent·ambiguous 0, role-binding digest
  불변, command surface 0, old daemon 0을 확인했습니다. observer의 current restart count는
  `0`이었습니다.
- **이 실행으로 새로 확정된 사실:** D1i의 positively verified terminal-tombstone admission은 실제
  private artifact에서 동작해 qualification이 이전 interlock을 통과했습니다. D1h producer도
  operation record와 세 recovery blob/action을 첫 frame 전에 durable하게 만들고, 첫 same-value
  action의 ACK 증거가 생기지 않자 하향·계획 복원을 재생하지 않은 채 exact baseline recovery로
  terminal 종료했습니다. 즉 이 실행은 receipt 자격을 만들지 않았지만, 실패 뒤 잘못된 39% 단계로
  진행하지 않는 fail-closed·no-retry·recovery 성질을 실기에서 확인했습니다.
- 이 실행으로 확정하지 못한 것: same-value frame이 실제 wire를 건넜는지, ACK가 없어진 직접 원인이
  empty response body·timeout·sequence/transport 오류·pre-send permit 중 무엇인지, 장비가 39% 하향과
  40% planned restore를 받아들이는지, qualification receipt 두 장과 control-mode 동작, firmware
  capability, Q1·Q2는 확인하지 못했습니다. 최종 baseline이 같다는 사실을 same-value actuation
  성공으로 승격하지 않습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 이 operation을 자동 재실행하지 않습니다.
  먼저 보존된 기존 성공 write의 control-response shape와 현재 writer ACK 계약을 write 없이 대조해
  empty ACK가 정상 transport evidence인지 확인합니다. positive evidence가 없으면 ACK gate를
  완화하지 않고 qualification을 park합니다. 코드 내부 진단 공백을 수정해야 한다면 이 기록을
  선행 근거로 별도 policy/source slice와 offline 회귀를 거친 뒤, 새 live operation은 maintainer와
  on-site hardware approver의 별도 명시 승인으로만 검토합니다.

## 검토 후 정정

- 위 기록의 `durable FORWARD_ATTEMPTING`은 terminal journal이 직접 보존한 중간 phase가 아니라
  producer의 send 경계 코드와 terminal action 결과를 결합한 추론입니다. journal store는 current
  revision 하나만 보존하므로, 이 실행의 보존 artifact가 직접 증명하는 것은 terminal revision과
  action 결과입니다. 따라서 중간 phase가 recovery 직전 어떤 값으로 durably 남아 있었는지는
  증거등급 (b) 사실로 승격하지 않습니다.
- 위 `exact baseline recovery`는 restore frame을 송신한 경로가 아닙니다. 이 실행에서 직접 확인한
  것은 fresh strict read가 이미 baseline `40%`와 일치해 `baseline_already_present`로 종료된
  **frame 0 recovery**입니다. exact-restore frame 송신 경로가 실기 검증됐다고 주장하지 않습니다.
