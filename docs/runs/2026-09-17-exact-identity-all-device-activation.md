# run: 2026-09-17-exact-identity-all-device-activation

## 승인·질문

사용자는 legacy qualification receipt와 활성 template을 제어 노출 조건으로 사용하지 않고,
비공개 설정에 결속된 정확한 물리 identity만으로 **보유 6대 전부의 구현된 제어 표면을
배포·활성화**하도록 승인했다. 이 배포에서 닫을 질문은 다음이다.

> 정확한 identity에 결속된 전 기종 제어 표면을 기존 영수증·template 입력 없이 배포해도,
> 기동 중 장비 명령을 보내지 않고 Home Assistant에 완전한 표면을 게시할 수 있는가?

배포 자체에서 장비 제어 명령은 승인하지 않았다. 특히 Aquarium 메인 리턴은 현재 상태를
유지하며 조작하지 않는다. 실제 버튼·값 시험은 이후 장비와 기능을 하나씩 골라 별도 승인 아래
수행한다. 동결된 native ASYNC 실험 하네스는 수정하거나 실행하지 않았다.

- source `ce4f80f399adce513aa4ff83437765d6341a0a1c`.
- source archive SHA `36d45060c74fc1682c296d60b1b88beb016a63af7fe727a112f7c800b1b9823f`.
- runtime image `sha256:4ece904bf877b178e9b3662037d69fcde048cb3d3e1635cef884b6f8aff3ee1a`.
- protocol schema `b6ac93d8613706ebdc9bc282ea5c419948d927ec1826d203ec59b7e2ebd0188a`.
- compose generation `63345b81c6e1795121a176e71d4bbfe1`.
- 관측 구간: 2026-09-17 02:33:13.542463–02:40:27.916611 UTC.

## 배포된 제어 계약

private config의 각 장비는 정확한 물리 identity와 허용 function 집합을 함께 갖는다. 운영 입력에는
qualification 파일과 template root가 모두 없으며, 과거 138개 receipt는 삭제하지 않았지만 현재
제어 노출 판정에 사용하지 않는다. 제품별 identity authorization은 Aquarium 8, Dosing 8, DC 10,
Bar 11, Pro 각 13기능이다.

이번 source에는 앞서 비어 있던 Dosing 송신 경로가 포함됐다. master power, 4채널 manual,
timer, interval, calibration, 채널별 96-byte schedule image를 지원한다. Dosing 정지 명령은 실패
복구가 자동 재시작하지 않는 safer-stop 계약이다. 앱 정수 계약 범위를 송신하지만 물리 투여량의
단위·보정 정확성까지 확정한 것은 아니다.

Aquarium `transport_mode`도 단일 `Mode` 명령으로 노출했다. 이 저장소에는 전환 뒤 별도 endpoint를
찾아 연결을 인계하는 절차가 없으므로, 전환 뒤 readback을 잃으면 성공으로 포장하지 않고 operation을
`UNCERTAIN`으로 남긴다. 기능이 빠진 것은 아니지만 첫 실기에서는 이 특성을 별도로 감시해야 한다.

준비 projection은 identity-bound standalone surface 73개를 만들었다. 이는 장비 command entity
64개와 schedule request API 9개다. 별도로 group control 1개와 group resume 3개가 있다. Pro native
member와 attach member는 각각 2개다. 명시적 master/slave schedule topology가 없어서
`slave_per_slot_flow_extension`의 topology slot entity는 0개이며, 인코더 부재가 원인은 아니다.

## 실행·검증 결과

Linux 격리 image에서 `3802 passed in 204.35s`였고, macOS 최종 단위 시험은
`3785 passed, 1 skipped`였다. 최종 image를 새 data volume으로 stage한 뒤 runtime을 시작했다.
stage exit code는 0, runtime restart count는 0이며 2026-09-17 02:39:15.541897102 UTC부터
`running`이다. `/hardware-safety`는 기존 external shared volume을 read-write로 그대로 mount했고,
`/data`만 새 generation volume이다.

기동 전후 journal은 2,638파일, operation 73개, unfinished 0개로 같고 digest도
`32c77355c90a315ce391f3a29a6f8cad5a5da90fc2b44bdb94548ba48786c2ad`로 동일했다. receipt도
138파일과 digest `4dbd7755ab5909186123fb837a210fac78c8ff0adc9b4ec21c0027b2b97fcb9c`가
동일했다. 새 runtime input은 qualification 파일이 없어 4개다.

배포 뒤 passive MQTT 감사 결과는 fresh device 6/6, online 6/6, global online, state message 237,
command message **0**, audit publish **0**이다. 기대 discovery 104개가 모두 present·matching이고
non-empty tombstone은 0이다. Home Assistant registry 읽기 전용 감사도 expected/matched 104/104,
missing 0, disabled 0이다. command entity 68개는 장비 direct 64개와 group 4개다. schedule request
API 9개는 별도 MQTT request 표면이다.

따라서 배포·discovery 과정에서 Aquarium 메인 리턴을 포함한 어떤 장비에도 control frame을 보내지
않았다. 이 실행은 **명령 라우팅과 HA 노출이 준비됐음**을 확인했지만, 각 장비가 이후 실제 명령을
수용하고 기대 readback을 내는지는 아직 시험하지 않았다.

## 보존 증거

opaque artifact id는 `exact-identity-activation-20260917.TYi1Wb`다. 아래 파일은 private 위치에
보존했으며 원본 wire frame이 아니라 생성기·데몬·MQTT·HA 감사의 구조화 결과이므로 증거 등급은
**(b) preserved structured/durable daemon artifact**다.

| artifact | SHA-256 |
|---|---|
| `prepared-summary.json` | `70c4c6bb266de24f223eeddcd0f269fded51c7e08677f79dc5249e79ad6f569e` |
| `deployment-state.json` | `9ab2beacdb393ebe817b257e9f791e07848addfcbe9526de407820030b5b743c` |
| `integrity-before.json` | `e1f1b18fb8d20791352e8631e2c536dab9c5588225e9e7b385535c4925c1dc52` |
| `integrity-after.json` | `556339abe3a8ba53628a7dd6dd1d15bb1f060144e1ca2d64a574ed05aabb145b` |
| `mqtt-before.json` | `1db3b930dd34a277c597f295446610a7393e3b8266e7f52101a3b8f90c804686` |
| `mqtt-after.json` | `827c82f22f5e7dfa0a6c9c0f8041a4946fb3f02f4aa55aa205fa15d810500099` |
| `ha-registry.json` | `f29b0a2c8e0a5cc1f1355d9bc5b50e61c1c656dc3e35543dcd712a88800f3d01` |
| `runtime-summary.json` | `773b85124e7b7ec9104869768cbefc9ca4f1d4fc8245ab45c1b5622a8da93811` |
| `linux-tests.log` | `d5762ed6fb2e2e46a78c5683fd799e5c07349b9ff1f56688298987d27e28f58c` |

## 새로 확정된 사실

정확한 identity만을 activation authority로 사용하고 qualification/template 입력을 완전히 생략한
최종 image가, 6대의 구현된 제어 표면을 Home Assistant에 104/104로 게시하면서 기동 command 0,
journal·receipt byte 집합 불변, unfinished 0을 유지했다. Dosing 송신 경로와 Aquarium transport
명령도 이 배포에 포함되어 HA에서 선택 가능한 상태다. 실제 기능별 장비 수용은 이번 무송신 배포의
판정 범위가 아니며 다음 attended operation에서 하나씩 확인한다.
