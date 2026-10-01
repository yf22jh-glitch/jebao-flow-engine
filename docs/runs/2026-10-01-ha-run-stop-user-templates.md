# 2026-10-01 HA Custom Run·Stop 표시와 사용자 시간표 두 개 저장

## 질문·범위·결론

- 질문: 앱의 Custom Run·Stop을 어떤 필드에 대응시켜야 하며, 사용자 표 두 개를 기존 운전에 영향을 주지 않고 저장·표시할 수 있는가.
- 설치된 Jebao Aqua Pro 화면 자산 3종의 연결 코드를 대조했다. 앱의 **Run (%)는 `Frequency`**, **Stop (%)는 `Cust_Wav_Freq`**다. 외부 시간표에는 각각 `frequency`, `custom_frequency`로 보관한다. [필드 대응과 자산 digest](../pro-custom-run-stop.md)를 참조한다.
- HA 시간표·현황·수류모터 제어 카드와 팝업의 Custom 표시는 Run·Stop으로 맞췄다. 다른 모드의 Frequency는 이름을 바꾸지 않았다. 숫자 40·20을 초로 변환하지 않는다.
- 사용자 표 1은 `일일 패턴 1 · Pulse·Tide`의 GMP 27행/MCP 27행, 표 2는 `일일 패턴 2 · Custom·Nutrient`의 GMP 31행/MCP 31행으로 실제 서버에 저장했다. 각 표의 Feeding 9회·6분 및 전체 행을 재조회하여 대조했다. `기본 시간표`는 변경하지 않았다.
- 표 2의 16:00 GMP Custom은 상단/하단 Flow 40/40, Run 40%, Stop 20%다. MCP는 Constant 45이며 19:00은 Constant 40이다.
- **MCP 표는 템플릿에 저장·표시하는 참조 데이터다. 현재 GMP 템플릿 활성화가 MCP 운전을 바꾸지는 않는다.** 편집기에 이 범위와 `선택 GMP 템플릿 활성화`를 명시했다. MCP actuator를 구현·실행했다고 주장하지 않는다.
- 활성화·재개·일시정지·장비 변경 요청은 보내지 않았다. 기존 `paused`와 `ConnectionResetError` 기록을 유지했다. 이번 작업의 장비 송신은 0회다.
- native ASYNC 동결 대상, 기존 actuator·실행기·일정 파서·서비스 코드는 수정하지 않았다. 이전 관측 기록은 먼저 별도 커밋으로 보존했다: [전날 감사](2026-10-01-yesterday-schedule-audit.md), commit `b8366bc`.

## 실제 배포·저장 증거 · 등급 b

원본 장비 프레임의 증거가 아니라 보존된 서버 배포·metadata 결과와 파일 digest의 증거다. 아래 release 파일과 digest를 실제 재조회했다. 배포 artifact 구간은 `2026-10-01T02:01:10Z`부터 `2026-10-01T02:10:28Z`의 마지막 UI 배포까지이며, 아래 import 재조회 시각을 별도로 기록한다.

| 용도 | opaque artifact id | 보존 release SHA-256 |
|---|---|---|
| editor metadata-only 이미지 교체 | `pro-schedule-editor-20261001T020110Z` | `00d4156e20905d52b07b8560132e3f59287461fc14ed6ebe122eb3d9c64bf7d5` |
| 사용자 표 두 개 저장·재조회 | `aqua-user-templates-20261001T020149Z` | `9fed6711fbe9404ecb68745d365a7ec867ef71a59745567199e3379996a3e937` |
| 현황·제어·편집기 자산 배포 | `aqua-dashboard-assets-20261001T020339Z` | `72d7a7054d91d9bacb86af85733404d581ee71a3fd3d7ea0d55a7cc2ac6abaa4` |
| 모바일 MCP 표 줄바꿈 후속 배포 | `aqua-schedule-templates-20261001T021028Z` | `13b3bcbb1f9149c4c8f668d113ffe3d68e4f390491ca80312ce4f3e81ccf5794` |

