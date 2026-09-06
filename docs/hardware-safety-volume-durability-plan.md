# Hardware-safety volume durability incident and remediation

상태: **P1 INCIDENT / ALL HARDWARE OPERATIONS NO-GO / DEVICE CONTACT 0**

작성일: 2026-09-06

근거 실행:
[`2026-09-04 standalone exact-restore qualification`](runs/2026-09-04-exact-restore-qualification-4dcd8364.md)

## 닫을 질문

승인된 physical-write 프로세스가 폐기 가능한 Docker 익명 볼륨을 deployment-wide
`/hardware-safety` root로 오인해 durable journal, rollback authority 또는 operation replay
barrier를 잃을 수 있는가?

2026-09-06 forensic과 현재 source trace의 답은 **가능**입니다. 이것은 `AGENTS.md` §3의
durable journal/rollback 및 duplicate-write 가능성에 해당하는 P1입니다.

## 확인된 사실과 확인되지 않은 인과

- 현재 `/hardware-safety`를 제공하는 유일한 컨테이너는 유휴 `sleep infinity` 프로세스이며,
  그 mount는 컨테이너 생성 때 만들어진 Docker 익명 볼륨입니다.
- 그 root는 owner·mode가 실행 계정의 `0700`과 일치하지만 내용이 비어 있습니다.
- read-only forensic은 현재 daemon의 전체 volume, 사용 가능한 Docker context/profile, 사용자 홈
  아래 depth `7` 범위(`Library`·`node_modules`·`.git`·`.Trash` 제외)와 Docker 내부 저장소를 제외한
  VM root에서 `2026-09-04` run이 참조한 exact-restore journal snapshot, disposition, tombstone과
  operation finalization을 찾지 못했습니다. 외부 매체·다른 머신·백업은 조사 범위가 아니었습니다.
- 따라서 그 파일들은 **2026-09-06 조사 범위에서 발견되지 않아 재검증할 수 없습니다.** 당시
  생성·대조됐다는 기록을 소급해 부정하지 않지만, 현재 독자는 그것을 보존된 (b) artifact로 사용할
  수 없습니다.
- daemon event history는 남아 있지 않습니다. 삭제 시각·주체와 실제 삭제 명령은 `UNKNOWN`입니다.
  현재 익명 볼륨이 2026-09-04 run에서 사용된 exact volume인지도 증명하지 않습니다.
- 다만 Dockerfile이 image 안에 owner·mode가 정확한 빈 `/hardware-safety`를 만든 뒤 그 경로를
  `VOLUME`으로 선언합니다. Docker가 새 익명 볼륨을 이 metadata로 초기화하고
  `os.path.ismount()`도 참이 되므로, 현재 runtime 검사는 이 폐기 가능한 root를 sanctioned
  persistent root로 받아들입니다. 이 구조적 결함은 삭제 인과와 독립적으로 확정됩니다.

## 즉시 containment

- B1/B2/B3 device read, C host semantic mutation, D device write, 앱 live-write, Home Assistant
  조작과 recovery supervisor 기동은 모두 `NO-GO`입니다.
- 빈 root에서 `exact-restore status`를 실행하지 않습니다. temporary probe만 만들고 `idle`을
  정상 terminal 상태로 오독하게 할 수 있습니다.
- 현재 Docker volume을 삭제·rename·prune·recreate·초기화하지 않습니다.
- 기존 run의 digest만으로 사라진 artifact bytes를 재구성하거나 성공 barrier를 가장하지 않습니다.

## 작업 A — 관측된 자동 익명-volume 경로 차단

1. Dockerfile의 `VOLUME` 선언에서 `/hardware-safety`만 제거합니다. `/data`는 대응하는 mount
   검사가 없으므로 이 incident와 함께 제거하지 않습니다.
2. image 안의 `/hardware-safety` directory와 owner·`0700` metadata는 유지합니다. explicit mount가
   없으면 이 경로는 mount가 아니므로 기존 `os.path.ismount()` gate가 device discovery 이전에
   fail-closed합니다.
3. official Compose의 두 service가 exact named volume
   `jebao-flow-hardware-safety:/hardware-safety`를 쓰고 top-level volume name도 고정하는 계약을
   repository regression으로 검증합니다.
