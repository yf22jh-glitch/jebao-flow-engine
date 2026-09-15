# 수류모터 제품 구현 계획

## 목표와 승인

대상은 Bar 한 대와 Pro 두 대다. 사용자는 앱과 동등한 개별 기능, 마스터가 시간표·파형·파형별
부가 설정을 정의하고 슬레이브는 슬롯별 Flow만 지정하는 Sync/Async 제품 기능을 요청했다.
“일단 계획서 만들고 검토한 후 바로 구현들어가자”, 이어서
“sync랑 async 해제는 기존 값으로 덮어 씌워서 해제하는걸로 구현하자”라고 요청했다.
이 문서는 제품 코드·오프라인 검증 범위이며 배포·장비 write·초기화 승인이 아니다.
사용자가 앱 해제에 어려움을 겪어 초기화했다는 발언은 과거 경험이다. 현재 원복 완료 회신이나
기존 해제 오류의 원인 증명으로 바꾸지 않는다.

닫을 질문은 **앱 기능과 제품 구현 사이의 공백, 설정 상속과 기존값 복원의 입출력 계약**이다.
근거는 저장소에 보존된 앱 3.4.18 정적 catalog, 제품별 wire 정의, 실제 Sync/Async 반환값 기록,
코드 경로와 계약 테스트다. 소프트웨어 테스트로 장비가 새 명령을 수용했다고 주장하지 않는다.

## 현재 기능표

| 기능 | Pro 두 대 | Bar 한 대 | 구현 방향 |
|---|---|---|---|
| 전원·수동 Flow·모드·주파수·먹이 시간 | scalar 경로 있음 | scalar 경로 있음 | 기존 경로와 장비별 qualification 유지 |
| 파형 부가 설정 | custom 주파수 등 있음 | feed switch·pulse/tide 있음 | 가족별 필드 유지, Pro 설정을 Bar에 강제하지 않음 |
| 시간표 편집·삭제 | 48×9바이트, 슬롯 저장 builder 있음 | 48×8바이트, 전체 저장 builder 있음 | 외부 선언 모델과 정확한 image/read-back 연결 |
| 스케줄 ON/OFF | 앱의 SwitchON/Linkage 결합 구현됨 | 앱 화면·현재 Auto 상태에 따른 결합 미확정 | Bar의 미확정 동작을 단순 bool 명령으로 꾸미지 않음 |
| 장비 시각 | byte builder 있음 | byte builder 있음 | 정확히 저장된 날짜·시각을 복원 대상으로 삼지 않음; 별도 시각 동기화 동작 |
| Sync/Async 슬롯 Flow | compiler 및 qualified 실행·MQTT 슬롯 Flow 표면 있음 | Pro native 역할과 다름 | 기존 경로 재사용, 마스터 편집 및 lifecycle 보완 |
| 성공 후 결속 해제 | 제품 lifecycle 없음 | 해당 Pro 계약 대상 아님 | 연동 전 snapshot으로 명시적인 새 복원 작업 구성 |

“앱과 동일”는 검토한 앱 catalog 범위다. Wi-Fi 등록·계정·클라우드 공유를 장비 파형 기능과
섞어 완료 처리하지 않는다. Bar의 native `slave`는 Pro의 `sync_slave/async_slave`와 같다고
가정하지 않는다. Bar 시간표의 실제 384바이트 복원 자격은 아직 없으므로 실행 가능 표시와
오프라인 구현 상태를 분리한다.

## 사용자 설정 계약

- 외부 JSON 한 개에 제품 가족, 48개 위치 중 사용 슬롯, 시작·종료 시각, 모드, Flow,
  frequency/feed/custom 또는 Bar pulse/tide를 명시한다. 값 변경은 코드·이미지 재빌드가 아니다.
- Pro native 계획은 마스터 시간표 한 개와 각 슬레이브의 **활성 슬롯별 Flow 전체**만 받는다.
  슬레이브 전용 mode·time·부가 설정은 입력 항목이 아니다. 누락 Flow를 옛 저장값으로 추측하지 않는다.
- 결과는 마스터와 슬레이브의 모든 비-Flow 바이트가 같아야 한다. 비활성 슬롯도 그대로 공유한다.
- 앱 범위 30..100과 특수 먹이 슬롯 의미를 구분한다. 지원 범위를 운영 시 50 이하 지침이나
  qualification 준비 상한으로 축소하지 않는다. 모드별 부가 필드도 검증·반환 목록에 포함한다.
- 장비가 반환한 저장 image, 제어값, 현재 Auto 값은 별도 항목이다. 기대값을 상태로 출판하지 않는다.

## 기존값 덮어쓰기 해제

1. 연동 **이전**에 각 장비의 authenticated 상태에서 원본 시간표 전체와 제어값을 보존한다.
   원본은 장비별 identity·내용 해시·operation에 결속한다. TimerOFF여도 시간표를 보존한다.
2. 시간표만 덮는 것을 해제로 부르지 않는다. 기존 Pro `schedule_enable(false)`의 앱 결합 명령은
   슬레이브를 independent/TimerOFF로 만든다. master는 기존 `linkage_role(independent)`도 필요하다.
   이 기존 조작들의 반환값으로 독립 상태와 TimerOFF를 먼저 확인한 뒤 원본 image를
   쓰고, 원래 수동 Mode/Flow/Frequency/부가값·전원·Timer 상태로 복귀한다.
3. 마지막 반환값의 독립 역할·원본 image·제어값이 모두 일치해야 완료다. 현재 Auto 값은 시각에
   따라 달라지므로 과거 활성 슬롯을 억지로 복원하지 않는다. 장비 시각도 과거로 돌리지 않는다.
