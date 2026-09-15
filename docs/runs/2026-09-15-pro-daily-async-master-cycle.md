# run: pro-daily-async-master-cycle

**사용자가 요청한 Pro 두 대의 16구간 시간표를 실제 적용했다. 위가 master, 아래가 Async다.**
마지막 Async ACK는 여전히 미확인이었지만 별도 master 링크 해제·재설정 두 명령이 성공했고,
해제 중 slave Async 직접reply, 재설정 뒤 역할·전체 시간표 일치를 확보했다. 원 Async 전달은
UNKNOWN이다. 현재 시간표를 유지하며 야간 read-only collector를 실행했다. Bar write는0이다.

## 범위·입력

사용자는 master 링크 재설정 옵션, Pro 두 대만의 일일 시간표, 실제 반영과 밤사이 로그를
명시했다. 위/아래를 확인했고 선택 가능한 15시 파형은 Cross-flow(`circulation`)로 정했다.
[직전 자기 원복](2026-09-15-before-daily-pro-owned-restored.md) 뒤 새 역할 배치의 independent
baseline을 포착했다. [앱 절차](2026-09-15-async-master-link-app-rearm.md)는 관측 근거이며
앱의 내부 명령과 이번 코드가 같다고 주장하지 않는다. 제품 질문 계열 누적8회다.

- 정책0fb1fee, 구현fdcddc0; [구현·전체 시험의 한계](../wavemaker-master-cycle.md)
- image:78c6b712a1186229ec74148a6bb458a85f33118c519608bc48d606d7b2f06fd5
- 기반 기존 이미지의 id를 대조한 뒤 network-none·git archive source-only build
- 이미지 안 lifecycle SHA:b807e0d2761942351a86470258908a98bf2dd78b959cc55349dbd1d9cdabdf5d
- 이미지 안 cycle SHA:766b8c79a56c86df437706d388d25e727b5a7be25cc417fab1a720701682122f
- schema:81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1 (writer/9입력 불변)
- 기존40receipt 파일 SHA:4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031
- private plan SHA:6b6376fefa8ee4543ee45f05938ae3dc3ccc443111a640ba1aaa1a3f74c0c712
- 위/master identity SHA:97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c
- 아래/slave identity SHA:4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea

실제 identity는 private 입력에서만 대조했다. 예시에는 [upper/lower 논리 이름](../../config/examples/pro-pair-daily-async.json)만 있다.
16슬롯은 00:00–24:00을 덮으며 위 Flow30..45, 아래30..35, 나머지32슬롯EE다.
장비별 설정 limits 및 현재9종 자격을 첫 전송 전 대조했고 자격을 재발급/수정하지 않았다.

## 전송·최종 결과

source `wm_pro_daily_fdcddc0_01`, UTC16:53:24.363992Z–16:54:18.308978Z, CLI exit0.
staged9는 앞8 ACK/VERIFIED, 마지막 Async send 뒤 ACK timeout·직접reply 확인 실패로
UNCERTAIN이다. 별도 cycle2는 각각 ACK/after VERIFIED로 끝났다.
**총 send 경계11/ACK10**, source9+cycle2. Async 역할 payload 재전송0, child/assist0이다.

| 단계 | 위/master | 아래/slave | 전체 432B |
|---|---|---|---|
| master 해제 후 |16:54:10.227553Z independent/reply3|16:54:13.778210Z async_slave/reply3|양쪽 일치|
| master 재설정 후 |16:54:17.674440Z master/reply3|16:54:18.211909Z async_slave/report4|양쪽 일치|

표의 시각은 capture completed_at이며 물리 전이 시각이 아니다. 재설정 후에는 설계대로 보고를
수용한다. 해제 중 직접reply와 재설정 후 보고를 같은 증거 등급으로 합치지 않는다.
두 pair의 원 wire를 decode_frame으로 읽어 action+status를 재대조했다.

- 위 image SHA:c8f9f83083b7cd152d72c5826331bf8035cd58149d4be9ea93adba66324e00e7
- 아래 image SHA:d47a45a3985443ec3af0706e19b85284925ee74fa8da829d77a0c644b17186b1
- 결정적 slave 직접reply wire SHA:04bb2c35f5892c20802ee8ff2301daf4cb6226f4db99b86af6ad5f52b7e095f8
- 재설정 후 slave 보고 wire SHA:2c3a3ad43bc229007940cbb729116696221ee5583d5f16bfde2902d8d11feb12

