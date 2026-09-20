# run: 2026-09-20-full-state-recorder

- 목적(한 문장): 결속된 Async slave가 스스로 `TimerON`을 끈 방아쇠를 다음 발생 때 특정할 수
  있도록, 장비에 쓰지 않고 local-control 표면 전량을 손실 없이 기록한다.
- 이 실행이 답하려던 질문: 2026-09-18T11:36:31Z의 방아쇠가 기존 관측에 안 남은 이유가
  무엇이고, 무엇을 남기면 다음 발생이 특정 가능한가?
- 실행한 commit SHA: n/a (저장소 `src/` 변경 없음). recorder는 기존 collector와 같은 관례로
  저장소 밖 스크립트다.
- `hardware_write=false`, `mqtt_publish_count=0`, `device_session_count=0`

## 왜 기존 관측으로는 못 찾는가

`daily-three-pump-monitor-20260918-01`(30초, 5853 표본)에서 11:36:31Z에 바뀐 항목은
`schedule_enable` 하나뿐이다. fault `[]`, 역할 `async_slave` 유지, 시계 정상, 상단 무영향,
두 장비 모두 slot33 `pulse/40/80` 중간이며 경계가 아니다. 저널상 10:48:22Z~12:45:15Z에
이 소프트웨어의 오퍼레이션은 0건이다.

그 뒤가 더 중요하다. 11:38:31Z에 하단이 **스스로 `Linkage=master`를 보고**했고(상단도 master),
30초 뒤 `async_slave`로 돌아왔다. 그날 로컬 자정에는 두 Pro 모두 날짜 롤오버에 실패해
skew가 86434초가 됐는데, **상단은 7표본(약 3.5분) 만에 회복했고 하단은 1184표본(약 10시간)
동안 회복하지 못했다.** 물리 전원 재투입으로만 풀렸다.

방아쇠가 안 남은 이유는 collector가 버렸기 때문이다. 데몬은 장비당 **16개 function 토픽**을
publish하는데 기존 collector는 그중 6개만 구독하고, 그것도 파생 요약만 저장한다. **펌프를
실제로 세우는 manual 블록(`manual_mode`/`manual_flow`/`manual_frequency`)은 구독조차 하지
않는다.** 30초 표본 사이의 값 변화는 전부 사라진다.

## 시작한 관측

- artifact: `pro-pair-full-state-recorder-20260920.01`
  (`/data/pro-pair-full-state-recorder-20260920-01`)
- recorder SHA-256: `dd381b8b7f4c816901804091a068c84eabdd7f8b217552670c1310e8105549e7`
- 시작 `2026-09-20T11:34:59Z`, 예정 종료 `2026-09-23T11:34:59Z` (72시간)
- 구독: `jebao-flow/main/local/availability`,
  `.../local/devices/+/availability`, `.../local/devices/+/functions/#` (와일드카드)
- 기록 방식
  - **변화 이벤트 로그**: 토픽 값이 실제로 바뀔 때마다 `previous`/`current` 전문을 1건씩
    durable(O_EXCL+fsync)로 남긴다. 데몬이 매 주기 갱신하는 `observed_at`은 비교에서 제외해
    "이벤트 1건 = 실제 변화 1건"이 되게 했다. 30초마다 flush.
  - **전체 스냅샷**: 5분마다 모든 토픽의 최신 전문을 한 파일에 남긴다(앵커).
- 스모크 2회로 확인: 6장비 **98개 토픽**, 두 Pro 각 16 function(manual 블록 포함), 최초
  수신 이후 실제 변화는 27초당 5건 수준. `complete.json`의 publish·session 카운트 0.

이 표면이 담는 값은 `SwitchON`, `TimerON`, `Linkage`, manual `Mode`/`Flow`/`Frequency`/
`FeedTime`/`Cust_Wav_Freq`, 7개 fault 비트 전부, `Auto*` 블록, 시간표 entry와 digest,
장비 로컬 시각이다. 452바이트 raw가 디코드되는 항목과 사실상 같다.

- 증거 등급: (b) preserved structured·durable daemon artifact. 원본 wire frame은 아니다.

## 아직 남은 것 — raw wire frame

저장소의 `src/jebao_flow/read_only_collector.py`는 write-free로 **정확한 Gizwits wire
frame**을 보존하며, `CaptureTransportPolicy.REPLY_OR_REPORT`가 act=3·act=4를 모두 받으므로
결속된 slave도 읽을 수 있다. 2026-09-16 `Er03`을 byte 451로 특정한 것이 이 계열이다.

지금 바로 붙이지 못한 이유는 두 가지다.
1. `verify_collector_source_tree()`가 clean git tree를 요구하는데 런타임 image에 `git`이
   없고 서버에 체크아웃도 없다. 전용 collector image가 필요하다.
2. 이 collector는 표본마다 장비 세션을 새로 연다. 지금 조사 대상이 링크/상태 불안정이므로
   고빈도 세션은 현상 자체를 바꿀 수 있다. 붙일 때는 5분 앵커 정도의 저빈도로 한다.

- **이 실행으로 새로 확정된 사실:** 데몬이 publish하는 16개 function 중 6개만 기록되고
  있었고, manual 블록은 전혀 기록되지 않았다. 지금은 98개 토픽의 모든 값 변화가 남는다.
- 확정하지 못한 것: 11:36:31Z의 방아쇠. 다음 발생 때 이 기록으로 판정한다.
