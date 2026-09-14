# Native Sync-only 단회 · 실행 계획

상태: **사용자가 Sync 단회·원복·만료 날짜 예외를 승인. 별도 정책 커밋·독립 검토와 실행 준비 전.**
`6ecb276`의 구현 예외는 소진됐으며 새 실행 권한은 AGENTS의 별도 승인 절에 기록합니다.
앱 준비와 fresh baseline 확인은 아직 수행하지 않았고 device command·배포는0입니다.

## 질문과 범위

두 Pro의 Sync/TimerON에서 slave의 protocol-reported Mode·AutoFlow·AutoFreq 내용과
전환 시점이 누구의 시간표를 따르는지, 서로 다른 값·두 경계의 raw pair로 구분합니다.
Async 실행·물리 유량/위상 판정·일반 firmware 성공률은 이번 범위가 아닙니다.
Bar·리턴·도징은 대상이 아닙니다. 두 Pro의 master/slave binding은 fresh identity 확인 뒤
실행 manifest에 고정하며 과거 별칭만으로 선택하지 않습니다.

| 역할 | 임시 safe manual | A slot | B slot | 경계 |
|---|---|---|---|---|
| master | Constant30/F20 | Sine35/F30 | Constant30/F0 | T |
| slave | Constant31/F20 | Constant32/F0 | Sine40/F30 | T+5분 |

관측은 900초이고 준비·복원·사후 확인 시간이 추가됩니다. 임시 출력 상한은41, 계획값 최고는40.
원 시간표로 복귀한 뒤의 실제 출력은 그 시점 slot에 따르며 임시 cap과 혼동하지 않습니다.
master T는 CLI의 분 경계 계산으로 선택하고 실행 직전 lead380..420초를 그대로 검사합니다.
과거 실행의 시각이나 token을 재사용하지 않습니다.

## 실행 전 확인된 차이

### 네이티브 테스트 자격 기록의 만료

2026-09-14T08:52:21Z read-only inventory에서 native 형식 receipt 두 파일이 있었고,
둘의 valid_until은 `2026-08-29T02:56:43.659868Z`였습니다. 같은 qualification operation의
SHA-256은 `ebac79ff21cc3b95dc408227fd2b53f782e54c77947a1cc9c6390b0b606c16dc`입니다.
receipt 파일 SHA-256:

- `b0daaacf4de8f0465bb9f26c15dbb0a91bab2539b5e46130a0d38405057d55aa`
- `8e02974714c5595f0905ce04c4f77fc0235d0add4ba8a19bf25097bbe2bef177`

같은 directory의 나머지16개는 local-control 형식이므로 native receipt를 대신하지 않습니다.
첫 inventory는 모든 파일에 valid_until이 있다고 가정해 KeyError로 끝났고, 두 번째는 schema를
구분했습니다. 둘 다 기존 image·network-none·volume read-only로 실행했고 변경은 없습니다.
이 inventory의 별도 원출력 artifact는 아직 고정하지 않았으므로 위 관측은 등급(c)입니다.
파일 digest 관측을 현재 physical binding 일치나 새로운 qualification으로 올리지 않습니다.

현재 코드가 제공하는 시간 유효성 예외를 본 단회에만 적용하는 승인을 받았습니다.
영수증 존재·지명된 operation·device_id·physical binding 일치는 그대로 검사하고,
파일/만료일을 수정하지 않습니다. 과거 만료 예외 승인은 소진됐으며 재사용하지 않습니다.
대안은 별도 승인된 새 qualification 또는 park이고, 추가 qualification write를 자동 실행하지
않습니다. 이 차이를 승인 질문에 포함했고 사용자가 원복을 조건으로 승인했습니다.

만료 예외 뒤에도 첫 control write 전 fresh state·TimerON·원 schedule 일치 검사를 유지합니다.
이는 현재 검사 대상의 불일치를 잡는 것이며, 지난 기간의 모든 장비 변화나 고장을 배제하거나
새 qualification의 실제 write 검증을 대신한다는 뜻은 아닙니다.

확인한 native intent·outer journal·temporary schedule journal·role journal·emergency latch
다섯 경로는 inventory 시점에 없었습니다. 이를 모든 lock·다른 operation의 무충돌 증명으로
확장하지 않고 실제 preflight에서 기존 배포·physical lease 검사를 그대로 수행합니다.

### 시작 TimerON과 복원 소유권

하네스 `_stage_devices`는 첫 write 전에 양쪽 TimerON을 요구합니다. 마지막 수용 상태의
first Pro는 TimerOFF이므로 그 상태 그대로 실행할 수 없습니다. current fresh read로 먼저
재확인하고, TimerOFF라면 **현재 출력은 manual 값**, 시간표의 현시각 slot은 **TimerON으로
바꾸면 적용될 후보**로 구분하여 사용자에게 전환 폭을 공개합니다. slot을 이미 active라고
부르지 않습니다. second Pro의 수용된 inactive manual30은 정상화하지 않습니다.

