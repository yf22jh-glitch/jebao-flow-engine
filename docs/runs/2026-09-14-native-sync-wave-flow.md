# Native Sync 파형·슬레이브 Flow 세 구간 관측

## 실행 중 기록 — 15:16Z 기준

이 절은 실행 중 확보한 사실이다. 최종 종료·원복·서비스 복귀는 아래 후속 절에서 별도로
판정한다. 자동 분류나 전체 시험 PASS를 미리 선언하지 않는다.

질문은 Sync 역할에서 보고 파형이 어느 쪽과 일치하는지, 슬레이브의 자기 시간표 Flow가
변경되는지다. 실제 임펠러 파형·위상·수류량은 protocol 필드만으로 증명하지 않는다.
사용자는 수조 앞에서 끝날 때까지 감시한다고 확인하고 계속 관측하도록 지시했다.

## 승인·실행 결속

- 정책 `18233ae`, source `6978d2dadf0400958241dc7ebd638d4be04b4763`.
- image SHA256 `200564aeeaa084e181c887a431149f9d672d443d9931e376475d720c58a506f1`.
- schema SHA256 `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6`.
- operation SHA256 `d58b03e6bbc3679694393b6b05c58bb2df004e50ac875f65a5d20d861b4f0d03`.
- wrapper SHA256 `5e62da9c7aae45712fcc8232e1fdf666bcbc743e00ea778809cf811a058b4bc8`.
- 실행 container 시작 `2026-09-14T15:01:54.407457775Z`.
- master는 second Pro, slave는 first Pro. Bar는 대상이 아니다.
- 별도 승인 파일과 exclusive 실행 표식 뒤 기존 CLI preflight1회/run1회만 실행했다.
  Anti-Sync는 별도 container를 준비했지만 이 절 시점에는 시작하지 않았다.
- 동료는 저장소 밖 wrapper의 서술만 검토했으며 바이트 검토를 수행했다고 쓰지 않는다.

지명 native receipt의 만료 시간만 승인 범위에서 제외했고, 존재·operation·장비·physical
binding 검사는 유지했다. 검증된 park 존재 상태에서 admission했으며 qualification을 새로
취득하거나 빈 safety root에서 실행한 것이 아니다. journal·archive·receipt 삭제/이동은 없다.

## baseline과 원복 의무

자정 이후 fresh 수집은 `15:00:19.216507–15:01:02.964175Z`, 2 accepted pair/0 failure다.
series `JFS-0728e41eb9a73625b2910b00b410bba6`, SHA256
`bc62b8b2fbb90cb4cb654c218256e27be5e87a528250c4795724639abc01110d`다.
두 Pro ON/TimerON/independent, 원 시간표432B는 각각 byte-exact였다.
당시 Auto는 master Sine40/F40, slave Sine67/F80이며 이전 65/45를 현재값으로 쓰지 않았다.
이 수집 PASS는 취득 판정이며 시계 안정성이나 기능 질문의 PASS가 아니다.

하네스 baseline은 앱 준비 **이후** 두 대 TimerON이다. first의 앱 이전 원본은
TimerOFF/manual constant40/F34이고 마지막 앱 토글은 사용자 소유다. second의 수용된
inactive manual30/F32를 옛31로 되돌리라고 요구하지 않는다. 하네스 원복과 이전 실행에서
남은 first TimerOFF·서비스 복귀 의무는 따로 계상하며 이번 시작으로 이전 의무를 닫지 않는다.

## 계획과 실제 중간 관측

확정 device-local 경계는 **00:11 / 00:16**이다. 새 임시 상한41, safe manual master30/slave31을
유지했다. Sync 정상 경로 정적 logical 호출 회계는 sentinel forward2/restore2를 포함한22이며,
실제 physical frame 수나 모두 성공한 호출 수라는 뜻은 아니다.

field 두 대상 VERIFIED는 `15:02:32.080304Z`였다. 그 뒤 master/sync_slave 역할이 raw에
보고됐다. 첫 관측은 pair1의 `15:02:43.723151–15:02:43.942749Z`다.

| 관측 | master Auto | slave Auto | slave 별도 Mode/Frequency | slave 별도 Flow |
|---|---|---|---|---|
| pair1, 첫 경계 전 | Sine35/F30 | Constant30/F5 | Sine/F30 | 31 |
| pair49, 15:11:11.827807–15:11:11.848349Z | Constant32/F5 | Sine40/F30 | Constant/F5 | 31 |
| pair77, slave 15:16:06.743748Z | Constant32 유지 | Sine35/F30 | Constant/F5 유지 | 31 |

