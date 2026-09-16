# 리턴모터 제품 구현 재개 — 2026-09-16

사용자는 등록된 리턴모터 두 대도 기능 구현에 포함했고, 미리보기만 추가한 중간 종료 뒤 실제
진행 여부를 물으며 작업 재개를 요청했다. 대상은 보존된 inventory의 DC Pump Pro와 Aquarium
Pump이며 live inventory를 새로 조회하지 않는다. 도징기는 기존 별도 요구를 유지한다.

닫을 소프트웨어 질문: 기존 scalar 실행기가 있는 리턴모터를 수류모터 전용 state/qualification
모델 때문에 검증하지 못하는 공백을, 제품에 없는 필드를 발명하지 않고 채울 수 있는가.
근거는 기존 앱 catalog·제품 schema·writer 계약과 durable transport 테스트다. 이것은 장비
수용이나 물리 효과에 관한 질문의 답을 대신하지 않는다.

첫 최소 단위는 DC의 기존 manual_flow/power producer 지원이다. DC에는 Linkage가 없으므로
프로파일에서 해당 전제를 적용하지 않고 상태에는 None으로 명시한다. 기존 Pro/Bar의
independent 전제·출력 범위·journal·single-send·전원 실패 후 OFF 정책은 그대로 유지한다.
허용 파일은 qualification_producer.py, power_qualification_producer.py와 직접 tests/CLI/docs다.
Aquarium의 다른 필드 구조는 독립 정적 검토 뒤 별도 최소 범위를 정한다.

frozen native·protocol·공유 hardware guard·writer·catalog 실행 승격은 변경하지 않는다.
장비 write·실기 자격 발급·배포는 이번 소프트웨어 요청에서 추론하지 않는다. writer 변경이
필요해지면 기존 receipt currency에 미치는 영향을 먼저 보고하며 fingerprint를 우회하지 않는다.

## DC 구현

- 기존 manual_flow와 power producer가 DC 제품을 지원한다. TimerON은 현재 status에서 읽고
  Linkage는 `None`(제품에 없음)이다. 수류모터의 independent 상태를 가짜로 보고하지 않는다.
- `DcScalarQualificationProducer`는 기존 Bar scalar의 same/step/원본복구 scaffold를 재사용해
  manual_mode/manual_frequency/feeding_duration을 지원한다. Bar 기본 producer는 기존 두
  함수만 지원한다. DC top-level Mode는 앱 정의를 사용하며 미확정 schedule mode 1/2와
  혼동하지 않는다.
- feeding_duration은 기존 writer가 원래 값을 다시 인코딩할 수 있을 때만 진행한다.
  FeedTime=0 baseline은 현재 writer에서 거부되므로 journal/write/receipt 0이며, 임의 1로
  바꾸거나 Pro raw-copy 예외를 옮겨 심지 않는다.
- 기존 qualification CLI에서 현재 대상의 product family에 따라 Bar/DC producer를 선택한다.
  manual_flow만 downward-step을 요구하며 power는 기존 power qualification CLI를 사용한다.
- 실제 자격을 발급하면 기존 catalog·coordinator·HA가 해당 물리 장비의 함수를 노출하는 구조다.
  이번 구현은 장비에 접속하거나 자격을 발급하지 않으며, 테스트 영수증은 test 전용 임시 저장소다.

형식상의 호출은 아래와 같다. 이것은 실행 승인이나 live operation 수행 기록이 아니다.

```text
jebao-flow-local-qualify run --function manual_flow --downward-step 1 <기존 승인·현재 입력>
jebao-flow-local-qualify run --function manual_mode <기존 승인·현재 입력>
jebao-flow-local-qualify run --function manual_frequency <기존 승인·현재 입력>
jebao-flow-local-qualify run --function feeding_duration <기존 승인·현재 입력>
jebao-flow-local-power-qualify <기존 승인·현재 입력>
```

위 코드로 전체 제품 완료를 선언하지 않는다. 현재 DC timer coupling, schedule mode 1/2,
schedule/reset의 자격 발급 및 clock 성공 판정은 별도이며 배포도 하지 않았다.

## Aquarium 전원 구현

`AquariumPowerQualificationProducer`는 별도의 실제 상태/intent 모델을 사용한다.
SwitchON·TimerON·Motor_Speed·boolean transport Mode를 읽고, 없는 Flow/Linkage/waveform
mode를 발명하지 않는다. Motor_Speed는 앱 30..100, 현재 config 한도, 기존 첫 write 상한 45의
교집합이어야 한다. 이 조건은 물리 안전 범위를 실장 검증했다는 뜻이 아니다.

