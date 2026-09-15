# 제품 Async 단계별 활성화 — 누적5회, 보고4/직접reply0와 자기 원복

**Async 수용 실패·자기 원복 완료·운영 배포 미완료다.** 준비, image, 독립 TimerON,
Master, Sync까지8개 action은 ACK/after readback이 일치했다. 마지막 Async는 ACK timeout,
별도 새 연결의 직접reply1회도 timeout이었다. 그 읽기 창에서 실제 action4 보고4개를 보존했다.
보고는 모두 sync_slave였고 reply는0이다. 이는 무수신이 아니라는 증거이지 Async 미적용 증명이 아니다.

앞선 [제품 Sync](2026-09-15-product-sync-detach-ack-timeout.md),
[Async3필드](2026-09-15-product-async-transition-stop.md),
[Async역할단독](2026-09-15-product-async-role-only-stop.md),
[Async연결유지](2026-09-15-product-async-reply-reconciliation.md)를 지우지 않는다.
[옛 하네스 Async](2026-09-15-native-scenario-async.md)는 입력/응답 PASS지만 exit2였고,
그 관측 결과를 제품의 정상 종료나 이번 성공으로 재사용하지 않는다.

## 범위·결속

- 사용자 요청: “야 확인하고 다시 수정 배포해 뭐가 잘 못되었는지 ?? 성공했던 절차대로 진행해봐”.
- 정책 `bc52c58`, 형상 보완 `b9fc68e`, 실행 source `5ac6e1749abaa77499091667c7ac074aad7c81f6`.
- 구현 전체 diff SHA `6e1ee237dd4fa15fb214e44df8ac1d89863fd4aac55a3e2359c27a217830896e`;
  src/tests SHA `4882f39c21ed53982237ecdecebf92ab6f2a6b50bc4ceb625ad1a63f018905e1`.
- image SHA `f972319b83985f060a8bba44152f4e4d09da17ba0403e9e5e7175cdc9aeddb34`;
  설치 source146개를 로컬과 전수 대조했다. image 검증 보고 SHA는
  `89af20331b02b15f6f46dbdae6afd4daafd4a4842adb116502d526075fbf49a3`다.
- schema `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1` 불변.
  새20device/function pairs·40receipts·11bindings에 기존6pairs 포함, ignored0을 실기 전 확인했다.
- qualifications SHA `4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031`;
  plan SHA `f4ecee7f9d93b599e94cebc815a371cd2099b7d207342fc6f2b28e6844265feb`.
- operation `wm_product_async_staged_5ac6e17_01`; 결정적 child/assist id의 공통 suffix는
  `cb2ea17903230aa814850ba6d1f7f86d2cfd7d616049466b92d39bc2c8f64e78`다.
- 신규 `staged_roles_v1`9단계 한 번이다. 추가 여섯 번째 전진·동일 payload 재전송은 포함하지 않는다.

정상 활성화 순서·연결·간격을 [구현 계약](../wavemaker-staged-activation.md)에 맞췄다.
옛 하네스의 sentinel, 날짜/Auto 게이트,10/15분 epoch, ACK손실8회/55초 report 수용은 이식하지 않았다.
옛 시험 시간표·절대시각·전체 조건까지 동일하다고 주장하지 않는다. ACK 후 직접reply1회는 유지했다.
Auto/장비 시각은 고정 expected가 아니며 이번 최초 실패는 값 비교가 아니라 ACK timeout이다.

## 원본과 시간선 — UTC

사전 `baseline-staged-activation-01`: 14:54:58.346133–14:55:03.581968, 2pass×3대 전부 action3.
요약 jsonl SHA `5df5e8fedf728b3ed3e2d7174f00973429a59de94392fb7f822b08b34dd37c77`.
master는 second Pro, slave는 first Pro다. 물리 identity는 해시로 결속하고 실제 식별자는 private에만 보존했다.

