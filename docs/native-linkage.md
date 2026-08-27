# 네이티브 Linkage 임시 시험

Local Wavemaker Pro 스키마에는 `independent`, `master`, `sync_slave`, `async_slave`가
있습니다. Jebao Flow Engine은 이 값을 일반 그룹 패턴으로 계속 덮어쓰지 않고, 시작과
종료가 명확한 임시 시험 트랜잭션으로 다룹니다.

현재 구현 범위는 Python 서비스 코어, LAN payload 생성, 영속 JSON 저널, 현장 전용 one-shot
CLI와 recovery-only supervisor입니다. 2026-08-27에는 활성 TimerON 시간표를 snapshot한 뒤
35%/33% Async를 최대 150초로 시작하고 60초 시점에 slave 38% 변경을 요청했습니다. 실행은 그
구간 직후 안전 실패했고 자동 exact restore가 완료되지 않아 qualification 영수증은 0/2였습니다.
같은 실행을 반복하지 않고 새 확인 토큰의 attended recovery를 수행해 두 snapshot을 복원했으며,
재시작한 읽기 전용 Observer도 시험 전 TimerON 상태를 다시 확인했습니다.

복구 코드 `7e8c41b` 배포 뒤 수행한 후속 저출력 Sync도 아직 성공 증거가 아닙니다. 첫 새
operation은 `--bootstrap-active-schedule`, `sync_slave`, 31%/31%, 실행 중 출력 변경 없음,
`--duration 10`으로 실행해 `LinkageApplyError`로 끝났습니다. 다만 트랜잭션은
`terminal/restored`, journal은 `none`이 됐고 Observer가 원래
`TimerON/independent/mode/power`를 정확히 확인했습니다. qualification 영수증은 0/2였으며,
duration이 bootstrap 전체 deadline으로 적용돼 너무 짧았을 가능성이 있습니다. 두 번째 새
operation은 같은 저출력 Sync를 `--duration 60`으로 실행했지만 자동 rollback 뒤
`RECOVERY_REQUIRED/restore_failed`와 `timer_on` blocker가 남았습니다. 새 토큰의 attended
recovery로 닫은 뒤 Observer가 원래 상태를 정확히 확인했고 영수증은 계속 0/2입니다.

후속 코드 감사에서는 성공한 TimerON restore write 뒤 기존 세션으로 decoded mismatch를 4회,
약 1.75초만 확인해 명목상 64초 convergence 창을 사용하지 못하는 경로를 찾았습니다. 이를
fresh authenticated read-only 세션과 deadline 기반 capped backoff로 수정한 뒤 세 번째 새
저출력 Sync operation을 31%/31%, `--duration 60`으로 실행했습니다. 이번에는 TimerON
convergence보다 앞선 slave detach 단계에서 실패해 두 장비가
`RECOVERY_REQUIRED/restore_failed`로 남았고 영수증은 다시 0/2였습니다. 새 토큰의 attended
recovery는 약 11초 안에 성공했으며, 재시작한 Observer가 두 장비 모두 시험 전
`TimerON/independent/mode/power`와 정확히 일치함을 확인했습니다.

세 번째 실행으로 ACTIVE 세션을 첫 rollback reconciliation과 detach에 재사용하는 별도 결함을
찾았습니다. rollback 시작 전에 slave→master 순서로 새 인증 세션을 강제하고, session refresh나
detach 실패 뒤 fallback도 오염된 세션을 재사용하지 않도록 수정했습니다. normal rollback과
attended recovery, stale exact 조기 clear, 반쪽 인증, detach 실패를 포함한 테스트 571개가
통과했고 독립 감사도 P0/P1 0건이었습니다. 이 시점에는 마지막 수정도 실기 재검증 전이었으며,
위 세 실행 모두 물리 수류·파형의 성공 관찰로 해석하지 않습니다.

이 수정 배포 뒤 네 번째 새 저출력 Sync operation은 `--bootstrap-active-schedule`, 31%/31%,
`--duration 60`으로 자동 종료와 exact restore까지 성공했습니다. journal은 `none`, 안전 래치는
clear였고 두 물리 바인딩의 qualification 영수증 2/2가 발급됐습니다. 재시작한 Observer도 두
장비의 시험 전 `TimerON/independent/mode/power/frequency`를 정확히 확인했습니다. 이 결과는
slave-first fresh rollback session 경계의 Sync 현장 검증이지만 물리 파형 검증은 아닙니다.

