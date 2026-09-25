# run: 2026-09-25-pro-linked-flow40-daily-apply

## 목적·범위

사용자가 승인한 최신 일일 표의 일반 운전 하단 목표 `Flow=40`을 현재
Pro Master/Async 쌍에 단회 적용했다. Feeding에는 Flow를 쓰지 않는다.
상단·하단 시간표 이미지, Bar 및 리턴 펌프는 변경 대상이 아니다.
사용자는 앞서 현장 감시와 IoT 전원 차단 수단을 알렸고, 이번 적용을 지시했다.
이 기록은 **단회 적용**이며 반복 일일 송신기의 배포·활성화를 뜻하지 않는다.

- source commit: `9841f50`
- 운영 컨테이너 image는 교체하지 않았다. 직전 검증된 local-control source를
  `python -B -`로 실행하고 기존 identity·journal·lease·writer를 사용했다.
- source schema SHA-256: `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`
- UTC operation span: 2026-09-25 01:17:54–01:24:22
- 물리 binding SHA-256: 상단
  `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
  하단 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`

## 전송과 중간 중단

사전 read-only preview는 상단 Master·하단 Async, 하단 수동 Flow45에서
40으로의 5단계 하향과 계획된 `release → flow → relink`를 확인했고 장비 write는 0회였다.
첫 child `release`가 1회 송신·ACK·VERIFIED된 직후 하단 child는 물리 lock 충돌
(`LocalControlLeaseBusyError`)로 **저널 생성 전** 중단됐다. 그 시점 fresh 조회는
상단 independent, 하단 Async·Flow45, 전원 ON·Timer 상태 유지·fault 0이었다.

실패한 전체 명령을 재실행하지 않았다. `flow` child가 부재임을 확인하고,
잠금 충돌로 저널이 생성되지 않은 경우에만 같은 ID의 첫 시도를 짧게 기다렸다.
Flow40 1회 송신·ACK·VERIFIED 뒤, `relink` child도 별도로 1회 송신·ACK·VERIFIED했다.
따라서 이번 operation의 물리 송신은 **release 1 + flow 1 + relink 1 = 3회**이고,
이미 전송한 child forward 재송신은 0회다.

| child | terminal | durable record SHA-256 |
|---|---|---|
| `release` | VERIFIED, ACK | `84fdf478633ca50c5867be9479c3f012ea49f7e3f026791b05f86471e89f179f` |
| `flow` | VERIFIED, ACK | `4528b68fc7a13ac09dfd4237c0ca49f13b00785253338c9c6c96f4565635949e` |
| `relink` | VERIFIED, ACK | `17c0b51852f670c9c3f0530877a446c30ba5d5203b44a2bfed382e84ef689d42` |

세 record는 운영 저널에서 다시 load해 각 digest·ACK·VERIFIED를 대조했다.
미완료 pair record는 0개다. 이 저널 판정은 증거 등급 (b)이고 raw 프레임 자체의
보존을 주장하지 않는다.

## 종료 상태

사후 fresh 조회에서 상단은 직접 reply action 3·Master·전원 ON·TimerON·fault 0,
하단은 결속 시 report action 4·Async·수동 Flow40·전원 ON·TimerOFF·fault 0이었다.
하단 direct-only read는 결속 장비의 보고 형태와 맞지 않아 timeout됐고,
report 허용 read로 재확인했다. 이 추가 조회들은 장비 write 0회다.

시간표 image SHA-256은 중간·종료 조회에서 상단
`118c23c58189d18b48c8bd8445590ad41b69140132f852949615949052d721c9`,
하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`로
같았다. 사후 fresh 조회의 raw 원본을 별도 파일로 보존하지 않았으므로, 이 상태
설명은 기록된 조회 결과에 대한 등급 (c)이며 장기 물리 출력의 증명이 아니다.

## 이 실행으로 새로 확정된 사실

1. 하단 수동 Flow45→40을 검증된 3-child 경로로 한 번 적용했고 최종 Master/Async
   역할과 두 image digest를 유지했다.
2. 운영 read lock과 일회성 control lease가 충돌하면 **첫 child 뒤 다음 child의 저널
   생성 전에** 멈출 수 있다. 이는 실제로 발생했으며, 반복 일정의 지속 실행 전
   중간 중단 처리가 필요하다.
3. 일일 반복 송신기는 아직 운영에 설치·활성화되지 않았다. 이번 결과로 Feeding
   경계 또는 장기 자동 실행을 검증했다고 주장하지 않는다.

## 추가 — 2026-09-25 01:35 UTC 읽기 전용 일정 준비

실기 뒤 관측한 잠금 충돌에 대해서는 별도 commit `48be597`에서 저널 생성 전
충돌만 bounded 대기하고 pair를 재검사하도록 수정했다. 관련 99개 회귀가 통과했고
프로토콜 스키마 digest는 변하지 않았다. 이 새 소스의 image
`sha256:c785579a321d778bd14c83aaaf3a5c93dfa2c2f27837009dbea608893b911bef`를
별도로 빌드했지만 기존 운영 제어 컨테이너는 재시작·교체하지 않았다.

31행 JSON 파일 SHA-256은
`9d6418bb83d0d72b55c502714a6cc8c321acd34ec0ba8077c02e02a2a20b850c`이고,
운영 데이터 볼륨에 올린 사본과 일치했다. 새 image의 one-shot preview는 현재
`10:33 / Flow40`, 다음 경계 `11:40`, 장비 write 0을 출력했다.
별도 `jebao-linked-flow-schedule-preview-20260925` 컨테이너는
**network none·데이터 볼륨 read-only·write_enabled false**로 기동했다.
따라서 이 컨테이너는 장비 명령을 보낼 수 없고 live 일정은 아직 활성화되지 않았다.

10:33 Feeding 복귀 뒤 하단의 report 허용 조회는 Async·Flow40·TimerOFF·
전원 ON·fault 0이었다. 이 조회 raw를 별도 파일로 남기지 않았으므로 등급 (c)다.
반복 자동 write의 현장 감시 종료 시각은 확인되지 않아, 지속 송신을 켜지 않았다.
