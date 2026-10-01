# GMP Custom Wave: 앱 항목과 내부 필드 대응

2026-10-01에 설치된 Jebao Aqua 앱의 보존 Pro 화면 JavaScript 3종을 읽었다.
장비·앱을 조작하거나 펌웨어의 물리 동작을 시험한 결과가 아니라 UI 연결 코드의 확인이다.

| 앱 화면 | 수동 상태·송신 필드 | 장비 시간표 보고 | 외부 시간표 JSON |
|---|---|---|---|
| Run (%) | `Frequency` | `AutoFreq` | `frequency` |
| Stop (%) | `Cust_Wav_Freq` | `Auto_Cust_Wav_Freq` | `custom_frequency` |

- Custom Wave에서 Run과 Stop은 각각 5–100의 슬라이더다. 앱 표시 단위는 `%`이며 초로 변환하지 않는다.
- 앱의 운전 구간 편집은 Run을 `frequency`, Stop을 `wavFrequency`로 보관한다.
- 앱이 저장하는 9-byte 장비 시간표 슬롯에서 Run은 index 6, Stop은 index 8이다.
- 수동 Stop 변경 콜백은 `Cust_Wav_Freq`를 쓴다. Run과 Stop을 서로 교환하지 않는다.
- Pulse·Sine·Random·Cross-flow에서 같은 Frequency 필드를 사용하는 표시는 Frequency로 유지한다. 다른 모드의 값을 일괄 Run으로 이름 바꾸지 않는다.
- Run 40·Stop 20 요청은 `frequency: 40, custom_frequency: 20`으로 보관한다. 이것으로 40초/20초 운전이나 실제 정지 시간을 확정하지 않는다.
- HA entity ID·native tile feature·기존 actuator 송신 코드는 변경하지 않는다. 명칭은 카드·요약·팝업에서만 맞춘다.

확인한 앱 화면 자산 SHA-256:

- `221f8b5cfaf0584378249805e9ff7fe769700b0e5ddaf8d7b32758e018989bbe`
- `c1ed7f76a979b7e1176a20306d0cfb8385461305bc7b0fdf9d396781f0cfc1b5`
- `bcb962a5cf7cd49bf59b4a67526d02d5b06b047f366c18a53eb2521b0c892f7d`

앱 원본 자산·private 절대경로·기기 식별값은 저장소에 넣지 않는다.
