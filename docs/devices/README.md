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
뿐이며, Pro에서도 물리 유량과 파형은 검증하지 않았습니다. 뒤이은 저출력 Sync 세 건도 영수증
0/2였고, 마지막 slave detach 실패는 attended recovery로 원복한 뒤 rollback의 slave-first fresh
session 경계를 코드와 시뮬레이터에 추가했습니다. 수정 뒤 네 번째 저출력 Sync는 자동 exact
restore와 영수증 2/2까지 성공했습니다. 후속 Async slave 38% 진단은 자동 rollback 실패 뒤
attended recovery로 원복됐습니다. version 2 evidence를 적용한 한 번의 재진단도 write 시도만
남고 adapter/full-state 검증 0건으로 끝났으며, attended recovery와 Observer가 원래 TimerON
상태·14개 스케줄·지문을 확인했습니다. Async 독립 출력과 물리 파형은 계속 미검증입니다.
2026-08-28 TimerON Constant→Sine 단일 시험은 두 역할 적용 뒤 슬레이브 manual Frequency의
지속적인 역할 유발 변경을 fresh explicit reply에서 확인해 A→B 경계 전에 안전 중단했습니다.
자동 rollback과 두 번의 새 연결 원상태 비교가 성공했으므로 장비는 다시 Observer 운전 중이지만,
슬롯별 slave `AutoFlow` 전환과 물리 파형은 여전히 미검증입니다.
이어 `da62b73`으로 한 번 실행한 새 `_08`은 write 없는 preflight를 통과하고 임시 Constant
31%/32% → Sine 35%/40% 계획을 staged했지만, native 역할 실행·Linkage write와 A→B 관찰 전에
`role_preflight`에서 fail-closed로 종료됐습니다. 자동 outer rollback 뒤 세 journal은 모두
`none`이었고, 서로 독립적인 두 fresh read-only 확인에서 원래 controls와 두 장비의 전체
432-byte schedule image가 exact였습니다. private 설정은 `dry_run: true`로 복귀했고 Observer와
recovery 서비스도 정상이며 `_08`은 재실행하지 않았습니다. 슬롯별 slave `AutoFlow`와 물리 파형은
계속 미검증입니다.

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
