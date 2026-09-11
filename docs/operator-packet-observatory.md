# Operator packet observatory and local-first control boundary

## 목표와 현재 상태

이 설계의 목표는 제바오 앱을 흉내 내는 것이 아니라, 앱이 실제로 만든 wire intent와 장비
응답을 보존해 사람이 확인할 수 있게 하고 그 증거 위에서 더 넓은 로컬 제어 기능을 만드는
것입니다. 1차 제품 목표는 등록된 다섯 제품군의 장비 기능을 로컬에서 소유하고 Home
Assistant에서 제어할 수 있게 하는 것입니다. 두 Local Wavemaker Pro에는 master의 48개
schedule slot 전체를 slave에 투영하면서 slave마다 각 slot의 Flow를 따로 지정하는 확장 모델도
포함합니다.

Track A/B는 PCAP ingest, TCP 재조립, frame catalog, 내구성 있는 raw 증거, 앱 기능 catalog,
증거 승격의 offline 단계와 inert schedule model을 제공합니다. 별도 승인으로 부분 해제된 Track C는
`local_control/`에 격리된 writer, 독립 write-ahead journal, fresh endpoint resolver, Home Assistant/MQTT
adapter와 Pro slave별 48-slot Flow composition을 구현했습니다. 구현과 실장비 qualification은
분리하며, 현재 accepted write 영수증은 다섯 제품 전체에서 `0`입니다. 상세 계약은
[`local-control.md`](local-control.md)를 따릅니다.

## 구조

```text
Jebao app or local controller <---- unchanged LAN traffic ----> device
                                |
                         passive mirror/SPAN
                                |
                        classic PCAP stream
                                v
  TCP sequence reassembly -> GAgent framing -> positive protocol catalog
                                |
               owner-only raw blobs + hash-chained JSONL
                                |
        capability evidence ledger -> read-only HA diagnostics

  qualified catalog bytes -> separate device_writer -> ACK/readback -> HA controls
```

관측기는 data path에 들어가지 않습니다. v1에는 transparent MITM, ARP spoofing, DNAT,
discovery spoofing 또는 인증 세션을 대신 잡는 relay가 없습니다. 따라서 앱과 장비 사이의
latency·재시도·제어권을 바꾸지 않습니다. 앱은 상세 역설계와 exact intent 수집의 기준이며,
최종 runtime 의존성은 아닙니다.

## 캡처·재조립 계약

- 입력은 stdin의 classic PCAP입니다. 관측기 자체는 interface를 열거나 장비 주소로 연결하지
  않습니다. SPAN/OpenWrt capture 배치는 별도 네트워크 검토 뒤에 결정합니다.
- Ethernet, Linux cooked v1/v2의 IPv4/TCP를 인식하고 기본 장비 포트 `12416`을 양방향으로
  분류합니다. 주소는 메모리에서 4-tuple을 구분할 때만 사용하고 공개 event에는 allocation
  순서의 `flow-NNNNNN`만 남깁니다.
- TCP sequence로 방향별 stream을 재조립합니다. wire-identical retransmission은 한 번만
  decode하고 발생 사실은 별도 event로 남깁니다. gap, history 밖 overlap, 서로 다른 overlap,
  frame magic·length 손상 또는 EOF의 partial frame을 만나면 그 방향을 `DESYNCHRONIZED`로
  고정하고 이후 frame 해석을 중단합니다. 이후 raw packet 보존은 계속합니다.
- 하나의 chunk에 여러 frame이 있거나 하나의 frame이 여러 chunk로 나뉘는 것은 정상입니다.
  손상 뒤 magic을 검색해 임의 resync하지 않습니다.
- command 이름만 안다고 payload 전체를 안다고 보지 않습니다. command-only 항목과
  `(command, action)` 항목을 positive catalog에 각각 열거하며, 열거되지 않은 조합은 offset과
  length가 있는 `unknown` 진단입니다.

## 내구성·privacy 계약

`JEBAO_FLOW_OPERATOR_CAPTURE_ROOT`는 repository 밖의 owner-only `0700` 절대경로여야 합니다.
CLI는 `/tmp`, `/private/tmp`, `/var/tmp`와 실행 환경의 temporary root를 거부합니다. raw PCAP,
frame, 사설 주소, device id와 passcode는 이 private root 밖으로 출력하지 않습니다.
이 저장소의 임시 worktree·private config·작업 worktree가 실제로 반복 소실됐으므로 이 제한은
경로 취향이 아니라 evidence durability 조건입니다. 모든 공개 JSON 직렬화도 ASCII escape를
강제하고, 공개 모델은 비-ASCII label 자체를 생성 시점에 거부합니다.

