# 2026-10-01 전일 수류모터 일정 읽기 전용 감사

## 질문·범위

- 질문: 2026-09-30 KST에 저장된 GMP·MCP 시간표의 전환이 실제 보존 결과와 일치했는가.
- 관측 구간 UTC: `2026-09-29T15:00:00Z`–`2026-09-30T15:00:00Z`.
- 기존 실행기의 durable operation·보존 read-back 프레임과 HA 전체 attribute history를 읽었다. 새 장비 명령, 일정 활성화·재개·일시정지 요청은 0회다.
- upper identity SHA-256: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- lower identity SHA-256: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`.
- GMP 활성 plan SHA-256: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.

## 보존 증거와 결과

- GMP 31구간 중 verified parent operation은 27개다. `00:00`, `05:57`, `06:03`, `06:57`의 전환 기록은 없어서 전일 전체 정상 운전으로 판정하지 않는다.
- `07:03` 구간은 `07:30:44`–`07:31:26` KST에 뒤늦게 적용됐다. `07:34`에는 `ProtocolTimeout`으로 일정이 일시정지됐다. `07:45:23`–`07:45:46`의 재개는 현재 행 확인으로 종결되어 해당 재개의 장비 write는 0회였다.
- `08:40`–`21:00`의 이후 26개 경계는 verified다. 저장된 Mode·Flow·Frequency와 마지막 read-back이 일치했다. 이 구간의 Feeding 진입·복귀 7쌍도 포함한다.
- 27개 verified pair의 마지막 read-back 54개 프레임(각 462 bytes)을 실제 보존 파일에서 확인하고 디코딩했다. 비교 순서로 연결한 프레임의 SHA-256은 `55cfce2bcc01d1bcbde7ccbb1327e2918c75611e1a1bb58a0f35f6dcb022561c`다. raw 파일은 private 저장소에 남기고 이 저장소에는 복사하지 않는다. 논리 artifact group: `pro-manual-final-readbacks-20260930`.
- 이 프레임의 역할·전원·Timer·fault 비교에서 기대 역할, 전원 ON, 장비 Timer OFF, fault 0을 확인했다. 이는 보존 read-back 시점의 증거 등급 (a)이며 그 사이의 모든 순간을 증명하지 않는다.
- MCP는 당일 시작·종료의 장비 시간표 31구간이 동일했고, 각 구간에 Mode·Flow·Frequency가 맞는 Auto 보고를 확인했다. 경계 주변 일시적인 불일치 표본 2개가 있었으므로 즉시 전환·지연 0초를 주장하지 않는다. 전체 HA attribute history에는 보고된 fault 재발이 없다. HA history는 원본 무선 프레임이 아니며 누락 없는 물리 관측으로 취급하지 않는다.
- operation의 verified 상태와 타이밍은 보존 structured/durable daemon artifact의 주장(등급 b)이다. 마지막 프레임 확인과 구분한다. 단순 UI 관찰로 등급을 올리지 않았다.

## 감사 시점의 현재 상태

- 2026-10-01 `04:20:57` KST에 idle 확인 중 `ConnectionResetError`가 기록됐고 GMP 실행기는 paused다. 현재 active 파일 SHA-256은 `6be9bcdf724354319d211f5cb70dd587b366572c035434071619670394e1c05c`다.
- 이번 감사는 원인 수정을 하거나 일정을 재개하지 않았다. 전일 정상 구간과 현재 paused 상태를 분리해 보고했다.

**새로 확인한 사실:** 전일 후반의 보존 GMP 26경계와 MCP 31구간의 일치 표본은 확인됐지만 GMP 전일 전체는 정상으로 판정할 수 없다. 일정 추가·UI 명칭 작업과 별개로 현재 pause는 유지한다.
