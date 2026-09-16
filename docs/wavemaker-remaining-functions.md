# 남은 수류모터 기능 — 2026-09-16 재개 범위

사용자는 HA 대시보드를 조작 화면으로 사용하며, 기존 Sync/Async 구현을 다시 만들지 않고
남은 기능을 구현하라고 요청했다. 별도 frontend는 만들지 않는다. 이번 범위는 소프트웨어와
오프라인 검증이며 장비·앱 조작, 자격 발급, 운영 배포 또는 기존 미완료 source 종료가 아니다.

닫을 질문: 기존 native 실행기를 HA의 MQTT 요청에서 현재 대상·쓰기 잠금·원본 소유·멱등성·
전용 복구 의미를 유지하며 호출할 수 있는가. 근거는 기존 실행기·앱 정적 계약과 transport/
durable artifact 계약 시험이다. Bar/clock은 별도로 앱 근거와 구현 가능 계약을 조사한다.

## 구현 범위와 유지할 계약

- `local_control/ha.py`, `mqtt.py`, `local_control_cli.py`와 직접 tests·문서에서 기존
  `NativeWavemakerController`의 preview/attach/detach/status/recover와 기존 소유 지원·master cycle
  호출을 연결한다. 새로운 복구 시퀀스나 자동 대안은 만들지 않는다.
- 명시적인 write-enabled runtime에만 native 요청 경로를 노출한다. 현재 manifest의 Pro
  대상만 허용하고 runtime dry-run 및 대상별 write 잠금을 적용한다. attach 자격은 실제
  실행기에서 현재 신원별로 다시 확인한다. 자기 원본 복구를 새 attach 자격에 종속시키지 않는다.
- 모든 실행은 scalar·그룹·poller와 같은 operation lock을 사용한다. 새 attach는 두 장비의
  직접 소유권을 요청하며 replay는 소유권을 다시 바꾸지 않는다. 해제/복구에는 보존된 원본
  대상을 사용하고 현재 manifest의 stable identity/role과 일치시킨다.
- native recover만 호출한다. generic recover를 성공 attach의 undo로 바꾸지 않는다.
  기존 자동 원복/명시 관찰 보류/master cycle 옵션의 의미는 유지하고 자동 재시도하지 않는다.
- retained/DUP 요청 거부, 결과 비retained, 예외 원문 비공개, 역사 결과를 현재 상태로 출판하지
  않기, 미완료 source의 전역 차단을 유지한다. 미리보기와 상태 조회는 장비에 접속하지 않는다.
- frozen native 여섯 파일·protocol·공용 guard·writer/schema 입력·기존 자격은 수정하지 않는다.
  Bar timer/clock에 근거 없는 wire·readback·복원 동작을 추가하지 않는다.

Bar schedule의 384B 복원 자격과 추가 scalar producer, timer 복합 명령, clock의 진행하는 시각
검증 계약은 독립 정적 조사 결과로 판단한다. 확정 가능한 소프트웨어와 미확정/실장 필요를
구분하며 남은 것을 완료로 표시하지 않는다. 이번 구현 결과 커밋은 구현한 범위만 소진한다.

## 후속 요청 — 리턴모터와 도징기

사용자는 등록 리턴모터 두 대도 기능 구현에 포함하고, 도징기는 이전 요구를 확인하면서
진행하라고 추가했다. 등록 분석상 DC Pump Pro 1대와 Aquarium Pump 1대다. 새 live inventory
조회는 하지 않았으며 실제 모델·신원을 제품 이름만으로 다시 매핑하지 않는다.

`pump_preview.py`와 CLI/MQTT에 기존 앱 근거의 clock 및 리턴 schedule 미리보기를 연결한다.
DC schedule code 1/2를 manual mode로 추측하지 않고, Aquarium feed의 마지막 0 byte는 앱 형식
미리보기로만 다룬다. 실제 clock write 성공 판정과 복원 의미는 별도 계약이 필요하므로 새 write
경로는 만들지 않는다. Bar 추가 producer는 Claude가 새 `bar_qualification.py`와 직접 tests·문서에
한정해 검토/구현하며, 실행·영수증 발급·catalog 승격은 하지 않는다.

