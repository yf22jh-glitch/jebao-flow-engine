# run: 2026-09-13-local-group-constant-commissioning

- 시간 기준: 파일명은 KST 기준이고 아래 timestamp는 UTC입니다.
- 목적(한 문장): software-independent 세 멤버의 Constant 최소 출력 group ON 한 번이
  plan-wide journal과 멤버별 exact scalar readback을 지키고 후속 cycle에서 재전송하지 않는지 확인합니다.
- 이 실행이 답하려던 질문: 세 장비가 ON·TimerOFF·constant·independent·Flow 31인 상태에서
  Constant 30 ownership을 arm하면 Flow frame 세 번만 보내고 power는 NO_CHANGE로 처리하는가.
- 사용자 경계:
  - repository maintainer이자 on-site approver인 사용자는 집에서 수조를 감시하며 앱 조작과
    중단 수단을 가지고 진행을 승인했습니다. 임시 default 30 배포와 prior default 50 복귀,
    group ON 한 번, 관측과 앱 복원이 범위입니다. group OFF·재publish·recovery는 하지 않았습니다.
  - 앱 precondition은 사용자가 수행했습니다. Bar의 classic/91 → constant/31은 60%p 하향과
    파형 변경이며 두 Pro의 40 → 31도 사용자 조작입니다. agent가 보낸 것은 각 31 → 30뿐입니다.
  - 복원 중 사용자는 두 번째 Pro의 비활성 manual Flow 30을 그대로 두고 TimerON으로 운전하기로
    결정했습니다. 원래 manual 40의 exact 복원은 주장하지 않습니다. 현재 schedule 출력과 latent
    manual 값은 다른 필드이며, 이후 TimerOFF로 전환하면 수동 설정 30에서 시작할 수 있습니다.
  - 사용자 요청에 따른 장비 식별 안내는 사설 채팅 범위였으며 원문 식별자는 이 기록에 싣지 않습니다.
- 실행한 commit SHA: 정책 HEAD `f4e55055b5154d7e257e05f6aedef7f572f3c6d6`.
  source는 재빌드하지 않고 기존 reviewed x86_64 image
  `sha256:785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`를 사용했습니다.
- 사용한 명령·주요 파라미터:
  - 기존 Compose preparation·stage·serve와 기존 MQTT group command 경로를 사용했습니다.
    임시 group default만 50 → 30으로 선택했으며 capability나 영구 입력 상한을 줄이지 않았습니다.
  - Constant power 30, min 30, max 75, period 10초, transition 0, enabled member 3, gain 각 1.
    qualified binding 4·receipt 12·physical identity/function pair 6, ignored 0을 유지했습니다.
  - subscribe-only observer를 먼저 붙인 뒤 group ON boolean을 QoS 0·retain false로 정확히 한 번
    발행했습니다. auditor가 production lock을 따로 획득하지 않고 coordinator의 기존 acquire를 씁니다.
- 종료 지점(stage / failure code): physical transaction `VERIFIED`, action 6, revision 13.
  command observer wrapper는 초기 MQTT pending snapshot에 잘못된 완료 assert를 걸어 exit 2였으며,
  그 wrapper 전체를 PASS라고 하지 않습니다. 별도 terminal·fresh-state 검증과 prior 배포 복귀는 완료됐습니다.

## 배포 입력과 실행 전 결속

prior Compose generation은 `2b4d91a3a662ac2aa85bacf91a03f72b`이며 Compose SHA-256은
`9e13071c09df9440575200f2d71461614a9879ffa87562f952f503ea10712fa5`입니다. prior input bundle은
파일 4개, tree `4fc622a0562862b71fde384c520c60fd464b4da58916137e84e33989092f5e19`로 따로 보존했습니다.
임시 generation은 `9dc26216607d18f360cd9e16730468a7`, Compose digest는
`ac7a651a1aca4c268a8d8378a09b4301ce1c6a71afd1abf3262186e757dc8023`, input bundle tree는
`a533e3edb7ac0d30440fb1e7e025a5ce34e7c1abddbbe14c310c5cf5c001c8b4`입니다.
qualification input은 2,429 bytes, SHA-256
`b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033`로 새 자격을 만들지 않았습니다.