- import 재조회 시각: `2026-10-01T02:01:50.009762+00:00`. 두 `template_create` 결과 모두 `created`, `device_writes: 0`이었다. 표의 일부 특수 행만이 아니라 GMP/MCP 배열 전체가 요청 JSON과 같았다.
- 표 1 canonical JSON SHA-256: `e432a6e963090b5c884f31fa2da9fa7af6d825a6c3f0c29e083565b32b5b2d2a`.
- 표 2 canonical JSON SHA-256: `133edacc2e37e3c4749181de7d531c03c202169eeeb304ff408078ddeec6c4e2`.
- 원본 표는 공개 설정 예제 [표 1](../../config/examples/aquarium-template-1.json), [표 2](../../config/examples/aquarium-template-2.json)로 보존했다. 비밀값은 포함하지 않는다.
- upper identity SHA-256: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- lower identity SHA-256: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- 새 이미지 SHA-256: `79d2e442b872e615f52c71e568091bc3b7291cc0f5eab70bb4eca7ba790f4d28`. 기존 이미지 `ede83fbeeda5c55e948db3e1135b54550032c438285c05cdf1fee1a8b734ab23`와 editor metadata backup을 복구용으로 보존했다. 새 이미지의 변경 범위는 editor 모듈 하나다.
- active 파일 byte SHA-256 `6be9bcdf724354319d211f5cb70dd587b366572c035434071619670394e1c05c`와 원 실행 plan 파일 byte SHA-256 `7a9289b5cf46988b4682f59ff0724bb5a9028c465196f026343ad5cd12c8d8ef`는 전후 동일했다. 활성 plan 식별 digest는 `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`다. byte digest와 plan 식별값을 혼동하지 않는다.
- 기존 non-editor operation 39개는 전후 동일했다. 상대 operation 이름과 각 파일 digest로 계산한 aggregate SHA-256은 `ac1ddfe436d1c8bc9f5d938455e0b62c05ed1f131c2380b6cb4d82c524ca91d8`이다. 새로운 기록은 metadata 요청 두 개뿐이다.
- 기존 last-error의 시각 `2026-09-30T19:20:57.470211+00:00`, code `scheduler_paused`, error type `ConnectionResetError`를 유지했다. 해결·재개했다고 표시하지 않았다.
- HA의 7개 view와 기존 동작은 유지했다. 자산/resource만 API로 갱신했고 `.storage` 직접 수정이나 HA 재시작은 없다.

최종 HA HTTP 재조회 SHA-256은 로컬 자산과 각각 동일했다:

| 자산 | SHA-256 |
|---|---|
| `aqua-schedule-editor.js` | `96e7a70465b7e07807a8fc0a25a9067fbf6df2e4027928912e4485b526dc9f92` |
| `aqua-overview-card.js` | `357274a49bd81fdf08ef044dbd362bd9b90e1d1e30540aafea9ecfa1dea60e1a` |
| `aqua-return-card.js` | `9ba3294ce45109feee0a3ed23751cc7944d349b15937075862e860ea67dbd104` |

## 코드·화면 검증과 한계

- Python 검사 102개를 read-only·network-none 격리 컨테이너에서 통과했다. touched Python Ruff, Node 검증·생명주기·metadata at-most-once·stale 저장 검사도 통과했다. 새 펌웨어 가설을 fake로 증명한 검사가 아니라 JSON 보존과 내부 UI/저장 동작 검사다.
- Orca 내장 브라우저에서 실제 card source와 사용자 JSON 두 개로 만든 **장비 미연결 preview**를 확인했다. 1400px·390px에서 page 가로 overflow가 없고, 모바일 MCP 표 폭도 실제 표시 폭과 같았다. Run 40%·Stop 20%, Flow 40/40과 두 템플릿의 27/31행 표시를 스크린샷으로 검사했다.
- preview의 JavaScript 오류는 0이었다. 로컬 fixture의 템플릿 선택 요청 2회, 활성화·일시정지 요청 0회였다. fixture는 실제 장비 송신 경로가 없으며, 인증된 HA 전체 화면을 검사한 것으로 표현하지 않는다. 실제 HA 서버 metadata·HTTP 자산 검사는 위 별도 증거다.
- MCP 참조 행은 저장·복사·가져오기·내보내기에서 보존된다. metadata 결과는 `mcp_schedule_scope: stored_reference_only`를 표출한다. 이번에는 MCP 실행 경로를 추가하지 않았다.
- 이번 작업은 물리 운전 실기가 아니다. 두 새 템플릿의 실제 전환, Custom의 실제 시간·파형, MCP 동시 전환, 기존 일정 중지 원인의 판정을 확정하지 않는다. 원래 장비 보고를 Run·Stop으로 표시하는 것과 장비에 새 값을 쓰는 것을 구분한다.

**새로 확인한 사실:** 설치 앱 코드에서 Run/Stop의 표시 단위와 필드 방향을 확인했고, 실제 HA 서버에서 두 사용자 시간표 전체가 저장·재조회되며 기존 활성 plan·pause·장비 operation 기록을 바꾸지 않음을 확인했다.
