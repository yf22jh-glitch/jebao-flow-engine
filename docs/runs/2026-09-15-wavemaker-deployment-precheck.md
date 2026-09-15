# run: 2026-09-15-wavemaker-deployment-precheck

- 목적: 새 Pro lifecycle 이미지를 운영에 적용할 때 기존 제어 자격과 복귀 상태를 보존할 수 있는지,
  현재 운영 입력의 격리된 읽기와 source의 자격 계약으로 확인한다.
- 사용자 요청: “오케이 그럼 배포하자”. 이를 새 장비 명령·재qualification·초기화 승인으로
  확대하지 않았다.
- source: `0efd68c153aa2ffa73c9a460fe38cad61a153a63`, 시작 worktree clean.
- 종료: **운영 전환 전 정지 — 배포 미완료**. 새 이미지 빌드·서비스 교체·재시작·장비 명령은 0이다.
  source bundle과 미실행 빌드/검사 파일만 private 위치에 준비했다.

## 실제 확인

현재 운영 container를 조회했을 때 이전 exact image
`785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`이고 stopped였다.
이 Docker 출력은 별도 원본 파일로 보존하지 않아 (c) operator observation이다.
새 서비스로 대체하거나 원 서비스를 다시 시작하지 않았다. 이전 first Pro 앱 OFF와 원 서비스
복귀 의무는 이 조회나 이번 배포 요청으로 닫지 않는다.

기존 exact image를 일회용 container에서 `network none`, read-only root, capability drop ALL,
기존 data/safety volume **둘 다 read-only**로 실행했다. 임시 디렉터리만 별도 tmpfs이며 UID와
보호 root·receipt 권한은 변경하지 않았다. 장비·브로커에 접촉하지 않고 기존 production
qualification loader/verifier/catalog로 현재 입력을 읽었다.

- 보존 산출물: opaque id `WM-DEPLOY-PRECHECK-sm0Lke88`, inventory-v4.
- 시각: `2026-09-15T07:00:26.388754Z`의 시점 inventory. 수집 span은 별도 계측하지 않았다.
- 등급: **(b) 보존 구조화 inventory**. 장비 frame이나 현재 펌프 상태의 증거가 아니다.
- 산출물 SHA-256: `056ea6930b7419c0f8c12949753e7972519b6144743f2695d878fd78461f7ba3`.
- 장비 binding 6, 물리 신원 sorted-set SHA-256:
  `5c0a9b8943832df7f8fc30472a6b57af39a5ad3b997fce34dca56b6a80da4a19`.
- qualification **4 binding / 6 device-function pair**: manual_flow 3, power 3; ignored 0.
- qualification SHA-256: `b38ab22c93b47f6d1a6d6cd2dcc1ed8307a0f265f1d427df88880a526d91b033`.
- schema는 모두 `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`.
- data 30 files, tree `85b5772897df5848e18102912c90b21a88f1e35992a33a7f99d66c80602e4e5b`.
- local-control journal 115 files, tree `36d3b1da823172ed445a6cf4a1f0a7b3d720f28d870adef40108c7747042dcc6`.
- qualification receipts 18 files, tree `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`.
- native-linkage / temporary-schedule / schedule-linkage / emergency-stop 네 blocking path는 부재.

위 tree는 sorted `(relative path, SHA-256(bytes))`의 compact JSON digest다. 한 번의 inventory이며
전후 비교나 새 장비 상태 검증을 했다고 주장하지 않는다.

## 전환을 멈춘 근거

새 source의 schema는 `b487319e6479772c120e9c1f48feae918d3e223880d364f4fa64c28251360296`이다.
`build_local_control_catalog`는 schema 불일치 binding을 `qualification_not_current`로 제외한다.
따라서 기존 입력을 그대로 새 코드에 연결하면 **4 binding이 제외되고 기존 직접 제어 6개가
자격을 잃는다**. 이는 현재 입력과 정적 코드에서 도출한 전환 예측이며, 새 이미지의 기동 결과나
HA에서 실제 제어가 제거됐다는 관측이 아니다. 독립 검토의 ignored 6 표현은 binding과 pair를
혼동한 것이므로 여기서는 4/6을 구분한다.

또한 새 native attach는 두 Pro 각각 power·schedule_enable·linkage_role·manual_mode·manual_flow·
manual_frequency·feeding_duration·custom_wave_frequency·schedule 자격을 요구한다.
**기존 Flow/power 재qualification만으로 새 native 기능이 활성화되는 것도 아니다.**
구현된 전송 실행기와 현재 장비의 실행 자격은 별개다. 기존 영수증의 schema를 고치거나,
새 검증을 했다는 영수증을 오프라인에서 만들어 우회하지 않았다.