임시 배포의 service stop 요청은 `08:12:52.896316Z`, runtime StartedAt은
`08:12:54.471518136Z`였습니다. 임시 기동 뒤 fresh cycle 19회 동안 armed false이고 operation 증가는 0이었습니다.
production manager는 enabled group을 RUNNING으로 구성하지만 submission은 ownership arm 전에는 열리지
않습니다. 이 관측은 해당 구성의 비무장 동작을 확인한 것이지 향후 모든 상태의 무전송 보장이 아닙니다.
앱 precondition 전에 service stop을 요청한 시각은 `08:24:52.766205911Z`, 완료는
`08:24:53.290077805Z`입니다. 사용자 앱 조작 뒤 separate authenticated read에서 세 장비 모두
ON·TimerOFF·constant·independent·Flow 31이었습니다. 보존된 재독은 `08:37:44.662Z`부터
`08:37:46.815Z`까지이고 service는 그 뒤 `08:37:47.034104899Z`에 시작했습니다.

세 target은 다음 domain-separated `qualification-target:` anchor로 exact one-to-one 결속했습니다.

| 구분 | SHA-256 identity binding | 앱 precondition 뒤 Frequency |
|---|---|---:|
| 첫 Pro | `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1` | 34 |
| 두 번째 Pro | `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa` | 32 |
| Bar | `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371` | 80 |

Bar PulseTide는 false였습니다. 두 Pro의 Frequency는 앱 precondition **뒤** 처음 확보한 값이므로,
앱 precondition 이전 Frequency까지 원복 입증한 것으로 소급하지 않습니다.

## 장비가 실제로 돌려준 값과 판정

사전 예상은 manual_flow/power × 3의 action 6, Flow write 3, power write 0, NO_CHANGE 3,
terminal revision 13, receipt mutation 0이었습니다. journal에서 확인한 결과는 전부 이 예상과 같습니다.
create revision 0 + changed action 3 × save 3 + NO_CHANGE action 3 × save 1 + terminal save 1 = 13입니다.
각 Flow expected readback은 30, baseline은 31이며 power expected·baseline은 모두 true입니다.

- group ON publish: `08:45:02.239170Z`, 발행 한 번.
- MQTT 결과: `08:45:04.243876Z`, completion_pending true, applied_identity_matches false,
  ownership_accepted true. 이는 약 2초 completion timeout 시점의 dispatch snapshot입니다.
- journal request: `group_2fb928817f064b9048ae7af4c5ecdf47`.
- journal created: `08:45:11.645913Z`; terminal updated: `08:45:15.764104Z`.
  journal 내부 span 약 4.12초, publish부터 terminal까지 약 13.52초입니다.
- terminal artifact SHA-256:
  `6395857a7b2f748e7edde48db597573811a26d57dadc35d1e504dc3b482a06e9`.

초기 MQTT pending은 장비 실패나 최종 판정이 아닙니다. 기존 정책도 MQTT 문구만으로 PASS를 만들지
않도록 정했는데 wrapper가 이 정책과 달리 최초 결과의 non-pending을 요구했습니다. terminal을 관측하고
후속 fresh cycle 최소 3회 조건을 통과한 뒤 그 assert에서 종료했습니다. 이 경로는 보존한 wrapper와
trace로 확인하며, 최종 cycle 숫자 자체를 독립 snapshot으로 보존했다고 주장하지 않습니다. guard는
해당 관측창에서 기존 operation bytes·새 terminal bytes 불변과 추가 operation 0을 확인했습니다.
이는 유한한 관측창에서의 deduplication 확인이지 무기한 반복-write 부재의 증명이 아닙니다.

wrapper 종료 때 service를 `08:45:47.144818Z`에 stop 요청했고 `08:45:47.386454Z`에 정지했습니다.
그 뒤 `08:46:07.713Z`부터 `08:46:08.879073Z`의 별도 authenticated fresh read에서 세 장비가 모두
ON·TimerOFF·constant·independent·Flow 30이었고 Frequency 34/32/80 및 Bar PulseTide false가
유지됐습니다. Flow-only field 변경은 이 관측 범위에서 확인됐으며 raw full-state 불변은 주장하지 않습니다.
group OFF·ON 재발행·generic recovery·qualification receipt 생성은 모두 0입니다.

## Durable inventory와 blob 산술

tree는 고정 journal root 및 receipt root 각각에 대해 파일의 relative POSIX path와 SHA-256(bytes)의
pair를 정렬한 뒤 `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`의 ASCII JSON으로
직렬화한 digest입니다. 전후 모두 이 방법을 사용했습니다. 다른 canonicalization의 tree 값과 직접
비교하지 않습니다. 사전 wrapper의 비호환 직렬화가 만든 값은 별도 보존하고 corrected inventory를
추가했으며, 문서 방식 재계산으로 직전 power authority run과 동일한 pre anchor를 얻었습니다.