4. 모든 hardware entry 전에 running·exited 전체 container에서 `/hardware-safety` mount를 열거해
   sanctioned service 이외의 container가 하나라도 쓰는지 확인하고, 이어 실제 target container
   mount의 destination, type과 exact volume name을 확인합니다. 명령 실패·불일치·중복·부재면 root를
   열거나 probe하지 않고 중단합니다.

이 작업은 **자동으로 만들어진 익명 mount**라는 관측된 메커니즘을 hard failure로 바꿉니다.
운영자가 `-v <other-volume>:/hardware-safety`를 명시하는 잘못까지 container 내부에서 식별하지는
못하므로, 그 경계는 위 host preflight로 닫습니다.
**작업 A와 이 preflight만으로 hardware `GO`가 되지는 않습니다.** 아래 replay 차단과 별도
volume-identity/provisioning 결정을 포함한 이 문서의 나머지 선행조건도 모두 남습니다.

## 작업 B — 사라진 permanent replay barrier의 대체 차단

2026-09-04 parked sentinel operation의 공개 SHA-256은
`4dcd8364b272644ac883faca9494c7884b045e387970e9a38601df6d5bf4baa0`입니다. backing finalization이
사라졌으므로 archive 조회만으로 이 operation의 재사용을 막을 수 없습니다.

현재 reviewed source에는 이 digest를 명시적 revoked-operation 집합으로 넣습니다. 새 record를
설치하는 경계와 기존 record를 이어 가는 경계 모두에서 **현재 record와
`qualification_final_record` predecessor chain 전체**의 operation digest를 oldest까지 검사합니다.
따라서 revoked sentinel 자체의 `create`·promotion뿐 아니라, 그것을 embed한 baseline(depth `1`)과
phase-5(depth `2`)의 create·`arm`·`execute` continuation도 device contact 전에 `AUTHORITY`로
거부합니다. 진단에는 원 operation id 대신 공개 digest와
`operation_revoked_after_barrier_loss`를 남깁니다. 이 차단은 사라진 bytes를 재구성하거나
qualification 성공을 주장하지 않습니다.

적용 범위는 새 device-capable record를 create·promotion하거나, 그것을 arm·reauthorize·recover·
execute하거나, 아직 final observation이 필요한 record를 finalize하는 경계입니다. 반대로 read-only
status, disposition `park-verify`, device-write-free `park`, 이미 `FINAL_VERIFIED`인 record의 receipt
재취득, receipt에 결속된 idempotent archive·`clear`는 이 source revocation으로 막지 않습니다.
revocation은 재실행 장벽이지 write-free terminalization을 없애는 장벽이 아닙니다.

## 별도 후속 — volume identity marker

작업 A는 explicit wrong-volume mount를 container 내부에서 구별하지 않습니다. 그 구분이 필요한
후속 설계는 image에 들어 있지 않은 random volume identity marker와 volume 밖의 locked expected
identity를 비교해야 합니다. marker 부재는 fail-closed하고 최초 provisioning은 no-clobber create,
file fsync, parent fsync, exact reload를 거쳐야 합니다.

fresh named volume도 처음에는 비어 있으므로 provisioning은 별도 승인된 host-state operation입니다.
이 incident 대응에서 자동 marker 생성이나 현재 빈 volume 초기화를 하지 않습니다. 후속이
착지하거나 maintainer가 host preflight를 충분한 장기 경계로 명시적으로 선택하기 전에는 hardware
`NO-GO`를 유지합니다.

## 검증 계약

- Dockerfile은 `/hardware-safety`를 어떤 `VOLUME` instruction에도 포함하지 않습니다.
- official Compose의 daemon과 recovery service는 둘 다 exact named safety volume을 같은 destination에
  mount합니다.
- mount가 아닌 image directory는 기존 runtime gate에서 probe 전에 거부됩니다.
- revoked digest의 새 create·promotion과, 그 operation을 depth `1`·`2` predecessor로 embed한 record의
  create·continuation은 모두 device observation·resolve·write 전에 `AUTHORITY`로 거부되고
  privacy-safe diagnostic을 냅니다.
- 같은 revoked record의 `park-verify`·`park`와 이미 검증된 receipt 회수·receipt-bound clear 같은
  write-free terminal/idempotent 경로는 revocation 집합 때문에 거부되지 않습니다.
