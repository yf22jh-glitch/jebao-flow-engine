# run: 2026-10-01-pro-read-retry-deployment

## 범위와 질문

- 목적: ACK 미수신 뒤 fresh 상태를 끝까지 조회하는 실행기와 HA 표시를, 기존 일시정지·저장 시간표·명령 기록을 유지하며 배포한다.
- 질문: 새 실행기 6개 변경 파일과 카드·표시 수집기가 실제 운영에 반영되고 기존 운전 상태가 보존됐는가.
- 사용자의 `배포하자` 요청에 따라 실행기만 교체했다. 일정 활성화·재개·실제 장비 설정 명령·장비 전원 조작은 이번 범위가 아니다.
- 기반 HEAD: `57de73569ea88d30ea44c0f21511372de6f3f555` 및 기존 operator 작업트리. 배포 입력의 실제 bytes를 private archive로 보존했다. 기존 미커밋 프로토타입을 통째로 커밋·병합하지 않았다.
- 동결 native 6개 파일, 장비 wire codec·writer·공용 journal schema는 변경하지 않았다. schema 지문은 전후 `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27`이다.
- 배포·검증 UTC span: `2026-10-01T04:50:03.229793Z`–`2026-10-01T05:00:47Z`.

## 반영한 계약

- ACK 제한시간은 기존 5초다. ACK 없는 명령은 같은 물리 명령을 재전송하지 않고, 연결을 새로 열어 identity 재확인·인증·fresh 상태 조회만 수행한다.
- 네트워크·조회 실패는 1·2·5·5…초 간격으로 계속 조회한다. 통신 오류와 조회 재시도는 정상 상태로 표시하지 않으며 일시정지·프로세스 종료로 조회를 중단할 수 있다.
- 목표 값이 관측되면 운영 결과를 `readback_confirmed`로 구분한다. ACK를 받은 것으로 만들거나 기존 generic/qualification `VERIFIED`로 승격하지 않는다.
- 목표가 다르면 시도한 명령을 drop/abandoned 처리하고 해당 행의 나머지 종속 명령을 진행하지 않는다. 과거 미완료 명령의 재시작 처리는 조회만 수행하며 원 명령·일부 행을 replay하지 않는다.
- UDP identity 응답과 TCP 인증·상태 조회의 성공은 별개다. 조회 실패를 장비 사망의 확정 증거로 처리하지 않는다.
- 상세 계약과 근거: [명령 read-back 정책](../pro-command-readback-policy.md), [ACK·조회 감사](2026-10-01-control-ack-readonly-audit.md).

## 실행기 배포와 보존 증거 · 등급 b

private JSON/backup 파일을 실제로 재읽고 digest를 확인했다. 아래는 persisted daemon/deployment 상태에 대한 증거이며 장비 raw frame 증거가 아니다.

- 실행기: `jebao-flow-pro-manual-schedule`.
- prior image: `sha256:79d2e442b872e615f52c71e568091bc3b7291cc0f5eab70bb4eca7ba790f4d28`.
- 배포 image: `sha256:e786e245df02c1acb6051538eafc60b1f58bc0808c3470e8fa1fb2094cdb1e4d`.
- 기동 시각: `2026-10-01T04:50:49.754446726Z`. 확인 시 running/restart-count 0.
- 배포 artifact: `pro-schedule-editor-20261001T045048Z`, `release.json` SHA-256 `2c4318d25cc077fdfd89483a044233dc566ef61bfdf448e46b3325560cbfc6f1`.
- audit artifact: `pro-read-retry-audit-20261001T044925Z`.
  - `before.json`: `f1021079a195beb43e6eaab923d2e6aad092ad49abc43083aa6a24a40fb3a70c`.
  - `after.json`: `b06ceb2c41d16fe0ee6f6f8c7d7298133da9b160b745432222d4bb1db94b517a`.
  - `final.json`: `e9dc2d2967a132daf56a94ee7f3170ddb896280d5c5d1d097e89c94bcc24523c`.
  - `deployment-inputs.tar`: `62fb83e8abfa210bfdf94af0fb3aa40b828f7360e804aa4b8185d7a078813c61`、15개 배포 입력 파일、307200 bytes. 비밀 설정이나 raw capture를 포함하지 않는다.

다음 저장 상태는 교체 직후와 최종 확인까지 동일했다.

| 보존 대상 | 결과 / SHA-256 |
|---|---|
| 일정 상태 | `paused` 유지 |
| 활성 plan identity | `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5` |
| active 파일 | `6be9bcdf724354319d211f5cb70dd587b366572c035434071619670394e1c05c` |
| plan 파일 | `7a9289b5cf46988b4682f59ff0724bb5a9028c465196f026343ad5cd12c8d8ef` |
| editor/template metadata | `88c80115436894477fa6eb9c430e69c2c14f1a5c4b7a60870e37a1b68bb6ff4d` |
| scheduler operations | 47개、tree `e519b4c1f487e070d47b5245b6f7b90b039e313939e458b932bd74a36e257c8f` |
| local-control operations | 248개、tree `0866d3ecf76c26dd182dcf7197ed542402960c697ca47c59872db81ca81d368e` |
| 두 Pro 미완료 명령 | 0개 유지 |

