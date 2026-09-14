# 파형 주도권·슬레이브 슬롯 Flow 세 구간 구현

## 상태와 범위

`18233ae`의 별도 한정 해제 뒤 구현·오프라인 검증을 수행했다. 실기 답은 아직 없다.
이번 변경은 Sync와 Anti-Sync의 두 질문을 측정할 수 있게 하는 준비이며,
테스트 PASS를 펌웨어 기능 PASS나 이전 실행의 원인 규명으로 올리지 않는다.
직전 실기는 [field 준비 중단 기록](runs/2026-09-14-native-sync-field-preparation-stop.md)을 따른다.
그 실행의 원예외 UNKNOWN·앱 이전 first TimerOFF·원 서비스 복귀 미완료를 여기서 닫지 않는다.
이번 구현 중 새 device command·앱 조작·운영 배포는 0이다.

허용된 열 파일 중 source 다섯·직접 테스트 네 파일을 수정했다.
`tests/unit/test_hardware_test.py`는 수정하지 않았으며, intent의 새 표본 계약은
schedule-flow CLI 테스트에서 직접 검증했다. 허용 목록 밖 source/test 변경은 없다.
`local_control/`, 공용 guard, `devices/linkage.py`, 다른 CLI, 기존 timeout/retry는 불변이다.

## 구현 계약

`wave_flow_role`은 `sync_slave` 또는 `async_slave`인 새 명시적 선택이다.
네 Flow·주파수 서명 `(35,30,32,40,30)`과 정확한 관측 설정을 함께 검사한다.
marker가 없는 기존 spec·role spec·sample은 새 필드를 직렬화에서 생략한다.
기존 고정 Sync/Async 계획·토큰 형태·기록 로드·원복 의미를 바꾸지 않는다.
CLI는 `--wave-flow-role`을 명시한 때만 새 계획을 만든다. 이를 생략하면 기존 계획이다.

| 항목 | 새 계약 |
|---|---|
| master | Sine35/F30 → Constant32/F0 |
| slave 저장 시간표 | Constant30/F0 → Sine40/F30 → Sine35/F30 |
| safe manual / 상한 | master30·slave31 / 양쪽41 |
| 경계 | fresh 장비시각의 분 시작 T0에서 +10분·+15분 |
| 역할 관측 | authority1035초 − 기존 reserve15초 =1020초 |
| 표본 | W0 10 pair/120초, W1·W2 각각2 pair 이상/30초 |
| 읽기 경계 | 기존 취득8초·pause10초, 새 계획 최대104회 |

