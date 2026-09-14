# run: 2026-09-14-m4-group-power-diagnostic-retest

- 시간 기준: 파일명은 KST 날짜이며 아래 timestamp는 UTC2026-09-14입니다.
- 결과: **시험 관측 PASS — 이 세 binding·선언된 all31 baseline·동일 member 순서의 단회 범위**.
  OFF rev10, ON rev19, record2·Flow3·power6과 ON 이후 세 complete fresh cycle을 확인했습니다.
- 종료: **사용자 수용 상태와 exact prior 서비스 복귀 확인**. second Pro의 TimerON 운전을
  유지하면서 inactive manual30을 수용했습니다. 원래 manual31까지 exact 복원한 것은 아닙니다.
  아래 복원 대기와 후속 확인을 구분합니다.
- [D1u FAIL](2026-09-14-m4-group-power-commissioning.md)의 원 예외 UNKNOWN은 유지합니다.
  이번 PASS는 과거 실패의 수정·원인 규명이나 M4 전체 완료를 뜻하지 않습니다.
- 정책·실행 HEAD `245cc94`, [D1w 계획](../local-group-power-diagnostic-retest-plan.md).
  사용자는 진단 image 임시 배포와 세 대 OFF/ON 단회 범위를 각각 공개한 질문에 승인했습니다.
  전부 또는 일부 OFF 잔류, recovery의 재-OFF 가능성, 현장 직접 복원 필요와 재현 미보장을
  먼저 알렸습니다. 앱 준비 완료 회신 뒤 독립 read를 통과한 다음에만 명령을 보냈습니다.
- 실행 중 source/tests·native ASYNC·timeout·retry·생산 gate 변경은 없습니다.
  재OFF·재ON·corrective direct write·recovery·새 실기는 실행하지 않았습니다.

## 준비와 배포

prior image는 `785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`,
diagnostic image는 `847c51edb112abb9717c7a577666d5d46842dc78e3a470e40e8386b96ba82d0c`입니다.
기존 diagnostic image를 사용하며 재빌드하지 않았습니다. 138개 source 파일의 결속과 prior 대비
coordinator·failure_diagnostics 두 파일 차이, Python·의존성 동일을 다시 확인했습니다.
schema digest는 양쪽 모두
`fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`입니다.
최근 [테스트 후속 처리](../full-suite-failure-followup.md)의 full2589 PASS는 테스트 검증이며,
실기 실패의 원인을 규명하거나 새 production source를 배포한 근거로 사용하지 않습니다.

prior Compose generation은 `2b4d91a3a662ac2aa85bacf91a03f72b`,
candidate generation은 `811e4865ad65905509ef9c602229c0ae`입니다. image와 generation을 함께
식별합니다. config delta는 Constant default.power50→30 하나입니다. min30/max75/period10/
transition0과 second→first→Bar 순서, phase0/180/90·gain1·invertfalse·role은 유지했습니다.
원 Compose2개·bundle4개를 no-clobber 보존하고 production source0444/parent0700/owner/link1을
확인했습니다. 두 image의 network-none projection은 discovery90 byte exact였습니다.

prior stop 요청06:42:53.536961→정지54.199619, candidate StartedAt06:42:56.568653293Z,
manifest `c505b4c645030e447afe2f858d264275` 재생성06:42:57.627954 순서입니다.
첫 배포 exit0, 06:43:14.690344 postcheck는 qualified/unarmed·fresh6·failure0·command0·
unfinished0·override0·discovery90 exact와 기존 journal109/receipt18 불변을 확인했습니다.
cycle counter1 snapshot은 세 cycle hold가 아닙니다.

