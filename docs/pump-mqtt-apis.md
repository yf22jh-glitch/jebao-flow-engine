# 펌프 MQTT API — native lifecycle와 읽기 전용 미리보기

후속 운영 연결: [2026-09-16 observer 배포](runs/2026-09-16-observer-preview-deployment.md).
상태 조회·11개 preview topic만 활성화됐고 아래 native/apply 명령은 운영에 열리지 않았다.

2026-09-16 소프트웨어 연결. HA 대시보드는 기존 MQTT publish action으로 이 계약을 사용할 수
있다. 대시보드 설치·운영 배포·장비 자격 발급·실기 검증은 이 변경에 포함하지 않는다.
실행기는 기존 `NativeWavemakerController`이며 새 펌웨어 동작이나 복구 알고리즘은 없다.

## Native Sync/Async

`serve --write-enabled`의 control runtime에서 현재 manifest에 Pro 두 대가 있을 때
`<prefix>/local/wavemakers/request`를 구독한다. runtime dry-run과 두 장비의 write 잠금을
기존 시작 검사로 확인한다. attach는 두 물리 신원의 기존 9개 기능 자격을 요구한다.
자기 원본의 해제·복구는 새 attach 자격 없이도 사용하지만 journal의 대상과 현재 manifest의
product/physical identity/logical role가 일치해야 한다. observer에서는 이 경로가 없다.

`ha-schema`의 `native_wavemaker_api`와 각 Pro Schedule sensor 속성에서 주소·멤버·
attach 자격 멤버를 확인한다. 목록에 있는 것과 지금 실행 가능한 것은 다르다. 기존
RECOVERY_REQUIRED 거래는 여전히 새 write를 막는다.

모든 요청은 bounded ASCII JSON(32768 bytes 이하), 중복 key 금지, 미지 필드 금지다.
`request_id`는 기존 8..128자 ASCII token 규칙을 따른다.

| operation | 추가 필수 필드 | 의미 |
|---|---|---|
| `preview` | `plan` | CLI NativeWavemakerPlan과 같은 선언, 장비·journal·소유권 접촉 없음 |
| `attach` | `plan` | 기존 결속 실행. 새 ID만 직접 소유권 요청 |
| `status` | `record_id` | 보존된 이력 조회, 현재 상태 검증 아님 |
| `detach` | `attachment_id` | 결속 전 자기 원본 시간표·제어값 복원 |
| `recover` | `record_id` | `recover_native` 호출. generic recover 사용 안 함 |
| `assist_detach` | `record_id` | 기존 소유 해제 지원 계약. 임의 강제 해제 아님 |
| `cycle_master` | `record_id` | 원래 source의 명시 승인 옵션을 요구하는 기존 master cycle |

`attach`에만 선택적 boolean `await_operator_observation`,
`master_cycle_on_async_failure`를 허용하며 둘 다 Async 전용이다. 기본 false다.
관찰 대기·불명확 delivery·master cycle의 별도 결과를 기존 실행기 그대로 반환한다.
master cycle 성공은 원래 source의 UNCERTAIN/RECOVERY_REQUIRED 해소를 뜻하지 않는다.
해제 실패 뒤 generic undo로 다시 결속하는 우회 경로는 없다.

```json
{
  "operation": "preview",
  "request_id": "native_preview_001",
  "plan": {
    "mode": "sync",
    "master": "master",
    "schedule": {
      "family": "pro",
      "slots": [{"slot_index": 0, "start": "00:00", "end": "24:00",
                 "mode": "constant", "flow": 35, "frequency": 20}]
    },
    "slaves": [{"logical_member": "slave", "flows": [{"slot_index": 0, "flow": 30}]}]
  }
}
```

논리 멤버는 설치 manifest에 맞춘다. 위 예시는 preview이며 실제 operation 승인이 아니다.
실행 시 adapter는 scalar·group·poller와 같은 operation lock을 사용한다. 동일 attach ID를
다시 보내면 기존 controller가 동일 선언인지 검사하고 이력만 반환한다. 소유권을 재취득하거나
재전송하지 않는다. 복구 결과의 내부 request ID는 기존 deterministic 복구 ID이고,
바깥 `request_id`는 MQTT 응답 상관관계 ID다. 결과를 요청 plan으로부터 HA state에 복사하지 않는다.
두 멤버의 그룹 직접 소유권 요청은 기존 service에서 순차 처리한다. 두 번째 멤버가 거부되면
장비 attach는 시작하지 않지만 첫 멤버의 수동 소유권은 남을 수 있다. 자동 그룹 재개를 끼워
넣지 않으며, 운영자는 기존 그룹 상태·멤버 resume로 후속 처리를 판단한다. 원본 복구는 새
attach 소유권 취득을 요구하지 않고 기존 소유 복구·공유 lock·lease 계약을 따른다.