한 root에는 `flock` lease 하나와 단조 generation ledger가 있습니다. session은 generation과
무작위 token을 합친 opaque id를 사용합니다. 각 raw 객체는 SHA-256 content address에
`0600` no-clobber로 설치됩니다. 같은 frame의 반복은 기존 bytes가 digest와 정확히 같을 때만
idempotent success이고 JSONL의 각 occurrence는 별도 `seq`입니다. digest 이름의 기존 bytes가
다르면 즉시 거부합니다.

각 JSONL record는 이전 chain digest를 포함하고 record마다 `fsync`합니다. 정상 종료는 terminal
record와 no-clobber manifest를 설치합니다. crash·전원 손실로 manifest가 없으면 session은
폐기하지 않고 `INCOMPLETE`로 보존하며, 마지막 완전한 hash-chain record까지 검증합니다.
orphan blob도 자동 삭제하지 않습니다.

공개 CLI 출력은 session id, generation, count, 상태, chain/journal digest와 bounded
semantic뿐입니다. operational frame digest는 증거 승격에 필요할 때만 보이며, IP/MAC header를
가진 PCAP packet과 login/passcode/discovery identity frame의 raw-object digest는 사전대입
위험 때문에 공개 event에서 제거합니다. private root 경로와 raw bytes를 출력하지 않습니다.
operator note도 MAC, 32-hex device id, IPv4 모양을 거부합니다.
Binary schedule과 clock datapoint는 semantic JSON에 raw bytes를 복제하지 않고 각 필드의
길이와 content SHA-256으로만 투영하므로, schema 확장 뒤에도 event는 직렬화 가능하고 bounded
raw provenance는 private blob에 남습니다.

### Fresh install preparation

`jebao-flow-observe prepare-install`은 설치할 때마다 현재 입력과 현재 interface 목록을 다시 읽어
private install manifest를 **통째로 새로 만들고 atomic replace**합니다. 이전 manifest를 읽거나
기본값으로 병합하지 않으므로 오래된 자동탐지값 때문에 실패하는 분기를 만들지 않습니다. 지금
자동 판별할 수 없는 현재 interface 후보가 여러 개일 때만 그 실행에서 명시 선택하며, 다음 설치는
그 선택도 상속하지 않고 다시 관측합니다. 각 runtime 항목에는 `method`와 `rederived_at`을 함께
기록하고 공개 출력에는 private path를 내보내지 않습니다.

영구 mapping 기준은 `product_key`, `physical_identity_key`, operator-confirmed logical role,
검증된 wire layout과 content SHA-256뿐입니다. PID, IP, ephemeral source port, operator UI listen
port, container/capture session id, hostname, cache path·directory name와 file mtime은 provenance나
그 실행의 진단일 뿐 mapping·설치 gate가 아닙니다. 반면 device control port `12416`, frame magic,
action code와 Pro의 48-slot layout은 변동 runtime 값이 아니라 protocol/source 상수입니다.

## 앱 기능 catalog와 지원 상태

정적 catalog는 Jebao Aqua `3.4.18`/build `341800000`, cached UI template, vendor declaration에서
파생된 **관찰 후보 목록**입니다. 제품키로 scope하며 앱·vendor 선언은 firmware acceptance가
아닙니다. vendor datapoint의 `source_label`과 사용자에게 보이는 영문 `exposed_name`을 분리하고,
실제 의미는 반복 capture와 differential evidence 뒤에만 채웁니다. 공개 catalog·CLI·Home
Assistant의 이름과 상태 문자열은 ASCII 영문만 허용하며 앱 artifact의 한자 label을 복사하지
않습니다.

| 상태 | 뜻 | Home Assistant 노출 |
|---|---|---|
| `UNKNOWN` | 해당 제품의 exact wire 관측이 없음 | 숨김 |
| `OBSERVED` | preserved capture에 exact frame과 출처가 있음 | diagnostic만 |
| `REPRODUCED` | 같은 의미의 서로 다른 retained occurrence가 2개 이상 | diagnostic만 |
| `DIFFERENTIAL` | 이웃 값 비교에서 기대한 영역만 달라 의미를 확인 | diagnostic만 |
| `QUALIFIED` | 별도 승인 실측과 ACK/readback 영수증 2/2 | 해당 physical device에만 control 노출; 현재 영수증 0 |

`OBSERVED`를 곧바로 control로 노출하지 않습니다. UI control의 존재는 지원된다는 주장과 같기
때문입니다. qualification된 Flow control의 HA native 범위는 앱이 선언한 운전 범위 `30..100`과
같습니다. 현재 agent 실기의 `<=50` 계획값은 수조의 갑작스러운 유량 변화를 줄이기 위한 별도
attended-operation 가이드이며 HA 입력 validation이나 제품 capability 범위가 아닙니다.

