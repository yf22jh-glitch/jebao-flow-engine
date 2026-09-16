# 수류모터 제품 구현 1차

## 완료 범위

[계획](wavemaker-product-plan.md)의 선언·compiler·미리보기와 원복 비교 계약을 구현했다.
Pro 독립 시간표는 기존 qualified coordinator의 journal-first 전송·전체 image read-back과 연결했다.
**앱 전체 동등 기능, native 결속/해제 실행기, HA 시간표 편집 화면 완료는 아니다.**
이번 변경은 실기를 수행하지 않았으며 기존 종료 오류를 고쳤다고 주장하지 않는다.

| 항목 | 이번 구현 |
|---|---|
| Bar 시간표 | 48×8바이트; 알려진 6개 슬롯 모드, 먹이 시간/Flow 공유 바이트, pulse/tide 포함; 오프라인 |
| Pro 시간표 | 48×9바이트; 9개 모드, frequency/feed/custom 포함; 독립·TimerOFF에서 qualified 실행 API |
| Sync/Async 상속 | 마스터 전체 image 복사, 슬레이브는 활성 슬롯별 Flow만 명시; 외부 JSON compiler |
| 원본 | 기존 journal의 장비별 첫 baseline, TimerOFF여도 전체 image 보존; 원본 기록 불변 |
| 해제 확인 | Pro 8개/Bar 9개 제어 필드와 전체 image 비교; 시각·현재 Auto를 과거값으로 돌리지 않음 |
| 실제 해제 | 장비별 순서 선언만 구현; 전송 실행기는 아직 없음 |

Bar의 `schedule_enable`은 앱 진단 catalog에 있지만 실행 encoder가 없다. 앱 UI/Auto 상태에 따른
7필드 결합이 미확정이며, 이번에 Pro timer 명령을 복사하지 않았다. Bar native Async도 만들지 않았다.
Bar 384-byte 복원 실기 자격 부재와 write 금지는 유지한다.

Pro schedule의 과거 진단 사유는 먹이 시간 1..60 범위 밖의 미검증이었다. 새 입력 모델은
`feed_time <= 60`이고 먹이 슬롯은 `>= 1`을 요구하므로 그 조건을 구성할 수 없다.
전체 image coordinator/read-back 연결 뒤에만 실행 catalog에 추가했으며, 실제 자격 검사는 별개다.

## 사용

장비 접근 없이 시간표를 검증한다.

```sh
jebao-flow-local plan-wavemaker --file config/examples/wavemaker-bar.json
jebao-flow-local plan-wavemaker --native --file config/examples/wavemaker-pro-native.json
```

native 예시의 `mode`만 `async`로 바꾸면 같은 compiler를 사용한다. 예시 시간은 장비의 하루 시각이며
"실행 후 10분/15분"이 아니다. 상대 시각 실기 runner와 혼동하지 않는다. 값 변경은 재빌드가 아니다.
위 예시 JSON 두 개는 논리 장비 이름만 사용하는 공개 추적 파일이다. private 설정을 포함하지 않는다.

`apply-wavemaker-schedule`은 별도 자격이 있는 Pro의 독립 시간표 **전체 교체** 명령이다.
입력은 native 계획이 아닌 `family`·`slots` 형식이고, 생략한 위치는 EE로 지운다.
`--file`, `--member`, `--request-id`, 기존 설치/역할 결속 인자, `--qualifications`, `--write-enabled`가
필요하다. runtime control, dry-run 해제, 해당 장비 write 허용, 현재 schema/firmware/물리 identity의
schedule qualification, 기존 lock과 journal이 모두 적용된다. CLI를 구현했다는 사실은 실행 승인이 아니다.

기존 스케줄이 켜져 있거나 연동 중이면 이 명령은 자동으로 끄지 않고 전송 전 거부한다.
명시적인 같은 request id·같은 입력의 재호출은 journal 결과를 반환하고 다시 전송하지 않는다.
그 결과는 **이전 transaction의 판정**이지 재호출 시각의 새 장비 상태 확인은 아니다.
실패용 `recover`는 기존 의미 그대로이며 성공한 VERIFIED 기록의 undo 용도로 사용할 수 없다.

