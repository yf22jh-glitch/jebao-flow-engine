# run: 2026-09-14-local-bar-off-flow-diagnostic

- 시간 기준: 파일명은 KST 날짜이고 아래 timestamp는 UTC2026-09-14입니다.
- 목적: 그룹을 비무장으로 유지한 채 Bar만 OFF→Flow30→ON하여, OFF-state Flow write가
  독립 direct transaction에서 완료되는지 terminal journal·독립 read·후속 fresh cycle로 판정합니다.
- 결과: **PASS — 이 Bar binding·단회 direct 경로·후속3 cycle 범위**. record3/rev4×3,
  Flow1/power2, recovery0이며 사용자 앱 복원과 exact prior 서비스 복귀를 확인했습니다.
  [D1u group FAIL](2026-09-14-m4-group-power-commissioning.md)과 원 예외 UNKNOWN은 유지합니다.
- 승인: full-suite 실패2건 미해결, Bar OFF 잔류 가능성과 현장 직접 복원 필요를 공개한 뒤
  사용자 승인을 받았습니다. Pro pair·리턴·도징은 write 대상이 아닙니다. 정책 `ad95635`,
  [D1v 계획](../local-bar-off-flow-diagnostic-plan.md)의 단회만 실행했습니다.
- 실행 시 HEAD `e307516eefaa8c9f991a1d295e053bba993e57a1`, diagnostic source `269e92b`.
  실행 중 source/tests·gate·timeout·retry·동결 native ASYNC 변경은 없습니다.
- 명령: 기존 qualified direct MQTT QoS0/retainfalse OFF1·Flow30 1·ON1입니다.
  group ON/OFF·Pro command·재전송·fault injection·recovery는 실행하지 않았습니다.

## 진단 준비와 image 배포

먼저 D1u 원 예외의 보존 부재를 failure-only 진단으로 보완했습니다. coordinator의 forward send,
forward readback, rollback 예외 catch에서 uncertain journal save 이후 ERROR event를 남깁니다.
예외 class chain·내부 module/function/line·monotonic boundary 경과와 request digest만 담으며
메시지·locals·원 traceback·private 식별자를 출력하지 않습니다. journal/writer/schema는 그대로입니다.
event v1의 `request_sha256`은 접두어 없이 ASCII request_id의 SHA-256입니다. 기존 D1u 예외를
복원하거나 원인을 설명하는 기능이 아닙니다. 필드 계약·monotonic 경계 정의는 계획에 고정했습니다.

targeted21·Ruff는 PASS지만 최종 full suite는 두 실행 각각2588 PASS/1 FAIL입니다.
한 번은 기존 Linkage primary failure 관측, 다른 한 번은 기존 first-write lower-power 관측이
실패했고 원인은 UNKNOWN입니다. 초기2587 PASS·HEAD2578 PASS로 이를 덮지 않습니다.
사용자는 이 미해결 상태를 알고 배포를 승인했습니다. missing Git 시험 환경 실패·ENOSPC 및
test-only 환경 정정은 계획에 보존했으며, full-suite 문제는 D1u 원인과 별개로 미해결입니다.

prior image: `785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`.
candidate image: `847c51edb112abb9717c7a577666d5d46842dc78e3a470e40e8386b96ba82d0c`.
candidate는 clean `4c31f3f` archive에서 첫 build exit0으로 생성됐습니다.138 Python source 파일이
archive와 exact 일치하고 prior와 다른 source는 coordinator·failure_diagnostics 두 개입니다.
Python·설치 의존성은 동일하며 network-none 비교에서 protocol-schema digest
`fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`와 projection이 일치했습니다.

Compose input generation `2b4d91a3a662ac2aa85bacf91a03f72b`는 양쪽 image에서 같습니다.
generation 단독으로 배포를 구분하지 않고 image digest를 함께 씁니다. config delta0,
Constant default50·member 설정·qualification은 유지했습니다. 원 Compose2개와 bundle4개를
no-clobber 보존했고 input0444/parent0700/owner-matched/link1을 확인했습니다. overlay는 두 service
image만 바꾸며 모든 호출에 Compose 파일을 명시했습니다. 복귀는 overlay를 제외하고 prior image를
inspect로 확인합니다. 재빌드·pull·volume 교체·일괄 prune는 하지 않았습니다.

