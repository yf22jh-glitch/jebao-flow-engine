# run: 2026-09-14-ha-core-update-readonly-postcheck

- 목적(한 문장): 사용자 HA Core 업데이트 완료 회신 뒤 기존 local-control의 fresh publication과
  durable tree가 유지되는지 명령 없이 확인한다.
- 이 실행이 답하려던 질문: 업데이트 뒤에도 여섯 장비의 새 MQTT 관측과 기존 discovery가
  제공되며, 현재 group이 비무장이고 감사 전후 journal·receipt가 같은가.
- 실행한 commit SHA: operator HEAD `7a42675`; 새 진단 source `269e92b`는 미배포.
  기존 runtime image는 `785cf449e528ef86562af8f776bf5703c028f34d8e21b99f237c6435236988d9`.
- 사용한 명령·주요 파라미터: 기존 runtime에서 stdin으로 실행한 read-only subscriber.
  discovery·state topic을 구독하고 최대45초 동안 complete fresh batch를 기다린 뒤8초 관측한다.
  MQTT publish·장비 command·추가 direct device session·service 변경은 모두0이다.
- 종료 지점(stage / failure code): `ha_core_update_postcheck`, exit0, stderr0 bytes.

## 장비가 실제로 돌려준 값

이 값은 기존 데몬의 authenticated poll에서 나온 MQTT publication 관측이다. 이번 감사가
별도 장비 세션으로 직접 재독한 값이나 보존 raw frame이라고 주장하지 않는다.
retained state만으로 fresh를 판정하지 않았다. 각 장비의 새 state topic 전체가 도착하고
그 장비 online availability로 닫힌 batch를 요구했다. batch의 diagnostic timestamp가 하나이며
감사 시작 뒤여야 한다. 장비 unavailable 수신 시 이전 complete 표본을 폐기했다.
group status도 non-retained publication을 별도로 요구했다.

| 역할 | 관측 시각 UTC | power / Flow | Mode / Timer / Linkage |
|---|---|---|---|
| Bar | 04:12:08.524279 | ON / 91 | classic / OFF / independent |
| second Pro | 04:12:09.704904 | ON / latent31 | constant / ON / independent |
| first Pro | 04:12:10.296491 | ON / 40 | constant / OFF / independent |

Bar diagnostic은 Frequency80·PulseTidefalse도 포함했다. Pro Frequency와 second의 current
active slot 일치는 이번 출력에 없으므로 새 exact baseline 검증으로 확장하지 않는다.
여섯 장비의 complete fresh timestamp 범위는04:12:08.146070~04:12:10.296491이며
장비별 표본이지 물리적 동시 관측이 아니다.

- 증거 등급: 보존된 구조화 감사 출력은 (b), 데몬 publication을 수신했다는 주장까지다.
  raw wire 등급(a)이 아니다. 사용자 업데이트 완료 회신과 별도 container inspect 관측은(c)다.
  inspect에서 Core version label2026.9.2·running과 StartedAt03:45:37.884091306Z를 봤다.
  기존 Jebao StartedAt01:51:12.062472495Z는 유지됐다. 이 inspect 원출력은 본 artifact에 없다.
- opaque artifact id: `ha-core-postupdate-3ef2b6322418003733fbd715e31bd06a`.
- UTC span: 2026-09-14T04:11:56.185201Z / 2026-09-14T04:12:18.547562Z.
- SHA-256 identity binding: 기존 `qualification-target:` ASCII physical identity 해시를 재계산했다.
  first `6a3d37793153287545e726444f7d57570e88a47b321f083f0ad7c18cae6001f1`,
  second `17cfb292909445d9bdd4b3506a9e67b513ddf00d8b2445de593d388ae75bfffa`,
  Bar `182e1d8263dd8551208fc4356e276ebc09b7cf1084d89d3501e4c73013c2c371`.
- artifact digest: script `bae38251aa0110bb93a39058d736fbd47b47116a80cbd5e750d9a739900a0d73`;
  stdout2876 bytes `183557bfb860ccd0c3939e1c5e1f6d9cd381124e1fb01f6ed41cdc5e4aab44c3`;
  stderr0 bytes `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
  Files were installed no-clobber with file/directory fsync; raw private paths are not recorded here.

## 원복 검증 결과

변경하지 않았으므로 원복 작업은 없다. pre/post tree는 sorted relative-path/file-SHA256 pair의
canonical ASCII JSON을 해시하는 기존 정의를 양쪽에 동일 적용했다.
journal105 files `8eb0ae8c82032544a94c2866a1d058ccf5c5f27209a3b70b781791294f5cfa3e`,
receipt18 files `9c0eef3e352f1485238c7b3dbbeae27ed7fbf0f62315561a360a897dc45adfaf`가
감사 전후 동일했다. unfinished0이다. 이 구간의 불변이지 전체 업데이트 중 무변경의 연속 증명은 아니다.

## 이 실행으로 새로 확정된 사실

업데이트 완료 회신 뒤 이 유한 관측창에서 discovery90/90 byte exact이며 canonical digest는
`b435b45d453f73d0742b87523dcd37a940dcd4ff12c3c3c1eb6f16dca27f59e6`다.
tombstone53의 nonempty0, command topic10의 수신0·retained0, fresh/online devices6,
global online, supervisor1·unconstructed0·cycle failure0·armedfalse·override0·resume online0,
ignored0·default power50을 관측했다. fresh cycle count620은 한 snapshot 값이다.

## 이 실행으로 확정하지 못한 것

HA 내부 entity registry·UI·automation의 전체 정상 여부, 업데이트 전 구간의 명령 부재,
펌프의 물리 수류량, 새 diagnostic image의 운영 검증은 확인하지 않았다.
D1u 원인 UNKNOWN과 최종 diagnostic source의 full-suite 실패 두 건은 그대로 남는다.
이번 PASS는 read-only postcheck뿐이며 배포나 Bar-only 물리 operation 승인이 아니다.

## 다음에 이 지점을 다시 만나지 않으려면 필요한 것

명령 유래 retained state와 fresh poll batch를 구별한다. 새 배포 판단에는 targeted21/Ruff PASS와
미해결 full-suite 결과를 함께 공개한다. Bar-only operation은 별도 현장 승인·앱 준비·복원 권한을
확보한 뒤 진행하며, 기존 D1u 승인을 재사용하지 않는다.
