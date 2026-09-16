# 남은 제품 기능 구현 — 2026-09-16

사용자는 Aquarium 속도 제어로 공용 writer가 바뀌면 기존 Pro/Bar 자격을 새 버전에서
갱신해야 한다는 설명에 동의했고, 이어서 "죄다 구현해"라고 요청했다. Claude token limit으로
Codex가 단독 진행한다. 이는 코드 구현·오프라인 검증 범위이며 새 실기·배포·자격 발급은 아니다.

질문: 앱에서 정적으로 확인된 기능의 제품 실행/HA 연결 공백을 실제 없는 필드나 펌웨어
동작을 발명하지 않고 채울 수 있는가. 근거는 기존 앱 분석, vendor schema, writer·journal
계약 및 오프라인 byte/fault-injection 테스트다. 장비 수용·물리 효과를 증명하지 않는다.

## 구현 순서와 경계

1. Aquarium `motor_speed`: 기존 Motor_Speed 필드에 앱 30..100 범위 encoder를 연결한다.
   기존 scalar readback·자기 baseline 복구·single-send를 재사용한다. 제품별 qualification
   경로와 CLI를 추가하되 없는 Flow/Linkage를 만들지 않는다. 자격 없는 HA 제어는 닫힌다.
2. 도징: 이전 요청인 하루 총량/횟수 자동 배분, 1 mL 미만 목표의 여러 날 평균 계획을
   유지하고 HA/MQTT에서 이용 가능한 비전송 계획 API를 연결한다. 확인된 채널 schedule
   이미지는 preview만 제공한다. 실제 투여량과 interval 의미를 추정하지 않는다.
3. 나머지 제품 기능은 현재 실행기·자격 producer·HA 연결을 대조해, 확정된 byte 계약의
   소프트웨어 공백을 채운다. 아래 UNKNOWN을 일반 bool이나 같은 이름의 다른 제품 명령으로
   대체하지 않는다. 기능별 남은 범위와 재개 조건을 이 문서에 남긴다.

허용 범위는 local_control writer/catalog/qualification/dosing/HA/MQTT, 해당 CLI와 직접
tests·제품 문서다. `protocol/`, 기존 frozen native 하네스·직접 tests, 공유 hardware guard는
수정하지 않는다. writer 변경에 따른 schema digest를 공개하며 계산식·기존 receipt를 변조하지
않는다. 구 버전 서비스는 이번 로컬 코드 변경만으로 교체되지 않는다.

구현 중 writer가 위임하는 `wavemaker.py`와 `pump_preview.py`의 schedule compiler가 기존
`install.py`의 schema 입력 목록에 없음을 정적 호출 경로로 확인했다. 이 필수 결속 변경을
사용자에게 보고하고 입력 파일 목록에 두 파일을 **추가**한다. digest 계산 알고리즘은 유지한다.
기존 receipt를 살리는 우회가 아니라 compiler 변경 시에도 재자격을 요구하는 연결이며,
`install.py`와 직접 test를 이번 범위에 포함한다.

## 근거 부족으로 별도 유지할 항목

- Bar/DC/Aquarium timer의 복합 필드·화면별 선택: 현재 앱 분석에는 복수 shape 또는 Auto*
  coupling만 확인됐다. 단순 TimerON bool로 축소할 근거가 없다.
- DC schedule mode 1/2와 Aquarium transport bool 방향: 이름 매핑이 미확정이다.
- clock sync: byte builder는 있으나 계속 흐르는 시각을 무엇으로 검증할지, 과거 시각으로
  되돌리지 않는 복구 계약이 미정이다. 기존 exact scalar 복구로 위장하지 않는다.
- 도징: protocol schema의 control_supported=False와 미확정 물리 단위·interval semantics는
  그대로다. 캘리브레이션 10 mL는 0.5 mL wire scale의 근거가 아니다.

