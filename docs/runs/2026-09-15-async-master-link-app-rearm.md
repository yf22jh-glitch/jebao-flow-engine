# run: async-master-link-app-rearm

**사용자가 앱에서 master 링크를 해제·재설정한 구간에 slave의 Async 직접reply를 확보했다.
이후 master/async_slave 보고가 유지됐다. 원복·운영 배포는 하지 않았다.**
이는 앱 보조 절차의 이번 관측 결과이지 기존 ACK timeout을 성공으로 바꾸거나 펌웨어 원인을
확정하는 결과가 아니다. [직전 앱 관측·원복](2026-09-15-app-async-toggle-and-owned-restore.md)과
[첫 보류 실행](2026-09-15-product-async-operator-observation.md)을 그대로 보존한다.

## 1. 원복 뒤 기존 결속 절차 재시도

사용자 “일단 원래대러 async 시도해봐”, “그리고 확인해봥”에 따라 정책09b1861 뒤 기존
9860107 이미지·동일 plan·staged9단계·--await-operator-observation으로 새1회를 실행했다.
제품 계열 누적7회이며 앞선6회와 같은 실패를 새 원인 정보로 세지 않는다. direct Async 구현·
공장 초기화는0이다. 직전 source ROLLED_BACK/child VERIFIED와 원432B/8controls exact 뒤
새 attach가 자기 원본을 포착했다. source/test·writer·schema9·40receipt는 전부 불변이다.

- source:wm_operator_observe_9860107_02
- 실행 UTC:2026-09-15T16:20:18.920926Z–16:21:01.962161Z, exit2
- image:e4d32e1adc9f0fffdc197235a30bc000441e8eedf01bc2441a665f2b508044fe
- action0–7 VERIFIED/ACK8, action8 Async UNCERTAIN/ACKNone. 9send 경계/8ACK
- 마지막 ACK와 새 직접reply 확인은 timeout. public sync_slave 비교는 BEFORE이므로 현재값 아님
- RECOVERY_REQUIRED/accepted=false/awaiting_operator_observation=true/automatic_restore=false
- child0/assist0, 동일 control 재전송0, Bar write0, 배포0

network-none·안전볼륨RO `evidence-operator-observation-02`는170blob+source1+report1이다.
source revision79 SHA:8b1aa6e73dad1247857441668d0a97f2a8ae8931059b53c18ca5c847267ce25f,
context SHA:708fc4ce15ab185ac089d9a6cb11b3ab49710807e17a7d32e05252715bcd07de,
report SHA:f9640cad2652b5e131e6dab90bb630d863f13b1e8f1945b983f0c357fccea7be,
stdout SHA:ef33a777e1f5d6158529f21682008a7c7fc8179369704b1bff59b4b19a5e0c6f,
stderr SHA:cd8c25e7e2f21c657138e09aff2498c7649ad2cfbc77f46d9f865c3fa4e3bef0.
이 단계의 ACK 미확인은 후속 앱 관측으로 소급 변경하지 않는다.

## 2. 앱 master 링크 해제·재설정 관측

새 read-only 두pair에서 master/Sync 보고를 확인한 뒤 해당 collector가 종료됐다.
사용자 “자이제 모니터링 준비해 준비되면 내가 눌러 볼께”에 따라 별도 collector를 실행해
전환 전 master/Sync를 보존하고 준비됐음을 알렸다. 사용자는 “되었다 마스터를 해제했다가
다시 키면 async 변경 완료”라고 보고했다(c). 에이전트는 이 구간에서 제어·원복0이다.

`master-link-app-observation-01`은 두 Pro fresh session read, 보고 수용, pair 완료 뒤10초 대기다.
판정 조건으로 값을 고정하지 않으며 모든 원본 wire/status와 action·관측시각을 보존한다.
첫 적격 보고가 읽기를 만족시킬 수 있으므로 action4만 선택된 창을 reply 불가능으로 해석하지 않는다.

| 관측 단계 | master | slave |
|---|---|---|
| ordinal1–4,16:22:27–16:23:02Z | master, 직접reply3 | sync_slave, 보고4 |
| ordinal5,16:23:13Z | independent, 직접reply3 | **async_slave, 직접reply3** |
| ordinal6–11,16:23:24–16:24:21Z | master, 직접reply3 | async_slave, 보고4 |

ordinal5 원본을 archive에서 decode_frame으로 다시 읽어 action+status 일치를 확인하고 schema로
controls를 재해석했다. master 직접reply 관측시각16:23:13.815234Z, slave16:23:13.884981Z다.
둘 다 SwitchON/TimerON true이며, 이 표본에서 전원OFF가 아닌 역할 변화가 보인다.
슬레이브 Async가 직접reply로 관측됐다는 점은 이전의 보고만 있던 해제 구간보다 강한 새 증거다.
master 재설정 뒤6개 표본에서도 Async가 보고됐다. 설정된 두432B image SHA는 전체11pair에서
각각612f0aa8b3f6292ff9395ef2b0cbaa00d61c38de893a81f0fab9b10404a1fee1과
e1caad069c02015d3db883887442592989d09121f8f455a3709b5dc76423edcf로 불변이다.

- UTC 수집:16:22:26.855588Z–16:24:21.972947Z, collector 종료16:24:31.980852Z/exit0
- 11pair/22frame:master reply11, slave reply1/report10; slave Sync4/Async7
- private archive69파일=raw44+sample22+plan1+complete1+STOP1. raw44개 digest/프레임/control 재대조
- archive SHA:e0051f5ea0bd132e96e6de63798e3272810eea13ea97433fe588399da35b7967
- stdout SHA:58e5d9902f24148608d79ed68ea49d22cbfc8260b21436cf8023bf78322fda80
- pivotal master wire SHA:93ad77aeab38d942f8a4a8fae2ec4a4d6b6f1eacf5dc090e6fc9d88251455981
- pivotal slave wire SHA:812e4b414fee18d2a83f2b7b70bc067ed7845fc376a871d721479e5515edae0e
- identity SHA master:4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea
- identity SHA slave:97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c

## 3. 확정 범위와 남는 의무

이번 앱 순서에서 independent/async_slave 직접reply pair와 재설정 뒤 master/async_slave 보고를
확보했다(a). 사용자의 화면 관찰(c)과 일치한다. 그러나 버튼의 정확한 시각·앱 명령 wire·장비
내부 전이 시점은 보존하지 못했다. 앞선 Async 명령의 지연 적용인지, master 해제의 영향인지,
왜 ACK가 없었는지는 분리하지 못한다. 같은 절차의 일반적 필요·충분조건도 증명하지 않는다.
물리 유량·위상 동작·생물 안전을 판정하지 않으며, Auto/장비시각은 고정 검증 대상이 아니다.

소유 source의 UNCERTAIN·ACKNone·전달UNKNOWN·보류 phase는 그대로다. 관측 결과를 source
VERIFIED나 신규 qualification으로 승격하지 않았다. 사용자 명시 원복 지시가 없어 자기 원복0,
시험 시간표와 마지막 master/Async 상태를 남겼다. 관측 프로세스만 종료했으며 추가 자동 명령은
없다. 보류에 의한 전역 제어 차단, first 최종 앱OFF·Bar 앱이전값·원서비스·배포 의무는 별도다.
