# run: 2026-09-13-local-group-sync-commissioning

- 시간 기준: 파일명 날짜는 KST 기준이고 아래 timestamp는 UTC입니다.
- 목적(한 문장): software-independent 세 멤버의 Sync 30/31 파형이 두 번의 방향 전환과
  같은 목표의 유한 hold를 거쳐 bounded stop으로 끝나는지 확인합니다.
- 이 실행이 답하려던 질문: manual-owned 세 멤버에 같은 목표가 적용되고, 31로 재방문할 때
  과거 identity 때문에 suppress되지 않으며, 같은 목표의 후속 cycle에서는 중복 operation이 없는가.
- 사용자 경계:
  - repository maintainer이자 on-site approver인 사용자는 집에서 수조를 감시하며 진행을 승인했고,
    앱 조작과 중단 수단을 담당했습니다. 임시 Sync 배포, ON 한 번, 두 전환 관측, 앱 복원과
    exact prior Constant50 서비스 복귀가 범위입니다. 다른 physical operation은 하지 않았습니다.
  - 앱 precondition은 첫 Pro 40→30, Bar classic/91→constant/30의 61%p 하향·파형 변경,
    두 번째 Pro의 TimerOFF 전환입니다. 두 번째 Pro의 수동 레지스터가 이미 30이어도 그 시점
    schedule 출력에서 manual30으로 운전이 바뀝니다. 조작 전 fresh 관측의 sine/45와 이 차이를
    사용자에게 알렸으며, 과거 slot을 현재값으로 고정하지 않았습니다.
  - 복원 뒤 두 번째 Pro는 TimerON과 latent manual31이었습니다. 사용자는 비활성 31 유지를
    수용했습니다. 원래 manual30의 exact 복원은 주장하지 않습니다. 다음 TimerOFF 때의 수동
    설정은 31이며, 이 수용을 다른 run의 baseline으로 소급하지 않습니다.
- 실행한 commit SHA: 정책 `d183bc3e75bc2bec5c50687929f6c5dfcfaec58e`.
  source/tests는 수정하거나 재빌드하지 않고 exact image
  `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`를 사용했습니다.
- 사용한 명령·주요 파라미터:
  - 기존 prepare-compose·stage·serve와 MQTT group ON 경로. QoS0, retain false, publish 한 번.
  - Sync min30/max31, period120초, transition0, enabled member3, gain1.
    default.power도 50→30으로 바꿨지만 **Sync는 이 값을 읽지 않습니다**. 이 변경은 안전 근거가
    아니며 실제 목표 선택과 상한은 min/max30/31입니다. fallback ceiling도 min/max로 clamp됩니다.
  - ON 전에 subscribe-only 관측을 시작했고, 기존 coordinator의 resolver·lease·journal-first를
    사용했습니다. 감사 도구가 별도의 production exclusive lock을 획득하지 않았습니다.
- 종료 지점(stage / failure code): VERIFIED record3, 각 revision13, Flow frame9, power frame0.
  세 번째 terminal 관측 직후 service stop, observer exit0, 별도 fresh read와 prior 서비스 복귀 완료.

## 배포 입력과 실행 전 결속

prior Compose generation은 `2b4d91a3a662ac2aa85bacf91a03f72b`, Compose SHA-256은
`9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`, 보존 bundle4의 tree는
`4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`입니다.
임시 generation은 `056fbf5fbbc482b365c56b8b688ff28b`, Compose digest는
`fa80252555c8ac51c96ef8d01acded8f05c237ec0208fe4638827040eb5c2ba1`, bundle4 tree는
`3961fa9e54d5b2a37becec061adb7679bdf018eaf92bddb16aff57aeb200619d`입니다.
qualification input은 기존 2,429 bytes,
`b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033` 그대로입니다.

config의 실제 delta는 default pattern constant→sync, power50→30, max75→31, period10→120의
네 항목뿐이고 나머지는 deep-equal입니다. 그중 Sync 출력에 영향을 주는 변경은 pattern·max·period입니다.
offline projection은 binding4·qualified identity/function pair6·ignored0, discovery90 byte-identical,
old-only/new-only/common-changed 모두0, tombstone53·command10·direct Flow3/power3·group1/resume3를
확인했습니다. app/device range30..100은 바꾸지 않았고 세 exact member의 30/31이 그 범위 안입니다.

