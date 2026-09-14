# 사용자 퇴근에 따른 작업 중지

상태 갱신: 아래는 중지 당시 기록입니다. 이후 사용자가 “이어서 진행하자”로 오프라인 작업을
재개했고, 진행·검증 결과는 [진입 판정 구현 기록](native-sync-parked-admission-implementation.md)에
별도로 남깁니다. 퇴근 전 현장 확인을 새 실기의 현재 현장 확인으로 재사용하지 않습니다.

사용자는 “나 퇴근 할꺼니까 지금 하던거까지 하고 중지하자”라고 지시했습니다.
작성 중이던 진입 검사와 회귀 테스트 초안만 보존하고, 새 빌드·설치·배포·실기는 진행하지 않습니다.
재개 지시와 현장 실기 조건이 다시 갖춰지기 전 장비 명령을 보내지 않습니다.

## 현재 상태

- 정책·준비 기록은 `7ba8959`에 커밋돼 있습니다.
- source 변경은 Sync 시험 CLI의 전용 guard 초안입니다. 공용 guard·다른 CLI·장비 writer는
  바꾸지 않았으며 기존 journal reader와 immutable archive 확인을 재사용하도록 작성했습니다.
- 신규 테스트 초안은 실제 park fixture의 archive 정상/손상/누락·nonterminal·파일 안전성·
  전역 lock·긴급중단·원본 불변을 확인하려는 것입니다. 아직 성공 결과가 없습니다.
- network-none·production volume 미마운트 테스트 실행을 한 번 시도했지만 기존 이미지에
  pytest가 없어 `No module named pytest`로 종료됐습니다. 테스트 수집·실행 전 환경 실패입니다.
  archive 추출 시 macOS provenance 확장 header 경고도 있었으며 이를 코드 실패로 쓰지 않습니다.
- 새 dependency 설치나 이미지 빌드는 하지 않았습니다. 전체 suite·Ruff·독립 최종 검토는 미완료입니다.
- 구현 source/tests는 미커밋 상태로 보존합니다. 완료·배포 가능으로 판정하지 않습니다.

## 수조 및 보존 자료

Sync·Async 실행, 앱 준비, 장비 설정·시간표 write는 하지 않았습니다.
원 서비스는 이전 exact image로 재시작된 상태이며 이번 중지 단계에서 변경하지 않았습니다.
두 Pro의 원 시간표 각432바이트·14개 슬롯, 원 controls, 보존 raw가 남아 있습니다.
first Pro TimerOFF의 시간표도 포함합니다. second Pro의 수용된 inactive manual30을 바꾸지 않았습니다.

검색 목록 정정과 두 Pro 읽기 각2회 통과는
[준비 기록](runs/2026-09-14-native-sync-preparation-admission.md)에 있습니다.
보류 복원 기록은 삭제·이동·변경하지 않았습니다.

## 재개 시 할 일

현재 diff·정책 범위를 다시 확인하고 격리된 테스트 환경에서 초안을 검증합니다.
영구 replay 거부 및 CLI dispatch가 전용 guard를 실제로 사용하는 계약도 확인합니다.
미검증 source를 운영에 쓰지 않고, 결과 기록·독립 검토 뒤 새로운 image와 원복 경로를 준비합니다.
앱 조작은 그 뒤에 요청하며, 현재 중지 지시를 과거 실기 승인으로 덮어쓰지 않습니다.