HA/MQTT 기존 scalar·슬레이브 슬롯 Flow 표면은 변경하지 않았다. Pro schedule 실행 API를
catalog에 연결했지만 HA의 일반 schedule 항목은 여전히 진단 센서이며 시간표 편집 UI가 아니다.

## 해제 후 다시 결속되는 복구를 방지해야 하는 이유

현재 `detach_sequence`는 **장비 한 대**의 조작·확인 순서다. pair 실행 순서나 승인권을 만들지 않는다.
첫 `schedule_enable(false)`는 앱 계약상 `SwitchON=true`도 쓴다. 원래 OFF였던 장비에도 중간 ON 상태가
생길 수 있어 이 값을 명시했으며, 무출력 조작으로 서술하지 않는다.

실제 실행기를 바로 generic transaction으로 만들면 역할 해제 두 조작의 rollback baseline이
Sync/Async가 된다. 기존 `recover`는 이를 다시 쓸 수 있어 **해제 실패 뒤 재결속 금지**와 충돌한다.
`restore_on_recovery=False`는 현재 power-off 한 가지에만 허용된다. 따라서 이를 함수명만 바꿔
우회하거나 성공한 record를 recover 대상으로 재분류하지 않았다.

후속 구현 대안은 (1) 역할 해제의 복구 방향을 기존 journal 계약 안에서 명시하고, fresh 독립 상태
확인 이후 image/controls 복원을 별도 단계로 수행하거나 (2) 그 실행기를 보류하고 이번 compiler·
개별 제어까지만 제공하는 것이다. (1)은 `journal.py`와 직접 tests까지 범위 검토가 필요하다.
snapshot 비교와 단계 목록만으로 durable lifecycle 또는 원복 완료라고 보고하지 않는다.
보존 baseline은 복원할 데이터이지 복원 권한 자체가 아니다. 기존 scalar 작업이 남긴 전체 status를
읽었다고 그 작업에 없던 전체 시간표·제어값 쓰기 권한이 생기지 않는다.

## schema와 자격 범위

9개 fingerprint 입력 중 바뀐 것은 `local_control/device_writer.py` 하나다.

- 이전: `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`
- 이번: `704cff6d7bf15ce166acaa990ec83546c8830c51237a3c7e6b42942df2f64821`

따라서 이전 schema에 결속된 **local-control qualification**은 새 내용에 유효하지 않다.
재qualification·배포는 수행하지 않았다. frozen native의 `DeviceQualificationReceipt`는 다른 모델이고
schema 필드를 갖지 않으며, 그 authorizer는 device/operation/physical binding/시간을 검사한다.
위 digest 변경만으로 native 만료 예외가 코드상 자동 무효가 된다고 주장하지 않는다.
반대로 기존 native 자격이 새 제품 구현의 자격이 되는 것도 아니다. 미해결 native 오류 재개와
새 제품 qualification은 각각의 권한·구현 범위로 다시 다뤄야 한다.

## 검증 이력

로컬 Docker가 실행 중이 아니고 기존 Python 3.12 링크도 삭제된 임시 경로를 가리켰다.
운영 서버에 접속하지 않고 별도 임시 Python 3.12.14·프로젝트 dev 의존성 환경을 구성했다.
저장소 source·tests·추적 문서 전체를 사용하고, 이후 검증은 별도 pycache prefix로 기존 cache와 분리했다.

- 최초 import: 기존 encoder 함수명을 잘못 적은 ImportError. 정확한 공개 함수명으로 정정했다.
- 첫 신규 시험: 24 PASS / 15 FAIL. 새 테스트가 `decode_schedule`의 필수 `enabled` 인자를 빠뜨렸다.
  source 기대값·검사는 변경하지 않고 테스트 호출을 정정했다.
- 두 번째 targeted: 90 PASS.
- 세 번째 targeted: 127 PASS / 2 FAIL. 새 CLI 시험의 필수 역할 인자 누락, 그리고 기존 catalog 시험이
  Pro schedule을 실행 불가로 예상한 계약 변경이다. 전자는 인자를 추가했고, 후자는 Pro 실행 계약과
  여전히 미실행인 clock의 거부 계약을 함께 검증하도록 바꿨다. 검사를 제거해 통과시키지 않았다.
