# run: 2026-09-14-local-group-antiphase-commissioning

- 시간 기준: 파일명은 ON 실행의 KST 날짜이며 본문 timestamp는 UTC입니다. 준비는 KST 9월13일,
  ON과 복귀는 자정을 지난 9월14일입니다. 아래 UTC 시각은 모두 2026-09-13입니다.
- 목적(한 문장): 기존 member phase0/180/90의 software-independent Anti Phase가 서로 다른
  목표 벡터를 네 번 전환해 처음 벡터로 돌아오고 bounded stop으로 끝나는지 확인합니다.
- 이 실행이 답하려던 질문: 각 멤버의 exact scalar readback이 순환 벡터와 일치하고,
  같은 목표의 유한 hold에서는 cycle이 진행해도 새 operation이 없는가.
- 사용자 경계: repository maintainer이자 on-site approver인 사용자가 수조를 보며 진행을 승인하고
  앱 준비·복원과 물리 중단 수단을 담당했습니다. 임시 배포, MQTT ON 한 번, 네 전환 관측, service
  stop, 앱 복원과 exact prior Constant50 복귀만 실행했습니다. native Linkage 실기가 아닙니다.
  앱 준비는 첫 Pro40→31의9%p, Barclassic91→constant31의60%p 하향과 파형 변경입니다.
  두 번째 Pro도 TimerOFF로 schedule 소유권을 해제하므로 실제 운전이 바뀝니다. 조작 전 fresh
  active sine45를 읽어45→31의14%p 변화를 알렸고, 그 값을 복원 시점까지 고정하지 않았습니다.
  서로 다른 목표를 주는 이번 실기의1%p 범위를 수조에 무조건 무해하다고 일반화하지 않습니다.
- 실행한 commit SHA: 정책 `fe3b0d1cee981755fc574131abb579ffdfaac453`.
  source/tests·동결 대상은 무수정이며 재빌드하지 않았습니다. 사용한 exact image는
  `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`입니다.
- 사용한 명령·주요 파라미터: 기존 prepare-compose·stage·serve 및 MQTT group ON,
  QoS0·retainfalse·publish1. Anti Phase min30/max31·period120초·transition0·gain1입니다.
  default.power50은 그대로이며 AntiPhasePattern은 이 값을 읽지 않습니다. phase·gain·invert·role도
  바꾸지 않았고 app/device capability30..100·qualification·HA schema에 새 gate를 넣지 않았습니다.
- 종료 지점(stage / failure code): VERIFIED5, revision11/9/11/9/11, Flow frame8, power frame0.
  다섯 번째 terminal 직후 service stop, observer exit0, 별도 fresh read와 prior 서비스 복귀 완료.

## 배포 입력과 준비

prior Compose generation `2b4d91a3a662ac2aa85bacf91a03f72b`, Compose SHA-256
`9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`, bundle4 tree
`4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`를 보존했습니다.
임시 generation은 `4ba73cbc05a40f01a4c621f5d2001c40`, Compose digest는
`f05b10df41e04c5fe18c351991f955c8da39da80e6f35edc8468de872e16f94d`, bundle4 tree는
`54d0896028ecf2876358891ca69aa1b58f2d652a1f106fe95e2f324d4efac0aa`입니다.
qualification input은2429 bytes,
`b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033` 그대로입니다.

config delta는 default.pattern constant→anti_phase, max75→31, period10→120 세 항목입니다.
min30·power50 및 members는 불변입니다. 실제 config를 manifest physical anchor로 역해석해
secondPro0°·firstPro180°·Bar90°를 확인했습니다. invertfalse는 이 pattern의 위상 반전 수단이 아닙니다.
기존 image·network-none 계산기로0/29/30/59/60/89/90/119/120초의 네 벡터와 재방문을 확인했습니다.
offline projection은 binding4·authority receipt12·qualified identity/function pair6·ignored0,
discovery90 byte-identical·old/new/common-changed0·tombstone53·command10·direct Flow3/power3·
group1/resume3였습니다. source4는 production 생성 경로의 owner-matched0444/parent0700/link1입니다.
backup0600과 source metadata를 혼동하지 않았고 Compose 교체도 production 함수를 사용했습니다.