대안은 (1) 추가 앱 정적 자료·기존 raw를 read-only로 확보하여 shape/의미를 확정하거나,
(2) 별도 승인된 최소 앱 조작과 raw 관측으로 두 가설을 구분하는 것이다. park 중에는 해당
live 기능이 닫히고 계획/관측 기능은 유지된다. 재개에는 부족한 근거 및 기능별 검증·복구
계약이 필요하며 실제 write에는 별도 현장 승인이 필요하다. 이번 요청으로 기존 미완료
native source를 복구·삭제하거나 전역 unfinished 차단을 우회하지 않는다.

## 추가한 소프트웨어

| 대상 | 추가/연결한 코드 | 실제 사용 전 남은 것 |
|---|---|---|
| Aquarium 속도 | 앱 30..100 encoder·scalar 실행·전용 자격 모델·CLI·자격 장비에만 HA number | 새 내용에 결속된 실제 장비 자격 |
| Bar/Aquarium 급이시간 | 기존 scalar same/step/원본 복구 producer·CLI | baseline FeedTime 1..60 필요, 0 원본은 쓰기 전 거부; 실제 자격 |
| Bar/DC/Aquarium 시간표 | 선언 image 준비, A/B 두 image 시험 후 자기 원본 raw image 복구 producer·CLI | 실제 image 수용·원본 복원 자격 후 별도 실행 승격; 현재 HA apply는 계속 닫힘 |
| Pro/DC 전체 시간표 삭제 | reset → 지정 B image → reset → 자기 원본 raw 복구 producer·CLI | 실제 reset 두 요청의 자격; 기존 executor 재사용 |
| 도징 | 기존 총량/횟수 및 장기평균 planner의 HA/MQTT 비전송 API | 실제 단위·interval 의미·실행/복구 계약·protocol 동결의 별도 해제 |

시간표 producer는 임의 baseline을 편집 모델로 다시 해석하지 않는다. 최초 raw image를 모든
action의 복구 대상으로 먼저 내구화하고 마지막에도 정확히 그 image로 복원한다. 실패한
action은 재전송하지 않으며 `RECOVERY_REQUIRED`와 영수증 0을 남긴다. 일반 recover는 A/B
전진을 재개하지 않고 보존 원본만 복구한다. VERIFIED 재조회는 장비 무접촉이고 다른 A/B를
같은 id로 제시하면 거부한다. reset 자격은 중간 B 쓰기를 reset으로 세지 않고 실제 두 reset
action에 대해서만 발급하며, 마지막 원본 복원 확인 전에는 발급하지 않는다.

명시적 시험 입력의 CLI 형식(실행 승인이 아님):

```text
jebao-flow-local-qualify run --function motor_speed <기존 현재 입력·별도 승인>
jebao-flow-local-qualify run --function feeding_duration <기존 현재 입력·별도 승인>
jebao-flow-local-qualify run --function schedule --schedule-a a.json --schedule-b b.json <기존 입력>
jebao-flow-local-qualify run --function schedule_reset --schedule-a empty.json --schedule-b b.json <기존 입력>
```

각 호출은 기존 `--write-enabled`, 현 config/manifest/template, 장비 identity/role와 공유 lease를
요구한다. schedule/reset은 장비별 실제 상태의 기존 first-write envelope 및 TimerOFF를
요구하며 Timer를 대신 끄지 않는다. trial 출력은 config와 기존 첫 write 상한 45를 넘지
않는다. reset의 첫 선언은 Pro/DC의 빈 slots여야 한다. 추가 실기는 전혀 실행하지 않았다.

## 아직 전부 완료라고 부를 수 없는 이유

- Pro/Bar/DC/Aquarium clock은 preview까지만 있다. 오차·관측 시각·실패 처리 계약 없이
  과거 시각을 exact rollback하는 실행기는 만들지 않았다.
- Bar timer·feed_switch/pulse_or_tide/linkage와 DC/Aquarium timer·Aquarium feed_switch/transport는
  기존 encoder 여부와 별개로 결합 동작·원본 복원 또는 방향 근거가 부족하다. 기존 진단과
  이미 있는 gated executor는 유지하고 새 자격을 발명하지 않았다.