시간 계산과 outer1200초·복원 마감 구분은
[시나리오의 시간 예산 검산](native-follow-wave-own-flow-scenario.md#시간-예산-검산)에 있다.
분 단위 제약 때문에 첫 실제 대기는9~10분이다. 아직 확정된 live 시각은 아니다.

temporary schedule은 master2/slave3 슬롯의 정확한 전체 계획을 먼저 인식한다.
일반 검사는 첫 두 슬롯의 Mode 차이만 본다. slave의 두 번째·세 번째 슬롯이 둘 다 Sine인
예외는 `_is_wave_flow_patch_pair`의 exact 값·순서·연속성·5분 검사에 의존한다.
그 함수를 일반적인 세 슬롯 허용으로 넓히지 않는다. 나머지 계획은 기존 두 슬롯/옛 topology
거부 경로에 남으며, 모든 슬롯의 Flow 범위·step 검사는 유지한다.

새 monitor는 기존 authenticated raw 취득을 사용한다. 두 장비 시각이 같은 분이고
각각 전진한 pair만 센다. 두 경계의 포함±60초는 raw로 남지만 안정 표본에서 제외한다.
세 창을 순서대로 보존하며, 중복 시각·누락 창·충돌·raw 보존 실패로 관측 완료를 만들지 않는다.
경계 제외 중에도 보호된 controls 이상을 숨기지 않는다. 장비 identity·online/error·ON·
TimerON·roles·시간표 fingerprint·manual Flow exact·active cap 검사는 유지한다.

`WaveFlowWindowEvidence`는 창 번호, master/slave Auto 값, pair 수·안정 경과,
첫/마지막 ordinal, 마지막 pair의 취득 monotonic span·장비시각·프레임 digest를 담는다.
`sample.wave_flow_windows`에는 최대 세 창을 둔다. 전체 raw stream과 ordinal로 대조하며,
안정 시간은 취득 span 한 개 자체가 아니라 monitor의 누적 판정이다.
`wave_flow_observed`는 세 창이 유효하게 관측됐다는 뜻이지 기능 가설 PASS가 아니다.
파형은 master/자기 슬롯/기타, Flow는 자기 슬롯/master/이전 슬롯/manual/기타로 독립 표시한다.
protocol 보고를 실제 임펠러 파형·위상·수류량이나 master clock 인과로 승격하지 않는다.

park admission은 새 exact 계획의 Anti-Sync에도 적용되지만 옛 일반 Async에는 적용되지 않는다.
복원은 durable role sequence·기존 ordered rollback을 사용한다. 전진 snapshot 경계 계산을
default-controller recovery가 다시 수행한다고 주장하지 않는다.

## 실패 진단

CLI는 실패 시 원예외 chain을 동기 stderr 한 줄로 남긴다. event v1 필드는
`event`, `event_version`, `operation_sha256`, `stage`, `exception_chain`,
`chain_truncated_or_cyclic`이다. event는 `schedule_flow_failure`다.
operation 해시는 접두어 없는 ASCII operation_id의 SHA256으로 고정한다.
stage는 진단 호출 당시 마지막 보존 단계이며 원인이 발생한 최초 단계라고 단정하지 않는다.
chain은 최대8개, 항목마다 class/module·저장소 내부 function/line 최대32개·truncated 표기다.
cause/context와 suppress_context를 따르며 순환을 제한한다.
메시지·args·locals·파일 경로·exc_info·extra는 출력하지 않는다.
진단 출력 실패는 원예외나 원복을 대체하지 않는다. await 없는 동기 관측자로 유지한다.
이 전달 시험은 격리된 코드 증거이며 새 기능의 production 실패 발동 검증은 아니다.

## 오프라인 검증과 중간 실패

기준 `18233ae`의 전체 추적 파일을 빈 시험 디렉터리에 git archive로 풀고 변경 Python을
덮어 시험했다. source/test만이 아니라 추적 config·문서도 포함했다.
실행은 Python3.12.14, pytest8.4.2, pytest-asyncio0.26.0, Ruff0.16.7이다.
시험은 network-none·read-only source/deps·일회성 tmpfs에서 수행했고,
production hardware/journal/receipt volume이나 장비 설정을 마운트하지 않았다.

| 회차 | 결과 | 처리 |
|---|---|---|
| 의존성 준비1 | 임시 쓰기 디렉터리 없음 | read-only 컨테이너의 pip가 사용할 tmpfs 누락. 원 로그 보존 |
| 의존성 준비2 | 성공 | `/tmp`에 일회성 tmpfs 제공. production image 수정 없음 |
| 기존 관련4파일 검사 | 544 PASS,9.84초 | 초기 초안의 legacy 회귀 검사이며 최종 전체 결과 아님 |
| 새 계약 포함5파일 검사 | 675 PASS·2 FAIL,11.83초 | 신규 helper가 role spec의 `_roles` 접미사를 중복해 record operation_id와 불일치 |
| helper 정정 | record.operation_id를 실제 role spec에 일치 | source 게이트·기대값·기존 단언 완화 없음 |
| 모니터 계약 검사 | 18 PASS | 합성 read 입력의 창·표본 회계, 중복시각·42 상한·raw 유실 검사. 펌웨어 답 미주장 |
| 최종 targeted | 686 PASS,11.23초 | 신규 계약과 기존 hardware 테스트 포함 |
| 전체 suite 단1회 | 2680 PASS,96.84초 | 기준2640 + 신규40, 성공할 때까지 반복하지 않음 |
| 최종 Ruff / diff check | PASS | 변경 Python9파일 명시 검사 / whitespace 검사 |

첫 Ruff는 import 정렬과 새 `zip`의 strict 명시 누락을 보고했다. 변경 hunk 범위만
형식 정리하고 의도된 길이 차이의 zip에 strict=False를 명시했다. 원 출력도 보존했다.
전체 suite 이후 source 차이는 위 exact 세 슬롯 결합을 설명하는 주석2줄뿐이다.
그 뒤 최종 Ruff를 수행했으며 동일 동작에 대해 전체 suite를 다시 돌리지 않았다.
AST로 기존 네 테스트 파일의 기존 함수·클래스가 모두 동일함을 확인했다.
기존 assert 수 253/204/1093/223은 각각270/215/1111/226으로 증가했고 제거는 없다.

보존 묶음 id는 `wave-flow-implementation.WnBPHC9s`다. 원 로그 digest는 다음과 같다.

| 산출물 | SHA256 |
|---|---|
| 초기544 | `fd71460efd04ee25b842b96242e85a2298b9bff6419a7bb98f3ac82def72fb0d` |
| 675/2 | `afdb3634ab607604d9dfffe291f742403d80919536c0917f4d039574e05d68f1` |
| targeted686 | `9883ddec0fc302e1b966a12ca6ee405b54655ac0b4070fd38e58466a8159901f` |
| monitor18 | `843ef1e0d68869366deae855c1cc695985871c48b448195ef4a7108fd038add5` |
| full2680 | `46b08d1e2ae1f9d4ddb1eaeb1589e2bd87e34211dc01a320624bfff1c240e7ef` |
| Ruff 최종 | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |

Ruff 성공 bytes는 다른 실행과 같을 수 있으므로 digest 자체는 실행 식별자가 아니다.
검사 대상은 위 변경 Python9파일이며 묶음의 소스 digest와 실행 로그를 함께 본다.
보존된 최종 소스9개는 `source.sha256`으로 전부 재대조해 일치했다. 이 목록의 digest는
`3c087424d51a6df578662ec313c6184a6541a4f292ea8daccfcaa45e04bd995b`이다.
시험 후 보존용 tar 복사에는 macOS 부가정보 경고와 `._` 파일9개가 생겼다.
이들은 위 명시적 소스 목록에 포함되지 않으며, 이미 끝난 시험의 입력으로 쓰이지 않았다.
schema 입력9개와 변경 집합 교집합은0이고, network-none 실행 재계산도
`fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`으로 동일했다.
이는 현재 live receipt 재감사나 새로운 qualification을 뜻하지 않는다.

이 구현 결과 커밋으로 `18233ae`의 구현 예외는 소진·재동결된다.
두 역할 실기는 같은 절의 별도 실행 범위·현장 조건·이전 복원 의무를 확인하고 진행하며,
이 문서의 테스트 결과만으로 시작하거나 원복 완료를 선언하지 않는다.

## 실행 이미지 준비 결과

구현 커밋 `6978d2dadf0400958241dc7ebd638d4be04b4763`의 bundle로 실행 이미지를 준비했다.
이미지 SHA256은 `200564aeeaa084e181c887a431149f9d672d443d9931e376475d720c58a506f1`이다.
기본 이미지는 기존 `a6ad7c71fb0ebd94ac6454bdb04441ba56e08f5ddfe0630d4dc4161b2ea06c4c`이며,
그 로컬 태그의 실제 ID를 빌드 전·후 대조했다. 운영 서비스에 배포하지 않았다.

첫 Dockerfile은 이미지 ID를 FROM에 직접 적어 BuildKit이 원격 이미지 이름으로 해석했고,
메타데이터 조회에서 실패했다. layer 실행 전에 끝났으며 로그와 Dockerfile을 보존했다.
`--network none`은 RUN 단계 제한이지 메타데이터 조회 전체의 네트워크 차단이 아니다.
로컬 태그와 실제 ID 결속으로 표기만 정정한 두 번째 빌드는 exit0이었다.
기본 이미지의 git·의존성을 재사용하고, fetch/checkout은 제공된 bundle만 사용했다.

격리 이미지 검사 첫 호출은 operator script의 읽기 권한 때문에 양쪽 모두 시작 전에 실패했다.
비밀값 없는 검사 script만0644로 정정했다. 운영 UID·보호 root·receipt 권한은 바꾸지 않았다.
정정 검사 두 호출은 network-none/read-only이며 script만 마운트하고 운영 데이터는 마운트하지 않았다.
각 이미지의 추적 source138개를 그 이미지 HEAD의 git bytes와 대조해 모두 일치했다.
기본 대비 source 차이는 승인된 다섯 파일뿐이며 Python·설치 패키지 목록·schema 지문은 동일했다.
새 이미지에서 가상 식별자를 사용한 순수 spec 생성으로 Sync·Anti-Sync marker와1035초 계약을 확인했다.
이는 실제 신원·현장 조건 확인이나 device write가 아니다.

보존 묶음은 `wave-flow-build.btTl4t53`이다.

| 산출물 | SHA256 |
|---|---|
| source bundle | `51e1ff5358643928978ebafe75596718ca514e7d7e4095db2320c9cea92a8e46` |
| 첫 빌드 실패 로그 | `ed3c648683896c1373f82f9df46d97ac2022ab8edcfe3e9bd137dc954d0f76c2` |
| 정정 빌드 로그 | `2a4aa490dc14a177399657b3565da686cd4f98b77e903a585be180986901336c` |
| 최종 Dockerfile | `f373a54354d3561eff1c8dd57c40cab19da36b045c34c856797891f97cf62c49` |
| candidate 검사 | `35e8b23c4dc8647aed2a55becbcb0c40ad7385eeffef2509e37b7d9b5ee2e9d7` |
| base 검사 | `6be72014fe2559f7489faec1ebe1fca5a3e5baf38ea39bd4397df3a425393287` |

정정 검사 stderr는 양쪽0B다. 이미지 준비 뒤에도 원 exact 서비스는 정지 상태이고,
이전 first TimerOFF는 새로 확인하지 않았다. 새 실기·앱 조작·운영 배포0을 유지한다.
