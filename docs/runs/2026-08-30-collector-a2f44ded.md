# run: 2026-08-30-collector-a2f44ded

- 목적(한 문장): 현재 collector commit이 두 Local Wavemaker Pro에서 30초 cadence의 완전한
  acquisition-only 시리즈를 보존하고 오프라인 재검증할 수 있는지 확인한다.
- 이 실행이 답하려던 질문: "source-attested write-free collector가 현재 실장비 상태에서
  18 pair를 누락 없이 수집하고, pair timing과 안정된 independent baseline을 보존하는가?"
- 실행한 commit SHA: `b6594ef7c0229701e8db4e49629441df7d1934bf`
- 사용한 명령·주요 파라미터:
  - exact commit의 fresh detached clean source, external dependency target, project 미설치
  - systemd transient service의 direct `ExecStart` argv(private 값 치환): `python3.12 -B -P -m
    jebao_flow.read_only_collector_cli --config <private-config> --first <logical-role-a>
    --second <logical-role-b> --output-root <private-artifact-root> --samples 18
    --cadence-seconds 30 --collector-commit
    b6594ef7c0229701e8db4e49629441df7d1934bf --timeout 5`
  - `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, external pycache
  - 실행 직전 collector-focused suite: 77 passed
    - `tests/unit/test_read_only_collector.py` 35개
    - `tests/unit/test_read_only_collector_cli.py` 16개
    - `tests/unit/test_source_attestation.py` 6개
    - `tests/unit/test_protocol_session.py` 20개
    - write-freedom 핵심 검사:
      `test_collector_import_graph_does_not_load_device_or_frozen_modules`,
      `test_real_transport_pilot_series_sends_no_control_frame`
  - 실장비에 사용한 uninstalled attested runtime에서 전체 suite를 실행하면 1,359 passed /
    2 failed였다. 두 실패는 group import-graph subprocess가 `python -I`로 external
    `PYTHONPATH`를 제거해 `pydantic`을 찾지 못한 환경 차이였고, dependency path를 subprocess
    `sys.path`에 직접 넣은 재검사에서는 두 검사 모두 `forbidden: []`로 통과했다. 따라서
    실장비 preflight는 위 77개를 재현 가능한 고정 범위로 사용했다.
  - verifier가 commit blob에서 재계산한 collector runtime source digest:
    `3421fa9194ca20d65c42a4634819d833ad1e77cf7fd0bab9dfbd0b39254a1aef`
  - collector plan이 scope를 `acquisition_only_not_q2_boundary`, Q2 판정 권한을
    `not_authorized`로 고정
- 종료 지점(stage / failure code): `pilot_completed_all_acquisitions_accepted`, exit 0
- 장비가 실제로 돌려준 값:
  - 18/18 pair, 36/36 sample이 `accepted`; rejected 0, read failure 0
  - 36/36 원본 프레임이 explicit state reply이며 각 프레임의 exact wire bytes를 보존
  - 실제 pair 시작 간격(`pair.json`의 `attempt.started_monotonic_ns` 연속 차):
    최소 29,995.989 ms / 중앙값 29,999.761 ms / 최대 30,009.481 ms
  - scheduler 최대 lateness: 9.774 ms
  - pair completion gap: 최소 10,545.568 ms / 중앙값 11,111.630 ms /
    최대 11,269.170 ms
  - 36/36 state observation에서 active fault와 invalid schedule slot이 없고 schedule parameter
    range와 device-local time 조건을 통과
  - ordinal 0과 17에서 두 역할 모두 `independent`, `TimerON`, `AutoMode=sine`; 역할 A의
    `AutoFlow=50`, 역할 B의 `AutoFlow=70`
  - 역할별 schedule image digest는 18 sample 동안 각각 한 값으로 유지:
    - 역할 A: `60edc697ebe4492259c6df01b9f92726af5648343f9b8e24950d721fa8f56b99`
    - 역할 B: `c50331c20d220b2c51b2022863d29e5fc760d7ede71c299ec4f606e597d29426`
  - 각 역할의 18개 452-byte status body는 장비 시계의 시·분·초 3바이트를 제외하면
    byte-identical이었다. 따라서 관측된 sample 시점에는 schedule뿐 아니라 디코딩된
    `SwitchON`, `TimerON`, `Linkage`, `Mode`, `Flow`, `Frequency`, `AutoMode`, `AutoFlow`,
    `AutoFreq`와 active-problem 상태도 각각 변하지 않았다.
  - 장비 `NowTime`을 host monotonic 경과와 대조한 파생 관측:
    - 역할 B는 host 경과와 ±1.03초 이내로 일치
    - 역할 A는 지속적으로 9.4~10.4초 뒤처졌고 단조 증가는 유지했으나, 30초 step 사이에
      19초·20초·41초의 이산 도약이 관측됨
    - 장비 시계 값은 등급 (a), host timing과 파생 비교는 등급 (b)
  - 증거 등급:
    - reply action과 status body exact bytes: **(a) preserved raw artifact**
    - source attestation, host UTC·monotonic 시각, identity binding, plan·series claim,
      decode summary와 timing 통계: **(b) preserved structured/durable daemon artifact**
    - 컨트롤러 부재 점검, recovery 중지·재기동과 private 설정 잠금 상태:
      **(c) reconstructed operator observation**
  - opaque artifact id:
    - plan: `JFP-b283bd4219a10563b67a793f4db5e009`
    - series: `JFS-a2f44ded609b34adab1425c1dcc40c0e`
  - UTC span (시작 / 종료): `2026-08-30T00:53:51.477734Z` /
    `2026-08-30T01:02:43.697507Z`
  - SHA-256 identity binding:
    - `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`
    - `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`
  - artifact digest (SHA-256):
    - plan: `c30bd4d1ff637d81bca94fffec9483d547884e53a4bd474ccb2cc1fd39ed2d90`
    - series: `df6803b99548ffd68b910518cce33d4277859213a238b1ae4e1381671d29ddf2`
- 오프라인 검증:
  - collector 자체의 completion 검증 PASS
  - 같은 exact commit의 별도 verifier가 source attestation, plan, ordinal 0~17의 완전한 집합,
    raw/sample/pair digest, physical binding, explicit reply action과 terminal marker를 재계산해 PASS
  - 최초 별도 verifier wrapper는 plan 필드명을 잘못 참조해 artifact 검증 전에 실패했다. 올바른
    `collector_runtime_source_digest_sha256` 필드로 수정한 새 read-only verifier에서 PASS했으며,
    collector 산출물이나 저장소 코드는 변경하지 않았다. 이 별도 verifier 실행 사실은 당시
    명령 결과에 근거한 **(c) reconstructed operator observation**이며, 입력 raw·plan·series의
    등급 (a)/(b)를 대신하지 않는다
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 이 실행은 source-attested acquisition-only
  collector라 schedule·role 원복 대상이 없다.
  - 실행 전후 hardware-safety artifact content-set digest가 동일했다는 비교는 실행 전 값을
    별도 artifact로 보존하지 않았으므로 **(c) reconstructed operator observation**이다.
  - postflight의 보존된 hardware-safety content-set digest는
    `a2846e292aba0593422d51315c44403299078f1f56d612f1642933563241e558`이다. 단일
    `native-linkage-intent.json`은 `phase=terminal`, `outcome=experiment_failed_restored`이고
    digest는 `b6b67dbecbe10ae277c23727737fface66b298c6db6fdfd1028760662d9e8569`였으며, 나머지
    고정 intent/journal과 emergency-stop latch는 없었다. 이 항목은 **(b) preserved
    structured/durable daemon artifact**이고 장비 상태 자체를 증명하지 않는다.
  - recovery 중지는 `2026-08-30T00:53:49.719Z`, collector 첫 sample은
    `2026-08-30T00:53:51.478Z`, 재기동은 series 종료 155 ms 뒤인
    `2026-08-30T01:02:43.852059337Z`였다.
  - 실행 후 recovery process running, private 설정 `dry_run=true`와 모든 장비
    `allow_hardware_writes=false`, TCP 12416 established connection 0을 재확인했다. supervisor가
    clean/idle 상태를 별도로 보고했다는 뜻은 아니며, process·잠긴 설정·artifact terminality만
    확인한 **(c) reconstructed operator observation**이다.
- **이 실행으로 새로 확정된 사실:**
  - exact commit `b6594ef7`의 source-attested collector가 현재 두 Pro에서 18 pair / 36 exact
    explicit-reply frame을 모두 받아 durable series를 만들고 오프라인 재검증을 통과한다.
  - 요청 cadence 30초에서 실제 pair 시작 간격은 29.996~30.009초였고, 이 단일 실행에서는
    연속 catch-up이 관측되지 않았다. 첫 pilot의 21.2~22.5초 실제 간격보다 일정한 30초
    cadence를 보존했다.
  - 현재 host-side pair completion gap은 10.546~11.269초였다. 이 실행과 첫 pilot에서 같은
    크기의 순차 pair gap이 반복 관측됐으므로 이후 manifest의 pair-gap 후보와 headroom을 정할
    때 두 preserved series를 함께 근거로 사용할 수 있다. 그대로 상한으로 복사하지는 않는다.
  - 18/18 sample에서 두 역할은 `independent`였고 역할별 `AutoFlow`(A=50, B=70)가 변하지
    않았으며, 각 schedule image digest도 18 sample 동안 동일했다. 이는 현재 baseline을
    보존하지만 ASYNC 동작을 판정하지 않는다.
  - 18 sample의 status body가 시계 3바이트 외에는 byte-identical이어서, **관측된 sample
    시점들에 한해** schedule 외 control과 fault 상태도 변하지 않았다. 30초 sample 사이의 순간
    변경까지 배제하지는 않는다.
  - 첫 pilot에서 약 22~25초로 보였던 장비 시계 갱신은 sample cadence aliasing이었다는 해석이
    이번 30초 cadence raw에서 강화됐다. 특히 역할 A의 `NowTime`은 정상적으로 단조 증가하면서도
    host보다 약 10초 뒤처져, retry5 방식의 2초 device-clock skew gate가 정상 장비를 거부할 수
    있음을 실측했다. 원인은 `UNKNOWN`이며 이 1회 관측만으로 새 게이트를 만들지 않는다.
- 이 실행으로 확정하지 못한 것:
  - scope가 `acquisition_only_not_q2_boundary`이고 Q2 판정이 `not_authorized`이므로,
    `async_slave`의 슬롯별 `AutoFlow` 독립 적용 여부는 여전히 `UNKNOWN (PARKED)`이다.
  - 앱 live-write, independent control epoch, ASYNC epoch와 exact restore는 수행하지 않았다.
  - 장비의 물리 유량·파형·위상은 측정하지 않았다.
  - 수신 raw는 송신 프레임 pcap이 아니다. device-state write 0은 source attestation, static
    import graph·transport 테스트와 실행 절차로 뒷받침되지만 수신 raw만으로 증명하지 않는다.
  - Home Assistant에서 Jebao/Gizwits 관련 활성 config entry가 없고 서버의 다른 Jebao controller와
    TCP 12416 established connection이 없음을 확인했지만, 휴대폰 앱이 완전히 종료돼 있었다는
    사실은 서버에서 증명할 수 없다.
  - 한 host·한 network path·약 532.220초의 단일 실행이므로 장기 tail을 보장하지 않는다.
    또한 30초 sample 사이에 발생했다가 되돌아온 순간 변경은 안정된 schedule digest만으로
    배제할 수 없다.
  - 앱이 만든 제3자 baseline을 되돌릴 승인·검증된 exact restore 수단은 여전히 없다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것:
  - transport·timing 확인을 위한 collector 반복은 여기서 멈춘다. Q2는 park 상태를 유지하고,
    제품 트랙의 software-independent actuator와 그룹 런타임을 계속한다.
  - Q2 관측을 재개하려면 앱 write 전에 제3자 baseline을 되돌리는 exact restore 수단·권한과
    사전 실장 검증을 먼저 확보한다. 이 기록을 근거로 동결 write 하네스를 재개하지 않는다.

## 실행 전 준비 중단 기록

이 실기 전에 네 준비 시도가 모두 collector network call 전에 fail-closed 됐다. collector
acquisition에 진입하지 않았고 장비 상태를 변경하지 않았으므로 별도 실기 횟수로 세지 않으며,
어느 runtime·artifact도 성공 실행에 재사용하지 않았다. 네 번째 준비 중단 뒤 recovery를 정상
상태로 되돌리는 운영 재기동은 이 collector 실행 횟수와 분리한다.

1. 호스트에 `python3.12`용 `ensurepip` 구성요소가 없어 fresh venv 생성이 중단됐다.
2. product-key 상수의 잘못된 import를 77-test preflight 뒤 발견해 network call 전에 중단했다.
3. 77-test preflight 뒤 pre-mutation assertion이 발생해 중단했다. 같은 준비 상태를 read-only로
   재확인했을 때 locked pair와 terminal intent 조건은 통과했고, private 설정과 container 상태는
   바뀌지 않았다. 이 assertion의 더 구체적인 원인은 preserved artifact가 없어 단정하지 않는다.
4. host의 private 설정을 잠갔지만 실행 중이던 Docker bind mount가 교체 전 inode를 유지해,
   mounted-config assertion이 collector 시작 전에 중단했다. 설정 파일 권한이라는 한 원인으로
   recovery가 `2026-08-30T00:51:42.444Z`~`00:52:21.585Z` 사이 9회 연속 fail-closed
   재시작했고, supervisor는 약 117초간 부재했다. container가 읽을 수 있는 owner와 mode
   `0600`으로 고친 뒤 `dry_run=true`와 모든 write-disabled 설정을 읽는 running process를
   확인했다. 이 구간은 성공 epoch 시작 전이며 겹치지 않는다.

그 뒤 완전히 새 runtime에서 collector acquisition을 단 한 번 실행했고, 그 실행이 18/18 pair를
완료했다.
