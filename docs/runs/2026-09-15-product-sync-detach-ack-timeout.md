# 제품 Sync 결속 성공, 첫 해제 ACK timeout — 원복 미완료

## 현재 결론과 상태

**Sync attach는 PASS지만 detach는 미완료다. 시험 시간표·역할이 장비에 남았다.**
Async와 운영 배포를 중단했다. Pro/Bar 자격 성공을 해제 성공으로 올리지 않는다.
기존 원 서비스는 stopped이며 앱 마지막 원복 의무도 별도로 남는다.

source `bb5405c`, image `d5e00203d9daedceca2e331d3c827b215bdd64a38446a7bd5f55dba2cdfcc720`,
schema `81ee699d…`, 자기 attach `wm_product_sync_bb5405c_01`에 원본432B/8controls가 보존됐다.
계획은 제품 예시의 master 시간/모드/부가값 상속, slave 슬롯 Flow만30/40/35이며 master35/32/38다.
준비30/F20은 명시한 제품 값이며 10/15분 epoch를 새로 돌리지 않았다.

| 단계 | UTC 2026-09-15 | 실제 결과 |
|---|---|---|
| attach | 08:54:19.246322572–08:54:45.245737 | exit0, 6ACK/6VERIFIED; 두 image와 master/sync_slave·ON/TimerON exact |
| detach | 08:55:54.417023558–08:56:06.871107881 | exit2; 첫 slave atomic 전송 시 ACK timeout, ACK0, uncertain1/prepared5 |
| 같은 원복의 recover | 08:57:27.186468132–08:57:35.254384931 | exit2; fresh 상태 불일치, 재전송0, 동일 child 유지 |
| 별도 RO6표본 | 08:59분, 마지막08:59:41.006029 | master/slave 시험 상태 유지, Bar 준비 상태 유지 |

detach의 원 예외는 `ProtocolTimeoutError`이며 `_advance:309 → send_once → send_raw_control →
_exchange`에서 났다. `TimeoutError`/취소 하위 체인이 보존됐다. ACK 부재가 전송 미발생이나
명령 미적용을 증명하지는 않는다. 전송 시도1, ACK0으로 기록하며 성공 전송으로 세지 않는다.
첫 evidence는 **before**이며 after readback 불일치로 쓰지 않는다.

recover는 `_advance:281`의 fresh read부터 했다. 아직 sync_slave/TimerON/sine/F30이어서
expected independent/TimerOFF/constant30/F20과 다르고 `:292` no-resend 거부로 끝났다.
명령 재전송은0이다. 최초 원본과 해제 직전 연동 상태를 같은 baseline으로 부르지 않는다.

## 지금 보고된 값과 고정 대조의 범위

RO2pair에서 second는 action3/master/ON/TimerON/AutoSine38/F30,
first는 action4/sync_slave/ON/TimerON/AutoSine35/F30이다. 양쪽 시험432B 유지.
Bar는 action3/independent/ON/TimerOFF/constant40/F80와 원384B 유지. fault7 false지만
이 보고만으로 물리 유량이나 생물 안전을 단정하지 않는다.

실행 verifier는 저장 image와 실제 쓴 controls만 대조한다. 장비 시각/Auto 활성 슬롯의 변화는
고정 expected로 쓰지 않는다. 이번 최초 실패는 그 비교가 아니라 **ACK timeout**이다.
원복의 independent·TimerOFF 전제는 image write 안전 조건이며 변동 관측값과 다르다.

## 남은 경로와 정지 결정

기존 native recover는 uncertain action의 fresh 값이 forward expected와 일치할 때만 전송없이
닫는다. 현재는 다르므로 같은 호출을 반복하지 않는다. 일반 recover도 native context를 거부한다.
자격 있는 schedule_enable은 인접 기능이지만, 일반 coordinator의 `execute:162 →
_require_no_other_unfinished:905-911`이 이번 미완료 record 때문에 새 operation도 막는다.
따라서 단순 다른 id/다른 function으로 해결되는 기존 경로가 있다고 주장하지 않는다.

대안1은 사용자 앱으로 승인된 독립·TimerOFF·준비 제어 상태를 만든 뒤 기존 own recover에서
fresh 일치와 남은 원본 복원을 확인하는 것이다. 앱 수용은 보장하지 않고 초기화는 포함하지 않는다.
대안2는 보존 원본·미완료 명령을 유지하는 별도 제한 복구 절차의 범위를 정하는 것이다.
해제 순서 변경이나 다른 function의 사용은 현재 경로가 아닌 새 결정이며 효과도 미검증이다.
어느 쪽도 uncertain record 삭제, 같은 명령 재전송, no-resend 완화를 자동 포함하지 않는다.
현재 park하면 시험 image/Sync 역할과 원복 의무가 남으므로 정상 복원이나 배포 완료가 아니다.
동결 하네스·source/test·timeout/retry 변경0, 추가 write0 상태에서 보고한다.

## 보존 증거

private artifact id `pro-qualification-20260915.51uFgd6n`; 원격 논리 `evidence-after-sync-01`에
Bar2/native2 record 및 참조95blob을 오프라인 digest 대조 후 복사했다. raw wire/status는(a),
journal·진단은(b), 수동 실행 조회는(c)다. 연속 감시를 주장하지 않는다.
export report SHA `a2e07cdcbe1ab75c3a4256af7d7936a5d0c4c399147e7bcda069966a215cb51b`.
attach record SHA `ccead982f2f8f95f88c72c11864d039101f57d0029d6ed0219da09b22508461a`,
child revision5 SHA `87c4755bbae5eb52b2982306e746196a8874dc3284e5cf0506ea361f598e7410`.

| 원 로그 | SHA-256 |
|---|---|
| attach | `f16e0ccda8c600ca1de5d50cd4aa302302908eba4760cf40cca4274e9b6730d5` |
| detach | `884bd9dae80adce705264eead804926e83177dbb6889e8e12f8b0ee4022b993e` |
| recover | `907728e0ace37f46d29447dbd7d39ae5f5e939dce46c23939c56f000bbed098d` |
| RO current | `fc96ed4525c62afb3f2077ff2f843e92b5b9ee5fc81ec540d324fd04ff559e31` |
| export | `d4dcc2cb3271d2cd6e78539718c265063b47d58e1f977f748a769df653c5a376` |

**새 사실:** 새 product atomic attach가 실제 Sync 목표에 도달했으며, slave 해제 첫 명령은
ACK를 받지 못하고 후속 상태도 목표와 달랐다. 기존 recovery는 재전송을 막고 원본을 보존했다.
원인(명령 처리/응답 손실/보고 지연)은 UNKNOWN, 실제 해제와 원본 복원은 미완료다.