다음 선택은 새 schema의 기존 제어 및 native 기능 실장 검증·자격 발급까지 범위를 확대하는 것,
또는 기존 제어의 비활성화를 명시적으로 수용한 배포, 또는 배포 보류다. 실장 검증은 실제
장비 설정 변경과 자기 원본 복원을 포함하므로 소프트웨어 배포 승인만으로 실행하지 않는다.
보류 시 새 native 기능은 운영에 열리지 않고 기존 서비스도 이전 stopped 상태로 남는다.

## 작업 오류와 보존

SSH가 간헐적으로 exit255로 끝났다. 실패가 있었던 명령은 조회뿐이며 이후 조회를 다시 했다.
첫 inventory는 manifest 파일명 추정 오류, 다음은 read-only container의 tmpfs 누락으로 실패했다.
정확한 `install.json`과 일회용 tmpfs를 사용해 정정했고 production 코드·검사·권한은 바꾸지 않았다.
한 wrapper의 zsh 예약 변수 `status` 대입도 실패했다. 실패 로그와 이후 성공 로그를 함께 보존한다.
source bundle SHA-256은 `76200c0f9c43aab7d400f627c29e294e0a3fd739099983516d584c1a602107e0`이다.

**새로 확정된 사실:** 현재 보존 자격은 여전히 구 schema의 4 binding/6 pair이므로 새 구현으로의
그대로 전환은 기존 제어를 유지하지 못한다. 장비 기능·새 atomic 명령 수용·원복 성립은 이번에
검증하지 않았으며 소프트웨어 구현 완료를 운영 활성화 완료라고 보고하지 않는다.

## 2026-09-15 07:44 UTC 추가 — 이미지 완료, 실제 Bar 준비 상태의 범위 경계

이 절은 앞선 장비 접촉0 기록 이후의 추가 관측이다. 사용자의 “ㅇㅇ 진행해줘” 승인으로
`30931ee` 계획 및 `28d885c` 자격 발급 연결을 구현했다. [구현 검증](../wavemaker-qualification-implementation.md)은
최종 full2865 PASS/5 FAIL이며 전체 PASS가 아니다. source 수정 이후 새 장비 write는 아직0이다.

새 이미지 `sha256:985d5bacbb8c39ef94ec65ed7f3431afc1a993d5a645168c79c393ad7e6c30ac`를 만들었다.
source는 `28d885c3257f9311e805100c75e1a25a6a777b31`, source145개가 exact이고 새 qualification CLI,
native 다섯 CLI, 외부 Sync/Async plan432B 및 Bar384B 미리보기를 격리 대조했다.
schema는 `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`이다.
base는 `edbbbdaaed5d92c0e5729b8f5f3e91842655b2c255006dc4611ac7b1262a5c58`이며
패키지 집합 digest `7dec254f01f2cc9efd2c6838137f17dab506bd9a50340282a61c5b1a0a11e16d`가 같다.
새 console entry point는 로컬 격리 builder의 wheel을 이미지 안에서 no-index/no-deps로 설치했다.
빌드 RUN의 network-none을 metadata 조회 전체의 네트워크 차단으로 확대하지 않는다.
root는 이미지 패키지 설치 단계에만 사용했고 실행 USER·보호 root·receipt 권한은 변경하지 않았다.

운영 config는 변경하지 않고 현재 config에서 observer 사본과 새 manifest를 별도 private 위치에
생성했다. 원 운영 config SHA는 `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`로
전후 일치한다. 새 manifest는 `2a3539ef31d16e57e6371433c298a394471fdf8e43491ea06e8a8aab3d1022fd`다.
입력 준비는 network-none, hardware-safety mount 없이 수행했다. 이후 기존 resolver·lease·
reply-only session으로 세 장비를 두 번씩 읽었으며 control payload 전송 경로는 호출하지 않았다.

### 실제 raw 관측 (a)

UTC span은 `2026-09-15T07:44:48.841892Z`–`07:44:54.116204Z`, reply action3이 6/6이다.
각 표본의 wire/status/full image를 별도 보존했고 두 회 읽기의 controls와 image가 일치한다.
각 장비의 보고된 fault7개는 모두 false다. 표의 Auto는 그 시각의 보고값이지 복원 목표가 아니다.

| 장비 | 현재 controls | 현재 Auto | 전체 image SHA-256 |
|---|---|---|---|
| second Pro | ON/TimerON/independent, constant30/F32, Feed10/custom5 | sine60/F15 | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` |
| first Pro | ON/TimerON/independent, constant40/F34, Feed10/custom5 | pulse80/F70 | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` |
| Bar | ON/TimerOFF/independent, **classic91/F80**, Feed10, FeedSwitch/PulseTide false | inactive random100/F100 | `8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea` |

