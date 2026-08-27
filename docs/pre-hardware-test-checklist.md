# 실기 제어 안전 체크리스트

이 문서는 실제 펌프의 제한된 control/write 검증을 반복하거나 다음 실기 단계를 진행하기 전에
확인할 중단 지점입니다. 2026-08-27 Pro 두 대의 TimerON Async 장기 시험은 slave 출력 변경 구간
직후 안전 실패했고 자동 exact restore가 완료되지 않았습니다. 새 확인 토큰의 attended recovery와
Observer 교차 확인으로 시험 전 상태는 복원했지만, qualification 영수증은 0/2이며 schedule-linkage
진입 조건도 충족하지 못했습니다. 같은 실행은 반복하지 않습니다. 물리 유량·파형과 TimerON
시간표 경계 동작은 새 사전 조건을 모두 충족하고 사용자가 수조를 직접 볼 수 있을 때만 검증합니다.

복구 코드 `7e8c41b`를 배포한 뒤 같은 날 수행한 후속 저출력 Sync 시험도 자격 판정에는
도달하지 못했습니다. 첫 새 operation은 `--bootstrap-active-schedule`, `sync_slave`, 31%/31%,
실행 중 출력 변경 없음, `--duration 10` 조건에서 `LinkageApplyError`로 끝났지만 트랜잭션은
`terminal/restored`, journal은 `none`이 됐고 Observer가 원래
`TimerON/independent/mode/power`를 정확히 확인했습니다. qualification 영수증은 계속 0/2입니다.
이때 duration이 ACTIVE 시간만이 아니라 bootstrap 전체 deadline으로 쓰여 너무 짧았을 가능성이
있습니다. 두 번째 새 operation은 동일한 저출력 Sync와 `--duration 60`으로 실행했으나 자동
rollback이 `RECOVERY_REQUIRED/restore_failed`와 `timer_on` blocker를 남겼습니다. 새 토큰의
attended recovery로 이를 닫은 뒤 Observer가 다시 원래 상태를 정확히 확인했으며, 영수증은
여전히 0/2입니다. 어느 실행도 물리 수류·파형의 성공 관찰 증거로 사용하지 않습니다.

후속 코드 감사에서는 TimerON restore write 성공 뒤에도 기존 세션을 사용하면서 decoded mismatch를
4회, 약 1.75초 동안만 확인해 명목상 64초 convergence 창을 사용하지 못한 경로를 찾았습니다.
fresh authenticated read-only 세션과 deadline 기반 capped backoff로 수정한 뒤 세 번째 새
저출력 Sync operation을 31%/31%, `--duration 60`으로 실행했으나, 더 앞선 slave detach에서
자동 rollback이 실패해 다시 `RECOVERY_REQUIRED/restore_failed`, 영수증 0/2로 끝났습니다.
새 토큰의 attended recovery는 약 11초 안에 성공했고 Observer는 두 장비의 시험 전 상태를 정확히
확인했습니다. 이 결과로 ACTIVE 세션을 rollback 첫 read/detach에 재사용하는 결함을 찾아
slave→master fresh authenticated session 경계를 추가했습니다. refresh/detach 실패의 강제 fresh
fallback까지 포함한 테스트 571개와 독립 감사 P0/P1 0건을 통과했습니다. 이 시점에는 마지막
수정도 아직 실기 재검증 전이었습니다.

마지막 수정 배포 뒤 네 번째 저출력 Sync operation은 31%/31%, `--duration 60`으로 자동 종료와
exact restore까지 성공했고 journal `none`, 래치 clear, qualification 영수증 2/2를 확인했습니다.
Observer도 두 장비의 시험 전 `TimerON/independent/mode/power/frequency`와 정확히 일치했습니다.
이어 실행한 150초 Async 진단은 35%/33%로 시작해 60초 뒤 slave 38%를 요청했지만
`LinkageRollbackError`와 `RECOVERY_REQUIRED/restore_failed`로 끝났습니다. typed primary failure는
`none`이어서 slave 변경 실패로 단정할 수 없고 Async 영수증은 발급되지 않았습니다. attended
recovery는 약 18초 안에 완료됐으며 Observer가 다시 시험 전 상태를 확인했습니다. 자동 rollback
실패 뒤에도 forward 진행과 원복 실패 단계를 보존하는 version 2 intent 진단을 구현했으며,
cross-store 불일치까지 fail-closed로 보강한 전체 테스트 586개와 독립 감사 P0/P1 0건을
통과했습니다.