- 네 번째 targeted: 129 PASS. 이후 Bar 활성 슬롯의 zero sentinel 충돌과 추가 실패 복구/잠금 회귀를
  추가했다. 신규 코드의 Ruff 정렬·줄 길이와 불필요한 기존 CLI 포맷 변경도 정리했다.

- 다섯 번째 targeted: **134 PASS**. 이후 전체 suite를 한 번 실행했다.
- 전체 v1: **2797 PASS / 6 FAIL / 경고 2건**, 55.55초. import 수정 이전 트리이며 전체 통과가 아니다.

| 실패 시험 | 확인된 내용과 처리 |
|---|---|
| `test_write_protocol_imports_exist_only_at_the_device_writer_edge` | 신규 compiler의 `schedule_wire` 직접 import가 기존 경계를 위반했다. 기존 검사는 그대로 두고 순수 9바이트 조립으로 수정했다. 9개 Pro 모드 모두 실제 decoder로 읽고 공개 encoder 결과와 바이트 대조한다. |
| `test_public_boot_identity_and_suspend_inclusive_clock_are_controller_defaults` | Linux boot id가 필요한 시험을 macOS에서 실행하여 실패했다. 운영 코드의 fail-closed 처리를 변경하지 않았다. |
| `test_missing_receipt_and_unsafe_output_metadata_fail_closed` | 실행 shell의 umask가 077이어서 `mkdir(mode=0755)`가 실제 0700을 만들었다. 남은 시험 디렉터리 mode 0700을 직접 확인했다. |
| `test_private_directory_mode_is_required` | 같은 umask 조건이며 실제 시험 디렉터리 mode 0700을 확인했다. |
| `test_concurrent_same_receipt_contenders_converge_to_same_exact_success` | receipt claim lock 열기에서 FileNotFoundError, 한 스레드가 결과를 남기지 못했다. 근본 원인은 미확정이고 이번 범위에서 고치지 않았다. |
| `test_concurrent_conflicting_receipts_have_exactly_one_winner` | 위와 같은 lock 예외 경로. 신규 제품 코드와 무관하다고 추정만 하고 넘기지 않고 변경 전 코드에서 대조했다. |

대조는 `cf18e2f`의 전체 추적 파일을 별도 빈 디렉터리에 `git archive`로 풀고, 같은 Python 환경과
umask에서 위 **기존 실패 5개만** 실행했다. `PYTHONPATH`와 별도 pycache를 그 archive로 지정했고,
보존 traceback의 source 경로도 archive를 가리킨다. 결과는 **5 FAIL / 경고 2건**으로 같은 이름과
예외가 재현됐다. 이는 이번 변경 전에도 이 환경에서 실패했다는 근거이며, 동시성 실패의 근본 원인을
macOS 탓으로 확정하거나 Linux에서 정상이라고 주장하는 근거는 아니다.

import 수정 후 여섯 번째 targeted는 **147 PASS**다. 기존 134개와 import 경계 13개를 포함한다.
Pro 활성 슬롯의 zero sentinel은 pulse의 Flow 0 거부와 feed의 mode code 7 때문에 도달 불가이고,
EE sentinel은 시각 범위로 배제된다. encoder 호출을 제거한 뒤에는 이 입력 계약과 9모드 encoder
바이트 대조가 그 성질을 지킨다. Bar의 zero sentinel은 실제 구성 가능하여 명시적으로 거부한다.
Ruff 최종 검사도 PASS다. 수정 뒤 전체 suite를 다시 실행하지 않았으므로 최종 트리의 전체 PASS는
주장하지 않는다. 이후 source 변경은 설명 주석·docstring뿐이다. 최종 Ruff와 diff 검사를 다시 했다.
첫 Ruff 정렬 정정 외에 import 수정 직후 주석 길이 E501도 있었고, 주석을 두 줄로 나눠 해결했다.

