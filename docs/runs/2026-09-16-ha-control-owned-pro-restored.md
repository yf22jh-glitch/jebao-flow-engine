# HA 제어 활성화 전 Pro 일일 운전의 소유 원복

## 질문·승인

사용자는 HA 실제 조작을 요청했고, 현재 Async 유지와 미완료 source 해소가 기존 코드에서
양립하지 않는다는 설명을 받았다. 현재 운전 종료·연동 전 저장 원본 복원 및 현장 감시·중단
수단의 필요를 알린 질문에 진행을 승인하면서, 이후 Async와 시간표 재적용을 요청했다.
정책은 `750ded2`다. 질문은 **해당 source의 자기 원본을 복원하고 미완료 회계를 닫을 수 있는가**다.
이 실행은 새 Async 결속·기능 자격 발급·HA 제어 배포가 아니다.

기존 16슬롯 일일 계획, 역할 배치, source 원본과 입력 파일을 별도로 보존했다.
plan SHA `6b6376fefa8ee4543ee45f05938ae3dc3ccc443111a640ba1aaa1a3f74c0c712`.
이후 재적용 성공을 미리 약속하거나 원 Async ACK 부재를 성공으로 바꾸지 않는다.

## 실행

- 기존 fdcddc0 image `78c6b712a1186229ec74148a6bb458a85f33118c519608bc48d606d7b2f06fd5`와
  context에 결속된 별도 CONTROL 입력을 사용했다. 새 observer 설정은 바꾸지 않았다.
- 기존 qualification 파일 SHA는 `4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031`.
  recover는 소유 context를 사용하며 qualification을 요구하지 않는다. 별도 assist는 이 기존
  이미지/schema에 유효한 자격을 사용한다. 새 schema에서 옛 자격을 쓴 것이 아니다.
- observer는 UTC `07:14:50.207657Z`, collector02는 지원된 STOP으로 `07:14:52.169072Z`에
  각각 exit0으로 종료했다. 기존 raw·로그·container를 삭제하지 않았다. 이후 수집 공백이 있다.
- source `wm_pro_daily_fdcddc0_01`의 recover 한 번: UTC `07:15:30.982100Z–07:15:43.232060Z`, exit2.
  첫 slave atomic send의 ACK timeout으로 action0 UNCERTAIN, 나머지5 PREPARED였다.
- 기존 own-assist: `07:16:20.627814Z–07:16:36.846362Z`, exit2. master independent는 ACK/VERIFIED,
  slave action1은 전송 전 `connect_and_capture` timeout으로 PREPARED였다.
- 같은 assist id/보존 payload에서 미전송 단계만 한 번 재개했다.
  `07:17:30.727470Z–07:18:00.754535Z`, exit0. VERIFIED master는 건너뛰었고 원 atomic은 재전송하지 않았다.
- assist는 ACK4/NO_CHANGE1, detach는 원 atomic의 fresh 일치 해소와 나머지5 ACK다.
  이번 복구의 send 경계10/ACK9다. 준비된 action 수를 물리 전송 수로 세지 않는다.

CLI가 별도 daily install manifest를 재생성하므로 그 입력 볼륨은 RW로 사용했다. 직전 manifest는
immutable backup에 보존했고 기존 production config/qualification이나 shared guard는 수정하지 않았다.
공장 초기화·generic recover·새 전진 id·Bar/리턴/도징 write·제품 source 변경은0이다.

## 결과와 증거

최종 두 wire를 다시 decode하여 action3와 status bytes의 일치를 확인하고, source 최초 원본의
각432B 및 SwitchON/TimerON/Linkage/Mode/Flow/Frequency/FeedTime/Cust_Wav_Freq를 재대조했다.
시각·Auto 출력·활성 슬롯은 과거 일치 판정에 쓰지 않았다.

| 역할 | 직접 응답 UTC | 결과 |
|---|---|---|
| 위 Pro | 07:17:59.862746 | 원432B와8controls exact; independent/ON/TimerON, constant40/F34 |
| 아래 Pro | 07:18:00.643971 | 원432B와8controls exact; independent/ON/TimerON, constant30/F32 |

둘 다 FeedTime10/custom5다. 이는 원복된 저장 제어값이며 현재 Auto 출력이나 물리 유량은 아니다.
source는 ROLLED_BACK/rev80, detach VERIFIED/rev33, assist VERIFIED/rev26이며,
원 Async ACK None/전달 UNKNOWN은 보존됐다. 운영 journal의 미완료 레코드는0이다.

- source SHA `732a5b2663364a0ba8abc1b464c01999ebe55d5197ca7760c46bb26c90e72698`
- detach SHA `c1b274cca981af3843e215276a23d9bb4beebbad2eafe590aea4484cf3b33ac8`
- assist SHA `903b0e27806f489a1b4f144a1c229e162e2694b52cefaefb2a008cbd1b723238`
- 보고서 SHA `dd1888bc41ce68b5ce72675ea33f157fd042e4f978384ec578be1fe7f2171076`
- opaque artifact `control-activation-20260916.Acq8Dn`, archive SHA
  `9516fa6157f2816162e9499bd870c12b1c5641c9cfe4d64fac45414a5ef8b028`
- archive451파일, 전·후 합계440 blob 사본의 내용-address SHA를 전수 대조했다.
  사후 export는271개 고유 blob이며 복사 개수와 혼동하지 않는다.

장비 원 frame은 등급(a), journal/회계는(b)다. 실제 유량·위상·생물 안전의 계측은 아니다.
이 실행으로 **소유 원본 복구 및 전역 미완료 작업0**을 확인했다. 새 schema 자격과 제어 배포는
아직 별도이며, observer/collector는 이 기록 시점에 중지 상태다. 읽기 잠금 후보03은 배포하지 않았다.
이전 앱/서비스 복귀 의무를 이번 원복으로 흡수하지 않는다.