재시험은 저출력 시간대를 기다리지 않았습니다. 활성 `TimerON` 상태와 전체 시간표를 먼저
snapshot하고, schedule bootstrap이 `TimerOFF + independent + safe-low`로 시간표를 일시 정지한
뒤 Async를 적용했습니다. version 2 evidence는 slave 38% `write_attempted=yes`만 기록했고
`adapter_verified=no`, `full_state_verified=no`, `samples=0`이므로 출력 변경 성공 증거는 없습니다.
자동 rollback의 typed failure는 slave detach/control restore/final verification/safe fallback과
master control restore/final verification이었습니다. 새 토큰의 attended recovery는 성공했고,
Observer가 시험 전 Constant 30%/32와 Random 89%/34, 두 장비의 TimerON/Independent,
schedule enabled, 14 slots, invalid 0 및 snapshot schedule fingerprint 일치를 교차 확인했습니다.
private 현장 설정은 다시 `dry_run: true`로 잠갔습니다.

## 완료된 사전 검증

- 격리 IoT VLAN의 장비 6대 discovery 및 TCP 12416 연결
- 여섯 대 모두 로컬 인증과 raw 상태 읽기 성공
- 자체 제품 프로필로 다섯 product key 상태 디코딩 성공
- 전원, 출력, 모드, 주파수, linkage와 fault 디코딩 교차검증
- 네 펌프 제품군의 control payload를 기존 캡처 기반 구현과 바이트 단위 비교
- 출력 범위, 출력 step, 최소 명령 간격, 중복 억제와 read-back 불일치 단위 테스트
- 어댑터 write 잠금 기본값 적용
- 전역 `runtime.dry_run`이 장비별 write 허용보다 우선하도록 적용
- Pro 두 대의 제한된 TimerOFF/Constant/Linkage 레지스터 write와 반복 read-back 수행,
  최초 자동 원복 확인 실패 뒤 attended exact recovery 성공
- TimerON Async 장기 실행은 slave 출력 변경 구간 직후 실패했고 qualification 영수증 0/2
- 자동 exact restore 미완료 뒤 새 토큰의 attended recovery 및 Observer 원상태 교차 확인
- 복구 코드 `7e8c41b` 배포 뒤 저출력 Sync 후속 시험 두 건도 영수증 0/2로 종료:
  10초 실행은 자동 복원 성공, 60초 실행은 attended recovery 뒤 원상태 확인
- TimerON convergence 수정 뒤 세 번째 60초 Sync는 slave detach에서 실패했으며, attended
  recovery 및 Observer 원상태 확인 뒤 영수증 0/2 유지
- rollback 첫 read/write 전 slave→master 새 인증 세션과 실패 시 fresh fallback을 구현했으나
  네 번째 저출력 Sync에서 자동 exact restore와 영수증 2/2 확인
- 후속 Async 35%/33%→slave 38% 진단은 자동 rollback 실패, attended exact recovery 성공,
  typed primary failure 없음, Async 영수증 없음
- version 2 재진단도 38% write 시도 뒤 adapter/full-state 검증 0건으로 실패했고, attended
  recovery와 Observer가 원래 TimerON 상태·14개 스케줄·지문을 exact 확인
- `async_slave` Flow 개별 유지 기능은 지원 기능으로 인정하지 않으며, 독립 유지와 물리 유량 모두 미검증
- `async_slave` 상태의 시간표 경계에서 `AutoMode`와 `AutoFlow`가 함께 바뀌는 동작은 미실행·미검증
- 네이티브 Linkage의 Pro 4역할과 `TimerON` encode/decode 단위 테스트
- Sync/Async 개별 출력, timeout·수동 종료·취소·부분 실패·재시작 원복 시뮬레이터 테스트

## 현장에서 먼저 확인할 것

1. 각 펌프의 물리 모델 라벨과 수조 내 위치를 기록합니다.
2. 앱에 표시되는 현재 모드·출력과 `jebao-flowctl probe --decode` 결과를 비교합니다.
3. 기존 Home Assistant 직접 제어 통합이 있다면 비활성화합니다.
4. 첫 대상은 현재 낮은 출력의 수류모터 한 대로 제한합니다.
5. 리턴펌프와 도징펌프는 첫 write 대상에서 제외합니다.
6. 펌프 전원 차단 수단을 손이 닿는 곳에 둡니다.

