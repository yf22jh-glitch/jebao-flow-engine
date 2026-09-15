# 미완료 native 해제의 소유 scalar 지원

기준: `31dc0a0`, [제품 Sync 실제 실패](runs/2026-09-15-product-sync-detach-ack-timeout.md).
첫 실패는 값 대조가 아니라 ACK timeout이다. 현재 해제 명령의 적용 여부는 UNKNOWN이다.
사용자는 별도 복구와 배포까지 진행하도록 승인했다. 아직 새 명령을 실행한 기록은 아니다.

닫을 질문: **기존 실패를 지우거나 재전송하지 않고 감사된 별도 scalar 경로로 자기 원본을
복원할 수 있는가.** 소프트웨어 소유권/순서는 durable 시험, 실제 수용은 새 raw로 확인한다.

## 고정된 단회 계획

자기 attach id에서 지원 id를 결정한다. 기존 source·detach 원본과 지원 시작 전 실패 record를
내용 해시로 결속한다. 첫 slave 준비만 UNCERTAIN이고 나머지5개 PREPARED인 Pro pair만 대상이다.
임의 target·값·원본·단계 입력은 받지 않는다. 모든 payload/context는 첫 send 전에 fsync한다.

1. master: `linkage_role=independent` (Timer와 시간표는 이 단계에서 바꾸지 않는다).
2. slave: 기존 앱 계약 `schedule_enable=False`로 TimerOFF·SwitchON 및 slave 해제를 요청한다.
   명령 shape는 실제 준비 read의 역할에 따라 기존 writer가 정하며 원본 rollback은 자기 attach다.
3. slave: `manual_mode=constant`.
4. slave: 자기 계획의 `preparation_flow`.
5. slave: 자기 계획의 `preparation_frequency`.

이것은 실패한 6필드 atomic payload 재전송이 아니다. 새로운 function·제한된 payload이며
기존 실제 자격을 검사한다. 각 단계는 직전 실제값이 일치하면 NO_CHANGE, 시도 후 미확인은
재전송 없이 정지한다. slave가 준비 상태에 도달한다고 가정하지 않는다. master-first의 역할
해제와 slave의 TimerOFF 수동 운전은 실제 부수 효과이며 무출력이라고 부르지 않는다.
master만 먼저 independent인 중간 상태의 slave 거동은 미관측이며 예측하지 않는다.
지원 실패는 자동 rollback하지 않는다. 저장 rollback도 결속 중 상태가 아닌 자기 attach 전
원본에 결속한다. 지원 실패를 처리하기 위해 sync_slave를 재설정하거나 TimerON을 켜지 않는다.

## 기존 의무로 돌아가는 조건

별도 fresh 읽기에서 master independent 및 slave의 준비6필드가 확인돼야 지원 완료다.
그 뒤 같은 lease에서 기존 detach를 이어간다. 첫 UNCERTAIN은 기존 fresh-match 분기가 전송
없이 해소하고 나머지5개 단계가 원 image·원 controls를 복원한다. 지원 완료만으로 원복을
주장하지 않는다. 최종 reply의 전체432B·8controls 일치가 원복 완료 조건이다.

지원 record도 generic reverse recovery 대상이 아니다. 다른 미완료 작업은 여전히 거부한다.
재생은 저장된 결과와 현재 확인을 구분한다. 지원 중 실패하면 source/child/support와 실제
comparison·raw를 남기며 자동 새 id·재전송·다른 대안을 실행하지 않는다.

원 UNCERTAIN의 phase가 기존 기계상 VERIFIED가 되더라도 진단에 지원 경로로 목표가
충족됐음을 남기고 지원 id/증거에 결속한다. **원 timeout 전송의 도달 여부는 여전히 UNKNOWN**이다.
이는 전송 성공으로의 소급 변경이 아니라 현재 목표 상태 도달에 의한 복구 완료다.

Auto·clock은 원복 고정 목표가 아니다. 설정 image와 명령으로 변경한 필드만 검증한다.
writer·schema81ee699d·40영수증·일반 guard·timeout/retry는 불변이다. 이전 실패도 불변이다.
현재 snapshot 복원과 first 앱 이전 OFF·Bar classic91/F80 복귀는 별도 의무다.

## 검증 범위

기존 fake는 byte-copy + ACK/read fault injection만 사용한다. 펌웨어 역할 전파·앱 해제
성공 가설을 추가하지 않는다. 결정적 id·원본 결속·다른 미완료 차단·첫 write 전 내구화·
각 단계 no-resend·재생 무접촉·최종 전체 원복·CLI 자격 전달과 실패 출력이 대상이다.
실장 전 최종 source/image/schema를 결속하고 지원1회만 수행한다. 실패하면 새 실행 없이 보고한다.
