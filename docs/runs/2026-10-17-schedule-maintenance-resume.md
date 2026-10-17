# run: 2026-10-17-schedule-maintenance-resume

- 요청: 운영자가 현재 동작하지 않는 수류모터 시간표의 복귀를 명시적으로 요청했다.
- 질문: 청소·환수 전 설정과 저장된 시간표를 보존하면서 현재 구간으로 복귀하고, 세 장비의 응답과 서비스 상태로 이를 확인할 수 있는가?
- 범위: 이미 배포된 local-control의 qualified scalar 및 시간표 편집 서비스 사용. 동결된 실험 하네스 실행, 소스 변경, 새 이미지 배포 없음.
- 복구 작업 UTC span: `2026-10-17T14:13:36Z` ~ `2026-10-17T14:17:34.590994Z`.
- 배포 이미지: `sha256:579cb2481668028f5852da87b690f4020e39bf28fd2b54d4e58c4f5e6a2ec746`.

## 복귀 전 확인

시간표 서비스는 실행 중이지만 `paused`였고, 청소·환수 상태는 `enabled=true`,
`phase=error`였다. 이전 복귀 요청에는 바형 discovery 응답 부재 기록이 있었다.
이번 조회에서는 세 장비 모두 응답했다. 바형의 역할이 청소 전 기록의 `independent`와
달리 `master`여서 기존 복귀 경로의 상태 검증을 통과할 수 없었다.
바형 시간표 이미지와 세 장비의 identity binding은 보존 기록과 일치했고,
미완료 명령은 없었다.

이것은 이번 청소·환수 복귀가 거부된 조건이다. 이전 시간표 일시정지의 최초 원인과
바형 역할이 변경된 경위까지 이 실행으로 확정하지 않는다.

## 수행

1. 기존 qualified coordinator로 바형 역할을 저장된 `independent`로 복구했다.
   해당 명령은 physical write 1회 후 read-back을 확인했다.
2. 실행 중인 편집 서비스에 새 `maintenance_off` 요청 1회를 보냈다.
   서비스는 현재 시각 구간으로 새 실행 epoch를 만들고 기존 single-write 경로로 적용했다.
   GMP 행 기록에는 master release, slave Flow 35, master relink의 세 단계가
   각각 ACK와 read-back으로 검증되어 있다.
3. 바형의 native 시간표를 재개한 뒤 서비스가 청소·환수 상태를 OFF로 확정했다.
   완료 시각은 `2026-10-17T14:16:28.628852Z`다.
4. 별도 조회로 세 장비 상태를 재확인했다. 저장된 시간표 JSON은 실행 epoch를 식별하는
   `source_table`을 제외하면 변경 전후 동일했고, 세 native 시간표 이미지도 동일했다.

## 결과

| 대상 | 복귀 후 장비 응답 |
|---|---|
| 아래 GMP | master, 전원 ON, Timer OFF, sine, Flow 40, Frequency 80, fault 없음 |
| 위 GMP | async_slave, 전원 ON, Timer OFF, sine, Flow 35, Frequency 80, fault 없음 |
| 바형 | independent, 전원 ON, native Timer ON, Feeding OFF, fault 없음 |

- 최종 조회 UTC span: `2026-10-17T14:17:22.632213Z` ~ `2026-10-17T14:17:34.590994Z`.
- 시간표 `active`, 청소·환수 `off`, 미완료 명령 없음.
- 대시보드 상태: `health=normal`, `current_row_verified=true`, `runner_running=true`.
- 현재 구간: Asia/Seoul 21:00~24:00. 다음 예정 경계는 2026-10-18 00:00 KST.
- **새로 확정된 사실:** 바형 역할을 보존된 독립 설정으로 복구한 뒤 기존 복귀 경로가
  성공했고, 실제 장비 응답이 현재 구간의 위 35 / 아래 40 / sine / Frequency 80 및
  바형 Timer ON과 일치했다.
- 한계: 물리 유량·축 움직임 및 다음 자정 경계는 이 실행에서 관측하지 않았다.
  과거 Q1/Q2의 판정은 변경하지 않는다.

## 보존 증거

private artifact id: `schedule-recovery-20261017T141336Z`.
전후 상태, 요청·완료 로그, 행 저널, 전후 시간표, 최종 장비 원본 프레임 및
대시보드 상태를 보존했다. 파일 존재와 SHA-256을 확인했다.

- manifest SHA-256: `7df60fc4fa59d3336097776f594ef7684f5fc0c5a920fccbb2d0d15bc0c6ff84`.
- (a) 아래 GMP 원본 frame: `after-lower_master-raw.bin`, 462 bytes,
  SHA-256 `cb90daf1728ee408754ec9b21572abf1b2070141b0135e59c52bc28c84ad4fb0`.
  Identity binding SHA-256 `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- (a) 위 GMP 원본 frame: `after-upper_slave-raw.bin`, 462 bytes,
  SHA-256 `d21953842bbb805a88f32609a8e95ac49a88f51570f2c9f4c2b92328de51811e`.
  Identity binding SHA-256 `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- (a) 바형 원본 frame: `after-bar-raw.bin`, 411 bytes,
  SHA-256 `b5ef3a5b0914817730a722a4da4393f13b991c0449dc49a560b4c2ef22ff0c54`.
  Identity binding SHA-256 `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- (b) 서비스 상태·행 저널·대시보드 상태는 데몬의 persisted claim이며 물리 유량 증명이 아니다.

복귀 성공 후 저장된 시간표를 계속 실행하도록 두었다. 변경 전 일시정지·청소 상태로
되돌리는 추가 명령은 수행하지 않았다.