production prepare-compose가 owner-only 0700 parent 안에 source0444 파일4개를 만들었습니다.
host bind source의 존재·owner·mode·link count1을 확인했고 Compose 교체도 production
`_replace_private_file`을 사용했습니다. backup/runtime의 0600과 source0444를 혼동하지 않았습니다.
임시 배포는 첫 시도 exit0이며 stop 요청 `10:50:52.795176Z`, runtime StartedAt
`10:50:56.021706436Z`입니다. manifest 재생성이 프로세스 시작 이후임을 확인한 뒤 postcheck했습니다.
`10:52:33.643369Z`에는 unarmed, fresh cycle7, device6 online, discovery90 exact,
journal62·receipt18 불변이었습니다. 기동 자체가 group ON이나 Flow write를 의미하지 않습니다.

앱 조작 전에 service를 `10:52:33.778240Z`에 stop 요청해 `10:52:33.938167Z`에 정지했습니다.
그 뒤 baseline fresh read와 `11:04:37.557276Z`부터 `11:04:38.992751Z`의 재독에는 여전히
first40/TimerOFF, second30/TimerON, Barclassic91/TimerOFF가 있어 앱 변경을 다시 요청했습니다.
사용자 완료 회신 뒤 `13:16:27.349057Z`부터 `13:16:29.480392Z`에 세 장비 모두
ON·TimerOFF·constant·independent·Flow30을 확인했습니다.

| 구분 | SHA-256 identity binding (`qualification-target:`) | Frequency |
|---|---|---:|
| 첫 Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` | 34 |
| 두 번째 Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` | 32 |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` | 80 |

Bar PulseTide는 false였습니다. 앱의 Pulse 선택과 Mode 레지스터를 같은 것으로 쓰지 않습니다.
서비스를 `13:20:11.432078312Z`에 재시작했고 runtime manifest
`27425e0da0e06ddb3ff3854ad97716dc`의 rederived_at은 `13:20:12.395222Z`입니다.
readiness·unarmed·서비스 fresh fields 및 별도 authenticated read를 다시 통과한 뒤에만 ON을 보냈습니다.

## 장비가 실제로 돌려준 값과 판정

첫 목표는 실제 epoch를 외부에서 정확히 읽지 못하므로 두 분기를 사전 등록했습니다.
처음30이면 record1은 NO_CHANGE6/rev7/frame0, 이후31·30은 각각 rev13/Flow3입니다.
처음31이면 31·30·31의 세 record가 모두 rev13/Flow3이며 합계 Flow9입니다.
두 경우 모두 power frame0, record3 뒤 stop이고 MQTT pending은 verdict가 아닙니다.

- ON publish: `13:20:29.513927Z`, 한 번. command 관측도 한 번입니다.
- 최초 MQTT 결과: `13:20:31.525186Z`, completion_pending true, ownership_accepted true,
  applied_identity_matches false. 약 2초 시점의 snapshot이며 정상 완료를 대체하지 않습니다.
- 최초 submitted identity는
  `93d01908bb1e7e399b6a035f1c7c5c488a52759123744503724ce2efcab6cdcc`로 보조 관측에만 사용합니다.
  주기적 lifecycle에 같은 identity나 epoch·dedupe counter가 있다고 가정하지 않습니다.

| record | baseline→target | journal created (UTC) | journal updated (UTC) | revision / Flow frame |
|---|---|---|---|---|
| 1 | 30→31 | 13:20:38.923597 | 13:20:41.894827 | 13 / 3 |
| 2 | 31→30 | 13:21:21.427723 | 13:21:24.338492 | 13 / 3 |
| 3 | 30→31 | 13:22:19.881591 | 13:22:24.059978 | 13 / 3 |

각 record는 manual_flow/power ×3의 action6, Flow VERIFIED3·power NO_CHANGE3입니다.
create0 + changed3×save3 + NO_CHANGE3×save1 + terminal1 = revision13입니다.
세 target의 expected Flow는 record마다 동일한31/30/31이고 baseline은30/31/30입니다.
power expected·baseline은 ON입니다. 첫 번째31의 재방문도 새 record로 적용됐습니다.

각 terminal file은 6,386 bytes이며 SHA-256은 순서대로 다음과 같습니다.

1. `4acb800fab6810ae7904c502f552c38200e72565b5d3db3713b3f167f655d1cf`
2. `6da169ca3ab64f445b5ae096f63c25ef25ab680a5365af9d595f7c98002e36ab`
3. `9eb35fcbc6a130bfa8c2929a2235756a3388e3b21a9f85c9a10c97d06aa7472b`

hold에서 fresh cycle2/3/4의 record1 bytes가 같고 cycle5/6/7/8의 record1·2 bytes가 같았습니다.
그 유한 관측창에서는 새 operation이 없었습니다. lifecycle의 disposition 문자열을 dedupe counter로
읽은 것이 아니라 cycle 진행과 record 수·digest를 함께 보존한 근거입니다. 네 번째 record·기존 record
변경·terminal 변경·receipt 변경이나 관측된 lifecycle failure는 없었습니다.

세 번째 terminal 관측은 `13:22:24.086043Z`, host stop 요청은 `13:22:24.096080Z`, 정지 완료는
`13:22:24.388133Z`입니다. 180초 상한 전에 정상 종료했고 재ON·group OFF·recovery는0입니다.
내부 observer와 host의 상한은 관측 기반 stop 규칙이지 추가 frame을 물리적으로 불가능하게 만드는
pre-write gate가 아닙니다. 이번에는 정지 뒤에도 record 증가가 없음을 따로 확인했습니다.
`13:22:30.408536Z`부터 `13:22:32.578471Z`의 별도 fresh read는 세 장비 모두
ON·TimerOFF·constant·independent·Flow31 및 Frequency34/32/80·Bar PulseTidefalse였습니다.

첫 publish는 프로세스 시작 약18.1초 뒤였고 첫 target31은 기동 기준 위상 추정과 부합합니다.
이는 사후 참고이며 monotonic manager epoch와 wall-clock process 시작·실제 plan 시각의 오프셋
상한을 확보하지 않았으므로 판정 근거나 gate로 쓰지 않았습니다. 설정의 60초 반주기가 장비에서
정각60초 간격으로 동시에 적용됐다는 주장도 하지 않습니다.

## Durable inventory와 blob 산술

tree는 각 고정 root에 대해 relative POSIX path와 SHA-256(bytes)의 pair를 정렬하고,
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`의 ASCII JSON으로 해시했습니다.
전후 같은 방법을 썼으며 다른 serialization의 tree와 직접 비교하지 않습니다.