- private config/install manifest의 digest도 전후 동일하다. endpoint·passcode·broker 비밀값을 출력하거나 저장소에 추가하지 않았다.
- 템플릿은 3개, 기존 실행본은 31행이다. master/upper/lower 인수·기존 compose/network/mount와 장비 자체 시간표는 변경하지 않았다.
- 새 `communication.json`에는 query null인 상태가 추가됐다. active/editor/기존 operation 파일의 변경은 없다.
- 장비 변경 명령과 새 제어 operation은 0회다. schedule activate/resume 또는 MQTT 제어 요청을 보내지 않았다.
- prior image와 기존 compose override, active/plan/editor backup을 보존했다. 복귀 대상은 정확한 prior image이며 unrelated volume이나 HA 전체 backup을 복원할 필요가 없다.

## HA 반영 증거 · 등급 b

- artifact `aqua-dashboard-assets-20261001T045146Z`, `release.json` SHA-256 `52e2c13f48ec31d0d2b75d550663cc4e1b39dab5561e44f1c0a19ef4ddadf042`.
- 기존 resource 2개만 HA WebSocket API로 cache revision을 갱신했다. 기존 7개 view의 구성 재조회는 동일하고 entity ID·급여기·리턴·도징·Inkbird 매핑은 변경하지 않았다. `.storage` 직접 수정과 HA 재시작은 없다.
- 시간표 카드 HTTP SHA-256: `90fc34c075167a43f6c983da0e59fb0fb7c7653c88dfec26b04142c65e763365`.
- 전체 현황 카드 HTTP SHA-256: `bfeca478c7f0881c323a481c79ed0d507bbd41b178655d0ee879f31fb1f29964`.
- 카드 원 파일, dashboard와 resources의 private backup을 보존했다. 장비 service 호출은 0회다.
- 표시 수집기 artifact `aqua-status-read-retry-20261001T045313Z`, `release.json` SHA-256 `6a9bd054d7938c371948958a617c5e78aaedb2c4cb5bd879849ea12ef91b304f`.
- 수집기 script SHA-256: `fa11c07b405c8056b51f61a5ec0a496832f6e109fabe795438efc46ae4811098`.
- 수집기 내부에서 bind-mounted 파일의 digest를 재확인했다. 수집기만 한 번 restart했으며 network none·safety read-only·uid 100 권한을 유지했다. 장비 실행기와 HA는 이 단계에서 재시작하지 않았다.
- 보존 `after.json`: `3564cec5798cc0f21ad1737039a8fe8ed1cc4f5761c635dfbc0cacaebdb6e645`. `health=paused`、runner_running true、communication.query null이었다.

## 검사와 한계

- 네트워크 none、source read-only、장비 비밀 설정/실기 volume 없는 컨테이너에서 회귀 검사 253개 PASS(12.16초). journal/coordinator、시간표·편집기、조회 재시도、generic service/MQTT/catalog/qualification/install을 포함한다.
- 새 image를 실제 운영 UID로 네트워크·장비 mount 없이 import했다. 설치된 package의 전체 Python 파일 비교에서 변경은 허용한 6개뿐이고 나머지 codec/writer/native 파일 및 schema 지문은 동일했다.
- Ruff、Node 문법 검사、기존 편집기 at-most-once/생명주기 검사、overview 신선도·시간표 비교·Inkbird 표시 검사、조회 중 pause UI 검사를 통과했다.
- 첫 테스트 context에서 예제 템플릿 2개가 빠져 3개 FileNotFoundError가 발생했고, 입력 파일을 보충한 뒤 253개 전체를 재실행했다. 제품 코드를 실패에 맞춰 바꾸지 않았다.
- 초기 후보는 COPY 파일 권한으로 운영 UID import가 실패했다. 운영에 올리지 않고 COPY mode 0644로 재빌드한 최종 image를 검증·배포했다.
- 표시 수집기의 첫 교체 확인은 restart 직전 old JSON을 너무 빨리 읽어 실패했다. 새 프로세스 이후 sample과 communication 필드를 기다리도록 배포 확인 도구를 수정하고, 이미 설치된 동일 script를 추가 restart 없이 확인했다. 실행기/장비 상태는 바꾸지 않았다.
- 후속 HA 직접 재조회(등급 c)에서 editor `paused`、31행/3템플릿、current_row_verified false、communication.query null 및 신선한 public `paused` 상태를 확인했다. 재시도 중이라고 운영 입력을 꾸며 넣지 않았다.
- Orca 브라우저에서는 배포한 card source를 별도 장비 미연결 DOM 시험에 넣었다(등급 c). `상태 조회 재시도 중` / `장비 통신 오류 · 재시도 중`、pause enabled、save/activate disabled、JS error 0、service 호출 0을 확인했다. 이 입력은 운영 장비 오류의 재현이 아니다. screenshot은 browser frame timeout으로 확보하지 못했으며 인증된 HA 전체 화면의 시각 검증을 완료했다고 주장하지 않는다. 이번에 만든 시험 탭은 닫았다.
- ACK loss를 실제 장비에 강제로 발생시키는 실기는 하지 않았다. native Q1/Q2、물리 유량·파형、자동 스케줄 정상 운전 전체의 판정은 변경하지 않는다.

**이 실행으로 새로 확정된 사실:** ACK 미수신 후 read-only 재시도 계약을 담은 실행기와 HA 표시 파일이 운영에 배포됐고、기존 일정 일시정지·템플릿·장비 제어 기록이 그대로 유지됐다. 새 일정 활성화나 정상 스케줄 운전을 실증한 실행은 아니다.
