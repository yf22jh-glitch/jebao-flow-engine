# Pro first-write 자격 발급 구현

`30931ee`의 [승인 계획](wavemaker-qualification-plan.md)을 구현했다. 기준 source는
`0efd68c`의 제품 lifecycle이며 기존 frozen 하네스·protocol·공용 guard는 변경하지 않았다.
**소프트웨어 발급 연결 완료이지 장비 자격 발급, 새 atomic 명령 수용, 이미지/운영 배포 완료가 아니다.**
기존 6개 direct 제어를 잃는 상태에서 운영 generation을 교체하지 않는다.

## 고정된 발급 계약

- 새 producer만 명시적 first-write 진입점이다. ordinary `attach`의 9종 `_qualify`는 그대로다.
  임의 시나리오를 받지 않으며 계획은 두 target의 순수 함수로 재계산한다.
- `epochs`·firmware 길이는 정확히 2이고 resolver 결과는 요청 target tuple과 같아야 한다.
  앞의 preparation 2개를 제외하는 위치 계약은 이 조건에 결속된다. 1/3대 거부 회귀가 있다.
- 최초 독립 reply 원본을 소유하고 준비2+scalar36 전량을 기존 journal에 내구화한 뒤 전송한다.
  함수별 payload를 최초 원본에서 만들되 실제 직전 상태는 각 action의 before raw로 따로 보존한다.
- 상속한 `_advance`의 attempting-before-send, ACK, raw, no-resend와 image 직전 TimerOFF·독립
  재독을 사용한다. 오류/취소 뒤 기존 own-original detach를 shield로 완료까지 기다린다.
  복구 성공을 보장하는 것이 아니며 실패하면 원본과 미확인 단계가 남는다.
- 시험 및 최종 증거는 reply-only다. report 수용/timeout/retry 정책을 전역으로 바꾸지 않는다.
- source의 36개 scalar 모두 실제 VERIFIED/ACK/after raw가 필요하다. NO_CHANGE와 atomic
  준비는 receipt가 아니다. source VERIFIED는 기능 전송 단계의 완료이지 native attach 완료가 아니다.
- 별도 detach VERIFIED 및 그 final capture의 full432B·원8controls를 다시 대조한다. 전량
  action의 대상·순서·전진/rollback/기대/원본 digest·검증종류·복구 정책도 재계산 대조한다.
  모든 근거를 통과한 뒤 18개 device/function 후보, 36개 receipt를 기존 보호 root에 설치한다.
- receipt의 readback digest는 기대값 JSON이 아니라 실제 status blob이다. capture는 action·wire·
  status·시각을 보존한다. 두 명령으로 모든 파형/파라미터의 실장 유효성을 증명하지 않는다.
- 반복 호출은 원본 증거의 오프라인 발급만 하며 endpoint 조회와 명령 전송이 없다.
  `current_state_verified=false`로 과거 최종 대조를 현재 상태로 올리지 않는다.
- 현재 epoch는 진입/각 전송/발급 전에 검사한다. 원복 중 epoch가 바뀌어도 자기 원복을 막지 않고
  발급을 거부한다. writer→schema→이미지→첫 자격 write 순서가 문서에만 의존하지 않는다.

CLI는 `jebao-flow-local-pro-qualify`다. 현재 observer config/manifest/template, 대상 두 개,
operation id, 별도 candidate 출력 위치와 `--write-enabled`가 필요하다. 운영 qualification 파일을
덮지 않는다. 산출물 디렉터리 소유권 검사도 장비 접촉 전에 한다. 실패 JSON은 상태와
`recover-wavemaker --request-id`를 남기며 예외 메시지/비밀값을 노출하지 않는다.

## FeedTime 원본 0과 schema

Pro `feeding_duration`의 최초 원본이 0일 때만 rollback을 해당 FeedTime flag 하나와 원본 raw로
만든다. 나머지 controls를 덮지 않는다. forward builder의 1..60 입력 계약은 그대로이며 새 입력
0은 거부된다. 원본 inactive0의 실측 경계는 기존 lifecycle 기록의 보존 분석 근거를 승계한다.
새 펌웨어 가설을 fake에 넣지 않았다.

