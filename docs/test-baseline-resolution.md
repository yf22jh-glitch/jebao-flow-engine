# 2026-09-16 반복된 다섯 테스트 실패 정정

사용자가 다섯 실패의 원인을 확인한 뒤 수정하고 해결되면 배포하도록 요청했다.
질문은 **기존 다섯 실패를 재현한 조건에서 해결하면서 기록의 단일 소유·권한 검사를
보존하는가**이며, 로컬 재현·실제 파일 경쟁·fault injection·전체 suite로 검증한다.
장비 동작에 관한 새 가설이나 실장 qualification은 이 검증 대상이 아니다.

## 원인과 수정

| 실패 | 확인한 원인 | 수정 |
|---|---|---|
| boot identity/default clock 1건 | macOS에 Linux `/proc/.../boot_id`와 `CLOCK_BOOTTIME`가 없음 | 기본 함수 결속과 지원 부재 시 거부를 플랫폼 독립 단위 테스트로 분리하고, 실제 boot ID/clock 검사는 Linux에서 실행 |
| directory-mode 2건 | `umask 077`이 `mkdir(mode=0755)`를 실제 `0700`으로 만듦 | 테스트의 의도적인 unsafe directory에 `chmod(0755)`를 명시. 제품의 권한 검사는 불변 |
| concurrent receipt 2건 | macOS에서 같은 미존재 lock을 동시에 비배타적 `O_CREAT`로 열면 한쪽의 open이 `ENOENT`로 실패 | `O_CREAT|O_EXCL`로 최초 생성자를 정하고, `EEXIST`인 경우에만 생성 플래그 없이 기존 파일을 열기 |

수정 전 최소 syscall 대조는 새 lock 20회에서 한쪽 `ENOENT` 20건, 기존 lock을 여는
대조에서는 40개 open 전부 성공이었다. 이를 Linux에서도 발생하는 OS 결함이라고
일반화하지 않는다. archive에서는 이 오류가 재시도 가능한 busy가 아니라 일반 archive
오류로 전파되어 두 경쟁 시험의 한 worker가 결과를 남기지 못했다.

기존 lock이 fallback 전에 사라지면 재생성·반복하지 않고 실패한다. `O_NOFOLLOW`,
파일 권한·link-count·inode 검사, nonblocking flock, immutable receipt/finalization,
fsync 순서와 전역/물리 장비 lease는 그대로다. open 오류에는 비밀값 없이 `errno`를
덧붙였다. frozen native source·직접 tests, protocol, 공용 hardware guard는 변경하지 않았다.
boot ID를 임의 값으로 대체하거나 지원하지 않는 clock으로 우회하지 않는다.

## 검증

- 관련 네 파일: **182 passed, 1 skipped**. Linux 실환경 검사는 macOS에서만 제외된다.
- macOS 전체: **3152 passed, 1 skipped**, 120.12초. thread 미처리 예외를 오류로 승격해 실행했다.
- 기존 concurrency 2건과 permissions 2건을 `umask 077`/`022` 각각 10회 실행:
  **20 runs × 4 tests = 80 passed**. warnings를 무시하거나 xfail하지 않았다.
- Linux 전체: **3153 passed**, 187.33초. 실패·스킵·경고 0이며 boot ID 실환경 검사도 통과했다.
- Ruff 전체 `src tests`와 `git diff --check` 통과.

Linux 검사는 홈서버의 일회용 container에서 network `none`, read-only root/source,
capability drop, 임시 test dependency 설치를 사용한다. 운영 data/safety volume을 연결하지
않는다. 첫 소스 전달의 macOS AppleDouble `._*` 파일 때문에 collection이 중단됐으며,
원본 로그를 보존한 뒤 해당 메타데이터 없는 archive로 다시 전달했다. 테스트 제외 패턴을
추가해 통과시킨 것이 아니다. 운영 container·장비·HA는 시험하지 않는다.

로그는 gitignored `data/five-failure-fix.Vj158I/`에 보존한다.

- `full-macos.log`: `3502cf1c06afba65f2ca9fcd53156825f3c0f565af6cef7dee4fe8d3c5cba0a6`
- `full-linux-v2.log`: `4d8d8695367ad05a964a4751e65b5cffa6ce93b1282f1f06fb5b6e83157f35d8`
- `repeated.log`: `dc901b1fe2d70e34af7ddf94a92622a025c29176768c2b2535c7e7c3fb56caed`
- Linux에 전달한 source archive:
  `61ae3ebc0923c4e095644612a4b30a3970b7eb4d9b26dbebcd51150f77fb2d9a`

## 배포와의 구분

이번 수정은 writer/compiler schema를 바꾸지 않는다. 현재 schema는 앞선 기능 추가가 만든
`3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b` 그대로다.
시험 통과는 구 schema의 실장 영수증을 갱신하거나 현재 Pro 미완료 source를 종료하지 않는다.
새 버전의 운영 전환은 이 별도 제약과 사용자 배포 선택을 확인한 뒤에만 수행한다.