4. 성공한 `VERIFIED` record를 `recover`로 다시 실행하지 않는다. 해제는 보존 baseline을 읽는
   **새 요청**이며 기존 기록은 불변이다. 이미 해제된 요청의 재전송·서비스 재시작은 write를 만들지 않는다.
5. 해제 실패 때 원본을 지우거나 Sync를 다시 켜지 않는다. 불일치 필드와 write 단계·ACK·read-back을
   보존하고 불확실 상태로 보고한다. 최근 실기의 해제 확인 실패는 제품의 성공을 보장하지 않는다.
   초기화·재시도·timeout 증가를 해제 알고리즘으로 넣지 않는다.
6. 시작 snapshot과 그보다 앞선 앱 상태는 구분한다. 이번 기능은 자기 baseline 복원이며, 과거 실기의
   first Pro 마지막 TimerOFF·서비스 복귀 의무를 자동으로 닫지 않는다.

## 구현 순서와 범위

계획 정적 검토 후 다음 순서로 작은 제품 단위를 연결한다. 각 단계의 완료 범위를 기록한다.

1. **선언 모델·compiler·미리보기**: Bar/Pro 시간표와 native 상속 계약을 외부 JSON으로 제공하고,
   해제용 baseline 및 원복 반환값 비교 모델을 구현한다. 앱 catalog 범위와 미구현 기능을 함께 출력한다.
2. **기존 제품 실행 경로 연결**: qualified scalar·schedule 요청을 기존 coordinator/writer에 연결한다.
   전체 시간표 read-back을 검증하고, 미완료 journal 차단·single-write·복구 순서를 유지한다.
3. **연동 lifecycle**: 원본의 내구성 보존, 적용/해제 순서, 재시작 후 명시 복구를 제품 계약으로 연결한다.
   일반 실패용 `recover`를 성공 후 undo로 재사용하지 않는다. Bar native 역할을 Pro에 합치지 않는다.
4. **HA/MQTT 연결**: 기존 native MQTT Discovery를 사용한다. 미자격 기능은 조작 가능으로 광고하지
   않고 상태·미지원 사유를 제공한다. 옛 custom component와 시험 하네스를 제품 서비스에 붙이지 않는다.

소스 변경 후보는 `local_control/`의 새 `wavemaker.py` 및 직접 지원 모듈,
`catalog.py`, `device_writer.py`, `coordinator.py`, `ha.py`, `mqtt.py`, `local_control_cli.py`와
직접 테스트다. frozen native 여섯 파일·직접 테스트, `protocol/`, 공용 guard, 옛 daemon/HA는
변경하지 않는다. 추가 필수 범위가 확인되면 근거를 문서화하고 범위를 다시 검토한다.

`device_writer.py`와 `local_control/schedule.py`는 schema digest 입력이다. 이 파일을 변경하면
기존 receipt currency가 달라진다. digest 계산을 피해 가지 않으며, 기존 실기 PASS나 receipt를
새 구현의 실행 자격으로 승격하지 않는다. 새 구현 배포 시 필요한 재qualification은 별도 작업이다.

## 검증과 완료 기준

- Bar/Pro 모든 알려진 모드·부가값, 48슬롯·미사용 위치·슬롯 삭제·경계값을 코드 계약으로 검증한다.
- 두 native 역할에서 비-Flow 바이트 동일성, 명시한 Flow exact, 입력 불변, 잘못된 장비 가족을 검증한다.
- 원본 보존 후 원하는 설정이 변해도 해제 목표가 바뀌지 않음, TimerOFF 시간표 포함, 잘못된 identity,
  image·control 각각의 불일치, 부분 결과를 완료로 올리지 않는 것을 검증한다.
- 실행 연결에는 journal-before-send, 중복 요청, 실패 뒤 전진 중단, 안전한 복원 순서 계약이 필요하다.
  이 테스트는 장비 펌웨어가 해제된다는 예측을 fake로 증명하는 시험이 아니다.
- targeted 검증과 독립 diff 검토 뒤 전체 suite를 한 번 실행한다. 중간 오류와 환경 실패를 보존한다.
- 소프트웨어 구현 완료, 물리 qualification, 새 실기, 상시 배포는 각각 별도 상태로 보고한다.

## 현재 상태

Claude의 정적 계약·범위 검토에서 차단 소견 0이었다. 기존 scalar·slot Flow 실행을 재사용하고,
schedule 실행·read-back 계약을 먼저 구현한 뒤에만 해당 catalog 실행 상태를 확장한다.
Bar schedule_enable은 앱 catalog의 진단 항목에는 존재하지만 encoder와 실행 경로는 없다.
이번 계획 검토 시점의 소스 변경·새 장비 접근·배포는 0이다.

이후 1단계와 2단계의 Pro 독립 시간표 실행 API를 구현했다.
[1차 구현 기록](wavemaker-product-implementation.md)에 검증 결과와 남은 범위를 구분했다.
3단계의 실제 결속/해제 실행기와 4단계 HA 편집 연결은 아직 미구현이다.
해제 방향 계약에 필요한 `journal.py` 및 직접 테스트는 이번 열거 범위 밖이므로 근거를 보고했으며,
이번 커밋에는 포함하지 않는다. 역할 해제 두 조작의 복구가 Sync/Async를 재생하지 않도록 하는
범위 결정이 선행돼야 한다. 시간표·제어값 복원은 그 뒤의 fresh 독립 상태를 기준으로 구성한다.
