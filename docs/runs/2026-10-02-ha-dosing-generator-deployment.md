# HA 도징 자동 생성 편집기 배포

## 목적과 범위

하루 평균량 또는 N일 총량으로 도징 일정을 생성하고, 기존 HA 도징 제어를 보존한 채
미리보기와 저장·활성화 분리를 제공할 수 있는지 확인했다. 사용자의 구현·HA 배포 요청에
따른 서버와 UI 배포이며 실제 투여 실험이 아니다. 장비 설정 명령은 실행하지 않았다.

기준 HEAD는 `f83492fc5d6dcb70c2396f214fecc1f4a73dd8aa`이다. 배포 후보는 작업트리의
명시적 파일 목록으로 만든 이미지이며 HEAD 전체를 배포한 것이 아니다. 동결 하네스를
수정하지 않았고 원본 격리 작업트리는 건드리지 않았다. 기존 wire writer와 protocol은
교체하지 않았다. 별도 수류모터 실행기 컨테이너의 ID·이미지·시작 시각은 배포 전후 동일했다.

## 보존된 구조화 증거

등급 (b): 원본 장비 프레임이 아닌 배포 영수증과 HA 읽기 전용 감사 산출물이다.
실제 장비의 투여 성공·액체량·native 슬롯 소거를 증명하지 않는다.
아래 영수증과 audit 파일은 서버에서 재조회하여 digest를 확인했다.

- 서버 artifact `dosing-scheduler-20261002T090902Z`.
  - UTC span `2026-10-02T09:09:02Z` → `2026-10-02T09:09:06.462397Z`.
  - release SHA-256 `25db05640eb12cb489c6c48c1fe497f79550d2e53a97e08a3ffd51e0aff23a44`.
  - 도징 physical identity SHA-256 `e71a706c37457dc6f9ccc5b0a94341bffcd897d40eaf0eda9d06e539d7d3423e`.
  - 후보 이미지 `sha256:43f701ad2b9e3479f13c680fc416ad07bf87eb478054851cdd29b6390359e907`.
  - 전후 protocol schema SHA-256 `8863840c2444d54dce306a5759789ba86b1f2c678504924e28f783b3689a8e27` 동일.
- 최초 UI artifact `aqua-dosing-generator-20261002T090931Z`.
  - 기존 dashboard·resource·asset 백업 포함. 이 artifact가 원래 도징 화면 복구 기준이다.
  - release SHA-256 `fe742ab403e8d2508a10e647d72517eb563e55147f2f92a85c1ca8418d7e5043`.
- 모바일 보완 후 최종 UI artifact `aqua-dosing-generator-20261002T091411Z`.
  - UTC 배포 시각 `2026-10-02T09:14:11.471831Z`.
  - release SHA-256 `d0c5b66ca2452a488d0823df495c5acbb1245c159aa04d47943958b176fa00b6`.
  - dosing module SHA-256 `5385b444c1b200516a65e76411a01528a8fb704425fda7334218debd55b3857b`.
  - utility module SHA-256 `583f369067813e84d9d4827c58e1681f6e80987b572e84d27e4167f437ff5e84`.
  - audit UTC `2026-10-02T09:15:24.349999Z`.
  - audit SHA-256 `363b0be021b323eb29febf633b32f27e6a2f413bcbbede8993f45136b2dc39ca`.

## 관측과 검사

- HA API로만 도징 화면의 `dosing_schedule_entity`와 두 module resource를 연결했다.
  `.storage` 직접 수정은 없었다. 새 entity 연결 필드만 제외하면 전체 dashboard가
  최초 백업과 동일하다. 기존 채널·보정 제어와 다른 여섯 view를 보존했다.
- 감사 당시 도징 sensor는 `idle`, active null, channels/templates/history 각 0,
  revision 5였다. 요청 다섯 건은 생성 미리보기뿐이며 저장·활성화·도징 송신은 하지 않았다.
  서버의 durable dosing 상태 파일도 비소유·비활성·기록 없음으로 확인했다.
- 같은 감사에서 GMP 실행기는 `active`, 현재 행 검증 true, last_error null이었다.
  이는 해당 시점의 실행기 보고이며 전체 기간 무고장을 의미하지 않는다.
- HA API 미리보기 확인: 하루 1.5mL → 2일 3회·1mL·16시간;
  하루 0.3mL → 10일 3회·1mL·80시간; 3일 30mL 3회 → 10mL·24시간;
  3일 30mL 한 번 → 지정일 30mL. 네 응답은 실행 출력으로 확인했으며,
  모든 개별 응답 raw를 보존했다고 주장하지 않는다.
- 실제 Orca HA에서 도징 편집기·원래 제어 화면이 렌더링되고 1.5mL 입력의 미리보기 버튼을
  눌러 1mL 세 건과 날짜·간격을 확인했다. 이 마지막 preview는 위 audit에도 보존됐다.
  활성화 버튼은 저장본 없음으로 비활성화였다. Orca 연결의 일시 장애 뒤 재접속하여 확인했다.
- 로컬 PC·390px 모바일 브라우저 테스트에서 저장/활성화 분리, revision 충돌,
  서버 응답 중 편집본 보존, 임의 HTML escape, 입력칸 경계 검사를 통과했다.
  초기 모바일 입력칸 겹침을 렌더링에서 발견해 수정했다. 서버 배포 asset HTTP digest도 일치했다.
- local-control·dosing 회귀 테스트 1,669개 통과. 이후 날짜 overflow 오류를 ValueError로
  변환하는 사례 추가 후 도징 관련 57개 통과. Ruff 및 diff 공백 검사 통과.
  구현 에이전트가 독립된 재독·회귀 검사를 수행했으며 별도 리뷰 에이전트는 실행하지 않았다.

## 새로 확정된 사실과 미검증 범위

HA 화면 → MQTT 생성 요청 → 운영 서버의 정확한 정수량·시각 계산 → HA 미리보기 표시가
동작한다. 기본 상태에서 외부 도징 일정은 실행되지 않으며 기존 제어 설정은 보존됐다.

새 외부 일정의 실제 투여와 완료 후 예약 소거는 이번에 검증하지 않았다.
실투여량은 보정·계량을 필요로 한다. 장치 슬롯은 매일 반복하므로 호스트·네트워크 장애로
소거되지 않으면 다음 날 재실행될 수 있고, 정리 대기는 물리 완료 센서가 아니다.
이 한계를 화면에 표시했다. 긴 기간의 평균 도징을 매일 소량 도징과 동등하다고 보장하지 않는다.

## 복구

서버 artifact에 이전 image·compose 입력을 보존했다. 외부 도징이 비활성이고 채널 소유가
없는 현재 상태에서만 이전 일반 제어 서비스로 복구한다. 활성 일정이 있다면 먼저 예약을
명시적으로 해제하고 확인해야 한다. 공유 hardware-safety 데이터와 별도 수류 실행기는
삭제·재생성하지 않는다. UI는 최초 artifact의 원래 도징 view와 module resource를 HA API로
복원한다. 최신 모바일 보완 artifact는 기능 도입 이전 백업이 아니라는 점을 구분한다.