- DC schedule mode 1/2는 계속 거부한다. 구현된 constant/random/feed image와 별개다.
- 도징 power/manual/timer/interval/calibration/channel schedule의 live write는 여전히 없다.
  기존 frozen protocol의 `control_supported=False`를 바꾸거나 별도 sender로 우회하지 않았다.
  계획 API는 실제 자동 투여가 아니며 캘리브레이션을 했다고 주장하지 않는다.
- 앱 외 Wi-Fi provisioning, 초기화, native 실기 질문 Q1/Q2, 현재 Pro 미완료 source의 정리는
  이 요청의 기능 코딩으로 수행하지 않는다. 특히 마지막 live source의 전역 차단은 유지된다.

따라서 **소프트웨어 공백을 추가로 채운 것**이지, 등록 장비 전체 기능·실장 검증·HA 배포를
완료한 것이 아니다. 위 park 항목의 대안·영향·재개 조건은 앞 절을 따른다. 기존 운영 서비스는
교체하지 않았고, 새 코드를 설치할 때에는 이전 자격을 재사용할 수 없다.

## 검증·인계

- 관련 suite **417 passed**. 기존 Pro/Bar/DC/Aquarium 회귀, 각 실제 모델의 own-baseline
  복구, schedule 세 phase 및 reset 실패의 receipt 0/no-resend, CLI 선택, HA 물리 identity
  자격 격리, 도징 observer 요청과 금지된 apply 거부를 포함한다.
- 전체 suite **3139 passed, 5 failed, 2 warnings**. `/proc/.../boot_id` 없는 macOS의 boot-id
  시험 하나, receipt claim 동시성 두 시험, umask 077 조건의 directory-mode 두 시험이
  실패했다. 이전 baseline과 같은 실패 형상이며 이번에 수정하지 않았다. 전체 PASS가 아니다.
- Ruff와 `git diff --check` 통과. frozen native·protocol·공유 hardware guard 변경 0.
  실기·장비 접속·자격 발급·배포도 0이다. Claude 후속 검토를 받았다고 주장하지 않는다.
- 새 schema SHA-256:
  `3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b`.
  직전 `81ee699d...` 자격은 새 코드에서 `qualification_not_current`로 닫힌다. 원인인 writer와
  compiler 내용 결속을 우회하지 않았다. 기존 receipt 파일 자체는 변경하지 않았다.
- 로그: gitignored `data/remaining-feature-tests.SsTkk0/`.
  `targeted-final.log` SHA-256
  `800294c4a8f5300515b4562d4ef3ac251bae16ee7b4c71fc63b161763cecd0bd`;
  `full-final.log` SHA-256
  `deaf0d11a7cbc92b090ee16db58b7e700ef3a8ddf27686cba0700773264ebc6b`.

최종 연결 점검에서 MQTT의 기존 schedule 요청 parser도 DC/Aquarium 모델을 읽도록 확장했다.
동일한 요청 id·가족/실물 target 대조·coordinator·ownership 경로를 재사용한다. 이는 실행
승격이 아니며 현재 unqualified DC/Aquarium의 request topic은 생성/구독되지 않는다.
이 후속의 관련 회귀는 **139 passed**, 최종 전체는 **3141 passed, 같은 5 failed, 2 warnings**다.
schema digest는 위 값 그대로다. 같은 로그 디렉터리의 `router-final.log` SHA-256은
`7ad06af330438401c02b028187374077332a2cc2456fc2a1684667ac0891d2d5`,
`full-router-final.log`는
`f18461be0c39f766b0b300087d8e4ccb429602dfb8a635eae725d9d96f107826`이다.

새 앱/펌웨어 증거를 수집한 것은 아니다. 설치 앱 bundle의 존재만 read-only 확인했으며,
인증/SDK 로그·Keychain·장비/서버에는 접근하지 않았다. 앱 근거가 부족한 위 항목의 상태를
그 확인만으로 올리지 않는다.
