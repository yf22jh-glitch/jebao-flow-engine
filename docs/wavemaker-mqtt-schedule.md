# 수류모터 전체 시간표 MQTT 계약

2026-09-16 소프트웨어 연결. 기존 `WavemakerSchedule` compiler와
`LocalControlCoordinator.execute()`를 그대로 사용한다. 새 펌웨어 동작·실장 자격·운영 배포를
주장하지 않는다. 후속 native Sync/Async lifecycle MQTT 연결과 미자격 장비의 읽기 전용
preview는 [펌프 MQTT API](pump-mqtt-apis.md)에 정리했다. 시각적인 슬롯 편집기는 별도다.

## 노출 조건과 HA 표면

현재 manifest의 **해당 물리 장비**가 `schedule` 실행 자격을 가지고 runtime이 control 모드인
경우에만 다음 요청 topic을 구독한다. 제품 공통 qualification만으로 다른 장비에 열지 않는다.
현재 catalog에서 이 조건에 도달하는 제품은 Pro다. Bar 시간표 자격을 이 변경으로 만들지 않는다.

```text
<prefix>/local/devices/<logical_member>/functions/schedule/request
```

HA의 기존 Schedule 진단 sensor는 유지한다. sensor 속성의 `schedule_request_topic`과
projection JSON에서 API 주소를 확인할 수 있다. `control_available: false`는 **HA entity 자체에
조작 위젯이 없다**는 뜻이다. API 노출은 현재 장비 상태·안전 승인·write 허용의 증명이 아니다.
실제 시간표 요약은 fresh read의 `observed` 속성으로만 갱신한다.