그다음 Async operation은 35%/33%로 시작해 60초 후 slave만 38%로 요청하는 150초 진단으로
실행했습니다. 프로세스는 `LinkageRollbackError`로 끝나
`RECOVERY_REQUIRED/restore_failed`와 `timer_on_snapshot` blocker를 남겼습니다. 영속 intent의
typed primary failure는 `none`이어서 slave 변경 실패로 판정할 증거는 없지만, 자동 rollback이
완료되지 않았으므로 Async qualification 영수증은 발급되지 않았습니다. 새 확인 토큰의 attended
recovery는 약 18초 안에 성공했고 Observer가 위 시험 전 상태를 다시 정확히 확인했습니다.
따라서 Async 독립 출력은 여전히 미검증이며, rollback 실패 단계를 영속·redacted evidence로
남기는 진단을 구현했습니다.

version 2 진단과 cross-store fail-closed 보강은 전체 테스트 586개와 독립 감사 P0/P1 0건을
통과했습니다. 이 빌드로 저출력 시간대를 기다리지 않고 현재 TimerON 상태와 전체 시간표를
snapshot한 뒤, bootstrap이 시간표를 `TimerOFF + independent + safe-low`로 일시 정지한 상태에서
같은 Async 조건을 한 번만 재검증했습니다. 시험 전 상태는 한 장비가 Constant 30%/32, 다른
장비가 Random 89%/34였고, 둘 다 TimerON/Independent와 유효한 14개 스케줄 슬롯이었습니다.

재검증 intent에는 `ACTIVE`와 slave 38% `write_attempted`가 남았지만
`adapter_verified=no`, `full_state_verified=no`, `samples=0`, typed primary failure `none`이었습니다.
즉 LAN write/read-back 경로에 진입한 것은 확실하지만 38% frame의 전달·적용과 Async 독립 유지는
어느 것도 입증되지 않았습니다. 자동 rollback은 `restore_failed`로 끝났고, allow-listed 진단에는
slave detach/control restore/final verification/safe fallback과 master control restore/final
verification 실패가 남았습니다. 추가 시험 명령 없이 새 토큰의 attended recovery를 수행해 약
14초 안에 journal을 닫았습니다. 재시작한 Observer는 두 장비의 원래
`TimerON/Independent/Mode/Flow/Frequency`, schedule enabled, 14 slots, invalid 0을 fresh read로
확인했고 두 schedule fingerprint도 snapshot과 일치했습니다.

따라서 현재 결론은 “38% 변경 성공”이 아니라 “변경 시도 뒤 adapter 검증 전에 실패”입니다.
같은 실기는 반복하지 않으며, 원인을 더 좁히려면 예외 원문 대신 transport/send/read-back 단계를
추가 allow-listed category로 나눈 새 진단이 먼저 필요합니다.

후속 구현은 빠진 control ACK의 종류를 allow-listed 값으로 먼저 fsync하고, 원 세션을 폐기한 뒤
최대 55초·8회의 서로 다른 fresh session에서 explicit state reply만 조회합니다. resolver는 control
payload를 받지 않아 같은 변경을 재전송할 수 없고, 각 quarantine/connect/authenticate/query/decode
단계와 attempt를 version 2 evidence에 남깁니다. stop·deadline·safety interlock은 이 read-only
resolver보다 우선하며, 실패 후 rollback도 별도의 새 세션 경계에서 시작합니다. 이 변경은 전체
소프트웨어 테스트와 독립 감사를 통과한 뒤에만 다음 5~10분 저출력 실기에 사용합니다.

이 결과는 이전 짧은 실행에서 관찰한 native Linkage 레지스터 관계와 read-back 자체를 부정하지
않지만, `async_slave`의 개별 `Flow` 유지나 물리 유량·파형을 입증하지도 않습니다. 따라서
slave별 gain/출력이 필요한 운영은 모든 펌프를 `independent`로 둔 소프트웨어 그룹을
사용합니다. 네이티브 페어는
`experimental / unavailable / hardware_not_qualified`로 남기며 데몬 actuator, MQTT 명령,
Home Assistant 버튼에는 연결하지 않습니다. 일반 `jebao-flowd`도 계속 Observer로 운용합니다.

## 영속 진단 증거

현장 one-shot intent version 2는 다음 진행 상태를 atomic replace와 fsync로 보존합니다.

- 네이티브 관계의 ACTIVE 진입
- live slave 출력 쓰기 시도 직전
- control ACK 유실의 allow-listed 종류와 read-only resolver 단계·상태·시도 횟수
- LAN adapter의 쓰기·read-back 정상 반환
- 두 장비 전체 상태 검증 성공과 이후 성공 sample 수·첫/마지막 시각
- forward failure의 고정 분류
- rollback 시작·완료
- rollback 실패의 `master/slave + stage + allow-listed category`