| 항목 | 실행 전 | 실행 후 및 최종 복귀 |
|---|---:|---:|
| journal operations | 13 | 14 |
| journal blobs | 39 | 48 |
| journal files | 52 | 62 |
| receipt-root files | 18 | 18 |
| unfinished operations | 0 | 0 |

pre journal tree는 `0644759bcaa36161a4ad1c0a0874b2c0750e46752b290b177921e2a0aaddce4d`,
post/final은 `9626907b1f145327454c7bbdad0d8a697bf6ffaf3a1e2009efb3e32e3d659aeb`입니다.
기존 52개 파일의 path·bytes는 전부 동일하고 새 record 1 + 새 blob 9 = 10개만 추가됐습니다.
receipt tree는 전후 모두 `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`입니다.

신규 blob은 서로 다른 세 baseline status(Pro 452 bytes 두 개, Bar 401 bytes 하나),
Pro/Bar 각각의 Flow30·Flow31 payload(460 bytes 두 개, 409 bytes 두 개),
canonical Flow30·Flow31 readback(각 11 bytes)의 9종입니다. power payload·readback과 empty ACK은
기존 content-addressed blob을 재사용합니다. qualifier와 달리 generic coordinator는 **intent 원문을
install_blob하지 않고 digest만 기록**합니다. 따라서 intent를 신규 blob에 더하지 않으며 journal만으로
intent preimage를 복원할 수 있다고 주장하지 않습니다. 같은 request의 intent digest 비교를 통한
idempotency 결속과 원문 보존 여부는 다른 속성입니다.

empty ACK은 요청 sequence와 일치하는 control response를 받은 뒤 body가 비었다는 runtime 결과입니다.
sequence 결속 검사 자체는 별도로 보존하지 않았고 SHA-256(empty)는 실행별 판별력이 없습니다.
보존 증거는 terminal journal과 exact readback에 두며 raw wire ACK를 보존했다고 하지 않습니다.

## 앱 복원과 기존 서비스 복귀

앱 복원 완료 여부를 추측하지 않고 다시 읽었습니다. `09:18:50.690776Z`부터 `09:18:52.904488Z`에도
세 장비가 constant/Flow30/TimerOFF여서 추가 안내가 필요했습니다. `09:54:23.777471Z`부터
`09:54:25.910654Z`에는 첫 Pro와 Bar가 복귀하고 두 번째 Pro는 TimerON·latent30이었습니다.
사용자는 그 latent 값을 유지하기로 결정했고 다음 재독에서 이를 수용된 최종 상태로 검증했습니다.

`09:56:46.434124Z`부터 `09:56:47.960874Z`의 separate fresh read 결과:

| 장비 | 전원 / Timer | Mode / manual Flow | Frequency / PulseTide |
|---|---|---|---|
| 첫 Pro | ON / OFF | constant / 40 | 34 / 해당 비교 없음 |
| 두 번째 Pro | ON / ON | constant / 30 (사용자 수용) | 32 / 해당 비교 없음 |
| Bar | ON / OFF | classic / 91 | 80 / false |

두 번째 Pro는 valid schedule entry 14·invalid 0, 장비 local time 기준 active slot exactly 1이며
AutoMode sine·AutoFlow45가 해당 slot과 일치했습니다. 과거 다른 시각의 slot 번호나 출력값을 gate로
고정하지 않았습니다. 이는 그 시점 schedule ownership의 관측이지 전체 schedule image 원복 증명이 아닙니다.

보존한 prior bundle bytes를 원 generation에 복원하고 host bind source의 존재, exact prior Compose와
image를 확인했습니다. 첫 복귀 start는 stage exit 2로 runtime 시작 전에 중단됐습니다. 원 stderr·stage
log·Compose 상태는 보존했습니다. 원인은 backup의 mode 0600을 source 파일에 그대로 적용해 host UID와
다른 stage UID가 읽을 수 없었던 것입니다. 동일 이미지의 network-none read-only 진단에서 네 secret
모두 readable false를 확인했습니다. 기존 source 규격인 파일 0444·parent 0700으로 복원한 뒤 네 파일
모두 readable true를 확인했고 bytes는 그대로였습니다. private backup과 runtime config의 0600은
그대로이며, host에서 source의 기밀성 경계는 owner-only parent입니다.
이번 permission 관측은 D1p의 미보존 stderr를 대신하지 않습니다. 그 배포의 원인 UNKNOWN은
소급 설명하거나 배제하지 않고 그대로 남깁니다.

