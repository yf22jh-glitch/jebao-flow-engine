# 제품 Async 역할 단독 전환: ACK timeout

**Linkage 단독 Async 명령도 ACK를 얻지 못했다. Async 결속은 미확인이다.**
변동 Auto/시각을 고정값과 비교하다 실패한 것이 아니다. 당시 자동 원복도 첫 atomic에서
멈췄으며, 뒤의 별도 [소유 복구](2026-09-15-product-async-role-only-restored.md)로 원복했다.
앞선 실험 하네스의 Async 응답 PASS를 취소하거나 이번 제품의 성공으로 재사용하지 않는다.

## 범위와 결속

정책 `582a360`, 구현 `be8bfcb3eb352fba2768224dcce9e0f37d631580`.
사용자 “이어서 계속 진행하자 async 될때까지” 후 제한된 단회다. 제품 질문 계열 누적3회,
중단·보고 뒤 이번 승인1회이며, 질문을 좁혔다고 누적 횟수를 리셋하지 않는다.
선행 [두 Pro 원복](2026-09-15-product-async-owned-restored.md) 뒤 같은 pair·JSON으로 시작했다.

- operation: `wm_product_async_role_only_be8bfcb_01`.
- image: `dc6782b19270dc34bccda5315142a58158645a226994f2cc071526319c3bce98`.
- schema: `81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`.
- 외부 Async JSON: `25ddb198f643f6e2d8716309afc37403aa6275dfdb861878a743972f802b37ee`.
- second/master identity SHA: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- first/slave identity SHA: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

source-only image의 설치145파일·package·schema를 volume0/network none 검사로 대조했다.
검사 로그 SHA `e97de7ca55d122497fd5588f13f64294305b63f7902b4070f96d6f92c8379a04`.
writer/schema 입력9개·기존40receipt·frozen 하네스·timeout/retry 변경0이다.

## 실제 순서와 실패

컨테이너 UTC `12:13:41.585085–12:14:19.765096`, exit2.
slave/master 준비2 → 원본을 소유한 채 임시image2 → master/Sync 역할2는 모두 ACK/after VERIFIED.
시간표는 절대 하루 시각00:00/10:00/15:00의3슬롯이며 실행 후10/15분 경계 시험이 아니다.
master Flow35/32/38, slave30/40/35, 나머지 시간·파형·부가값은 공유했다.

추가 pre-Async 관측의 original capture metadata/status/wire blob을 해시 대조했다(a).
`native_final_sha256.previous`를 따라가고 capture SHA로 중복 제거하면 **2표본**이다.
부분 저장 master1행→master+slave2행을 master2회 관측으로 세지 않는다.

| 대상 | UTC 취득 완료 | action | 보고 제어값 | 저장 image |
|---|---|---|---|---|
| master | 12:14:05.249212 | 3 reply | ON/TimerON/master/constant30/F20 | 임시432B exact |
| slave | 12:14:06.253196 | 4 report | ON/TimerON/sync_slave/sine30/F30 | 임시432B exact |

Auto는 master sine38/F30, slave sine35/F30을 기록했으나 gate로 비교하지 않았다.
capture SHA는 master `984143adf9b51b161e612a8de7a0051053f4899b2297ea0668c9e09aa48b1071`,
slave `9423d93ce1979bd3c6ea3b28304516ebaa71610f3d86dab9bc757af70e0f209d`다.

마지막 action6의 expected는 `{"Linkage":"async_slave"}` 한 필드다. payload SHA
`c02514f9291162f4b02a76df76188ddcb68d3099b289cdd97e4fd2dc21a3f2c7`.
실패 진단은 `send_once → send_raw_control → _exchange`의 `ProtocolTimeoutError`/ACK 대기다.
action6은 UNCERTAIN·ACK None이며 after capture는 없다. 화면 comparison의 sync_slave는
**12:14:08.386637 완료 BEFORE report**이지 실패 후 새 응답이 아니다.