schema는 `b487319e6479772c120e9c1f48feae918d3e223880d364f4fa64c28251360296`에서
`81ee699d9a71d98a1caa8bc99ac1d9619d2959ba04f343335a31d27e8ac914f1`로 바뀐다.
입력 9개 중 `device_writer.py`만 변경했다. 기존 receipt를 이 digest로 고치지 않는다.
새 자격 뒤 writer의 작은 변경도 currency 재평가 대상이다.

## 검증 — 전체 PASS가 아니다

Python 3.12.14, 동일 로컬 환경·umask077에서 실행했다. 새 시험 transport는 기존 선택 raw field
복사와 ACK/read fault injection만 재사용하며 펌웨어 propagation이나 실제 응답을 흉내 내지 않는다.

| 실행 | 결과 | 해석 |
|---|---|---|
| 신규 v1 | 19 PASS / 2 FAIL | fixture의 `evolve` 미지원 인자와 readonly attribute flag 인덱스 오류 |
| 관련 v2 | 165 PASS | 신규/기존 lifecycle·writer·restore·두 기존 producer |
| 신규 v3 | 22 PASS | 발급 직전 epoch 재검증 및 원복 중 변경 포함 |
| 최종 full 1회 | 2865 PASS / 5 FAIL / 경고2 | 신규 최종24개 모두 포함; 84.81초 |
| 최종 Ruff | PASS | 세 source와 신규 test, no-cache |

v1 오류는 시험 fixture만 정정했고 원 로그를 보존했다. 최종 full의 실패 이름 5개는
[이전 lifecycle full](wavemaker-lifecycle-implementation.md)의 다섯 개와 동일하다:
boot identity의 Linux 전용 입력 부재 1개, exact receipt 경쟁 시험 2개, 디렉터리0755가
umask077로0700이 되는 거부 기대 시험2개다. 경쟁 시험의 근본 원인은 미상이며 이름 집합 일치로
동일 원인을 주장하지 않는다. 이전 구현 전 대조는 해당 기록을 참조하고, 이번 full을 반복해
초록불로 만들지 않았다. 최종 source/test를 덮는 full 결과이며 이후 source/test 변경은 없다.

## 보존과 결속

private artifact id `pro-qualification-20260915.51uFgd6n`에 원 출력을 보존했다.
홈서버 base 도구 읽기는 첫 SSH255 뒤 두 번째 조회에서 성공했다. 장비/운영 volume 없이
network-none 일회용 컨테이너의 패키지 존재만 확인했으며 새 hardware write0이다.
wheel 제작용 로컬 격리 환경은 hatchling1.27.0을 설치했고 runtime 의존성 변경은 아니다.

