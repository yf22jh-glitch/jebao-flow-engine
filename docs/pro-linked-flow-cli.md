# Pro Async 하단 Flow 변경 — operator-only 명령

결속 중 하단에 `Flow` 단독, `Linkage+Flow` 복합, `Linkage` 단독을 직접
보낸 세 단회에서는 값 변경이 확인되지 않았다. 이 명령은 직접 쓰기를
우회하는 새 프로토콜이 아니다. [실측 성공 순서](runs/2026-09-25-pro-lower-flow45-master-release-relink.md)인
**상단 Master 잠시 해제 → 하단 Flow 변경 → 상단 Master 재결속**을
한 번의 터미널 실행으로 수행한다. 검증된 [44 적용](runs/2026-09-25-pro-linked-flow-cli-44.md)과
[45 복귀](runs/2026-09-25-pro-linked-flow-cli-45-restore.md)는 각각
세 child write가 VERIFIED였고, 최종 Master/Async·시간표 이미지 불변을
확인했다.

설치된 현재 generation의 CLI와 private 환경변수가 있는 운영 컨테이너에서,
실제 논리 멤버 이름과 새 고유 request-id로 먼저 `--write-enabled` 없이
미리본다. 미리보기는 장비에 쓰지 않는다. 그 뒤 같은 인자에
`--write-enabled`를 붙여 실행한다.

```text
jebao-flow-linked-flow --master-member <상단 논리멤버> --slave-member <하단 논리멤버> --flow 45 --request-id <새 고유 ID>
jebao-flow-linked-flow --master-member <상단 논리멤버> --slave-member <하단 논리멤버> --flow 45 --request-id <새 고유 ID> --write-enabled
```

상단은 두 role write 사이에 **independent**이다. 세 child는 하나의
원자적 트랜잭션이 아니므로, 실패·중단 때는 상단이 독립 상태로
남을 수 있다. CLI는 실패한 child를 재송신하지 않고 남은 단계를
중지한다. 같은 ID나 새 ID로 곧바로 재시도하지 말고, 세 child journal과
양쪽 fresh 상태를 확인해 미완료 child를 기존 복구 경로로 종결한 뒤
현장 운영자가 재결속 여부를 결정한다.

각 단계는 현행 identity/물리 binding, 설정 출력 범위와 한 번에 5 이하
변경, single-send, durable journal, ACK/readback 및 두 시간표 이미지
불변 검사를 사용한다. 목표 Flow는 현행 설정 범위와 시험 상한 45를
함께 만족해야 한다. 이 조건은 요청 값을 제한하며 물리 유량·위상의
증명이 아니다. HA나 앱에서 같은 두 장비를 동시에 조작하지 않는다.

이 도구는 동결된 `devices/*` ASYNC 하네스를 사용하지 않는다. 새 CLI가
포함된 이미지로 설치하기 전에는 명령 이름이 운영 컨테이너에 없을 수
있으므로, 설치 여부와 source generation을 확인해야 한다.