자동 child의 첫 slave atomic도 같은 ACK 대기 경로에서 timeout, UNCERTAIN1/PREPARED5다.
그 BEFORE report 완료12:14:14.646735도 sync_slave/sine30/F30/TimerON이었다. 두 BEFORE와
pre-Async slave 표본의 status/wire SHA가 동일하나, 그것만으로 갱신 시점·미적용을 단정하지 않는다.
child payload SHA `00351d47291b14c2ea26d5b5d858063595df3ff60910e8944f2cfa815dd6b3ac`.
준비13개와 실제 send 경계8개(ACK6/미확인2)를 구분한다. 같은 action 재전송0이다.
전송 뒤 물리 수신 횟수나 원 명령 적용 여부는 UNKNOWN이다.

## 보존과 추가 접근 없는 비교

실패 직후 export `evidence-async-role-only-stop-01`: record2/blob83/report1.
source SHA `358caaf4c27a67ea536918d3c985868557af734a33de3e943b3f13da4ff22734`,
child SHA `38afd4ea6bdd88ebf64c5256dc6ac24349197c1167ad0d58f0ef1174919b66db`.
report SHA `a908cb81a2d466372d379372b7d9eaaeafa57d6fbc8398b363f5a964592f3298`.
운영자 출력·진단 로그 SHA `2b8e3bf5370499ffb5556ba0a768730ed9ca6bb51e30edc85b530959d3a8b3df`.
저널/진단은(b), 참조 원본 프레임은(a)이며 혼합하지 않는다. 복구 전에 원 실패 파일을 보존했다.

이전 성공 [Async 기록](2026-09-15-native-scenario-async.md)의 보존 stderr를 다시 읽었다.
SHA `99342368b50a93075b863bacdf80c4d0ce2aeb97e93e778d1a4be2385aac023b`로 원 기록과 같다.
`a4fcd98..be8bfcb`의 Lan adapter·공유 session/control codec4파일은 diff0이다.
정적 경로에는 다음 차이가 남는다. 원인 확정이나 새 실행 승인이 아니다.

| 지점 | 이전 실험 하네스 | 이번 제품 |
|---|---|---|
| TimerON·초기 역할 | independent TimerON을 먼저 쓰고 pair 확인 뒤 역할 단독 쓰기 | TimerON+master/Sync 역할을 각각 함께 쓰기 |
| Async 앞 연결 | Sync 뒤 양쪽 disconnect/connect·pair 읽기, 그 연결로 Async | 관측용 연결을 읽고 닫은 뒤 action용 연결에서 다시 BEFORE 읽기·전송 |
| adapter 후처리 | command 간격·ACK 후 readback 지연 및 ACK 불명시 별도 RO 확인 경로 존재 | action별 writer, ACK 예외면 보존·자기 원복 |

전송 primitive와 기본 응답 timeout5초/max-skipped8은 같다. old 경로도 Sync 뒤 session 객체를
교체하므로 단순히 old sequence2/new1이라고 일반화하지 않는다. ACK blob은 sequence4바이트를
제거한 body이고 실제 보존 ACK는0B다. 따라서 그 blob으로 wire sequence 분포를 복구할 수 없다.
async report는 `_exchange`에서 skipped 계수를 늘리지 않는다. report8개 포화 설명은 코드와
다르다. 새 로그의 skipped DEBUG0을 실제 report0으로 해석하지 않는다. 필요한 wire 흐름·
ACK 원 프레임이 없어 이 자료만으로 어느 통신 차이가 실패 원인인지 확정할 수 없다.

**새 사실:** Linkage 단독+별도 fresh pair 관측 조합도 Async ACK를 얻지 못했다. 앞선3필드
동시 쓰기만 없애면 충분하다는 기대는 이번 구성에서 성립하지 않았다. payload/관측 두 변수의
효과를 따로 분리하거나 session 문제를 배제한 결과는 아니다. 이 실행은 정보0이 아니지만
제품 누적3회라는 §7 보고 의무는 그대로다. 다음 forward0, source 수정0에서 원복을 먼저 했다.

부수 로그 집계 v1은 모든 JSON에 level 키가 있다고 가정해 KeyError, v2는 stderr 전체가
JSON 행이라고 가정해 JSONDecodeError로 실패했다. 원 stderr는 변경하지 않았고 도구 출력에
실패를 남겼다. 최종 텍스트 검색만 사용했으며 이 집계 실패를 장비 오류나 gate 결과로 세지 않는다.