- frozen native linkage source와 test는 수정하지 않습니다.
- 표적 unit, exact-restore unit, 전체 unit과 변경 파일 ruff를 network-none에서 재검증합니다.

## 완료가 아닌 것

이 수정은 사라진 journal·snapshot·disposition·finalization을 복원하지 않고 formal qualification
receipt나 qualified bundle도 만들지 않습니다. fresh named volume provisioning, marker 결정,
fresh Phase 0와 모든 단계별 승인은 별도입니다. 따라서 이 계획과 source가 착지해도 Q2와 앱
live-write는 계속 `NO-GO / BLOCKING`입니다.

## 2026-09-06 실행 addendum — 작업 A·B source 착지

작업 A와 B는 source commit
`c2a4e8adb425283f94f1d5329d0657c0c705faad`로 착지했습니다. base
`fa151903c3b616de9dd9a6b9af412fe97873e915` 대비 diff SHA-256은
`ec8a93333441b8eeebd88d5d43392bf97e49f5b6d551b3eb85bc080b7f7d08ba`이고, frozen native
linkage source와 test는 수정하지 않았습니다.

- Dockerfile은 `/data`만 `VOLUME`으로 선언하고 `/hardware-safety`의 image directory·owner·`0700`
  metadata는 유지합니다. 정적 회귀는 대소문자·선행 공백·tab으로 쓴 `VOLUME`도 탐지하고 shell
  형식 instruction은 fail-closed하며, official Compose의 두 service가 exact named volume을 공유하는
  계약을 함께 고정합니다.
- 사라진 operation의 공개 digest는 source revocation에 들어갔고, 현재 record부터 embedded
  predecessor chain의 oldest까지 순회합니다. create·promotion과 device-capable continuation은 device
  observation·resolve·write 이전에 privacy-safe `AUTHORITY` 진단으로 거부됩니다.
- write-free disposition verify·park, 이미 `FINAL_VERIFIED`인 record의 receipt 회수와 receipt-bound
  clear는 계속 열려 있습니다.

마지막 항목의 의미를 다음 세 경계로 좁힙니다.

1. 2026-09-06 forensic이 확인한 이 deployment 상태에는 live journal이 없었습니다. 이 commit의
   source가 적용된 뒤에는 revoked operation의 create·promotion·device-capable continuation이 모두
   닫히므로 sanctioned 경로로 같은 digest의 `FINAL_VERIFIED` journal에 도달할 수 없습니다. journal을
   손으로 만드는 것은 계속 금지합니다.
2. 다른 deployment에 revocation 착지 전에 만들어진 legacy `FINAL_VERIFIED` journal이 실제로
   존재한다면, 그것을 좌초시키지 않기 위해 write-free terminalization은 의도적으로 허용됩니다. 그
   경로가 남기는 finalization은 **현재 terminal journal**에서 receipt를 도출해 결속한 새 replay
   barrier이며, 사라진 2026-09-04 archive bytes를 재구성한 것이 아닙니다.
3. operation finalization archive는 operation digest로 keying되고 어느 journal instance가 만들었는지
   provenance를 보존하지 않습니다. 따라서 나중에 같은 digest의 v1 success finalization만 읽어서는
   2026-09-04 parked operation의 성공 여부와 legacy terminal journal의 후속 처리를 구분할 수 없습니다.
   과거 성공 판정은 archive 단독이 아니라 원 run 기록과 이 incident 문서를 함께 따라야 합니다.

최종 staged bytes는 서로 다른 두 실행자가 network-none container에서 독립 검증했습니다. 한쪽은
`/work`·`/tmp`·`/data`·`/hardware-safety`를 tmpfs로 두었고, 다른 쪽은 `/hardware-safety` mount 자체를
두지 않았습니다. 양쪽 모두 표적 4파일 `167 passed`, 전체 unit `1899 passed`, 변경 Python 5파일
ruff lint·format 통과를 확인했습니다. 이 실행 결과는 보존 daemon artifact가 아니라 console
관측입니다. 두 실행자가 일치시킨 diff digest는 commit에서 재계산 가능한 source property이며
증거등급 대상이 아닙니다. 실제 safety volume·private durable state·device·앱·Home Assistant 접촉은
`0`이었습니다.