| 장비 | 시작/복원 목표 제어값 | 전체 image SHA |
|---|---|---|
| second Pro/master | ON/TimerON/independent/constant/Flow30/F32/Feed10/custom5 | `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99` |
| first Pro/slave | ON/TimerON/independent/constant/Flow40/F34/Feed10/custom5 | `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426` |
| Bar | ON/TimerOFF/independent/classic40/F80/Feed10/FeedSwitch=false/PulseTide=false | `8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea` |

identity SHA: master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`,
slave `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`,
Bar `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.

attach container: 14:56:21.784114248–14:57:12.767279723, exit2.

| action | 전진 | 결과 |
|---|---|---|
| 0/1 | master/slave independent·TimerOFF·constant·Flow30/31·F20 | 각 ACK/after 일치 |
| 2/3 | master/slave 전체432B image | 각 ACK/전체 after 일치 |
| 4/5 | 두 장비 independent·TimerON | 각 ACK/after 일치 |
| 6 | master Linkage 단독 | ACK/after master |
| 7 | slave Linkage sync_slave 단독 | ACK/after sync_slave 보고 |
| 8 | slave Linkage async_slave 단독 | ACK timeout·UNCERTAIN |

action8 forward SHA `c02514f9291162f4b02a76df76188ddcb68d3099b289cdd97e4fd2dc21a3f2c7`는
앞선 역할단독과 같은460B payload다. `send_once` 실패의 timeout 기록 시각은
14:57:01.205432203이며 보고된 경과는 약5.062초다. 그 뒤 별도 새 직접reply 확인의
timeout 기록 시각은14:57:06.443519504이며 경과는 약5.237초다. 두 시각은 송신 시작이 아니다.
아래4프레임은 후자의 읽기 창에서 보존됐고 원ACK 대기 창의 프레임이 아니다.
자동 child 첫 slave atomic도 ACK timeout; 나머지5개 PREPARED.
진행 조회14:57:11.638898은 한 시점 조회이며 연속 감시가 아니다.

## 새 raw 증거 — 실패 확인 창의 보고4개

`after_ack_error_reply_frames`4개 모두 action4·selected=false다. wire/status blob을 해시 검사한 뒤
공개 codec으로 다시 decode했다. 아래 시각은 호스트의 frame 관측 시각이지 장비 상태 생성 시각이 아니다.

| 호스트 관측 UTC | capture SHA | wire 구분 |
|---|---|---|
| 14:57:02.431618 | `a1d0893e65a4c8bdc8917e1a1e0623e0fd2372a0a9f8d4f442a33635f9a55fab` | A |
| 14:57:03.455713 | `e6381fbf374ab68ab5551c1526eb8ad7f49e799ae5ee226d74f5fb99d920e976` | A |
| 14:57:04.499785 | `13b29a66a4c77e8b2694537e2f7f2fd6fcfe3460ecc86151ad46127ee669452b` | B |
| 14:57:05.608850 | `5bca59d5322e171d8d96ac7d4e0fb2226b79d0ff660e984d0ed3d6815078a92d` | B |

wire A SHA `5768c32fa5a81bc451389cfeedc733a02a32d243c13d8f25f23bb103f1fbe805`,
B SHA `8723defb3ccea65d2b8ff64ebef94bb20d35d81c8892aeef2e4f3df5ecb3b7ae`.
4개 관측·2 unique wire이며, 같은 bytes를 제거해서2표본이라고 세지 않는다.
전건 ON/TimerON/sync_slave/sine/Flow31/F30/Feed10/custom5, Auto sine35/F30이다.
slave image는 `e1caad069c02015d3db883887442592989d09121f8f455a3709b5dc76423edcf`다.
간격은 약1.024/1.044/1.109초. 주기 보고인지 중복 전달인지는 이 간격만으로 확정하지 않는다.

