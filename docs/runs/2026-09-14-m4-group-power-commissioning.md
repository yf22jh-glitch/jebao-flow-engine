# run: 2026-09-14-m4-group-power-commissioning

- 시간 기준: 파일명은 KST 날짜이고 아래 timestamp는 UTC입니다. 준비·실행·복귀는 모두
  UTC 및 KST 2026-09-14 범위입니다.
- 목적(한 문장): 세 wavemaker의 canonical OFF 뒤, 꺼진 상태에서 Flow31→30을 먼저 쓰고
  power ON을 보내는 기존 group 경로를 단회 관측합니다.
- 이 실행이 답하려던 질문: all31/TimerOFF/constant baseline에서 OFF power-only와
  ON Flow-before-power가 세 멤버 모두 완료되고 후속 세 fresh cycle 동안 유지되는가.
- 사용자 경계: 사용자는 세 수류모터가 함께 꺼지는 구간, 실패 시 전부 또는 일부가 OFF로 남을
  가능성, 즉시 현장 복원 필요를 고지받고 진행을 승인했습니다. 리턴·도징은 조작하지 않았습니다.
  임시 배포·OFF1·ON1·마지막 nonterminal recovery·사용자 앱 복원·prior 배포 복귀가 범위입니다.
  OFF-state Flow 쓰기는 새 실패 지점이며 위험이 늘지 않는다고 주장하지 않았습니다.
- 실행한 commit SHA: 정책 `9d49c4037e3af79b22ed47406238f30fc3f4cab1`, 실행 시 HEAD
  `ccff7828449fb3ef447bf92aec999d4ce0eeba73`. source/tests·동결 ASYNC 코드는 무수정입니다.
  exact image는 `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`이며
  재빌드하지 않았습니다.
- 사용한 명령·주요 파라미터: 기존 prepare-compose/stage/serve, MQTT QoS0·retainfalse OFF1/ON1,
  기존 `jebao-flow-local-qualify recover --write-enabled`로 마지막 operation만 복구했습니다.
  일반 local recover, 추가 direct ON, 명령 재시도, fault injection은 실행하지 않았습니다.
- 종료 지점(stage / failure code): **FAIL — ON 중 Bar manual_flow가 UNCERTAIN**,
  `forward_outcome_uncertain`, revision14/RECOVERY_REQUIRED. OFF는 revision10/VERIFIED입니다.
  별도 recovery는 revision25/ROLLED_BACK·write4로 완료했고 앱 복원·prior 복귀를 확인했습니다.
  성공 예상 revision19와 post-ON 세 cycle은 달성하지 않았습니다. 복구 성공은 시험 PASS가 아닙니다.

## 준비와 배포

임시 변경은 Constant default.power50→30 하나입니다. min30/max75/period10/transition0,
member phase(second0/first180/Bar90), gain1·invertfalse·role·qualification은 유지했습니다.
baseline all31과 다른 목표30을 택해 ON의 Flow를 NO_CHANGE로 건너뛰지 않도록 했습니다.
이는 이 operation의 목표 선택이지 capability나 HA range의 새 gate가 아닙니다.

prior Compose generation `2b4d91a3a662ac2aa85bacf91a03f72b`, Compose digest
`9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`, bundle4 tree
`4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`를 보존했습니다.
임시 generation `40719c731927211f72cfa1dc59cce59c`, Compose digest
`ebaaa311e819ee052b2d8df5db72e28993144cdbb491975dbeb3309e9c9ac830`, bundle4 tree
`3ed13c4f657322bff16a3e799a01282cebf3710b407d2442993be47aa2f1cc35`입니다.
production 생성 source4의 owner-matched0444/parent0700/link1을 확인했습니다.

offline 계산은 선택한 시점0/9/10/60/120/180에서 세 target30입니다. 새 projection은 기존과
discovery90 byte-identical, old/new/common-changed0, tombstone53/command10/direct Flow3·power3/
group1/resume3/ignored0입니다. offline hardware copy의 receipt18 canonical digest도 대조했습니다.

첫 prepare CLI는 generic `current_operation_failed_closed`로 끝났습니다. candidate manifest는
생겼으나 Compose와 generation directory는 없었습니다. 동일 준비 환경에서 build context resolve가
실패하는 진단과, 빠진 read-only build-context mount를 추가한 뒤 성공하는 진단을 따로 보존했습니다.
원 manifest·실패 출력을 덮지 않고 새 manifest-v2로 prepare를 완료했습니다. 이 정정은 offline
준비에 한정되며 당시 live input·배포·device command는0입니다. 과거 D1p UNKNOWN의 원인으로
소급 적용하지 않습니다.