이 착지는 자동 익명 root와 잃은 replay barrier의 source 경로를 닫았지만 hardware `GO`가 아닙니다.
named volume identity marker와 별도 승인된 provisioning 또는 host preflight를 장기 경계로 삼는
maintainer 결정, fresh Phase 0와 각 승인 축은 여전히 남아 있습니다.

## 2026-09-06 volume identity marker 구현 결정

repository maintainer는 host preflight만을 장기 경계로 선택하지 않고 marker source를 구현합니다.
이 결정은 **source·test·문서 변경만** 승인하며, 실제 volume 생성·mount·provisioning이나 private
expected identity 생성은 승인하지 않습니다.

### 신뢰 경계와 남는 한계

- expected identity는 fixed environment variable
  `JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID`의 lowercase 32-hex 값으로 받습니다. journal·archive·guard의
  production 생성자는 normal runtime config를 받지 않고 `status`도 config 없이 root를 열기 때문에,
  config 결속을 새로 퍼뜨리지 않습니다. Compose는 daemon·recovery·provisioning service 모두에서
  `${JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID:?...}`로 미설정을 먼저 거부하고 실제 값은 commit하지
  않습니다. 값은 최초 provisioning 전에 한 번만 생성해 deployment private `.env` 또는 동등한
  secret store와 별도 복구 가능한 private backup에 보존합니다. marker가 설치된 뒤에는 값을 rotate·
  재생성하지 않습니다. 관측 전용 `jebao-flowd`도 같은 Compose 계약 때문에 이 값이 없으면 시작하지
  않습니다.
- root marker는 `.hardware-safety-identity`, owner process 소유, mode `0600`, regular file,
  `nlink == 1`이고 marker의 `st_dev`는 root descriptor의 `st_dev`와 같아야 합니다. 내용은 newline으로
  끝나는 canonical JSON이며 exact key set은
  `{"version":1,"volume_id":"<lowercase 32 hex>"}`입니다. dirfd-relative no-follow open과 named/opened
  inode 대조를 사용하고 expected value 비교는 constant-time으로 수행합니다.
- 이 marker는 unprovisioned root와 **다른 identity를 가진** accidental wrong volume을 fail-closed로
  구분합니다. marker 파일까지 byte-for-byte 복제하고 같은 expected env를 주거나 volume과 env를
  함께 고의로 바꾸는 경우를 container 내부에서 구분하지는 못합니다. 그러므로 running·exited 전체
  container 열거와 exact Docker volume-name host preflight는 marker 뒤에도 필수입니다.
- provisioning 직전에는 primary carrier와 별도 backup carrier를 각각 읽어 lowercase 32-hex·길이·
  SHA-256·원문 exact match를 확인하고 공개 기록에는 일치한 SHA-256만 남깁니다. marker가 아직 없는데
  두 carrier가 다르면 어느 쪽도 권위로 간주하지 않고 둘 다 폐기한 뒤 `secrets.token_hex(16)`으로 새
  identity를 생성해 다시 저장·대조합니다. marker가 이미 있으면 marker가 유일한 권위이고 새 값을
  생성하지 않으며 승인된 read-only 회수 절차를 따릅니다. identity digest를 공개할 수 있는 근거는
  반드시 `secrets.token_hex(16)`으로 만든 128-bit entropy입니다.

### runtime과 provisioning 순서

1. runtime open은 fixed root mount·owner·`0700` descriptor를 먼저 확인합니다.
2. **어떤 writability probe보다 먼저** marker metadata·canonical bytes·expected identity를 확인하고,
   marker 부재·환경변수 부재/형식 오류·mismatch·corruption·다른 filesystem의 marker이면 volume에
   write하지 않고 거부합니다.
3. marker 확인 뒤에만 기존 temporary `0600` probe를 수행하고, mount descriptor와 marker를 다시
   확인한 뒤 retained capability를 반환합니다. 따라서 journal store·archive·guard의 실패는 device
   discovery·observation·resolve·write보다 앞입니다.
