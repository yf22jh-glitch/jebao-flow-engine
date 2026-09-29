# run: 2026-09-29-pro-external-manual-transition-lock-stop

- 목적: 이전 [TimerOFF 미확인 실행](2026-09-29-pro-external-manual-activation-first-stop.md) 뒤, 상단의 baseline을 무송신으로 확인·종결하고 Master/Slave를 독립으로 해제한 다음 31행 시간표의 현재 Tide 행을 활성화한다.
- 질문: 역할 해제 뒤 상단 TimerOFF가 검증되는지, 이어서 현재 14:00 행과 원 Master/Async 결속을 완성할 수 있는지?
- 사용자 승인: 마지막 31행 시간표의 실제 활성화. 실제 유량·위상은 계측하지 않았다.
- 실행 source `0deabd9`, image `sha256:22370c07146c83e3143a11a245522e2b201bfbd9565078d403c163c9a1e72931`. 기존 운영 제어 서비스는 재시작하지 않았다.
- UTC 실행·관측 구간: 2026-09-29 05:20:12–05:25:19.
- physical identity binding SHA-256: 하단 Master `4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea`, 상단 Async `97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c`.
- 계획 digest: `e60eb4dd952dffcc8daf96a03e1380e12f32644efa1e80e53a9848392321f3a5`.

## 실행과 결과

기존 미종결 `pro_prep_20260929140946_e60eb4dd952dffcc_slave_timer_off`는 fresh raw에서 원 `TimerON=true, SwitchON=true, Linkage=async_slave`가 그대로여서 control frame **0회**로 `ABANDONED` 종결했다. 사전 저널 digest `5c7d658e697afa7f138139dabb8cb7f628a5d5ff49d9d130c08e9c01c44fdacb`, 종결 저널 digest `46ed984d5d7fc15b60b5a566fa49e260d51d728faa992c033cd7d20e4cd88d93`(등급 b), 새 관측 raw blob digest `88eaf0fe9caf348a6052303577e4d62dc383b34949cf321f351172e7ca6f8b7b`(등급 a)이다. 첫 송신이 결코 도달하지 않았다는 주장은 하지 않는다.

수정된 준비 operation `pro_prep_20260929142055_e60eb4dd952dffcc`는 하단 Master 해제 → 상단 Slave 해제 → 상단 TimerOFF → 상단 Async 재결속 → 하단 Master 재결속 **5 child 모두 1회 송신·ACK·fresh readback VERIFIED**였다. 각 child journal digest(등급 b)는 순서대로 `63d41bc25cbdab33280c4290d66ef19d42fa281624eceeaced0461156495ea56`, `7318dab8ea701b1d01ee77f8bbc9e3c9c4c233da91c274dee77eb4115b6f21dc`, `b35eee0d9c4e435510efbbc8e6a883baf538b4166ab42c6dcd9f937546b3aa65`, `c71c609df93f275512a59d14eba4bce9755c20cc0879e56d1dc8e6bdb5e08b9c`, `54728681b4d3a35c9f3e8ef2fbbac0297a2033a197d26fc381e2cdd9885f2c78`이다. 장비 시간표 image write는 0이었다.

현재 행 operation `pro_manual_20260929_0840_e60eb4dd952dffcc`에서는 하단 Master 해제 → 상단 Flow45 → 하단 Flow40 → 하단 Mode=Tide까지 **4 child 모두 1회 송신·ACK·fresh readback VERIFIED**였다. 순서대로 journal digest(등급 b)는 `21f07373ee27e9c6789d86fb11b59783b4c05881a49efa21c83e4abbcb84ac5e`, `b0bf2141a9645622f390ac0c880a4caa372c26f6de037e6fcc50e80edf44e25e`, `e4c5117e46d8586b62b2337e741addd4b0e49e62215833f4eb115aeaf3dbb25f`, `1108e5a07850f9560b0cd63670e334a82542d9a3fd4a980b82a6385eaf6d6f7b`이다.

마지막 하단 `Linkage=master` child에서 `LocalControlLeaseBusyError`가 발생했다. **그 child의 저널은 생성되지 않았고 송신도 0회**다. Parent는 `stopped`, 검증된 prefix는 4/5, 전체 활성 상태는 `paused`, 미종결 child 저널은 0건이다. 동일 행을 처음부터 재생하지 않았다.

중단 뒤 fresh pair는 하단 `independent·TimerOFF·ON·Tide·Flow40/F100`, 상단 `async_slave·TimerOFF·ON·Pulse·Flow45/F100`, 양쪽 fault0이었다. 양쪽 image SHA-256은 전 실행과 동일한 하단 `0148b591d380d9fdeca02fed4b38f97c84b627ee520db0c256acd7fd20507e87`, 상단 `4081e18a0412ad63e7773c770b43ba0cf109d6d86bbf1a622babdafb042ba835`다. fresh raw blob digest(등급 a)는 하단 `4210eeb3a2c67b308fbd0ddc6ea148e278be973f651ac18763384444da481bb4`, 상단 `f00814fd60a712ec80752baed04fe1a910534c29b72ad82ba7df7643d79ad8b9`이다. 두 장비가 설정 범위 안의 값을 보고했지만 **계획한 쌍 파형은 아직 불일치**한다.

## 판정과 후속 조건

**새로 확정된 사실:** 이번 조건에서는 두 역할을 먼저 해제한 뒤 상단 TimerOFF가 ACK/readback으로 검증됐다. 그러나 행의 마지막 재결속은 잠금 충돌로 송신 전 종료되어 시간표 실행은 아직 활성화되지 않았다.

**미확정:** 첫 TimerOFF ACK 부재의 원인이 결속 그 자체였는지, 순간 전송 문제였는지. 현재 Master 독립 상태에서 Slave가 Async라고 보고하는 물리적 의미도 파형 계측 없이 단정하지 않는다.

후속 작업은 검증된 4 child를 재송신하지 않고, 마지막 child의 저널 부재와 현재 fresh pair가 검증된 직전 상태와 일치함을 확인한 뒤 **남은 재결속 1회만** 실행하고 최종 쌍 상태·시간표 상태를 검증하는 것이다. 이 결과 파일을 커밋하기 전에는 `src/`를 바꾸지 않는다.
