# Pro 두 대 HA 제어 활성화

사용자의 HA 실제 조작 요청에 따라 [소유 원복](2026-09-16-ha-control-owned-pro-restored.md)과
[새 schema 자격](2026-09-16-pro-control-qualification.md)을 먼저 기록·커밋한 뒤 Pro 전용 제어
generation을 기동했다. 질문은 **갱신한 자격으로 의도한 Pro 제어 표면만 HA에 등록되고,
기동·읽기 전용 smoke가 추가 장비 설정 쓰기 없이 기존 작업 기록을 보존하는가**다.

## 범위·배포

- source `d79289714dc90e08cfeabfb1646922d673b9f8a0`, image
  `85a387b10671556fac3c8c2684e32a79f0c431ac50a64369c6ae54d65d3a78e5`.
- schema `3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b`.
- generation `7c1ba73767a94948be66d72c00986451`; 기존 observer와 별도 Compose project/data volume.
- 새18pair/36receipt만 기존 검증 merger로 병합했다. ignored qualification 0이며 옛40receipt와
  기존 production config는 덮어쓰지 않았다. 물리 identity·제품·역할 결속도 자격 시점과 같다.
- 실제 runtime CONTROL/dry_run=false, write 허용은 두 Pro뿐이다. group control/resume 표면0.
  Bar·리턴2대·도징은 조회/미리보기만 유지한다.
- stage exit0, runtime 시작 `2026-09-16T07:34:26.639133437Z`, 최종 running/restart0.
  Compose activation은 한 번 실행했고 제품 source/test·shared guard·writer/schema는 변경0이다.
- 기존 observer는 `07:14:50.207657336Z`, collector02는 STOP 절차로 `07:14:52.169072246Z`에
  종료됐다. 제어 runtime 시작까지 조회 서비스 공백이 있으며 연속 수집이라고 주장하지 않는다.
  collector03은 배포하지 않았다. 예전 collector와 stopped 원 제어 서비스는 재기동하지 않았다.

## 실제 수신·등록

MQTT 감사 창은 `2026-09-16T07:40:54.893376Z–07:41:41.892036Z`다.

| 항목 | 결과 |
|---|---|
| retained discovery | expected JSON **86/86** 일치, 제거 대상 잔존0 |
| 실제 HA MQTT registry | **86/86**, missing0, disabled0 |
| scalar 조작 | **16개**: 두 Pro 각각 power/timer/linkage/mode/flow/frequency/custom-frequency/feed-duration |
| 추가 API | Pro schedule request2개, native lifecycle request1개 |
| 장비 상태 | fresh publication **6/6**, live state292건, 마지막 global/device6 모두 online |
| runtime | running, restart0 |

HA의 기존 MQTT integration을 사용했고 Core restart/reload, 사용자 dashboard 편집은 하지 않았다.
숫자·선택·스위치 조작 항목은 등록됐지만 시간표 편집·native lifecycle용 전용 dashboard UI를
새로 만든 것은 아니다. Flow 표면30..100은 기존 D1l 사용자 결정이며, 에이전트 실기의 attended
상한50과 별개다. 이번 활성화에서는 Flow를 포함한 장비 설정 명령을 보내지 않았다.

smoke는 native **status**와 도징 **preview**만 publish했다. status는 원 source의 ROLLED_BACK과
`current_state_verified=false`, 원 Async `delivery=UNKNOWN`/ACK 없음까지 그대로 반환했다.
역사적 comparison을 현재 장비 상태로 승격하지 않았다. 도징은13mL/4회 오프라인 계획을
반환했으며 `hardware_write=false`, `wire_send_available=false`, 실제 투여0이다.
두 결과 모두 non-retained다. 감사 창의 scalar `/set` 및 native write 요청은0이다.

HA 화면에서 실제 조작을 클릭하는 추가 실기 smoke는 하지 않았다. 기능별 실기 근거는 직전
자격의42send/42ACK 및 자기 원복이며, 이것을 HA 클릭 end-to-end 검증으로 바꾸어 서술하지 않는다.

## 기록 보존·검증 도구의 실패

기동 전 `07:33:27.333042Z`와 최종 `07:42:34.175982Z` inventory에서 다음 전체 byte digest가 같다.

- journal **2349 files**: `4bd38ea502bf80e27780e9e83c1132c24d133b09ed45a2e8142cdf2e9c254a52`
- receipt **94 files**: `b4b8ef9fd6caf9474e6cedaaf93466490a7ce5c7d26fda687ff3d355a9168b8c`
- 기존 config: `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`
- 기존 qualification: `4abc2f0c0591e48dee28e159eb1928bee1bef66ce32eb1f5135dc76c3de20031`
- 전역 unfinished **0**.

