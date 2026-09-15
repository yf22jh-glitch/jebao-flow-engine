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