| 산출물 | SHA-256 |
|---|---|
| `targeted-v1.log` | `524333f9ff9850e74b46d7d67d11843b10f406130e131d89e7ad0cdb28649c00` |
| `targeted-v2.log` | `27f77658b20785282e87f7268d56affa8d14869782b1f6c65323788c99aeef63` |
| `targeted-v3.log` | `05b62e31cd52d737b076bf05ce59bdb4fa892ee4903fd26cc533ea7d361ff767` |
| `full-v1.log` | `04f0949d8be38c6f52f98ce2f1b80070526b175e909672f8217446ac1b5abd70` |
| `ruff-final.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |

| 검사 대상 | SHA-256 |
|---|---|
| `local_control/pro_qualification.py` | `69ac8a6fc4729002d3f5ce3f6b8fbdbaca3736992d2dd868b700a7451b0b8cb1` |
| `local_control_pro_qualification_cli.py` | `60618a01a9873a673425d547ec21ddfef389b0b2a6ed8372baa50934f9276abf` |
| `local_control/device_writer.py` | `fc5f9c432277212357a114bd644cafbcb9c5f875d560caae0f5d8dafdcdae7cd` |
| `tests/unit/test_local_control_pro_qualification.py` | `4d0f50f3871f9bb196a20af82ca9383a75266bc9100afbc7ae70930c4a93ec59` |
| `pyproject.toml` | `8f6d8885695f227235c53eb296d098e20adcaa1e4ef8d7a49466ea8b68ae68bc` |

독립 정적 검토는 ordinary qualification·실제 scalar 증거·own restore·no-resend 경로에 차단
결함이 없다고 확인했다. prep 개수 지적은 기존 exact2 계약과 반대 산술 때문에 철회됐다.
리뷰어가 pytest 또는 보존 private raw를 재검증했다고 인용하지 않는다.
완료 커밋으로 `30931ee`의 소프트웨어 구현 범위를 소진한다. 승인된 실장 qualification,
Sync/Async 각1회, 기존6개 보존 배포와 이전 first 앱OFF/서비스 의무는 각각 남아 있다.

## 2026-09-15 첫 실기 뒤 시험값·빈 ACK 정정

[첫 실기](runs/2026-09-15-pro-qualification-custom-frequency-stop.md)는 custom 입력1을
실제로 전송한 뒤 반환5에서 멈췄다. app 최소5를 무시한 시험값 선정은 우리 계획 결함이다.
장비가 clamp했는지 명령을 무시했는지는 단정하지 않는다. 같은 실행의 정상 ACK body는
0B였으며, 별도 오프라인 추적에서 발급 시 truthiness 검사가 이를 거부함을 확인했다.
후자는 해당 실행이 도달하지 않은 잠재 결함이지 그 실행의 종료 원인은 아니다.

`a1dcd4d`의 별도 승인 커밋 뒤 producer와 직접 test만 정정했다. Feed/custom은 기존
`declared_app_integer_range`로 최소값과 그 다음값을 고르고, 원본이 최소면 순서를 뒤집는다.
현재 원본 기준 Feed는 1→2, custom은 6→5다. 펌웨어 가설을 fake나 gate로 옮기지 않았다.
ACK digest 존재 검사와 `load_blob`의 존재·무결성 검사는 유지하고 body truthiness만 없앴다.
실제 after raw, reply-only, scalar VERIFIED, 자기 final restore 확인 후 전수 발급은 불변이다.

옛 빈 ACK corruption 시험은 부재(None)로 정정했다. 별도 정상 빈 ACK, blob 부재와 손상
거부 시험을 추가했다. 빈 ACK fake는 기존 byte-copy transport의 반환 body만 비우며 보존
실기의 protocol 사실을 인용한다. 역할 전파·장비 정규화를 모사하지 않는다.

| 검증 | 결과 |
|---|---|
| 초기 신규 시험 | 37 PASS / 26.39초 |
| 최종 관련 6파일 | 183 PASS / 38.06초 |
| 최종 source/test full 1회 | 2880 PASS / 5 FAIL / 경고2 / 91.76초 |
| 최종 Ruff | PASS |

**전체 PASS가 아니다.** full의 실패 이름 5개는 위의 이전 full과 동일하다. Linux boot identity
입력 부재, receipt 경쟁 2개, umask 관련 2개이며 이름 일치로 근본 원인 동일성을 주장하지 않는다.
최종 source/test를 덮는 full이며 이후 source/test 변경은 없다. 독립 리뷰는 +90/-9의 source1/
test1을 대조했고 차단 결함0이었다. 리뷰어의 실기 또는 pytest 재실행으로 인용하지 않는다.

private artifact id `pro-qualification-20260915.51uFgd6n`의 원 로그를 보존했다.

| 산출물 | SHA-256 |
|---|---|
| `correction-targeted-v1.log` | `76b09096873e3f91e02a0526623eab024973e4bd082a9a762d4cca9ee51b5d4c` |
| `correction-targeted-v2.log` | `c1c73e3c2a3296f15900d4f2bf77d582dcb4a70e10025dbeb02caf337d2f8acb` |
| `correction-full-v1.log` | `956a70a4fe17513f6ecb13c6c78f7948862cc5511d75ae1daf45f68a38258a5c` |
| `correction-ruff-final.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `local_control/pro_qualification.py` | `8c18a00c2c07b8c7bc45b37cd116dc8a5913ae1545d7667ecb9de5a59bdbac2d` |
| `tests/unit/test_local_control_pro_qualification.py` | `f3f4697837342ef3af74f06890102eb0aed756d1a04ccd1b1c7eae20b1592483` |

writer와 fingerprint 입력9개는 바꾸지 않아 schema `81ee699d…`는 유지된다. 아직 새 자격 발급·
제품 실기·운영 배포는 하지 않았다. 다음 Pro 실행은 별도 id/후보 경로의 두 번째 실기이며,
이 질문의 첫 실행은 08:03Z 시작·정보0 아님이다. 다른 native 질문의 소요시간과 합산하지 않는다.
§7의 보고 기준과 기존 원복 의무를 유지하며, 이번 수정으로 실패한 실행을 PASS로 바꾸지 않는다.