도징기의 보존된 daily/average planner는 하루 총량과 횟수의 정확 분배 및 정수 용량의 여러 날
평균 계획이다. 소수 투입량을 몰래 반올림·배율 변경하여 장비에 보내지 않는다. 사용자의 추가
조건을 확인 중이며, 이를 네이티브 실장 기능으로 완료 처리하지 않는다.

## 소프트웨어 연결 결과

[MQTT API 계약](pump-mqtt-apis.md): 기존 Sync/Async 실행기·소유 해제·복구·상태 조회와
네 제품의 읽기 전용 schedule/clock preview를 연결했다. DC/Aquarium의 preview compiler 및
오프라인 CLI도 추가했다. HA integration 지침대로 실행 의미는 기존 domain 실행기에 남기고
HA는 MQTT 표면만 제공하며 별도 화면·자동화를 만들지 않았다.

[Bar producer](bar-qualification-implementation.md)는 Claude가 manual_mode/manual_frequency
두 기능으로 한정 구현했다. 기존 qualification CLI의 `run --function`으로 선택하고
기본 manual_flow의 기존 `--downward-step` 계약은 유지한다. Bar 두 기능에는 downward-step을
받지 않는다. 기존 `recover`는 native 복구가 아니라 이 scalar 자격 거래의 자기 원본 복구다.
코드 노출이 live operation 승인이나 실제 qualification 발급을 뜻하지 않는다.

Claude 독립 검토는 native/preview 표면의 P1 0건이었다. 순차 그룹 소유권 취득 후 두 번째
멤버가 거부될 때 첫 멤버의 수동 소유권이 남는 특성은 API 문서에 명시했다. 넓은 예외 포착은
기존 실행기의 durable 진단을 `status: failed`로 전달하기 위한 것이며, 현재 상태나 성공으로
승격하지 않는다. 추정에 따른 추가 gate나 generic 복구 우회는 넣지 않았다.

writer·protocol·frozen 하네스·공유 guard는 불변이며 schema digest도
`81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`로 유지했다.
장비 접속·write·배포 0회. 미완료 live source는 이 작업으로 변경·해소하지 않았다.

### 검증 기록

Python 3.12.14 / pytest 8.4.2, macOS, umask 077. 첫 전체 실행은 3025 PASS / 6 FAIL이었다.
새 실패 1건은 group transport 시험의 `_DirectRouter`가 새 `request_topics` 인터페이스를
구현하지 않아 구독 단계에서 실패한 것이었다. 테스트용 router에 기존 command 목록을 request
목록으로 제공하도록 반영한 뒤 group transport 31 PASS, 전체 재실행 **3031 PASS / 5 FAIL /
경고 2건**(112.51초)이다. 나머지 5건은 [이전 변경 전 대조](wavemaker-mqtt-schedule.md#오프라인-검증-기록)와
같은 boot identity·두 receipt 경쟁·두 permission 시험이며 전체 PASS로 보고하지 않는다.

전체 수집 이후 Bar의 transport failure/recovery 시험 3개를 추가했다. 별도 최종 관련 suite는
**240 PASS**(Bar 14개 포함, 5.17초), Ruff와 `git diff --check` PASS다. native 실행 결과를
현재 상태로 승격하지 않음, observation 대기와 원래 source의 불명확 delivery 보존, preview
무접속, 현재 identity/쓰기 opt-in, Bar 영수증 없는 실패와 자기 baseline 복구를 검사했다.
오프라인 결과는 장비의 수용·물리 동작 검증이 아니다.

gitignored 검사 로그는 `data/pump-api-tests.Uod0KQ/`에 있다.
`full-final.log` SHA-256:
`d13eec829300c7badcf4fda2271792c60527a05c0b70e300c84e248dad3dfa91`.
`targeted-final.log` SHA-256:
`160204dbd7567194b7c36f8e5d9c0169236780361b81187bcb07405f290c4178`.
