# 수온 그래프 가열 색상과 구간 양끝 투명 효과 배포

사용자가 승인한 붉은색 가열 구간과 파란색 냉각 구간을 실제 HA overview 카드에
적용했다. 확인 질문은 구간의 실제 시작·종료 시각을 유지하면서 승인한 색상과 양끝
투명 효과를 재현하는가이다. 로컬 표시 테스트와 배포 후 HA 렌더링으로 확인했다.

기준 HEAD는 `b95d4cd8033a10e38d532a82b9eba3adafaaabce`다. 기존
[출력 이력 기능](2026-10-06-ha-temperature-output-history.md)의 표시만 변경했다.
동결 하네스, `src/`, 엔티티, 장비 설정과 시간표는 변경하지 않았다.

## 구현과 검증

- 가열 색은 `#f35c63`, 냉각 색은 `#63aeff`다. 각 구간 내부에서 양끝의
  불투명도를 0으로 낮추는 수평 SVG gradient를 적용했다. 구간 바깥으로 번지거나
  실제 시작·종료 위치를 옮기지 않는다. 목표 수온선의 노란색은 유지했다.
- 가열 범례, 누적 시간 글자와 현재 가열 상태 카드도 같은 붉은 계열로 맞췄다.
- overview 단위 테스트, JS 구문 검사, return·utility·GMP schedule·bar schedule
  회귀 검사와 `git diff --check`를 통과했다. 변경 코드는 구현 에이전트가 재독했다.
- 1440px·390px 브라우저 표시 테스트에서 구간 좌표, gradient 방향과 양끝 투명도,
  범례 색상, 목표선 색상 및 가로 넘침을 검사하고 화면을 확인했다. 가상 이력을
  사용한 UI 테스트이며 실제 장비 동작의 증거는 아니다.
- 실제 HA에서 새 resource 버전, 붉은 범례와 두 gradient 정의를 확인했다.
  `2026-10-06T08:41:53.400Z`의 출력 이력 조회 오류는 없었고, 기록된 구간은
  대기뿐이었다. 가열·냉각 누적값은 각각 0분이며 조회 이후의 짧은 구간은 미확인으로
  표시했다. 색상을 보이게 하려고 장비를 가열·냉각으로 전환하지 않았다.

## 배포와 보존 증거

기존 도구로 overview JS 파일 하나만 교체하고 HA API로 resource 버전을 갱신했다.
HTTP asset digest와 dashboard 재조회가 일치했다. 7개 view 구성과 31행 활성
시간표 digest가 전후 동일했고 controller는 `active`였다. 장비 제어와 재시작은 0회다.

- artifact ID: `aqua-dashboard-assets-20261006T084052Z`.
- UTC 배포·상태 검증 구간: `2026-10-06T08:40:52Z` → `2026-10-06T08:41:53.400Z`.
- 등급 (b) 구조화 영수증 `release.json` SHA-256:
  `ad456b235a9816e832ab38c01c9d9d08a2cb29c86c7263711a389e72b7402bb9`.
- 이전 asset SHA-256:
  `80f2b65582af11127665367b4a553f15b59d4de805d84c8b319443442d14de14`.
- 배포 asset SHA-256:
  `aff88af0cb3680f62b4ddbffd867b0054297d7213316de8d49f19c33f635f108`.
- 활성 시간표 SHA-256:
  `8a4268b0b495294d1dc07be807fd7d99860d472cccead91087c15e6e05f26f2f`.
- 실제 HA 화면 `ha-live-red.png` SHA-256:
  `7ee3647b024ec214dc01be085e21e7c2da3de0da74732dc8c7dea0b581edc8ad`.
- 가상 이력 PC 화면 `thermal-desktop.png` SHA-256:
  `3ee03d5f23b098599d24f4fc13e730e6705c0c4f028b2d94fcb895b7196164c7`.
- 가상 이력 모바일 화면 `thermal-mobile.png` SHA-256:
  `f7a37e67a01ef65e0bb7ebb018e36245d187e8f81eac5eef7caf17349417faa2`.

영수증과 화면 3개는 같은 artifact에 보존하고 서버에서 digest를 확인했다.
영수증은 배포 검사 결과, 화면은 UI 표시의 증거이며 원본 장비 프레임이 아니다.
브라우저 내부 값 확인은 별도 응답 파일을 보존하지 않은 등급 (c) 관측이다.
이번 UI-only 배포에는 물리 장비 identity binding이 해당하지 않는다.

## 복구

같은 artifact에 보관된 이전 overview asset과 resource 등록을 HA API로 복원한다.
수온·출력 기록과 일정 실행기는 변경하지 않는다.
