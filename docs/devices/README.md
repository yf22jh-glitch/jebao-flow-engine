# 검증된 장비 카탈로그

이 디렉터리는 실제 장비에서 확인한 **제품군별** Capability를 기록합니다.
공개 저장소이므로 개별 장비의 MAC 주소, device id, 사설 IP와 passcode는 포함하지 않습니다.
`product_key`는 Gizwits 스키마를 선택하는 제품군 식별자이며 장비별 인증 정보가 아닙니다.

제한 write와 원복 관측의 전체 이력은 [실기 기록](../runs/README.md),
현재 판정은 [실기·설치 환경 준비 기준](../hardware-readiness.md)을 따릅니다.
8월 네이티브 Linkage 기록은 [재구성 이력](../runs/2026-08-26--2026-08-28-native-linkage-history.md)의
증거 등급 (c)이며, 보존된 원본 프레임이나 물리 유량 검증으로 해석하지 않습니다.

실기 종료 뒤에는 private 설정을 `dry_run: true`와 모든 장비
`allow_hardware_writes: false`로 잠그고 write-ready recovery를 해제한 뒤,
읽기 전용 Observer의 identity·online·health를 확인합니다.

| 제품군 | 수량 | 분류 | 상태 읽기 | 제한 레지스터 write |
|---|---:|---|---|---|
| DC Pump Pro (WiFi+BLE) | 1 | return pump 후보 | 성공 | 미검증 |
| Aquarium Pump (WiFi+BLE) | 1 | return pump 후보 | 성공 | 미검증 |
| Local Wavemaker (with AP time-sync) | 1 | wavemaker | 성공 | 미검증 |
| Local Wavemaker Pro (WiFi+BLE) | 2 | wavemaker | 성공 | 제한 시험·attended 정확 복원, 미승격 |
| Dosing Pump (no AP time-sync) | 1 | dosing pump, MVP 제외 | 성공 | 미검증 |

물리 모델명과 안전한 최소 출력은 장비 라벨 및 저출력 실험으로 별도 확인해야 합니다.
상세 데이터 포인트 위치는 아직 이 저장소에 복사하지 않았으며, 공개 MIT 구현의 제품
스키마를 읽기 전용 디코딩 교차검증에 사용했습니다. 출처는
[프로토콜 조사 문서](../protocol-research.md)에 기록되어 있습니다.