승격은 아래 순서를 건너뛰지 않습니다.

1. 같은 시계의 exact intent frame과 장비 response를 보존합니다.
2. 같은 의미 동작을 두 번 관측해 nonce·sequence·timestamp를 상수로 오인하지 않습니다.
3. 이웃 값과 differential 비교해 의미 필드만 변했는지 확인합니다.
4. 동결 해제와 별도 실기 승인 뒤 catalog가 가리키는 **관측 bytes**를 bounded live test에서
   재생하고 ACK와 readback을 확인합니다.
5. 영수증 2/2 뒤에만 `QUALIFIED`와 HA control을 만듭니다.

앱 표현 가능성과 장비 수용 여부는 별도 축입니다. 예를 들어 slave slot별 Flow는
`INFEASIBLE_BY_APP`와 `UNKNOWN_ON_DEVICE`가 동시에 참입니다. `앱이 표현하지 못함`을
`장비가 지원하지 않음`으로 승격하지 않습니다.

관측 단계의 replay 후보는 유래 capture session·seq·frame digest를 가져야 합니다. 별도로
qualification된 local-control writer는 현재 설치의 content digest에 결속된 audited schema로
결정론적 payload를 만들 수 있지만, catalog에 없는 필드나 `Auto*` status write를 추측해
추가하지 않습니다. 앱이 기능을 표현하지 못한다는 결과와 firmware가 기능을 지원하지 않는다는
결과도 구분합니다.

## 앱 parity와 확장 schedule

현재 catalog는 등록된 다섯 제품군을 모두 포함합니다.

- Local Wavemaker Pro: 전원, timer, 9개 manual mode, Flow/Frequency/custom frequency/feed,
  48×9 schedule, independent/master/sync-slave/async-slave, slave common Flow
- Local Wavemaker: 전원, feed, pulse/tide, timer, 4개 manual mode, Flow/Frequency/feed,
  48×8 schedule, independent/master/slave
- DC Pump Pro: 전원, timer, 5개 mode, Flow/Frequency/feed, 48×8 schedule
- Aquarium Pump: 전원, AP/wireless transport mode, feed, timer, motor speed, 48×6 schedule
- Dosing Pump: 4채널 manual/timer, interval, calibration, 채널별 24×4 schedule
- 앱 shell: discovery/binding, owner 확인, rename, room, delete, scene, group view/edit, status
  refresh, channel-name lookup, universal menu와 firmware-update 진입

이 목록은 device operation parity의 추적표입니다. cloud account, vendor firmware 배포와 외부
automation cloud를 로컬 runtime 의존성으로 복제한다는 뜻은 아닙니다. 그 기능이 로컬 제품에서
필요하면 별도의 local equivalent로 명시합니다.

`NativeSchedulePlan`은 정확히 48칸인 Pro master slot vector와 하나 이상의 logical slave를
가집니다. 각 칸은 active schedule 또는 명시적인 unused 상태이며, runtime은 master의
time/mode/frequency/feed/custom-frequency bytes를 slave에 복사합니다. 각 active slot의 Flow는
그 slave의 fresh current image에서 보존하고 명시된 app-declared `30..100` override만 교체합니다. current
slave slot이 unused인 경우에만 master Flow로 시작합니다. 따라서 bulk composition이 이전에
설정한 slave별 Flow를 조용히 master 값으로 되돌리지 않습니다. 앱의 공통 slave `Flow`도 slot별
`AutoFlow`와 합치지 않고 별도 operation으로 둡니다.

Sync와 Async 모두 같은 desired-state 모델을 쓰며 48개 원소 각각에 declared app range를
적용합니다. Pro의 slot byte `5`는 mode와 무관한 Flow이지만 다른 제품군에서는 같은 위치가
feed time일 수 있으므로 이 모델을 일반화하지 않습니다. Pro raw image의 date/time 영역은
device-owned로 고정하고 inert 모델과 미래 schedule writer가 소유하지 않습니다. 앱 UI에는
slave slot editor가 없으므로 앱 capture에서 이 기능의 완성 frame이 직접 나오지 않으며,
**앱 표현은 불가능하고 장비 수용 여부는 아직 unknown**입니다.

`indexed_only_precedence_unknown`은 시간 순서를 추측해 새 거부 조건을 만드는 뜻이 아닙니다.
향후 writer도 slot index와 unused 위치를 그대로 보존하며 start/end를 정렬하거나 overlap의
승자를 발명하지 않습니다. 공통 slave Flow와 per-slot override는 별도 operation으로 유지하고
한쪽이 다른 쪽보다 우선한다고 암묵적으로 합성하지 않습니다.

