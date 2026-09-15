# 외부 시나리오 기반 네이티브 시험

상태: 재사용 이미지·외부 JSON 구현 후 [최초 Sync 실기](runs/2026-09-15-native-scenario-sync.md)를
수행했다. 입력/응답 PASS와 종료 기술실패·Async 미실행을 구분하며 상세 결과는 실행 기록을 따른다.

사용자 요청은 이미지 하나에 실행기와 검증기를 넣고, 시험값은 외부 JSON으로 바꾸는 것이다.
Sync/Async·파형·파형별 옵션·Flow·시간표·기대 응답을 같은 자료로 결속한다.

## 분리할 것

- 이미지: 코드·의존성·기존 장비 확인·트랜잭션·원복·수집·판정기. 코드 변경 시에만 빌드.
- 시나리오: 논리 역할·예약값·기대 응답. 비밀값이나 실행 명령을 포함하지 않는다.
- 실행 결속: 실제 장비 설정은 private에서 공급하고, 시나리오 digest와 fresh baseline을
  기존 intent/확인 토큰에 포함한다. 시험값 변경은 새 입력 검증 대상이지 전체 suite 대상이 아니다.
- 결과: 저장 image exact 여부, 구간별 기대/실측/불일치, 표본 부족, 기술 종료, 원복을 분리한다.

## 최초 통합 시험

같은 이미지에서 Sync와 Async를 각각 한 번 실행한다. 첫 회차를 원복한 뒤에만 다음 역할로
넘어가며 기술 실패나 원복 미확인 시 중단한다. 시나리오 값과 기대 응답은 구현 검증 뒤 실행
전에 고정한다. 공통 관측 구간은 시작, 10분, 15분이고 입력 파형·Flow와 별도 반환 Mode/
Frequency를 함께 읽는다. 물리 유속계·영상은 사용자 수용 조건이 아니다.

마스터 시간표에서 슬레이브 Flow만 바꾼 image를 만들어 넣는 것과, 연동 중 마스터만 고친 뒤
슬레이브에 자동 전파되는지를 보는 것은 다른 질문이다. 후자는 기존 TimerOFF-before-write
계약에 없는 쓰기이므로 이번 실행기가 임의로 추가하지 않는다. 처음부터 다른 파형을 넣는
대조 구간과 slave-only Flow 경계를 통해 가능한 질문들을 한 실행에 묶는다.

기존 실기 결과의 자동 창 미달·exit2·disarm 원인 미확정을 새 수용 기준이나 시나리오 도입으로
해결된 것으로 쓰지 않는다. 이전 두 실행은 각각 원 기록을 따른다.

- [반대 파형 Sync](runs/2026-09-14-native-sync-wave-flow.md)
- [공통 시간표·상이 Flow Sync](runs/2026-09-15-native-sync-master-template-flow-only.md)

## 이미지 하나, 입력 파일만 변경

[통합 입력 예제](native-scenarios/sync-async.json)는 같은 세 구간을 Sync·Async 두 case로
묶는다. `slave`에 다른 시간표를 지정하거나, `slave_flows`에 Flow 목록만 지정해 master의
시간·파형·추가 요소를 공유할 수 있다. 둘을 동시에 지정하지 않는다.

```sh
# 장비·설정·안전 root에 접근하지 않는 순수 검사
python -m jebao_flow.schedule_flow_experiment_cli scenario-validate \
  --scenario /scenario/sync-async.json
```

실제 실행은 기존 `preflight`와 `run`에 `--scenario <파일> --case <이름>`을 전달한다.
preflight가 읽은 장비 시각으로 분 단위 `scenario-anchor`와 절대 경계를 정하고, 새 intent에
전체 선언·기대값·시각과 원본 snapshot/image를 결속한다. run은 출력된 `--scenario-anchor`,
`--boundary-time`, `--confirm`을 그대로 사용한다. 바뀐 파일은 기존 확인 토큰을 재사용하지
못한다. 실제 장비 이름·receipt 지명·설정은 private 운영 wrapper에서 공급한다.

이미지의 `/scenario`에 JSON을 읽기 전용 mount한다. 값·역할·슬롯 변경에는 입력 검사와 새
preflight만 필요하며 재빌드나 전체 테스트 반복은 필요 없다. 실행기 코드가 바뀔 때만 이미지를
교체한다. 이 명령은 별도 실기 권한이나 현재 복원 의무를 없애는 기능이 아니다.

## 자료가 바꾸는 것과 고정되는 것

