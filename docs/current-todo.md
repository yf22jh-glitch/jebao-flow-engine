# Jebao 현재 작업 To-do

마지막 갱신: 2026-09-11 14:22 KST

> 이 파일은 진행에 따라 갱신하는 운영 체크리스트이며 `docs/runs/`의 append-only 실기 기록이
> 아닙니다. run record와 별도 커밋으로 관리합니다.

## 목표

자격이 확인된 Pro 한 대의 `manual_flow`만 current local-control에 노출하고,
MQTT 경유 `40 -> 39 -> 40` 실기와 exact readback·journal 원복을 확인한다.

## 범위

- Jebao 작업만 진행한다.
- Home Assistant 업데이트·재부팅·Core 조작은 하지 않는다.
- 사용자 앱 조작이 새로 필요하면 그 지점에서만 요청한다.
- frozen native ASYNC 하네스, old daemon·old MQTT 경로는 건드리지 않는다.
- Claude는 독립 정적 검토 역할을 유지한다.

## 현재 상태

- [x] 기존 observer 1개 실행 중
- [x] 장비 6/6 관측 상태 유지
- [x] HA 업데이트·재부팅 0
- [x] 선택 Pro `manual_flow` qualification 완료 (`40 -> 39 -> 40`, receipt 2장)
- [x] MQTT 실기 `40 -> 39 -> 40` 완료, 각 command exact readback·terminal journal 확인
- [x] 최종 Flow 40 및 observer-only 배포 복귀
- [x] Claude 1차 독립 검토 수신·반영

## 완료

- [x] startup 경로의 자동 device write 0 정적 확인
- [x] command surface가 qualified physical identity + function 쌍으로 제한됨을 확인
- [x] control config: write 허용 장비 1대, qualified control 1개(`manual_flow`), group command 0
- [x] observer rollback Compose를 별도 generation으로 준비
- [x] control Compose를 별도 generation으로 준비
- [x] control/observer가 같은 data volume과 같은 hardware-safety lock volume을 쓰는지 확인
- [x] 실패 복구값을 명령별로 분리
  - 첫 명령 `40 -> 39` 실패: recovery 기대값 `40`
  - 둘째 명령 `39 -> 40` 실패: recovery 기대값 `39`; 자동 forward retry 없음
- [x] reviewed source image를 Compose tag에 정확히 결속
- [x] 시작 직전 fresh preflight: observer 1, old daemon 0, unfinished journal 0, 6/6 fresh
- [x] 기존 observer stop-first 후 control service 단독 기동
- [x] 시작 직후 startup device write 0과 MQTT command topic 1개 확인
- [x] MQTT command 1: `40 -> 39`, terminal journal + exact readback 확인
- [x] MQTT command 2: `39 -> 40`, terminal journal + exact readback 확인
- [x] 최종 observer-only 복귀 및 Flow 40·6/6 fresh·command surface 0 확인

## 진행 중

- [ ] 다음 Jebao-only 단계의 current contract·precondition 확인

## 다음 순서

- [x] append-only `docs/runs/` 기록 작성
- [x] Claude 최종 독립 검토
- [x] Claude 필수 정정 반영본 재결속
- [x] run record 커밋 (`2f23343`)
- [x] To-do를 run record와 분리해 저장소 문서로 관리
- [ ] 다음 Jebao-only 단계의 current contract·precondition 확인
- [ ] 다음 단계 실행·검증·기록

## 실패 시 행동

- 첫 명령 실패: forward retry 없이 해당 request recovery, 40 확인
- 둘째 명령 실패: forward retry 없이 해당 request recovery, 39 확인 후 observer 원복 및 보고
- identity·범위·journal·rollback 관련 실제 P1 또는 물리 위험이면 즉시 중단
- 단순 진단/비안전 과도 조건은 새 gate로 키우지 않는다

## 사용자 조작 필요 여부

- 현재: 없음
- HA 재부팅/업데이트: 예정 없음

## 진행 로그

- 13:32 KST — 최초 To-do를 저장소 밖 숨김 경로에 생성.
- 13:36 KST — 두 Compose image의 installed Python 131-file tree가 reviewed source와 exact 일치함을 확인.
- 13:50 KST — fresh preflight PASS: device 6/6, absent·ambiguous 0, target Flow 40, identity·template·role·qualification exact, unfinished journal 0, group supervisor armed 0.
- 13:53 KST — observer stop-first 후 reviewed control service 1개 기동. startup 전후 journal tree byte 동일로 startup write 0 확인.
- 13:54 KST — broker read-only 확인 PASS: global online, device 6/6, target state 40, command topic 1, retained command 0, group armed 0.
- 13:56 KST — MQTT command 1 PASS: 40→39, physical write 1, transaction/action VERIFIED, fresh readback 39, unfinished 0.
- 13:58 KST — MQTT command 2 PASS: 39→40, physical write 1, transaction/action VERIFIED, fresh readback 40, unfinished 0.
- 14:01 KST — control 종료·observer 복귀 전후 journal byte 동일. reviewed observer-only 1개, restart 0, write flag·qualification 0.
- 14:02 KST — 최종 observer 확인 PASS: device 6/6, command surface 0, strict fresh read Flow 40, absent·ambiguous 0, unfinished 0.
- 14:08 KST — 사용자 요청에 따라 To-do를 저장소의 `docs/current-todo.md`로 이전. 이후 이 파일만 갱신.
- 14:12 KST — 두 terminal journal과 referenced blob을 fresh process에서 재확인하고 append-only MQTT control run record 초안 작성.
- 14:18 KST — Claude 독립 검토 완료: P1·물리 위험 0. journal-tree 증거등급과 recovery write 미검증 문언을 보강하고 run record와 To-do 커밋을 분리.
- 14:21 KST — 보강본을 Claude가 재계산해 필수 finding 0·랜딩 GO 확인. run record와 index를 commit `2f23343`으로 랜딩.
- 14:22 KST — 현재 MQTT control 실기 slice 마감. 다음 Jebao-only 단계의 contract·precondition 확인으로 이동.