| 항목 | 실행 전 | 실행 후·최종 복귀 |
|---|---:|---:|
| journal operations | 14 | 17 |
| journal blobs | 48 | 55 |
| journal files | 62 | 72 |
| receipt-root files | 18 | 18 |
| unfinished | 0 | 0 |

pre journal tree는 `9626907b1f145327454c7bbdad0d8a697bf6ffaf3a1e2009efb3e32e3d659aeb`,
post/final은 `68a1060b12f8ca50deb5632d293475914e26843c61a42696d1f06143ae737163`입니다.
기존62개 path·bytes는 모두 불변이고 record3 + blob7 = 파일10개만 추가됐습니다.
receipt tree는 전후 `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`입니다.

신규7종은 전부 baseline_status입니다. 세 record의 두 Pro status는 각각452 bytes이며 총6개 신규,
Bar record1의401 bytes는 신규입니다. Bar record2는 기존 blob
`4eb855d8e1e196cfa03208d236fe63f1b4a62d734300a624f2357b6a93d0a690`을 재사용했고,
Bar record3은 record1의
`68571cc105f47a641d2f10e513c65803844806103373630d0b0e30dae7c57d8f`를 재사용했습니다.
이는 이번9회 capture의 구성일 뿐, Pro의 항상 다른 bytes나 Bar의 항상 같은 bytes를 보장하지 않습니다.
가변 필드의 존재·부재를 이 중복만으로 판정하지 않습니다.

Flow30/31의 Pro·Bar payload, canonical readback, power payload/readback, empty ACK의 모든 참조는
기존 blob에 있었습니다. generic coordinator의 intent는 digest만 기록되며 세 intent preimage가 blob
store에 없음을 확인했습니다. 원문 보존과 같은 request의 intent digest에 의한 idempotency는 구분합니다.
empty ACK은 runtime의 sequence 결속된 response 뒤 body가 비었다는 결과이며, 결속 검사 자체의
wire 증거는 보존하지 않았습니다. SHA-256(empty)를 실행별 판별 증거로 사용하지 않습니다.

## 원복 검증 결과와 기존 서비스 복귀

첫 복원 검사 초안은 장비를 읽은 뒤 schedule 일치를 먼저 assert해 실제 값을 출력하지 못했습니다.
이는 device contact0이 아니라 device write0입니다. 진단 재독에서는 값을 출력하면서 불일치도 남겼고,
`13:43:25.020000Z`부터 `13:43:27.091038Z`에는 세 장비가 여전히 Flow31/TimerOFF였습니다.
`13:46:35.237421Z`부터 `13:46:37.562861Z`에는 first40·Barclassic91이 복귀했고,
두 번째 Pro는 TimerON·latent31이었습니다. 사용자가31 유지를 수용한 뒤 다시 검증했습니다.