임시 배포는 첫 시도 exit0입니다. 01:25:53.313652 stop 요청, 01:26:01.471409 정지 반환,
StartedAt01:26:16.817228954Z입니다. manifest `59d05fe25ee244320ea70342cdcc5250`의
재생성01:26:18.157585 뒤 readiness, 01:26:48.560896의 unarmed·fresh6·failure0·command0·
discovery90 exact와 journal98/receipt18 불변을 확인했습니다. startup write0은 journal-first
local-control 경로에 한정합니다.

앱 조작 전01:27:03.316832 stop 요청→01:27:06.562494 정지 뒤 fresh baseline을 읽었습니다.
first40/TimerOFF/Frequency34, second latent31/TimerON/Frequency32 및 그 시점 sine50 schedule,
Barclassic91/TimerOFF/Frequency80/PulseTidefalse, 모두 ON/independent였습니다.
앱 준비는 first40→31의9%p, Bar91→31의60%p와 파형 변경, second의 관측 schedule50→manual31
소유권 전환19%p로 안내했습니다. 실제 앱 조작 시각·전송 수는 보존하지 않았습니다.
01:30:09–10 fresh read에서 세 대 ON/TimerOFF/constant/independent/31과 위 extra fields를 확인했습니다.

## 장비가 실제로 돌려준 값과 실패

identity는 `sha256("qualification-target:" + physical_identity_key)`로만 결속합니다.

| 역할 | anchor |
|---|---|
| first Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` |
| second Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` |

재기동 StartedAt01:33:09.201078943Z, manifest `290f97f13d851451190d2880fbe05b10`
재생성01:33:10.361628 뒤 readiness/unarmed/current qualification을 재확인했습니다.
독립 preflight01:33:24–27도 세 대 all31/TimerOFF/ON과 선언 field를 확인했습니다.
group action 순서는 second→first→Bar이며, ON은 각 멤버의 Flow 다음 power입니다.
동시 전송·동시 기동이 아님을 사용자에게 알렸습니다.

| 단계 | 사전 등록 | 실측 |
|---|---|---|
| OFF1 | power3, action3, rev10 | 세 action VERIFIED·rev10, Flow action0 |
| ON1 | Flow3+power3, action6, rev19 | Pro 네 action VERIFIED, Bar Flow UNCERTAIN, Bar power PREPARED; rev14 |
| ON 이후 hold | complete fresh cycle3 | 미도달; FAIL로 정지 |

OFF publish intent01:33:27.158286, record `group_3a8932ab906c172236c92540cc397e6c`의
created01:33:36.766370→VERIFIED01:33:41.420095입니다. OFF 뒤 불필요한 hold 없이
독립 read01:33:46.534557–01:33:48.229106에서 세 대 OFF/Flow31, TimerOFF/constant/
independent/Frequency34·32·80 및 Bar PulseTidefalse를 확인했습니다.

ON publish intent01:33:48.238647, record `group_7144de92a67f8e3c106f71725dacbd17`의
created01:33:55.626180→RECOVERY_REQUIRED01:34:06.220774입니다.

| ON action 순서 | 실패 시 phase | 의미 |
|---|---|---|
| second Flow31→30 | VERIFIED | OFF baseline에서 Flow write·readback 완료 |
| second power OFF→ON | VERIFIED | ON readback 완료 |
| first Flow31→30 | VERIFIED | OFF baseline에서 Flow write·readback 완료 |
| first power OFF→ON | VERIFIED | ON readback 완료 |
| Bar Flow31→30 | UNCERTAIN | `forward_outcome_uncertain`, ACK artifact 없음 |
| Bar power OFF→ON | PREPARED | forward 미시도 |

revision은 네 changed action×3 save + Bar Flow ATTEMPTING/UNCERTAIN2 save =14입니다.
UNCERTAIN 저장 자체가 RECOVERY_REQUIRED를 기록하므로 별도 성공 terminal save는 없습니다.
diagnostic은 `send_once()`가 예외를 올린 범위를 가리킵니다. readback mismatch나 readback
unavailable이 아닙니다. 그러나 그 원래 예외가 보존되지 않아 네트워크·펌웨어·기타 원인을
구분하지 못합니다. 보존한 runtime stdout/stderr는 둘 다0 bytes입니다. ACK 부재는 장비가
frame을 받지 않았다는 증거가 아니며, 뒤 Flow31 관측만으로 순간 적용 여부도 단정하지 않습니다.

