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

## 덧붙임 (2026-09-21) — raw wire frame 앵커 추가, 두 조건 모두 해소

위에 「아직 남은 것」으로 적어둔 두 가지를 처리했다.

### 1. 전용 collector image

`verify_collector_source_tree()`는 `git`, 지정 commit과 일치하는 HEAD, `--untracked-files=all`
기준으로 완전히 clean한 tree, 그리고 **`jebao_flow`가 site-packages가 아니라 그 tree에서
import될 것**, `sys.dont_write_bytecode is True`를 요구한다.

그래서 패키지를 설치하지 않는 image를 따로 만들었다. 의존성 세 개(`aiomqtt`, `pydantic`,
`PyYAML`)만 설치하고, repo는 git bundle로 옮겨 `/repo`에 clone한 뒤 해당 commit으로 detach
checkout하며, `PYTHONPATH=/repo/src`와 `PYTHONDONTWRITEBYTECODE=1`로 실행한다.

- image: `jebao-readonly-collector:f39b207`
  (`sha256:073cf6187f4bf015f220ca7efd90332a6f7d6c2782430d84ff7792313f97e91e`)
- 고정 commit: `f39b20714eea1d092b9a1885cb55eecab3e7b7ff`
- image 안에서 `verify_collector_source_tree()` 직접 호출이 통과함을 먼저 확인했다
  (`commit=f39b20714eea`, `runtime_source_digest=45d9e2873f98dbe6…`,
  module path `/repo/src/jebao_flow/read_only_collector_cli.py`, `dont_write_bytecode=True`).

collector는 추가로 `runtime.dry_run is True`와 **모든 장비의
`control.allow_hardware_writes is False`**를 요구한다(`runtime_not_dry_run`,
`hardware_writes_not_fully_locked`). 배포 설정은 데몬용이라 둘 다 반대이므로, 그 두 항목만
잠근 파생 설정을 전용 volume에 `0600`으로 만들어 쓴다. 나머지 항목은 그대로 두었고 내용은
어디에도 출력하지 않았으며 저장소로 옮기지 않았다. 파생본 SHA-256
`d030c7317cc1b4518468219fce111bde32cc1fe70a1f7f3aac51410d6dc0d690`.

### 2. 저빈도 앵커 — 조사 대상을 건드리지 않는 간격

collector는 표본마다 장비 세션을 새로 연다. 지금 조사 대상이 링크/상태 불안정이므로 고빈도
세션은 현상 자체를 바꿀 수 있다. 그래서 **5분 간격**으로 고정했다. 데몬 observer가 이미 약
15초마다 읽고 있으므로 이 추가분은 장비당 하루 288세션, 기존 대비 수 %다.

스모크(2표본 / 60초)로 먼저 확인했다: `accepted_pair_count=2`, `rejected=0`,
`read_failure=0`, `action_counts {"0x03": 4, "0x04": 0}`. 두 Pro가 현재 `independent`라
명시 응답만 왔다. `reply_or_report` 정책이라 결속된 slave의 act=4도 받는다.

- artifact: `/out/anchor`의 series `JFS-7974c178ad369a750f63417429e19f26`
  (volume `jebao-raw-anchor-20260921`)
- 시작 `2026-09-21T01:40Z`, 864표본 × 300초 = 72시간
- 표본마다 장비별 **`raw.frame` 462바이트** = 정확한 Gizwits wire frame + `sample.json`
- transport policy `reply_or_report`, collector commit 고정

### 현재 가동 중인 관측 둘

| | 대상 | 간격 | 장비 세션 | 남기는 것 |
|---|---|---|---|---|
| `pro-pair-full-state-recorder-20260920.01` | 6장비 98토픽 | 변화 즉시 | **0** | 디코드 전량의 모든 값 변화 |
| `JFS-7974c178ad369a750f63417429e19f26` | Pro 2대 | 5분 | 2/표본 | **462바이트 raw wire frame** |