pair50과78에서도 각각 같은 전환 후 값이 관측됐다. 저장 Constant wire Frequency0과
보고 Auto Frequency5는 구분하며 이 차이의 원인을 단정하지 않는다.
슬레이브 AutoFlow는 자기 예약값 **30→40→35**로 보고됐고, 별도 Mode는 master Auto의
Sine→Constant 변화와 일치했다. 별도 Flow31을 현재 스케줄 출력으로 오인하지 않는다.
이 사실은 보고 필드에 대한 관측이지 실제 수류·내부 제어 인과·alias 부재의 증명이 아니다.

## 자동 관측 자격과 원본 관측의 구분

65 pair의 보존 원본을 오프라인 계산했을 때 master는 모두 action3 reply, slave는 모두
action4 report였다. 취득 span 최대1.038815785초, 시작 간격10.442043576–11.162783101초다.
시각 차이는 일정30초가 아니며 같은 날짜에서는0·30·31·-4초, 이후 날짜 차이를 포함한
값들이 나왔다. 이 수치만으로 RTC 오프셋·push staleness·펌웨어 날짜 갱신 원인을 확정하지 않는다.

pair7은 양쪽09-14였고 pair8에서 master만09-15로 바뀌었다. pair8의 clock은
master `2026-09-15T00:03:48`, slave `2026-09-14T00:03:31`이다.
이는 raw YMD/HMS에서 디코딩된 보고 날짜이며 호스트 날짜로 덧씌운 값이 아니다.
78 pair 기준 날짜 불일치71, 날짜를 포함한 같은 분 불일치72였다.

현 monitor는 날짜를 포함한 같은 분을 요구하고 창 제외 시 연속 표본 누적을 초기화한다.
따라서 raw 변화가 남는 것과 사전 등록된 W0/W1/W2 안정 관측 자격은 별개다.
완료되지 않은 자동 판정을 "연동 없음"이라는 부정적 기능 결과나 PASS로 바꾸지 않는다.
실행 중 source·게이트·임계값·pause·장비 시계를 바꾸지 않았다.

원본은 private 보존하며 저장소에는 식별자 원문이나 raw를 넣지 않는다. 최종 raw·intent·로그
digest와 종료·원복 결과는 실행 종료 후 아래에 덧붙인다.

## 종료와 독립 원복 확인

container는 `15:20:57.275450558Z`에 exit2로 종료했다. terminal intent는
`15:20:57.210343Z`, `experiment_failed_restored`, 기능 분류 없음이다.
전체97 pair/194 raw frame에 header1줄을 포함한195줄·202,962B를 보존했고 마지막 줄도
완결됐다. 취득 UTC span은 `15:02:43.614343–15:19:38.015448Z`다.
마지막까지 slave AutoFlow35·별도 Mode Constant가 유지됐고 master Auto Constant32도 유지됐다.
최종 날짜 불일치는90/97, 날짜 포함 같은 분 불일치는91/97이었다.

### 서로 다른 실패 두 가지

1. **자동 관측 자격 미달:** `15:19:48.022682Z`의 종료 로그는 `windows=[]`,
   `conflicting=[]`, `raw_failed=False`다. 같은 분·fresh clock·연속 안정 표본 계약을
   충족하지 못했다. 따라서 전체 자동 기능 판정은 없으며 raw에서 본 변화를 이 판정의 PASS로
   바꾸지 않는다. 반대로 자동 미달을 "장비가 자기 Flow를 변경하지 못했다"로 쓰지도 않는다.
2. **관측 종료 뒤 역할 해제 확인 오류:** `15:20:12.413948Z`에 role observation 실패,
   `15:20:40.357671Z`에 role disarm 실패가 기록됐고 이어 `control_disarm_unverified`가
   보존됐다. 이는 단순한 기능 가설의 부정적 관측과 다르다.

이번 CLI 진단은 `LinkageApplyError → TemporaryScheduleRollbackUnsafeError`의 class/frame
chain을 보존했다. 가장 안쪽 frame은 `schedule_transaction.py`의 `_cancel_observation`이며,
해당 `except Exception` 분기가 `CONTROL_DISARM_UNVERIFIED`를 `from None`으로 올린다.
그러므로 그 안에서 사라진 최초 예외와 정확한 실패 장비·I/O 원인까지 회수됐다고 주장하지 않는다.
자동 관측 미달이 역할 해제 오류를 일으켰다는 인과도 입증하지 않았다.

이후 기존 보상 경로에서 `15:20:43.224564Z` role disarmed,
`15:20:46.717608Z` 두 대상 field restored, `15:20:57.190089Z` outer restored가 기록됐다.
빠른 두 번째 보상 성공으로 앞선 실패를 지우지 않으며 정상22 logical 호출을 실제 총량으로
단정하지 않는다. 실패 분기의 실제 전송 수는 이 stage 기록만으로 확정하지 않는다.

