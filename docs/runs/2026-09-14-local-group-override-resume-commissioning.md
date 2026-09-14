# run: 2026-09-14-local-group-override-resume-commissioning

- 시간 기준: 파일명은 KST 날짜이고 timestamp는 UTC입니다. 준비는 UTC 9월13일,
  실제 명령과 복귀는 UTC 9월14일이며 둘 다 KST 9월14일입니다.
- 목적(한 문장): Constant31 그룹에서 첫 Pro의 직접 Flow32 override가 후속 cycle에 유지되고,
  그 멤버의 resume 뒤 첫 Pro만 Flow31로 돌아오는지 확인합니다.
- 이 실행이 답하려던 질문: manual ownership으로 제외된 멤버를 그룹이 덮지 않으며,
  resume이 전체 plan을 재평가하더라도 실제 변경은 그 멤버 하나에 한정되는가.
- 사용자 경계: maintainer이자 on-site approver인 사용자가 현장 감시·중단 수단을 맡고
  앱 준비·복원을 수행했습니다. 임시 배포, group ON1, first Pro direct32 1, member PRESS1,
  유한 hold, service stop, 앱 복원과 exact prior 배포가 범위입니다.
  native Linkage·group OFF·다른 멤버 direct·실패 뒤 corrective PRESS는 실행하지 않았습니다.
- 실행한 commit SHA: 정책 `4eb884b3c98a5059bcb3d1a21716a53592ef1311`.
  source/tests·동결 대상은 무수정이고 image 재빌드는 없습니다. exact image는
  `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`입니다.
- 사용한 명령·주요 파라미터: 기존 prepare-compose·stage·serve와 MQTT QoS0/retainfalse의
  ON, 32, PRESS 각1회. Constant default.power만50→31로 바꾸고 min30/max75/period10,
  transition0·member phase0/180/90·gain1·invertfalse·role은 유지했습니다.
  Constant의 실제 목표31과 baseline31이 일치하므로 초기 ON이 no-change입니다.
  direct32는 app/per-device30..100 안이며 group limit을 direct 입력 gate로 쓰지 않습니다.
- 종료 지점(stage / failure code): VERIFIED4·revision7/4/5/9·Flow frame2·power frame0,
  각 단계 후속 fresh cycle3회 통과 뒤 stop, observer exit0, 별도 fresh read·선언 field 복원·prior 복귀 완료.

## 배포 입력과 앱 준비

prior Compose generation `2b4d91a3a662ac2aa85bacf91a03f72b`, Compose SHA-256
`9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`, bundle4 tree
`4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`를 보존했습니다.
임시 generation은 `b1c51cc12a4ad0cd60941d71f42f988e`, Compose digest는
`d6d37c00b323a0ae1213f7100659a12aea40ccc794510451fdb61e378f57becb`, bundle4 tree는
`805a8cee45471c20ff154cbd75a953daeab2a4f61210c9a51e93e734a0d7b767`입니다.
qualification input은2429 bytes,
`b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033` 그대로입니다.

변경은 default.power50→31 하나이며 capability·영구 상한·HA schema·qualification은 바꾸지 않았습니다.
offline projection은 discovery90 byte-identical·old/new/common-changed0, binding4/authority receipt12/
qualified identity-function pair6/ignored0, tombstone53/command10/direct Flow3·power3/group1/resume3입니다.
source4는 production 생성 규격 owner-matched0444/parent0700/link1이고 backup0600과 구분했습니다.

임시 배포는 첫 시도 exit0입니다. 2026-09-13T15:33:45.696290Z stop 요청,
15:33:45.876009Z 정지 뒤 StartedAt15:33:48.191311055Z입니다. runtime manifest
`c7391261acf9488e9300a08a7ce64373`의 재생성15:33:49.147092Z 뒤 readiness를 확인했습니다.
15:34:19.207091Z postcheck는 unarmed·fresh6·cycle failure0·command0·discovery90 exact였고
journal86·receipt18 불변으로 journal-first local-control 경로의 startup write0을 확인했습니다.