[HA 업데이트 후 read-only 확인](2026-09-14-ha-core-update-readonly-postcheck.md) 뒤,
prior stop04:34:14.005149→14.778936, candidate StartedAt04:34:16.699757580Z,
manifest `a7bf3ebde319579ac77694ee923884a7` 재생성04:34:17.877622 순서였습니다.
04:34:34.116737 postcheck는 fresh6·discovery90 exact·qualified/unarmed·failure0·command0·
unfinished0·journal105/receipt18 불변입니다. 이 확인은3-cycle hold가 아닙니다.
앱 준비 전 stop 요청04:34:34.463891→정지34.684504 뒤 독립 baseline을 읽었습니다.

## 준비와 현장 승인 경계

04:34:40–42의 독립 read는 Bar ON/classic91/TimerOFF/Frequency80/PulseTidefalse,
first Pro ON/constant40/TimerOFF/Frequency34, second Pro ON/constant/latent31/TimerON/
Frequency32를 확인했습니다. 세 대 모두 independent이며 second의 그 시점 tidal55가 active slot과
exact-one·valid14/invalid0으로 일치했습니다. 이전 sine50이나 이번 tidal55를 고정 복원 목표로
쓰지 않습니다. 동일 slot 개수만으로 전체 schedule 내용 불변도 주장하지 않습니다.

Bar 준비의 실제 변화는91→31의60%p 하향과 classic→constant 파형 변경입니다.
04:43:34–35의 첫 확인은 아직 classic91이라 준비 미충족이었고 명령을 보내지 않았습니다.
사용자 앱 완료 뒤04:45:50.350629–51.888387 재독에서 Bar ON/constant31/TimerOFF와 선언 extra
fields, 두 Pro 유지가 확인됐습니다. 앱 변경 순간·앱 전송 횟수는 보존하지 않았습니다.

처음 operator approval v1은 앱 완료를 현재 현장 확인까지 포함한 것으로 잘못 해석했습니다.
첫 실행 전에 이를 철회하고 correction artifact를 보존했습니다. v2 승인 파일이 없으면 실행을
거부하는 host로 바꾼 뒤, 별도 현장 감시·직접 ON 복원 가능 질문에 사용자가 “ㅇㅇㅇ 시작하자”로
답했습니다. 이후 사용자의 ON-before-Flow 순서 질문으로 실행을 보류하고 시험 목적을 설명했으며,
“아 알겠어 진행하자” 뒤에만 실행했습니다. v1 승인으로 실행하거나 명령을 보낸 적은 없습니다.
observer v1·host v3는 코드 버전이고 stream/summary v1은 단회 실행 번호입니다. 세 번 실행한 뜻이
아닙니다. private member 목록은 공개 출력에서 count와 anchor로 치환했습니다.

## 실행과 실제 관측

anchor는 ASCII `qualification-target:` + physical_identity_key의 SHA-256입니다.