4. 별도 `jebao-flow-hardware-safety` console script는 `verify`와 `provision`만 제공합니다.
   provisioning service는 official named volume 하나만 mount하고 `network_mode: none`, read-only root
   filesystem, `restart: "no"`와 explicit profile을 사용합니다. default command는 `verify`이고
   `provision`은 operator가 명시적으로 선택해야 합니다.
5. provision은 mount metadata를 확인하고, marker가 이미 있으면 probe 전에 exact identity를
   검사합니다. 동일하고 `nlink == 1`이면 file fsync → close → parent fsync → exact reload로 ACK-loss를
   멱등 복구하고, 이어 기존 probe와 재확인을 통과한 뒤 성공합니다. 동일하지만 `nlink > 1`이면
   중단된 hard-link install만 복구할 수 있습니다. root 안에서 marker temporary의 exact 이름 형식을
   만족하고 final marker와 `(st_dev, st_ino)`가 같은 엔트리만 dirfd-relative로 unlink한 뒤 parent
   fsync하고 `nlink == 1`과 canonical bytes를 다시 확인합니다. 이름이나 inode가 다른 엔트리는 절대
   지우지 않으며, 잉여 link가 모두 이 좁은 조건으로 설명되지 않으면 거부합니다. 다르거나 corrupt한
   final marker도 overwrite·unlink 없이 거부합니다. 이 복구는 별도 승인된 provisioning에만 있고,
   runtime은 `nlink != 1`을 probe 이전에 그대로 거부합니다.
6. marker가 없을 때만 승인된 provisioning에서 temporary probe를 수행한 뒤 기존 durable journal의
   no-clobber 설치 관용구를 그대로 사용합니다. random `.tmp`를
   `O_CREAT|O_EXCL|O_NOFOLLOW`로 열어 canonical bytes를 전부 write하고 file fsync·close한 다음,
   dirfd-relative `os.link(..., follow_symlinks=False)`로 final marker를 원자적으로 설치합니다. final이
   이미 있으면 `EEXIST`로 거부하며 overwrite하지 않습니다. link 뒤 parent fsync → temporary unlink →
   parent fsync → final marker exact reload 순서로 끝냅니다. crash나 partial write는 orphan temporary를
   남길 수 있지만 **부분 내용의 final marker는 만들지 않습니다**. orphan temporary는 final marker로
   신뢰하지 않습니다. link 이전에 남은 orphan은 다음 provisioning의 새 random temporary와 충돌하지
   않고, link 이후 남은 같은-inode temporary는 위 5번의 좁은 복구로만 제거합니다.

### migration과 park 순서

기존 named volume도 marker가 없으면 자동 생성하지 않고 fail-closed합니다. 먼저 active Docker
context와 모든 container mount를 host preflight로 확인하고, journal·archive를 삭제하거나 고치지 않는
별도 host-state 승인 아래 provisioning one-shot을 **`docker compose run --rm` 형태로** 실행합니다.
provisioning 자체는 volume 안의 journal·archive 내용을 판정하지 않으므로, exact named-volume host
preflight가 잘못된 volume에 identity를 찍지 않게 하는 유일한 선행 경계입니다. 이 순서를 바꾸지
않습니다. provisioning interval에는 daemon·recovery·다른 root user가 부재하거나 정지된 상태임을
확인하고 one-shot 종료 전에는 시작하지 않습니다. provisioning container는 hardware preflight의
허용 container가 아니며, 성공·실패 어느
경우든 preflight 시점에 running·exited 목록에서 완전히 부재해야 합니다. `--rm` 실패 등으로 남아
있으면 삭제를 자동 수행하지 않고 별도 확인·승인 전까지 hardware `NO-GO`를 유지합니다.

marker 없는 root에서는 `status`·`park-verify`·`park`도 열리지 않습니다. 이는 revocation이 write-free
terminalization을 막지 않는다는 계약과 모순이 아닙니다. root identity가 불명인 동안에는 그 root의
journal과 archive를 신뢰할 수 없으므로, **exact named-volume preflight와 marker provisioning이 먼저**이고
그 뒤에야 status 또는 좌초 journal의 write-free park를 검토합니다. provisioning은 journal을 읽거나
수정하지 않습니다.

### 검증 계약

- expected env 부재·형식 오류, marker 부재·mismatch·0-byte/partial/extra-key/wrong-version,
  symlink·mode·owner·`nlink` 위반은 probe와 device I/O 전에 거부됩니다.