최종 인코딩 근거는 vendor product definition과 앱의 저장 dataflow에서 구성적으로 얻습니다.
Pro의 48x9 schedule layout, slot byte `5` Flow, 개별 `AutoTimeNN` write와 all-slot reset 형태는
정적으로 결속됐습니다. runtime은 매 operation에서 master와 각 slave의 fresh 432-byte image를
읽고, master schedule bytes와 slave Flow를 위 규칙으로 조합합니다. 이 결정론적 구성은 device
acceptance를 뜻하지 않으며, bounded live qualification 전까지 control은 숨겨집니다.

## 앱 본체와 template 전체 감사 경계

정적 감사 대상은 등록 제품 template 일부가 아니라 설치 앱 본체, 캐시된 plain-JS template
14개 전체, 등록 product definition 다섯 개, 그리고 JS-to-native host bridge입니다. 현재 확인된
host call surface는 `sendCmd`를 포함한 14개 호출이며 공개 catalog에 영문 이름으로 고정합니다.
credential·token store는 열지 않고, 추출·decompile 산출물과 private cache 경로도 저장소에
넣지 않습니다.

숫자가 가장 큰 cached template를 active라고 간주하지 않습니다. template의 영구 identity는
JavaScript **내용 SHA-256**뿐이며 cache directory 이름·bundle id·mtime·size는 provenance일 뿐
mapping이나 gate가 아닙니다. Bundle id는 실제 제품과 일치하지 않았으므로 product-specific
datapoint fingerprint와 content digest로 매핑합니다. 현재 runtime-active template 선택은
`unresolved`이지만, 여러 retained 판본에서 같은 save/reset/clock/Linkage dataflow는 그 digest
집합에 결속된 static fact로 기록합니다.

Native audit도 완료했습니다. Template `sendCmd`는 semantic object를 host `callService`로 넘기고,
앱은 Gizwits SDK `write:withSN:`으로 진입하며, SDK가 vendor product definition에서 P0 bytes를
조립합니다. 네 schedule 제품의 exact geometry, `0xEE` unused sentinel, zero-prefixed HMS,
제품별 single-slot/full-image save와 reset 차이는 이 경로에서 확인됐습니다. Wire acceptance와
물리 효과는 별도 evidence 축이라 계속 `UNKNOWN`입니다.

Hermes 본체의 `sendCmd` wrapper는 payload를 native host call로 넘기기 전에 local `data`에
낙관적으로 합치고, 전송 뒤 약 2초 동안 같은 key의 incoming update가 local 값을 덮지 않도록
억제합니다. 따라서 앱 화면이 명령값을 보여 주는 것은 ACK도 device readback도 아닙니다.
capture evidence는 exact outgoing intent, device response, 별도 readback을 서로 다른 event로
보존하고 APP-to-device notification도 ACK로 오인하지 않습니다.

## exact restore와의 분리

wire PCAP는 앱 intent와 장비 response의 forensic evidence입니다. exact-restore `prepare`가
소비하는 collector artifact, operation manifest, durable journal 또는 rollback authority가
아닙니다. 서로를 대체하거나 freshness를 이월하지 않습니다.

첫 실제 passive capture도 장비가 만든 packet을 접촉하는 live run입니다. 실행 전
`docs/hardware-readiness.md`의 quiescence·approval을 다시 통과하고, 새 `docs/runs/` 파일에
private artifact의 opaque id, UTC span, digest와 identity binding만 기록합니다.

## 동결 경계와 다음 단계

`operator_capture` import closure에는 `jebao_flow.devices.*`, `protocol.control`,
`protocol.control_session` 또는 미래의 `operator_capture.device_writer`가 들어가지 않습니다.
회귀 테스트가 이 경계를 고정합니다. 기존 동결 여섯 파일과 그 테스트도 수정하지 않습니다.

write-capable local runtime, HA control service와 Sync/Async schedule composition은
`AGENTS.md`의 2026-09-08 부분 해제와 `unfreeze:` commit 아래 신규 `local_control/` 경로로만
구현합니다. 기존 동결 여섯 파일과 관측기의 write-free import closure는 그대로 유지합니다.

구현 뒤에도 device write, 앱 조작, HA 연결, passive mirror 배치는 각각 별도 실기 승인과 exact
restore 준비를 요구합니다. app-transparent relay/MITM은 local-first 제품에 필요하지 않으며,
추후 필요성이 증명될 때 별도 위험 경계로 검토합니다.