cycle 결과 accepted/current_state_verified=true, phase VERIFIED. source는 revision79/
RECOVERY_REQUIRED, 마지막 action UNCERTAIN/ACKNone/기존 진단 그대로다.
cycle context에 보존한 source snapshot과 source 현재 bytes를 대조하는 시험도 있다.
실제 원 전달이 늦게 반영됐는지 master 해제가 전이를 유발했는지 등 인과는 미분리다.
이번 master 배치·시간표·cycle이 함께 바뀌었으므로 어느 하나만의 원인 규명도 아니다.

## 별도 야간 로그

UTC16:54:48.683238Z에 24시간·30초 간격의 Pro-only read-only collector를 시작했다.
스크립트는 private 영속 볼륨에 설치했다(SHA:f0c274ba7cd257631301cdcc197351f1cebafba5dd65b3636ec1cbd3d5237a76).
opaque artifact `pro-daily-async-20260916-01/overnight-01`에 각 표본 JSON·wire·status가 fsync로
저장된다. device time, 역할, AutoMode/AutoFlow/AutoFreq, 설정 image SHA, 해당 시각의 선언 슬롯,
reply/report 구분, 읽기 오류가 남는다. 제어 명령·불일치 보정·종료시 자동 원복은 없다.

초기2pair 모두 원 image 일치·ON/TimerON/역할 일치였다. 장비시각01:54–01:55의 야간 슬롯에서
**위 Sine AutoFlow35/AutoFreq70, 아래 Sine AutoFlow30/AutoFreq70**이 직접 관측됐다.
위는 reply3, 아래는 report4다. 현재 구간의 일치이지 아직 지나지 않은 08시 경계나 하루 전체
검증은 아니다. 수동 Flow/Mode가 Auto와 달라도 시간표 실패로 판정하지 않는다.
서버 관측 UTC와 장비 시각을 모두 남기므로 아침 분석은 장비 시각과 선택된 프레임 출처로 한다.
물리 유량·위상·생물 안전을 측정했다고 주장하지 않는다. 기록 실패/수집 공백도 성공으로 메우지 않는다.

## 보존·남는 구분

private `evidence-daily-01`:196blob+source1+cycle1+report1=199파일, 모든 blob SHA 대조.

- report SHA:754d8ca197fdc82ddbebba6c4c2413c52d6cad50dc5f3baa3ac6d64ba610bf87
- archive SHA:1ce2b781f57f3fbd0f0e690aabeafe39933cc88f3d7f600d6d6a897d57f1cde5
- source SHA:e149e60d71e170abbd24124d1566c3c29ecc9693524995b94196bffe6a20ab65
- cycle revision15 SHA:89a7ca1aeb0a42c8388386f30ab4fb81542fe1c5b924876cad0da64ea7c5f17c
- CLI stdout+상태 SHA:4e4e02b1799b94ce8e784b72accf9eaf325bb81f7ef1f6e0b384dfc04746b6a0
- CLI stderr SHA:c994c179d8937979e85493e22b2724b5f849ac4184f0940d178ab3638655ffe3
- 초기2pair 로그+상태 SHA:9ab6f6e6d7da8cd33f35c25758d38b832582d90ea4333f38aa5a384dbd1fb9da
- 야간 초기 archive SHA:7ba38eee87a795db23ff16f6d3d05a74d60f9295533ba46286603628dcb0c5c9

야간 archive는 진행 중 복사한 부분 산출물이다. 그중 이미 완료된 초기2pair의 4개 표본만
wire/status digest·decode_frame·controls를 독립 재대조했다. 하루 전체 또는 최신 끝까지의
완결 archive로 인용하지 않는다. 이후 로그는 영속 볼륨에 계속 추가된다.

새로 확정된 사실은 **옵션의 두 master 명령 후 Async 상태·새 전체 image 및 현재 야간 Flow의
일치**다. 이번 Pro 시간표 실반영은 완료이며 사용자 요청대로 유지한다. 원 Async ACK 부재와
source 미완료에 따른 일반 전역 write 차단은 그대로다. 현재 운영을 되돌리는 명령은 자기
`recover-wavemaker --request-id wm_pro_daily_fdcddc0_01`이며 명시 요청 없이 실행하지 않는다.
Bar write0, 기존 운영 inputs3개 불변, 중단된 옛 서비스는 재기동/교체하지 않았다. Pro 장비 설정
적용과 HA/MQTT 서비스 generation 배포는 다른 결과다. 기존 앱/서비스 의무도 완료로 대체하지 않는다.
야간 관측은 진행 중이며 아침 검토 결과는 이 기록에 덧붙이거나 별도 기록으로 남긴다.
