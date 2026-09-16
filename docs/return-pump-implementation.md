# 리턴모터 제품 구현 재개 — 2026-09-16

사용자는 등록된 리턴모터 두 대도 기능 구현에 포함했고, 미리보기만 추가한 중간 종료 뒤 실제
진행 여부를 물으며 작업 재개를 요청했다. 대상은 보존된 inventory의 DC Pump Pro와 Aquarium
Pump이며 live inventory를 새로 조회하지 않는다. 도징기는 기존 별도 요구를 유지한다.

닫을 소프트웨어 질문: 기존 scalar 실행기가 있는 리턴모터를 수류모터 전용 state/qualification
모델 때문에 검증하지 못하는 공백을, 제품에 없는 필드를 발명하지 않고 채울 수 있는가.
근거는 기존 앱 catalog·제품 schema·writer 계약과 durable transport 테스트다. 이것은 장비
수용이나 물리 효과에 관한 질문의 답을 대신하지 않는다.

첫 최소 단위는 DC의 기존 manual_flow/power producer 지원이다. DC에는 Linkage가 없으므로
프로파일에서 해당 전제를 적용하지 않고 상태에는 None으로 명시한다. 기존 Pro/Bar의
independent 전제·출력 범위·journal·single-send·전원 실패 후 OFF 정책은 그대로 유지한다.
허용 파일은 qualification_producer.py, power_qualification_producer.py와 직접 tests/CLI/docs다.
Aquarium의 다른 필드 구조는 독립 정적 검토 뒤 별도 최소 범위를 정한다.

frozen native·protocol·공유 hardware guard·writer·catalog 실행 승격은 변경하지 않는다.
장비 write·실기 자격 발급·배포는 이번 소프트웨어 요청에서 추론하지 않는다. writer 변경이
필요해지면 기존 receipt currency에 미치는 영향을 먼저 보고하며 fingerprint를 우회하지 않는다.