물리 identity SHA-256은 second `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`,
first `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
Bar `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`다.
Pro432B 두 원본은 이전 보존 SHA와 같다. 이 사실로 8개 controls 전부가 과거와 같다고 쓰지 않는다.
Bar 장비 시각은 `2024-06-07T22:59:44`로 보고됐지만 수정하지 않았다. 이번 scalar 준비 판정의
입력이 아니며 오래된 날짜가 control 동작의 원인이라고 주장하지 않는다.

### 진행을 멈춘 이유와 대안

기존 Bar manual_flow/power producer는 **constant·Flow45 이하**를 요구한다. 현재 classic91은
두 조건과 다르다. 이는 기존 qualification 계약 불일치이며 장비 고장이나 새 안전 상한 선언이 아니다.
기존 자격은 Flow/power뿐이므로 그 자격으로 Mode를 constant로 바꾸는 경로는 없다.
`30931ee`의 Bar 준비는 기존 qualified scalar로 가능한 경우에 한정되어 있어 새 Bar bootstrap을
묵시적으로 추가하지 않는다. Pro qualification44도 아직 시작하지 않았다.

대안은 (1) 사용자가 앱으로 Bar를 Constant40으로 준비하고 자기 검증 뒤 기존 Classic91/F80을
앱에서 복원하는 것, (2) 기존 상태를 소유하는 Bar 준비·원복/first-write 경로의 구현과 검증 범위를
별도로 정하는 것이다. 두 경로 모두 현재 시간을 바꾸거나 Bar 시간표/native write를 자동 포함하지
않는다. 보류 중 새 image는 운영되지 않고 기존6개 제어를 새 schema에 위조 승계하지 않는다.
Pro만 전환하고 Bar2개 control을 잃는 선택도 사용자 결정 없이 하지 않는다.
앱 준비는 Bar의 설정값91→40 변화와 나중의40→91 복귀를 포함한다. 이를 실제 유량 비율로
바꾸어 말하지 않는다. 기존 first Pro 앱OFF 외에 Bar 준비/복원 두 수동 단계가 추가된다.
384B 원본 보존은 복원 목표를 확보한 것이지 Bar schedule write 권한·수단을 확보한 것이 아니다.

새 qualifier/native attach/detach 실행0, 장비 control/schedule/role write0, 앱 조작0,
운영 generation 교체0이다. 원 서비스는 사후 inspect에서도 같은 exact image의 exited 상태다.
이전 first Pro 최종 앱OFF/원 서비스 복귀 의무는 이번 read로 닫히지 않았다. 신규 실장 승인은
실행하지 않았으며 이 사전 대조를 qualification 성공이나 실행 권한 소진으로 세지 않는다.

### 추가 산출물 결속

artifact id는 `pro-qualification-20260915.51uFgd6n`이다. 원격 private28파일(입력3+baseline25)을
다시 읽어 tree `8eeb788706dd02ece39aa0d3b5efc55585fe15e651b60801d9eda7818b2c0bd3`를 확인했다.
baseline/result SHA는 `695ba4ac967d920627608f5b69b39034e933f2253249873b4b13031f6315323a`다.
이는 관측 여섯 표본이며 연속 감시가 아니다. 보호 namespace와 UID의 권한 완화는 없다.
SSH255는 base 조회와 입력 준비에서 각각 한 번 있었다. 입력 준비는 원격 산출 디렉터리 부재를
read-only로 확인한 뒤 실행했고 v1/v2 원 출력을 모두 남겼다. 장비 write 재시도가 아니다.

| 산출물 | SHA-256 |
|---|---|
| `source.bundle` | `47b4fbbc87c675fb4c56a5733a6535a86498f8148b4227a64979663a1c128ab6` |
| wheel | `c1941688567a36791de320528c1e0abc03a292b7af2b74ee5e71482776d571f8` |
| `image-build-v1.log` | `d67a7e65e0addeb27a54fd086559b23ffd7dc9f421ed9030e17a7c0c4d8b4828` |
| `image-verify-v1.log` | `c76cf71d11fc4d95d3a6db213b494eddfb0abae893aaefb9c62974359415dd2c` |
| `prepare-inputs-v2.log` | `3174a125445a2c42eb66d26da18a78d36e28dc5dd177d7bbca08697a38c04bbc` |
| `fresh-baseline-v1.log` | `8117b833d80e4e9a84a793a189ff73386ea0c1a744e5b2bc02a26e381323742a` |
| `prepare_inputs.py` | `5dcbfb47f980b6faa87904179ed12f5a65ed0d75389f66cd0a2b92ca77258f11` |
| `fresh_read.py` | `721baba977e643a73d3dd88702559d5f80198f1ac0b9764ad8b8e43b58db597a` |

**새로 확정된 사실:** 새 source/image/schema·CLI가 결속됐고, 현재 Bar의 classic91 상태는
승인된 기존 자격 producer의 준비 조건과 다르다. 기능 검증을 실패로 기록하지 않으며 준비
범위 선택이 필요하다는 결과로 남긴다.