앱 조작 전15:40:33.451594Z에 stop을 요청해15:40:33.647219Z에 정지했습니다.
15:40:40–41의 baseline은 first40/TimerOFF/constant/Frequency34, second latent31/TimerON/
constant/Frequency32와 current sine40 schedule, Barclassic91/TimerOFF/Frequency80/PulseTidefalse입니다.
모두 ON·independent였습니다. 앱 준비의 변화는 first40→31의9%p, Bar91→31의60%p와 파형 변경,
second는 TimerOFF로 그때 관측한 schedule40에서31로 바뀌는9%p입니다.
이 schedule 관측을 다음 날까지 현재값으로 고정하지 않으며 실제 앱 조작 순간·전송 수는 미보존입니다.

사용자 완료 회신 뒤 2026-09-14T00:43:16.053990Z–00:43:17.686872Z fresh read에서
세 멤버 ON/TimerOFF/constant/independent/Flow31과 Frequency34/32/80·Bar PulseTidefalse를 확인했습니다.
전후 journal86·receipt18은 불변이며 이 읽기와 준비에서는 agent device write0입니다.
서비스 재시작 StartedAt00:43:41.766495726Z 뒤 manifest
`b294b3eabe24e243b5138e71c0033802` 재생성00:43:42.725582Z, readiness·unarmed와
별도 authenticated preflight를 통과한 뒤에만 ON을 보냈습니다. 앱·HA 병행 조작은 하지 않도록 요청했습니다.

## 장비가 실제로 돌려준 값과 사전 등록치 대조

physical identity는 `sha256("qualification-target:" + physical_identity_key)`로만 결속합니다.

| 역할 | anchor |
|---|---|
| first Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` |
| second Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` |

group action 순서는 second, first, Bar 각각 manual_flow/power입니다. reduced에서는 first가 제외됩니다.

| record / 단계 | 구성과 exact readback | NO_CHANGE | revision | Flow / power frame |
|---|---|---:|---:|---:|
| 1 / ON | 세 멤버31→31, power ON 유지, action6 | 6 | 7 | 0 / 0 |
| 2 / direct32 | first31→32, manual_flow action1 | 0 | 4 | 1 / 0 |
| 3 / reduced | second·Bar31→31, power ON 유지, action4 | 4 | 5 | 0 / 0 |
| 4 / resume | first32→31만 변경, full group action6 | 5 | 9 | 1 / 0 |

revision은 create0 + changed action당 save3 + NO_CHANGE당 save1 + terminal1입니다.
따라서7/4/5/9이며 사전 등록한 record4·Flow2·power0과 일치합니다. skip은 journal action을 만들지 않습니다.
override가 membership·control_epoch·plan identity를 바꾸므로 reduced no-change record1개는
중복 frame이 아니며 정상 예상치입니다. resume도 전체 plan을 다시 평가하지만 나머지 두 멤버에는 frame이 없습니다.

ON publish intent00:43:57.026535Z, record1 생성/terminal00:44:07.381611Z/00:44:07.450148Z;
direct32 intent00:44:47.400922Z, record2 생성/terminal00:44:55.793925Z/00:44:57.632849Z;
record3 생성/terminal00:45:07.168697Z/00:45:07.204201Z;
PRESS intent00:45:50.877797Z, record4 생성/terminal00:46:00.299089Z/00:46:02.191645Z입니다.

| hold | 완전한 후속 fresh cycle | 관측 결과 |
|---|---|---|
| initial armed | 2,3,4 | all31·override0·record1 bytes 불변 |
| direct override | 6,7,8 | first32·second/Bar31·override first exact-one·record3개 불변 |
| resumed | 9,10,11 | all31·override0·record4개 불변 |

