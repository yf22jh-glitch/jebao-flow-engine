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
