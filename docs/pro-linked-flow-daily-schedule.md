# Pro Async 하단 수동 Flow 일일 시간표

이 도구는 [2026-09-22의 31구간 표](runs/2026-09-22-ramped-flow-carrier-bar-schedule.md)를
별도 JSON 시간표로 보관한다. 현재 표에서는 하단 Pro의 일반 운전 22구간이 모두
`Flow=40`이고 Feeding 9구간은 Flow 대상이 아니다. Feeding 시작에는 **송신 0회**,
종료 뒤 일반 운전 경계에는 현재 하단 수동 Flow를 확인하고 필요할 때만 40으로
맞춘다. 상단 Pro와 MCP-70의 파형·Flow 및 기존 장비 시간표 이미지는 변경하지 않는다.

원본 예시는 [`config/pro-linked-flow-daily.example.json`](../config/pro-linked-flow-daily.example.json)이다.
각 행은 다음 시작 시각까지 적용하며, 21:00 행은 다음날 00:00까지 적용된다.
JSON의 `flow: null`은 Feeding에 Flow를 쓰지 않는다는 뜻이지 `Flow=0` 명령이 아니다.
이 일정은 장비의 432-byte 시간표에 쓰지 않고 호스트의 Asia/Seoul 시계로 실행한다.
현재 Dockerfile은 예시 JSON을 이미지에 복사하지 않으므로 운영 컨테이너에서는 승인된
파일을 읽기 전용으로 별도 mount하고 그 컨테이너 경로를 `--schedule-file`에 준다.

읽기 전용으로 현재 행과 다음 경계를 확인한다.

```text
jebao-flow-linked-flow-schedule --schedule-file <읽기 전용으로 제공한 JSON 경로> \
  --master-member <상단 논리멤버> --slave-member <하단 논리멤버>
```

`--loop`만 붙이면 경계마다 미리보기만 출력한다. `--write-enabled --loop`는 경계에
도달했을 때 기존 [3단계 명령](pro-linked-flow-cli.md)을 호출하는 구현이지만,
**지속 송신 서비스는 현재 설치·활성화되지 않았다.** 장비 write에는 해당 operation의
현장 감시·전원 차단 수단과 승인, 단일 제어권, 실행 기록이 필요하다.

루프 시작 때 이미 진행 중인 행을 소급 적용하지 않는다. 현재 행을 즉시 맞추려면
현장 승인된 단회 operation에서 `--write-enabled --apply-current`를 별도로 사용한다.
현재 하단 수동 Flow가 45인 상태에서 시간표 파일을 읽거나 미리보기만 실행하면
Flow는 45 그대로다. 경계 처리 실패·호스트 시계 역행 시 루프는 중단하고 자동
재시도하지 않는다. 미완료 child journal과 두 장비 fresh 상태를 먼저 확인해야 한다.

일반 운전 경계에서도 이미 Flow가 40이면 장비 write는 0회다. 값이 다르면 기존
명령이 상단 Master를 잠시 해제하고 하단 Flow를 변경한 뒤 재결속한다. 세 child는
원자적이지 않으므로 중단 시 상단이 independent로 남을 수 있다. 이 도구가 파형·위상
또는 Feeding 종료를 보장하지는 않는다.