최종 running fresh read `14:08:28.520782Z`부터 `14:08:30.950993Z`:

| 장비 | 전원 / Timer | Mode / manual Flow | Frequency / PulseTide |
|---|---|---|---|
| 첫 Pro | ON / OFF | constant / 40 | 34 / 해당 비교 없음 |
| 두 번째 Pro | ON / ON | constant / 31 (사용자 수용) | 32 / 해당 비교 없음 |
| Bar | ON / OFF | classic / 91 | 80 / false |

두 번째 Pro는 그 시점 active slot exactly1, valid14/invalid0, AutoMode sine·AutoFlow45가 slot과
일치했습니다. 현재 schedule 소유권과 inactive manual31을 구분하며, manual30 exact 원복이나
전체 schedule image byte 불변은 주장하지 않습니다. TimerON 중 manual 값의 수정이 불가능하다는
앱·프로토콜 일반 규칙도 이 사례로 만들지 않습니다.

prior source4의 bytes·owner·0444 및 parent0700·link count1을 확인하고 preserved image만 사용해
production Compose 교체로 복귀했습니다. 복귀 배포도 첫 시도 exit0, start 요청 `14:08:02.845868Z`,
runtime StartedAt `14:08:04.183823340Z`입니다. restart counter0은 force-recreate 뒤 신규 counter이며
연속성 증거가 아닙니다. 이번 성공으로 이전 D1p 배포 실패의 UNKNOWN을 설명하거나 배제하지 않습니다.

최종 `14:08:22.408242Z` postcheck는 Constant50, binding4, ignored0, discovery90/90 exact,
tombstone53 nonempty0, command10·관측 message0·retained0, direct Flow3/power3, group1/resume3,
unarmed, resume online0, supervisor1·cycle failure0·fresh cycle1, device6/global online, unfinished0입니다.
discovery SHA-256은 `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`로 불변입니다.
final runtime manifest generation `41f81e96c34e7105f478a963b6827bf3`는 prior Compose generation과
다른 개념입니다. journal72·receipt18 tree는 복귀 전후 불변이며 startup write0은 journal-first인
local-control direct/group 경로에 한정합니다. 앱이나 다른 외부 경로의 write0을 주장하지 않습니다.

임시 30..31 목표 선택은 끝났습니다. 복귀한 Constant default.power50은 현재 group ON의
manual Flow 목표50을 뜻합니다. 두 번째 Pro는 TimerON이므로 manual 레지스터와 schedule 출력의
소유권은 별개입니다. 이 복귀 뒤 group ON을 다시 실행하거나 그 조건의 물리 출력을 검증하지 않았습니다.

앱 대기 중 service 정지 완료부터 재기동까지는 약147.6분, 마지막 guard-stop 완료부터 prior runtime
시작까지는 약45.7분입니다. 이들은 펌프 OFF 시간이 아니며 HA availability의 실제 online 발행 시각도
따로입니다. 초기 임시 배포 정지 구간도 별도여서 총 service 부재를 command span으로 축소하지 않습니다.
앱 변경 순간은 보존되지 않았습니다. 저출력 관측13:16과13:43, 복귀 관측13:46을 남기되 그 사이의
연속 운전 시간을 확정하지 않습니다. 준비·복원 대기가 포함된 물리 비용과 약115초 command 관측창은 다릅니다.

## 증거 등급과 보존물

- 증거 등급: (b)는 실제 확인한 journal·referenced blob·immutable bundle·manifest와 inventory의
  persisted claim입니다. broker/Docker/CLI·앱 조작·authenticated fresh read의 해석은 (c)로 구분합니다.
  관측 JSON을 보존했다는 이유로 원본 wire frame 등급(a)로 올리지 않습니다.
- opaque artifact id: `m4-sync-bde65c8d8b99b970f4bc261375ea6b1c`.
- UTC span: 최초 pre inventory `2026-09-13T10:44:00.651287Z`부터 최종 보존물 재독
  `2026-09-13T14:09:40.401156Z`까지이며 각 live/read/deploy 구간은 위에서 분리했습니다.
- identity binding·artifact digest는 위와 아래에 적습니다. 실제 주소·MAC·device id·private 절대경로는
  기록하지 않습니다. 아래는 opaque artifact 안의 논리 파일명입니다.