사후 독립 authenticated 수집은 **`15:21:38.890840–15:22:22.963799Z`**, 2 accepted pair/0 failure다.
series `JFS-3161084cd115950967cca1afc471dce7`이며 두 번 모두 다음을 확인했다.

- 두 원432B 시간표가 해당 실행 preflight digest와 byte-exact.
- master: ON/TimerON/independent/manual constant30/F32, 당시 Auto Sine40/F40.
- slave: ON/TimerON/independent/manual constant40/F34, 당시 Auto Sine67/F80.
- 원 controls 대비 mismatch0, active problem0, invalid slot0.
- native/temporary/role 복구 journal과 emergency latch 네 경로 부재.

이것으로 **이번 하네스가 소유한 앱 준비 이후 baseline 원복**을 확인했다.
first의 앱 이전 TimerOFF와 원 서비스 재시작은 아직 미완료다. 사후 independent reply에서
두 날짜가09-15로 일치한 사실을 다음 slave report의 날짜 일치 보장으로 확대하지 않는다.

## 보존 산출물 결속

| 산출물 | SHA256 |
|---|---|
| 원본 raw195줄 | `3f0e953e7c571d00afa90b80d0b91475e80b17381d043ed434bfeebe8af0686b` |
| 실행 preflight intent 사본 | `0d05de70d89c7621ea1024fbc6cc174437a08a4b0c0466a6cdc0b25258c5c024` |
| 종료 intent 사본 | `2a2d087943f9258f3ca386bbb55c7589350d75262ba7c53ff822a266b4db21bd` |
| exclusive 실행 표식 | `497f353b5846eed6cbcf3fd423bec576c7676d4adaa3fe9e4fed8447a9148e38` |
| wrapper 종료 결과 | `a876532b82e87918bd4401b85621d6897bef4f2681653299131f72ee7a475173` |
| stdout 및 container stdout 각각 | `fe80b5774061e67863ae23cba671e9eebc9ba8fbd4042ee9574788080f0c43e3` |
| stderr 및 container stderr 각각 | `189c8b9471893b384353d79ff6fb7c249a0ba22681c918abc1817b72f9be1da8` |
| 사후 독립 series | `a13cd598838c9b5c4ee3f4981351ae5b74d857eb7af87a3ab836e80af0e668b4` |
| preflight 대비 사후 원복 대조 | `829e6c458eab10c1a01f040367c724a4c5c6abf1ec72ba2660b76b4d308862eb` |
| 실행 중65 pair 시각 분석 | `acd21c189229b4ddeb205b2a2b6f8ad0037c56446800a33fc03bc892e9fb8e80` |

raw와 독립 capture는 등급(a), intent·stage·로그는 등급(b), 원본을 읽어 재구성한
표·시각 통계는 파생 분석이다. 분석 표만으로 gate 적격 count·실제 임펠러 출력을 재구성하지 않는다.
역할·물리 장비·reply/report 종류가 이 실행에서 교차 통제되지 않았다는 한계도 남는다.

## 이 실행에서 새로 확보한 것과 다음 경계

Sync 연결 상태에서 자기 AutoFlow30→40→35가 두 예약 경계를 거쳐 보고됐고, 별도 Mode가
master Auto의 Sine→Constant 변화와 일치하는 원본을 확보했다. 보고 필드의 변화는 관측됐지만
실제 출력 적용·물리 파형·자동 안정 관측 자격을 완료했다고 쓰지 않는다. 정보0 실행이 아니다.

역할 해제 확인 오류가 별도로 발생했으므로 정책의 첫 회 기술적 실패 시 둘째 자동 중단에 따라
**Anti-Sync는 시작하지 않았다.** 종료 뒤 새 스케줄·역할 write나 source/test 변경은 없다.
사용자의 계속 진행 요청은 기록·원인 확인을 이어갈 근거이지만 원복 오류를 무시하거나 임의
새 전송·timeout·게이트 변경을 정당화하지 않는다. 추가 실기의 범위는 이 실패와 원복 증거를
공개한 상태에서 정하며 현재 안전 핵심·동결을 자동으로 해제하지 않는다.

## stderr 전수 확인 보충

stderr109줄은 JSON108줄과 마지막 CLI 오류1줄이다. JSON은 park admission2줄,
schedule-linkage 로그105줄, failure chain1줄이며 일반 traceback은 없다.
`15:20:12` role observation 실패 시각의 별도 class/traceback은 이 stderr에 없다.
따라서 chain 밖의 해당 로그에 최초 예외가 살아 있을 것이라는 제안을 확인했지만 회수하지 못했다.
코드·임계값을 바꾸거나 같은 실기를 반복해서 이 공백을 덮지 않았다.