corrected start는 `10:00:31.913170Z`부터 `10:00:33.850107Z`, exit 0이었습니다.
runtime StartedAt은 `10:00:33.739767696Z`, 신규 restart counter 0입니다. 이 카운터는 연속성 증거가
아닙니다. force-recreate에 사용한 image override는 exact 보존 image를 지정할 뿐 재빌드하지 않았으며
prior Compose bytes와 input bundle bytes는 위 digest와 동일합니다. 임시 input과 실패 자료도 보존했습니다.
guard-stop 완료부터 이 runtime 시작까지의 최종 service 부재 구간은 약 74.8분입니다. 이는 펌프 OFF
시간이 아니며 HA availability가 실제 online으로 발행되기까지의 시간은 별도입니다. 앞선 준비·정지
구간까지 합친 총 서비스 부재를 이 숫자로 축소하지 않습니다.

최종 `10:02:10.488665Z` audit은 prior default50, discovery 90/90 byte exact, tombstone53 nonempty0,
command topic10·관측 message0·retained0, direct manual_flow3·power3, group1·resume3, armed false,
resume online0, supervisor1·cycle failure0·fresh cycle7, 장비6/6·global online, ignored0·unfinished0입니다.
discovery digest는 `b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`로 불변입니다.
final runtime manifest generation `2867bf8363b47f93a703e071cfedd0b0`는 prior **Compose** generation과
다른 개념이며 동일값이라고 주장하지 않습니다. post-operation journal62와 receipt18 tree는 복귀 전후
불변입니다. startup device write0은 journal-first인 local-control direct/group 경로에 한정한 결론이며,
앱 조작이나 외부 경로까지 write0이라고 확대하지 않습니다. HA registry 자체는 이번에 검사하지 않았습니다.
`10:03:14.974932Z`부터 `10:03:16.672516Z`의 별도 running fresh read에서도 위 수용된 일곱/여섯 field
집합과 secondPro의 active slot sine/45 일치를 재확인했습니다.

## 증거 등급과 보존물

- 증거 등급: (b)는 확인한 terminal journal·referenced blobs·immutable input bundle·manifest 및
  inventory의 persisted claim 범위입니다. broker/Docker/CLI·앱 조작·authenticated fresh read의 해석은
  (c)로 구분합니다. 관측 JSON을 보존했다는 이유로 raw wire frame 등급 (a)로 올리지 않습니다.
- opaque artifact id: `m4-constant-dc72902b5c22ae6fb5e491aea189c1ff`.
- UTC span: 위 각 관측·command·정지·복귀 시각으로 구간을 구분합니다. 앱 조작 시각과 실제 변경
  순간은 보존하지 않았으므로 전체 altered-regime 체류 시간을 정확히 계산하지 않습니다. 확인된
  저출력 관측은 최소 `08:36:07Z`와 `09:18:50Z`에 있고 최종 원복 관측은 `09:54:23Z` 이후입니다.
  두 관측 사이를 연속 운전 시간의 확정 하한이라고 주장하지도 않습니다. 물리 비용은 command span만이
  아니라 이 앱 준비·복원 대기까지 포함합니다. 이번 agent operation에 전원 OFF frame은 없었습니다.
- artifact digest: terminal·tree·입력 digest는 위에 적었습니다. 추가 보존 file digest는 아래에 기록합니다.

아래 이름은 위 opaque artifact 내부의 논리 파일명입니다. 관측 wrapper 출력은 보존돼 있어도 (c)의
관측 범위를 넘는 장비 원본 증거로 취급하지 않습니다. journal revision sample은 stream 안에 있으나
첫 frame 이전 rev0 snapshot을 확보했다고 주장하지 않습니다.

| 보존물 | bytes | SHA-256 |
|---|---:|---|
| post-app-fresh-read.json | 1537 | `7fe790a886ae7e8404f0ee082a145fe61b4b654ea990a1b21d10fb8278c29f0d` |
| group-audit-script-v2.py | 11005 | `44900abc7a434671850ae5ac6cad1278d715ece6a4d51c0e824deed2bb5ed1b0` |
| group-command-stream-v2.jsonl | 43460 | `8fbd1d208c54c41403447e1d622c754bebcba48c6a4c9a084c2f4ba6da11faac` |
| group-blob-reconciliation.json | 2814 | `65560f8665f093553a7d2dc9210357f02c63864dd865908485c1a5f12098abd8` |
| accepted-app-restore-read.json | 1875 | `1da554dbea8ae1df490be04939defea44bcd13ac0c1a7dac2878ca3f801a8983` |
| prior50-compose-start.stderr | 741 | `42be268f878fd3b06534a8a6ffca55a9a8234c9f6268659671d4d76f62afb4dd` |
| prior50-source-permission-diagnosis.json | 389 | `104bb2ebff2e2ba541d812279f18c8f2ed6bd06b91056d1771e7f073870c87e3` |
| prior50-source-permission-corrected.json | 385 | `24eac18804d804bdcc038fd9b1e8db9b9c0b7587fe33d33f8b59ab342985c90d` |
| final-prior50-mqtt-audit.json | 1255 | `532221243107cb223c44288608231a0d80c08b6bd7a7af5fd264df9451318da7` |
| final-running-accepted-state.json | 1875 | `89489674e92ac14499fade7282a3af80f4adef122abefc15b4dc8d2cba54bedf` |
| final-runtime-manifest.json | 2914 | `2f8dcda8870e7fb7a15c9f2486b8ae4f9a320b7ba8c5366899863253b5239064` |