## 강제되는 안전 경계

현장 도구는 일반 데몬과 분리되어 있고 다음 조건을 모두 강제합니다.

- `runtime.mode: control`, `runtime.dry_run: false`, `observer.enabled: false`
- 같은 제품 키의 Pro 수류모터 최대 두 대만 `allow_hardware_writes: true`
- 각 Pro의 vendor device ID와 MAC을 모두 지정하고 매 실행마다 discovery로 다시 바인딩
- 시작 상태는 ON, `constant`, `independent`, `TimerON=false`, 출력 45% 이하
- 단일 장비 변화는 현재값보다 1~5%p 낮은 값, 전체 실행은 최대 10초
- 명령 전 영속 journal, 명령 뒤 fresh read-back, 종료 뒤 exact state read-back
- 모든 도구와 supervisor가 같은 `/hardware-safety` 볼륨과 전역 operation lease 사용
- 최근 30초 복구 유예를 벗어나거나 TimerON/safety 기록이면 자동 ON 복구 금지
- 복구 단계별 production 상한은 guarded write 31초, connect/disconnect 16초, fresh read 1회
  5.5초, 연결 이후 convergence 64초이며 합산된 완료 보장 시간이 아님
- outer recovery attempt 사이는 audited 최대 장비 명령 간격 이상인 2초를 대기하고, 모든 timeout과
  retry보다 safety interlock을 우선

`config.hardware-test.example.yaml`을 private 파일로 복사하고 실제 두 Pro identity와 IoT VLAN
directed broadcast 주소를 넣습니다. 저장소의 예제는 `dry_run: true`라서 write 명령을 통과하지
못합니다. 현장에서만 `dry_run: false`로 바꿉니다.

Compose는 `hardware` 프로필의 서비스만 실행해도 전체 파일의 환경 변수 보간을 먼저 검사합니다.
따라서 배포 호스트의 private `.env`에 `MQTT_PASSWORD`와
`JEBAO_FLOW_HARDWARE_CONFIG=./config.hardware-test.yaml`을 설정한 뒤 아래 명령을 실행합니다.
MQTT 비밀번호는 recovery 컨테이너 환경에는 전달되지 않지만, 기본 `jebao-flowd` 서비스의
필수 변수 검증 때문에 비어 있으면 Compose 명령 자체가 거부됩니다.

## 1. 현장 상태 준비

앱에서 두 Pro를 각각 ON, Constant, Independent, 31~40%, `TimerON=false`로 맞춥니다. 현재값이
장비 최소 출력과 같으면 하향 시험을 할 수 없으므로 최소값보다 적어도 1%p 높여야 합니다.
바형 수류모터, 리턴펌프, 도징펌프는 대상에서 제외합니다.

그다음 앱을 완전히 종료하고 기존 Home Assistant 직접 통합을 비활성화한 뒤 일반 Observer도
중지합니다.

```bash
docker compose stop jebao-flowd
```

## 2. recovery supervisor 먼저 시작

Supervisor는 평상시 장비를 검색하거나 연결하지 않고 `/hardware-safety`의 unfinished journal만
확인합니다. one-shot 프로세스가 비정상 종료된 경우에도 시험을 재개하지 않고 복구만 수행합니다.

```bash
docker compose --profile hardware up -d jebao-flow-recovery
```

Supervisor와 모든 one-shot 컨테이너는 Compose의 동일한
`jebao-flow-hardware-safety:/hardware-safety` 볼륨을 사용해야 합니다. 공식 Compose 파일은
project 이름과 무관하게 이 호스트 전역 볼륨 이름을 고정합니다. 임의 볼륨으로 바꾸거나 Compose
밖에서 별도 컨테이너를 실행하면 전역 제어권을 공유한다고 볼 수 없으므로 사용하지 않습니다.

## 3. 첫 Pro 한 대 검증

먼저 read-only preview로 현재 snapshot과 `JFV-...` 확인 토큰을 발급합니다. 아래 34는 현재
출력이 35%일 때의 예시입니다. 모든 현장 명령은 supervisor 컨테이너 안에서 실행해 같은 이미지와
안전 볼륨을 사용합니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-device-verify --config /config/hardware-test.yaml preflight-device \
  --operation-id qualify_left_001 \
  --device wavemaker_left \
  --target-power 34 \
  --duration 10 \
  --verification-interval 0.5
