# 외부 시나리오 실행기 구현 검증

정책 기준 `9f68f7b`. 이 기록은 구현·오프라인 검증이며 새 실기 결과가 아니다.
사용법과 최초 두 case는 [실행기 문서](native-scenario-runner.md),
[JSON 입력](native-scenarios/sync-async.json)을 따른다.

## 바뀐 계약

새 순수 모듈 둘이 JSON 계약·432B 컴파일·명시 기대값/실측값 대조를 소유한다.
기존 source 네 곳은 이 자료를 patch/role/관측/CLI로 전달한다. 값 변경을 fixed signature에
추가하는 방식은 새 시나리오 경로에서 중단했다. 공통 master 시간표+slave Flow 목록과 각자
다른 시간표 둘 다 입력할 수 있다. 이전 fixed/legacy 경로는 그대로 유지한다.

safe30/31·constant/F20, 출력 상한41·capability/step, 첫0→1, 기존1035/1020/104 예산,
receipt·single-write·journal/fsync·원복 shield는 자료 입력으로 해제하지 않는다.
입력 image 불일치를 반환 PASS로 쓰지 않고, 실제 비교한 필드는 창별 `compared_fields`에
남긴다. 안정 부정 응답의 `SCENARIO_RESPONSES_OBSERVED`와 `response=FAIL`을 구분한다.
영구 park bundle 검증의 적용은 선언형 Async까지 포함하는 명시 결정이며, qualification이나
추가 실행 권한을 새로 만드는 것은 아니다. 공용 guard 자체는 변경하지 않았다.

raw 메타데이터의 전체 bound·digest, pair별 frame, 창별 기대/실측·개수·안정 시간, 최종
보고서가 같은 private artifact에 결속된다. 현재값 없는 필드를 기대값으로 대체하지 않는다.
disarm은 보호 필드별 기대/실측/일치와 read/binding/proof 실패 분류만 추가하며 기존 안전
거부·예외·재시도·timeout·원복 순서를 바꾸지 않았다. 직전 오류 해결은 주장하지 않는다.

## 격리 검증 전 과정

기존 실행 이미지에 pin된 정책 HEAD의 전체 추적 파일 archive와 이번 파일만 overlay했다.
장비망 없는 컨테이너에서 source 읽기 전용·임시 캐시·기존 검사 의존성으로 시험했다.
시험값 변경 때문에 이미지를 다시 만들지 않았으며, 구현 루프 중 새 이미지 build는 0이다.
전체 archive는 추적 입력 누락을 막기 위한 것이지 git 바이너리/디스크 문제까지 해결하는
장치라고 주장하지 않는다.

| 단계 | 결과 |
|---|---|
| 초기 실행 환경 확인 | 이미지에 pytest 없음, 기존 검사 deps를 읽기 전용 mount |
| 첫 포장/접근 정정 | macOS metadata 경고와 신규 비밀값 없는 파일의 읽기 권한 오류; 원본 source/설정 보호 무변경 |
| targeted v1 | 635 PASS, 8.69초 |
| targeted v2 | 643 PASS, 8.79초 |
| targeted v3 | 652 PASS, 8.64초 |
| targeted v4 | 661 PASS, 9.74초 |
| 전체 suite 단1회 | 2757 PASS, 98.64초 |
| 전체 이후 최종 delta | 창별 비교 필드 목록 추가와 해당 계약 단언. 실행 제어 변경 없음 |
| targeted v5 | 661 PASS, 9.73초 |
| 최종 Ruff 첫 호출 | read-only source에 cache 생성이 거부됨; 원 로그 보존 |
| 최종 Ruff 정정 | `--no-cache`로 source6/test6 명시 검사 PASS, 규칙·source 변경 없음 |

전체 2701+신규56=2757이다. 전체 suite는 `compared_fields` 추가 이전 스냅샷이며, 그 뒤에는
해당 연결을 포함한 targeted만 재실행했다. 결과를 숨기거나 전체가 최종 delta를 실행했다고
쓰지 않는다. Ruff 초기33건(긴 줄·import 정렬)을 수정했으며 전체 파일 포매팅 대신 신규
구간만 정리했다. 검증 오류를 없애기 위한 기존 기대값/단언 변경은 없다.

기존 네 test 파일의 최상위 함수 AST는 전부 동일하며 기존 함수 제거/변경0이다.
assert 전수는 experiment 284→291, CLI222→234, linkage1121→1138,
transaction247→249다. 신규 순수 비교기 테스트는 합성 입력의 비교·직렬화·불완전 증거 거부
계약이며 펌웨어 동작을 증명하는 fake가 아니다. monitor 연결 시험도 표본 회계·role label·
원본 결속·부정 응답/유실을 시험하며 Async의 실제 동작을 예측하지 않는다.

보존 증거의 논리 묶음은 `JNS-20260915-implementation`이다. 주요 digest:

- 전체 로그: `3a72650f3615fd873a7906c64cd352bcebdd9de244bb4fd4e8850d3943fb134a`
- targeted v4: `6ab792a0068f5f3098f62a01493600642d6d1eb2a40afff0e218ac2f983d1cfb`
- targeted v5: `a4f8a55e4c3544e369ac9e2aee6293971472e70eb9421632d9e4b23dfd3d07c1`
- 포매팅/검사 v4: `6ac07488cd9ef6d3c91feb237b643f61e890ecb54bcfa272a58a718397ba3559`
- 최종 보존 raw 재분석: `706e56f68801a5852c45da9c88b7d0a3b2b697da0cb8836fc272203a214b4c1f`

Ruff의 성공 본문은 실행 사이 동일할 수 있어 출력 digest만으로 실행을 식별하지 않는다.
여기서는 검사 명령의 source6/test6 범위·이 커밋의 source와 원 로그를 함께 결속한다.