| 역할 | anchor |
|---|---|
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` |
| first Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` |
| second Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` |

재기동 StartedAt04:59:43.751021654Z, manifest `8c7b82fc0a4052654f6ad751b45067c7`
재생성04:59:44.798032 뒤 readiness·별도 fresh6·unarmed·discovery90·두 tree 불변을 재확인했습니다.
observer도 새 complete batch와05:00:11–12 독립 read로 Bar31·first40·second31/TimerON을 확인한
뒤에만 publish했습니다. stderr follow는 첫 publish 전에 열었고 정지 뒤 final logs도 보존했습니다.

| 단계 | publish intent | journal created → VERIFIED | baseline → expected | rev / action / write |
|---|---|---|---|---|
| OFF | 05:00:12.788138 | 05:00:21.663177 → 21.956363 | power true→false | 4 / 1 / power1 |
| Flow | 05:00:27.049053 | 05:00:32.109710 → 32.470952 | Flow31→30 | 4 / 1 / Flow1 |
| ON | 05:00:37.589624 | 05:00:43.003519 → 43.463609 | power false→true | 4 / 1 / power1 |

각 revision은 create0 + changed action의3 save + terminal1 =4입니다. 세 action 모두 VERIFIED이며
ACK·baseline/expected readback·forward/rollback payload digest를 보존 파일과 대조했습니다.
OFF action의 restore_on_recovery=false, Flow/ON은 true입니다. NO_CHANGE는 없습니다.
첫 보존 journal 표본은 세 record 모두rev1이므로 첫 frame 이전rev0 snapshot 확보는 주장하지 않습니다.

OFF terminal 뒤05:00:27.035400 독립 read에서 OFF/Flow31, Flow terminal 뒤05:00:37.575636
독립 read에서 **OFF/Flow30**을 확인한 뒤 각각 다음 명령을 보냈습니다. Frequency80·TimerOFF·
constant·independent·PulseTidefalse는 유지됐습니다. MQTT command-result는 보조 관측일 뿐
verdict가 아니며, 명령 유래 state나 retained 값만으로 후속 유지 여부를 판정하지 않았습니다.

| post-ON hold | lifecycle cycle | Bar fresh stamp | 값 |
|---|---|---|---|
| 1 | 4 | 05:00:43.852396 | Bar ON30 / first ON40 / second ON31·TimerON |
| 2 | 5 | 05:00:57.514091 | 동일 |
| 3 | 6 | 05:01:10.876345 | 동일 |

각 hold는 세 target의 모든 state topic이 새로 도착하고 online availability로 닫힌 non-retained
batch이며 diagnostic timestamp가 ON terminal과 직전 표본보다 뒤여야 했습니다. 장비별 timestamp는
서로 다르므로 물리적 동시 관측이 아닙니다. 세 hold 동안 terminal record3개의 bytes가 같았고
새 record는 없었습니다. 그룹은 비무장, override는 Bar1개이며 Pro override는 없었습니다.

세 번째 hold 뒤 guard05:01:13.131755→stop 요청13.133723→정지13.314997 exit0입니다.
OFF intent부터 guard까지60.343617초로180초 관측 상한 안입니다. 내부/host 상한과 빠른 정지는
추가 frame을 물리적으로 불가능하게 하는 gate가 아닙니다. 이번에는4번째 record·추가 명령·
superseded·observer 실패가 관측되지 않았습니다. 상한 만료 시 observer부터 종료하는 분기는 미실행입니다.

## 시간과 물리 비용의 한계

publish→terminal은 순서대로9.168225/5.421899/5.873985초입니다. publish 간격과 device 체류를
혼동하지 않습니다. OFF publish→ON publish는24.801486초, OFF publish→ON terminal은30.675471초,
두 terminal 차이는21.507246초입니다. 성공한 journal의 software 경계는 OFF.updated→ON.created
21.047156초와 OFF.created→ON.updated21.800432초입니다. 이는 직렬 send/readback을 둘러싼
realtime timestamp 경계이며, monotonic OFF 측정이나 임펠러 정지·유량 계측이 아닙니다.
불필요한 OFF hold는 두지 않았지만 command 처리·독립 resolve/read 시간도 운영 비용에 포함됩니다.

앱 준비 전 서비스 정지→시험 재기동은 약25.15분, 시험 정지→prior StartedAt은 약3.92분입니다.
이 값은 서비스 부재 구간이지 펌프 OFF 시간·연속 altered-regime·HA online 실제 발행 시각이
아닙니다. 앱 변경 순간이 없어 저출력 구간 전체를 정확히 산출하지 않습니다.

## journal·blob reconciliation

canonical tree는 정렬된(relative POSIX path, file SHA-256) 쌍을 compact/sort_keys/ensure_ascii
JSON의 ASCII bytes로 직렬화한 SHA-256입니다. 운영 파일을 수정하지 않고 실행 사용자로 읽었습니다.

| 항목 | 전 | 후 |
|---|---|---|
| operation / blob / files | 28 / 77 / 105 | 31 / 78 / 109 |
| journal SHA-256 | `8eb0ae8c82032544a94c2866a1d058ccf5c5f27209a3b70b781791294f5cfa3e` | `a19ba5ccb76769ceeb5268a30ff3d3745359891c792ac199e4ab307cf85a3905` |
| receipt files | 18 | 18 |

receipt SHA-256은 전후 `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`로
동일합니다. 기존105 path·bytes 전부 불변, 추가4 = record3 + blob1입니다. 세 capture 중 첫 OFF
record의 ON31 baseline과 Flow record의 OFF31 baseline은 기존 blob을 재사용했습니다.
신규는 ON record의 **OFF30 baseline_status401B** 하나이며 digest는
`12bff58a81efcd38d42edc7e46821de982c59b8c39c24713bad2960d5f20c0fc`입니다.
forward/rollback payload·readback·ACK는 모두 기존 blob이고 세 intent preimage는 설치돼 있지
않습니다. D1u 사후 Flow31 관측만으로 그때 순간적인 OFF30 도달까지 배제하지는 않습니다.

| record request id | size | SHA-256 |
|---|---|---|
| `mqtt_84d0b166b7b630b1b47101b52551228b` | 1319B | `6eefa0a9326ac3824872f684466caf2fd3b13b082bbabf4471a43b392b316877` |
| `mqtt_205ae42c6bf145e2b0c2284f4474b23c` | 1324B | `855d6b0b5e36f646a34985fed00b9c9fb7094cc6c799d82dfeec286b35d860a6` |
| `mqtt_5024db911f8b3cce4b1790684d50f2a7` | 1318B | `c4ab60ffdec4080a99115a0bd3dd7436a32e236291b34f50037ef92947a6d025` |

## 실제 복원과 prior 복귀

정지 후05:01:42–44 독립 read는 Bar ON/constant30/Frequency80/PulseTidefalse와 두 Pro 원 선언값을
확인했습니다. 이때 original-baseline all_admitted=false는 Bar가 아직classic91이 아니기 때문이며
시험 실패나 복원 완료로 해석하지 않았습니다. 사용자가 Bar 앱 설정을 복원한 뒤05:04:16.697364–
18.958225 재독은 Bar ON/classic91/TimerOFF/independent/Frequency80/PulseTidefalse, first
ON/constant40/TimerOFF/Frequency34, second ON/latent31/TimerON/Frequency32·현재tidal55 slot 일치를
확인했습니다. all_admitted=true이며 전체 raw status·schedule byte-exact 복원을 주장하지 않습니다.

prior 복귀 첫 시도 exit0, 실제 실행 image는 위785cf449… exact prior입니다. 두 원 Compose만
명시했고 config와 source input bytes·permissions를 대조했습니다. StartedAt05:05:08.785804598Z,
manifest `d79dcc0d56fba1c87cbede451808d164` 재생성05:05:09.979656 뒤 readiness를 확인했습니다.
05:05:27.133755 postcheck는 fresh6·discovery90 byte-exact·tombstone53 empty·command10에서
messages0/retained0·supervisor1·unarmed·override0·resume offline·failure0·ignored0·unfinished0,
journal109/receipt18 불변입니다. startup write0은 journal-first local-control 경로에 한정합니다.
discovery digest는 `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`입니다.
복귀 후 default는Constant50이며 이번 OFF-state 시험 목표30을 상시 목표로 남기지 않았습니다.
prior image에는 새 failure event 구현이 없습니다. diagnostic image 재배포는 이번 복귀와 별개입니다.

## 보존 산출물과 증거 등급

private artifact id: `bar-off-flow-diagnostic-a9c77231316e506777a70d38ef939347`.
아래는 해당 artifact 내부의 논리 이름이며 private 절대경로·식별자는 기록하지 않습니다.

| 산출물 | bytes | SHA-256 |
|---|---|---|
| bar-command-stream-v1.jsonl | 59613 | `cfb659ead26b1a61131352c4214b779fc43adf6f7320256315190fe5f4e345db` |
| bar-execution-summary-v1.json | 11526 | `9f28fb194df7538423729046b2da6d90a6eb057623b027fac368cd0c38014699` |
| bar-bounded-observer-v1.py | 17555 | `6e2291325c155bc8f0ac46db2a5bbcbb3210ae56ac241fc336149cc697a3cc36` |
| bar-execution-host-v3.py | 11442 | `6d6da89a8d28c1417b0a089034e62747480e1f1ee22d1445bc07a9f02966c079` |
| bar-execution-host-v3.stdout | 20193 | `bf3400193f216ea3ee1565fb7e31ca9d2c9980730e46d4a8be20e428f85bfee5` |
| execution-approval-v1-correction.json | 421 | `b38e30de0ba4e4238d473de79cb3c0b02f42bbe77aca24120906b656c2d01896` |
| execution-approval-v2.json | 597 | `9a623d2b8dca5c66b5b293d2c0749c17292403c51ec96e003e4b9e561b832ea2` |
| app-precondition-2c54920d.stdout | 2643 | `72810f1c50d31ddf1a7e7df656cd7b88233f73fb44eb920e8375ab6f05bccbc1` |
| bar-post-stop-v1.stdout | 2644 | `bd2bcfd6f2606527ca8b6457d84000ee0993be204552912afa48ebb95e06bcbe` |
| app-restore-v1.stdout | 2640 | `7fce73c82ec62777a46d6992cb219bf79c44ceb8e97149537100d38da9f3a430` |
| bar-reconcile-v1.stdout | 2726 | `5341e7b72c56b63c80bc06c9ebb3c4d9b34094cb42d93387fb35d1d796ed6de2` |
| prior-return-host.stdout | 3391 | `188418c1f1ef6916b4c1ed9f3081ec22d87a48e97db0f8dfa64903d79169fdc7` |

runtime follow/final의 stdout/stderr 네 파일, execution/return host stderr는 모두0 bytes이며
각 digest는 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`입니다.
실패가 없어 진단 event는 발동하지 않았습니다. 배포·성공 실행은 진단의 production 실패 시
전달을 입증하지 않습니다. 그 전달 경로는 격리 interpreter 시험 근거만 있습니다.