임시 배포는 첫 시도 exit0입니다. stop 요청14:39:32.214601Z, 정지14:39:32.362678Z,
StartedAt14:39:34.642801746Z, runtime manifest `87cb07effb113ee8a19725caf2223898`의
재생성14:39:35.560664Z를 확인한 뒤 postcheck했습니다.14:40:25.477082Z에 unarmed·fresh cycle4·
failure0·fresh6·discovery90 exact·journal72/receipt18 불변이었습니다.
앱 조작 전14:40:58.199146Z에 stop을 요청해14:40:58.408291Z에 정지했습니다.
14:41:04.566275Z–14:41:07.306537Z의 baseline과14:49:46–48Z의 첫 precondition 재독은
아직 원 운전 설정이었습니다. 후자를 준비 성공으로 처리하지 않았습니다.
사용자 완료 회신 뒤14:57:15.703985Z–14:57:17.155725Z에 세 장비 모두
ON·TimerOFF·constant·independent·Flow31을 확인했습니다.

| 장비 | SHA-256 identity binding (`qualification-target:`) | Frequency |
|---|---|---:|
| 첫 Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` | 34 |
| 두 번째 Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` | 32 |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` | 80 |

Bar PulseTide는 false입니다. 이를 Mode와 합쳐 쓰지 않습니다.15:02:12.023674808Z에 서비스를
재기동했고 runtime manifest `456320c1433423e87b7e2f303bfde46d`의 재생성은15:02:12.985769Z입니다.
readiness·unarmed·서비스 fresh state와 별도 authenticated read15:02:27.346462Z–15:02:29.589026Z를
다시 통과한 뒤에만 ON을 보냈습니다. 관측 도구는 production exclusive lock을 별도로 취득하지 않았습니다.

## 장비가 실제로 돌려준 값과 판정

멤버 순서는 (두 번째 Pro, 첫 Pro, Bar)입니다. 사전 등록한 벡터는 A=(31,30,30), B=(31,30,31),
C=(30,31,31), D=(30,31,30), 순서는 A→B→C→D→A입니다. 최초 A/D이면 변경2/rev11,
B/C이면 변경1/rev9이고 이후 네 전환의 Flow frame 합은6입니다. 총 frame은8 또는7, power0입니다.
첫 baseline은(31,31,31), 이후 baseline은 직전 terminal target입니다.

- ON publish_intent15:02:29.607007Z, published15:02:29.607598Z, publish와 관측 command 각1회.
- 최초 result15:02:31.614217Z는 completion_pending true·ownership_accepted true·
  applied_identity_matches false였습니다. 약2초 시점 snapshot이지 terminal verdict가 아닙니다.
  submitted identity `ebfb70f347dd14ee68db31a6efb6603a7b24b40ac879eb1d097165198e4f279c`는 보조 관측입니다.
  주기적 lifecycle에 epoch나 내부 dedupe counter가 있다고 가정하지 않았습니다.

| record | target / index | journal created UTC | journal updated UTC | Flow / NO_CHANGE / rev |
|---|---|---|---|---|
| 1 | (31,30,30) / 0 | 15:02:39.019442 | 15:02:41.291919 | 2 / 4 / 11 |
| 2 | (31,30,31) / 1 | 15:02:52.547516 | 15:02:52.848368 | 1 / 5 / 9 |
| 3 | (30,31,31) / 2 | 15:03:23.145531 | 15:03:25.917826 | 2 / 4 / 11 |
| 4 | (30,31,30) / 3 | 15:03:54.998617 | 15:03:55.355568 | 1 / 5 / 9 |
| 5 | (31,30,30) / 0 | 15:04:25.267242 | 15:04:28.067513 | 2 / 4 / 11 |

전부 action6(manual_flow/power ×3), power NO_CHANGE3이며 실제 Flow 변경만 VERIFIED입니다.
changed Flow 수를 c라 하면 create0 + c×save3 + (6−c)×save1 + terminal1 = revision7+2c입니다.
변경 멤버 집합은 first/Bar, Bar, second/first, Bar, second/first입니다. index0→1→2→3→0와
각 record의 집합을 assertion 전에 stream에 남겼고, 첫 record부터 VECTORS 소속을 확인했습니다.
각 terminal file의 bytes와 digest는 다음과 같습니다.

| record | bytes | SHA-256 |
|---|---:|---|
| 1 | 6353 | `2a662afd48f0152fda1db9efbd0e17c391464a485637fdb4ce6782bddc4f312c` |
| 2 | 6319 | `11ca559f9be92179e7e88bf52a7874f1c740a6f1480859eb5c90914b99ca0858` |
| 3 | 6353 | `784b4138a468c080ba70706ce5fa8bb87a6d219b077155be59b9ffbb9ed2e11a` |
| 4 | 6319 | `dd0437b32d96a64870a3f9d7e5e4b463ad1e74674ba4b3205dd27db953e1e2f3` |
| 5 | 6353 | `8196416813e021e5247ecbbb3915688ce16b93997ca8dd580b6c9ba4492a819d` |

hold pair는 fresh cycle3/4에서 record2까지,5/6에서 record3까지,7/8에서 record4까지의 개수와 bytes가
동일했습니다. 이 유한 관측을 무기한 dedup 보장으로 승격하지 않습니다. 여섯 번째 record·기존 record
변경·terminal 변경·receipt 변경·관측된 lifecycle failure는0입니다. 처리 간격30초를 gate로 삼지 않고
벡터 순서·개수·frame으로 판정했습니다. 최초 publish는 process 시작 약17.6초 뒤로A와 부합하지만
monotonic epoch와의 offset 상한을 확보하지 않아 판정 근거가 아닙니다. 물리적 동시성·정확한 위상차·
실측 수류량은 미검증입니다.

다섯 번째 terminal 관측15:04:28.111127Z, stop 요청15:04:28.117601Z, 정지 완료15:04:28.304559Z, exit0.
publish부터 terminal 관측까지 약118.5초입니다. 내부·host 독립180초는 관측 상한이지 완주 보장이나
추가 frame을 물리적으로 불가능하게 하는 gate가 아닙니다. 재ON·group OFF·recovery는0입니다.
정지 뒤15:04:44.449398Z–15:04:46.502062Z의 fresh read는 second31/first30/Bar30으로 terminal과
일치했고 전부 ON·TimerOFF·constant·independent, Frequency32/34/80·Bar PulseTidefalse도 불변이었습니다.

## Durable inventory와 blob 산술

고정 root의 relative POSIX path와 SHA-256(bytes)의 pair를 정렬하고 sort_keys=True,
separators=(",", ":"), ensure_ascii=True의 ASCII JSON을 hash하는 같은 방법을 전후에 사용했습니다.

| 항목 | 실행 전 | 실행 후·최종 복귀 |
|---|---:|---:|
| operations | 17 | 22 |
| blobs | 55 | 64 |
| journal files | 72 | 86 |
| receipt-root files | 18 | 18 |
| unfinished | 0 | 0 |

pre journal `68a1060b12f8ca50deb5632d293475914e26843c61a42696d1f06143ae737163`, post/final
`d0bcad6238fd2e45e84ec1112a1547b303a21f8019f3cadfb57982e6a8929b06`.
기존72 path/bytes는 모두 불변이고 추가14=record5+blob9입니다. receipt tree는
`9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`로 불변입니다.

baseline_status capture15회 중9개가 신규이며 전부 Pro의452 bytes입니다. 재사용6회는 다음과 같습니다.

- Bar record1/3/4: pre blob `4eb855d8e1e196cfa03208d236fe63f1b4a62d734300a624f2357b6a93d0a690`.
- Bar record2/5: pre blob `68571cc105f47a641d2f10e513c65803844806103373630d0b0e30dae7c57d8f`.
- second Pro record2: record1의 `7d5d2a3d9d98e742bed4ad73f01c560a6873be6d2e8271238fb1e8071b376d38`.

비status payload·readback·ACK 참조는 모두 pre blob에 있고 각 참조 bytes와 SHA를 검증했습니다.
generic coordinator의 intent preimage5건은 blob store에 없으며 digest의 idempotency 결속과
원문 보존을 구분합니다. empty ACK은 runtime에서 sequence 결속된 response의 body가 빈 결과입니다.
wire 결속 검사 자체를 보존했다고 하지 않고 SHA-256(empty)를 실행별 판별 증거로 쓰지 않습니다.
D1r에서는 Pro capture6건이 모두 신규였지만 이번에는 second Pro에 재사용이 있어 구성이 다릅니다.
특히 second의 baseline Flow31인 record1/2는 같은 bytes이고 record3은 신규입니다. 같은 장비의
같은 Flow에서도 결과가 달랐으며 원인은 확정하지 않았습니다. Pro는 항상 다르다거나 Bar는 항상
같다거나 가변 field가 없다는 일반 규칙을 만들지 않습니다.

## 원복 검증 결과와 기존 서비스 복귀

사용자 복원 완료 뒤15:08:30.670503Z–15:08:32.158060Z에 선언한 decoded field 집합의 복귀를 확인했습니다.
시작A 분기라 second는 이미 manual31이었고 추가 latent 조정이나 변경 수용은 필요 없었습니다.

| 장비 | power / Timer | Mode / manual Flow | Frequency / PulseTide |
|---|---|---|---|
| first Pro | ON / OFF | constant / 40 | 34 / 해당 비교 없음 |
| second Pro | ON / ON | constant / 31 | 32 / 해당 비교 없음 |
| Bar | ON / OFF | classic / 91 | 80 / false |

second의 현재 active slot은 sine/40, exactly1·valid14/invalid0이며 AutoMode/AutoFlow가 일치했습니다.
복원 확인은 그 시점 slot 기준이고 준비 때 sine45를 목표로 쓰지 않았습니다. TimerON에서 schedule이
소유하는 출력과 latent manual31을 구분하고 raw status·schedule image 전체의 byte-exact 원복은 주장하지 않습니다.

prior bundle4의 bytes·owner·0444·parent0700·link1을 확인하고 exact image 그대로 production
Compose 교체로 복귀했습니다. 첫 시도 exit0, start 요청15:09:12.664643Z, StartedAt
15:09:14.0141882Z입니다. restart_count0은 force-recreate 뒤 신규 counter로 연속성 증거가 아닙니다.
이번 성공으로 이전 D1p UNKNOWN의 원인을 설명하거나 배제하지 않습니다. final runtime manifest
`9c9a1a912b790af5c3f6d5b9ece1308e`의 재생성15:09:14.997529Z와 prior Compose generation은 별개 개념입니다.

15:09:31.313939Z postcheck는 discovery90/90 exact, digest
`b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`, tombstone53/nonempty0,
command10/message0/retained0, direct Flow3/power3·group1/resume3, unarmed·resume online0,
supervisor1/cycle failure0/fresh cycle1, device6/global online, binding4/ignored0/unfinished0였습니다.
최종 fresh15:09:37.566570Z–15:09:39.024141Z에서도 위 복원값과 현재 slot 일치를 재확인했습니다.
journal86·receipt18은 복귀 전후 불변이어서 startup write0을 확인했습니다. 이는 journal-first인
local-control direct/group 경로에 한정하고 사용자 앱이나 외부 경로의 write0을 뜻하지 않습니다.

임시30..31 설정은 끝나고 default Constant/power50/max75/period10으로 돌아왔습니다. 다음 group ON의
manual 목표는50이지만 복귀 자체가 ON이나 재전송은 아닙니다. TimerON인 second에 대한 물리 작용도
이번 복귀로 검증하지 않았습니다. HA registry나 전 구간의 broker 연속 관측도 주장하지 않습니다.
앱 대기 정지14:40:58.408291Z부터 재기동15:02:12.023674808Z까지 약21.2분, 마지막 stop부터
prior runtime 시작까지 약4.8분입니다. 펌프 OFF 시간이 아니며 HA online 실제 발행 시각도 별도입니다.
초기 배포 정지도 별도이므로 총 service 부재를 약118.5초 command 관측창으로 축소하지 않습니다.
앱 변경·복원 순간은 미보존이며 저출력 관측14:57/15:04와 복귀 관측15:08 사이를 연속 운전 시간의
확정 하한으로 삼지 않습니다. 준비·복원 대기의 물리 비용은 command span만으로 나타낼 수 없습니다.

## 증거 등급과 보존물

- 증거 등급: (b)는 실재·digest를 확인한 journal·referenced blob·immutable bundle·manifest·inventory의
  persisted claim입니다. Docker/CLI/broker·앱 조작·authenticated read의 해석은(c)입니다.
  관측 JSON을 보존했다는 이유만으로 원본 wire frame의(a)로 올리지 않습니다.
- opaque artifact id: `m4-antiphase-d08f9fd09c902f515dbccc4cf4155611`.
- UTC span: pre inventory `2026-09-13T14:34:13.900949Z`부터 보존물 재독
  `2026-09-13T15:11:28.316980Z`까지. 각 live/read/deploy 시각은 위에서 분리했습니다.
- identity binding·artifact digest는 위와 아래에 기재합니다. MAC 등 원문이나 private 절대경로는 적지 않습니다.
  아래는 opaque artifact 안의 논리명입니다.

| 보존물 | bytes | SHA-256 |
|---|---:|---|
| pre-inventory.json | 13271 | `ce2f88dcba96d8ba4fb435ae5064f8063a7d1e652b8034ea9983cc53079a439a` |
| anti-bounded-observer-v2.py | 14166 | `e6ece5218c2e11ccd956623cb1ed29b657894f289796aaf709a500e9c6bacf75` |
| anti-execution-host-v2.py | 7672 | `48f328aaab1c6e8e60f5e4cb5dd81cef160aaf90ba3d5e6589b130ff8eef7e25` |
| anti-command-stream-v1.jsonl | 144740 | `86bb8b943dd6e5eed091b6dc3635ca528c42402be80faa16087e0eba86d6bbd0` |
| anti-execution-summary-v1.json | 3139 | `0a212519afeaf03a16010376b4b4a98b4bbd75b839dcb36a6d83578afa71df7b` |
| anti-blob-reconciliation-v2.stdout | 4320 | `7ca13ba3ac9ac7d4d48878c0d7d28fe9039c34502d1f6699abdaf9604c8a5eb2` |
| anti-post-stop-fresh.stdout | 1627 | `9d0548dbd0c5fe501357bacc3936c746f198965f9e16eea7ac2ba4c59121fad2` |
| app-restore-read-01.stdout | 2737 | `a6b64df2f4363191f5167fa2eaf4ca6dc6a30779c2ca90a0157ae81e957648eb` |
| final-prior50-mqtt-audit.stdout | 1255 | `32c500a95b6a4ef28fb2e3edd8e66aed91f595a1aef1b8ee87f07d6dfb218865` |
| final-running-fresh.stdout | 2737 | `b86a70da1704022feb25c63139c2f98f13be0ba918aab960d76727cbbdbb76e8` |
| final-runtime-manifest.json | 2914 | `d02f513459b42061527fa158a95aecdcbd7a42bbed685bf4d6e2c36a0d021b3b` |
| anti-prior-return-host.py | 20385 | `f3940ee8d5708cc7b1e19e307793c99c978e02204926494b1c5b49edf43c610f` |

첫 record sample은 revision3입니다. 첫 frame 전 rev0 보존은 주장하지 않으며 journal-first는 기존
구현 계약에 의존합니다. record2의 rev0 sample이 있어도 epoch 첫 frame 전 증거로 바꾸어 쓰지 않습니다.
observer/host v2와 stream v1은 코드 버전과 단회 실행 번호의 차이이며 ON을 두 번 보낸 뜻이 아닙니다.

## 실행 중 정정

- 첫 prepare wrapper는 identity FILE만 전달하고 VALUE env가 빠져 manifest/Compose 전에 끝났습니다.
  진단·실패 출력을 남기고 보존한 identity의 value env를 올바르게 전달해 준비했습니다. device/broker 접촉0.
- offline receipt copy의 첫 mount는 image parent permission 때문에 host UID가 통과하지 못했습니다.
  owner-matched root 전체의 read-only mount로 정정하고 receipt bytes 불변을 확인했습니다. 접촉0.
- 실행 observer 준비판v1에서v2로 기존 전체 record의 vector membership 검사를 유지하면서
  assertion 전 index/changed-member 진단을 추가했습니다. v1의 physical 실행은 없습니다.
- 실기 후 첫 blob 감사도 host UID로 production volume을 읽으려 해 PermissionError로 끝났습니다.
  stderr를 보존하고 permission 변경 없이 runtime UID의 읽기 전용 처리에 pre inventory를 stdin으로
  전달해 정정했습니다. device/broker 접촉과 device write는0이고 journal/receipt를 변경하지 않았습니다.
- 복원 감사는 decoded 값을 먼저 출력한 뒤 판정했습니다. D1q의 pending assert나 D1r의 값을 가리는
  premature assert를 재사용하지 않았습니다. 실패 자료는 덮어쓰지 않고 별도로 보존했습니다.

## 이 실행으로 새로 확정된 사실

- 이 세 physical binding의 manual 소유 상태에서 Anti Phase의 A→B→C→D→A를 확인했습니다.
  기존 phase0/180/90의 다른 target이5 record·Flow8/power0으로 적용되고 첫 벡터로 재방문했습니다.
- 유한 hold 세 쌍에서 cycle 진행과 record 개수/bytes 불변을 함께 관측했고 다섯 번째 terminal 뒤
  stop과 fresh read로 최종 벡터를 확인했습니다. 선언한 field 집합과 prior 서비스로 복귀했습니다.
- 신규 baseline_status9개·재사용6회·기존72 file 불변으로 산술을 닫았습니다. Pro 재사용도 실측돼
  D1r만으로 항상 신규라는 규칙을 만들 수 없다는 구체적 사례를 확인했습니다.

## 이 실행으로 확정하지 못한 것

- 시작B/C/D 분기, 다른 gain/phase/limit, 무기한 동작·dedup, 누락 멤버나 실패 시 recovery.
- 정확한 epoch 원점·물리적 완전 동시성·위상차·실측 수류량, device command 빈도 제한의 실효성.
- group OFF, direct override/resume, 다른 physical workflow와 실제 경합, native Q1/Q2.
- 전체 raw status/schedule image byte-exact 원복, HA registry, 향후 update/reboot 뒤 동작.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

이 기록을 먼저 commit하고 다음 M4 조작은 별도 계획으로 다룹니다. second의 manual baseline은31 그대로지만
Timer와 그 시점 schedule 소유권은 다시 확인합니다. 패턴 이름으로 member grouping을 추측하지 않고
실제 config의 phase·gain과 calculator로 target을 확정합니다. 짧은 record 간격을 실패로 단정하지 않고
누락 벡터를 성공으로 바꾸지 않습니다. 감사는 runtime 접근 조건을 존중하고 값을 먼저 보존하며 유한 관측을
일반 규칙으로 만들지 않습니다. M4 전체 완료나 native ASYNC의 답으로 승격하지 않습니다.