앱 준비 전 stop 요청06:43:14.965961→정지15.191435 뒤 독립 read06:43:21.163052–22.816719는
first ON/constant40/TimerOFF/F34, second ON/constant/latent31/TimerON/F32,
Bar ON/classic91/TimerOFF/F80/Pulsefalse를 확인했습니다. 모두 independent입니다.
second의 그 시점 sine60이 active slot exact-one·valid14/invalid0과 일치했습니다.
이 slot 이름을 복원 목표로 고정하지 않으며 slot 개수만으로 schedule 전체 bytes 불변을 주장하지 않습니다.
앱 준비는 first40→31, Bar classic91→constant31, second의 그 시점 schedule 출력60→manual31
전환을 포함합니다. 앱 변경 순간과 앱 전송 횟수는 보존하지 않았습니다.

사용자 완료 회신 뒤06:46:27.915537–29.997432 독립 read에서 allON/TimerOFF/constant/
independent31, F34/32/80·Bar Pulsefalse를 확인했습니다. Pro의 실제 MAC은 사용자 직접 안내에만
사용했으며 이 기록과 Agent Bridge에는 anchor만 사용했습니다. 별도 현장 재확인 단계를 중복
추가하자는 검토 제안은 철회됐고, 기존 승인과 사용자의 계속된 준비·진행 회신으로 실행했습니다.

## 실행 관측

anchor는 ASCII `qualification-target:` + physical_identity_key의 SHA-256입니다.

| 역할 | anchor |
|---|---|
| second Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` |
| first Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` |

재기동 StartedAt06:49:21.668614365Z, manifest `3e3d23a6a82afe5d04d275ed9dd6069c`
재생성06:49:22.639303 뒤 readiness·fresh6·unarmed·qualification·tree를 재확인했습니다.
독립 preflight read06:49:49.617285–51.835086도 all31이었습니다. runtime logs follow를
첫 publish 전에 열고 stop 뒤 final logs를 따로 보존했습니다. 명령은 기존 group MQTT
OFF1·ON1, QoS0/retainfalse이며 OFF 이전 별도 arm ON은 없습니다.

| 단계 | intent | journal created → VERIFIED updated | action / revision | 확인된 write |
|---|---|---|---|---|
| OFF | 06:49:51.849250 | 06:50:01.274329 → 04.921937 | 3 / 10 | power3·Flow0 |
| ON | 06:50:11.720239 | 06:50:18.071173 → 25.630399 | 6 / 19 | Flow3·power3 |

OFF는 second/first/Bar power true→false 세 action입니다. ON은 second Flow31→30·power
false→true, first Flow31→30·power false→true, Bar Flow31→30·power false→true의 직렬 순서입니다.
전 action changed/VERIFIED이며 NO_CHANGE와 diagnostic은 없습니다. ACK는 기존 empty blob입니다.
OFF revision은3×3+terminal1=10, ON은6×3+terminal1=19로 사전 등록과 일치합니다.
각 record의 첫 보존 표본은rev1이므로 첫 frame 이전rev0 관측을 주장하지 않습니다.

OFF terminal 뒤 독립 read06:50:09.975781–11.703721에서 세 대 OFF31을 확인했습니다.
그 종료0.016518초 뒤 ON intent를 발행했습니다. OFF 상태의 세 cycle hold는 하지 않았습니다.
ON terminal 이후 full fresh cycle4/5/6에서 세 대 ON30·TimerOFF·constant·independent와
override0, 추가 record0을 확인했습니다. 장비별 전체 state topic의 새 non-retained batch,
terminal 및 이전 표본 뒤 diagnostic stamp, online availability 종료를 요구했습니다.
중복 function에 해당하는 topic도 포함하며 retained scalar·command echo·pending을 verdict로 쓰지 않습니다.

hold 완료06:51:02.047388, guard06:51:02.067315, stop 요청06:51:02.069686,
정지06:51:02.400522Z입니다. 첫 OFF intent→guard70.218065초로180초 관측 상한 안입니다.
observer/host exit0, retry·superseded·recovery0이며 세 번째 record는 관측되지 않았습니다.
빠른 stop은 in-flight frame을 물리적으로 불가능하게 하는 gate가 아닙니다.
정지 후 독립 read06:51:22.528366–24.078189는 allON30·F34/32/80·Bar Pulsefalse였습니다.

