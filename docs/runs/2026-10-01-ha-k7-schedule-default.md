# run: 2026-10-01-ha-k7-schedule-default

## 목적과 범위

- 사용자 요청: Aqua 조명 화면이 처음부터 `일정` 탭을 표시하게 한다.
- 확인 질문: 실제 HA 카드 설정과 제공되는 frontend가 `schedule` 초기 탭을 지원하면서 다른 화면·장비 설정·활성 GMP 시간표를 그대로 보존하는가.
- UTC span: `2026-10-01T05:42:04.536944Z`–`2026-10-01T05:42:04.595800Z`.
- 조명·수류모터 제어 요청 0회, HA 및 장비 서비스 재시작 0회다.

## 변경과 확인

1. K7 카드에 `default_tab` 옵션을 추가했다. 미설정 기본값은 기존 `control`이며 `schedule`·`transfer`도 허용한다.
2. Aqua `lights` view의 기존 카드에만 `default_tab: schedule`을 설정했다. 사용자가 이후 선택한 탭은 같은 카드 설정·상태 갱신에서 유지한다.
3. 기존 resource ID를 유지하고 해당 K7 resource의 cache key만 변경했다.
4. 현재 배포 파일에 초기 탭 코드만 추가했다. 별도 K7 작업트리에 있던 버전·모드·저장 동작 변경은 함께 배포하지 않았다.
5. Lovelace WebSocket API로 변경하고 저장 후 전체 config와 resource 목록을 재조회했다. 다른 view 6개·resource 18개는 동일하다.

- 제공 파일 SHA-256: `334b2b2cb8ffc763ce550e2f7bd2ec65a4edc60e44156260190607dc533f56e9`. HA HTTP 응답 body와 실제 파일 digest를 확인했다.
- 초기 탭·수동 탭 유지·기존 기본값·옵션 변경·잘못된 옵션 테스트 5개 통과. 기존 K7 테스트를 포함한 전체 frontend 테스트는 7개 통과했다.
- 실제 배포 변형을 별도로 로딩한 초기 탭 검증도 통과했다. 검증에서 장비 service 호출은 0회다.
- HA 상태 전후 전체 snapshot digest가 동일하다. 조명 상태·저장값과 GMP 활성 실행 revision은 불변이다.
- GMP editor는 `active`, `current_row_verified=true`를 유지했다.
- 사용자 브라우저의 새로고침 이후 실제 화면은 직접 촬영하지 않았다. 현재 열려 있는 오래된 카드 인스턴스는 새로고침이 필요하다.

## 보존한 private 산출물 · 등급 b

artifact id: `k7-default-schedule-20261001T054204Z`. 백업과 적용 결과를 파일로 보존하고 digest를 재확인했다.

| 산출물 | SHA-256 |
|---|---|
| 기존 카드 | `ec9cc9becfb55313c7bb47233a20eea314a84e6575ee7503c3c080a92b101db5` |
| 변경 전 dashboard | `a706333d4d71b4ae8c3bd880a26c54c0d5b43fd897c577ac9a08c04841d609b7` |
| 변경 후 dashboard | `09c453b03fc22741dc859f13aa9dba44410ae7cc167ab88a9353648fc99412f1` |
| 전후 동일 HA 상태 snapshot | `ec43d5e204a2e15f0f29d6151e7a57cbe1fb07ab0c0a49d606c722818fbb6535` |
| release metadata | `58c372f24bf7578a790a3281c896c2fa820863fbce25ef86dee852a086adb9ba` |

**새로 확인한 사실:** HA에 저장된 Aqua 조명 카드는 `일정`을 초기 탭으로 지정하며, 그 옵션을 처리하는 파일이 실제 제공된다. 장비 제어 없이 변경했고 기존 값·다른 화면·활성 수류모터 시간표가 보존됐다.
