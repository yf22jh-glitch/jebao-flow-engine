# run: 2026-09-16-bar-schedule-qualification

- 질문: Bar full-image 시간표 A/B를 쓰고 임의 원384B image를 그대로 복원할 수 있는가.
- 승인: `ef4a1d6`의 Constant40/39 A/B·원384B 복원, 새 generation 실행 범위 `d1f020c`.
- source `d1f020c34b9c70513cbfe249b59bab39fdb2ce45`, image
  `2fe95ce8a277b5fd7cad7bf661edbd5e5fb972307251c06ea784dec8541a9a08`.
- schema `0cb054233e2a5691463f0f7b3812cc87f8536e935755f0e3c756f142bb9797fc`.

직전 timer operation의 원본 복원·terminal을 확인한 뒤, 같은 Bar OBSERVER 입력으로
`bar_schedule_qualification_d1f020c_01`을 한 번 실행했다. 각48×30분 Constant40 image A,
Constant39 image B, 자기 원384B raw 복원의3 action이며 TimerOFF를 유지했다.
UTC 09:14:53.343517–09:15:04.841004, exit0, 3send/3ACK, VERIFIED/rev10, receipt2장.
재시도·미확인·복구 실행0이다.

09:16:38.745422 UTC의 별도 fresh explicit reply를 직접 decode했다. 최초9controls와 모두
일치하고 TimerOFF, 원384B image가 byte-exact다. SHA:
`8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea`.
시각·Auto·활성 슬롯 snapshot은 원복 gate가 아니다. 원 Random100 시간표는 켜지 않았다.

## 보존·운영 오류 구분

- opaque artifact: `bar-timer-renewal-20260916-01/schedule-evidence`.
- archive SHA: `310e71541ad6e3d6f42e7775d20f996312bfbfbdd72b81f65455c2e0fc818233`.
- raw UTC span: 2026-09-16 09:12:29.278862–09:16:38.745422.
- identity SHA: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- journal SHA: `ff59b3866ed947fc93a906ea0f26a7f4710c42978af647c3e51571411cdf06ee`.
- (a) before/after wire·status·384B. (b) 두 operation의 journal/blob 및 timer/schedule 실제
  receipt4장과 binding. archive60파일, blob30개의 내용 SHA를 직접 대조했다.
- 사후 read-only capture 컨테이너는 exit0으로 raw를 보존했지만, 실행 중 wrapper 파일을
  갱신한 운영 실수로 바깥 shell이 뒤에서 잘못된 조각을 읽어 exit127을 냈다. 컨테이너 종료 코드,
  로그와 raw archive를 직접 대조했다. 장비 명령이나 capture를 재시도하지 않았다. 이후 wrapper는
  별도 버전 경로·고정 SHA·읽기 전용으로 보존하고 실행 중 교체하지 않는다.

**새로 확정된 사실:** 이 Bar binding의 TimerOFF·independent 조건에서40/39 full-image write와
원384B exact restore, 실제 자격2장 발급이 성립했다. TimerON 상태의 image 변경이나 모든 slot
조합 수용은 주장하지 않는다. HA executable 승격·배포는 이 기록 뒤 별도 단계다.
