# 새 schema의 두 Pro 제어 자격

사용자의 HA 실제 조작 및 이후 Async/시간표 적용 요청에 따라, [직전 자기 원복](2026-09-16-ha-control-owned-pro-restored.md)
결과를 `2b72726`으로 먼저 기록한 뒤 기존 고정9기능 producer를 한 번 실행했다. 범위는 `6d8c40e`다.
질문은 **현재 제품 코드에서 두 Pro의 기존9기능 실제 명령·응답과 자기 원본 복원이 성립하는가**다.
새 native 결속·Bar/리턴/도징 write는 없다.

## 입력·실행

- source `d79289714dc90e08cfeabfb1646922d673b9f8a0`, image
  `85a387b10671556fac3c8c2684e32a79f0c431ac50a64369c6ae54d65d3a78e5`.
- schema `3e6b0c5a2d8c5cfe9999b0035cc7a2de94add52faef1d95b22ecf8f60c85968b`.
- 별도 OBSERVER config/새 manifest, 쓰기 허용 장비2개, 기존 production config·40receipt 미교체.
  기존 config SHA `2a3a66ea3e6f7b15b7c8b1a241d9b7d5f3bdaf5f2ce5e40357d9f05efe7e4ea7`.
- operation `pro_control_qualification_d792897_01`, UTC
  `2026-09-16T07:23:44.142487Z–07:25:58.003486Z`, exit0/stderr0B.
- 준비40/F20, 시간표40→39, TimerON/OFF, sine/constant, frequency21/20,
  Feed1/2, custom6/5, Flow39/40, powerOFF/ON, master/independent의 기존 고정 순서다.
- source38 actions 모두 ACK/VERIFIED. 자기 원복6 actions 중4 ACK/VERIFIED와2 NO_CHANGE.
  총 물리 send/ACK는42/42이며 NO_CHANGE를 전송으로 세지 않는다. 재시도·새 source 반복0.

## 원복·자격

최종 direct reply는 아래 Pro `07:25:56.407591Z`, 위 Pro `07:25:57.429286Z`다.
원 wire를 decode하여 action3·status bytes를 대조하고, 최초 reply 원본과 각432B 및8controls를
재비교해 모두 일치했다. 시각·Auto·현재 슬롯은 원복 판정 대상이 아니다.
둘 다 independent/ON/TimerON/constant이며 아래30/F32, 위40/F34, Feed10/custom5다.

source VERIFIED/rev191, detach VERIFIED/rev27, assist는 필요하지 않았다.
원본 복원 뒤 **18 device-function pair / 36 receipts**를 새 후보로 발급했다.
schedule/schedule_enable/manual_mode/manual_frequency/feeding_duration/custom_wave_frequency/
manual_flow/power/linkage_role의9종×2대다. 기존 원Async ACK 미확인은 이 자격으로 소급 변경하지 않는다.

- source SHA `6d61b26fe13f9f39a0fb155ddcd887402b75aa3db4924e4869e19fb5151e7187`
- detach SHA `199ed4c0a7e7203aa0ea82624155f47709ea74cf09a6d769fc2816e72b385234`
- 보고서 SHA `d8c4b65b46c9b95826b3bf4a460d98dde67d4c9a1d5579ffffaeaa03b36c8769`
- opaque artifact `control-activation-20260916.Acq8Dn/qualification-evidence`
- evidence archive SHA `a6357090507898dcd1fb9edce2262cdcb4d29f1d2e1a0b14d113586e740c158d`
- candidate archive SHA `08cd202368a8f4d1bc09e200ded9fd635b65509a04761a3a40d0d4092c9a93c5`
- stdout SHA `a0965cd93f1895e71fb320cb38fe166130ee9379d397fd404dc31f5ff403d44c`

evidence321파일 중318blob의 SHA를 전수 대조했다. 실제 frame은(a), journal/receipt는(b)다.
기존 receipt 검증 merger도 새18pair/36receipt를 수용하고, 현재 catalog의 ignored qualification은0이다.
이는 시험한 기능·물리 binding의 자격이지 모든 값·물리 유량·위상·생물 안전의 계측은 아니다.

**새로 확정된 사실:** 새 schema에서 두 Pro9기능의 실제 전달·응답 및 자기 원본 복원이 성립했다.
이 기록 시점에 HA 제어 generation은 준비 상태이며 아직 기동하지 않았다. 다른4장비 제어와
후속 Async/일일 시간표 재적용은 별도다.