기존 `PowerQualificationProducer`에는 state/intent 구성 hook만 추가하고, 동일한
same_on → power_off → planned_on, journal-first, 단일 전송, receipt finalization을 재사용한다.
실패 시 복구 목표는 **OFF**이며 원래 ON을 다시 켜는 일반 scalar 복원과 혼동하지 않는다.
실패 뒤에는 재시도하거나 영수증을 발급하지 않는다. 기존 Pro/Bar/DC의 intent 필드와
canonical 직렬화는 유지하며 Aquarium만 별도 intent 모델을 사용한다.

기존 power qualification CLI는 current manifest의 제품을 읽어 해당 producer를 선택한다.
현재 config/manifest/content 확인과 명시적 write opt-in 이전의 장비 접속 금지는 유지한다.
이것은 전원 기능의 소프트웨어 자격 발급 경로이며 실제 장비 검증/자격 발급/배포는 아니다.

Aquarium motor_speed의 wire 필드는 알려졌지만 현재 writer/catalog 매핑은 없다. 이를 추가하면
공용 writer/schema digest가 바뀌어 기존 Pro/Bar 자격이 새 버전에서 더 이상 current가 아니다.
사용자에게 그 영향을 설명하고 코드 추가 여부를 질문했으며, 답변 전에는 writer를 수정하지 않는다.
transport bool의 AP/Wireless 방향, timer coupling, 급이의 실제 효과 등 미확정 항목은 별도다.

## 협업·검토 범위

Claude가 Aquarium의 정적 조사, 별도 state/intent 및 Power hook, 최초 11개 테스트를 작성했다.
사용자가 Claude token limit을 알리며 단독 진행을 지시했다. Codex가 이어서 source를 검토하고
power CLI 연결, CLI 제품별 회귀, 현재 epoch validator 시험, lint·전체 검증을 마무리했다.
이번 DC 변경에 대한 Claude의 후속 독립 리뷰는 수령하지 못했으므로 완료된 리뷰로 세지 않는다.

도징기는 변경하지 않았다. 기존 하루 총량·횟수 분배와 여러 날 평균 계획을 보존한다.
두 리턴모터 구현이나 이 테스트를 도징기 실행 근거로 전용하지 않는다.

## 오프라인 검증 결과

- 관련 회귀: **194 passed**. DC scalar 자기 baseline 복원·실패 후 재전송 금지,
  DC/Aquarium 전원 실패 후 OFF 복구·실패 영수증 0, 제품별 CLI 선택을 확인했다.
- DC 테스트 영수증을 실제 catalog·HA projection에 전달해 자격이 있는 물리 identity에만
  제어가 노출되고, 같은 제품의 다른 identity에는 노출되지 않는 것도 확인했다.
- 최종 전체 suite: **3073 passed, 4 failed, 1 warning**. 전체 통과가 아니다.
  네 실패는 앞선 baseline에서 재현된 다섯 항목의 부분집합이다. Linux boot identity를
  전제한 exact-restore 시험 하나, concurrent receipt claim 시험 하나, umask 077 아래의
  directory-mode 시험 두 개다. 앞선 실행에서 실패했던 다른 concurrent claim 시험은
  이번에는 통과했으므로, 경합 문제가 해결됐다고 해석하지 않는다.
- Ruff와 diff whitespace 검사 통과. writer/protocol/frozen 파일은 변경하지 않았고 schema
  digest는 `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`로 유지했다.
- 조건: macOS, Python 3.12.14, pytest 8.4.2, 별도 bytecode cache, 기존 umask 077.
  로그는 gitignored 논리 경로 `data/return-pump-tests.E8S6cv/`에 보존했다.
  `targeted-final.log` SHA-256:
  `d3889069e258d1cdb48d7bad68720228938f3b26ee4f0fdc0f098d79018abf2c`;
  `full-final.log` SHA-256:
  `0a94ebbbbd15d293137d093f0b837d53cc342a2198f2bf25ab8416f3bbbcbd69`.

장비 연결·실제 write·자격 발급·배포는 모두 0이다. 이 결과는 소프트웨어 연결 공백을 채운
증거이며, 장비 수용·생물에 대한 안전성·전체 리턴모터 기능의 완료 근거가 아니다.

## 후속 범위

사용자가 Aquarium 속도 writer 변경과 자격 갱신 영향을 승인한 뒤
[남은 제품 구현](remaining-product-implementation.md)으로 이어졌다. 그 후속 범위에는
Aquarium 속도·조건부 급이시간, schedule/raw restore·reset 자격 producer와 도징 계획 API가
포함된다. 이 문서의 "writer 불변/schema 유지"는 앞선 83037b1 범위의 기록이다.
