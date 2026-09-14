# D1w — 동일 group OFF/ON 순서의 진단 재시험

상태: **재시험 요청에 따른 준비 계획**. 사용자 “야 이거 다시 해봐 절차상이든 뭐든 문제가
있는건지 확인을 해야 할꺼 아니야???”를 D1u group 실패의 새 진단 재시험 요청으로 받았습니다.
과거 D1u/D1v 단회 권한을 재사용하지 않습니다. 이 문서 작성은 배포나 장비 명령 실행이 아닙니다.
아래 물리 결과를 공개하고 현재 현장 감시·즉시 직접 ON 복원 가능 여부를 확인한 뒤,
해당 단회의 정책 결속과 독립 검토를 마쳐야 live 준비로 진행합니다.

## 닫을 질문과 비교 설계

동일한 세 binding, second Pro → first Pro → Bar 순서, all31 baseline과 Constant30 목표에서
기존 group OFF/ON 경로가 완료되는가? 실패하면 어느 action·예외 catch 경계에서 멈추는가?
순서·baseline·명령 수·readback을 감사하여 절차 이탈과 실행 중 실패를 구분합니다.

근거는 [D1u FAIL](runs/2026-09-14-m4-group-power-commissioning.md),
[D1v direct PASS](runs/2026-09-14-local-bar-off-flow-diagnostic.md),
[보존 bytes 비교](bar-group-direct-evidence-comparison.md)입니다. 동일한 payload·준비 snapshot
bytes는 확인됐지만 transport·전송 순간 상태·세션 대기와 원 예외는 미확정입니다.
이번에도 동일 실패를 재현하거나 근본 원인을 반드시 밝힌다고 약속하지 않습니다.
성공해도 이번 group 경로의 단회 완료이며 과거 D1u FAIL이나 원인을 소급 변경하지 않습니다.

대안은 추가 write 없이 park하거나, Bar 우선 등 순서 변경 대조입니다. 전자는 추가 물리
부담이 없지만 group 성공 질문이 남고, 후자는 선행 action·세션 대기도 바꾸므로 이번에는
선택하지 않습니다. 이번 한 번 뒤 결과를 기록하고 재평가하며 성공할 때까지 반복하지 않습니다.
세션 순서·timeout·retry·펌웨어 가설 gate는 수정하지 않습니다. native ASYNC는 동결입니다.

## 실행 전 확인할 절차

1. exact prior image·명시적 Compose 파일 목록·전체 input bundle·canonical journal/receipt를
   새 private artifact에 no-clobber 보존합니다. 마지막 알려진 journal은31/78/109,
   receipt18이며 실제 현재 tree와 unfinished 상태를 재확인합니다. 과거109를 무조건 현재로 쓰지 않습니다.
2. 기존 diagnostic image를 사용합니다. prior는
   `785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`, diagnostic은
   `847c51edb112abb9717c7a577666d5d46842dc78e3a470e40e8386b96ba82d0c`입니다.
   이미지 identity와 source/schema 결속을 다시 확인하며 generation과 image를 함께 기록합니다.
   최신 test 수정의 full2589/Ruff PASS는 시험 검증이지 새 생산 코드나 원인 규명의 근거가 아닙니다.
3. production prepare-compose 경로로 임시 Constant default.power50→30만 변경합니다.
   min30/max75/period10/transition0, phases0/180/90, gains1, invertfalse, roles와 members는
   유지합니다. source0444/parent0700/owner/link1을 확인하고 projection90 byte exact,
   binding4·qualified pair6·ignored0을 검증합니다. prior Constant50에서 ON을 누르지 않습니다.
4. 승인된 임시 진단 배포는 stop-first, no-build/no-pull로1회만 합니다. readiness·실제 image·
   manifest 재생성 순서·unarmed·fresh6·discovery90·journal/receipt 불변을 확인합니다.
   실패하면 자료 보존→candidate 정지→exact prior 복귀 검증으로 command0 종료합니다.
   복귀도 실패하면 정지·보고하며 같은 승인 안에서 재배포를 반복하지 않습니다.
5. 앱 준비 전에 서비스를 정지하고 독립 authenticated baseline을 보존합니다. 첫 Pro40→31은
   9%p, Bar classic91→constant31은60%p와 파형 변경입니다. second는 TimerOFF가 실제
   schedule 출력→manual31 전환이므로 조작 직전 active slot을 읽어 실제 변화 폭을 공개합니다.
   Pro 안내에는 사용자 요청대로 실제 MAC을 붙이되 저장소·중계 기록에는 anchor만 씁니다.
6. 사용자가 모두 ON/TimerOFF/constant/independent/31로 준비한 회신 뒤 별도 fresh read,
   재기동·readiness·unarmed·override0·qualified/fresh field·tree 결속을 재확인합니다.
   Frequency는 first34/second32/Bar80, Bar PulseTidefalse를 유지합니다. 앱 완료를 현장
   준비 확인으로 대신하지 않으며 관측 값이 다르면 원인을 기록하고 임의로 맞추지 않습니다.

## 단회 명령·관측