```

표시된 현재 상태와 수조를 직접 확인한 뒤 동일한 인수와 토큰으로 실행합니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-device-verify --config /config/hardware-test.yaml run-device-verification \
  --operation-id qualify_left_001 \
  --device wavemaker_left \
  --target-power 34 \
  --duration 10 \
  --verification-interval 0.5 \
  --confirm JFV-REPLACE_WITH_PREFLIGHT_TOKEN
```

도구는 `동일값 frame → 하향 한 단계 → exact 원복`과 각 단계의 Actual State를 검증합니다.
세 단계와 최종 원복이 모두 성공한 경우에만 해당 물리 바인딩의 24시간 자격 영수증을 만듭니다.
같은 절차를 `wavemaker_right`에 새 operation ID로 반복합니다. 복구 실행이나 동일값까지만 끝난
시험에는 영수증을 발급하지 않습니다.

## 4. Native Sync를 가장 낮은 위험도로 검증

두 영수증이 모두 있어야 preflight가 통과하며, 첫 linkage frame 직전에 만료·삭제·바인딩을
다시 확인합니다. 첫 시험은 `constant + sync_slave`와 낮은 서로 다른 출력으로 시작합니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml preflight \
  --operation-id attended_sync_001 \
  --master wavemaker_left \
  --slave wavemaker_right \
  --slave-role sync_slave \
  --mode constant \
  --master-power 35 \
  --slave-power 33 \
  --frequency 20 \
  --duration 10
```

출력된 `JFL-...` 토큰과 동일 인수를 사용합니다. snapshot이 조금이라도 바뀌면 첫 control
frame 전에 거부됩니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml run-native-linkage \
  --operation-id attended_sync_001 \
  --master wavemaker_left \
  --slave wavemaker_right \
  --slave-role sync_slave \
  --mode constant \
  --master-power 35 \
  --slave-power 33 \
  --frequency 20 \
  --duration 10 \
  --confirm JFL-REPLACE_WITH_PREFLIGHT_TOKEN
```

마스터와 슬레이브의 실제 유량이 독립적으로 유지되고 원래 TimerOFF 상태로 정확히 돌아온 것이
확인된 뒤에만 `sine`, 그다음 `async_slave`를 각각 새 operation ID로 시험합니다.

### 활성 스케줄을 잠시 멈추는 일회성 Bootstrap

두 Pro가 아직 첫 write 자격 영수증이 없고 `TimerON=true`인 경우에는 명시적인
`--bootstrap-active-schedule` 경로만 사용합니다. 이 경로는 스케줄 slot을 쓰거나 지우지 않고,
현재 `enabled/power/mode/frequency/linkage/TimerON`과 스케줄 구조 해시를 PREPARED journal에 먼저
fsync합니다. 그다음 두 snapshot을 다시 읽고 각 장비에서 `31% + Constant + Independent +
TimerOFF → 30% → 31%`를 atomic frame과 fresh read-back으로 검증한 뒤 Async 관계를 적용합니다.
종료 시 안전 저속·TimerOFF로 분리한 다음, 스케줄 구조가 그대로일 때만 원래 수동 fallback 값과
TimerON을 하나의 guarded atomic frame으로 복원합니다. 따라서 TimerOFF 상태에서 저장된 고출력
fallback이 별도 프레임으로 노출되지 않습니다.

마지막 TimerON 원복 write가 timeout 또는 ACK 유실로 불확실해지면 같은 target을 재전송하지
않습니다. 오염된 세션은 convergence 전에 한 번 교체하고, convergence 중 최초의 transport read
실패가 발생하면 64초 창 안에서 read-only session recovery를 한 번 더 허용합니다. 이 과정에서
fresh state가 snapshot과 두 번 연속 exact 일치할 때만 복구 완료로 인정합니다. 단계별
31/16/5.5/64초 상한 안에서 확인되지 않거나 safety interlock이 걸리면 journal을 유지하고 안전한
TimerOFF fallback을 우선합니다.

Async 슬레이브 출력 변경이 실제로 독립 유지되는지 확인하는 2분 예시는 다음과 같습니다. 전체
bootstrap 실행은 최대 600초이고 임시 시험·qualification target은 45% 이하로 제한됩니다. 마지막
atomic 원복 frame만 journal에 저장된 원래 수동 fallback 값을 그대로 사용합니다.