## 네 제품의 읽기 전용 미리보기

Pro·Bar·DC Pump Pro·Aquarium Pump의 Schedule 및 Device clock 진단 sensor에
`preview_request_topic`을 제공한다. `serve`와 `ha-schema`에서 사용하며 observer/미자격
장비에도 존재한다. coordinator나 write-enabled가 필요 없고 명령 전송은 불가능하다.

```text
<prefix>/local/devices/<member>/functions/schedule/preview
<prefix>/local/devices/<member>/functions/device_clock_sync/preview
```

schedule 요청은 `request_id`, `schedule` 두 필드만 허용한다. family는 `pro`, `bar`,
`dc_pro`, `aquarium` 중 topic의 현재 장비 제품과 일치해야 한다. Pro/Bar는 기존
WavemakerSchedule을 재사용한다. 리턴모터는 다음과 같이 앱 정적 근거가 있는 필드만 사용한다.

```json
{"request_id":"return_preview_001","schedule":{"family":"dc_pro","slots":[
  {"slot_index":0,"start":"06:00","end":"12:00","mode":"constant",
   "flow":35,"frequency":20,"feed_time":0}
]}}
```

DC는 48×8 bytes, constant/random/feed만 명명한다. mode code 1/2는 manual 모드 이름으로
추측하지 않는다. 동일·역순 시각은 앱처럼 비활성 EE 슬롯이며 overnight wrap으로 바꾸지 않는다.
Aquarium은 48×6 bytes, stopped/auto/feed이고 auto만 `speed: 30..100`을 받는다.
앱의 feed 마지막 byte 0을 보존하되 실제 급이 동작·안전 범위를 검증했다고 주장하지 않는다.
`slots: []`는 오프라인 전체 비활성 image이지 실행 명령이 아니다.

clock 요청은 `request_id`, `local_time` 두 필드다. 호출자가 UTC offset을 명시해야 하며
실행 호스트의 현지 시각을 임의 선택하지 않는다.

```json
{"request_id":"clock_preview_001","local_time":"2026-09-16T12:34:56+09:00"}
```

기존 app-shaped builder의 byte 길이·digest를 반환한다. wire에는 timezone이 없고 초 미만은
제외한다. `verification_contract: not_defined`, `restore_previous_clock: false`이며 실제
clock sync가 아니다. 진행하는 시각의 성공 판정·실패 처리 계약 결정 전에는 apply를 넣지 않는다.
모든 preview는 `hardware_write: false`, `device_acceptance: unverified`다.

오프라인 CLI도 제공한다:

```text
jebao-flow-local plan-return-pump --file plan.json
jebao-flow-local plan-device-clock --product-key <product-key> --local-time 2026-09-16T12:34:56+09:00
```

## 전달·결과·남은 범위

요청 QoS 0/retain false. retained/DUP 메시지는 실행 전 거부한다. 결과는
`<prefix>/local/requests/<request_id>/result`, QoS 1/retain false다. 바깥 `status: result`는
응답이라는 뜻이며 실제 성공은 안쪽 `accepted`, `phase`, `current_state_verified` 등을 읽는다.
native 예외는 바깥 `status: failed`와 보존 가능한 durable 진단을 반환한다. 유효하지 않은
요청·신원 불일치 등은 기존 `<prefix>/local/status`에 안전한 reason으로 거부되므로 request별
응답을 항상 보장하지 않는다. 응답 없음은 성공도 재전송 허가도 아니다.

리턴모터의 기존 scalar encoder/executor와 HA 진단은 보존한다. 이번에 schedule·clock을 live
조작으로 승격하지 않는다. Bar timer의 Auto 필드 결합, DC timer/mode 1·2, Aquarium transport
방향·속도 범위·feed 동작, clock 검증은 미확정이다. 앱 근거와 승인된 장비 검증이 필요하다.

