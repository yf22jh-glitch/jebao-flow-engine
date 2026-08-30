# run: 2026-08-30-q2-qualification-retry

- 목적(한 문장): 두 Pro를 저출력 Sync로 다시 자격화해 Q2 단회 실기의 최신 영수증을 만든다.
- 이 실행이 답하려던 질문: 기존 영수증 만료 없이 새 Q2 실기를 시작할 수 있는가?
- 실행한 commit SHA: `8653a7604e79762ab5dcbf3079f464940429c0a2`
- 사용한 명령·주요 파라미터: 두 논리 역할, Constant 31%, Sync, 60초, attended
  qualification과 즉시 ordered recovery.
- 종료 지점(stage / failure code): 두 역할 write 뒤 자동 rollback 검증이 완료되지 않아
  recovery-required가 됐고, 같은 operation의 승인된 `recover-linkage`로 terminal 복구. 새
  qualification receipt는 발급되지 않음.
- 장비가 실제로 돌려준 값:
  - 증거 등급: **(c) reconstructed operator observation**. 이 실행에 연결된 raw 또는
    content-addressed daemon artifact는 보존되지 않았다.
  - opaque artifact id 또는 안전한 상대 논리경로: 없음
  - UTC span (시작 / 종료): 보존되지 않음
  - SHA-256 identity binding: 승인된 두 역할의 기존 binding과 일치했으나 이 실행 전용
    artifact는 없음
  - artifact digest (SHA-256): 없음
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): attended recovery가 terminal을 만들고
  미완료 journal을 모두 닫은 것은 당시 운영자 관측 등급 (c)이다.
- **이 실행으로 새로 확정된 사실:** 최신 receipt를 만들려는 재자격 경로도 기존 역할 해제
  read-back 문제를 만나므로 Q2 단회 실기에 바로 사용할 수 없었다.
- 이 실행으로 확정하지 못한 것: Q2 슬롯별 `AutoFlow`, 물리 유량, 새 receipt의 유효성.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: 같은 physical binding과 operation에 묶인
  기존 receipt에서 만료 시각만 무시하는 단회 권한을 별도로 기록한다.
