# Aquarium 자동 준비·최초 출력 복귀

사용자가 현재60에서 임시 시험하고 원복하는 범위와 필요한 자동 준비·복귀 구현을 승인했다.
선행 읽기 전용 결과는 [사전 확인](runs/2026-09-16-aquarium-preparation-precheck.md)에 먼저
기록했다. 이번 구현은 **오프라인 검증 단계**이며 실제 장비 자격·배포 완료가 아니다.

## 명시 선택과 소유 범위

기존 `jebao-flow-local-qualify run`에 `--preparation-speed`를 지정한 Aquarium
`motor_speed` 또는 `feeding_duration`만 새 producer를 선택한다. 옵션 없는 기존3-action
producer와 그 현재속도≤45 조건은 변경하지 않는다. 다른 제품/기능에는 사용할 수 없다.

예시는 실행 승인이 아닌 인자 설명이다. 실제 대상·template·config·manifest·output과
`--write-enabled`는 기존 CLI와 같이 필요하다.

```text
jebao-flow-local-qualify run --function motor_speed --preparation-speed 40 <current inputs>
jebao-flow-local-qualify run --function feeding_duration --preparation-speed 40 <current inputs>
jebao-flow-local-qualify recover --operation-id <failed operation id> <current inputs>
```

각 기능은 별도 한 operation이며 **4 action 전량과 최초 raw를 fsync한 뒤** 첫 명령을 보낸다.

| 단계 | speed 자격 예시 | feeding-duration 자격 예시 | 자격 영수증 |
|---|---|---|---|
| 저출력 준비 | speed60→40 | speed60→40 | 없음 |
| 기능 시험 | speed40→39 | FeedTime10→1, speed40 유지 | 1장 후보 |
| 시험값 복귀 | speed39→40 | FeedTime1→10, speed40 유지 | 1장 후보 |
| 최초 출력 복귀 | speed40→60 | speed40→60 | 없음 |

정상 완료는 기능당4send/2receipt, 두 기능이면8send/4receipt다. 먼저 실행한 operation의
terminal과 원복을 확인한 뒤 다음을 실행해야 한다. 전역 미완료 차단은 그대로다.
후보 두 영수증도 **마지막 최초 출력 복귀까지 VERIFIED가 되기 전에는 발급하지 않는다**.
feeding-duration 영수증에 준비/복귀용 motor_speed 자격이 섞이지 않는다.

## 최초 상태와 시험 상태를 구분

새 intent의 `original_motor_speed`는 최초 raw의 실측값이다. nested `trial`은 준비 완료 뒤
시험할 저출력 상태의 선언이며, 이 선언을 최초 raw인 것처럼 만들지 않는다. 네 action의
`baseline_status_sha256`은 모두 같은 실제 최초 raw를 가리킨다. 실제 준비 명령 이후
readback에서 저출력 상태를 검증한 뒤에만 시험으로 진행한다.

원본은 앱 범위30..100과 **기존 config 범위** 안이어야 한다. 준비는 원본 이하이면서 기존
first-write 상한45·config/app 범위의 교집합 안이어야 하고, speed 시험은 기존1단위 규칙이다.
45를 넘는 forward는 최초 raw에서 보존한 **자기 원본 speed로 돌아가는 마지막 명령뿐**이다.
임의 복귀값 입력이나 상한 변경 옵션은 없다. config max50인 장비의 원본60은 복구 권한을
얻기 전에 거부되며, 이를 위해 config 상한을 자동 확장하지 않는다. FeedTime0도 정상화하지 않는다.

60은 이 사용자의 직전 관측 예시이지 코드에 박힌 원본값이 아니다. 실제 operation의 최초
fresh 값을 범위 안에서 소유·복구하며, 과거60과 같아야 한다는 gate는 넣지 않는다. 실기 직전
관측이 승인된 물리효과와 달라졌다면 운영 범위를 다시 정해야 한다. 준비값이 이미 원본과
같은 경우도 명시 옵션은4회 전송한다. 저출력 준비가 불필요하면 기존3-action 경로를 쓰면 된다.

ON/TimerOFF를 전제로 전원·Timer·Mode·시간표 명령은 만들지 않는다. 급이시간 시험 전에는
운영자가 **실제 급이 OFF**를 확인하고 동시 앱/HA 조작을 멈춰야 한다. 기존 scalar envelope가
급이 OFF까지 증명한다고 주장하지 않는다. 사용자 입력·시각·Auto값을 과거 snapshot과
동일하게 유지시키는 새 gate는 추가하지 않았다.
transport Mode는 최초 관측에만 기록하고 변경·검증·복원하지 않는다. 모든 장비 필드가
완전히 원복됐다고 주장하지 않고, 쓰기를 소유한 speed와 대상 기능의 값만 복원 계약에 포함한다.