각 target의 Flow/power 새 수신과 diagnostic의 동일 observed_at를 해당 장비 availability online으로
닫히는 fresh publication batch에 결속했습니다. timestamp는 해당 단계 terminal 이후이고 cycle마다
증가합니다. 다른 target의 timestamp나 정지된 retained 값을 붙여 fresh라고 하지 않습니다.
처음 두 hold 뒤 independent authenticated read를 추가해 all31 및 first32/others31을 재확인했습니다.
private manual_override_members는 내부에서만 읽고 화면·중계·기록에는 count와 anchor만 남겼습니다.
resume availability는 armed 조건일 뿐 first override의 독립 증거가 아닙니다.

MQTT ON/PRESS 결과는 약2초의 pending snapshot으로 반환됐고 verdict로 사용하지 않았습니다.
direct 경로는 accepted 뒤 요청값을 retained state로 발행할 수 있습니다. 이는 readback을 통과한
값이지만 후속 poll의 timestamp를 갖지 않으므로 그 단일 publication을 유지 증거로 쓰지 않았습니다.
직접 변경 중 device_write_in_progress unavailable은 가능하며 실패와 동일시하지 않습니다.
이 실행은 일반 경로의 관측이지 generation guard의 인위적 race/fault injection 실기가 아닙니다.

## 정지와 durable 산술

마지막 terminal 직후가 아니라 resumed hold3회 뒤00:46:33.775808Z에 guard stop을 냈습니다.
host 요청00:46:33.777325Z, stopped00:46:33.987674Z, exit0입니다.
최초 ON intent부터 guard까지 약156.75초이고180초 상한 안입니다. 이 상한은 관측 기반 stop이지
추가 frame을 물리적으로 불가능하게 만드는 gate나 완주 보장이 아닙니다.
5번째 record·SUPERSEDED·power attempted·추가 Flow·retry·group OFF·recovery는 없었습니다.
SUPERSEDED가 생겼다면 기존 guard의 정상 보호일 수 있어 관측 미완료 UNKNOWN으로 끝내는 정책이었고,
이번 성공으로 그 실패/경쟁 경로를 검증했다고 주장하지 않습니다.

정지 뒤00:47:03.732573Z–00:47:05.743075Z에 세 장비 ON/TimerOFF/constant/independent/31 및
Frequency34/32/80·Bar PulseTidefalse를 별도 fresh read로 확인했습니다.

| record | bytes | SHA-256 |
|---|---:|---|
| 1 | 6286 | `439661f202e947884d0a38d3702b70c35777bc8d9063a3a027d35d6333c2d61d` |
| 2 | 1322 | `43789068cfd95a25da0ed3f6d9d9a765f1cac89cabb87268f67af2f28b1b8d44` |
| 3 | 4288 | `e4955940bc857b780f64106d07eae457ceb83e100654d019a3ce316263337f98` |
| 4 | 6319 | `a1114541a26206ecd88d35cada309a7361e02a299117be98c09e40e1be6ed25b` |

journal은 operations22/blobs64/files86에서26/72/98로 바뀌었습니다. 기존86개 path/bytes는 전부 불변,
추가12개는 record4+blob8입니다. canonical tree는 local-control root 상대 POSIX path와 각 파일
SHA-256 pair를 정렬한 뒤 JSON sort_keys=True·separators=(",",":")·ASCII로 해시했습니다.
pre/post를 같은 방식으로 계산했습니다.

- pre journal: `d0bcad6238fd2e45e84ec1112a1547b303a21f8019f3cadfb57982e6a8929b06`
- post journal: `ab7a91e4290c5cd0c9f2703a838ba8250f2a1d469dd418c3bcc1b14700583683`
- receipt files18 불변: `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`

