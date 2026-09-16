# run: 2026-09-16-observer-preview-deployment

## 요청·판정 기준

사용자는 제어가 아닌 상태 조회·미리보기 전용 우선 배포에 동의하면서, 수시로 변하거나
사용자가 바꾸는 값을 검증의 고정 기준으로 삼지 말라고 요청했다. 이번 질문은 **새 조회
서비스가 실제 현재 상태를 표시하면서 장비 설정을 쓰지 않고 기존 복구 기록을 보존하는가**다.

확인 기준은 exact source/image, 설치 세대의 내용 무결성, 물리 identity/제품 결속,
write 경로 미생성, MQTT 등록·응답, journal/receipt 보존이다. 현재 시각·AutoFlow·활성 슬롯·
Mode·TimerON·사용자가 편집한 시간표가 과거 snapshot과 같은지는 배포의 합격 조건이 아니다.
endpoint 주소도 장비 신원이 아니며 현재 검색된 endpoint를 사용한다. runtime provenance와
설정 변경을 넘어 유지되는 physical identity key는 분리돼 있다.

정적으로 `serve` observer 경로는 qualifications를 읽지 않고 coordinator/native controller를
구성하지 않음을 확인했다. `FreshStatePoller`는 현재 응답/report를 읽어 표시하며 위 변동값을
과거 값과 대조하지 않는다. 실제 읽기 불가/잠금 경합은 unavailable 진단이지 장비 동작 FAIL이나
값 보정의 근거가 아니다. 과거 collector의 slot 비교도 관측 정보이며 이번 배포 판정에 쓰지 않았다.
identity·허용 범위·기록 무결성·single-send·복구 권한 검사는 제거하지 않았다.

## 배포

- 결과: **observer HA/MQTT 배포 PASS**, 장비 제어 활성화·실장 자격 갱신은 아님.
- source: `d79289714dc90e08cfeabfb1646922d673b9f8a0`, image:
  `85a387b10671556fac3c8c2684e32a79f0c431ac50a64369c6ae54d65d3a78e5`.
- image 내부 git HEAD/clean source와 실제 import 경로를 확인했다. dependency 설치는
  기존 base 위에 새 project wheel만 `--no-index --no-deps`로 수행했다.
- software 전체 검증은 [직전 정정](../test-baseline-resolution.md)의 Linux **3153 PASS**,
  macOS **3152 PASS/1 Linux-only skip**이며 이후 제품 source/test 변경은 없다.
- 새 bundle generation: `545e6083defa6b112370345aafe7c48b`.
- runtime 시작: `2026-09-16T06:08:30.779736958Z`.
- 기존 stopped HA/MQTT container와 원 data volume은 그대로 두고 별도 observer Compose project와
  새 data volume을 사용했다. hardware-safety는 기존 fixed shared volume 그대로이며 새로운
  recovery namespace나 write 권한을 만들지 않았다. 기존 Pro collector도 정지·교체하지 않았다.
- stage는 network none, runtime은 host network이며 둘 다 read-only root/capability drop ALL이다.
  기존 topic prefix를 유지하고 현재 topology의 소유 discovery만 갱신했다.
- 실제 runtime: mode **observer**, dry-run **true**, write-allowed device **0**,
  qualification file **없음**, `--write-enabled` **없음**. scalar/schedule apply/native/group/resume
  명령 표면은 없다. 미리보기 request topic만 **11**개다.

준비 중 짧은 commit ID와 full SHA의 비교, 임시 출력 경로 거부로 각각 빌드/준비가 한 번
중단됐다. full SHA와 실제 durable 출력 디렉터리를 사용해 정정했고 해당 검사를 완화하지 않았다.
이 두 오류는 운영 기동 전이며 실패 로그를 보존했다. Compose activation은 한 번 성공했다.
새 준비 출력 폴더의 owner를 image user에서 배포 host user로 이관했을 뿐, 기존 secret·
hardware-safety·receipt의 권한/owner는 변경하지 않았다.

## 관측 결과

MQTT 감사 span은 배포 전 `06:07:27.062993Z–06:07:33.364618Z`, 배포 후
`06:09:06.437385Z–06:09:53.378089Z`다(모두 2026-09-16 UTC).

| 항목 | 결과 |
|---|---|
| native discovery | **86/86** expected JSON 일치 |
| HA MQTT entity registry | **86/86**, missing 0, disabled 0 |
| 상태 수신 | **6/6** 장비의 새로운 관측 publication, live state 189건 |
| 감사 마지막 availability | global online, device **6/6 online** |
| 장비 제어 command topic 메시지 | **0** |
| 제거 대상 owned discovery 잔존 | **0** |
| 도징 계획 API | 요청 1회·정상 비-retained 결과, `hardware_write=false`, `wire_send_available=false` |
| runtime | running, restart **0**; 기존 서비스 exited·collector running/restart0 유지 |

