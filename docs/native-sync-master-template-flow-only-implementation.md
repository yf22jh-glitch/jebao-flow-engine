# Sync 공통 시간표·Flow 전용 차이 구현

## 범위와 결과 구분

정책 `3aa263a`가 사용자의 "그냥 진행하라니깐"을 근거로 해제한 source4·test4만 수정했다.
실기 질문과 세 구간은 [시험안](native-sync-master-template-flow-only.md)에 있다.
이 기록은 구현·격리 검증이며 **새 실기 결과가 아니다**. 장비 write·앱 조작·운영 배포0이다.
직전 Sync의 자동 창 미달·종료 오류를 해결했다고 주장하지 않는다.

## 새 계획과 기존 계획의 분리

`master_template_flow_only=true`를 상위 spec과 role spec에 보존한다. false는 직렬화에서
생략하여 기존 확인 토큰·기록 로드 형식을 유지한다. true는 정확한 wave 계획의 Sync에만
허용한다. 기존 Sync·Anti의 반대 파형 계획 및 오래된 fixed 계획은 변경하지 않는다.

| 구간 | 양쪽 Mode/Frequency | master Flow | slave Flow |
|---|---|---|---|
| 초기 | Sine/F30 | 35 | 30 |
| 첫 경계 | Constant/wire F0 | 32 | 40 |
| 5분 뒤 두 번째 경계 | Sine/F30 | 38 | 35 |

두 장비 모두 전체432B·3슬롯이다. 같은 생성 함수에서 Flow 튜플만 다르게 받아
master 시간표와 slave 시간표의 차이가 정확히 offset5·14·23에만 존재한다.
wire에서 slave Mode를 생략하거나 장비가 이를 알아서 생성한다고 가정하지 않는다.
하위 image 검사는 기존2/3슬롯 exact 경우를 그대로 유지하고 새3/3슬롯 exact 경우만 추가한다.
슬롯 순서·값·시간·연속성·미사용 슬롯 차이·출력 범위·step 검사는 유지한다.
CLI 사전 범위 검사와 실행 중 출력 검사에도 새 master38을 포함한다.

파형·Flow 값은 생성기, 하위 image matcher, role snapshot 검사, 관측 분류기가 같은 계약으로
사용한다. 한 곳만 바뀌면 다른 검사에서 거부될 수 있으므로 함께 대조해야 한다.
새 snapshot 검사의 Frequency 허용 집합은 기존 wave 분기의 `{0,5,20,30}`을 의도적으로
승계한다. wire Constant0과 보고 Constant5를 혼동하지 않는다.

같은 파형·시간이므로 새 계획의 유효 구간은 `shared_plan_ownership_unresolved`로 표시한다.
`flow_match`는 보고값과 지정 슬롯·master·이전 슬롯·manual 후보의 비교이며 실제 수류 판정이
아니다. 세 창의 `WAVE_FLOW_OBSERVED`도 관측 완료일 뿐 기능 PASS가 아니다.
날짜·같은 분·fresh clock·count·stable 요구, 제외 구간,1020초 관측과1200초 outer 상한은 불변이다.

## 종료 진단

`temporary_schedule_observation_failure`, event_version1은 다음 다섯 분기를 구분한다.

- `already_done_cancelled`: 이미 완료된 task의 취소 결과.
- `already_done_failed`: 이미 완료된 task의 예외 결과.
- `cancelled_then_cancelled`: 취소 요청 뒤 수거한 취소 결과.
- `cancelled_then_failed`: 취소 요청 뒤 수거한 예외 결과.
- `completion_not_disarm_verified`: 완료 값이 DISARM_VERIFIED가 아님.

operation은 ASCII 원문 SHA256으로만 결속한다. 예외는 class/module과 프로젝트 frame의
module/function/line만 남긴다. chain8·frame32 상한, 순환 탐지 및 suppress_context를 유지한다.
메시지·args·locals·절대 경로·exc_info·extra는 방출하지 않는다. 동기 logger 호출 전체를
best-effort로 감싸 진단 자체 실패가 원복이나 원 예외를 대체하지 않게 한다.
기존 네 `from None`, 오류 코드, 예외 전파·취소·대기·timeout·보상 순서는 그대로다.
로그가 남는다는 격리 검증과 운영 실패에서 실제 발동했다는 증거는 별개다. 후자는 아직 없다.

## 검증 이력