journal-first 쓰기 경로와 이 inventory, smoke의 읽기 전용 요청으로 활성화 구간의 추가 장비
설정 write0을 확인했다. 현재 시각·AutoFlow·활성 슬롯·사용자가 바꾸는 설정의 과거 snapshot
일치는 배포 판정 기준으로 삼지 않았다. 기존 identity/range/single-send/복구 권한은 유지한다.

검증 wrapper의 실패도 보존했다. 첫 Compose 준비 helper는 shared safety를 RO로 mount해
기존 merger의 writable-root 검사에서 중단됐다. network-none helper에 원래 필요한 RW mount를
부여해 재준비했으며 검사를 완화하지 않았다. 기동 전 MQTT 감사는 임시 디렉터리 부재로 실패했고,
그 실패를 확인하기 전에 activation이 진행됐다. 따라서 **이번 전환 직전 retained backup은 없다**.
빈 `mqtt-before.json`을 성공 증거로 세지 않는다. 이전 observer 배포의 보존 MQTT 출력과
기존 Compose/input/data volume은 남아 있지만 이를 이번 시각의 fresh backup이라고 하지 않는다.

기동 후 첫 감사는 정상 문자열 scalar state를 JSON으로 간주해 중단됐다. 다음 실행은 새 audit
script의 읽기 권한 때문에 요청 전 중단됐다. 비밀 없는 해당 script의 읽기 권한과 helper의
tmpfs, scalar 처리만 정정한 세 번째 감사가 완주했다. 첫 감사에서도 요청은 status/preview뿐이며
장비 제어 재전송은 없다. 실패 stderr/빈 출력과 최종 성공 출력을 구분해 보존했다. 제품 소스의
기존 다섯 테스트 실패가 다시 발생한 것은 아니며, 제품 gate나 장비 프로토콜을 바꾸지 않았다.

## 증거·남은 범위

opaque artifact `control-activation-20260916.Acq8Dn`, 증거 등급은 **(b) 보존된 구조화 출력**이다.
이번 activation 감사는 원 wire frame이나 물리 유량 계측을 추가하지 않았다.

| 산출물 | SHA-256 |
|---|---|
| `mqtt-after-03.json` | `1f7c05ff707939ab67b9ad74cc4b8ac2600fbd8ab1f6c5eb46f0a592325bdf25` |
| `ha-registry.json` | `89d0447abd86fdc7d2ad1e7997c8c772c31b559fac49cb64dcd2cd4359e73c5b` |
| `integrity-before-start.json` | `fe1f8e640347cc5172493a8fb00e779b8c216d8b17248816c283bbe783187215` |
| `integrity-final.json` | `663bf5ef6199abe1500b69adce2b539bff2154de1ccc61a4a27c94088c92570f` |
| `runtime-inputs.json` | `cd5342d178bae376304edcb9ffac54f0e4b4968bd22c4e1927c53c99f89a5a79` |
| `runtime-final.log` | `3addd492b97b538e5c353548f314536b93a0c55f0acf1a294760cdcea85536ab` |
| `prepared-summary.json` | `220c7799c5f54bf789867947d80e18b5eac81a556e0e72f77853447f58035a6a` |

**새로 확정된 사실:** 갱신 자격을 가진 두 Pro의 HA 조작 표면과 추가 API가 실제 운영 서버에
등록됐고, 최신6장비 상태와 읽기 전용 요청 응답 및 기동 중 기존 기록 보존이 성립했다.
다른4장비 live 제어, Async 재결속, 사용자16구간 계획 재설치는 이번 완료 범위가 아니다.
Pro는 앞선 자기 원복의 independent/각자 원 시간표로 남겨두었다. 보존된 일일 plan은 후속
사용자 요청에 사용할 수 있지만, 재결속이 미완료이면 전역 제어 차단이 다시 걸리는 기존 계약은
그대로다. Q1/Q2의 물리 동작 질문은 이 배포로 닫지 않는다.

Claude도 보존된 retained payload를 직접 base64 decode하여86개 전부 대조하고, registry·projection·
runtime 입력·세 시점 inventory를 독립 검토했다. 차단 소견0이며 fresh MQTT backup 부재와
HA UI→장비 직접 조작 smoke 미수행을 별도 한계로 유지했다.