- exact marker와 expected env는 기존 probe와 root capability open을 통과합니다.
- provisioning은 no-clobber이고 동일 marker 재실행만 멱등 성공합니다. 다른/corrupt final marker는
  보존한 채 거부하고, partial temporary가 final marker로 보이지 않음을 고정합니다. temporary write →
  file fsync → close → hard-link install → parent fsync → temporary unlink → parent fsync → exact reload
   순서를 회귀로 고정합니다.
- hard-link install 직후 crash 상태(final marker와 같은 inode인 temporary, `nlink == 2`)에서는 runtime이
  probe 전에 거부하고, 승인된 provisioning만 exact marker temporary link를 제거해 `nlink == 1`로
  복구합니다. 다른 이름이나 다른 inode의 파일은 그대로 남고, 설명되지 않는 link는 fail-closed합니다.
- Compose 세 service의 required env, provisioning service의 network-none/read-only/exact named mount와
  `restart: "no"`·console entrypoint를 정적으로 검증합니다. checklist는 provisioning을 `--rm`으로만
  실행하고 hardware preflight 시점의 running·exited 허용 집합에서 제외합니다.
- `.env.example`은 실제 identity를 넣지 않은 채 required variable과 one-time 생성·보존 규칙을
  드러냅니다. digest helper와 실제 verify/provision CLI 회귀는 private 32-hex 원문이 stdout·stderr에
  나오지 않음을 고정합니다.
- frozen native linkage source와 test는 수정하지 않고, 실제 volume과 private state는 테스트에서
  mount하지 않습니다.

### expected identity 분실 시 복구

private expected identity를 잃거나 변경했다면 새 값을 생성해 맞추지 않고 모든 hardware operation을
`NO-GO`로 둡니다. volume·marker·journal을 삭제하거나 marker를 overwrite·재provision하지 않습니다.
active Docker context, running·exited 전체 mount와 exact named volume을 아래 checklist 계약으로 먼저
확인한 뒤, 별도 승인 아래 reviewed exact image를 사용하는 network-none·read-only·`--rm` one-shot에
그 named volume을 **read-only**로 mount해 canonical marker의 기존 `volume_id`를 회수합니다. 그 exact
값만 deployment private `.env` 또는 secret store에 복원하고 one-shot이 완전히 사라진 뒤 host
preflight와 `verify`를 다시 통과시킵니다. raw identity는 저장소·run 기록·일반 console log에 남기지
않습니다. 어느 단계에서든 exact volume provenance나 marker canonicality를 확인하지 못하면 복구를
계속하지 않고 `NO-GO`를 유지합니다.

link 이전 crash가 남긴 orphan marker temporary는 자동 삭제하지 않으며 final marker로 신뢰하지
않습니다. root 목록에서 보이더라도 증거로 보존하고, final marker의 identity·metadata 검증과 별개로
기록합니다.

source가 착지해도 실제 expected identity와 marker는 존재하지 않습니다. 별도 provisioning 승인과
실행 결과를 확인하기 전까지 모든 hardware operation은 계속 `NO-GO / BLOCKING`입니다.

## 2026-09-06 volume identity marker source 착지

위 설계는 source commit `adec0a33ce7d212f7ad95b1b3fb91378d01614bf`로 착지했습니다. 기준 commit은
`631df0ab5b01b8849087295281a2955258b61211`이고, 그 사이 diff SHA-256은
`c125c7bbca62aeb929ef01e1186a32e056629c7d0739842352291d3c1ba91779`, 변경 규모는 16파일
`+1387/-67`입니다. frozen native linkage source와 test는 수정하지 않았습니다.

