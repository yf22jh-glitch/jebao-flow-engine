# run: 2026-09-16-bar-timer-qualification

- 질문: 앱 standalone 명령으로 bounded Bar timer ON/OFF를 수행하고 TimerOFF와 자기 원본을
  복원한 뒤 실제 응답에 결속된 자격을 발급할 수 있는가.
- 승인: 사용자 현장 감시·차단 질문에 대한 동의, 범위 기록 `d1f020c`.
- source: `d1f020c34b9c70513cbfe249b59bab39fdb2ce45`.
- image: `2fe95ce8a277b5fd7cad7bf661edbd5e5fb972307251c06ea784dec8541a9a08`.
- schema: `0cb054233e2a5691463f0f7b3812cc87f8536e935755f0e3c756f142bb9797fc`.
- Linux offline suite: 3,189 PASS. 시뮬레이션은 실기 수용의 대체 근거가 아니다.

## 실행·원복

기존 Pro HA 서비스를 09:12:22.836444 UTC에 중지했다. 별도 OBSERVER 입력은 Bar만 쓰기
허용, bounds30..50, trial40이다. 운영 config·기존 영수증은 바꾸지 않았다. 미완료0과 현재
safety-volume identity를 확인했다. fresh explicit reply의 baseline은 ON/TimerOFF/independent/
Constant40/F80/Feed10, FeedSwitch=false/PulseTide=false였다.

`bar_timer_qualification_d1f020c_01`, UTC 09:12:48.121384–09:12:49.535051:
48×30분 Constant40 시간표 → standalone TimerON → app TimerOFF → 원384B raw 복원.
4 action 모두 ACK/VERIFIED, exit0, source VERIFIED/rev13. ON/OFF만 receipt2장을 발급했다.
임시 image 두 액션은 schedule 자격으로 세지 않았다. 불확실·재전송·복구 실행0이다.

09:13:22.313753 UTC의 별도 explicit raw를 직접 decode하여 최초 raw와 비교했다.
**원9controls 전부 일치하고 원384B image가 byte-exact이며 TimerOFF**다.
image SHA:
`8c933a4d9d13763fa7740dc8cf0cebb1915cd2d48cbbe91ecc9c117d80f767ea`.
Inactive Auto 진단은 초기 random/Flow100/Freq100 → 사후 constant/Flow40/Freq100이다.
ON seed Frequency0과 사후 AutoFreq100은 같지 않으며, 이를 실패 gate로 만들거나 Auto* 전체가
요청대로 적용됐다고 주장하지 않는다. 현재 슬롯·시각·Auto snapshot은 원복 비교에서 제외했다.
비활성 Auto 잔여값은 바뀐 채 남았으며 소유·복원 대상이 아니다. 따라서 모든 상태의 원상복구를
주장하지 않는다. 원 Random100 시간표는 켜지 않았다.

## 증거와 한계

- opaque artifact: `bar-timer-renewal-20260916-01/timer-evidence-v2`.
- archive SHA: `e4779e2e4a44e5d336dc9ba906798af9ca77f5f5efef030cd0a6bd33af0564ed`.
- raw UTC span: 2026-09-16 09:12:29.278862–09:13:22.313753.
- identity SHA: `d03fd399c056628fd9b9380dd8203166d4c17e472d838d40086fabdb24064ea1`.
- journal SHA: `1f8459d2761f15e5b9e077f0526a9e47d370879d0b3327b4eba0973c6dc5e73c`.
- (a) before/after 원 wire·status·384B image. (b) journal, 연결13blob, 실제 receipt2장,
  candidate binding 및 preparation digest 보고서. archive31파일의 blob/receipt SHA를 재계산했다.
  최초 export에는 receipt 실체가 빠져 있었고 v2에 추가했다. 최초 archive도 보존했다.
- 중간 ACK·검증은 durable journal 주장이다. 전 구간 wire나 물리 유량 계측을 보존한 것은 아니다.
  네 ACK body는 모두 빈 바이트다. ON receipt의 검증 범위는 TimerON/FeedSwitch/Linkage,
  OFF는6개 persistent 필드이며 Auto* 수용 증명이 아니다. Claude는 보존 baseline에서
  네 forward payload를 독립 재구성하고 실제 receipt 바이트·phase 귀속·원복을 대조했다.

**새로 확정된 사실:** 이 Bar binding과 bounded Constant40 단회에서 timer ON/OFF의 persistent
검증·자격2장 발급 및 자기 원본 복원이 성립했다. 모든 시간표·값의 수용, 물리 파형·유량·안전은
확정하지 않았다. 이 시점 HA 노출은 아직이며 별도 schedule 자격과 Pro renewal 뒤 배포한다.
