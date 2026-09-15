# Pro Sync/Async 결속·원본 복원 실행기

## 목적과 범위

사용자가 미완료 실행기까지 구현하라고 요청했다. 1차 compiler의 외부 JSON을 실제 제품 실행
경로에 연결하고, 결속 전 자기 원본으로 해제하는 기능을 완성한다. 장비 write·배포·초기화는
이번 작업에 포함하지 않는다. Bar의 native 역할이나 미확정 timer 결합 명령은 구현하지 않는다.

닫을 질문은 **중단·재시작을 포함하여 원본을 잃거나 중복 전송·재결속하지 않는 실행 경로가
있는가**이다. 근거는 기존 lease/journal/writer의 정적 경로와 durable artifact/fault injection이다.
펌웨어가 실제 해제 명령을 수용했다는 증거를 synthetic 시험으로 만들지 않는다.

## 순서

1. 현재 manifest의 서로 다른 Pro를 확인하고 필요한 control/schedule 자격을 검사한다.
   전체 장비 lease 안에서 연동 전 independent raw status와 프레임을 모두 보존한다.
   TimerOFF였더라도 432바이트 전체 시간표와 8개 제어값은 원본에 포함한다.
2. slave 다음 master에 **한 frame**으로 independent/TimerOFF/전원 ON/constant·준비 Flow/Frequency를
   설정한다. 원래 OFF 장비에도 중간 ON이 생기는 준비 동작이며 무출력으로 부르지 않는다.
   준비값은 JSON의 `preparation_flow`/`preparation_frequency`로 명시한다. 기본 30/20은 제품의
   최소 정상 Flow와 명시적 준비 주파수 선택이지 과거 단회 실기 승인의 승계가 아니다.
   두 역할에 같은 준비값을 사용하며 미리보기에 출력한다. 역할별 30/31 실험값은 승계하지 않는다.
   급식·custom 저장값은 준비 중 건드리지 않는다. inactive FeedTime=0인 원본도 최종 복원에서
   앱 입력 최솟값으로 정상화하지 않고 원 제어 bytes로 복원한다.
3. independent·TimerOFF 반환값이 확인된 상태에서 master와 slave 시간표를 적재한다.
   master 전체 설정을 상속하고 slave 활성 슬롯 Flow만 달라진다.
4. master 역할·TimerON, slave Sync 역할·TimerON을 설정한다. Async는 확인된 Sync 다음에
   Async로 이동한다. final image·전원·Timer·역할을 읽어 일치해야 결속 완료다.
   연동 중 별도 Mode/Frequency가 변할 수 있다는 보존 관측을 반영하여 비활성 값을 고정하지 않는다.
5. 해제는 attach id로 결정되는 **새 요청**이다. 같은 atomic safe 해제, 원 시간표, 원 8개 제어값,
   별도 fresh 읽기의 전체 대조 순서다. 시각·현재 Auto 슬롯을 과거로 되돌리지 않는다.
   자기 attach가 포착하지 않은 baseline은 복원하지 않는다. 원 TimerON이면 OFF→원 TimerON의
   두 전환과 중간 수동 운전이 생긴다. atomic 제어는 앱의 Timer bool 단독 명령과 구별한다.

## 내구성과 복구 방향

기존 local-control root의 journal/blob을 사용한다. native context에는 소유 원본·논리/물리 결속·
계획과 schema를 고정하고, 첫 write 전에 모든 전송 대상·원본을 fsync한다. 단계별 attempting을
전송 전에 저장하고 ACK·원본 반환 프레임·판정값을 보존한다. 로그만으로 성공을 만들지 않는다.

일반 `recover`의 역순 rollback은 native record에 사용하지 않는다. native 실패/해제는
safe 해제→원 image→원 controls라는 방향을 유지하는 전용 복구로 다룬다. 기존 power-off
`restore_on_recovery=False`의 적용 범위는 넓히지 않는다. 성공한 attach 기록은 불변이다.
일반 recover의 거부에는 전용 `recover-wavemaker` 명령과 결정적 detach id를 함께 제공한다.

같은 요청 재호출은 이미 저장된 결과만 반환하며 현재 상태 확인으로 부르지 않는다. 해제 id는
attach에 고정되어 다른 id로 같은 해제를 반복하거나 이전 원본을 새 결속에 덮을 수 없게 한다.
미해제 attach의 소유권은 journal에서 확인한다. 다른 unfinished 작업은 여전히 차단한다.
시도 여부가 불확실한 action은 다시 보내지 않고 fresh 반환값이 해당 목표와 일치할 때만
해소한다. 일치하지 않으면 증거와 원본을 보존하고 정지한다. 자동 전진 재시도·timeout 증가는 없다.
attach 도중 실패하면 자기 원본 복원을 시도하되, 복원 실패를 숨기거나 성공으로 바꾸지 않는다.

## 읽기와 판정

independent 원본과 최종 원복은 reply-only로 읽는다. native 역할 구간은 기존 프로토콜의
reply/report 수용 기능을 명시적으로 사용하고 action·raw frame·읽기 시각을 보존한다.
기존 공용 writer 읽기 정책과 transport timeout/retry는 불변이다. report를 reply라고 쓰거나
현재성을 보장했다고 과장하지 않는다. 기대/실측 불일치는 필드별로 출력하고 raw를 보존한다.
해제 직후 reply가 즉시 성립한다는 실기 증거는 없다. 반환값을 얻지 못하면 미확인 상태로
정지하며, 후속 명시적 복구는 이미 시도한 명령을 다시 보내지 않고 확인부터 한다.

## 검증 및 전달

두 역할의 실제 production API 배선, 단계 순서, 전체 image와 모든 복원 제어값, 중복 요청,
시도 전/후 중단, 지연 반환값, 잘못된 신원, 원본 변경, 이전 원본 재사용, 재시작 후 복구를 검사한다.
기존 계약 시험을 줄이지 않는다. targeted 이후 전체 suite는 한 번 실행하며 중간 실패·환경 대조는
그대로 기록한다. 앞선 macOS 전체 검사에서 재현한 기존 5개 실패를 이번 코드 성공으로 덮지 않는다.
소프트웨어 실행기 완료와 실장 qualification·배포를 분리해서 보고한다.