신규 non-status blob은 Pro Flow32 payload460 bytes
`d1215f209b0bd491bfe20174b5a6c9f37e28c97187763563c03f4a18c4b3b7b8`와
canonical readback `{"Flow":32}` 11 bytes
`466fc0a5ff05dccc92f313d01fa476c19d3e0fff6c870382cdd7cf59c1fc4eb1`입니다.
둘은 record2에서 신설되고 record4 rollback/baseline 참조가 재사용했습니다.
나머지 신규6개는 아래 baseline_status입니다.

| record / member | bytes | SHA-256 |
|---|---:|---|
| 1 / second_pro | 452 | `6f5df1656ecd592bb8f9d35e9b9f873fa02b4f58def814fce2b5b88fd562455d` |
| 1 / first_pro | 452 | `59dd85d043409819331abaa92035c99a933c872be5906ecab6adf1175cb1cfe5` |
| 2 / first_pro | 452 | `f6b316d0a1450da58263d1b1e044b4f8d8e62572236aaee57fa4867bbdd2eab2` |
| 3 / second_pro | 452 | `b572a6b7dae9090382e2bd1899101aa2d90e1d1da9dbf9f8c1ae4a70ffd75deb` |
| 4 / second_pro | 452 | `262674eda64263e3d64f8ce5e32a76a2cba7871016410576d3a01684078e4115` |
| 4 / first_pro | 452 | `1a62eeb84a1ddef942f719f322f2fee4777bf9a9303d2e6d060218241fedd7a3` |

baseline capture는3+1+2+3=9회입니다. 재사용3회는 Bar record1/3/4로 모두 pre blob
`4eb855d8e1e196cfa03208d236fe63f1b4a62d734300a624f2357b6a93d0a690`의401 bytes입니다.
새32 관련 두 blob 외 기존 Flow31·power·ACK 참조는 기존 blob을 재사용했습니다.
제품별 status 재사용성의 일반 법칙을 만들지 않고 이번9회에 한정합니다.
generic coordinator의 intent digest4개에 대응하는 preimage는 blob store에 없으므로
intent 원문 보존이나 journal만으로 원문 복원을 주장하지 않습니다. digest의 idempotency 결속은 별개입니다.

## 원복 검증 결과와 prior 서비스 복귀

사용자 복원 완료 후00:52:03.258704Z–00:52:04.731364Z에 다음 선언 decoded field 집합을 확인했습니다.

| 장비 | power / Timer | Mode / manual Flow | Frequency / PulseTide |
|---|---|---|---|
| first Pro | ON / OFF | constant / 40 | 34 / 해당 비교 없음 |
| second Pro | ON / ON | constant / 31 | 32 / 해당 비교 없음 |
| Bar | ON / OFF | classic / 91 | 80 / false |

second는 현재 active sine50과 AutoMode/AutoFlow가 일치하고 exactly1·valid14/invalid0입니다.
과거 sine40을 복원 목표로 고정하지 않았고 latent31 변경 수용이나 추가 조정도 필요 없었습니다.
전체 raw status·schedule image byte-exact 복원이나 물리 수류량 계측을 주장하지 않습니다.

보존된 exact prior image·Compose·bundle4 bytes와 source0444/parent0700/owner/link1을 확인한 뒤
production Compose 교체로 복귀했습니다. 첫 시도 exit0, StartedAt00:52:52.390206813Z이며
restart_count0은 force-recreate 뒤 신규 counter이지 연속성 증거가 아닙니다.
runtime generation `069f5fe606cd64fe161c69b7e6337dbd`와 재생성00:52:53.592169Z를 확인했습니다.
이것은 prior Compose generation과 다른 개념입니다. 과거 D1p UNKNOWN의 원인을 소급 설명하거나 배제하지 않습니다.

00:53:09.819318Z postcheck는 discovery90/90 byte exact, digest
`b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`,
tombstone53/nonempty0, command10/message0/retained0, direct Flow3/power3·group1/resume3,
unarmed·override0·resume online0·supervisor1·cycle failure0·fresh cycle1·device6/global online,
binding4·ignored0·unfinished0입니다. 00:53:16–17 final fresh에서도 위 field와 current slot 일치를 확인했습니다.
journal98·receipt18은 복귀 전후 불변이어서 startup write0을 journal-first local-control direct/group 경로에
한정해 확인했습니다. 앱·외부 경로의 write0이나 broker 전 구간 연속 관측·HA registry는 주장하지 않습니다.

