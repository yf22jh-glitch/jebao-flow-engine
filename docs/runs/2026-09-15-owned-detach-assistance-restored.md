# 같은 지원 operation의 미시도 단계 연속: 원본 복원 PASS

**두 Pro의 이번 attach 원본으로 해제·복원 완료. 운영 배포와 제품 Async 실기는 아직 별도다.**
[최초 조회 중단](2026-09-15-owned-detach-assistance-read-stop.md)의 exit2는 그대로 보존한다.
코드·schema·자격·prepared payload 변경 없이 같은 지원 id를 이어 실행했다. 재결속이나 초기화는 없다.

## 실제 결과

- source `5fbf58df1fc0312797cd06cf01c4f13f93f6e564`, image `187c1bd1…6bd24` 동일.
- UTC `09:39:07.558757–09:39:40.313559`, exit0.
- 지원 action0 master는 기존 VERIFIED skip으로 재전송0. 남은 네 단계 중 slave의
  schedule_enable=False·manual_mode=constant·manual_frequency=20 세 건은 ACK·after match,
  manual_flow=30은 fresh 일치 NO_CHANGE였다.
- 기존 child의 첫 atomic은 **재전송0·ACK None**을 유지하면서 fresh 준비6필드 일치로
  VERIFIED가 됐다. 진단 `assisted_target_observed_delivery_unknown`과 evidence의 지원
  id/journal/final 결속이 출처를 남긴다. **원 timeout 전송의 도달 여부는 계속 UNKNOWN**이다.
- child의 나머지5단계는 ACK·after match로 완료됐다. 지원 전체 ACK4(최초1+연속3),
  NO_CHANGE1이고 이번 연속 구간 ACK는3+5=8이다. 지원5+child6을11회 전송으로 세지 않는다.

최종 별도 reply의 원432B와8controls가 두 장비 모두 exact였다. child revision35
SHA `c79ef1db1da1bc3b7e6fcfd0391da76a8f9d46447784a8bee37a7b56c3ebfbd7`, support revision26
SHA `71d20b31bfbc5c81b0cd790366b058261b2330dc4a81adaf9a9f1554703b1bb3`, 둘 다 VERIFIED다.
원 attach record SHA `ccead982…8461a`는 불변이며 과거 성공을 현재 증거로 대신하지 않았다.

## 사후 실측과 의무 구분

추가 reply-only6 표본 UTC `09:40:53.472834–09:40:58.162447`, command0. 전수 대조:

| 장비 | 원본 비교 | 현재 보고 상태 |
|---|---|---|
| second Pro | 원432B `60edc697…56b99`와8controls 모두 일치 | ON·TimerON·independent·manual constant30/F32·Feed10/custom5 |
| first Pro | 원432B `c50331c2…9426`와8controls 모두 일치 | ON·TimerON·independent·manual constant40/F34·Feed10/custom5 |
| Bar | 원384B `8c933a4d…67ea` 유지 | ON·TimerOFF·independent·classic40/F80 |

Auto·clock은 현재 관측일 뿐 고정 원복 목표가 아니다. 물리 파형·실제 유량은 판정하지 않았다.
first의 앱 이전 TimerOFF와 Bar classic91/F80 최종 복귀 의무는 이 원복에 흡수하지 않는다.
원 runtime은 여전히 stopped이고 운영 input 세 digest도 불변이다. 소프트웨어가 만들어 둔
시험 시간표·Sync 역할 잔존 의무만 이번에 닫혔다.

## 증거와 제한

private `evidence-assistance-continuation-01`에 record3/blob178/report1을 offline 복사·해시 확인했다.
report SHA `1354266031aeb93c627018a8afb4ae06e697850a5e55e234399526bd5a8df3cb`.
원 보호 root는 RO, 산출물은 기존 UID로 생성했다. 앞선 export와 로그는 덮어쓰지 않았다.
operator는 같은 종료 container를 한 번 start해 동일 operation을 재개했다. 그 과정의 완료 후
`docker exec` 조회1건은 container 종료로 exit1이며, 실기 exit0와 구분한다. 연속 감시 미주장.

**새 사실:** master 해제 뒤 별도 앱 scalar 경로가 slave의 독립·TimerOFF 상태를 실제 만들었고,
원본 전체 복원까지 완료했다. 어느 개별 순서가 펌웨어 원인인지, 처음 atomic이 왜 응답을 못 받았는지는
미확정이다. fake의 동작을 근거로 하지 않으며 첫 실패를 이번 성공으로 소급 정정하지 않는다.
source 변경 없이 제품 Async1회와 기존6개 제어 보존 배포를 이어간다.