| 항목 | 계약 |
|---|---|
| 시험 입력 | Sync/Async, 2..8슬롯, 기존 Pro 9개 Mode, Flow·frequency·feed_time·custom_frequency |
| 마스터 공유 | `slave_flows`를 쓰면 같은 slot의 Flow만 대체, 다른 슬롯 바이트는 그대로 |
| 기대 응답 | `auto_mode/auto_flow/auto_frequency` 필수, feed/custom 옵션 및 별도 reported Mode/Frequency 선택 |
| 시간 | 첫 경계 +6..10분, 마지막 경계 +15분 이내, 첫 경계 공유, 분 단위 |
| 기존 안전 계약 | 상한41·장비 min/max/step, safe manual30/31·constant/F20, 처음0→1 진입 |
| 기존 관측 예산 | 역할 창1035초·epoch1020초·최대104pair, outer1200초, 기존 rollback shield |
| 기존 자격 | 신원·binding·receipt·단회write·journal/fsync·원복 순서 불변 |

codec으로 표현되는 9개 모드는 wire 인코딩 가능 범위이지 9개 모드의 실기 검증 완료가 아니다.
선언 가능하더라도 현재 장비의 power 범위·step 등 기존 물리 검사는 여전히 거부할 수 있다.
예를 들어 feed의 0은 codec이 표현하지만 기존 전체 Flow guard를 우회시키지 않는다.
추가 요소는 image read-back으로 검증하며, 반환 필드 대조는 명시된 필드만 수행한다.
custom의 반환 필드 출처는 기존 profile의 `Auto_Cust_Wav_Freq`다. 없는 반환값은 기대값으로
채우지 않고 UNKNOWN으로 남긴다. 비활성 manual Flow를 현재 AutoFlow로 해석하지 않는다.

## 응답 판정과 보존

raw 메타데이터에 scenario/bound digest와 전체 선언을 남기고, 모든 pair를 기존 append/fsync
수집기로 보존한다. 각 pair의 JSON 진단에는 기대/실측/차이를 기록한다. 종료 보고서도 같은
private raw 파일에 fsync하며, 원복 예외가 나도 가능한 보고서를 먼저 보존한다. 원본 예외와
원복 경로를 진단 예외가 대체하지 않는다.

새 판정은 기존 고정 계획 판정기를 변경하지 않는 별도 계약이다. 양쪽 날짜·같은 분·엄격히
증가하는 장비 시각, 모든 경계 ±60초 제외, 첫 창10pair/120초·이후2pair/30초를 유지한다.
취득 실패는 안정 후보를 초기화한다. 제외 후 재진입 자체를 값 충돌이라고 부르지 않고,
실제 반환값 차이를 삭제하지 않는 방식으로 검사한다. 누락값은 PASS로 채우지 않는다.

보고서는 입력 read-back/기능 응답/창별 상태와 표본 수·안정 시간·마지막 실제 frame/time/
field를 함께 담는다. 입력 FAIL인 반환 PASS, 빈 안정 증거, 다른 시간표/시각/기대값의 증거는
허용하지 않는다. 안정된 부정 응답은 FAIL이라는 기능 결과이며, 기술 종료와는 구분한다.
마지막 표본은 그 실제 창의 frame과 값에 다시 결속한다.

각 창의 `compared_fields`는 실제 비교한 필드 집합이다. 생략한 옵션은 비교하지 않았음을
이 목록으로 구분한다. `SCENARIO_RESPONSES_OBSERVED`는 안정 관측을 얻었다는 종단 분류이며
응답 PASS가 아니다. status·run·readiness에서 반드시 `report.response`를 나란히 읽는다.
`matches_bound`도 결속 검사이지 FAIL 창의 기대값 일치를 뜻하지 않는다.

기술 종료, 안전 거부, 원복은 비교기의 PASS가 대신 만들지 않는다. Sync의 기술 실패·원복
미확인이 있으면 Async 자동 실행을 중단한다. 사용자 수용 기준은 저장값/반환값이며 물리
계측은 요구하지 않는다. 같은 경계만으로 내부 시계 소유나 실제 역위상을 확정하지 않는다.

`is_wave_flow_plan`은 새 자료 기반 경로에서도 기존 구성·예산·role sequence를 선택하는
내부 호환 속성이다. 소비처를 전수 확인했으며 속성 자체는 실기 승인·qualification이 아니다.
park admission은 검증된 tombstone 경로, 실행 권한은 새 정책/operation, 입력 신원은 전체
spec token으로 각각 별도 결속한다. legacy는 optional 필드 None 생략으로 기존 형태를 유지한다.

이번 선택자는 선언형 **Async에도** 검증된 park admission을 적용한다. 이는 새 Sync/Async
두 operation 정책에 따른 적용 대상 확대이며 고정 Sync 속성 변경의 우연한 부수효과로
취급하지 않는다. park 검증 자체·영구 bundle·원 경로·재읽기·`qualified=false`는 그대로다.
legacy Async의 공용 guard/복구는 유지되며, 이 선택자가 추가 실기나 qualification을 승인하지
않는다.