journal·blob·보존 구조화 출력은 파일과 digest를 확인한(b)이며 daemon의 persisted claim입니다.
fresh read와 MQTT decoded JSON 보존을 원 wire frame(a)으로 올리지 않습니다. 사용자 승인·앱
조작의 대화 재구성은(c)이고 앱 packet은 없습니다. 명령 한정·계측 한계를 넘어 물리 유량을
판정하지 않습니다.

## 이 실행으로 새로 확정된 사실

- 이 Bar binding은 independent/constant/TimerOFF에서 **OFF 상태 Flow31→30 direct write**를
  VERIFIED로 완료했고 독립 read가OFF30을 반환했습니다. OFF-state write가 언제나 불가능하다는
  일반화는 이 단회 관측과 맞지 않습니다.
- OFF·Flow·ON 세 독립 transaction과 후속3 fresh cycle이 선언 산술대로 완료됐습니다.
  Pro write0·기존105 파일 불변·신규OFF30 baseline blob1개를 확인했습니다.
- diagnostic image의 config delta0 배포·실행과 사용자 앱 복원·exact prior 복귀를 확인했습니다.

## 이 실행으로 확정하지 못한 것

- D1u Bar 예외 원인, group batch5번째 frame/선행4왕복·세션 대기·generation guard 조건.
  이번은 매 명령 독립 transaction/session이므로 D1u의 group FAIL을 PASS로 올리지 않습니다.
- diagnostic event의 실제 production 실패 발동·durable 전달, 최종 full-suite 실패2건의 원인.
- 무기한 유지·모든 Bar/firmware 조건·물리 수류량·임펠러 OFF 체류·정확한 앱 저출력 전체 시간.
- 이 단회에서 실행되지 않은 failure/recovery/superseded/host-timeout 경로와 M4/M5 전체 완료.

## 다음에 이 지점을 다시 만나지 않으려면

이번 단회 권한은 소진됐습니다. 추가 write를 자동으로 진행하지 않습니다. 다음 후보는
(1) 보존 자료·코드로 group/direct 세션 순서 차이를 write-free 조사하거나,
(2) 진단 image와 현장 승인을 새로 결속한 최소 group/order 시험을 별도 설계하는 것입니다.
후자는 Pro pair까지 노출할 수 있어 이번 Bar-only 승인을 재사용할 수 없습니다. retry·timeout을
가설에 맞춰 바꾸지 않습니다. 원인 UNKNOWN을 park하면 direct 기능의 관측은 남지만 group power
실기 완료는 계속 막힙니다. 재개에는 새로 가를 질문·범위·중단/복원 수단·현장 승인이 필요합니다.