## 보존 실기 raw 재분석

[직전 Sync](runs/2026-09-15-native-sync-master-template-flow-only.md)의 원본97pair,
SHA `9e1acb4dbd0cb2ff9a575e67c69424b2041f5b3ad7e59b13e44881a2c40baac3`를 읽기 전용
mount로 읽고 frame SHA·기존 profile decode·432B image를 확인했다. 새 장비 통신은 없다.
최초 도구의 artifact 접두어 길이 오타로 FileNotFound, 다음 도구의 raw binary DP까지
DeviceState scalar에 넣은 오류가 있었고 원 stderr를 각각 보존했다. 경로 정정과 기존 scalar
필드 선별 후 완료했다. 원본·decoder·판정 임계값은 수정하지 않았다.

재분석 v3은 입력97/97 exact, 기대 차이0, 창별 안정12pair/327.168초,
2pair/32.170초, 2pair/31.724초로 새 계약의 반환 PASS였다. 제외는 repeated59·분차3·창밖11.
이후 `compared_fields`를 넣은 최종 분석도 별도로 보존한다. 새 계약은 legacy의
`_assert_pair_sample` 계열 단정을 그대로 복제하지 않으며 제외 후 재진입 자체를 충돌로
세지 않는다. 따라서 **다른 판정 계약의 재분석**이지 과거 실행이 사실 PASS였다는 정정이
아니다. 이전 자동 미달·exit2·원복 오류·근본 UNKNOWN은 그대로다.

## 경계

신규 실기·장비 write·운영 배포0. 원 서비스 stopped와 first의 마지막 앱 TimerOFF 의무는
여전히 별개다. 현재 TimerON을 원본 상태로 바꾸어 기록하지 않는다.
이 구현 결과 커밋으로 `9f68f7b`의 source/test 예외는 소진·재동결된다. 실행은 같은 정책의
최초 Sync1/Async1 범위에서 source/image·fresh baseline·입력·원복을 별도로 결속한다.

## 실행 이미지와 최초 Sync 준비 — 구현 커밋 이후

구현은 `a4fcd987f36313fd032e51e608c211954aee4c67`로 커밋되어 재동결됐다.
별도 정적 검토는 staged +2191/-43·SHA
`06a54b79bb28be372d7ac327a0cbde2f23370f407e01d008641c586eca538f5b`와 기존 함수 AST
불변을 독립 대조했다. 검토자는 pytest를 재실행하지 않았으며 실행 로그는 작성자가 보존했다.

최초 build 한 번이 성공했다. 이미지 `jebao-native-scenario:a4fcd98`, ID
`edbbbdaaed5d92c0e5729b8f5f3e91842655b2c255006dc4611ac7b1262a5c58`.
기존 exact base `ab6a33dbffe54432788d389164abebe295341d01d34625a228432253037f3de6`에
해당 커밋 bundle만 적용했다. `--network none`은 RUN 단계 제한이며 메타데이터 네트워크
전체의 차단이라고 부르지 않는다. 새 시나리오는 이미지 밖에서 읽기 전용 공급한다.

이미지 source140파일과 로컬 커밋의 manifest SHA는
`b5743fc2c8fae5305298201026a5d5bbb2c5a50031f79212da3daa4cdae62508`로 일치한다.
schema `fa3203f127f81eca46b2242a039c019802c71c65598a00d5228bdaaedbd7d0d6` 불변이며,
이는 새 qualification/실기 승인이라는 뜻이 아니다. 이미지 내부 두 case의 compile/role
전달·전체432B를 검증했다. 검증 산출 digest:
`3fc571a8d21ca179368ab1f43d6a97f2cd1c5e6f66d70cbf45427307f85cc360`.

새 read-only baseline 두 pair에서 두 Pro 모두 online/ON/TimerON/independent,
원432B exact를 확인했다(device-local 2026-09-15 10:44:30~10:45:00).
second/master의 manual constant30/F32·Auto Sine50/F25, first/slave의 manual
constant40/F34·Auto Sine70/F80이다. 이 Auto 값은 현재 시간대의 반환값이지 고정 원복 목표가
아니다. baseline 요약 digest는
`4f1850e706e38dde35ac479c31ae1d33461d7ff2447882e482c6dc3cd44fd20e`다.
신규 빈 capture 디렉터리만 운영 UID100/GID101 소유로 생성했고 기존 root·receipt 권한은
변경하지 않았다. 네 blocking 경로 부재·receipt18·원 서비스 동일 container의 stopped 유지.

최초 Sync는 외부 `sync-wave-and-own-flow` case,
SHA `bf176447d6e78610caecdba7494f35bbb8022f8117cbd5eb77602796ad942194`로 결속한다.
정적 logical 정상 경로 회계는 Sync 전진10+보상12=22, Async는 Sync 경유 role1이 추가되어
23이다. sentinel forward2/restore2 포함이며 실제 frame 수·성공 횟수를 뜻하지 않는다.
skip·실패·보상 분기는 실제 기록에서 따로 계상한다. 새 wrapper는 O_EXCL 시작 기록 이후
기존 preflight1/run1만 부른다. 자동 반복은 없고 첫 기술 실패/원복 미확인이 둘째를 막는다.
wrapper digest `09f3d472a82424005a5a20d45fd7eb58a0a23f08a44efa23314c541a177c6510`은
보존 식별이며 독립 검토자가 해당 private bytes를 검토했다는 뜻이 아니다.

이 준비 시점의 새 native control/schedule/role write0. 각 run은 현재 TimerON snapshot을
자기 원복 목표로 삼고, 이전 앱 전 TimerOFF/manual constant40/F34 의무는 별도 유지한다.