시험은 바이트 형식·입력/반환 계약·durable 저장·중복 전송·실패 복구를 검증한다.
synthetic 세션 반환값으로 펌웨어가 실제로 결속/해제된다는 가설을 증명하지 않는다.

### 보존 로그

로컬 gitignored 논리 경로 `data/wavemaker-product.rXar4A/`에 다음 로그를 보존한다.
비밀값 없는 검사 산출물이나 로컬 경로를 담은 traceback을 공개 저장소에 추가하지 않는다.

| 파일 | SHA-256 |
|---|---|
| `targeted-v1.log` | `cdad78dd703ace2c7704f5998e90da4cb52629875d97adcf2bd8aee5935b348d` |
| `targeted-v2.log` | `50ca912fc620598809b52a036f361ac15ce37fb130ed8aac914ec328f3cd5d92` |
| `targeted-v3.log` | `f4fa9814940aacadd6414d73040bd0693baa6e725474cedc5fb8e7db18ea1e38` |
| `targeted-v4.log` | `072111a2a4ef4cc2ba950624adfa30b82ed2c66f48dbceddecaa32aa7c4cf195` |
| `targeted-v5.log` | `5d4137c0144157d49b97e8d754e8e7159d8e75447af4d9db46d0224b73ed1865` |
| `full-v1.log` | `12af8a654bdfed8330495ad2d0d4021362f22e4867fdcd39fe151adccb6d913f` |
| `baseline-five-v1.log` | `8a7d033720b28da6dca326b7cc6e3e01879657ff4671aef322303acf9a6402aa` |
| `targeted-v6.log` | `e5f5b8093f04d54f8967de255c2dc1087d60e39de88bb6dbe480c93aaf360cc1` |
| `ruff-final.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |

Ruff 성공 출력은 실행 간 byte-identical하므로 해당 digest는 실행 식별자가 아니다.
최종 검사 대상은 변경 source 5개와 test 3개이며 명령은 `ruff check --no-cache`다.

## 독립 검토와 남은 작업

Claude의 정적 검토에서 입력 계약·Pro 실행 배선·기존 자격/잠금 유지·부분 구현 표기·catalog 시험의
의도된 변경을 확인했다. verifier와 실제 물리 동작을 혼동하지 않으며 리뷰어가 pytest를 재실행했다고
인용하지 않는다. 전체 실패와 대조 실행, 최종 변경분의 결속 검토에서도 차단 소견 0이었다.
이후 문서에 Pro sentinel 보증의 소재와 full의 수정 전 트리 범위를 명확히 했으며 source/test는 불변이다.

이번 1차 커밋은 구현한 선언/비교/독립 시간표 API 범위만 소진한다. native lifecycle와 HA 편집은
미완료이며 journal 방향 계약 범위 결정이 남는다. `protocol/`, frozen native 6파일, 공용 guard,
기존 HA/MQTT, 운영 서비스·장비·앱에는 변경이 없다. 이전 앱 OFF·서비스 복귀 의무도 열린 채다.

## 후속 실행기 구현

정책 `6ed4059` 이후 [native lifecycle 실행기](wavemaker-lifecycle-implementation.md)를 연결했다.
위 미완료 표기는 1차 구현 시점의 범위이며 실제 결속·해제 API/CLI의 현재 상태는 후속 기록을 따른다.
과거 실기 원인 미확정·실장 자격·배포·HA 편집 연결을 이 변경으로 완료 처리하지 않는다.

## 2026-09-16 전체 시간표 MQTT 연결

기존 compiler/coordinator에 [미리보기·전체 교체·삭제 MQTT 계약](wavemaker-mqtt-schedule.md)을
연결했다. 새 시간표 실행기를 만들지 않으며, 장비별 qualification·원본 journal·no-resend·
미완료 거래 차단은 그대로다. HA Schedule sensor에 qualified 요청 주소만 추가하고 현재 시간표는
fresh read로만 출판한다. 시각적인 슬롯 편집기와 native lifecycle MQTT는 아직 미완료다.
이번 변경은 소프트웨어/오프라인 검증만이며 장비·앱·운영 서비스 조작과 배포는 하지 않았다.
