# Native Sync 준비 — 검색 설정 정정과 진입 차단

판정: **Sync 미실행 / 준비 차단**. 앱·장비 제어·시간표 write는0입니다.
본 기록은 단회 하네스 실행이 아니라 그 실행 전 준비와 구분된 읽기 진단을 다룹니다.
기능 질문의 성공·실패를 판정하지 않습니다.

## 구성과 검색 오류

정책·source 기준은 `f0cced4f69ec9c5ca4875e1d29ac5d5dfb65cac7`입니다.
이미지는 `f233978b17938c30334c3b3a3e1f3880099e45e92369acd38758a4d9f8578c9e`입니다.
준비 도구가 현재 운영 서비스의 설정 대신 다른 보존 설정에서 검색 대상을 복사했습니다.
두 목록은 각각7개이나 주소2개가 달랐고, first Pro의 현재 endpoint는 이전 목록에 없었습니다.
MAC·장비 ID·제품 키가 틀렸다고 확인된 것이 아닙니다.

| 관측 | 구간 UTC | 결과 |
|---|---|---|
| 초기 collector, 예정2 pair | 09:07:19.772481–09:07:52.748243 | second Pro 2회 accepted, first Pro 2회 identity-before 실패 |
| 이전 검색 목록의 별도 UDP 진단 | 09:23:14.352695–09:23:19.358795 | 발견4, second related/exact 1/1, first 0/0 |
| 운영 검색 목록의 별도 UDP 진단 | 09:24:43.828688–09:24:48.835210 | 발견6, 두 Pro 각각 related/exact 1/1 |
| 수정 collector, 예정2 pair | 09:33:18.572717–09:34:02.665144 | 2 pair accepted, failure0/rejected0 |

각 검색·collector는 별도 관측이며 한 번으로 합산하지 않습니다. 별도 UDP 진단의 첫 스크립트는
응답 모델을 dataclass로 잘못 취급해 직렬화에서 실패했습니다. 해당 검색도 수행됐지만 목록은
저장되지 않았고, 다음 버전은 실제 모델 직렬화를 사용했습니다. 초기 준비 도구의 uid/파일
읽기 권한 오류와 이 직렬화 오류를 장비 오류로 올리지 않습니다.

초기 collector의 `identity_not_exactly_resolved`는 미발견·관련 후보 복수·필드 불일치를
구분하지 않습니다. 당시 발견 목록은 보존되지 않았습니다. raw2개는 second의 상태 응답이며
first의 raw/read/observed-binding은 null입니다. 이후 진단을 초기 응답 목록의 복원으로
주장하지 않습니다. 검색 설정의 실제 결함과 정정 뒤 통과는 확인했지만 모든 과거 통신 변수를
배제한 것은 아닙니다.

구조화 산출물(b)의 결속:

- 초기 series `JFS-96948487939b2267192260cafce5e8df`:
  `9e6f3868a0f64e86b61bc29f60a92ae623f338f06e193bb08b04ce9ac9f57d10`.
- 이전 목록 UDP 진단 응답 직렬화:
  `82d2e9f995a45a3f7023be177ef1df58e5ec80ed3eb2834c04e66f500e4d3526`.
- 운영 목록 UDP 진단 응답 직렬화:
  `f9fe2d54aea367a65ffc9dbb25061b1b149bb8c5e58853aec7d359985f1fbea9`.
- 수정 series `JFS-d2cb106dc8dd1dce5d9e3d2fcd7365ef`:
  `f634b7c3113a0304df75af62610e9abbe82bb7cc3cb088411cff9c0cc75bd817`.

UDP 직렬화는 원본 UDP 프레임(a)이 아닙니다. collector의 보존 TCP raw와도 구분합니다.
수정은 private writer/collector의 검색 목록·bind 설정에만 한정했고, 장비 binding·제어 권한·
출력 제한·source/tests는 변경하지 않았습니다. 기존 파일은 남기고 새 준비본을 만들었습니다.
수정 writer SHA는 `0b5eb2e27ac4888301fd3c445f79943c76d40e68574d23a11c81042d5209de0d`,
collector SHA는 `cddf42b26a697886928b0f680731708fdc301e3a8524e98e0175acc3bf26b8e2`입니다.

## 원본 시간표와 운영 서비스

수정 collector의 두 표본에서 각 장비 시간표는 서로 동일한432바이트이고14개 슬롯입니다.
raw에서 추출한 원 시간표와 controls를 별도 private 파일로 추가 보존했습니다.
second Pro의 TimerON·inactive manual30과 first Pro의 TimerOFF·manual40을 구분합니다.

- second 원 시간표: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`.
- first 원 시간표: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`.

사용자는 꺼진 스케줄에도 기존 데이터가 있으니 보존·원복하라고 명시했습니다.
앱 전 원본과 하네스가 앱 준비 후 소유할 snapshot은 다릅니다. 원복 완료를 아직 주장하지
않으며 현재까지 장비 데이터 변경이 없습니다.
초기 준비 때 서비스는09:07:02.169808477Z에 정지했고09:11:49.258621098Z에 같은 prior image
`785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`로 재시작했습니다.
Running 확인은 컨테이너 관측(c)이며 전체 fresh readiness를 다시 증명한 것으로 쓰지 않습니다.
수정 collector 때 서비스는 실행 중이었으므로 경쟁 writer 배제된 실기 baseline으로 올리지 않습니다.

## 네이티브 진입의 별도 차단

같은 이미지의 network-none `status`가 exit2 / hardware workflow busy로 종료됐습니다.
실제 root의 exact-restore 파일은 `parked_unqualified`이며 SHA는
`f899c8117106cd5286d764d5c6663246bb7fe7e9a426c7186d86990dba829301`입니다.
이는 파일 관측(c)이고 이 단계에서 immutable archive backing 전체는 검증하지 않았습니다.
같은 이미지·network-none·volume read-only로 guard의 해당 검사를 직접 호출해
`an exact-restore journal blocks the legacy physical-write workflow`를 재현했습니다.
`hardware_guard.py`는 해당 파일의 존재만 검사하므로 보류 tombstone도 차단됩니다.
native intent/run·앱 조작은 시작하지 않았고 journal 삭제·이동·다른 root·검사 우회도 없습니다.

local-control D1i는 별도 envelope 판정으로 이를 다루지만 native guard에는 그 경로가 없습니다.
대안은 (1) 영구 보관 자료로 검증된 tombstone만 식별하는 좁은 진입 변경,
(2) 이 시험을 park하고 독립 운전을 유지하는 것입니다. standalone exact-restore lifecycle의
별도 qualification/write는 이번 승인에 포함되지 않습니다. tombstone 제거는 대안이 아닙니다.
사용자는 기록을 삭제하지 않는 진입 검사 수정·검증 질문에 “ㅇㅇㅇㅇ”라고 승인했습니다.
변경 범위는 AGENTS의 별도 정책에 결속하며, 본 준비 기록을 커밋하기 전 source는 수정하지 않습니다.