| 보존물 | bytes | SHA-256 |
|---|---:|---|
| pre-inventory.json | 11697 | `f23c95182105444601a6b3fb9677b2d2c39d50ec405c3128e52be4a7fc31cb4e` |
| sync-bounded-observer-v1.py | 12593 | `21a100e0e854dca86129837bba28ccfb8c9ad7b33e24d696f4e74a7b5b130ed1` |
| sync-command-stream-v1.jsonl | 151028 | `8db6e8d2b8fc536922c4809c3a6ec91ab7f532abf854b45e0aac9ae22eae1045` |
| sync-blob-reconciliation.stdout | 3054 | `1082667fdeff103198a0e7d8aa6ba4bbc6bd0c7f81cba2ca5b5882840a4fc361` |
| sync-post-stop-fresh.stdout | 1712 | `bc0a6cc49126c78bb103445b7253c55e115ac7ae87edae7ff14e614ddcc16045` |
| accepted-app-restore-read.stdout | 1899 | `ff940b12b01940dcb4f8b8b8734b683fd2d06a5a299026b47537b51c99155004` |
| final-prior50-mqtt-audit.stdout | 1255 | `0bd73df500d04b9caa0227285c5fd47d877e35b7bbb436a0aff4b568ebbd466d` |
| final-running-fresh.stdout | 1899 | `664774d67004eee19d1d00b7a1b197620f12a59e0912632f9eeb24da8652e301` |
| final-runtime-manifest.json | 2914 | `c895cbc115c630837dd685933a90af2828e910d5b9be8d3b968357e43efc1bd1` |

stream은 관측한 중간 revision과 lifecycle을 보존하지만 첫 sample은 revision1입니다.
첫 frame 이전 rev0 snapshot을 확보했다고 주장하지 않습니다. journal-first 순서는 기존 구현의 계약입니다.

## 실행 중 정정

- prepare CLI는 exit0이었지만 wrapper가 출력 키 generation을 잘못 참조했습니다. 보존한 결과의
  install_generation을 다시 읽었고 prepare 자체를 재실행하거나 기존 live 입력을 변경하지 않았습니다.
- offline audit의 import namespace 오류는 projection 전에 종료됐습니다. 다음 입력 copy는 temp path가
  거부돼 durable owner-matched copy로 정정했습니다. 둘 다 network-none, device·broker 접촉0입니다.
- 복원 audit의 성급한 assert는 읽은 값을 가렸습니다. 불일치를 PASS로 바꾸지 않고 값과 불일치를
  함께 남기는 진단 재독으로 정리했습니다. 잘못된 출력은 덮어쓰지 않고 각 버전을 보존했습니다.
- 초기 MQTT pending을 terminal failure로 보던 D1q의 assert는 재사용하지 않았습니다.

## 이 실행으로 새로 확정된 사실

- 이 세 physical binding의 manual 소유 상태에서 Sync는 31→30→31의 두 전환을 각 멤버에 적용했고
  rev13×3·Flow9/power0으로 완료됐습니다. 과거31로의 재방문이 잘못 억제되지 않았습니다.
- 같은 목표의 유한 hold에서는 cycle 진행 중에도 record 수·bytes가 불변이었습니다. 세 번째 terminal
  이후 stop과 별도 fresh read로 모두 ON/Flow31임을 확인했고 receipt는 변경하지 않았습니다.
- 신규 baseline status7개와 Bar의 재사용2회로 blob 산술을 확인했고 기존62개 파일은 불변이었습니다.
- 두 번째 Pro의 latent31은 사용자 수용으로 구분했으며 기존 Constant50 서비스로 첫 시도에 복귀했습니다.

## 이 실행으로 확정하지 못한 것

- 첫 target30의 rev7/frame0 분기, 정확한 epoch 원점, 물리적 완전 동시성과 실제 수류량 직접 측정.
- 무기한 deduplication, Sync의 다른 gain·limit·장시간 운전·누락 멤버·실패 시 recovery.
- Anti Phase, group OFF, direct override/resume 실기, native Q1/Q2, HA registry 상태.
- 원래 manual30의 exact 복원, 전체 raw status·schedule image byte 불변, 향후 update/reboot 뒤 동작.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

이 기록을 먼저 commit하고 다음 M4 operation은 별도 계획으로 다룹니다. 두 번째 Pro의 다음 manual
baseline은 사용자가 수용한31이며 Timer와 그 시점 schedule 소유권을 다시 읽습니다. Sync 상한은
default.power가 아닌 min/max에서 계산하고 Constant의 operation 증가 시 stop 규칙을 주기적 전환에
그대로 쓰지 않습니다. 관측 wrapper는 값을 먼저 보존한 뒤 판정해 실패해도 실측값을 가리지 않습니다.
복구·감사는 production의 생성·소유권·permission·serialization 계약을 재사용하고, 관측을 넘어서는
일반 규칙을 만들지 않습니다.
