# run: app-async-toggle-and-owned-restore

사용자 앱 조작 중 LAN 관측에서는 slave가 계속 sync_slave를 보고했다. 사용자는 앱에서
Async로 잠깐 표시된 뒤 Sync로 돌아온다고 말했다. 이는 화면 관찰(c)과 LAN raw(a)로 구분한다.
이후 사용자의 명시적 원복 지시에 따라 제품6회차 자기 원본을 복원했다. 공장 초기화·새 결속·
배포는 이 기록에서 실행하지 않았다. [원 관찰 실행](2026-09-15-product-async-operator-observation.md)의
실패와 전달 UNKNOWN을 소급 변경하지 않는다.

## 앱 조작 관측

기존 9860107 이미지의 ReadOnlyGizwitsSession과 기존 resolver/lease를 사용한 private collector다.
두 Pro에 fresh session pair read, 보고 수용, pair 완료 뒤10초 대기, 최대60pair로 준비했다.
사용자 원복 지시 때 STOP으로 종료했으며 실제25pair/50frame을 저장했다. 제어·원복 호출은0이다.
처음 script 접근 권한 때문에 Python이 실행 전 exit2였고, 비밀값 없는 script의 읽기 권한만
조정한 뒤 동일 컨테이너를 시작했다. 최초 실패에서 장비 접촉0, source/test 변경0이다.

- 수집 구간: 2026-09-15T16:08:08.659401Z–16:12:42.473073Z
- collector 종료:16:12:52.491803Z, exit0
- master:25개 직접reply(action3), 전부 master
- slave:25개 push report(action4), 전부 sync_slave
- 두 장비 각각 저장 image SHA가 수집 구간에서 불변, 읽기 오류0
- slave Auto=sine/30/F30, master Auto=sine/35/F30 보고; 변동 Auto·시각을 고정 검증하지 않음
- raw100개(wire50/status50)의 SHA를 archive에서 직접 재계산해 metadata와 일치 확인
- archive153파일=raw100+sample50+plan1+complete1+STOP1

사용자가 Async를 눌렀다고 두 번 알려줬으나 버튼의 정확한 시각과 앱 명령 wire는 보존하지
않았다. 표본 간 짧은 Async 전이를 관측하지 못했을 수 있고, push 보고의 생성 시점을 확정하지
못한다. 따라서 Async 내부 적용 부정·앱 문제·펌웨어 원인을 단정하지 않는다. LAN 접속 자체도
관측 조건이다. fault 보고0을 물리·생물 안전으로 승격하지 않는다. Bar는 관측·제어0이다.

artifact `app-async-transition-observation-01`:

- script SHA:3bcb49d2652c2239b85ea9d59c396fad60d9c9a5962e17e9ab6967658f45e8e1
- stdout SHA:289c5daa5a19acde25985043a274f5166ad6a733f785d6fc08c0136004677ba1
- private archive SHA:de9697d5c69485de86897e909541802c2fad0025d947fda2843a1539bd59488b

## 명시적 자기 원복

사용자 “일단 원복을 하고 async 로 바로 걸어봐” 뒤 관측을 끝내고 먼저 원복만 실행했다.
source는 wm_operator_observe_9860107_01, 사용 이미지와 source9860107은 그대로다.

- recover:16:13:34.687594Z–16:13:48.019521Z, exit2. 첫 slave atomic1 send/ACK0,
  UNCERTAIN1+PREPARED5 보존. 같은 atomic 재전송0
- 기존 assist:16:14:34.957442Z–16:15:11.737115Z, exit0. scalar5개 중4send/4ACK,
  Flow는NO_CHANGE. child 첫 atomic은 fresh 일치로 해소(ACKNone/원전달UNKNOWN), 나머지5send/5ACK
- 이번 원복 합계10 send 경계/9ACK. 논리단계11을11send로 세지 않으며 물리frame 계수도 아니다
- 최종 별도 reply 대조에서 양쪽 원432B와8controls exact. 둘 다 independent/ON/TimerON/constant,
  master30/F32·slave40/F34, 양쪽 Feed10/custom5
- 원image SHA:60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99 /
  c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426
- source ROLLED_BACK, child/assist VERIFIED. source 마지막 action 전달 UNKNOWN 유지

network-none·안전볼륨RO export `evidence-operator-observation-restored-01`은271blob+3record+report1다.
report SHA:cb19f264ddbd27749cde2af299a104ecae6c4baddb1e8dda64103224994c99d2,
source SHA:84b6e2c47a3895d4d81d9464b452308f256a2e9234e43c74853bb51b5533b4bb,
child SHA:83b6ef5269dc2aee252e5bf0603fe1aa902ca71a23d9515de860ebb06bcdf554,
assist SHA:3ba99ab59de6bbf8778d8d7b7160109832f87b61baae9dff57edd035f3059fe0.
종료 stdout(b)의 전체 대조와 보존 capture 참조를 확인했으며 별도 추가 장비 read는 하지 않았다.

새 사실은 앱 화면의 되돌아옴(c), 관측창의 지속 Sync 보고(a), 명시 원복의 exact 결과(b)다.
공장 초기화가 해결책인지는 미검증이다. 사용자의 연속 “잠시만”에 새 전진을 멈췄고 직접 Async용
source/정책 변경도0이다. 제품 누적6회와 원복 수습·앱 조작을 구분한다. 운영 배포·first 앱OFF·
Bar 앱 이전 classic91 복귀·원 서비스 의무는 여전히 별도다.
