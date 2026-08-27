# 검증된 장비 카탈로그

이 디렉터리는 실제 장비에서 확인한 **제품군별** Capability를 기록합니다.
공개 저장소이므로 개별 장비의 MAC 주소, device id, 사설 IP와 passcode는 포함하지 않습니다.
`product_key`는 Gizwits 스키마를 선택하는 제품군 식별자이며 장비별 인증 정보가 아닙니다.

2026-08-25에는 격리된 IoT VLAN에 홈서버를 통해 접속해 UDP discovery, TCP 12416 연결,
로컬 인증과 raw 상태 읽기를 수행했습니다. 2026-08-26에는 Local Wavemaker Pro 두 대에만
제한된 저출력 레지스터 write/read-back과 attended exact recovery를 수행했습니다. 2026-08-27
TimerON Async 장기 시험은 슬레이브 출력 변경 구간 직후 안전 실패했고, 새 토큰의 attended
recovery 뒤 Observer가 시험 전 상태를 재확인했습니다. 영수증은 0/2이므로 네이티브·스케줄
Linkage는 아직 qualified 기능이 아닙니다. 다른 제품군의 쓰기 가능 항목은 여전히 스키마 선언일
뿐이며, Pro에서도 물리 유량과 파형은 검증하지 않았습니다.

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