서로 다른 두 실행자가 동일 diff와 파일 bytes를 대조한 뒤 network-none container에서 전체 unit
`1939 passed`, 표적 7파일 `188 passed`를 독립 확인했습니다. `ruff check src tests`는 최종 diff에서,
format 검사는 byte-identical Python 변경에서 통과했습니다. Compose는 private identity가 없으면
non-zero로 거부하고 형식이 맞는 dummy identity에서는 구성이 유효함을 확인했습니다. 실제 volume
대신 disposable non-persistent root를 사용한 CLI
검증은 `provisioned` → `verified` → `already_provisioned` 순서로 성공했고 raw 32-hex identity를
stdout·stderr에 노출하지 않았습니다. foreign-device marker, FIFO marker, env 부재, probe 중 marker
교체, 중단된 same-inode hard-link install과 설명되지 않는 temporary 이름도 각각 fail-closed 또는
계약된 좁은 복구로 확인했습니다. 교차검증은 착지 전에 production privacy 계약을 실행하지 않던
공허한 test, supervisor 선기동 순서 모순, bash 3.2에서 무효였던 standalone `[[ -n ]]` guard와 실제
동작에 맞지 않던 restart-loop 설명을 찾아 수정했습니다. 이 실행 결과는 증거등급 **(c) reconstructed
operator observation**이고, diff SHA-256은 commit에서 재계산 가능한 source property라 증거등급
대상이 아닙니다.

이 작업은 실제 named volume을 생성·mount·provision하지 않았고 private expected identity도 만들지
않았습니다. 실제 safety volume·private journal·archive·device·앱·Home Assistant·recovery supervisor
접촉은 `0`입니다. 따라서 이 착지는 source 계약의 완료일 뿐 hardware `GO`가 아닙니다. 다음 순서는
**lowercase 32-hex identity를 한 번 생성해 private secret store와 별도 복구 가능한 private backup에
보관 → active context와 exact named-volume host preflight → 별도 승인된 provisioning → runtime
verify → 그 뒤 status 또는 park 검토 → fresh Phase 0와 해당 승인 축**입니다. provisioning 뒤에는
identity를 바꾸지 않고, 분실 시 새 값을 만들지 않으며 checklist의 승인된 read-only 회수 절차만
따릅니다. 그 전까지 모든 hardware operation은 계속 `NO-GO / BLOCKING`입니다.

## 2026-09-06 provisioning preflight — identity 준비, 두 host mutation은 대기

repository maintainer가 다음 단계를 진행하라고 한 뒤 범위를 private identity 생성·보관, exact
named-volume preflight, 별도 provisioning과 runtime verify까지로 한정했습니다. device·앱·Home
Assistant·Phase 0는 이 범위에 포함하지 않았습니다.

read-only host metadata preflight의 증거등급은 **(c) reconstructed operator observation**입니다.
active Docker context는 `colima`, exact named volume `jebao-flow-hardware-safety`는 부재했고,
`/hardware-safety` mount는 total `1`, sanctioned `0`, unsanctioned `1`, active `1`이었습니다. sanctioned
daemon·recovery·provisioning container는 모두 부재했습니다.

유일한 unsanctioned mount는 production daemon이 아니라 2026-09-03T07:01:59.439008193Z에 생성된
개발용 scratch container였습니다. image는 `jebao-flow-engine:sync-ownership-wip`, entrypoint/command는
`sleep infinity`, restart policy는 `no`, auto-remove는 false이고, read-write `/hardware-safety` mount의
source는 64-hex 이름의 Docker anonymous volume입니다. 공개 기록에는 원문 id를 넣지 않고 다음 digest로
결속합니다.

- container id SHA-256:
  `e26f1e8e264bc90ec37916ca90b4d50045cd22061377580f986d2faa4d5d3b31`
- anonymous volume name SHA-256:
  `9ad2c1b7ae3c07230ab75e8a4066f3a1b3797844e4a6a813ce32627bcb22a072`

이 container와 volume이 2026-09-04 run artifact를 실제로 보유했던 exact substrate였다고 단정하지
않습니다. 다만 run 이전부터 존재한 anonymous safety-root substrate 후보이고, container가 volume의
용도를 보여 주는 현재 유일한 Docker metadata link입니다. 따라서 기록보다 먼저 container를 제거하지
않습니다. 별도의 read-only `docker system df -v` 관측에서 이 volume은 2026-09-06 현재 `LINKS=1`,
`0B`였습니다. 이는 현재 보존할 volume 내용이 없다는 독립 관측이지, 과거 artifact가 이 volume에
있었거나 없었다는 증명은 아닙니다. 이 값과 위 digest·metadata를 기록한 뒤에는 empty anonymous
volume 자체의 생존을 증거 조건으로 삼지 않습니다.