OFF record 폭3.647608초, ON record 폭7.559226초입니다. journal 기반 중첩 구간은
OFF.updated→ON.created13.149236초(안쪽), OFF.created→ON.updated24.356070초(바깥쪽)입니다.
이는 software timestamp 경계이며 멤버별 임펠러 정지·수류량·물리 동시성이나 monotonic 계측을
뜻하지 않습니다. publish 간격을 물리 OFF 체류로 바꾸어 쓰지 않습니다.

## Journal·blob reconciliation

pre31 operations/78 blobs/109 files → post33/82/115입니다. 새 record2와 blob4로 file6이
증가했고 이전109개 파일은 byte-hash 불변입니다. receipts18도 불변입니다.

| record | request id | bytes / revision | SHA-256 |
|---|---|---|---|
| OFF | `group_e71c5d1e8dbcda3ad16ef73d13d5c1fd` | 3381 / 10 | `38da12542f64d84fe1d5a772546b23bbeedf9e096ed293ec016b1f3bfb5888d7` |
| ON | `group_66838849a359124efa7d9c74b8856cbb` | 6485 / 19 | `39445c36795ee059a931f21b90acf181e6f6cd8de82f0a4e1fb956c5c0305842` |

여섯 baseline capture 중 신규4는 모두 Pro baseline_status452B입니다. ON record에서는
각 장비의 Flow/power action이 같은 baseline_status를 공유합니다. 신규 non-status blob은0입니다.

| capture | 신규/재사용 | baseline_status SHA-256 |
|---|---|---|
| OFF second | 신규 | `0cdaf03f0aa07ca4c6a003c293139778692bf416a5765b1cd49d705c7c205197` |
| OFF first | 신규 | `7dda719275ec5d2992f2419f3afb7418c371c838b6b9b9f5f3ff40df0b42d78a` |
| OFF Bar | 재사용 | `4eb855d8e1e196cfa03208d236fe63f1b4a62d734300a624f2357b6a93d0a690` |
| ON second | 신규 | `3b4da2b1157f5f8325a4c00e8edb353c5b5e0a8e9e6f312ad2d2fc419dc4932d` |
| ON first | 신규 | `adea231cd8e7d0b199062a0fde5761e50e870623dc9c8e22d09229ee4d41e8f1` |
| ON Bar | 재사용 | `dfa8b131745e2f6d755288d3a13fb12ff918d8372ea580bb78f61d9607a77363` |

Pro status bytes가 다른 원인은 확정하지 않았습니다. schedule 위치나 내부 counter로 설명하지
않습니다. 선언된 baseline 필드·payload·member 순서 일치를 전체 raw state·환경 동일로 확장하지 않습니다.
Bar ON record의 baseline_status는 D1u와 D1v에서 비교한 `dfa8b131…7363`과 같습니다.
따라서 D1u 실패와 이번 성공에서 Bar의 저장된 준비 status bytes는 같았습니다. 전송 순간의
내부 상태나 transport·wire·환경까지 동일하다는 뜻은 아닙니다.
두 record의 intent digest preimage는 보존 blob 집합에 없습니다. digest만으로 원 intent를
복원하거나 raw packet을 확보했다고 주장하지 않습니다.

post journal tree SHA-256은
`36d3b1da823172ed445a6cf4a1f0a7b3d720f28d870adef40108c7747042dcc6`, receipts18은
`9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`입니다.
discovery90은 `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`입니다.

## 정리 요청과 복원 대기

사용자의 다음 단계 전 미사용 image 정리 요청으로 Jebao 이미지35개를 건별 삭제했습니다.
현재 diagnostic·exact prior·안전 artifact holder·기존 stopped container 참조 image4개는
보존했습니다. containers27·volumes41의 집합과 상태, journal115·receipt18은 전후 불변입니다.
다른 프로젝트·공용 build cache·volume·container 삭제나 일괄 prune는 하지 않았습니다.
Docker 보고 image 수71→36, image category12.87GB→11.84GB이며, 삭제 image의 가상 크기
합계를 실제 확보 디스크 공간으로 주장하지 않습니다. 삭제 이미지 재사용에는 별도 재구성이 필요합니다.