도징 요청은 13 mL/4회라는 오프라인 계획 입력이다. 반환 3+3+3+4와 96-byte 미리보기를 확인했지만
실제 투여, 물리 단위 검증, 장비 시간표 설치는 하지 않았다. 요청 결과를 장비 관측 상태로
출판하지 않는다. broker 등록뿐 아니라 실제 HA entity registry 반영도 read-only로 확인했으며
HA Core restart·reload·대시보드 편집은 하지 않았다.

observer 전환으로 기존 조작용 discovery 10개는 tombstone 처리되고 대응 진단 표면이 열렸다.
배포 전 소유 topic의 retained discovery 90개를 private backup으로 보존했으므로 복구 가능하다.
다른 integration의 topic은 삭제하지 않았다. 기존 config/qualification 입력과 receipt는 보존했다.

### 동시 관측의 제한

감사 창에 두 Pro가 각각 한 번 `device_write_in_progress`로 unavailable을 보고했다. 이는
기존 read-only collector가 배타 lease를 쓰는 동안 생길 수 있는 진단이며 실제 장비 write의
증거가 아니다. 이후 여섯 장비가 모두 다시 online이었다.

기동 이후 collector의 별도 보존 확인은 sample **12**개와 pair_error **1**개다. ordinal644,
`06:09:49.049248Z`의 `LocalControlLeaseBusyError`로 그 pair 표본이 빠졌으며 최신 sample은
`06:11:55.515461Z`에 완료됐다. **관측 공백 없는 연속 수집이나 잠금 경합 해결을 주장하지 않는다.**
collector를 중단하거나 lease 검사를 우회하지 않았다. HA 최신 조회와 기존 수집은 모두 유지되지만
동시 실행의 일시 unavailable/표본 누락은 남는 운용 제한이다.

## 기록 보존과 증거

`06:10:00.388606Z`의 사후 inventory는 준비 직전과 다음 값이 일치한다.

- journal: **1996 files**, `b31ee4182ef92413e35041f4e9229396fbf35bcd4549e996414df3e2ec5bf343`.
- receipt: **58 files**, `025af2428b0b38cac79e6bb6cfa0b9489a23b307c9fbb448858a6b418211ef3f`.
- 기존 config: `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`.
- 기존 일일 source: **RECOVERY_REQUIRED/revision79** 그대로. 원 Async ACK/전달 UNKNOWN과
  일반 write 차단은 종료시키지 않았다.
- stable role binding: `901a1c58fbbd1ea5cdbc4763988d9e3cf2dec42729449035a6cf0685d261e8dd`.

이 결과와 observer의 writer 미구성 경로로 이번 배포의 **장비 설정 write 0**을 확인한다.
장비의 변동 설정이 과거와 같은지로 이를 추정한 것이 아니다. 신원·schema는 고정된 이번
설치 세대의 결속이며, 시각이나 사용자 설정의 영구 불변성을 의미하지 않는다.

증거 등급은 **(b) 보존된 broker/registry/runtime 구조화 출력**이다. 이 배포 감사는 원본 장비
wire frame을 별도로 보존하지 않았으므로 (a)나 물리 유량/위상/투여의 검증으로 올리지 않는다.
opaque artifact id: `observer-deploy-20260916.FoLSM3`.

| 보존 산출물 | SHA-256 |
|---|---|
| `mqtt-before.json` | `d6cce041de17404360fb9d4e55b3296b9fb2c9ed2609848e47942fb4eb1376df` |
| `mqtt-after.json` | `f2fcde80b980c29bc90d477fcbbed10e34adda318fe280d22d41013a6207cf1f` |
| `ha-registry.json` | `761b7e8823437c51e72e7fb0f1b8f237165d4acab657fe9eef1303428582067a` |
| `integrity-after.json` | `647db6a99952ed7136b289e29e68068b6f0ecbba28a60f2287848e7fa10a97c6` |
| `runtime-inputs.json` | `8887fd753e81c4f70b721557cc6b49b716b55e0fb4a06f2ae2265658bceba560` |
| `collector-poststart.json` | `0830c3683a4c6d72e58c26b99bafe53c21939339e73c43b7628aaf448aebe59d` |

**새로 확정된 사실:** 새 제품의 조회·미리보기 API와 HA 등록이 실제 서버에서 동작하고,
장비 설정 쓰기 없이 기존 journal/receipt를 보존하는 observer 배포가 성립했다. 기존 제어
자격 보존 배포, 현재 Pro 미완료 operation 해소, 나머지 live 기능 수용은 여전히 별도다.