운영 이미지 기반 Python3.12.14를 사용하되 source는 정책 커밋의 **전체 tracked archive**에
변경 여덟 파일만 덮은 별도 디렉터리로 구성했다. pytest8.4.2·pytest-asyncio0.26.0·Ruff0.16.7은
별도 시험용 의존성 디렉터리다. 실제 검사는 network-none·source/deps 읽기 전용·빈 safety tmpfs에서
실행하며 운영 설정·장비·보호 volume을 마운트하지 않았다.

| 실행 | 결과 | 처리 |
|---|---|---|
| 의존성 확인 | 기본 image에 pytest 없음 | 수집 전 확인이며 별도 시험 의존성을 설치; production image 불변 |
| targeted v1 | 602 PASS / 1 FAIL,9.03초 | 신규 fixture의 AutoFrequency 오타를 실제 키 AutoFreq로 정정; 원 출력 보존 |
| Ruff v1 | 긴 줄6건 | 문자열·호출·단언을 줄바꿈; 동작·기대값 불변 |
| targeted v2 | 603 PASS,8.46초 | fixture 정정 검증 |
| targeted v3 | 605 PASS,8.33초 | master38 범위 검사2건과 durable role 계약 보강 후 검증 |
| Ruff v3 | PASS | 변경 Python8파일을 명시하여 검사 |
| full v1 | 2701 PASS,95.95초 | 최종 source/test에서 전체1회; 반복 실행하지 않음 |

테스트4파일은 모두 추가만 했으며 기존 함수 AST도 전수 exact 비교했다.

| 파일 | 기존 함수 exact | assert 전 → 후 |
|---|---:|---:|
| test_schedule_flow_experiment.py | 89 | 270 → 284 |
| test_schedule_linkage_transaction.py | 223 | 1111 → 1121 |
| test_schedule_transaction.py | 60 | 226 → 247 |
| test_schedule_flow_experiment_cli.py | 79 | 215 → 222 |

분류 테스트는 합성 입력에 대한 분류 계약이고 실제 firmware 결과를 만드는 fake가 아니다.
종료 진단 테스트는 다섯 예외 분기 fault injection, redaction·chain·cycle·logger 실패 보존을 검사한다.
기존 단언 제거·완화0이며 하위 실제 image/snapshot preflight에서 새 계획 통과를 별도로 확인했다.

## 산출물 결속

gitignored artifact `native-flow-only-validation-v1`에 중간·최종 원 로그와 source/AST 대조를 보존한다.

| 산출물 | SHA256 |
|---|---|
| targeted-v1.log | `4f78acd2ab4b080cef8ff0a7b5309899dd6219c6893b5df9ccca032c232724ae` |
| targeted-v2.log | `40efb7f8e6e47b08ac4e572e0468c635b8d9e1f896a403ef84b85a102f986bde` |
| targeted-v3.log | `32b7772f4ec02c3d12f4aff119f37f1af27659b5f7fea1ae4c07a09fb08d41b6` |
| ruff-v1.log | `8c5a82b6b6e73dd4fafe1b7445775a9ee6c5f78638a2e56790e225717a47d71d` |
| ruff-v3.log | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| source-assert-audit.json | `08a4f0688900db98cedb0dfdc020338c380df57a2c8a16742adbfa7fe07619ff` |
| full-v1.log | `29aaa30860ce2bba7c93b140679cf6f122e52ff5aa2497a21866af2cd7ecb5e6` |

Ruff 성공 출력은 실행 간 byte-identical이므로 digest만으로 이번 실행을 식별하지 않는다.
위 명령 범위·소스 결속·로그 위치를 함께 사용한다. source-assert-audit에는 변경8파일 해시가 있다.
공용 guard·linkage.py·local_control 및 protocol schema 입력은 변경하지 않았다.

## 재동결·실기 경계

검증 결과와 함께 이 구현을 커밋하면 `3aa263a`의 **구현 예외는 소진되고 여덟 파일은 재동결**된다.
새 image/source 대조와 현재 현장 조건·fresh 신원·baseline·만료 예외의 단회 결속은 실기 전 별도다.
사용자의 새 Sync1회 요청을 Anti·자동 반복으로 넓히지 않는다. 기술 실패 시 원복 후 중단한다.
이전 first 앱 TimerOFF/manual constant40/F34·원 서비스 복귀 의무는 아직 열려 있고,
이번 구현이나 테스트 PASS로 닫지 않는다.