이후 앱 복원 첫 read06:58:46.242617–48.008328은 세 대 ON/constant30/TimerOFF와
F34/32/80·Bar Pulsefalse를 확인했습니다. `all_admitted=false`이고 journal/receipt는 불변입니다.
이는 시험 FAIL이 아니라 복원 미완료입니다. 사용자에게 first40/TimerOFF, second는 TimerOFF에서
manual31로 바꾼 뒤 TimerON, Bar classic91/TimerOFF 복원을 요청했습니다.
exact prior 복귀 host는 준비·compile만 마쳤으며 이 시점에는 실행하지 않았습니다.

## 증거와 현재 한계

private artifact id: `m4-group-diagnostic-77210924d3432602d0684a50d3625392`.
아래 파일은 실제 보존 bytes·digest를 확인한 (b) 자료입니다. journal·structured observer claim이며
원본 wire frame (a)나 독립 유량 측정으로 승격하지 않습니다. 사용자 앱 조작 자체는 (c)입니다.

| artifact | bytes | SHA-256 |
|---|---|---|
| command stream v1 | 151502 | `c304d8a315156dd6c453a78b61101fc74d843f67924a5b600238a9e2225d9c0f` |
| execution summary v1 | 12732 | `9a1baf7228d0bf666a9dac0fdfb3d4931ec0bccab32979be33f6bcdd1f08604e` |
| observer v1 | 19332 | `6ff3702f799c14b08630f20cbda31a71f695c95d1cc5c6a513d2847c5dc76340` |
| host v1 | 11198 | `0192d562f68d0a3494f5b7157b037490d5a1b2cd6e4b7873721c9301595752dd` |
| reconciliation v1 | 1839 | `711487b8c9c32b80dac7d125cf59b1cfb426aebc6cf81d0ed12cbb9422046610` |
| app restore read01 | 1726 | `73db38f4fab6252d09ec2ab53e4d2e11df8e135289d3ee5cafafb099c64c5c58` |

