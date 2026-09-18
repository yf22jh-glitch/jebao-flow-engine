# 2026-09-18 HA Aqua 대시보드 생성·조명 뷰 이전

## 범위

- 실행 시각: `2026-09-18T02:32:24Z`
- source 기준: `2e00fdbedb0e`
- 변경 대상: Home Assistant storage-mode Lovelace 구성
- 장비 write: `0`
- `src/` 변경: `0`
- Home Assistant `.storage` 직접 수정: `0`

목표는 기존 Pink Fam의 조명 전용 뷰를 새 `Aqua` 대시보드로 이전하고, 리턴·수류모터·조명·도징을
유형별 뷰로 나누어 현재 Home Assistant가 사용 가능 상태로 노출한 제어 엔티티를 빠짐없이 배치하는
것이었다.

## 사전 상태

- `Aqua` 대시보드는 존재하지 않았다.
- Pink Fam은 9개 뷰였고 `lights` 경로의 조명 뷰가 정확히 1개였다.
- 이동할 조명 뷰는 기존 전역 custom-card resource를 사용하고 있었다.
- 새 구성에 들어갈 entity reference 90개가 모두 entity registry에 존재했다.

## 수행

Home Assistant의 공식 WebSocket API만 사용했다.

1. Pink Fam 원본 구성과 대시보드 등록 정보를 읽었다.
2. 원본을 storage 외부의 비공개 백업으로 O_EXCL 생성하고 fsync했다.
3. `Aqua` storage dashboard를 생성했다.
4. `리턴 → 수류모터 → 조명 → 도징` 순서로 4개 뷰를 저장했다.
5. Pink Fam에서 조명 뷰를 제거하고 기존 조명 진입 링크를 Aqua 조명 경로로 바꿨다.
6. 두 대시보드 구성을 API로 다시 읽어 저장 예정 바이트와 canonical JSON 기준으로 대조했다.

비공개 백업은 opaque id `aqua-dashboard-before-20260918T023224.725767Z.json`, SHA-256
`5b7e13681637faf1dfd0fa5448347786a6c9b98692a90f63f028061d4da3aa0d`로 보존했다. private 절대경로와
내용은 저장소에 기록하지 않는다.

## 결과

### Aqua

- dashboard: `Aqua`, storage mode, sidebar 노출, 일반 사용자 접근 가능
- URL path: `aqua-dashboard`
- 뷰: `return-pumps`, `wavemakers`, `lights`, `dosing`
- 저장·재조회 config SHA-256:
  `c225de3d71765fbdbb18477478d703a6d504a7e53a4af87bb0c08a5d091a29ae`
- 전체 entity reference: 90
- 현재 사용 가능한 Jebao 조작 엔티티: 65/65 배치
- 누락 entity: 0
- unavailable 조작 entity: 0
- 필요한 Bubble Card·Mushroom·card-mod resource 누락: 0

장비별 일반 제어와 시계 동기화·시간표 초기화·통신 모드·도징 보정 같은 영향 큰 제어를 시각적으로
분리했다. 비활성 상태인 수류 그룹 resume 버튼과 `app_static` 메타데이터 센서는 기본 조작 화면에
넣지 않았다.

### Pink Fam 조명 이전

- 뷰 수: 9 → 8
- Pink Fam의 기존 조명 경로 참조 15곳을 Aqua 조명 경로로 변경
- 이전 경로 문자열 잔존: 0
- Pink Fam 내 `lights` 뷰 잔존: 0
- 조명 뷰 자체의 self-link 1곳도 Aqua 경로로 변경
- Pink Fam 저장·재조회 config SHA-256:
  `27ccceaf9ecfeb56e2cf39004d32c6fb904e8b6635759d64627a0a4bb75e2aca`

기존 조명 뷰의 카드 구조·entity·스타일은 그대로 보존했다. 조명 뷰에서 Pink Fam의 홈·공조·가전으로
가는 기존 교차 대시보드 탐색도 유지했다.

### 비장비 변경 확인

- 대시보드 URL HTTP 응답: 200
- 적용 시점 이후 Lovelace/Aqua 관련 Home Assistant 오류 로그: 0
- local-control operations/receipts 대상 파일 수: 전후 모두 88
- 결합 SHA-256 전후 동일:
  `34bddd55bc1d080a0d4647f11c43a475719f506f157be8cb1847de76edb19ce4`

마지막 항목은 등급 (b) preserved structured/durable daemon artifact의 불변 확인이다. 장비 raw
frame을 수집한 실행이 아니며, 대시보드 저장 자체가 장비 명령 경로를 호출하지 않았음을 구조화 기록
불변으로 확인한 범위다.

## 새로 확정된 사실

- Pink Fam 조명 뷰는 내용 손실 없이 별도 storage dashboard로 이전할 수 있다.
- 현재 HA가 사용 가능 상태로 노출한 Jebao 조작 엔티티 65개는 모두 native tile feature로 구성할 수
  있다.
- dashboard create/save와 discovery 표면 구성은 local-control journal을 변경하지 않았다.

## 후속 확인 범위

- 이번 실행은 대시보드 구성 배포이며 각 tile의 실제 장비 조작 시험은 수행하지 않았다.
- 전원·수동 투여·시간표 초기화·통신 모드는 tile feature를 누르면 실제 명령이 나가는 live control이다.
- 브라우저에서의 최종 간격·색상·모바일 배치는 운영자 확인 후 별도 UI 수정으로 다룬다.