live 출력 쓰기 직전 evidence 저장이 실패하면 해당 출력 명령은 보내지 않고 rollback으로
전환합니다. rollback 중 evidence 저장 실패는 물리 원복을 중단시키지 않으며, journal을 지우기
전에 terminal evidence 저장이 실패하면 journal을 유지해 다음 attended recovery가 이어받습니다.
MAC, vendor ID, IP, 인증정보와 예외 원문은 evidence와 status에 기록하지 않습니다.

`full_state_verified`는 master/slave 모두의 online, error, power, mode, frequency, linkage,
TimerON과 snapshot schedule fingerprint 검증을 통과했다는 뜻입니다. 자동 rollback이 나중에
실패하더라도 이 forward 증거는 terminal intent에 남고, attended recovery 뒤에도 삭제되지
않습니다. `status`의 `verified_span`은 첫 full-state sample과 마지막 성공 sample 사이의 실제
시간이며, 장기 시험에서는 300초 이상이어야 5분 유지 증거로 사용합니다. 기존 version 1 intent는
읽을 수 있지만 evidence 의미는 `unknown`이며, version 1의 ARMED operation은 새 preflight 없이
실행할 수 없습니다.

## 지원 동작

- Pro 두 대 중 한 대를 `master`, 다른 한 대를 `sync_slave` 또는 `async_slave`로 지정
- 마스터와 슬레이브에 서로 다른 `Flow`를 진단용으로 요청 가능하나 slave 독립 유지로 판정하지 않음
- 각 장비의 `TimerON + Linkage + Mode + Frequency + Flow`를 한 control frame으로 적용
- 설정된 절대 만료 시각 또는 수동 중지 후 자동 원복
- ACTIVE 동안 실제 `Flow/Linkage/Mode/Frequency/TimerON`을 주기적으로 확인하고, 마스터
  broadcast 등으로 슬레이브 출력이 덮이면 즉시 실패·원복
- 적용 오류와 task cancellation에서도 shielded 원복
- 프로세스 재시작 시 시험을 재개하지 않고 저널 snapshot으로 즉시 복구
- OS advisory journal lease를 트랜잭션 전체 동안 유지해 같은 경로를 쓰는 별도 daemon의
  동시 시험·복구 차단
- 한 장비 원복 실패 시 다른 장비는 계속 복구하고, 실패 장비는 안전한
  `independent + constant + low + TimerOFF`를 한 번만 시도
- 정확한 복구가 끝나지 않으면 `RECOVERY_REQUIRED` 저널을 유지하고 새 시험 차단
- 마지막 TimerON 원복 frame의 결과가 불확실하면 같은 target을 다시 보내지 않고, 오염된 TCP
  세션을 폐기한 뒤 read-only fresh state에서 두 번 연속 exact 일치를 확인할 때만 완료
- rollback은 첫 reconciliation보다 먼저 slave→master 순서로 ACTIVE 세션을 폐기하고 새 인증
  세션을 만들며, detach와 TimerON은 이 경계 이후의 세션에서만 각 한 번 전송
- session refresh 실패는 해당 장비 exact restore를 차단하고 새 연결의 안전 저속
  `independent + TimerOFF` fallback만 허용하며, slave refresh 실패 시 master TimerON도 차단
- 기본 복구 상한은 guarded write 31초, connect/disconnect 16초, fresh read 1회 5.5초,
  연결 이후 convergence 64초로 분리하며 safety interlock이 걸리면 각 상한보다 먼저 중단
- snapshot 이후 스케줄 구조 변경을 한 번이라도 관측하면 즉시 `schedule_changed` 사유를
  fsync하고, 새 현장 확인 토큰을 받은 attended recovery 전에는 후속 read나 재시도로 해제 금지
- 비상 정지·정비 safety interlock이 걸리면 저장된 ON 상태보다 안전 정지를 우선하고,
  명시적인 latch 해제 전까지 정확한 복원을 보류
- 두 장비 각각의 최근 단일 write 자격 영수증이 preflight와 첫 linkage frame 직전에 모두
  유효해야 시작
- 아직 영수증이 없고 두 장비의 유효한 TimerON 스케줄을 잠시 멈춰야 하는 경우, 명시적인
  schedule-bootstrap 트랜잭션이 journal 뒤 atomic `31% + constant + TimerOFF` frame으로
  시작해 31→30→31 qualification을 수행하고 성공 시에만 24시간 영수증 발급
- `async_slave` ACTIVE 중 한 번만 슬레이브 Flow를 변경하고 이후 fresh read-back으로 독립 출력
  유지 여부를 검증하는 선택적 진단
- 프로세스가 비정상 종료되면 시험을 재개하지 않고, 최근 TimerOFF 기록만 30초 유예 안에서
  supervisor가 복구하며 stale·TimerON·safety·schedule-changed 기록은 현장 확인을 요구

## 사전 조건

