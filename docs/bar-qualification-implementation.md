# Bar 수류모터 스칼라 qualification producer — 1차 구현

기준 HEAD `bf1439d` 계열. 이 문서는 **소프트웨어 구현/오프라인 검증 범위**다. 장비 write·실기
qualification 발급·배포는 하지 않았고, 이 producer를 실행했다는 사실도 없다. 새 firmware 가설을
가짜 test/gate로 만들지 않았으며 기존 실행기·복원 의미만 재사용했다.

## 구현한 것

새 파일 `src/jebao_flow/local_control/bar_qualification.py` 와 직접 테스트
`tests/unit/test_local_control_bar_qualification.py` 만 추가했다. 공유 파일
(`qualification_producer.py`, `device_writer.py`, `catalog.py`, `protocol/`, frozen native,
CLI, `pyproject`)은 변경하지 않았다.

- `BarScalarQualificationProducer` 는 감사된 `ManualFlowQualificationProducer` 를 상속해 lease·
  resolver·journal 전이·artifact 기계·safe first-write envelope·`_manual_flow_state` 를 그대로
  재사용한다. 새 write primitive를 만들지 않는다.
- 지원 함수는 **`manual_frequency` 와 `manual_mode` 2종뿐**이다. 두 기능 모두 앱이
  Independent/TimerOFF/Constant 상태에서 **단일 필드 형식**으로 쓰는 manual 제어이고, 기존 scalar
  복원 계약으로 **정확히 자기 baseline을 복원**한다. **펌웨어 부작용 부재는 검증하지 않았다** —
  오프라인 테스트는 소프트웨어 계약(single-send, 자기 baseline exact 복원, verified 이후에만 영수증)
  만 증명하며 장비 수용·물리 안전을 주장하지 않는다.
- 계약은 manual-flow와 동일한 3프레임 `same -> step -> restore` 이다. `same` 은 현재값 재기록
  (no_change anchor), `step` 은 구별되는 값 1회, `restore` 는 baseline 재기록. 영수증은 durable
  journal이 `VERIFIED` 로 terminal이 되고 자기 baseline이 다시 쓰여 검증된 뒤의 `step`·`restore`
  두 실제 send에서만 나온다. 실패는 `RECOVERY_REQUIRED` 로 남고 영수증 0.
- `manual_frequency` 는 envelope가 Mode를 constant로 고정한 채 Frequency만 바꾸므로 기존
  `_require_state_envelope` 를 그대로 쓴다. baseline Frequency는 새 encoder 없이 기존 writer edge의
  probe(`prepare_scalar` 의 captured baseline)로 읽고, 앱 범위 `5..100` 밖이면 첫 write 전에 거부한다.
- `manual_mode` 는 trial이 pinned 필드(Mode)를 바꾸므로 step 단계에서만 완화한 envelope
  (`_require_bar_mode_envelope`, Mode ∈ 앱 manual 4모드)를 쓰고, baseline/restore는 Mode=constant를
  요구한다. 정확한 trial mode 일치는 기존 `verify_prepared_readback` 이 검증한다.

### 검증 결과

시험 환경 Python 3.12.14, pytest 8.4.2, `PYTHONPATH=src:.`.

- `tests/unit/test_local_control_bar_qualification.py` : **11 PASS**. manual_frequency/manual_mode
  성공(3-action journal·2 receipt·`sent[0]==sent[2]`·single-send), envelope 거부 5종 send 0,
  Frequency 앱범위 밖 거부, unsupported 함수·비-Bar 대상 거부, VERIFIED 재실행 offline(write 0).
- 관련 producer suite 회귀: `test_local_control_qualification_producer.py`,
  `test_local_control_power_qualification_producer.py`, `test_local_control_pro_qualification.py`,
  본 파일 합쳐 **122 PASS**.
- `ruff check` 신규 2파일 통과.

## 구현하지 않은 것 — 근거 코드 경로가 있는 blocker

Codex가 요청한 6종 중 나머지 4종과 전체 schedule은 **기존 복원 의미로 안전하게 구현되지 않아**
의도적으로 제외했다. 가짜 자격/추측 gate로 채우지 않는다.

- **`linkage_role`** — 안전한 자격에는 native own-original 복원 envelope가 필요하다. 그 기계
  (`prepare_native_controls`/`prepare_native_restore_controls`/`NativeWavemakerController`)는
  **Pro 전용**(`device_writer.py` 의 native helper가 non-Pro면 raise)이라 Bar에 없다. bare `{Linkage}`
  scalar write의 Bar 결합·부작용은 정적으로 미확정이라, 단일 필드 복원이 결합 drift를 되돌린다고
  보장할 수 없다.
- **`feeding_duration`** — baseline `FeedTime==0`(비활성)일 때 scalar 복원은 `{FeedTime:0}` 를
  forward한다. Pro의 baseline-0 raw-copy 처리(`device_writer.py:_prepare... prepare_scalar` 의
  `LOCAL_WAVEMAKER_PRO_PRODUCT_KEY` 분기)는 Bar에 적용되지 않고, Bar가 forward 0을 수용·round-trip
  하는지는 UNKNOWN(앱 min 1). baseline `FeedTime>=1` 전제를 강제하면 조건부 가능하나, 0 baseline은
  blocker다.
