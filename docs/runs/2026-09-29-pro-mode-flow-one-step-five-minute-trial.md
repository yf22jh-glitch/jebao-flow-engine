# run: 2026-09-29-pro-mode-flow-one-step-five-minute-trial

- 목적: 현재 Pulse인 하단 Master와 상단 Async Slave에서, Slave `Flow`를 한 단계씩 올리며 Master 파형을 Sine→Pulse로 5분 간격 전환한다.
- 질문: Master 역할 해제 → Slave Flow·Master Mode 변경 → Master 재결속 뒤, 두 장비가 새 파형과 Slave Flow를 보고하고 5분 동안 유지하는가?
- 승인 범위: 사용자의 직접 지시. 반복적인 현장 체류·차단 가능 여부 질문은 하지 않았다. 실제 물리 유량은 별도 계측하지 않았다.
- 실행 source HEAD: `0d46bbfcf38993ac17238ee9a0ebd83e077d9fa9`; 운영 image `sha256:c6a7e44f3fcef05cb209ee6247827640c71e65f7d4e1f43191da0c6c9921779a`.
- 송신 스크립트: `ops/pro_mode_flow_trial.py`, SHA-256 `ac55464431a6f6cb1e640ab84f879f988a753da6159eb91e8861990174ae729f`. 현재 manifest·catalog 권한·출력 범위·미완료 저널을 확인하고, 기존 `LocalControlCoordinator.execute`의 identity·lease·journal-first·single-send·ACK/readback·rollback 경로만 사용했다. Slave의 action-4 보고를 수용하는 writer adapter 외에 payload 인코딩은 바꾸지 않았다.
- UTC 실행·관측 구간: 2026-09-29 03:48:08–04:00:36.
- physical identity binding SHA-256: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`; 상단 Async Slave `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.

## 실행과 durable 결과

시작 fresh raw: 하단 action3·Master·Pulse·Flow36·TimerOFF·ON, 상단 action4·Async·Pulse·Flow38·TimerON·ON. 둘 다 fault0. 원 432바이트 image SHA-256은 하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`, 상단 `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`이었다. 시작 원본 raw blob SHA-256은 각각 `c06ff798bbaf04ead074a35845aacbc55d46e325df6532302166ae7a126d7a8d`, `f0c5072194747c1ee69841ad40abd1f0535fc86cbaa50b32515aac4563ce6eec`이다.

| 구간 | UTC 시작 | 명령 | 저널 결과·ACK | 저널 SHA-256 |
|---|---|---|---|---|
| Sine | 03:48:08 | 하단 `{Linkage:independent}` | VERIFIED, ACK 있음, 1 send | `a1450e08aae78aec58aab88ccc7d0e62cc8d72939d345006431e644d9cfd4478` |
| Sine | 03:48:22 | 상단 `{Flow:39}` | VERIFIED, ACK 있음, 1 send | `dd522283ece749dfa7e30b0bd660d41f7a4638d281d4cb61a0aabefcd3349cbd` |
| Sine | 03:48:36 | 하단 `{Mode:sine}` | VERIFIED, ACK 있음, 1 send | `55a0053667e05e01768fef56d62fec00f9fcab45cdd043805474953e732532f3` |
| Sine | 03:48:51 | 하단 `{Linkage:master}` | VERIFIED, ACK 있음, 1 send | `404f8f818a46368f5e5f4dcc7bb554eec1d4ba7424eca119d6519ee5bd00da5a` |
| Pulse | 03:54:35 | 하단 `{Linkage:independent}` | VERIFIED, ACK 있음, 1 send | `1ff863dd5d555bb53f989833047f403e5f1bfc221ebc90d5d3ea9ce2a01b886c` |
| Pulse | 03:54:50 | 상단 `{Flow:40}` | VERIFIED, ACK 있음, 1 send | `d6088dbe00a8591f38b837a604640cee6b3253512e6d16f2cc38b07392e6d5a2` |
| Pulse | 03:55:04 | 하단 `{Mode:pulse}` | VERIFIED, ACK 있음, 1 send | `403a935fa68b55c0e4da5f5a46b57b9dac558f6967b0b465fb9f9cd9e528030d` |
| Pulse | 03:55:19 | 하단 `{Linkage:master}` | VERIFIED, ACK 있음, 1 send | `2440d73124c6602618118fae936188354755b5cb033b023603b31cf8686deb42` |

Sine 재결속 직후 두 장비는 Sine, 상단 `Flow=39`를 보고했다. 약 5분 뒤 다음 구간 직전 fresh pair도 동일했다. Pulse 재결속 직후 두 장비는 Pulse, 상단 `Flow=40`을 보고했다. 재결속 5분 이상 뒤 최종 fresh pair도 하단 Master·Pulse·Flow36·TimerOFF·ON, 상단 Async·Pulse·Flow40·TimerON·ON, fault0이었다. 두 schedule image는 처음과 끝이 SHA-256 exact였다. 최종 raw blob SHA-256은 하단 `778093cc747ea5ca168924d6b9853ed230ffcc0dc0d13b63c21570f1da6334a0`, 상단 `01f164bca808058144eb2bbaf2d8ad38e239795c8bafa779bb318dbfa02e6a75`이다.

저널 8건은 원본 record와 ACK digest가 있는 등급 (b) 증거이며, 시작·종료 raw frame은 owner-only blob으로 보존된 등급 (a) 증거다. 별도 제어 프레임 재송신 0, 총 물리 송신 8회, 미종결 저널 0, 서비스 restart 0. 시간표 image write 0.

## 판정

**이 실행으로 새로 확정된 사실:** 현재 역방향 Master/Async 쌍에서 Master 해제→Slave Flow 한 단계 증가→Master Mode 변경→재결속을 두 번 수행했고, 각 단계가 ACK와 readback으로 검증됐다. Sine·Flow39와 Pulse·Flow40 각각 5분 이상 보고값을 유지했다.

**확정하지 못한 것:** 실제 물리 유량·위상, Flow와 Mode를 두 장비에 단일 물리 프레임으로 동시에 쓸 수 있는지, ACK 없는 명령이 미적용인지 여부. 이번 8건은 모두 ACK가 있어 ACK 부재 처리 경로를 시험하지 않았다. 장치 자체 TimerON 및 image는 변경하지 않았고, 그 영향은 이번 판정 범위 밖이다.