> 아래는 실행 형식을 보존한 예시입니다. 2026-08-27에 사용한 operation ID와 확인 토큰은 폐기됐고
> 같은 시험을 그대로 반복하지 않습니다. 새 시험은 코드·상태·현장 중단 조건을 다시 검토한 뒤
> 새 operation ID로만 진행합니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml preflight \
  --operation-id scheduled_async_001 \
  --master wavemaker_left \
  --slave wavemaker_right \
  --slave-role async_slave \
  --mode constant \
  --master-power 35 \
  --slave-power 33 \
  --frequency 20 \
  --duration 150 \
  --verification-interval 2 \
  --bootstrap-active-schedule \
  --slave-power-after 38 \
  --power-change-after 60
```

출력된 `JFL-...` 토큰을 사용해 모든 인수를 동일하게 유지한
`run-native-linkage`를 실행합니다. ACTIVE 60초 후 슬레이브만 33%에서 38%로 바꾸며, 남은 시간
동안 master/slave의 fresh read-back을 계속 확인합니다. 요청한 power change가 실행되기 전에
전체 deadline이 끝나면 성공으로 처리하지 않고 즉시 원복합니다.

이 경로도 정전·컨테이너 강제 종료·VLAN 단절까지 포함한 절대 원복을 보장하지는 않습니다.
특히 TimerON snapshot이 남은 비정상 종료는 supervisor가 자동으로 다시 켜지 않고
`recover-linkage --confirm JFR-...` 확인을 요구합니다. 복구 journal이 없어지고 원래 TimerON,
제어값과 스케줄 해시가 모두 fresh read-back으로 일치하기 전에는 완료로 판단하지 않습니다.

## 5. 중단·복구

첫 신호는 정상 중지와 exact 원복을 요청합니다. 두 번째 신호는
`/hardware-safety/emergency-stop.latch`를 fsync한 뒤 bounded OFF를 시도하고 자동 ON 복구를
잠급니다. 프로세스가 죽으면 같은 run 명령을 반복하지 않습니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-device-verify --config /config/hardware-test.yaml verification-status
docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml status
```

최근 TimerOFF 기록은 supervisor가 `expires_at + 30초` 안에서 복구할 수 있습니다. stale,
TimerON, `safety_interlock` 또는 `schedule_changed` 기록은 현장 확인 토큰 없이는 한 건도
쓰지 않습니다. `schedule_changed`를 한 번 관측한 확인 시도는 즉시 멈추며, 스케줄을 확인한 뒤
새 status에서 발급된 새 토큰으로만 attended recovery를 다시 시작합니다.

`status`는 raw 오류나 장비 식별자 대신 typed recovery reason과 고정 blocker label만 표시합니다.
`PREPARED`는 blocker가 없지만, 그 밖의 record에 `timer_on_snapshot`,
`stale_or_clock_invalid`, `safety_interlock`, `schedule_changed` 중 하나라도 표시되면
`--recovery-first`를 사용하지 않고 새 `JFR-...` 토큰으로 attended recovery를 실행합니다.

```bash
docker compose exec jebao-flow-recovery \
  jebao-flow-device-verify --config /config/hardware-test.yaml \
  recover-device-verification --confirm JVR-REPLACE_WITH_STATUS_TOKEN

docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml recover-linkage
docker compose exec jebao-flow-recovery \
  jebao-flow-hwtest --config /config/hardware-test.yaml recover-linkage \
  --confirm JFR-REPLACE_WITH_RECOVERY_TOKEN
```

Emergency latch는 자동으로 지워지지 않습니다. 펌프 전원과 수조 상태를 현장에서 확인한 뒤,
필요하면 물리 전원을 먼저 차단하고 운영자가 명시적으로 latch 파일을 제거합니다. 그 후 status와
확인 토큰을 새로 받아 attended recovery를 실행합니다.

모든 intent가 `terminal`, journal이 `none`인지 확인하기 전에는 앱 시간표나 다른 제어기를 다시
켜지 않습니다. 시험 종료 후 private 설정을 다시 `dry_run: true`로 잠그고, 필요하면 앱에서
TimerON 스케줄을 수동으로 다시 활성화합니다.
