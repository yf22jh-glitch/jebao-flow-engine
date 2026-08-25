# VorTech 계열 운전 패턴 대응

EcoTech Marine의 공식 VorTech 제품 설명에 있는 모드를 Jebao Flow Engine의 안전 원칙에
맞춰 고수준에서 모사합니다. 명칭과 동작 설명을 참고하지만 EcoTech의 펌웨어나 코드를
사용하는 구현은 아닙니다.

공식 설명: <https://ecotechmarine.com/vortech/>

| VorTech 동작 | Jebao Flow Engine | 구현 방식 |
|---|---|---|
| Constant Speed | `constant` | 고정 출력과 펌프별 gain |
| Lagoon | `lagoon` | 낮은 범위에서 느리고 부드러운 결정적 랜덤 변화 |
| Reef Crest Random | `reef_crest` | 높은 범위에 치우친 빠르고 큰 결정적 랜덤 변화 |
| Gyre | `gyre` | 펌프 phase에 따른 장주기 방향 교대 |
| Tidal Swell | `tidal_swell` | 혼돈→잔잔함→상승→청소 surge 후 주기마다 우세 방향 반전 |
| Nutrient Transport | `nutrient_transport` | 전반부 부유 파동, 후반부 오버플로 방향 surge |
| Short Pulse | `native` 예정 | 0.2~2초 펄스는 Jebao 내장 Mode/Frequency로만 실행 |
| Feed / Night | 특수 운전 모드 | 이전 상태 저장·복원 및 출력 상한 적용 |

보유한 Local Wavemaker Pro의 제품 스키마에는 `pulse`, `sine`, `constant`, `random`,
`tidal`, `nutrient_transport`, `circulation`, `feed`, `custom` 모드가 정의되어 있습니다.
따라서 Pro 모델에서는 짧은 파동을 장비 내부에서 만들고 그룹 엔진이 장기 envelope와 펌프
간 위상을 조정할 수 있습니다. 모드 번호와 payload 생성까지 오프라인 검증됐지만 실제
장비가 각 write를 적용하는지는 아직 검증하지 않았습니다.

## 안전 경계

- `lagoon`, `reef_crest`의 `period_seconds`는 외부 출력 목표가 바뀌는 시간 단위입니다.
  기본적으로 5초 이상을 사용합니다.
- `gyre`는 수십 초에서 수시간 주기를 사용합니다.
- `tidal_swell`은 분 또는 시간 단위의 전체 사이클로 사용합니다.
- `nutrient_transport`는 그룹의 장기 envelope만 계산합니다. 짧은 파동은 제품별 네이티브
  모드 매핑을 실기 검증한 후 장비 내부에서 생성합니다.
- 서버에서 0.2초 단위 ON/OFF나 출력 쓰기를 반복하지 않습니다.
- 이 모드들은 VorTech의 물리 구조, 유량 특성 또는 무선 동기화 펌웨어를 복제하지 않습니다.
