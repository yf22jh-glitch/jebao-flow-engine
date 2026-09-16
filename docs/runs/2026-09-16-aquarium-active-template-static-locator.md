# Aquarium 활성 템플릿 선택 경로의 정적 재확인

- 질문: Mac 앱의 현재 Aquarium 제품에서 선택하는 템플릿을 패키지 이름·수정 날짜 추측 없이
  특정하고, 실제 내용으로 자격을 결속할 수 있는가.
- 제품 source: `a238e92`. 장비 write·직접 장비 세션·MQTT publish·자격 발급·서비스 교체0.
  실행 중인 앱 창은 읽기만 했으며 GUI 입력0이다. 현재 화면은 DC였고 Aquarium과 구분했다.
- 이 기록의 관측 구간: `2026-09-16T12:45:16Z–2026-09-16T12:58:06Z`.
  앞선 컨테이너 탐색 전체 시간이나 실기 구간을 뜻하지 않는다.
- 설치 앱3.4.18/build341800000의 본체 SHA-256은
  `97b8eb17a915b8071dcef16c177f1e5bc3ab31e154510c69e6d3f9af2a533c71`이다.
  이전 Bar 분석과 같은 hermes-dec `9d215b1f2176f8e8007bd6a02aa4c5a66e1bcf59`로
  재분석했다. 분석 도구의 공개 소스 다운로드는 있었으나 vendor template fetch는0이다.
  원문 앱·decompile·URL·cache locator와 비밀값은 저장소에 넣지 않았다.

## 선택 경로와 결과

literal/register 대입만 해석해 compiled config62개와 product key의 유일성을 확인했다.
vendor JavaScript를 실행하지 않았다. Aquarium 제품 SHA-256은
`cba8c46ca278899bb71928fe60812abd8f1bd315f761787003df322f9b209e29`이다.

Aquarium config의 최초 `templateId=auto.1`을 최종 선택으로 읽으면 틀린다.
`useOtherProductTemplate`이 가리키는 config를 장비 페이지 getter가 찾아 그쪽의
templateId와 URL을 사용한다. 선택된 제품 config SHA-256은
`821567144227878306af0f779ca4e56b758fdcb5aa5807fdeaa721ddd93aa44d`,
templateId는 `com.gizwits.jiebao.rn.shuibeng`, URL 원문 SHA-256은
`18b0e5c4f4e73c0f0cfab61510fc662a8dce56863961c8b67b92837c6fbfe28f`다.
그 config에는 추가 참조가 없다. 이후 component loader는 선택된 URL로 cache를 찾는다.

Claude가 exact URL의 vendor MD5 locator를 읽기 전용으로 대조한 `12:55Z` 보고에서
**해당 root는 로컬 캐시에 없었다.** 다른 locator의 shuibeng 두 사본은 존재하지만 이번
선택의 내용 근거가 아니다. 초기 auto.1이나 기존 Pro/Bar digest도 대용으로 쓰지 않는다.
같은 재구성으로 Pro·Bar의 기존 URL digest를 재현했으며 exact cache 내용도 기존 배포와
일치했다. 따라서 패키지 이름이 다르다는 이유로 기존 Pro 귀속이 잘못됐다는 추론은 철회한다.

## 조사 중 정정과 증거 한계

- 앱 컨테이너가 bundle-id 이름으로 없다는 이유로 캐시 부재를 단정했던 이전 보고는 잘못됐다.
  UUID 이름의 컨테이너가 존재하며 metadata identifier로 확인했다.
- 본 에이전트의 cache 목록 읽기는 macOS 권한에서 거부됐다. `rglob`의 빈 결과는 부재 근거가
  아니며, 별도로 읽을 수 있었던 Claude의 명시적인 exact-root 대조와 구분한다.
- 첫 권한 오류의 Bar 경로를 Aquarium 경로로 잘못 전달한 중간 보고도 정정했다. 이후 전체
  config 재구성과 제품별 locator 대조 결과만 결론에 사용했다.
- 제품별 datapoint 등장이나 지원 product allowlist는 템플릿의 호환 후보 근거이지 현재
  선택 근거가 아니다. 최초 config와 최종 참조 config도 구분해야 한다.
- 이는 보존 raw device frame이나 durable daemon claim이 아니다. 정적 조사 결과와
  독립 에이전트 관측을 재구성한 기록이며, 장비의 수용·출력·물리 안전을 증명하지 않는다.
  파생 요약은 opaque artifact `aquarium-template-20260916.dkDg0S`에 보존했다.
  해당 `README.md` SHA-256은
  `3ba11fb179db06a2c4ed6ccb690977d5a9385c852a07385aeb8355c884f9070b`이다.

**새로 확정한 사실:** Aquarium의 compiled 선택 경로는 참조 config의 shuibeng URL로
결정되지만, 그 exact cache bytes는 확인 시점에 없어서 active content binding은 미완이다.
캐시가 없다는 사실로 과거 다운로드 이력까지 단정하지 않는다.

대안은 (1) 현장 사용자가 Mac 앱의 Aquarium 화면만 열어 앱이 필요한 내용을 받게 한 뒤
동일 locator를 재검사하는 것, (2) 같은 앱 build의 해당 exact locator 내용을 이미 보유한
운영 기기에서 별도 제공받아 byte binding을 검증하는 것이다. 기존 후보를 임의 선택하지 않는다.
확보 전에는 실기 발급을 park하며 Pro/Bar 운영과 Aquarium 원래 설정을 그대로 유지한다.
