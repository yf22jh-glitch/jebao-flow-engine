# Pro 일일 관측 로그 저장공간 이전

## 목적과 범위

사용자가 Docker 이미지 정리와 후속 수류모터 기능 구현을 요청한 뒤, 먼저 로그를 별도 저장공간으로
옮기도록 지시했다. 처음 지목한 경로를 사용자가 정정했고, 정정된 별도 볼륨으로 이전했다.
잘못 지목된 위치에는 준비 스크립트와 이미지 백업만 만들었으며 운영 로그는 넣지 않았다.
두 준비 파일의 새 사본을 대조한 뒤 원 사본과 빈 디렉터리를 삭제했다.

제품 source 기준은 `0d192db`, 실제 수집 이미지는 기존
`78c6b712a1186229ec74148a6bb458a85f33118c519608bc48d606d7b2f06fd5` 그대로다.
장비 write·원복·역할 전환·clock 변경·Bar 접근·HA/MQTT 서비스 교체는 하지 않았다.
[원 운영 기록](2026-09-15-pro-daily-async-master-cycle.md)의 Async 전달 UNKNOWN과
미완료 source·일반 write 차단은 이번 저장공간 이전으로 닫지 않는다.

## 보존 및 수집 연속성

- 기존 collector의 지원된 STOP 표식으로 현재 pair 저장 뒤 정상 종료했다. exit0,
  종료 시각 `2026-09-16T00:47:11.199391452Z`, pair 시도 ordinal944다.
- 종료된 `overnight-01`의 **5,665개 파일 / 논리 4,191,586바이트**를 복사했다.
  모든 파일의 SHA-256·크기와 파일 집합을 전수 대조하고 새 파일을 fsync했다.
  파일 개수는 성공 pair 개수나 물리 프레임 개수가 아니다.
- 원 collector의 stdout/stderr도 별도 보존했다. 새 collector가 첫 pair를 저장하고 그
  wire/status 해시가 일치한 뒤에만 이전 로그 사본을 지웠다. 복사본은 남아 있어 복구 가능하다.
- 새 segment `overnight-02`는 `2026-09-16T00:48:03.46957297Z` 시작했다.
  ordinal은 새 segment의1부터이며, metadata가 이전 segment를 명시한다.
- 이전 마지막 sample 완료 `00:46:47.049411Z`와 새 첫 sample 완료 `00:48:10.502687Z` 사이
  간격은 **83.453276초**다. 이 창은 수집 공백이며 연속 감시로 주장하지 않는다.
- 30초 cadence와 원래 종료 시각 **2026-09-16T16:54:49.869729Z**는 유지한다.
  재시작 시점부터 새24시간으로 연장하지 않았다. 종료 시 자동 원복도 없다.

새 container는 출력 디렉터리를 별도 볼륨의 bind mount로 사용하고, 기존 data 볼륨은
read-only로 마운트한다. 기존 hardware-safety lease 볼륨은 그대로다. Docker stdout log driver는
`none`이고 stdout/stderr는 새 로그 디렉터리에 직접 기록한다. 따라서 이 collector의 raw·요약·
stdout/stderr는 이전 Docker 저장공간에 계속 증가하지 않는다. 다른 서비스 로그와 Docker daemon
저장공간 전체를 이전한 것은 아니다. 중단된 이전 container의 Docker 로그 사본은 보존했다.

## 이전 후 관측

첫 pair의 원본 파일과 metadata 해시를 대조했다.

| 역할 | 완료 UTC | 출처 | 보고된 현재 값 |
|---|---|---|---|
| 위 master | 00:48:10.502687 | reply3 | master, TimerON, Pulse / AutoFlow40 / AutoFreq70 |
| 아래 slave | 00:48:10.795236 | report4 | async_slave, TimerON, Pulse / AutoFlow30 / AutoFreq70 |

양쪽 저장 image가 원 daily plan과 일치하고 해당 device-local 08:00–10:00 슬롯 Flow와 일치했다.
슬레이브의 수동 `Flow31`·`Mode feed`를 현재 Auto 값과 혼동하지 않는다. report는 요청 상관 reply가
아니며 물리 유량·위상·생물 안전을 측정하지 않았다. ordinal5의 새 로그도 00:50:10–11Z에
같은 현재 Auto 값을 기록했다. 그 시점 restart0·stderr0B였다. 하루 전체 PASS는 아니다.

수집·decode·slot 비교·원본 저장 함수5개는 이전 스크립트와 AST 동일임을 확인했다.
변경은 출력 segment 및 원 종료 시각을 받는 private runner의 실행 경계뿐이다.
문법 컴파일을 확인했으며 제품 source/test 변경이나 전체 pytest 실행은 없었다.

## 산출물

실제 주소·장비 식별자·private 절대경로는 별도 보존한다.

- opaque artifact: `pro-daily-storage-migration-01`
- 이전 사본 전수 manifest SHA: `701624018a20395a506e2c3c1d2761b6e2058f6aba71d5b4ac3e160f27ccbdb6`
- 완료 manifest SHA: `2f4525d6df5ce0d2f6e1dd504d8da7d1e16efcdd6ea7e58a1dc6520df0b07cd0`
- 후속 collector SHA: `290b699e62e3457d733e95143f1f7cecc225b251cb37947daad90b35042cde26`
- 위 identity SHA: `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
- 아래 identity SHA: `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`

## 함께 수행한 이미지 정리

모든 container의 실제 image id와 대조해 참조0인 Jebao 이미지2개만 대상으로 삼았다.
사용 중·복구용·실기 증거 container와 다른 서비스 이미지·볼륨·공용 build cache는 보존했다.

- `jebao-native-sync:f0cced4`, image `f233978b17938c30334c3b3a3e1f3880099e45e92369acd38758a4d9f8578c9e`
- `jebao-flow-engine:local-diagnostics-269e92b-ef939347`,
  image `847c51edb112abb9717c7a577666d5d46842dc78e3a470e40e8386b96ba82d0c`
- opaque backup: `unused-jebao-images-20260916-01`
- archive SHA: `74c48e151caffc3017e9da6eeb960883e4dda4618363881d8ee7c35da492d310`

Docker save archive의2개 config id와 모든33개 content-addressed blob을 대조한 후 force 없이
위2개 tag/image를 제거했다. 백업으로 정확한 image를 다시 load할 수 있다. Docker 표의 image
개수는58→56이나 표시 총량은12.34GB로 동일했다. 공유 layer/cache가 남으므로 두 이미지의
표시 크기를 합해 확보한 공간이라고 주장하지 않는다. 공용 build cache4.019GB는 정리하지 않았다.

**새로 확정된 사실:** 기존 raw 파일 전량 보존을 확인한 저장공간 이전과 별도 볼륨의 후속
읽기 전용 수집이 성립했다. 새로운 수류모터 기능 구현·장비 자격·서비스 배포 완료를 뜻하지 않는다.