도징기는 [기존 요구 및 planner](local-control.md#automatic-daily-dosing-planner)를 따른다.
하루 총량·횟수의 exact Decimal 분배와 정수 투입량의 여러 날 평균 계획은 이미 있다.
1mL 미만을 몰래 반올림·배율 변경하지 않으며 calibration의 10mL를 시간표 최소량으로 혼동하지
않는다. 도징 live write와 임의 신규 정책은 이번 변경에 넣지 않았다.

## 보류의 영향과 재개 조건

Bar 나머지 기능과 리턴모터의 현재 근거는 [앱 분석](capabilities/registered-device-app-analysis.md),
[Bar producer 조사](bar-qualification-implementation.md)에 있다. encoder 존재는 수용 자격이 아니다.
위 조사 당시 DC는 qualification 공통 profile에 없었고 Aquarium은 Mode/Linkage/Flow 전제와
맞지 않았다. 후속 [리턴모터 구현](return-pump-implementation.md)에서 DC scalar producer와
Aquarium 전원의 별도 상태 모델을 추가했다. 실제 receipt 발급은 여전히 별도다.
대안은 (1) 앱 정적 근거를 더 확보하고 제품별 원본 복원·producer 계약을 별도로 설계하거나,
(2) 현장 승인을 받아 미확정 항목을 가르는 최소 앱 조작과 raw 관측을 수행하는 것이다.
재개에는 해당 계약·근거와 실기 operation별 maintainer/on-site 승인이 필요하다. 그동안 기존
qualified 기능은 유지하고 나머지는 진단·미리보기까지만 제공한다. 새 receipt나 실행 승격은 없다.

Clock은 (1) maintainer가 진행 시각의 허용 오차·readback·실패 후 처리 계약을 결정한 뒤 구현하거나,
(2) 계속 미리보기로 두고 앱에서 시각을 관리할 수 있다. 현재는 후자에 해당하며 clock apply는 없다.
도징은 보존된 요구를 유지하되 추가 조건과 interval의 실제 의미를 확인하기 전 live write를 열지 않는다.

## 도징 계획 API 추가

`channel_schedule` 진단의 `preview_request_topic`에 다음 요청을 보낸다. 경로는
`<prefix>/local/devices/<logical_member>/functions/channel_schedule/preview`이며 observer 모드에서도
사용할 수 있다. 별도 화면이나 HA custom integration은 없다. 결과 topic은 위 공통 규칙을 따른다.

```json
{
  "request_id": "dosing_plan_0001",
  "plan": {
    "method": "daily",
    "channel": 1,
    "daily_amount": "13",
    "dose_count": 4,
    "first_dose_minute": 90,
    "amount_step": "1"
  }
}
```

네 번의 합계를 정확히 13으로 유지하고 하루에 고르게 분산한다. 양은 정수 또는 bounded decimal
문자열만 받으며 JSON 실수·지수 표기는 받지 않는다. 명시한 `amount_step: "0.5"`의 소수 계획은
유지하되, 소수 dose를 integer mL로 반올림하거나 이미지로 내보내지 않는다.

```json
{
  "request_id": "dosing_average_0001",
  "plan": {
    "method": "average_interval",
    "channel": 2,
    "target_daily_average": "0.5",
    "dose_count": 1,
    "first_dose_minute": 90
  }
}
```

위는 2일마다 1 mL라는 **로컬 계획**이다. 이 모드의 `dose_count`는 매일이 아니라 투여일의
횟수다. 3회면 정확한 평균을 유지하는 가장 짧은 간격인 6일에 1 mL씩 3회다. 30일 이내에
정수 dose로 표현할 수 없으면 거부하며 기약 없는 반올림은 하지 않는다.

결과에는 개별 시간·양, 계획 digest, 가능한 경우 96-byte 채널 이미지의 크기/digest가 있다.
항상 `hardware_write=false`, `wire_send_available=false`, `calendar_schedule_installed=false`이며
physical unit와 장비 interval 의미는 unknown이다. power/manual dose/timer/calibration/apply
명령은 이 API에 없다. 실제 장비 상태로 계획 값을 낙관적으로 발행하지 않는다.

후속 구현·현재 보류 목록은 [남은 제품 구현](remaining-product-implementation.md)을 따른다.
Aquarium 속도 writer 및 compiler 지문 결속이 추가돼 새 버전의 schema digest가 바뀐다.
앞 절의 기존 qualified 기능 보존은 구 운영 버전에 관한 설명이며, 새 버전에 예전 자격이
자동 승계된다는 뜻이 아니다. 이번에는 배포하지 않았다.