| 단계 | 허용 동작과 기대 증거 |
| --- | --- |
| OFF1 | second/first/Bar power=false만3 action, 모두 changed, VERIFIED/rev10, power3/Flow0 |
| 중간 확인 | OFF terminal 뒤 독립 authenticated read로 세 대 OFF/Flow31과 선언 필드 확인; OFF hold3 cycle 없음 |
| ON1 | second Flow→power, first Flow→power, Bar Flow→power; 여섯 changed action, VERIFIED/rev19, Flow3/power3 |
| ON 이후 | terminal 뒤 세 complete fresh cycle에서 allON/Flow30·추가 record0 확인 후 service stop |

합계 record2·Flow3/power6입니다. scalar retained나 command echo/pending은 지속성 증거가 아닙니다.
동일 target의 새 complete publication batch·terminal 뒤 diagnostic stamp·online 종료로
fresh를 판정하고 독립 read를 보존합니다. 명령은 QoS0/retainfalse 각1회이며 OFF 이전 arm ON을
추가하지 않습니다. 새 record3, Flow attempted>3, power attempted>6, 잘못된 identity/target/
action/order/revision, 기존/terminal record 또는 receipt 변경, nonterminal failure, observer/
lifecycle 오류는 stop입니다. superseded는 정상 guard 사건일 수 있으며 UNKNOWN stop으로
구분하고 실제 송신 frame으로 단정하지 않습니다. 원본 관측을 먼저 보존한 뒤 판정합니다.

첫 OFF intent부터 내부·독립 host180초 관측 상한입니다. 만료 시 observer를 먼저 종료하고
service를 정지해 이후 ON 발행을 막습니다. 자동 ON이나 물리 OFF 체류 상한이 아닙니다.
stop은 in-flight action을 순간적으로 없애지 않으므로 정지 후 실제 state·journal을 재독합니다.

첫 publish 전에 runtime logs follow를 시작하고 마지막 snapshot도 따로 보존합니다.
실패 event v1의 request_sha256은 접두어 없는 ASCII request_id의 SHA-256이며 index/function/
revision으로 journal과 대조합니다. exception chain·내부 위치·boundary_elapsed_ns를 보존하되
경과는 catch 경계의 software 시간이며 실제 wire 송신 시간과 동일시하지 않습니다.
logging은 best-effort이므로 event가 없으면 원인을 만들어내지 않습니다. 정상 성공에서 event0은
진단 기능의 production 실패 발동 증명이 아닙니다. private names는 count/anchor로 치환합니다.

## 실패·복원과 현장 조건

세 wavemaker가 함께 OFF일 수 있습니다. 리턴·도징은 대상이 아니지만 무조건 무해하다고
판단하지 않습니다. 동시 기동이 아니라 멤버별 순차 복귀이며 수십 초가 걸릴 수 있습니다.
실패 시 일부 또는 전부 OFF로 남을 수 있고 서비스 stop/재기동으로 자동 ON되지 않습니다.
현장에서 즉시 직접 켤 수 있는 사람이 감시해야 합니다.

- OFF 실패: recovery는 ON을 보내지 않아 혼합 상태가 남을 수 있습니다.
- ON 실패: 마지막 nonterminal record의 성공적인 recovery는 power baseline OFF로 수렴합니다.
  이미 켜진 Pro를 다시 끌 수 있고 Flow baseline31로 돌아갑니다. read/send 실패 시 상태는
  UNKNOWN·혼합 가능이므로 어느 경우에도 실제 세 장비를 확인합니다.
- stop→fresh state/journal 보존→필요한 마지막 nonterminal의 기존 config-only qualification
  recovery1회(최대6 frame)→fresh 확인→사용자 앱 복원 순서입니다. 앱 복원을 먼저 수행한 뒤
  recovery로 되돌리지 않습니다. 이전 VERIFIED record·전체 epoch는 자동 복구하지 않습니다.
  재OFF·재ON·corrective direct ON·일반 local recover·복구 retry는 포함하지 않습니다.
- 긴급한 물리 위해나 사용자의 즉시 복원이 우선이면 시험을 폐기하고 사람이 개입합니다.
  그 뒤 stale baseline recovery를 자동 실행하지 않고 새로운 실제 상태와 권한을 다시 확인합니다.
- 앱 목표는 first ON/40/TimerOFF/F34, second ON/latent31/TimerON/F32와 그 시점 active slot,
  Bar ON/classic91/TimerOFF/F80/Pulsefalse입니다. 이번 정상 종료의 second manual30은
  TimerOFF 상태에서31로 복원한 뒤 TimerON으로 넘깁니다. 이전 slot 이름을 고정하지 않습니다.
- exact prior Constant50 image/Compose/bundle 복귀 뒤 unarmed/fresh6/discovery90/unfinished0/
  command0와 새 journal·기존 receipt 불변을 확인합니다. 복구 실패 시 정지·보고합니다.

실행 후 별도 append-only run에 PASS/FAIL/UNKNOWN, action별 증거, 절차 일치/이탈,
예외와 관측 한계, recovery·앱 복원·배포 복귀를 기록합니다. 단회 성공을 D1u 원인 해소나
M4 전체 완료로 올리지 않습니다. 이번 문서까지의 배포·장비 command는0입니다.
