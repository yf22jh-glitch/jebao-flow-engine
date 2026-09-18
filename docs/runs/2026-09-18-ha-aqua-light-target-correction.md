# 2026-09-18 HA Aqua 조명 대상 교정

## 범위

- 실행 시각: `2026-09-18T03:38:12Z`
- source 기준: `ff3d5e2`
- 변경 대상: Home Assistant storage-mode Lovelace 구성
- 장비 write: `0`
- `src/` 변경: `0`
- Home Assistant `.storage` 직접 수정: `0`

앞선 실행은 Pink Fam의 `lights` 집 조명 뷰를 Aqua로 옮겼다. 사용자가 원한 대상은 Pink Fam의
`k7-light` 경로, `k7:np-logo` 아이콘과 `custom:noopsyche-k7-card`를 사용하는 해수어 조명이었다.
이번 실행은 최초 백업의 집 조명 뷰를 Pink Fam에 복원하고 NP 조명만 Aqua로 옮겼다.

## 사전 식별

Home Assistant WebSocket API로 대시보드·entity registry·device registry·area registry를 읽어
`NP`, `reef`, `marine`, `해수` 표시를 검색했다.

- dashboard match: Pink Fam `k7-light`의 `k7:np-logo`
- view title: `K7 조명`
- view type: `panel`
- card: `custom:noopsyche-k7-card`
- entity: Noopsyche K7 operating-mode select 1개
- source view SHA-256:
  `be2569f636edbe9fceb6e213cd71361cc16b0be15dd45d4a2fc463f2a4ff0d1b`

동명이인이나 유사 조명을 추측하지 않고 이 exact view를 대상으로 고정했다.

## 수행

1. 최초 실행 전 Pink Fam 백업의 digest를 다시 검증했다.
2. 최초 백업에서 `lights` 집 조명 뷰를 복원 대상으로 가져왔다.
3. 현재 Pink Fam의 NP 뷰가 최초 백업의 `k7-light`와 byte-equivalent인지 확인했다.
4. 교정 직전 Pink Fam·Aqua 구성을 새 비공개 백업으로 O_EXCL 생성하고 fsync했다.
5. Aqua `lights` 뷰를 NP 카드로 교체했다. 탭 경로와 분류 제목만 `lights`·`조명`으로 두고,
   NP 아이콘·panel·custom card·entity는 그대로 보존했다.
6. Pink Fam은 최초 백업 기준으로 집 조명을 복원하고 NP 뷰만 제거했다.
7. 두 dashboard를 API로 다시 읽어 canonical JSON digest와 구조를 대조했다.

교정 직전 비공개 백업은 opaque id `aqua-dashboard-before-20260918T033812.465818Z.json`, SHA-256
`864703497fb14d723d5481bdb4e6386b9233215a4c5f87a9bac2337934448a25`로 보존했다. private 절대경로와
내용은 기록하지 않는다.

## 결과

### Pink Fam 집 조명 복원

- 복원된 집 조명 view SHA-256:
  `13b566012de232204943b2ad18bf3e36a2cac27d638f7eac40879c2aa7b56fa4`
- 최초 백업의 집 조명 view SHA-256과 동일
- 집 조명 navigation: 16곳
- `k7-light` view 잔존: 0
- 전체 Pink Fam config SHA-256:
  `c8a69751a7c0aa5aa89623e06005d58d8cc76103864c4b0243507e0ddf3abe98`

### Aqua 해수어 조명

- view path/title: `lights` / `조명`
- icon: `k7:np-logo`
- type: `panel`
- card: `custom:noopsyche-k7-card`
- 집 조명 `light.*` entity 잔존: 0
- 전체 Aqua config SHA-256:
  `2765b63d4bc7ab37c384ab1ef5a4b748761603e0eb09176710826d1fb554f58b`
- entity reference 누락: 0
- unavailable reference: 0
- 기존 Jebao 사용 가능 조작 엔티티 누락: 0
- URL HTTP 응답: 200
- 적용 이후 Lovelace·Noopsyche·Aqua 관련 오류 로그: 0

### 비장비 변경 확인

- local-control operations/receipts 대상 파일 수: 전후 모두 88
- 결합 SHA-256 전후 동일:
  `34bddd55bc1d080a0d4647f11c43a475719f506f157be8cb1847de76edb19ce4`
- 장비 write: 0

마지막 검사는 등급 (b) preserved structured/durable daemon artifact의 불변 확인이다. 장비 raw
frame을 수집한 실행은 아니다.

## 새로 확정된 사실

- Pink Fam에서 `NP`로 표시된 해수어 조명은 `k7-light` Noopsyche K7 panel이다.
- 집 조명 뷰를 최초 백업과 동일하게 복원하면서 NP panel만 Aqua로 분리할 수 있다.
- 교정 후에도 Aqua의 리턴·수류모터·도징 제어 집합은 누락 없이 유지됐다.