- **이 덧붙임으로 새로 확정된 사실:** 저장소의 write-free collector를 배포 환경에서 실제로
  돌릴 수 있고, 그 전제(clean tree attestation, dry-run·write-lock 설정)를 만족시키는 방법이
  확정됐다. raw wire frame 보존이 다시 가능하다.
- 확정하지 못한 것: 2026-09-18T11:36:31Z의 방아쇠. 다음 발생 때 두 관측을 대조해 판정한다.

## 덧붙임 (2026-09-21) — 수동 전체 패킷 캡처

위 두 관측으로도 `2026-09-18T11:36:31Z`의 방아쇠는 못 잡는다. raw 앵커는 5분 간격이고 그
사이에 일어난 전이는 남지 않는다. 간격을 낮춘 이유는 collector가 표본마다 장비 세션을 열기
때문이었는데, **수동 캡처는 세션을 열지 않으므로 그 트레이드오프 자체가 없다.** 해상도를
포기할 이유가 처음부터 없었다.

- 대상 인터페이스 `enp1s0`(데몬이 host 네트워크 모드라 전 트래픽이 이 위로 지나간다)
- 필터 `port 12416 or port 12414`, 전체 스냅(`-s 0`), 패킷 단위 flush(`-U`)
- 링버퍼 100 MB × 15 = 최대 1.5 GB. 실측 약 110 MB/일이므로 **약 2주치 연속 이력**
- 저장 위치는 owner-only(0700) 전용 volume `jebao-pcap-20260921`
- 장비 세션 0, publish 0, 쓰기 0 (완전 수동)

75초 표본에서 확인한 내용:

| | |
|---|---|
| UDP 12414 | 8바이트 탐색 요청 / 127바이트 응답, 72 패킷 |
| TCP 12416 | 343 패킷 |
| 그중 length 462 | 19건 = 452바이트 상태 + 10바이트 Gizwits 헤더. collector가 남기는 `raw.frame` 462바이트와 같은 것 |
| 그 외 | 411·404·412(시간표 계열), 20·18(제어·ACK), 9, 0(순수 ACK) |

즉 **데몬이 주고받는 모든 프레임이 전속도로 남는다.** 15초 주기 polling 사이에 장비가 밀어
올리는 보고 프레임까지 포함되며, 이는 MQTT projection에도 5분 앵커에도 남지 않던 것이다.

### 보안 — 인증 프레임 보존

전체 pcap은 인증 프레임을 그대로 담는다. 저장소의 read-only collector는
*"Authentication frames are never retained"*를 명시하므로 원본 pcap은 그 선을 넘는다.
적용한 가드레일은 다음과 같다.

- pcap은 owner-only volume에만 두고 링버퍼로 크기를 묶는다
- 어떤 내용도 출력하지 않고 저장소로 옮기지 않으며 커밋하지 않는다
- 분석 결과를 보고할 때는 저장소의 `jebao-flow-observe ingest-pcap` 경로를 쓴다.
  이 경로는 인터페이스를 열지 않고, 이미 캡처된 pcap을 private evidence root로 들여오며,
  이벤트에 endpoint 주소를 남기지 않고 flow id를 할당 순서 기반 불투명 토큰으로 쓴다
- 이 문서의 모든 주소는 마스킹했다

### 현재 가동 중인 관측 셋

| | 대상 | 해상도 | 장비 세션 |
|---|---|---|---|
| `pro-pair-full-state-recorder-20260920.01` | 6장비 98토픽 | 값 변화 즉시 | 0 |
| `JFS-7974c178…` | Pro 2대 | 5분 앵커, 462바이트 raw frame | 2/표본 |
| `jebao-pcap-20260921` | 전 장비 전 프레임 | **전속도, 약 2주 링** | **0** |

- **이 덧붙임으로 새로 확정된 사실:** 데몬과 장비 사이의 모든 프레임이 손실 없이 보존된다.
- 확정하지 못한 것: `2026-09-18T11:36:31Z`의 방아쇠. **그 시점의 자료는 존재하지 않으므로
  소급해서 확정할 수 없다.** 재발 시 pcap으로 프레임 단위 판정한다.