임시31은 끝나고 default Constant50/max75/period10으로 돌아왔습니다. 복귀 자체는 group ON이 아니며
unarmed라50을 재전송하지 않았습니다. 이후 group ON의 manual 목표50과 TimerON 장비의 schedule
소유권은 별개이고, TimerON에서 이 조작을 실기 검증한 것은 아닙니다.

앱 대기 정지2026-09-13T15:40:33.647219Z부터 다음날 재기동00:43:41.766495726Z까지 약543.1분,
마지막 stop부터 prior StartedAt까지 약6.3분의 service 부재가 있습니다. 이는 펌프 OFF 시간도
HA availability online 발행 시각도 아니며 초기 임시 배포 정지까지 포함한 총량도 아닙니다.
앱 변경/복원 순간을 보존하지 않아 저출력 운전 시간을 정확히 계산할 수 없습니다.
두 시점 관측 사이를 연속 altered-regime의 확정 하한으로 바꾸지 않으며 준비·복원 비용을156.75초로 축소하지 않습니다.

## 증거 등급과 보존물

- 증거 등급: (b)는 실재·digest를 확인한 journal·referenced blob·bundle·manifest·inventory의
  persisted claim입니다. Docker/CLI/broker·사용자 앱·authenticated fresh read의 해석은(c)이며,
  JSON을 보존했다는 이유로 원본 wire frame의(a)로 승격하지 않습니다.
- opaque artifact id: `m4-override-resume-926ca3d9cc2e885ed8fc2bccc50e8745`.
- UTC span: pre inventory `2026-09-13T15:23:38.258900+00:00`부터 보존물 재독
  `2026-09-14T00:55:47.659929+00:00`까지. live·read·stop·배포 구간은 위에서 별도로 적었습니다.
- identity binding과 artifact digest는 위와 아래에 기재하며 private 이름·MAC·경로는 기록하지 않습니다.

| 보존물 | bytes | SHA-256 |
|---|---:|---|
| pre-inventory.json | 15207 | `20804b558a03a6375e595c943d61cd45d8c98536ff479ebb63df8136bad99878` |
| override-bounded-observer-v1.py | 18386 | `f5bbaa90583f08145d0f8b53c55674de6b2a6b5cd52f490aa92e71e600efc88b` |
| override-execution-host-v1.py | 8051 | `ad3f0f89a4b4527d49c4a9a0504d502e16c2db3a2797d41ae5ab7aa0c2bfb7c5` |
| override-command-stream-v1.jsonl | 137329 | `3bbd4885284c3d54201097834c88d0aebe54fc3c4cbb88e798de74d4e02a27d4` |
| override-execution-summary-v1.json | 37148 | `134ff75f6ea94a96516c0e05f86cd4ca953cb43119e0d27101797f4a447c7c67` |
| override-blob-reconciliation-v1.stderr | 90 | `64e15587027a3165dcb3da85df76d2521b3bf56c791a9a707f08dd7cf554b664` |
| override-blob-reconciliation-v2.stdout | 8262 | `f5ba54ce64ce77e2363eedf5e2267d7b229d9c2ff08dfdfaffea4edb76b940f5` |
| app-precondition-read-01.stdout | 1534 | `1a2ef8f822aafa53062e8960694ca2d59b211a7209568344bd7bab6c3b0b1301` |
| post-stop-fresh-v1.stdout | 1533 | `1d5ab05dd799ecc1e3eb187f6a6918ce51dac7b5201be6a18bc3e405511c580c` |
| app-restore-read-01.stdout | 1898 | `0491081acec056731ca04505de9d5564e5f754c8ea7cb09d6a1303c688e8f194` |
| final-prior50-mqtt-audit.stdout | 1255 | `6723ad52b286b4a72e5d80195fae13dfb4291459e68b63d151de686df156e703` |
| final-running-fresh.stdout | 2741 | `abaf113ab14d37d4d9c372089776182c6c9e7cb765aa49db88ec4de522fc6d44` |
| final-runtime-manifest.json | 2914 | `6296f7384aef2ff5c2524ec9db8deb5fac170986a49375a9e721b44869255f29` |
| override-prior-return-host.py | 20408 | `75219f112a9f8fcb15f775f9708a708b96ea2cb8f7d42d89893a7c4799a75652` |

