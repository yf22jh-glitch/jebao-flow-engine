# run: 2026-09-12-local-bar-active-template-static-binding

- 목적(한 문장): current app build와 current manifest의 정확히 한 Local Wavemaker Bar product
  binding에서 loader가 선택하는 template bytes를 cache 최신성 추측 없이 다시 특정했습니다.
- 이 실행이 답하려던 질문: mutable todo에 digest 없이 남아 있던 Bar positive-evidence 주장을 현재
  compiled product config의 product→URL→cache-locator 경로로 재현하고, 선택 bytes와 배제 후보를
  append-only 근거로 결속할 수 있는가.
- 실행한 commit SHA: governing D1m 정책은
  `689ca451d154ebcb35b551e82de44df6845f1374`이고, 검사한 repository source는 마지막 source landing
  `fe10bd5706a6c2c8b0e3a0b71be98bf7b7c62fc4` 이후 변경되지 않았습니다.
- 사용한 명령·주요 파라미터: 설치된 Jebao Aqua app의 version·build와 Hermes bundle bytes를
  read-only로 해시하고, 임시 `hermes-dec` checkout
  `9d215b1f2176f8e8007bd6a02aa4c5a66e1bcf59`의 기존 decompile 결과에서 current manifest의 Bar
  product digest와 일치하는 compiled config object를 exact-match했습니다. 일치 object는 정확히
  하나였고 그 template URL도 하나였습니다. raw product key·URL·cache locator·private path는
  출력하거나 기록하지 않았습니다.
- 사용한 명령·주요 파라미터(계속): URL 원문의 MD5를 vendor loader와 같은 cache locator로만
  유도해 같은 component의 `index.js` 후보 2개를 대조했습니다. locator exact-match는 하나였고
  `index.html`은 없었습니다. 파일 크기·mtime·directory 정렬은 선택에 사용하지 않았습니다. 별도
  network fetch, app 실행, device·broker 접촉은 모두 `0`입니다. running container에서는 별도
  read-only process로 current config·manifest만 읽어 configured Bar와 manifest Bar binding이 각각
  정확히 하나이고 current template count가 `1`임을 확인했습니다.
- 종료 지점(stage / failure code): static positive binding `PASS`. 선택된 `index.js`는 162,553 bytes,
  SHA-256
  `e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다. 다른 candidate는
  110,295 bytes, SHA-256
  `d6b35da90a9ee5db09f91a7819bfb5cf89c439d061e789a69af2b40706ebb6ab`이며 current URL locator에서
  도달되지 않아 제외했습니다.
- 장비가 실제로 돌려준 값:
  - 증거 등급: (c) reconstructed operator observation. 이 실행은 장비를 읽지 않았고, installed
    app bundle·cache와 임시 decompile을 별도 raw artifact로 보존하지 않았으므로 (a)·(b)로
    올리지 않습니다.
  - opaque artifact id 또는 안전한 상대 논리 경로: n/a (별도 보존 artifact 없음)
  - UTC span (시작 / 종료): `2026-09-12T14:33:58.513981000Z` /
    `2026-09-12T14:33:58.687778000Z`
  - SHA-256 identity binding:
    `197ae39afb4414b1c55c1c5873ece68813b7a7ac6b2e336ea8ba2011faa32b38`. 이는 raw product key의
    SHA-256이며 current config·manifest와 compiled app object 사이에 byte 동일했습니다.
  - artifact digest (SHA-256): installed 16,665,122-byte Hermes bundle은
    `97b8eb17a915b8071dcef16c177f1e5bc3ab31e154510c69e6d3f9af2a533c71`, URL 원문은
    `078b2e1bccdac4c0189c1fa40c19c6a191743ef7281ca7ae1045dfa226f51880`, 선택 template bytes는
    `e609fa34a6fc6fe1d22d27379a2c6c0ccd077cfba20d4bd3b500e0721782e329`입니다.
- 원복 검증 결과(어떤 수준의 확인이었는지 명시): 파일·service·manifest·journal·receipt·broker·장비를
  수정하지 않았으므로 원복 대상이 없습니다. 현재 template count `1`을 읽었을 뿐 Bar digest를
  current manifest나 running generation에 아직 넣지 않았습니다.
- **이 실행으로 새로 확정된 사실:** current app `3.4.18` / build `341800000`과 current Bar product
  binding 범위에서는 162,553-byte `e609fa34…e329` template만 compiled URL locator에서 도달되고,
  110,295-byte `d6b35d…b6ab` candidate는 도달되지 않습니다. 따라서 Bar content binding의 exact
  positive evidence를 cache recency 없이 다시 만들 수 있습니다.
- 이 실행으로 확정하지 못한 것: selected bytes를 host durable input으로 설치하거나 새 manifest에
  결속할 수 있는지, 배포 후 기존 두 Pro qualification·surface가 유지되는지, Bar firmware의
  `manual_flow` acceptance, 다른 app build·URL·향후 remote bytes는 확정하지 않았습니다.
- 다음에 이 지점을 다시 만나지 않으려면 필요한 것: write-free 조사도 positive content를 특정한
  즉시 app/build·product·URL digest, selected digest·byte size와 unreachable 후보를 append-only
  기록에 남기고, mutable todo의 체크박스만 증거로 사용하지 않습니다.