승인 범위 안에서 lowercase 32-hex expected identity를 생성했습니다. 원문은 출력·commit하지 않았고,
mode `0600`의 gitignored deployment `.env`와 macOS Keychain generic-password item이라는 두 carrier에
보관했습니다. 첫 non-interactive Keychain 입력은 empty item을 만들고 `.env`에는 valid identity를
남겼습니다. provisioning 전에 길이·digest mismatch를 탐지했습니다. marker가 없는 상태에서는 어느
carrier도 권위로 삼지 않는 위 규칙을 적용해 provisional identity SHA-256
`88e06fdf9ca74eb5d556bed738a5604716349c7027d2415e8537fd3cc905fd6f`를 폐기하고 새 identity를 두
carrier에 다시 저장·대조했습니다. 최종 두 값은 lowercase 32-hex로 exact match하고 identity SHA-256은
`6b9b4cfb01adc12242d19d8e72aebf3984c4010623182a979c511e5c8cba8dcc`입니다. marker와 named volume은
아직 없으므로 device나 durable safety root에는 영향이 없습니다.

preflight 뒤 새로 드러난 다음 두 mutation은 최초 범위가 자동으로 승인하지 않습니다.

1. scratch container를 되돌릴 수 없게 `docker rm`하되 **`-v`를 사용하지 않는** 처분. 현재 `0B`인
   anonymous volume은 당장 삭제하지 않지만 `LINKS=0`의 dangling·prune 대상이 되며, 그 생존을
   증거 조건으로 삼지 않습니다.
2. exact named volume `jebao-flow-hardware-safety`의 명시적 생성

권고는 위 digest·`0B` 기록 뒤 1번을 실행해 container metadata link를 문서로 승격하고, 별도 승인된
2번과 재검사를 거쳐 provisioning으로 가는 것입니다.
container 예외를 checklist에 넣어 계속 보존하는 대안도 있지만, 그동안 provisioning preflight는
계속 `NO-GO`입니다. 두 결정을 받기 전에는 container stop/remove, volume create·mount, marker
provisioning과 runtime verify를 실행하지 않습니다.

## 2026-09-06 scratch container 처분과 exact named volume 생성

repository maintainer는 앞 절의 두 host mutation을 각각 설명받고 모두 승인했습니다. 이 승인은
scratch container 처분과 empty named volume 생성만 포함했고 marker provisioning·runtime verify,
recovery supervisor, Phase 0와 device·앱·Home Assistant 접촉은 포함하지 않았습니다.

실행 직전 container id·anonymous volume name의 SHA-256, image, creation time, `sleep infinity`, running
state, `auto_remove=false`, restart `no`, anonymous 64-hex volume과 `LINKS=1 | 0B`를 precommit과 다시
대조했습니다. 모두 일치한 뒤 container를 `docker stop --time 10`으로 정지하고 `docker rm`으로
제거했습니다. `-v`는 사용하지 않았습니다. container가 더는 존재하지 않고 anonymous volume은
즉시 삭제되지 않은 채 `LINKS=0 | 0B`로 남았음을 확인했습니다. 이 empty dangling volume은 이후
prune될 수 있고 그 생존을 증거 조건으로 삼지 않습니다.

active context `colima`에서 exact named volume이 아직 없음을 다시 확인한 다음
`docker volume create --driver local jebao-flow-hardware-safety`로 명시 생성했습니다. 생성 결과는 exact
name, driver `local`, `LINKS=0 | 0B`입니다. running·exited 전체 container를 다시 열거했을 때
`/hardware-safety` mount는 `0`건이었습니다. Compose one-shot에 implicit volume creation을 맡기지
않았고, 새 volume을 아직 어떤 container에도 mount하지 않았으며 marker도 설치하지 않았습니다.

이 결과의 증거등급은 **(c) reconstructed operator observation**입니다. actual volume content,
private journal·archive, device·앱·Home Assistant·recovery supervisor 접촉은 `0`입니다. 다음 단계는
두 identity carrier의 provisioning-직전 exact 재대조, reviewed image attestation, network-none
provisioning과 runtime verify이지만, 그 실행 packet이 현재 Compose required-env 계약을 우회하지
않는지 확인할 때까지 모든 hardware operation은 계속 `NO-GO / BLOCKING`입니다.