앱 준비 전에 writer/recovery 구성·원복 경로와 현재 상태를 확인합니다. 준비가 끝난 뒤에만
필요한 first TimerON 앱 조작을 별도로 요청하고 앱을 종료합니다. 하네스가 그 이후의 fresh
baseline을 자기 snapshot으로 저장하며 그 baseline까지만 exact 복원을 소유합니다.
앱 준비 이전 first TimerOFF/manual40으로 돌아가는 마지막 앱 조작은 사용자 소유입니다.
앱 전·후 baseline을 분리하고 하네스에 앱 이전의 제3자 baseline 복원을 맡기지 않습니다.
사후 second TimerON의 current slot은 그때 시각으로 대조하며 옛 slot 이름을 목표로 고정하지
않습니다. 준비 자체가 중단되면 first의 앱 변경도 사용자에게 되돌리도록 안내합니다.

## 정상 경로의 write 회계

구 계획의 “forward8”을 전체 write 수로 사용하지 않습니다. 현재 fixed CLI는 sentinel 검사를
반드시 포함합니다. unused slot의 behavior-neutral 패치는 운전 변화가 없을 것으로 기대하나
실제 schedule write이며 생략하지 않습니다. field staging은 전체432-byte 시간표를 교체합니다.

| 순서 | logical 호출 | 구분 |
|---|---:|---|
| safe TimerOFF/independent/manual | 2 | 전진 |
| sentinel unused-slot patch | 2 | 전진 |
| sentinel 원 image 복원 | 2 | 보상 |
| field schedule staging | 2 | 전진 |
| 임시 TimerON arm | 2 | 전진 |
| master → sync_slave 역할 | 2 | 전진 |
| inner slave → master role detach | 2 | 보상 |
| composed safe TimerOFF/independent | 2 | 보상 |
| field 원 image 복원 | 2 | 보상 |
| outer safe detach | 2 | 보상 |
| outer 원 controls/Timer 복원 | 2 | 보상 |

정상 경로에서 모든 위 호출을 거치면 전진10+보상12=22입니다. 상태 재대조에 따른 skip이나
실패 분기의 보상 호출은 더 늘거나 줄 수 있어 별도로 계상합니다. 이는 정적 logical 회계이지 관측된 physical send22회라는
주장이 아닙니다. 재시도·상한을 맞추기 위한 추가 write를 만들지 않고 실제 intent·호출·ACK·
raw·readback을 구분합니다. sentinel forward90초·recovery1800초와 관측900초는 서로 다른 예산입니다.

## 관측·중단·원복

구체적 판정 매핑은 [구현 기록](native-sync-only-implementation.md)과
[두 경계 설계](native-sync-schedule-ownership-recheck.md)의 W0/W1/W2 규칙을 실행 전에 고정합니다.
오래된 설계의 Flow89 baseline·승인·forward8 총량은 재사용하지 않습니다.
master/slave raw frame·ordinal·device NowTime·host monotonic span·digest/length를 보존하고,
W1은 offline timing 판정으로 분리합니다. 불충분한 표본·conflict는 UNKNOWN이며
OWN_SCHEDULE을 Async PER_SLOT_POWER_VERIFIED로 올리지 않습니다.

실행 전 exact source/image·private config·저널/receipt·명령 대상·원본 시간표를 결속합니다.
운영 서비스의 기존 image/config를 보존하고 경쟁 writer를 배제하며 HA Core·broker는 조작하지
않습니다. 준비 실패는 device command0으로 종료하고 원 서비스 복귀를 확인합니다.
실제 하네스는 단회이며 다른 조건으로 자동 재시험하지 않습니다.

관측 종료·오류 때 기존 ordered rollback을 먼저 완료하고 그 뒤 사용자 앱 복원을 진행합니다.
복구 실패나 안전 interlock 시 두 Pro가 낮은 수동값·부분 설정·꺼진 상태로 남을 수 있으며
자동 정상 복귀를 약속하지 않습니다. 현장 위험이면 사람의 중단이 우선이며, 그 뒤 stale
snapshot으로 자동 복구하지 않습니다. 서비스 stop/epoch deadline은 물리 차단 보장이 아닙니다.
성공/실패와 별개로 native journals·원 controls·원432-byte images의 복원을 fresh raw로 확인합니다.
terminal 문자열만으로 원복 완료를 주장하지 않습니다.

## 승인과 다음 단계

새 단회 실행 해제와 별도 정책 커밋, exact 구성의 임시 writer/recovery 실행 및 원 서비스 복귀,
Pro 두 대의 위 Sync 조작·ordered recovery, 필요한 앱 TimerON 준비와 마지막 앱 복원,
**지명된 native receipt의 시간 유효성만 본 단회에서 제외**하는 항목을 사용자에게 공개했습니다.
사용자의 “ㅇㅇㅇㅇ 그후 데이터 원복만 시켜주면되” 승인을 AGENTS에 별도로 기록합니다.
승인 뒤에도 identity/복원 수단 등 기존
preflight가 성립하지 않으면 write하지 않습니다. 새 source 변경이나 Async로의 확대는 별도입니다.