runtime follow stdout/stderr와 final stdout/stderr 네 파일은 모두0B이며 각각의 digest는
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`입니다.
정상 성공에서 failure event가 없다는 것은 production 실패 때 진단이 발동함을 검증한 결과가 아닙니다.

새로 확정된 것은 이 선언 조건·동일 member 순서에서 group 경로가 한 번 완주했고,
ON 후 세 complete fresh cycle 동안 추가 record 없이 allON30을 관측했다는 사실입니다.
group 경로는 D1u FAIL1·이번 PASS1이며 D1v direct PASS는 별개 경로입니다.
원래 실패 원인, sequence mismatch·세션 대기·환경 요인의 관여, 향후 재현 여부는 미확정입니다.
timeout/retry 변경이나 상시 진단 배포, 다음 실기의 권한은 이 결과에서 따라 나오지 않습니다.

## 앱 복원 후속 확인과 사용자 수용

사용자 완료 회신 뒤07:11:45.272460–47.164539 read02에서 first ON/constant40/TimerOFF/F34와
Bar ON/classic91/TimerOFF/F80/Pulsefalse가 원 선언값에 맞았습니다. second는 ON/TimerON/F32,
그 시점 sine60이 active slot exact-one·valid14/invalid0과 일치했지만 inactive manual Flow는30이었습니다.
원래 manual31까지 요구한 reader가 `all_admitted=false`를 냈고 에이전트는 다시 TimerOFF→31→
TimerON을 요청했습니다. 사용자는 스케줄 운전 중 이전 수동값을 문제 삼지 말라고 정정했습니다.
에이전트는 현재 적용값과 비활성 저장값을 구분하지 않고 불필요한 조작을 요청했음을 인정했습니다.

사용자가 그 상태로 계속 진행하도록 지시한 뒤, **원래 manual31 exact 복원과 현재 manual30 수용을
분리**해 acceptance artifact를 보존했습니다. reader/return host v2는 second30을 명시적으로
대조하되 TimerON·현재 slot 일치·다른 원 필드·identity·journal·receipt 조건을 유지했습니다.
read02 실패를 덮어쓰거나 출력값을 조작하지 않았습니다. 이는 operator 복원 목표의 명시적
수용이며 production writer·schema·출력 범위·rollback gate 변경이 아닙니다.
v1 복귀 host는 실행하지 않았고 v2가 유일한 복귀 시도입니다.

07:15:31.347185–33.070677 독립 read03은 수용 조건을 모두 통과했습니다. second의
현재 sine60/TimerON·slot exact-one을 재확인했고 수동31의 exact 복원 여부는 false로 함께
기록했습니다. first·Bar는 위 원 선언값이며 journal115/receipt18은 불변입니다.
이후 복원 확인에서 과거 sine60 자체를 고정 목표로 삼지 않습니다. 추가 device command는0입니다.

## exact prior 서비스 복귀

07:15:38.417555에 복귀를 시작했습니다. 보존한 원 Compose bytes와 bundle4개, owner/mode/link를
검증하고 기존 Compose 파일2개를 명시해 no-build/no-pull로 한 번 복귀했습니다. 첫 시도 exit0,
실제 실행 image는 prior `785cf449…88d9`, generation은 `2b4d91a3a662ac2aa85bacf91a03f72b`입니다.
StartedAt07:15:41.223541568Z 뒤 manifest `bc387601253683898a528976e6655014`가
07:15:42.236306에 재생성됐고 readiness가 통과했습니다.

07:15:43.269138–59.577254 read-only postcheck는 Constant50·qualified/unarmed·override0,
supervisor1·failure0·ignored0·unfinished0·command0·retained command0, fresh6·online6,
discovery90 exact와 journal115/receipt18 불변을 확인했습니다. first40·Barclassic91,
second는 TimerON/저장manual30으로 사용자 수용 조건에 일치했습니다. 이 MQTT 숫자30은
스케줄 실출력60과 다른 manual register이며 새 group 명령이나 현재 목표30이라는 뜻이 아닙니다.
서비스는 비무장이고 이번 복귀가 Constant50을 세 대에 재전송한 것은 아닙니다.
prior image에는 새 failure event 구현이 없으며 상시 진단 배포는 별도 승인 사항으로 남습니다.

앱 준비 전 서비스 정지부터 시험 재기동까지 약6.11분, 시험 종료 정지부터 prior 재기동까지
약24.65분입니다. 이는 서비스 부재 구간이며 펌프 OFF 시간이나 연속 altered-regime 시간이
아닙니다. 앱 변경 시점을 보존하지 않아 실제 운전 변경 구간을 정확히 유도할 수 없습니다.

| 후속 artifact | bytes | SHA-256 |
|---|---|---|
| app restore read02 | 1904 | `48d9122e9a5294e9e8c08dbfbdcd168653f2439209df9102fe6059d3e23e5fc6` |
| app restore read03 | 2026 | `40d558735b276bae9413fabb1419bfbafedb3c9f5b8f5500aabe389584bce716` |
| accepted restoration v1 | 516 | `3f21e92a49b58cadab2b7bca3b99044fca56b2048cd2d3b88a9884b3a237c2d5` |
| prior return success | 305 | `770a61de9cfe4dc6ecc26568f994d0a44fef6f904cb119c56ef18329f8dd53bd` |
| prior return postcheck | 2923 | `88f9fef1800b980ff4700eba2ffd7a05bb0e3066a60a8ad2fc32728727c314a8` |

위 파일도 실제 bytes·digest를 확인한 (b)입니다. 사용자 수용·현장 앱 조작 발언은 (c)로
구분합니다. 실기 결과 커밋으로 D1w 단회 권한은 소진되며 새 native Sync/Async 시험으로
재사용하지 않습니다.