첫 관측 revision은 record별6/1/5/3입니다. 첫 frame 전 rev0 snapshot은 확보하지 않았으며
journal-first는 기존 코드 계약과 terminal artifact에 기대는 한정입니다.
observer/host/stream v1은 단회 실행이며, reconciliation v1/v2는 읽기 전용 감사 정정이지 재실기가 아닙니다.

## 실행 중 정정

첫 reconciliation은 Python tuple pair와 JSON에서 읽은 list pair의 객체 equality를 직접 비교해
receipt 검사에서 AssertionError로 끝났습니다. 실패 stderr90 bytes를 보존했고, 동일 canonical JSON
표현으로 비교한 v2에서 receipt bytes 불변과 전체 산술을 확인했습니다. tree 정의나 기준 digest,
production 데이터·permission은 바꾸지 않았고 해당 감사는 device/broker 접촉·device write0입니다.
앱 준비·복원·배포와 물리 observer는 재실행하지 않았습니다.

direct state publication 부재를 추측하지 않고 mqtt.py의 accepted 결과 발행 경로와 poll batch를
분리했습니다. hold는 timestamp 없는 scalar 단독이 아니라 후속 full batch와 독립 read로 판정했습니다.
MQTT pending을 terminal verdict로 assert하거나 감사용 out-of-band production lock을 취득하지 않았습니다.

## 이 실행으로 새로 확정된 사실

- 이 세 binding의 TimerOFF·manual 소유 상태에서 first Pro direct31→32가 세 후속 fresh cycle에
  유지됐고 first만 manual override인 reduced group의 나머지 둘은31을 유지했습니다.
- first resume 뒤 그 멤버만32→31 frame을 보내고 override0·세 후속 cycle의 all31을 확인했습니다.
  정상 예상치 record4·revision7/4/5/9·Flow2·power0, 선언 field·prior 서비스 복귀가 닫혔습니다.
- membership 변경에 따른 reduced no-change record와 새32 payload/readback·status6개를 구분해
  durable 증가4+8을 설명하고 기존86 files·receipt18 불변을 검증했습니다.

## 이 실행으로 확정하지 못한 것

- 다른 멤버·값·pattern·TimerON 조건에서 override/resume, 무기한 hold·dedup, 실제 유량 계측.
- stale generation 경합 주입·SUPERSEDED·direct 실패 뒤 override·nonterminal recovery의 실제 실행.
- group OFF·급여·offline/degraded·emergency-stop 등 남은 M4/M5, native Q1/Q2.
- raw 전체/schedule image byte-exact 복원, HA registry 및 향후 update/reboot의 동일 동작.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

관측 scalar의 출처를 poll과 accepted command publication으로 나누고 target별 full batch·시각을 보존합니다.
expected record 수는 값뿐 아니라 membership/control_epoch 변화까지 계산합니다. 정상 보호의
SUPERSEDED를 device 실패나 physical frame으로 바꾸지 않습니다. 감사는 canonical bytes로 비교하고
실패 값을 먼저 보존합니다. 이 기록과 현재 판정·todo를 갱신하되 M4 전체나 native ASYNC 완료로
승격하지 않으며 다음 physical operation은 별도 범위로 다룹니다.