HA MQTT Text는 최대 255자이므로 48슬롯 JSON 편집기로 사용하지 않는다.
[공식 MQTT Text 계약](https://www.home-assistant.io/integrations/text.mqtt/#configuration-variables)
및 [공식 MQTT publish action](https://www.home-assistant.io/integrations/mqtt/#list-of-actions)을
기준으로 연결했다. HA의 `mqtt.publish`에서 아래 JSON을 **문자열 payload**로 보내거나 외부
편집기가 같은 계약을 사용한다. 이 문서는 자동화·대시보드를 설치하거나 장비 명령을 실행하지 않는다.

## 요청과 미리보기

요청은 ASCII JSON, 최대 32768 bytes, 정확히 `operation`, `request_id`, `schedule` 세 필드다.
JSON 중복 key와 알 수 없는 필드는 거부한다. `request_id`는 기존 LocalControlIntent의
8..128자 ASCII token 규칙을 따른다. topic의 장비 바인딩을 사용하므로 payload에 주소·신원·대상을
지정할 수 없다. `schedule`은 CLI와 동일한 family/slots 선언이다.

```json
{
  "operation": "preview",
  "request_id": "schedule_example_001",
  "schedule": {
    "family": "pro",
    "slots": [
      {
        "slot_index": 0,
        "start": "00:00",
        "end": "23:59",
        "mode": "sine",
        "flow": 35,
        "frequency": 30
      }
    ]
  }
}
```

`preview`는 compiler의 정규화된 slots, image 길이와 SHA-256을 반환한다. endpoint resolve,
장비 접속·read/write, journal 생성, 그룹 소유권 변경은 없다. 결과는
`hardware_write: false`, `device_acceptance: unverified`다. 현재 장비와의 diff나 실행 가능 여부를
검증한 것이 아니다. observer/미자격 설치는 이 apply 가능한 `/request` 경로가 없으며 기존
CLI 또는 후속 읽기 전용 `/preview` 경로를 사용한다.

## 적용·수정·삭제

동일 선언의 `operation`을 `apply`로 바꾸면 기존 qualified full-image 실행기로 전달된다.
명시적으로 승인된 operation에서만 사용한다. **부분 patch가 아니라 48슬롯 전체 교체**다.
하나를 수정할 때도 유지할 슬롯을 모두 포함하고, 지울 슬롯은 제외한다. 아래 선언은 전체 삭제다.

```json
{
  "operation": "apply",
  "request_id": "schedule_example_clear_001",
  "schedule": {"family": "pro", "slots": []}
}
```

TimerOFF·independent 전제, 장비 신원/자격 재검증, 출력 범위, group ownership, lease,
write 이전 원본 journal/fsync, 단일 전송, 전체 image readback 비교는 기존 실행기에서 유지한다.
타이머나 역할을 MQTT adapter가 자동 전환하지 않는다. 활성 native pair 편집을 이 API로 우회하지 않는다.
기존 미완료 거래가 있으면 전진을 거부하며 자동 복구·재시도·초기화하지 않는다.

동일 request ID와 정규화된 동일 선언의 재수신은 terminal journal 결과만 반환한다.
`physical_write_count: 0`이고 group ownership도 재취득하지 않는다. 같은 ID에 다른 선언을
보내거나 미완료 ID를 다시 보내면 거부한다. **다른 변경에는 새 ID**를 사용하고, timeout을 이유로
새 ID를 자동 생성하여 다시 적용하지 않는다. preview는 journal을 만들지 않으므로 같은 ID로
preview 후 apply할 수 있지만 preview가 그 ID의 예약이나 이전 apply 결과의 무효화는 아니다.
`slots` 배열의 순서도 선언의 일부다. 최종 image가 같아도 슬롯 나열 순서를 바꾸면 기존 ID와
충돌하므로 재조회에는 동일한 선언을 유지한다.

## 전달과 결과

- 요청: QoS 0, retain false. retained 또는 DUP 표시 메시지는 기존 adapter에서 실행 전에 거부.
- 결과: `<prefix>/local/requests/<request_id>/result`, QoS 1, retain false. 보내기 전에 결과 topic을 구독.
- preview 결과와 apply의 durable transaction 결과는 현재 장비 상태가 아니다. 요청 JSON을 HA state에
  복사하지 않는다. VERIFIED 재조회 역시 과거 성공이며 새 fresh read를 대신하지 않는다.
- 거부/예외는 기존 `<prefix>/local/status`의 비retained `status: rejected`와 안전한 reason으로 전달.
  현재 오류 경로는 request별 결과를 보장하지 않으므로 UI가 응답 부재를 성공으로 처리하면 안 된다.
  장비 오류 상세·복구 여부는 기존 CLI/journal로 확인한다. exception 원문/사설 주소는 MQTT로 보내지 않는다.
- 동작 중 broker 응답을 놓쳐도 자동 재전송하지 않는다. 운영자가 기존 ID의 durable 결과를 확인한다.

## 검증과 다음 범위

직접 테스트는 실제 compiler/coordinator/journal과 기존 byte-copy transport fixture로 whole image,
자기 원본 보존, 단일 write, replay, ID 충돌, readback mismatch 이후 RECOVERY_REQUIRED 차단,
미리보기 무접속, 미자격/observer 미노출, 소유권 충돌, retain/DUP 거부, 결과/상태 분리를 검증한다.
이는 펌웨어 가설의 증명이 아니다. writer·protocol·동결 하네스와 기존 qualification은 변경하지 않는다.

`serve`의 시작 검사도 schedule API를 제어 대상으로 센다. HA 위젯 수가 0이어도
`--write-enabled`, runtime의 dry-run 해제, 해당 장비의 `allow_hardware_writes`가 필요하다.
이 검사를 위해 CLI의 기존 제어 대상 집합에 schedule API를 포함했으며 별도 safety gate는 만들지 않았다.

### 오프라인 검증 기록

Python 3.12.14, pytest 8.4.2, macOS, shell umask 077에서 실행했다. 관련 CLI/HA/MQTT/compiler
시험은 152 PASS, `ruff check src tests`와 `git diff --check`는 PASS다.
처음 전체 실행은 CLI 시작 검사 보완 전 트리에서 2957 PASS / 4 FAIL / 경고 1건이었다.
해당 네 실패를 변경 전 `cd2b612` archive, 같은 Python·umask, 별도 pycache에서 대조했으며
4 FAIL / 경고 1건으로 같은 오류가 재현됐다.
CLI 시작 검사 보완 후 최종 source/test 트리의 전체 재실행은 **2960 PASS / 5 FAIL / 경고 2건**,
129.31초다. 별도 pycache로 현재 source를 다시 로드했다. 추가 실패는 아래 conflicting receipts
경쟁 시험이며, 변경 전 archive에서 다섯 시험을 함께 대조해 **5 FAIL / 경고 2건**을 재현했다.

| 실패 | 확인한 근거 |
|---|---|
| `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults` | macOS에 Linux `/proc/sys/kernel/random/boot_id`가 없어 기존 fail-closed 예외가 발생. |
| `test_concurrent_same_receipt_contenders_converge_to_same_exact_success` | receipt claim lock open의 FileNotFoundError로 한 thread가 성공 결과를 남기지 못함. 변경 전에도 같은 경로이며 근본 원인은 여기서 확정하지 않음. |
| `test_concurrent_conflicting_receipts_have_exactly_one_winner` | 최종 전체 실행과 변경 전 대조에서 같은 claim lock open 예외가 재현됨. 경쟁 실패를 수정하거나 Linux 정상으로 단정하지 않음. |
| `test_missing_receipt_and_unsafe_output_metadata_fail_closed` | umask 077 때문에 시험의 `mkdir(mode=0755)`가 실제 0700을 만듦. 실제 mode를 확인했고 변경 전 archive에서 umask 022로 실행하면 PASS. |
| `test_private_directory_mode_is_required` | 위와 같은 mode 확인 및 umask 022 대조 PASS. |

기존 실패를 무시하거나 전체 PASS로 보고하지 않는다. 공유 guard/receipt 코드는 이번 범위에서
수정하지 않았다. encoder/writer schema digest는 변경 전과 같은
`81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`이다.

검사 로그는 gitignored `data/wavemaker-mqtt-tests.CG8RtW/`에 보존한다.
`full-final.log` SHA-256은 `0ada13d5944213415323c5371c7f0172ad84b85fc2ee005c8d62704ff67733a2`,
변경 전 대조 `baseline-five.log`는
`afce72774b1af5955eaaf9e0adf586d7d6f8f434acf992569edfb7fe395b3919`다.

### 독립 검토

Orca Agent Bridge로 Claude가 HA/MQTT를 읽기 전용 정적 검토한 결과 P1 차단 소견은 0건이었다.
별도 후속 검토에서 CLI 시작 검사의 schedule-only 누락은 P1에 해당하며 위 수정으로 닫혔음을
확인했다. `--write-enabled`는 runtime catalog에서도 미리 제한되지만 dry-run/장비별 잠금에는
이 대상 집합 보완이 필요했다. 직접 회귀 테스트는 통과했고 전체 suite의 기존 실패는 위에 기록했다.
리뷰 자체는 실기나 pytest 재실행의
증거로 세지 않는다. 슬롯 배열 순서에 민감한 멱등 계약은 위 본문에 명시했다.
표시 비대칭 후보인 `GROUP_FORCED_OFF_WILL_REVERT`는 실제
`group_supervisor.claim_manual_ownership()`에서 `power_on_requested=True`일 때만 결정에 담긴다.
새 schedule 경로는 False를 전달하므로 현재 production 경로의 추가 clear 로직은 넣지 않았다.

위 검증 당시 미완료였던 native HA/MQTT 연결과 일부 Bar producer의 후속 소프트웨어 구현은
[남은 기능 작업 기록](wavemaker-remaining-functions.md)에 있다. 시각적 슬롯 편집기,
Bar 나머지 자격·Timer 복합 명령·clock sync 실행 의미, 운영 미완료 source의 해결과 배포는
여전히 별도다. 이 API 추가로 전체 제품 완성을 선언하지 않는다.