## 실패와 복구

모든 rollback은 각 필드의 최초 원본(speed60/FeedTime10 예시)이며 중간40/39가 아니다.
미확인/취소/epoch·identity 변경은 전진을 멈추고 `RECOVERY_REQUIRED`로 남는다. 자동 재시도나
새 id 전진은 없다. 같은 id 재실행은 전진을 재개하지 않고, 기존 generic recover가 소유
원본만 되돌린다. **실패 시 자동 복구가 이미 완료된다고 주장하지 않는다.** 운영자가 같은
operation의 recover를 실행하고 그 결과를 확인해야 하며, 그때까지 임시값이 남을 수 있다.

실행 중에는 기존 single-send/lease/strict resolver/epoch/journal 전이와 실패 진단을 재사용한다.
각 성공 readback의 원 status bytes도 blob으로 저장하고 action 진단
`aquarium_status_sha256.<digest>`에 결속한다. 이는 transport 원본 frame 전체가 아닌 status bytes다.
최종 발급/무전송 재조회는 네 ACK·VERIFIED action, 실제 최초 raw, 모든 forward/rollback payload,
읽기 결과와 마지막 원본 복귀를 오프라인으로 다시 대조한다. 빈 ACK도 응답 수신일 뿐이며
값 수용 근거는 readback이다. 같은 id의 다른 준비값/기능은 거부한다.

`no_change`는 최초 snapshot과 payload의 비교일 뿐이다. FeedTime 원복과 최종speed 원복은
첫 snapshot으로 준비하면 no_change가 될 수 있지만 실제 실행 시에는 변경된 값에서 돌아가야
한다. producer는 이 action을 생략하지 않는다.

## 기존 운용과 남은 실기 전제

writer/protocol/schema 입력12개, 기존 producer, shared journal/lease/coordinator는 변경0이다.
schema는 `0cb054233e2a5691463f0f7b3812cc87f8536e935755f0e3c756f142bb9797fc` 그대로이며
기존 Pro/Bar 영수증을 만료시키지 않는다. 앱 UI/HA dashboard·운영 서비스·장비 설정도 변경하지 않았다.

**Aquarium의 실제 활성 template JS와 제품 귀속 양성 근거가 아직 없다.** 설치 앱의
제품 설정 JSON이나 이름이 비슷한 template 패키지, Pro/Bar digest를 대용으로 쓰지 않는다.
올바른 template을 확보·검증하고 현재 물리 identity/firmware와 설치 입력에 결속하기 전에는
실기와 자격 발급을 재개하지 않는다. 재개 시 source-matched image, 현장 감시/중단 수단,
급이 OFF, 단독 세션, 최초 상태 보존, 같은 operation 복구 절차가 필요하다.

이 구현·오프라인 테스트는 장비 수용 범위나 실제 순환량·생물 안전을 증명하지 않는다.
향후 발급하는 중간 두 receipt도 저출력 구간의 해당 기능에 대한 근거이지, 최초60을 포함한
전체 출력 범위의 수용을 증명하지 않는다. 마지막60 응답은 자기 원본 speed 복귀의 근거다.
리턴 전원 OFF/ON, DC, 도징, 새 Async와 시간표 삭제는 이번 범위 밖이다.

## 오프라인 검증 결과

- 기존 byte-copy transport와 fault injection으로 네 실패 위치의 ACK 후 읽기 실패, ACK 미수신,
  최초 raw·모든 payload의 첫 전송 전 내구화, 원본만 generic 복구, 원복 전 receipt0,
  마지막 ACK만 받고 값이 복귀하지 않은 경우의 거부, 무전송 replay와 옵션 충돌을 확인했다.
- CLI 명시 opt-in, 제품/기능 제한과 기존 경로 회귀를 포함한 local-control/qualification
  **974 PASS**(63.43초), macOS 전체 **3227 PASS / Linux-only 1 SKIP**(114.38초).
- `ruff check src tests`, diff whitespace 검사 통과. Linux 새 실행·장비 접근은 하지 않았다.
- Agent Bridge를 통한 Claude 독립 소스 검토: 제품 P1 지적0. 원본값·transport Mode·급이 상태와
  저출력 receipt의 증거 범위는 위 한계로 명시했으며 추가 펌웨어 가설 gate는 만들지 않았다.
- 로그는 opaque artifact `aquarium-live-20260916.a6x4lH`에 보존했다.
  `local-control-tests.log` SHA-256:
  `165a89fec4b755ba80d01e2df7a8109ddd77d741412b1f63f66328ed6f620084`;
  `full-suite.log` SHA-256:
  `436ef0290b0e4becb1332a74537bf6b6e7d971018348150a5db601e0acbcb677`.