observer는01:34:06.321712에 FAIL stop을 냈고 host는01:34:06.326339 요청→06.680129에
서비스 정지를 확인했습니다. 처음 OFF intent부터 host stop 요청까지 약39.17초로180초 관측 상한 안입니다.
내부/host 상한은 물리 OFF 체류 보장이 아니며, 만료 시 자동 ON도 없습니다.
host는 상한 만료 시 observer 종료 후 서비스를 멈추도록 준비했으나 이번에는 그 분기가 실행되지
않았습니다. MQTT pending은 보조 snapshot으로만 보존했고 verdict assert에 쓰지 않았습니다.

01:34:42–44의 독립 fresh read는 **Bar OFF/31, 두 Pro ON/30**의 혼합 상태를 확인했습니다.
Frequency34/32/80·PulseTidefalse는 유지됐습니다. 사용자에게 상태와 복구의 재-OFF 효과를
알리고, 앱 복원보다 승인된 마지막 nonterminal recovery를 먼저 수행했습니다.

## 실제 recovery와 사용자 복원

01:35:47.830067 recovery intent를 보존하고 config-only qualify recovery를1회 실행했습니다.
OFF VERIFIED record는 건드리지 않았습니다. 역순 처리 결과는 다음과 같습니다.

| 역순 action | 결과 | 복구 전송 |
|---|---|---:|
| Bar power PREPARED | `forward_never_attempted` | 0 |
| Bar Flow UNCERTAIN | fresh31 = baseline31, `baseline_already_present` | 0 |
| first power·Flow | ON→OFF, 30→31 | 2 |
| second power·Flow | ON→OFF, 30→31 | 2 |

복구는 skip/classify 각1 save + 실제 복구4×2 save + terminal1 =11 save를 더해
revision25/ROLLED_BACK01:36:03.304284, CLI exit0·physical_write_count4로 끝났습니다.
01:36:09–11 fresh read에서 세 대 OFF/31과 extra fields를 확인했습니다. 이 읽기의
`all_admitted=false`는 시험 종료 목표 ON/30과 다르다는 뜻이며 recovery 실패 판정이 아닙니다.
ROLLED_BACK만으로 원 운전 복원을 주장하지 않았습니다.

전송 회계는 forward attempted8 중 ACK·VERIFIED7과 UNCERTAIN1, recovery confirmed4입니다.
합쳐 확인된 회계는11(Flow4/power7)이고 Bar Flow uncertain attempt1은 별도입니다.
이를 정확한 전체 physical frame12라고 단정하지 않습니다. 복구가 Pro를 OFF로 만든 뒤의
Flow30→31 두 건까지 포함하면 두 Pro에서 OFF-state Flow 성공 사례4건이 있습니다.
이것을 Bar의 같은 기능 PASS나 모든 OFF-state Flow 쓰기 불가로 일반화하지 않습니다.

사용자 앱 복원 후01:47:37–39에는 모두 ON이고 second31/TimerON·그 시점 sine50 slot 일치는
확인됐지만 first31·Barconstant31이 남았습니다. 이 상태를 복원 완료로 부르지 않고 두 설정을
다시 요청했습니다. 01:50:43–44의 두 번째 read에서 first40/TimerOFF, second31/TimerON,
Barclassic91/TimerOFF와 Frequency34/32/80·PulseTidefalse를 모두 확인했습니다.
second는 그 시점 active slot exact-one·valid14/invalid0·AutoMode/AutoFlow 일치이며,
과거 slot값을 복원 목표로 고정하지 않았습니다. latent31은 기존 수용 baseline 그대로입니다.

prior Constant50 복귀는 source0444/parent0700 및 보존한 exact image·Compose·bundle로,
재빌드 없이 첫 시도 exit0입니다. StartedAt01:51:12.062472495Z, manifest
`d1a319557c6d7b133381ff9d6aa37195` 재생성01:51:13.050823 뒤 readiness를 확인했습니다.
01:51:30.899323 broker audit은 discovery90 exact·tombstone53·command10/messages0/retained0,
direct3+3/group1/resume3 offline, unarmed·supervisor1·failure0·fresh6·ignored0·unfinished0입니다.
discovery digest는 기존 `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`
그대로입니다. 01:51:36–38 별도 fresh read에서도 위 선언 field와 current sine50 slot이 일치합니다.
기존 default50 복귀는 장비에 Flow50을 보냈다는 뜻이 아니며 group은 비무장입니다.

