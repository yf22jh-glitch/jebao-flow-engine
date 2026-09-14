# Sync 마스터 시간표 상속·슬레이브 Flow 전용 차이 시험안

## 요청과 상태

사용자는 마스터에 파형·Flow를 지정하고, 슬레이브에는 대응하는 스케줄 Flow만 지정하여
Sync에서10분·15분 전환을 검증하도록 요청했다. 이 문서는 그 새 실기 요청을 구체화한다.
후속 동결 해제 질문에 사용자가 "그냥 진행하라니깐"으로 승인했다. AGENTS에 여덟 파일
한정 해제와 재개 조건을 별도 커밋한 뒤에만 구현한다. 이 승인으로 기존 실패가 해소되지는 않는다.
이 문서 작성과 아래 가상 이미지 검증은 장비 접근·write0이다. 새 실기는 시작하지 않았다.

## 해석과 세 구간

파형·시간·주파수는 마스터 시간표 한 곳에서 정의하고, 슬레이브는 동일한 시간표를 사용하되
각 슬롯의 Flow만 다르게 한다. 슬레이브에 반대 파형을 별도로 설정했던 이전 시험과 다르다.
슬레이브 wire에서 Mode/시간 필드를 생략한다는 뜻은 아니다. 현 Pro 경로는 전체432B
시간표를 보내며, **두 image 간 차이를 각 활성 슬롯 Flow 바이트로만 제한**한다.

| 기준시각 대비 | 공통 파형·주파수 | master Flow | slave Flow |
|---|---|---|---|
| 시작 | Sine / F30 | 35 | 30 |
| +10분 | Constant / wire F0 | 32 | 40 |
| +15분 | Sine / F30 | 38 | 35 |

이 값은 새 시험 후보값이며 실제 출력이 아니다. master가 두 경계 모두에서 파형과 Flow를
바꾸고, slave의 지정 Flow는 master와 세 구간 모두 다르게 한다. 출력 상한41을 유지한다.
절대 경계 시각은 현재 시각을 확인한 뒤 실행 시점에 고정하며 지금부터 카운트다운하지 않는다.

이번 질문은 **동일 시간표·파형에서 서로 다른 슬롯 Flow가 보고·적용되는가**다.
동일 파형·경계만으로 시간·파형 소유권의 내부 인과를 다시 증명하지 않는다.
slave AutoFlow30→40→35, master35→32→38, 별도 Mode/Frequency 및 inactive Flow를
각각 보존하고 섞지 않는다. 실제 임펠러·유량은 protocol 보고값만으로 증명하지 않는다.

## 확보한 오프라인 근거

기존 `local_control/schedule.py`의 `compile_slave_schedules`는 master 전체 image를 복사한 뒤
slave의 활성 슬롯 Flow만 치환한다. 이 코드를 수정하거나 local-control write 권한으로
native 실기를 대체하지 않는다. 보존 원본·운영 config·safety volume 없이 가상3슬롯 image를
기존 정확한 image에서 `network none`으로 계산했다. 이는 compiler 구성 검사이지 firmware 시험이 아니다.

- image 크기432B, 차이 offset은 정확히5·14·23(세 슬롯의 Flow 필드).
- master 가상 SHA256 `eeab7e365549036e9a3db1ab8e0fcdcffc386cab0aa1266c59f22e3422350647`.
- slave 가상 SHA256 `fb7b9dde326a9fb0ee621964b329fe1baf2931b6d65c2a794b6029163ca7d959`.
- exit0, 장비 접근·write0. 가상00:10/00:15는 실기 절대 시각이 아니다.

## 실행 전에 처리할 차이

현재 native 계획은 master2슬롯/slave3슬롯·서로 반대 Mode를 정확히 인지한다.
새 구성은 양쪽3슬롯·동일 Mode이며 master의15분 전환도 추가된다.
단순 값 덮어쓰기나 기존 marker 의미 변경으로 처리하면 이전 intent의 해석·복원이 달라질 수 있다.
새 계획을 구분하는 표현과 생성·검증·관측 분류의 일관성이 필요하며 legacy 의미는 유지한다.

정적 검토상 직접 관련 source는 `devices/schedule_flow_experiment.py`,
`devices/schedule_linkage.py`, `devices/schedule_transaction.py`,
`schedule_flow_experiment_cli.py`다. 직접 테스트 네 파일과 함께 한정 해제 범위를 검토한다.
추가 파일 필요성이 나오면 이를 자동으로 범위에 넣지 않는다. `local_control/`, schema 입력,
공용 guard, 새로운 임의 write 경로, retry/timeout 변경은 범위에 포함하지 않는다.

## 직전 실패·원복 경계

[직전 Sync](runs/2026-09-14-native-sync-wave-flow.md)는 raw 기능 보고와 별개로 자동 창 자격이
미달했고, 종료 시 역할 해제 확인 오류가 발생했다. 이후 원432B·controls는 독립2 pair에서
복원 확인됐지만 이 사실이 원인 미상의 종료 오류를 해결하지는 않는다.
현재 code를 그대로 쓰면 원 예외가 다시 사라질 수 있으므로, 해당 예외의 class/frame·분기를
비밀값 없이 보존하는 진단을 우선 검토한다. 진단 추가를 동작·안전 검사 완화로 확장하지 않는다.
이 단회에서 원복 오류를 건너뛰어 진행하거나 원인 확인 없이 임계값을 바꾸지 않는다.

새 시간표로 바꾸는 것 자체는 장비 날짜 불일치나 역할 해제 확인 오류의 해결책이 아니다.
같은 분·날짜 조건을 몰래 제거하지 않으며, 자동 자격과 보존 raw 관측을 구분한다.

first의 앱 이전 TimerOFF/manual constant40/F34 및 원 서비스 복귀 의무는 아직 열려 있다.
현재 실제 Timer 상태는 새 실행 전에 확인하며, 이번 요청을 앱 OFF 완료 회신으로 해석하지 않는다.
두 TimerON 상태를 이어 쓰더라도 이번 baseline과 앱 이전 원본은 분리해서 보존한다.
마지막 앱 원복은 사용자 소유이며 임의 추가 OFF→ON 왕복을 만들지 않는다.