- **`feed_switch` / `pulse_or_tide`** — 단일 bool 필드지만, Bar timer/조작이 화면·현재 Auto 상태에
  따라 `Auto*` 를 함께 쓴다는 정적 근거(`docs/capabilities/registered-device-app-analysis.md`
  §Mode와 timer)가 있고, 이 두 bool의 partial-write shape가 부작용 없는 bare write라는 정적 확인이
  없다. 단일 필드 exact 복원이 결합 drift를 놓칠 수 있어 앱 정적 근거 확인 전에는 blocker다.
- **전체 `schedule` (384B)** — 복원 자체는 전체 image 캡처/재기록이라 오히려 깨끗하지만, 임의
  baseline 384B는 유효한 `WavemakerSchedule` 로 디코드된다는 보장이 없어 scalar의 same/step/restore
  프레임에 그대로 맞지 않는다(별도 A/B/원본-raw-restore 프레임 필요). 또한 Bar schedule write
  acceptance가 정적으로 UNKNOWN이고 `_EXECUTABLE_FUNCTIONS_BY_PRODUCT` 의 Bar 집합에 `schedule` 이
  없다. 별도 프레임 설계가 필요한 더 큰 단위라 1차에서 제외했다.

## Codex 조립측에 필요한 연결(내가 소유하지 않은 공유 파일)

이 producer가 만든 영수증이 실제 control로 이어지려면 아래가 필요하다. 모두 **실기 qualification
실행과 maintainer/on-site 승인**이 선행이며 소프트웨어만으로 자격을 승격하지 않는다.

- `catalog.py` `_EXECUTABLE_FUNCTIONS_BY_PRODUCT` 의 Bar 집합은 이미 `manual_frequency`·
  `manual_mode` 를 포함하므로, 실기로 발급된 Bar 영수증이 있으면 기존 catalog 경로가 그대로 열린다.
  producer 자체는 catalog를 바꾸지 않는다.
- CLI 노출(선택)은 Codex가 조립한다. 기존 `qualification-cli`/`power-qualification-cli` 와 같은
  패턴으로 `BarScalarQualificationProducer(function=...)` 를 감싸면 된다.

조립 후: 기존 `jebao-flow-local-qualify run --function manual_frequency|manual_mode`에 연결했다.
기본 manual_flow는 그대로이며 downward-step은 그 함수에만 필수다. 기존 config/manifest/
template/output 입력과 명시적 write opt-in을 유지한다. 복구는 기존 `recover --operation-id`를
사용하며 native lifecycle 복구와 섞지 않는다. 실제 qualification 실행은 하지 않았다.

## DC Pump Pro / Aquarium Pump 확장의 필수 장애 (짧은 조사)

Codex가 물은 "기존 scaffold를 DC scalar 및 Aquarium known power/feed_switch/feeding_duration 로
확장할 때 필수 장애"에 대한 read-only 결론:

- **공통 필수 장애** — 재사용하는 safe-envelope와 state 모델(`_manual_flow_family_profile`,
  `_MANUAL_FLOW_FAMILY_PROFILES`, `_manual_flow_state`)은 **공유 `qualification_producer.py`** 에 있고
  `local_wavemaker_pro` 와 `local_wavemaker`(Bar) 두 family만 안다. DC(`dc_pump_pro`)·
  Aquarium(`aquarium_pump`) 확장은 그 shared 파일에 family 프로파일을 추가해야 하므로, 내가 소유한
  파일 밖 수정이 선행이다.
- **Aquarium은 더 근본적** — Aquarium executable은 `power`/`feed_switch`/`feeding_duration` 뿐이고
  `manual_flow`/`manual_mode`/`linkage_role` 이 없다. 그런데 재사용 envelope와 `_manual_flow_state`
  는 Mode/Linkage/Flow datapoint 존재를 전제한다. 따라서 Aquarium은 profile 한 줄 추가가 아니라
  **manual-flow 비의존 state/envelope 모델**이 새로 필요하다. `PowerQualificationProducer` 도
  `_manual_flow_state` 에 의존하므로 그대로는 Aquarium에 못 쓴다.
- **`feeding_duration` 는 모든 제품 공통 blocker** — 위 Bar와 동일하게 baseline `FeedTime==0` 복원이
  Pro 전용 raw-copy에만 안전하다. DC/Aquarium/Bar 모두 0 baseline 복원 수용이 UNKNOWN이다.
- DC schedule mode code 1·2와 두 timer coupling의 화면 귀속, Aquarium `feed0`/transport 방향은
  정적으로 UNKNOWN이라(`registered-device-app-analysis.md` §감사 후 남은 것) 소프트웨어만으로
  실행 승격 불가다.

후속 [리턴모터 구현](return-pump-implementation.md)에서 DC profile과 DC scalar subclass,
Aquarium의 별도 power state/intent를 추가했다. 위 목록은 최초 조사 당시의 공백이며,
새 구현도 실장 자격 발급이나 미확정 timer/transport 동작의 확정은 아니다.