## 시간과 물리 영향의 한계

정상 ON 성공 terminal이 없으므로 성공 시 계획했던 OFF/ON terminal 기반 체류 경계를 그대로
계산하지 않습니다. recovery terminal은 ON 완료가 아니라 다시 OFF로 수렴한 시각입니다.
OFF publish 두 명령 사이 약21.08초를 실제 OFF 시간으로 쓰지 않습니다.
관측 순서는 allOFF(01:33:46–48)→혼합(01:34:42–44)→allOFF(01:36:09–11)→
allON(01:47:37–39)입니다. 마지막 두 관측 사이는 약11.5분이지만 연속 OFF 체류나 실제
임펠러 정지·재기동 시각은 계측하지 않았습니다. 사용자 앱 조작의 정확한 시각도 없습니다.
어느 멤버가 가장 오래 OFF였는지는 직렬 순서만으로 판정하지 않습니다.

앱 준비용 stop 요청01:27:03.316832→재기동 StartedAt01:33:09.201078943Z는 약6.1분,
실패 stop 요청01:34:06.326339→prior StartedAt01:51:12.062472495Z는 약17.1분입니다.
이는 요청~프로세스 기동 span이며 정확한 service 부재·HA online 발행 시각과 동일하지 않고,
펌프 OFF 시간도 아닙니다. 임시 배포 stop/start 구간도 별도로 존재합니다.
낮은 Flow/constant의 전체 연속 운전 시간은 앱 변경 시각 미보존으로 유도하지 않습니다.

## durable 회계와 증거 등급

journal root에 대한 sorted(relative POSIX path, SHA-256(bytes))를 ASCII JSON으로
`sort_keys=True`, `separators=(",",":")` 직렬화하는 동일 방식으로 pre/post를 계산했습니다.
pre26 operations/72 blobs/98 files에서 post28/77/105로 증가했습니다. 기존98 path/bytes는 전부
불변입니다. 신규는 record2 + baseline_status blob5이며 forward/rollback payload·readback·ACK는
기존 content-addressed blob을 재사용했습니다. 여섯 baseline capture 중 OFF record의 Bar ON/31은
기존 blob 재사용, 두 Pro의 네 capture와 ON record의 Bar OFF/31은 신규입니다. 이 구성은 이번
capture 범위의 관측이지 제품별 재사용 규칙이 아닙니다. intent preimage 두 건은 blob에 없습니다.

pre journal digest `ab7a91e4290c5cd0c9f2703a838ba8250f2a1d469dd418c3bcc1b14700583683`,
실패 직후105-file digest `71dc0303dbbb5d8b08e110056763faa7baf9f8e6009d12d0f85afc35012427e8`,
recovery 이후105-file digest `8eb0ae8c82032544a94c2866a1d058ccf5c5f27209a3b70b781791294f5cfa3e`입니다.
파일 수가 같은 마지막 두 tree의 차이는 이번 새 ON record의 recovery revision 변경입니다.
직렬화 방법을 바꾼 것이 아니며 D1q의 inventory 계산 문제와 구분합니다. recovery 뒤 앱 읽기와
prior 복귀에서 마지막 digest가 유지됐습니다. receipt root18의
`9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`는 전 구간 불변입니다.

- 장비가 실제로 돌려준 값의 증거 등급: journal·blob·배포 입력·보존된 구조화 stream은 (b),
  MQTT/CLI·독립 authenticated read의 관측 해석은 (c)로 한정합니다. 관측 JSON을 보존했다는
  이유로 raw wire 등급(a)로 올리지 않습니다. 원인 예외·Bar ACK·raw wire는 보존 근거가 없습니다.
- opaque artifact id: `m4-group-power-63b6f4110306bf31e2b66563e60d95f4`.
- UTC span: 준비01:22:16.306078부터 최종 복귀 관측01:51:38.894388까지; 명령 intent는
  01:33:27.158286 / 01:33:48.238647입니다. 뒤 reconciliation은 같은 terminal 자료의 read-only 검사입니다.
- SHA-256 identity binding: 위 세 domain-separated anchor입니다. private 식별자·주소·경로는 제외합니다.
- artifact digest (SHA-256), 실제 파일 존재·bytes를 확인한 주요 산출물:

| artifact | bytes | SHA-256 |
|---|---:|---|
| pre inventory | 16597 | `b7e023cd2fe4b0843cd97cb4b5472e3adc1e9c177921ab662da1381bf171ee81` |
| observer stream | 98022 | `4b26d62dde51176f2096ad7b4f49d73213b93a142347beb4706cbc2d7b95b562` |
| execution summary | 433 | `fcd83813da48a1a07ab9f708c0a614b43b8737f09750bf484fee341912d97547` |
| OFF terminal record | 3381 | `8fa4637bce03f8448ef790c36ae0e981c13beeff479a1f63a5d97b47f99abec7` |
| ON recovered terminal record | 6425 | `80c83fc0fe4f73467a1454f386b702702b6bb3d4207776b97ffb50467a3cbf8b` |
| recovery CLI output | 235 | `24eef60e4d18e4f2792dd5165fe7c4439435a69bf479894df51e90d233f908f1` |
| reconciliation with final record copies | 36943 | `4dfb93151ebdbfdaf1b0f0fe224acf70aad41508a14827f009b3c4e5402ad5e4` |
| accepted app restore read | 2634 | `e459639a2950e4fd6de92760271e4673cb1970d686690e35cf44eb8e9da3c816` |
| final broker audit | 1256 | `24d13fb9f29504f9fafb9ae69529861f2534cb1d4a9061ca7c23db6a7547f11c` |
| final running fresh read | 2745 | `22bd5d172d76bab509c0ac9bd87ef22c6885552834b0ce238c4014c1539433dc` |

observer18176B digest `66bb6a8b8776823fb666f4494e9594531af50f68ae1e4401a8f127364e78a66e`,
host8121B digest `96cb25b1fd8b31d2010e8d7352367355b4007fcb4d218bde20fb9a80fd7170ea`를 단회 실행 전에
결속했습니다. 첫 관측 revision은 OFF1/ON1이며 first frame 전 rev0 snapshot은 확보하지 않았습니다.
journal-first는 기존 구현 계약과 보존 artifact에 근거하며 이번에 독립 wire 시각으로 증명했다고 하지 않습니다.
복구 뒤 reader를 찾던 첫 파일명 조회는 FileNotFound로 끝나 존재하는 이름을 확인해 읽었습니다.
이는 device 접촉 전 host 조회이며 실기·production 코드 변경이 아닙니다.

## 원복 검증 결과

마지막 fresh read에서 세 대 ON, 선언 manual/Timer/mode/linkage/Frequency/PulseTide와
second의 현재 slot이 일치합니다. raw status 전체나 schedule image 전체의 byte-exact 복원은
주장하지 않습니다. HA registry의 개별 등록이나 실제 수류량도 미계측입니다. prior service는 unarmed입니다.

## 이 실행으로 새로 확정된 사실

- 이 세 binding의 canonical OFF는 power-only 세 action으로 완료됐습니다.
- 두 Pro에서는 OFF-state Flow31→30과 후속 ON, 실제 역순 recovery의 OFF→Flow31이 관측됐습니다.
- Bar Flow의 UNCERTAIN으로 전체는 FAIL이며 혼합 상태에서 마지막 record만 recovery write4로
  allOFF/31에 돌아왔습니다. 사전에 선언한 실패·복구 순서를 실제로 거쳤습니다.
- generic coordinator의 intent 미보존, status-only 신규 blob5와 기존98file 불변을 이번 범위에서 확인했습니다.

## 이 실행으로 확정하지 못한 것

Bar send_once의 원 예외·wire 도달·순간 적용·제품 고유성, 세 대 ON 완료와 후속 hold,
무기한 dedupe, 의도적 경합/supersession, recovery 자체 실패 분기, 물리 OFF 시간·수류량은 미확정입니다.
전체 M4나 production grouping을 PASS로 올리지 않습니다. 과거 성공으로 이번 FAIL을 설명하지 않습니다.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

승인된 operation은 FAIL과 복원 기록으로 종결하며 재실행하지 않습니다. Bar 원인은 UNKNOWN으로
park합니다. 기존 blob·phase·보존 stream의 write-free 검사는 가능하지만 원 예외나 wire 정보를
뒤늦게 만들 수 없습니다. 대안은 (1)기존 자료와 source의 offline 검사, (2)별도 승인된 최소 조작에서
원 예외/transport 관측부터 보존하는 방법입니다. 지금 어느 쪽도 새 device write를 정당화하지 않습니다.
park 영향은 세 대 group OFF/ON 성공을 주장할 수 없다는 것이며 기존 한정 PASS를 철회할 근거는 아닙니다.
다음 실기는 목적·현장 복원 수단·새 승인이 필요합니다. 새 fake나 추정 firmware gate는 추가하지 않습니다.