전체 observer chain은38frame=master17(reply14/report3)+slave21(reply13/report8)이다.
selected32, 비선택6. `before_async_role_only`의 별도 요약2행은 같은capture 재게시이므로 더하지 않는다.
실패 확인4보고를 빼면 slave 성공 쪽 수집은17frame(reply13/report4)이다.
slave image 후 같은read에서 report를 비선택 보존한 뒤reply를 선택한 사례가 있다.
따라서 report 하나가 섞이면 reply를 못 받는다는 뜻이 아니다. Sync 후 report 허용read는 첫
적격 보고에서 끝나므로, 그 뒤reply가 올 수 없었다는 주장도 하지 않는다.

이번 새 명시reply read의5초 창에서는 report4/reply0을 직접 확인했다. report는skip budget8을
소모하지 않는다. 이 창의 무수신설은 배제되지만 원ACK 대기 중 전체wire를 보존한 것은 아니다.
원 전송 도달, MCU 내부 처리, 보고의 신선도, 왜reply가 안 왔는지는UNKNOWN이다.

## 동일 소유권의 지원·원본 복원

assist container:14:58:22.634824267–14:59:00.907936340, exit0.
5scalar 중 master independent, slave TimerOFF, constant, F20의4개가ACK, Flow31은NO_CHANGE다.
14:58:31.634530의master independent AFTER(action3) 뒤14:58:31.997179 slave BEFORE
(action4)에async_slave가 기록됐다. 그 창에서 지원이slave에 전송한 수는0이다.
이어 slave TimerOFF AFTER는independent였다. 과거3회와 같은 순서의4번째 사례지만 master해제 영향,
원 명령 지연 반영, 낡거나 전이 중인 보고는 분리되지 않는다. 원action8 delivery UNKNOWN을 유지한다.

child 첫atomic은 지원 후fresh일치로 전송 없이 닫혔다. ACKNone과
`assisted_target_observed_delivery_unknown`을 보존했다. 나머지5개는ACK/after일치다.
child 전체VERIFIED의 별도session 전체image/8controls 대조 뒤source는ROLLED_BACK이다.
action8은ACKNone, `native_own_baseline_restored_delivery_unknown`을 보존했다.

| 구간 | send_once 경계 | ACK 기록 |
|---|---:|---:|
| source | 9 | 8 |
| 자동child 첫 단계 | 1 | 0 |
| assist | 4 | 4 |
| child 나머지 | 5 | 5 |
| 합계 | 19 | 17 |

지원·잔여 새send는9다. 준비11개를 전송11회로 세지 않는다. 소프트웨어 전송 경계이지
TCP/UART 물리frame수가 아니다. 원 실패payload 재전송0, Bar write0이다.

사후 `post-staged-activation-01`:15:01:06.827702–15:01:11.410661, 2pass×3대 전부action3.
jsonl SHA `96a5b743e7928215879a58319e6f57bcd90f57537819de6e6ceda99d7273de07`.
identity, 원image, 제품별 명시controls는 사전6표본과 일치했다. Auto/clock은 원복 고정 목표가 아니다.
fault보고0을 물리 유량·위상·생물 안전 증명으로 올리지 않으며6표본을 연속 감시라고 하지 않는다.

## 보존과 오프라인 재검증

raw는private만 사용한다. wire/status는(a), journal/ACK body/계획/집계는(b)다. ACK body는 정상0B이며
그blob은 구현이 보존한ACK 수신 주장이지 원ACK frame이 아니다.

| artifact id | 내역 | report SHA |
|---|---|---|
| evidence-staged-activation-stop-01 | 175blob+2record+report=178file | `ef4eb75beab62962794e406957b9ba57d81080c90b821d876b0353eaaa6e00b1` |
| evidence-staged-activation-restored-01 | 268blob+3record+report=272file | `92016928102bbc652fb1bd089282d6779a0168d7bcc86638aad70fa061c13993` |