초기 버전은 다음 조건을 모두 만족할 때만 시작합니다.

- 서로 다른 두 장비이며 둘 다 연결·online·무오류 상태
- 두 장비가 `enabled`, `power`, `mode`, `frequency`, `linkage`, `timer` write를 지원
- 요청 모드는 첫 실기 범위인 `constant`, `pulse`, `sine` 중 하나이며 Linkage 역할 지원
- 둘 다 현재 `independent`
- 둘 다 이미 운전 중이며, 시험 기능이 꺼진 펌프를 임의로 켜지 않음
- 둘 다 `TimerON=false`; 앱 시간표는 현장 시험 전에 수동으로 중지
- 현재값과 시험 출력이 설정된 power range/step 안에 있어 정확히 복원 가능
- 두 product key가 모두 확인됐고 서로 동일
- 이전 복구 저널이 없음
- 같은 물리 바인딩으로 24시간 안에 완료한 단일 장비 자격 영수증이 두 대 모두 존재
- daemon이 명시적으로 만든 fail-closed safety interlock이 허용 상태이며, 이 interlock을
  장비 I/O lock 안에서 control frame 전송 직전에 다시 검사

바형 Local Wavemaker의 스키마는 `independent/master/slave`만 제공하므로 Pro의
`sync_slave`/`async_slave` 시험에는 넣지 않습니다. 바형 펌프는 상위 그룹 엔진에서
별도 phase/gain을 가진 보조 수류로 운용하는 방향을 유지합니다.

## 트랜잭션 순서

```text
actual state + schedule structure snapshot
                │
                ▼
 deployment-wide lease + PREPARED journal fsync
                │
                ▼
 independent + constant + safe-low (TimerOFF already required)
                │
                ▼
 master target ──────> slave target
                │
                ▼
       read-back 검증 / ACTIVE
                │
      manual stop · timeout · failure
                ▼
 slave detach → master detach → exact TimerOFF values
```

장비 두 대에 대한 네트워크 write는 하나의 원자적 연산이 될 수 없습니다. 그래서 ACK 이후
프로세스가 종료되는 경우까지 고려해, 적용 완료 목록이 아니라 snapshot에 들어 있는 두
장비 모두를 항상 원복합니다. 장비 시각은 읽을 때마다 바뀌므로 schedule 충돌 검사용 해시에는
slot entries, invalid slots, capacity만 포함하고 장비 시각과 `TimerON`은 제외합니다.

위 31/16/5.5/64초는 서로 다른 복구 단계의 production 상한이며 합산된 완료 보장 시간이
아닙니다. TimerON write가 timeout·ACK 유실 등으로 불확실해져도 write를 재전송하지 않습니다.
이 경우 오염된 세션을 convergence 시작 전에 한 번 교체하며, 그 뒤 convergence 중 최초의
transport read 실패가 발생하면 64초 창 안에서 read-only session recovery를 한 번 더 허용합니다.
두 경로 모두 ON target을 다시 보내지 않으며, 완전히 decode된 fresh state 두 개가 연속으로
snapshot과 일치해야 exact recovery로 인정합니다. 어느 단계에서든 safety interlock이 우선하며
확인이 끝나지 않으면 journal을 유지하고 안전한 TimerOFF fallback을 시도합니다.

`jebao-flow-hwtest status`는 raw 오류나 장비 식별자를 노출하지 않고 typed recovery reason과
`timer_on_snapshot`, `stale_or_clock_invalid`, `safety_interlock`, `schedule_changed` blocker만
표시합니다. `PREPARED`는 첫 frame 전 상태라 자동 정리할 수 있지만, 그 밖의 record에 blocker가
하나라도 있으면 `--recovery-first` 대신 새 토큰을 사용한 attended recovery가 필요합니다.

## 현재 판정과 남은 실기 게이트

1. 2026-08-27 실패 operation은 재실행하지 않으며, 후속 시험에는 새 operation ID를 사용
2. slave-first rollback session 교체는 저출력 Sync에서 자동 exact restore까지 현장 검증 완료
3. Async 자동 rollback 실패의 단계·장비별 redacted category를 attended recovery 전 영속화
4. Sync qualification 영수증 2/2가 있어도 Async rollback 원인 규명 전 schedule-linkage 금지
5. `async_slave` 상태의 시간표 경계에서 `AutoMode`와 `AutoFlow`가 함께 바뀌는 동작은 계속 미검증
6. `sync_slave`와 `async_slave`의 물리 파형 및 Frequency 의미를 현장에서 별도 확인
7. 장비 한 대 전원 제거와 프로세스 강제 종료 후 복구 확인
8. 모든 결과가 통과한 뒤에만 MQTT/HA 네이티브 시험 UI 승격 검토