## 실행 중 정정과 재발 방지

- 초기 read wrapper의 설정 로딩 전 실패 두 건은 장비 접촉·journal mutation 전에 끝났습니다.
- post-start manifest 생성보다 먼저 검증한 wrapper race는 readiness 후 재독으로 처리했습니다.
- tree serializer 불일치는 source bytes 변화로 오인하지 않고 문서 정의 재계산과 전후 같은 방법으로
  정정했습니다. 잘못된 inventory를 덮어쓰지 않았습니다.
- 첫 prepublish wrapper는 running poller와 out-of-band exclusive lease 점검이 경합해 중단됐습니다.
  publish0·새 operation0이라 ON retry가 아닙니다. stopped lease availability도 확인했지만 이는 stopped
  상태 관측일 뿐 live poller 아래 가용성 증명이 아닙니다. 감사 도구가 production lock을 획득하지 않도록
  구분하고 실제 command에서는 coordinator acquire를 재사용했습니다.
- 초기 MQTT pending을 terminal failure로 오독한 assert는 정책 위반인 wrapper 결함입니다.
  재실기나 source gate 추가로 대응하지 않았습니다. 판정은 terminal·멤버별 readback에 둡니다.
- restore source는 backup 0600과 bind-mounted source 0444를 구분해야 합니다. bytes 동일성만으로
  runtime UID의 실제 읽기 가능성까지 증명하지 않습니다. 이번에는 양쪽 permission 관측과 실패 원문을 남겼습니다.

## 이 실행으로 새로 확정된 사실

- 이 세 physical binding의 ON·manual 소유 상태에서 Constant31→30 group ON 한 번은 Flow3 frame과
  power NO_CHANGE3, terminal rev13으로 끝났고 멤버별 fresh state가 예상과 일치했습니다.
- 최소 세 후속 fresh cycle 관측창에서 새 operation과 terminal 변경이 없었습니다.
- 이 구성의 약 2초 MQTT completion snapshot은 terminal보다 먼저 pending으로 반환할 수 있습니다.
- generic coordinator의 신규 blob은 intent를 저장하는 qualifier와 달리 이번에 9종이며,
  기존52 파일을 보존한 채 record1+blob9만 추가됐습니다.
- prior service는 source permission 정정 후 exact image·Compose·bundle로 복귀했고 비무장 시작,
  discovery byte 불변과 journal-first startup write0을 확인했습니다.

## 이 실행으로 확정하지 못한 것

- Sync·Anti Phase, group OFF, direct override·resume, 장애 상태·장시간·rate-limit 경계는 미실행입니다.
- 실패한 physical group transaction의 실제 recovery와 전원 frame 경로는 이번 성공 사례로 증명하지 않습니다.
- 두 번째 Pro의 manual40 exact 원복, 앱 precondition 이전 Pro Frequency, 전체 raw state나 schedule
  image byte 불변, 물리 수류 측정은 주장하지 않습니다.
- 세 cycle 이후의 무기한 deduplication, HA registry·향후 update/reboot, native Q1/Q2는 미확정입니다.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

이 record를 먼저 commit하고 다음 별도 M4 operation의 실제 출력·동시성·복원 목표를 새로 결속합니다.
두 번째 Pro의 다음 manual baseline은 40이 아니라 사용자 수용한 30이며 Timer 상태와 그 시점 slot을
다시 읽습니다. wrapper는 최초 MQTT timeout snapshot을 완료 verdict로 쓰지 않고, tree canonicalization과
source/runtime 파일 권한의 서로 다른 계약을 유지합니다. 이번에 실행하지 않은 기능은 완료로 체크하지 않습니다.
복구·감사 경로는 bytes뿐 아니라 production의 소유권·permission·생성 방식까지 재현하고 가능한 한
기존 함수를 재사용합니다. 이번에는 실패 원문을 남긴 덕분에 읽기 불가라는 구체적 원인을 확인할 수 있었습니다.
