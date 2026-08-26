# 네이티브 Linkage 임시 시험

Local Wavemaker Pro 스키마에는 `independent`, `master`, `sync_slave`, `async_slave`가
있습니다. Jebao Flow Engine은 이 값을 일반 그룹 패턴으로 계속 덮어쓰지 않고, 시작과
종료가 명확한 임시 시험 트랜잭션으로 다룹니다.

현재 구현 범위는 Python 서비스 코어, LAN payload 생성, 영속 JSON 저널, 현장 전용 one-shot
CLI와 recovery-only supervisor입니다. 실제 장비 write는 한 건도 보내지 않았으며 데몬
actuator, MQTT 명령, Home Assistant 버튼에는 아직 연결하지 않았습니다. 일반 `jebao-flowd`는
계속 Observer로 운용하고, 현장 시험은 공유 `/hardware-safety` 볼륨을 쓰는 별도 컨테이너에서만
실행합니다.

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
- 비상 정지·정비 safety interlock이 걸리면 저장된 ON 상태보다 안전 정지를 우선하고,
  명시적인 latch 해제 전까지 정확한 복원을 보류
- 두 장비 각각의 최근 단일 write 자격 영수증이 preflight와 첫 linkage frame 직전에 모두
  유효해야 시작
- 프로세스가 비정상 종료되면 시험을 재개하지 않고, 최근 TimerOFF 기록만 30초 유예 안에서
  supervisor가 복구하며 stale·TimerON·safety 기록은 현장 확인을 요구

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

## 남은 실기 게이트

1. 현장에서 단일 Pro 펌프의 동일값 write와 read-back을 먼저 검증
2. `Flow`를 다르게 준 slave가 실제 유량도 독립적으로 유지하는지 관찰
3. `sync_slave`와 `async_slave`의 물리 파형 및 Frequency 의미 확인
4. 앱 스케줄은 별도 시험으로 분리하고, 첫 Linkage 시험에서는 계속 `TimerON=false` 유지
5. 장비 한 대 전원 제거와 데몬 강제 종료 후 복구 확인
6. 위 결과가 통과한 뒤에만 MQTT/HA 임시 시험 UI 연결