stop source revision78 SHA `5c937939c724b47cc0e78982718cf0c1ed68b517fa2c9be7bd50fd75077a2da7`,
child revision3 SHA `033fe6ee1ce968acf53d55672887fb8772e061e5cdff0e8ef86762ba78cf924d`.
restored source revision79 SHA `b4fece49123aacebfc68f989a80c131e499f22643db619221f981f751302b281`,
child revision33 SHA `2963737d435c8b3651498c38d302de4143bd1a7d658ad6465639053b54434db2`,
assist revision25 SHA `6dbca3c3c6d943507b26872fea9a490e3772f9ceccbcbb31edfdf4ba33e2e394`.
source context SHA `1a498ec68c4de87eefeb27c07e2d257d88f928878361924e47b12a1ead61c063`.
offline audit은record/blob digest, wire decode, previous chain을 전부 따라갔다. 장비 접촉0.
stop/restored audit 출력SHA는 각각
`54b85be4ef3c4939bc3f2e16fe39ca401dca92648027b4a10210393267bd219a` /
`0dd7740d3617568ffb58d573b5fe66e01fd200fe56fd85a33df7c9c9aadc2415`다.
원 기록을 재분류·덮어쓰지 않고 후속 분석을 분리했다.

## 공개 자료 검색 — 구현 원리와 이 장비의 원인은 별개

사용자의 검색 요청에 따라 Gizwits 공식source와 공개Jebao LAN 구현을 읽었다. 추가 실기0.
동일Pro의Sync→Async ACK timeout을 해결한 일차 자료는 이번 검색에서 찾지 못했다.

