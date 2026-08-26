# 네이티브 Linkage 임시 시험

Local Wavemaker Pro 스키마에는 `independent`, `master`, `sync_slave`, `async_slave`가
있습니다. Jebao Flow Engine은 이 값을 일반 그룹 패턴으로 계속 덮어쓰지 않고, 시작과
종료가 명확한 임시 시험 트랜잭션으로 다룹니다.

현재 구현 범위는 Python 서비스 코어, LAN payload 생성, 영속 JSON 저널, 현장 전용 one-shot
CLI와 recovery-only supervisor입니다. 2026-08-26 Pro 두 대에서 제한된 저출력
TimerOFF/Constant/Linkage 레지스터 write와 반복 read-back을 수행했지만, 실행 종료 시 최초 자동
원복 확인은 실패해 recovery journal이 남았습니다. 이후 새 확인 토큰을 사용한 attended recovery에서
원래 TimerON 시간표 상태의 exact recovery가 성공했습니다. 이는 최초 시험이나 qualification의
성공을 뜻하지 않으며 두 장비의 재qualification은 아직 대기 중입니다. `async_slave`의 Flow를
별도로 바꾸려 한 값은 유지되지 않았으므로 독립 출력 지원으로 판정하지 않았고, 물리 유량과
파형도 아직 검증하지 않았습니다. 데몬 actuator, MQTT 명령, Home Assistant 버튼에는 이 경로를
연결하지 않았습니다. 일반 `jebao-flowd`는 계속 Observer로 운용하고, 현장 시험은 공유
`/hardware-safety` 볼륨을 쓰는 별도 컨테이너에서만 실행합니다.

## 지원 동작

- Pro 두 대 중 한 대를 `master`, 다른 한 대를 `sync_slave` 또는 `async_slave`로 지정
- 마스터와 슬레이브에 서로 다른 `Flow` 적용
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

## 남은 실기 게이트

1. `Flow`를 다르게 준 slave가 실제 유량도 독립적으로 유지하는지 현장에서 관찰
2. `sync_slave`와 `async_slave`의 물리 파형 및 Frequency 의미 확인
3. 별도 TimerON schedule-linkage 진단으로 각 장비의 `Auto*` 레지스터 전환 확인
4. 장비 한 대 전원 제거와 프로세스 강제 종료 후 복구 확인
5. 위 결과가 통과한 뒤에만 MQTT/HA 임시 시험 UI 연결
