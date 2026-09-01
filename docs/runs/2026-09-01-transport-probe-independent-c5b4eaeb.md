# run: 2026-09-01-transport-probe-independent-c5b4eaeb

- 목적(한 문장): 직전 Q2-target 실행에서 `async_slave` raw가 0개였던 원인을 다음 write 없이
  좁히기 전에, 새 strict-read transport probe가 복원된 `independent` 두 장비에서 explicit reply와
  원본 wire frame을 보존할 수 있는지 확인한다.
- 이 실행이 답하려던 질문: "같은 홈서버·네트워크 경로에서 두 Pro가 `independent`일 때 각 fresh
  session의 strict state request에 `0x03`으로 응답하는가?"
- 실행한 commit SHA: `1d16b5360d671f612deb389002ecebd467989d13`
- 사용한 명령·주요 파라미터:
  - exact commit의 clean checkout을 14개 source pin으로 검증한 뒤 Python `-B -P`로 실행
  - 대상: 역할 A, 역할 B; expected Linkage=`independent` / `independent`
  - 장비별 fresh authenticated session 1개, strict state read 1회, `accept_reports=false`
  - 기존 recovery 프로세스는 실행 직전 idle·established control connection 0을 확인하고 잠시
    정지했으며, probe 종료 직후 원상 재기동
  - 장비 write·앱 조작·schedule 변경·Linkage 변경: 각각 0회
- 종료 지점(stage / failure code): `probe_completed_context_valid`; 두 대상 모두
  `explicit_reply_observed`; `q2_verdict=UNKNOWN`
- 장비가 실제로 돌려준 값:
  - 두 장비 모두 explicit reply action `0x03` 1회, report action `0x04` 0회였다.
  - 두 원본 frame 모두 462-byte wire frame 안에 452-byte Pro status를 포함했고 expected Linkage
    `independent`와 일치했다.
  - 역할 A: `ON / TimerON / independent / manual constant / Flow 30 /
    Frequency 32`; 현재 slot 보고값은 `tidal / AutoFlow 55 / AutoFreq 5`였다.
  - 역할 B: `ON / TimerON / independent / manual random / Flow 89 /
    Frequency 34`; 현재 slot 보고값은 `pulse / AutoFlow 75 / AutoFreq 60`이었다.
  - private result와 commit marker의 artifact digest를 독립 재계산해 일치함을 확인했다.
  - 증거 등급:
    - explicit action, exact wire frame, decoded control·slot 값: **(a) preserved raw artifact**
    - plan, source pin, host timing, identity binding, frame count: **(b) preserved structured/durable
      daemon artifact**
    - source-attested probe의 device write 0회: **(b)** — 실행 plan과 exact source pin으로
      control/write API가 없는 경로를 고정했으며 recovery·다른 control 연결 부재는 아래 (c)로 분리
    - recovery 프로세스의 정지·재기동과 사전 established connection 0 확인: **(c)
      reconstructed operator observation**
  - opaque artifact id: `JTP-c5b4eaeb90fd0cfc4e165eb9e4c79949`
  - UTC span (시작 / 종료): `2026-09-01T04:13:01.867959Z` /
    `2026-09-01T04:13:23.580829Z`
  - SHA-256 identity binding:
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
  - artifact digest (SHA-256):
    `d31cbbd7547d132fa2f1e2d4ef44a11bbd7b85622db2daabea6fdfc4991d99a6`
  - plan digest (SHA-256):
    `a6d490c7e8e454ebb27c09aee06544e38043d9e19c4f708373dcbe42e9da6f0f`
  - raw frame digest (SHA-256):
    - 역할 A:
      `6fe599cde8656bc3ab457036676206acc92e070a88b9bc3da81e92ceb6c2744b`
    - 역할 B:
      `6ce5b28116b296b2ed57952c3a49aef766592bea68cdbf9588327469ae811993`
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 장비 write가 0회라 복원 대상은 없다.
  probe 전후 장비 설정은 변경하지 않았고 recovery 프로세스는 종료 직후 다시 running 상태가 됐다.
- **이 실행으로 새로 확정된 사실:**
  - exact source-attested transport probe가 홈서버에서 두 Pro의 strict explicit reply와 원본 frame을
    보존했다.
  - 현재 `independent` 상태에서는 두 장비가 모두 같은 네트워크 경로의 fresh strict request에
    `0x03`으로 응답한다. 따라서 직전 live `async_slave` epoch의 slave raw 0개는 이 strict-read
    경로가 역할과 무관하게 항상 실패하는 현상으로는 설명되지 않는다. 장비가 영구 오프라인이
    아니었다는 사실은 직전 실행의 post-restore raw에서도 이미 확인됐다.
  - 이 실행은 정보 0이 아니다. 다만 ASYNC 상태에서 실행하지 않았으므로 Q2-target 판정은 바뀌지
    않는다.
- 이 실행으로 확정하지 못한 것:
  - `async_slave` 상태에서 slave가 `0x04` report만 보내고 strict `0x03`에는 timeout하는지,
    explicit reply 뒤 schema decode에서 실패하는지는 아직 `UNKNOWN`이다.
  - 이전 ASYNC 실패의 원인이 Linkage 역할 자체인지, staged schedule·TimerON·동시 pair-read 또는
    당시의 다른 상태 조합인지는 판정하지 않았다.
  - master Mode·timing 추종과 slave per-mode Flow는 계속 `UNKNOWN`이다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
  - 먼저 기존 preserved live raw `JFR-a3adb738c660`과 exact commit을 write 없이 다시 분석해,
    slave frame 0개의 원인을 기존 산출물만으로 더 좁힐 수 있는지 확인한다.
  - master/async_slave 역할 설정과 probe 재실행은 **현재 승인되지 않았다.** 기존 분석만으로
    원인을 닫지 못하고 추가 실기의 가치가 있다고 repository maintainer가 다시 판단한 경우에만,
    repository maintainer와 on-site hardware approver의 새 명시적 승인 및 별도 동결 해제 커밋을
    먼저 갖춘다.
  - 위 조건을 모두 충족한 경우의 최소 operation은 역할 A/B를 master/async_slave로 설정하고
    앱을 완전히 종료한 뒤 동일 probe를 expected Linkage `master` / `async_slave`로 한 번
    실행하며, 현장 중단 수단과 종료 뒤 `independent` 복원·fresh raw 확인을 포함한다.
  - probe 결과가 report-only timeout이면 raw를 오프라인 분석하고, explicit reply면 지난 pair-read
    경로와 비교한다. 어느 경우에도 `0x04`를 Q2 sample로 승격하지 않는다.