- [Gizwits 공식 local.c](https://github.com/gizwits/Gizwits-GAgent/blob/72d2db0c386c76224754f350422624255551a737/gagent/local/src/local.c)
  는MCU 보고와제어ACK를 별개로 처리하며 후자는cmd/SN 대조 후 요청자로 돌려준다.
  2015년 참조 구현이며 이 장비firmware와 동일성은 미증명이다. 장비 내부 응답 경로의 조사 근거는
  되지만 이것만으로MCU busy, firmware bug, 응답 라우팅 오류를 원인으로 단정하지 않는다.
- [공개Jebao LAN 구현 device.py](https://github.com/chrisc123/jebao_aqua-homeassistant/blob/bee59f5ce40cb2d915450f61f728e66a073fae09/custom_components/jebao_aqua/gizwits_lan/device.py)
  의`request_status_update`는action3/4 둘 다 상태로 받는다. 그러나 제어 write는
  0x94+sequence의 별도ACK를 기다린다. 보고 허용이 이Async 성공을 증명한다는 선례가 아니다.
- [프로토콜 독자 분석](https://github.com/spinrag/node-gizwits/blob/main/PROTOCOL.md)
  도상태reply/action3과자동report/action4, sequence 포함 제어ACK를 구분한다.

추론 범위: 단순 전체 통신 단절보다는slave 결속 중 요청 처리/응답 반환 경로로 조사 대상이 좁혀진다.
네트워크 한쪽 방향 문제나 적용 지연을 배제한 것은 아니다. 보고를reply로 취급해PASS로 바꾸거나,
timeout을 추측으로 늘리거나, 제어를 재전송하는 수정은 하지 않았다.

사용자가 “이전에는 왜 되었던거지”라고 물었다. 옛 실행에서는 실제async_slave/action4가95pair에
보존됐으므로 그 관측 성공을 부정하지 않는다. 다만 옛PASS는 보고/창 비교이고 정상 종료가 아니었다.
옛adapter에는report를 받는ACK손실 해소 경로가 있으나, 해당 옛 실행이 그 분기를 탔다고 증명할
원ACK wire/해당 진단은 없다. “이전엔ACK를 무시해서 성공했다”나 “timeout이 길어서 됐다”로
단정하지 않는다. 수용 계약 차이는 정적 사실이지만 장비 응답 차이의 원인은 여전히 미확정이다.

추가 시간선 대조: `old-async-docker-stderr-v1`(SHA
`99342368b50a93075b863bacdf80c4d0ce2aeb97e93e778d1a4be2385aac023b`)의
Sync 중간 검증 완료 로그는03:40:18.704283Z다. `async-diagnosis-old-intent-v1`(SHA
`93b3437ad4302c238fd732a962fb71efd31025efa254e6807a6a6e7946d10a08`)의
Async slave adapter 완료는03:40:20.378194Z다. 실제 호출 순서상 Async 호출은 전자 뒤 시작해
후자까지 반환했다. 두 호스트 UTC 기록 간격은1.673911초이며, (b) 보존된 구조화 기록의 경계이지
원ACK wire나 ACK 왕복 지연 계측이 아니다. 이 시간선은 현재의5초 ACK timeout을 거친 뒤
옛55초 해소 경로 덕분에 성공했다는 설명과 맞지 않는다. 정상 ACK 수신인지, 더 일찍 발생한
ProtocolError/OSError 뒤 빠른 상태 해소인지는 분리할 증거가 없다. 옛 `write_linkage`는
ACK 해소 콜백을 넘기지 않으며 adapter 내부 해소 세션은 하네스 pair-refresh 계수에도 잡히지
않는다. 따라서 빠른 반환과95pair Async 보고는 유효하되, 그 반환을 정상 ACK 증명으로 올리거나
성공 이유를 단순히 느슨한 판정으로 축약하지 않는다.

## 운영·실패 보존·§7

build v1은 로컬image ID를FROM에 써registry 참조로 해석되어 실패했다. 원log를 보존하고,
기존tag의image ID 일치를 확인해source-only v2를 만들었다. 의존성 변경·권한 완화는 없다.
source/tests는 실기 시작 후변경0. 최종full 전체PASS가 아니라는 점은 구현 문서에 유지했다.
기존runtime inputs3개는stop/restored export에서 불변 확인했고 원service는stopped다.
새image 준비·자격 유지·원복은 운영generation 교체/기동/HA 상태 출판 확인을 대신하지 않는다.
first Pro 최종앱TimerOFF, Bar 앱이전classic91/F80 복귀, 원service복귀는 별도 미완료 의무다.
이번 원복 목표는 시작snapshot이며 이 의무들을 닫지 않는다.

새 확정 사실은 독립TimerON→role단독, 명시한 정상간격, pair연결 유지 절차에서도 같은
Async ACK/직접reply 실패가 났다는 것, 그 확인 창이 실제report4/reply0이었다는 것,
새9단계context의 소유 지원으로 원image/controls를 회수했다는 것이다. 정보0은 아니다.
제품 질문 누적5회, 이번 신규승인1회이며 원복 수습 횟수와 분리한다. 누적을 초기화하지 않는다.
이번 단회 권한은 결과 기록으로 소진한다. 제6회forward나 추측에 따른hardening은 진행하지 않는다.

대안은 다음과 같다. 여기서 실행 완료나 승인 완료로 주장하지 않는다.

1. native Async를park하고 자격 있는 일반 제어를 먼저 배포한다. 전 기능 완료라고 하지 않고
   수용 범위 변경을 사용자가 결정한다. 기존6제어 유지·startup write0 등의 배포 확인도 남는다.
2. 응답 경로를 별도 질문으로 조사한다. 공개SDK 비교와, 확보 가능하면 기존앱 동일 조작의
   wire/log 대조로 송신cmd/sequence·ACK·report 행선지를 가른다. 복원 상태에서 새write를
   자동 시작하지 않고 필요한 권한/수집 수단과 새로 확정할 사실을 먼저 정한다.
3. 가용성 우선이면 원service exact복귀를 별도로 선택한다. 새image 배포 완료와 구분한다.

park하면20pairs 자격은 남지만native Async 수용·제품 배포·앱/원service 의무는 미완료다.
원인UNKNOWN과 실패 결과를 보존하며 후속 성공으로 이 실패를 지우지 않는다.
