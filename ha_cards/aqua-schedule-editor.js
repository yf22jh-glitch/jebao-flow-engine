/* GMP external + native Bar schedule editor. Save and device application are separate. */
const AQUA_SCHEDULE_MODES = Object.freeze({
  pulse: "Pulse", sine: "Sine", constant: "Constant", random: "Random",
  tidal: "Tide Rise and Fall", nutrient_transport: "Nutrient Transport",
  circulation: "Cross-flow", feed: "Feeding", custom: "Custom Wave",
});
const scheduleEscape = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);
const scheduleMinute = (v) => /^([01]\d|2[0-3]):[0-5]\d$/.test(v || "")
  ? Number(v.slice(0, 2)) * 60 + Number(v.slice(3)) : NaN;
const scheduleTime = (m) => `${String(Math.floor((m % 1440) / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const scheduleClone = (v) => JSON.parse(JSON.stringify(v));
const scheduleWithFrequency = (mode) => ["pulse", "sine", "random", "circulation", "custom"].includes(mode);
const AQUA_MCP_MODES = Object.freeze({pulse:"Classic · Pulse",sine:"Sine",random:"Random",
  tidal:"Classic · Tide",constant:"Constant",feed:"Feeding",stopped:"Stop"});
const scheduleMcpFrequency = (mode) => ["pulse", "sine", "tidal"].includes(mode);
const scheduleMcpCleanRow = (r) => r.mode === "feed" ? {time:r.time,mode:r.mode}
  : r.mode === "stopped" ? {time:r.time,mode:r.mode,flow:0}
  : {time:r.time,mode:r.mode,flow:r.flow,...(scheduleMcpFrequency(r.mode) ? {frequency:r.frequency} : {})};
const scheduleCleanRow = (row) => row.mode === "feed" ? { time: row.time, mode: "feed" }
  : { time: row.time, mode: row.mode, upper_flow: row.upper_flow, lower_flow: row.lower_flow,
    ...(scheduleWithFrequency(row.mode) ? { frequency: row.frequency } : {}),
    ...(row.mode === "custom" ? { custom_frequency: row.custom_frequency } : {}) };

function scheduleErrors(rows, min = 30, max = 45) {
  const errors = [];
  if (!Array.isArray(rows) || !rows.length || rows.length > 96) return ["운전 구간은 1~96개로 작성해주세요."];
  const times = rows.map((r) => scheduleMinute(r.time));
  if (times[0] !== 0 || rows[0].mode === "feed") errors.push("첫 구간은 00:00에 시작하는 운전 모드여야 합니다.");
  rows.forEach((r, i) => {
    const at = `${i + 1}행`;
    if (!Number.isFinite(times[i])) errors.push(`${at}: 시작 시각을 HH:MM으로 입력해주세요.`);
    if (i && times[i] <= times[i - 1]) errors.push(`${at}: 시작 시각이 중복되거나 순서가 뒤바뀌었습니다.`);
    if (!Object.hasOwn(AQUA_SCHEDULE_MODES, r.mode)) errors.push(`${at}: 지원하지 않는 모드입니다.`);
    if (r.mode === "feed") {
      const next = (i + 1) % rows.length;
      const end = times[next] + (next === 0 ? 1440 : 0);
      if (rows[next].mode === "feed" || end - times[i] !== 6)
        errors.push(`${at}: Feeding은 6분 뒤 운전 구간이 필요합니다.`);
    } else {
      ["upper_flow", "lower_flow"].forEach((key) => {
        if (!Number.isInteger(r[key]) || r[key] < min || r[key] > max)
          errors.push(`${at}: ${key === "upper_flow" ? "상단" : "하단"} Flow는 ${min}~${max} 정수로 입력해주세요.`);
      });
      if (scheduleWithFrequency(r.mode) && (!Number.isInteger(r.frequency) || r.frequency < 5 || r.frequency > 100))
        errors.push(`${at}: ${r.mode === "custom" ? "Run (%)" : "Frequency"}는 5~100 정수로 입력해주세요.`);
      if (r.mode === "custom" && (!Number.isInteger(r.custom_frequency) || r.custom_frequency < 5 || r.custom_frequency > 100))
        errors.push(`${at}: Stop (%)은 5~100 정수로 입력해주세요.`);
    }
  });
  return errors;
}

function scheduleMcpErrors(rows) {
  if (!Array.isArray(rows) || rows.length > 96) return ["MCP 보관 시간표 형식을 확인해주세요."];
  if (!rows.length) return [];
  const times = rows.map((r) => scheduleMinute(r?.time));
  const invalid = rows.some((r, i) => {
    if (!r || !Number.isFinite(times[i]) || (i && times[i] <= times[i - 1])) return true;
    if (!Object.hasOwn(AQUA_MCP_MODES, r.mode)) return true;
    const fields = r.mode === "feed" ? ["time", "mode"] : ["time", "mode", "flow", ...(scheduleMcpFrequency(r.mode) ? ["frequency"] : [])];
    if (Object.keys(r).length !== fields.length || !fields.every((f) => Object.hasOwn(r, f))) return true;
    if (r.mode === "feed") {
      const next = (i + 1) % rows.length;
      return rows[next]?.mode === "feed" || times[next] + (next === 0 ? 1440 : 0) - times[i] !== 6;
    }
    return !Number.isInteger(r.flow) || r.flow < 0 || r.flow > 100
      || (scheduleMcpFrequency(r.mode) && (!Number.isInteger(r.frequency) || r.frequency < 5 || r.frequency > 100));
  });
  return invalid || times[0] !== 0 || rows[0].mode === "feed" ? ["MCP 보관 시간표의 시각·Mode·Flow·Frequency·Feeding 6분을 확인해주세요."] : [];
}

function scheduleMcpPlan(rows, min = 30, max = 50) {
  const errors = scheduleMcpErrors(rows);
  if (errors.length) throw Error(errors[0]);
  if (!rows.length || rows.length > 48) throw Error("바형 장치 시간표는 1~48개 구간으로 작성해주세요.");
  if (rows.some((r) => r.mode === "stopped" && r.flow !== 0))
    throw Error("Stop 구간의 Flow는 0이어야 해요.");
  if (rows.some((r) => !["feed","stopped"].includes(r.mode) && (r.flow < min || r.flow > max)))
    throw Error(`바형 적용 Flow는 ${min}~${max} 범위예요.`);
  return {family:"bar",slots:rows.map((r,i) => ({slot_index:i,start:r.time,end:rows[i+1]?.time || "24:00",
    mode:["pulse","tidal"].includes(r.mode) ? "classic" : r.mode,
    ...(r.mode === "feed" ? {feed_time:6} : {flow:r.flow}),
    frequency:scheduleMcpFrequency(r.mode) ? r.frequency : 0,
    pulse_tide:r.mode === "tidal"}))};
}

function scheduleMcpObservedRows(observed) {
  if (!observed || observed.invalid_slot_indexes?.length || !Array.isArray(observed.entries))
    throw Error("바형 장치의 유효한 시간표 보고를 기다려주세요.");
  const entries = [...observed.entries].sort((a,b) => scheduleMinute(a.start)-scheduleMinute(b.start));
  const rows = entries.map((entry,i) => {
    const p=entry.parameters || {}, mode=entry.mode === "classic" ? p.pulse_tide ? "tidal" : "pulse" : entry.mode;
    if (entry.end !== (entries[i+1]?.start || "24:00")) throw Error("장치 표에 겹침·빈 구간이 있어요. 자동으로 변경하지 않습니다.");
    if (mode === "feed" && p.feed_time !== 6) throw Error("장치 Feeding 시간이 6분이 아니에요. 원본을 자동으로 바꾸지 않습니다.");
    return scheduleMcpCleanRow({time:entry.start,mode,flow:p.flow,frequency:p.frequency});
  });
  if (!rows.length || scheduleMcpErrors(rows).length) throw Error("장치 시간표를 편집 행으로 변환할 수 없어요.");
  return rows;
}

async function scheduleMcpDigest(plan) {
  const modes=["stopped","classic","sine","random","constant","feed"];
  const image=new Uint8Array(384).fill(0xee);
  for (const slot of plan.slots) image.set([
    ...slot.start.split(":").map(Number),...slot.end.split(":").map(Number),modes.indexOf(slot.mode),
    slot.mode === "feed" ? slot.feed_time : slot.flow,slot.frequency || 0,Number(!!slot.pulse_tide),
  ],slot.slot_index*8);
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",image)),b=>b.toString(16).padStart(2,"0")).join("");
}

function scheduleMcpMatches(plan, observed) {
  if (!observed || observed.invalid_slot_indexes?.length || observed.entries?.length !== plan.slots.length) return false;
  return plan.slots.every((slot) => {
    const e=observed.entries.find((entry)=>entry.slot === slot.slot_index), p=e?.parameters || {};
    return e?.start === slot.start && e.end === slot.end && e.mode === slot.mode
      && (slot.mode === "feed" ? p.feed_time === slot.feed_time : p.flow === slot.flow)
      && (p.frequency || 0) === (slot.frequency || 0) && !!p.pulse_tide === !!slot.pulse_tide;
  });
}

function scheduleInsertFeeding(rows, time) {
  const start = scheduleMinute(time);
  if (!Number.isFinite(start) || start === 0) throw Error("Feeding 시작 시각을 입력해주세요. 00:00에는 운전 구간이 필요합니다.");
  const end = (start + 6) % 1440;
  if (rows.some((r) => {
    const minute = scheduleMinute(r.time);
    return minute === start || (end > start ? minute > start && minute < end : minute > start || minute < end);
  })) throw Error("해당 6분 안에 다른 구간이 있어요. 기존 구간을 먼저 조정해주세요.");
  const prior = rows.filter((r) => scheduleMinute(r.time) < start && r.mode !== "feed").at(-1);
  if (!prior) throw Error("Feeding 전에 운전 구간을 작성해주세요.");
  const next = [...scheduleClone(rows), { time, mode: "feed" }];
  if (!next.some((r) => scheduleMinute(r.time) === end)) next.push({ ...prior, time: scheduleTime(end) });
  next.sort((a, b) => scheduleMinute(a.time) - scheduleMinute(b.time));
  return next;
}

const AQUA_SCHEDULE_CSS = `
  :host { display:block; box-sizing:border-box; max-width:1500px; margin:0 auto;
    padding:22px 24px 36px; color:#e7f4fa; }
  * { box-sizing:border-box; }
  ha-card { display:block; padding:24px; background:linear-gradient(140deg,#112736,#0c1b27);
    border:1px solid #2a475a; border-top:4px solid #4bb1ff; border-radius:24px; box-shadow:0 18px 50px #0003;
    font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif; }
  h1 { margin:5px 0 7px; font-size:27px; letter-spacing:-.04em; } p { margin:0; }
  .eyebrow { color:#59b9fc; font-size:10px; font-weight:750; letter-spacing:.16em; }
  .head { display:flex; justify-content:space-between; align-items:center; gap:18px; }
  .muted { color:#92adbe; font-size:12px; line-height:1.6; }
  .status { display:inline-flex; padding:9px 14px; border-radius:99px; background:#173b36;
    border:1px solid #326c59; color:#88edbf; font-size:12px; font-weight:700; white-space:nowrap; }
  .status.warn { color:#ffce87; border-color:#725e45; background:#40362a; }
  .status.offline { color:#adc1cd; border-color:#42596a; background:#213441; }
  .actions,.tools { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
  .actions { margin-top:20px; padding-top:18px; border-top:1px solid #2a4657; }
  button,.file { min-height:38px; padding:9px 13px; border:1px solid #375b70; border-radius:10px;
    background:#173444; color:#dceef7; font:inherit; font-size:12px; cursor:pointer; }
  button:hover:not(:disabled),.file:hover { background:#224659; }
  button:focus-visible,input:focus-visible,select:focus-visible { outline:2px solid #5abdfc; outline-offset:2px; }
  button:disabled { opacity:.4; cursor:not-allowed; }
  button.primary { background:#17604f; border-color:#358e74; color:#d4ffee; font-weight:700; }
  button.pause { color:#ffd090; background:#342e27; border-color:#695a46; }
  button.icon { padding:6px 10px; min-height:34px; color:#9cb6c7; }
  .revision { margin-left:auto; font-size:11px; color:#9bb7c6; }
  .notice { margin:12px 0; border:1px solid #2d4b60; border-radius:11px; padding:11px 13px;
    color:#a8c4d2; background:#122936; font-size:12px; line-height:1.6; }
  .notice.error { border-color:#8b5b4d; color:#ffc6b2; background:#3c2a28; }
  .notice.success { border-color:#387560; color:#a3edc7; background:#15382f; }
  .contract { display:flex; gap:18px; flex-wrap:wrap; margin:13px 0; color:#87a3b5; font-size:11px; line-height:1.6; }
  .contract b { color:#c5dce9; font-weight:600; }
  .template-bar { display:flex; align-items:flex-end; flex-wrap:wrap; gap:8px;
    padding:16px; margin-top:16px; border:1px solid #2e4d60; border-radius:14px; background:#122b39; }
  .template-picker { flex:1 1 250px; max-width:480px; }
  .template-picker > span { display:block; margin-bottom:7px; color:#96b4c5; font-size:11px; }
  .template-picker select { height:39px; }
  .template-bar .delete-template { color:#ffb1bb; }
  .template-form { display:flex; align-items:flex-end; flex-wrap:wrap; gap:8px;
    padding:14px 16px; margin-top:10px; border:1px solid #37596b; border-radius:12px; background:#173441; }
  .template-form label { flex:1 1 240px; max-width:480px; }
  .template-form label span { display:block; margin-bottom:7px; color:#a7c3d2; font-size:11px; }
  .template-form .muted { flex-basis:100%; }
  .timeline { display:flex; width:100%; height:15px; border-radius:8px; overflow:hidden; margin-top:18px; background:#233d4f; }
  .segment { min-width:1px; border-right:1px solid #0f2431; }
  .pulse { background:#5cb9ff; }.sine { background:#46d8c9; }.tidal { background:#968cf4; }
  .constant { background:#f8bf68; }.feed { background:#f375a9; }
  .stopped { background:#678397; }
  .random { background:#fa9b62; }.nutrient_transport { background:#8bcf7b; }
  .circulation { background:#63cedf; }.custom { background:#d78bed; }
  .ticks { display:flex; justify-content:space-between; color:#7895a9; font-size:10px; margin:7px 0 16px; }
  .legend { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; font-size:10px; color:#a9bfcc; }
  .legend i { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:5px; }
  .tools { padding:14px 0; border-top:1px solid #2a4657; }
  .tools .file { margin-left:auto; }
  input[type=file] { display:none; }
  input,select { min-width:0; width:100%; height:37px; padding:7px 8px; background:#0f2432;
    color:#e7f5fc; border:1px solid #325366; border-radius:8px; font:inherit; font-size:13px; color-scheme:dark; }
  input:disabled,select:disabled { opacity:.5; }
  input[type=number] { text-align:center; }
  input[type=time] { min-width:110px; }
  .columns,.row { display:grid; grid-template-columns:117px 63px minmax(150px,1fr) 100px 100px 100px 70px; gap:10px; align-items:center; }
  .columns { padding:12px; color:#8cacbe; font-size:11px; background:#122936; position:sticky; top:0; z-index:1; }
  .rows { max-height:720px; overflow:auto; border:1px solid #2a465a; border-radius:14px; }
  .row { padding:12px; border-top:1px solid #243f51; }
  .row.current { background:#173644; box-shadow:inset 3px 0 #4bb1ff; }
  .end { color:#8eaabb; font-size:12px; } .cell-label { display:none; }
  .empty-option { display:block; text-align:center; color:#7592a5; font-size:11px; }
  .row-actions { display:flex; gap:4px; justify-content:flex-end; }
  .feeding-note { color:#f2a4c5; font-size:11px; text-align:center; }
  .mode-options { display:grid; gap:7px; }.option-label { display:block; color:#92adbe;
    font-size:10px; line-height:1.3; margin-bottom:4px; }
  .feed-form { display:flex; align-items:center; flex-wrap:wrap; gap:10px; padding:12px; margin-bottom:12px;
    background:#2b2634; border:1px solid #594357; border-radius:12px; font-size:12px; }
  .feed-form input { width:120px; }
  .errors { padding:0; margin:0; list-style:none; } .errors li + li { margin-top:4px; }
  .footer { margin-top:14px; color:#7898aa; font-size:11px; line-height:1.6; }
  .mcp-companion { margin-top:18px; padding:14px; border:1px solid #3d5261; border-radius:14px; background:#102531; }
  .mcp-companion summary { cursor:pointer; color:#cee7f3; font-size:13px; font-weight:650; }
  .mcp-companion .muted { margin:10px 0; }.mcp-list { max-height:400px; overflow:auto; }
  .mcp-row { display:grid; grid-template-columns:117px 63px minmax(140px,1fr) 100px 100px 70px;
    gap:10px; padding:10px 8px; border-top:1px solid #294452; font-size:12px; }
  .mcp-columns { color:#8faebe; font-size:11px; }
  .mcp-row > span { min-width:0; overflow-wrap:anywhere; }
  .mcp-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:12px; }
  .mcp-tools { margin:12px 0; }.mcp-status { color:#96b9cb; font-size:12px; line-height:1.7; }
  @media(max-width:860px) { .mcp-row { grid-template-columns:1fr 1fr; gap:10px; padding:12px 0; }
    .mcp-columns { display:none; }.mcp-mode { grid-column:1 / -1; }.mcp-companion { padding:14px 10px; }
    .mcp-head { flex-direction:column; align-items:flex-start; } }
  dialog { width:min(520px,calc(100vw - 32px)); max-height:calc(100dvh - 48px); overflow:auto;
    margin:auto; padding:24px; color:#e7f4fa; background:linear-gradient(145deg,#153244,#0d1f2c);
    border:1px solid #3a5c72; border-top:3px solid #4bb1ff; border-radius:23px;
    box-shadow:0 28px 100px #0009; }
  dialog::backdrop { background:#030c16bb; backdrop-filter:blur(6px); }
  .dialog-head { display:flex; align-items:center; gap:12px; margin-bottom:18px; }
  .dialog-symbol { display:grid; place-items:center; flex:none; width:44px; height:44px;
    border-radius:14px; color:#66c0ff; background:#193f55; }
  .dialog-symbol ha-icon { width:24px; height:24px; }.dialog-head h2 { margin:5px 0 0; font-size:21px; }
  .dialog-close { margin-left:auto; padding:0; min-width:38px; min-height:38px; font-size:21px; }
  .dialog-text { color:#b2cbd9; font-size:13px; line-height:1.8; overflow-wrap:anywhere; }
  .dialog-actions { display:flex; gap:8px; justify-content:flex-end; padding-top:18px; }
  .dialog-actions button { min-height:42px; min-width:90px; }
  dialog input { height:46px; font-size:15px; padding:10px 12px; border-radius:11px; }
  dialog .template-form,dialog .feed-form { border:0; padding:0; margin:0; background:none;
    display:block; }.template-form .dialog-actions { margin-top:0; }
  dialog label > span { display:block; margin-bottom:8px; color:#9bb7c9; font-size:12px; }
  dialog .template-form label { display:block; max-width:none; }
  dialog .muted { margin-top:12px; }.feed-form input { width:100%; }
  .dialog-callout { border:1px solid #315268; border-radius:12px; background:#102634;
    padding:12px 14px; color:#a9c8d9; font-size:12px; line-height:1.7; margin-top:14px; }
  .dialog-callout b { color:#def5ff; }.dialog-actions .danger { color:#ffced5;
    background:#693346; border-color:#a4576e; }
  @media(max-width:700px) { dialog { padding:20px 16px; }.dialog-head h2 { font-size:19px; } }
  @media(max-width:860px) { ha-card { padding:18px 14px; }.columns { display:none; }
    .row { grid-template-columns:1fr 1fr; gap:10px 12px; }.cell-label { display:block; color:#8faebe; font-size:10px; margin-bottom:5px; }
    .row .mode-cell { grid-column:1 / -1; }.row-actions { grid-column:2; }.end { height:37px; display:flex; align-items:center; }
    .row.current::before { content:'현재 구간'; color:#63beff; font-size:10px; grid-column:1 / -1; }
    .head { align-items:flex-start; } h1 { font-size:23px; }.status { padding:8px 10px; font-size:11px; }
    .revision { flex-basis:100%; margin:4px 0 0; }.tools .file { margin-left:0; }.contract { gap:8px; } }
  @media(max-width:400px) { .head { flex-direction:column; gap:10px; } ha-card { padding:16px 10px; } }
  @media(max-width:700px) { :host { padding:12px 8px 24px; } }
`;

class AquaScheduleEditor extends HTMLElement {
  constructor() {
    super(); this.attachShadow({ mode: "open" });
    this._rows = []; this._mcpRows = []; this._dirty = false; this._pending = null; this._notice = null;
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this.shadowRoot.addEventListener("change", (e) => this._change(e));
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.matches?.("[data-template-name],#feeding-time")) {
        e.preventDefault(); this.shadowRoot.querySelector("[data-template-submit],[data-action=insert-feed]")?.click();
      }
    });
  }
  setConfig(config) {
    if (!config.entity) throw Error("시간표 편집기 상태 엔티티를 지정해주세요.");
    this._config = { ...config }; this._render();
  }
  getCardSize() { return 12; }
  connectedCallback() { this._timer = setInterval(() => this._renderStatus(), 1000); }
  disconnectedCallback() {
    clearInterval(this._timer);
    this._mcpUnsubscribe?.(); this._mcpUnsubscribe=null;
    if (this._mcpPending && !this._mcpPending.sent && this._mcpPending.operation !== "timer")
      this._mcpPending=null;
    const resolve=this._confirmResolve;this._confirmResolve=null;this._confirmation=null;
    if (resolve) resolve(false);
  }
  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config?.entity];
    this._data = state?.attributes?.schema_version === 1 ? state.attributes : null;
    this._available = !!state && !["unknown", "unavailable"].includes(state.state);
    const result = this._data?.last_result;
    let render = false;
    if (this._pending && result?.request_id === this._pending.id) {
      const operation = this._pending.operation;
      this._pending = null;
      if (result.outcome === "error") {
        const errors = { draft_changed: "다른 화면에서 저장본이 변경됐어요. 저장본을 다시 불러온 뒤 수정해주세요.",
          active_changed: "실행 중인 표가 변경됐어요. 현재 상태를 확인하고 다시 실행해주세요.",
          invalid_schedule: "시간표 형식이 맞지 않아요. 시각·Flow·Feeding 종료 행을 확인해주세요.",
          invalid_mcp_schedule: "MCP 보관 시간표 형식이 맞지 않아요. 시각·Mode·Flow·Frequency를 확인해주세요.",
          already_attempted: "이 요청은 이미 시도된 기록이 있어요. 재송신하지 않고 멈췄습니다.",
          operation_failed: "실행기에서 확인에 실패했어요. 일정 상태와 장치 보고를 확인해주세요." };
        Object.assign(errors, { templates_changed: "다른 화면에서 템플릿 목록이 바뀌었어요. 최신 목록을 확인하고 다시 선택해주세요.",
          template_selection_changed: "다른 화면에서 편집 템플릿을 바꿨어요. 저장본을 다시 불러온 뒤 수정해주세요.",
          template_missing: "이 템플릿은 삭제됐어요. 다른 템플릿을 선택해주세요.",
          template_name_exists: "같은 이름의 템플릿이 있어요. 다른 이름을 입력해주세요.",
          invalid_template_name: "템플릿 이름은 1~48자로 입력해주세요.",
          template_active: "현재 운전 중인 템플릿은 삭제할 수 없어요. 다른 표를 활성화한 뒤 삭제해주세요.",
          last_template: "마지막 템플릿은 삭제할 수 없어요.", templates_full: "템플릿은 최대 32개까지 저장할 수 있어요." });
        this._notice = { kind: "error", text: errors[result.code] || `요청 거부: ${result.code}` };
      } else {
        if (["save", "template_select", "template_create", "template_delete"].includes(operation)) {
          this._dirty = false; this._draftKey = null;
        }
        if (operation.startsWith("template_")) this._templateForm = null;
        const text = { save: "시간표를 저장했어요. 현재 운전은 변경하지 않았습니다.",
          activate: "GMP 활성화가 확인됐어요. 현재 구간부터 실행합니다. MCP 장치 시간표는 변경하지 않았습니다.",
          pause: "일정을 일시정지했어요. 장비는 마지막 수동 상태를 유지합니다.",
          template_select: "템플릿을 불러왔어요. 현재 운전은 변경하지 않았습니다.",
          template_create: "새 템플릿을 저장했어요. 활성화 전까지 기존 표로 운전합니다.",
          template_rename: "템플릿 이름을 변경했어요. 현재 운전은 그대로예요.",
          template_delete: "템플릿을 목록에서 삭제했어요. 현재 운전은 그대로예요." };
        this._notice = { kind: "success", text: text[operation] };
      }
      render = true;
    }
    const draftKey = `${this._data?.selected_template_id || ""}:${this._data?.draft_sha256}:${this._data?.draft_mcp_sha256 || ""}`;
    if (this._data && !this._dirty && !this._pending && !this._mcpPending && this._draftKey !== draftKey) {
      this._rows = scheduleClone(this._data.draft_rows); this._draft = this._data.draft_sha256;
      this._mcpRows = scheduleClone(this._data.draft_mcp_rows || []);
      this._mcpDraft = this._data.draft_mcp_sha256;
      this._templateId = this._data.selected_template_id; this._draftKey = draftKey; render = true;
    }
    if (this._library !== this._data?.templates_sha256) {
      this._library = this._data?.templates_sha256; render = true;
    }
    if (render || !this.shadowRoot.querySelector(".status")) this._render();
    else this._renderStatus();
  }
  _online() {
    return this._available && this._data && Date.now() - Date.parse(this._data.sampled_at) < 60000;
  }
  _errors() { return [...scheduleErrors(this._rows, this._data?.flow_min || 30, this._data?.flow_max || 45), ...scheduleMcpErrors(this._mcpRows.map(scheduleMcpCleanRow))]; }
  _mcpState() { return this._hass?.states?.[this._config?.mcp_schedule_entity]; }
  _mcpBounds() { return [this._config?.mcp_flow_min ?? 30,this._config?.mcp_flow_max ?? 50]; }
  _mcpPlan() { return scheduleMcpPlan(this._mcpRows.map(scheduleMcpCleanRow),...this._mcpBounds()); }
  _now() {
    return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date());
  }
  _renderStatus() {
    const maintenanceControl = this.shadowRoot.querySelector("aqua-maintenance-control");
    if (maintenanceControl) { maintenanceControl.entity = this._config?.entity; maintenanceControl.hass = this._hass; }
    if (!this.shadowRoot.querySelector(".status")) return;
    const online = this._online(), d = this._data;
    const status = this.shadowRoot.querySelector(".status");
    const busy = this._pending || this._mcpPending || d?.busy;
    const query = d?.communication?.query;
    status.textContent = !online ? "● 실행기 연결 확인 중" : query ? query.state === "communication_error"
      ? "● 장비 통신 오류 · 재시도 중" : "● 상태 조회 재시도 중" : busy ? "● 처리 중"
      : d.maintenance?.enabled ? "● 청소·환수 · 시간표 보류"
      : d.controller_status === "active" && d.current_row_verified ? "● 스케줄러 정상"
        : d.controller_status === "active" ? "● 구간 확인 중" : "● 일정 일시정지";
    status.className = `status${!online ? " offline" : query || busy || d.controller_status !== "active" ? " warn" : ""}`;
    const errors = this._errors();
    const selectionChanged = !!this._templateId && this._templateId !== d?.selected_template_id;
    const sameTemplate = !d?.templates || (d.active_template_id === this._templateId);
    const revisionActive = !!this._draft && (d?.active_template_revision || d?.active_sha256) === this._draft;
    for (const button of this.shadowRoot.querySelectorAll("[data-action=save],[data-action=activate],[data-action=pause]")) {
      const action = button.dataset.action;
      button.disabled = !online || (!!busy && !(action === "pause" && query && this._pending?.operation !== "pause")) || (action === "save" && (selectionChanged || !this._dirty || !!errors.length))
        || (d?.maintenance?.enabled && action !== "save")
        || (action === "activate" && (selectionChanged || this._dirty || !this._draft || !!errors.length
          || (d.controller_status === "active" && sameTemplate && revisionActive)))
        || (action === "pause" && d.controller_status !== "active" && !query);
    }
    this.shadowRoot.querySelector(".revision").textContent = selectionChanged ? "다른 화면에서 템플릿 변경 · 저장본을 다시 불러오세요"
      : this._dirty ? "저장하지 않은 변경사항"
        : sameTemplate && revisionActive ? d?.controller_status === "active"
          ? "선택 템플릿으로 운전 중" : "선택 템플릿과 마지막 실행본 동일"
          : "선택 템플릿 미적용 · 기존 운전 유지";
    const activeName = this.shadowRoot.querySelector(".active-template");
    if (activeName) activeName.textContent = `${d?.controller_status === "active" ? "운전 표" : "마지막 실행 표"} · ${d?.active_template_name || "기본 시간표"}`;
    for (const button of this.shadowRoot.querySelectorAll("[data-template-action]")) {
      const action = button.dataset.templateAction;
      button.disabled = !online || !!busy || !d?.templates || (action === "delete" && (
        this._templateId === d.active_template_id || d.templates.length <= 1))
        || (["new", "duplicate"].includes(action) && d.templates.length >= (d.max_templates || 32))
        || (action === "duplicate" && !!errors.length);
    }
    const selector = this.shadowRoot.querySelector("[data-template-selector]");
    if (selector) selector.disabled = !online || !!busy || !d?.templates;
    const submit = this.shadowRoot.querySelector("[data-template-submit]");
    if (submit) submit.disabled = !online || !!busy || !this._templateForm?.name.trim()
      || (this._templateForm.kind === "duplicate" && !!errors.length);
    const next = d?.next_boundary_at ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(d.next_boundary_at)) : "—";
    this.shadowRoot.querySelector(".next").textContent = d?.maintenance?.enabled
      ? "청소·환수 종료 시 현재 시각의 시간표로 복귀" : `Asia/Seoul · 다음 전환 ${next}`;
    const errorArea = this.shadowRoot.querySelector(".validation");
    errorArea.innerHTML = errors.length ? `<div class="notice error"><ul class="errors">${errors.slice(0, 8).map((e) => `<li>${scheduleEscape(e)}</li>`).join("")}</ul></div>` : "";
    const notice = this.shadowRoot.querySelector(".feedback");
    const uncertain = this._pending && Date.now() - this._pending.at > 120000;
    notice.innerHTML = query ? `<div class="notice">장비 상태를 다시 조회하고 있어요. 변경 명령은 재송신하지 않습니다.</div>`
      : uncertain ? `<div class="notice error">결과가 아직 확인되지 않았어요. 요청을 재송신하지 않고 기다립니다. 실행기 상태를 확인해주세요.</div>`
      : this._notice ? `<div class="notice ${this._notice.kind}">${scheduleEscape(this._notice.text)}</div>`
        : d?.last_error ? `<div class="notice error">실행기가 일정을 중지했어요 (${scheduleEscape(d.last_error.error_type)}). 자동으로 재송신하지 않습니다.</div>` : "";
    const current = this._rows.findLastIndex((r) => r.time <= this._now());
    this.shadowRoot.querySelectorAll(".row").forEach((row, i) => row.classList.toggle("current", i === current));
    this._renderMcpStatus();
  }
  _renderMcpStatus() {
    const state=this._mcpState(), p=this._mcpPending, report=state?.attributes;
    const fresh=!!state && !["unavailable","unknown"].includes(state.state)
      && Date.now()-Date.parse(report.observed_at)<60000;
    const matched=p?.operation === "apply" && p.accepted && fresh
      && Date.parse(report.observed_at)>=p.at && scheduleMcpMatches(p.plan,report.observed);
    if (matched) {
      this._mcpPending=null; this._mcpUnsubscribe?.(); this._mcpUnsubscribe=null;
      this._mcpNotice="바형 적용 확인 완료 · 장비 시간표 재조회 일치 · GMP 운전 유지";
    }
    if (p?.operation === "timer" && this._hass?.states?.[this._config?.mcp_timer_entity]?.state === p.goal) {
      this._mcpPending=null;this._mcpNotice=p.goal === "on" ? "바형 장치 시간표 운전 켜짐 확인" : "바형 장치 시간표 운전 꺼짐 확인";
    }
    let valid=false; try { this._mcpPlan(); valid=true; } catch (_) {}
    const busy=!!this._mcpPending || !!this._pending || !!this._data?.busy;
    const selectionChanged=this._templateId !== this._data?.selected_template_id;
    let alreadyApplied=false;try{alreadyApplied=fresh && scheduleMcpMatches(this._mcpPlan(),report.observed);}catch(_){}
    for (const b of this.shadowRoot.querySelectorAll("[data-mcp-action]")) {
      const action=b.dataset.mcpAction;
      b.disabled=busy || (this._data?.maintenance?.enabled && ["apply","timer"].includes(action)) || (action === "import" && !fresh)
        || (["preview","apply"].includes(action) && (!fresh || !valid || !report?.schedule_request_topic))
        || (action === "apply" && (!this._online() || this._dirty || selectionChanged || alreadyApplied))
        || (action === "timer" && !this._hass?.states?.[this._config?.mcp_timer_entity]?.state.match(/^(on|off)$/));
      if (action === "timer") b.textContent=this._hass?.states?.[this._config?.mcp_timer_entity]?.state === "on" ? "시간표 운전 끄기" : "시간표 운전 켜기";
    }
    const node=this.shadowRoot.querySelector(".mcp-status");
    if (node) node.textContent=this._mcpPending ? this._mcpPending.accepted
      ? "장비 적용 응답 확인 · 최신 시간표 보고 대조 중"
        : Date.now()-this._mcpPending.at>30000 ? "바형 응답 지연 · 연결·장치 보고 확인 필요 · 재송신하지 않습니다"
          : "바형 요청 처리 중 · 재송신하지 않습니다"
      : this._mcpNotice || (!fresh ? "바형 연결·최신 상태 확인 중" : (()=>{
        let same=false;try{same=scheduleMcpMatches(this._mcpPlan(),report.observed);}catch(_){}
        return `${report.observed?.enabled ? "장치 시간표 운전 중" : "장치 시간표 꺼짐"} · ${same ? "편집 표와 장치 보고 일치" : "선택 표 미적용"}`;
      })());
  }
  _input(event) {
    const input = event.target;
    if (input.id === "feeding-time") { this._feedingTime=input.value; return; }
    if (input.dataset.templateName !== undefined && this._templateForm) {
      this._templateForm.name = input.value; this._renderStatus(); return;
    }
    if (input.dataset.mcpIndex !== undefined && input.dataset.field && !this._pending && !this._mcpPending) {
      const r=this._mcpRows[Number(input.dataset.mcpIndex)], f=input.dataset.field;
      r[f]=["time","mode"].includes(f) ? input.value : input.value === "" ? null : Number(input.value);
      if (f === "mode" && r.mode !== "feed") { r.flow ??=40; if(scheduleMcpFrequency(r.mode)) r.frequency ??=80; }
      this._dirty=true; this._notice=null; this._mcpNotice=null; this._renderStatus(); return;
    }
    if (input.dataset.index === undefined || !input.dataset.field || this._pending || this._mcpPending) return;
    const row = this._rows[Number(input.dataset.index)];
    row[input.dataset.field] = ["time", "mode"].includes(input.dataset.field) ? input.value
      : input.value === "" ? null : Number(input.value);
    if (input.dataset.field === "mode" && row.mode !== "feed") {
      row.upper_flow ??= 40; row.lower_flow ??= 40;
      if (scheduleWithFrequency(row.mode)) row.frequency ??= 80;
      if (row.mode === "custom") row.custom_frequency ??= 5;
    }
    this._dirty = true; this._notice = null; this._renderStatus();
  }
  async _change(event) {
    if (this._pending || this._mcpPending) return;
    if (event.target.dataset.templateSelector !== undefined) {
      const id = event.target.value;
      const library = this._data?.templates_sha256;
      if (id === this._templateId) return;
      if (this._dirty && !await this._confirm("저장하지 않은 변경사항을 버리고 다른 템플릿을 불러올까요?")) {
        this._render(); return;
      }
      await this._sendTemplate("template_select", { template_id: id, expected_library: library }); return;
    }
    if (event.target.type === "file") {
      try {
        const file = event.target.files[0]; if (!file) return;
        if (file.size > 32768) throw Error("시간표 파일이 너무 커요.");
        const document = JSON.parse(await file.text());
        const rows = Array.isArray(document) ? document : document.rows;
        const mcpRows = Array.isArray(document) ? [] : document.mcp_rows ?? [];
        const errors = [...scheduleErrors(rows), ...scheduleMcpErrors(mcpRows)];
        if (errors.length) throw Error(errors[0]);
        this._rows = rows.map(scheduleCleanRow); this._mcpRows = scheduleClone(mcpRows);
        this._dirty = true; this._notice = null; this._render();
      } catch (error) { this._notice = { kind: "error", text: error.message }; this._renderStatus(); }
      return;
    }
    this._input(event);
    if (["time", "mode"].includes(event.target.dataset.field)) {
      const rows=event.target.dataset.mcpIndex !== undefined ? this._mcpRows : this._rows;
      rows.sort((a, b) => scheduleMinute(a.time) - scheduleMinute(b.time)); this._render();
    }
  }
  async _click(event) {
    const dialog=this.shadowRoot.querySelector("dialog"),bounds=dialog?.getBoundingClientRect();
    const backdrop=event.target===dialog && bounds && (event.clientX<bounds.left || event.clientX>bounds.right || event.clientY<bounds.top || event.clientY>bounds.bottom);
    if (event.target.closest?.("[data-dialog-close]") || backdrop) {
      this._closeDialog(); return;
    }
    const answer=event.target.closest?.("[data-dialog-answer]");
    if (answer) { this._finishConfirm(answer.dataset.dialogAnswer === "yes"); return; }
    const templateButton = event.target.closest("[data-template-action],[data-template-submit]");
    if (templateButton) {
      if (templateButton.disabled || this._pending || this._mcpPending) return;
      this._templateClick(templateButton); return;
    }
    const mcpButton=event.target.closest("[data-mcp-action]");
    if (mcpButton) { if(!mcpButton.disabled) await this._mcpClick(mcpButton); return; }
    const button = event.target.closest("[data-action]"); if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (["save", "activate", "pause"].includes(action)) { void this._send(action); return; }
    if (this._pending || this._mcpPending) return;
    if (action === "reload") {
      if (this._dirty && !await this._confirm("저장하지 않은 변경사항을 버리고 저장본을 불러올까요?")) return;
      this._rows = scheduleClone(this._data?.draft_rows || []); this._draft = this._data?.draft_sha256;
      this._mcpRows = scheduleClone(this._data?.draft_mcp_rows || []);
      this._mcpDraft = this._data?.draft_mcp_sha256;
      this._templateId = this._data?.selected_template_id;
      this._draftKey = `${this._templateId || ""}:${this._draft}:${this._data?.draft_mcp_sha256 || ""}`;
      this._dirty = false; this._notice = null;
    } else if (action === "active") {
      if (this._dirty && !await this._confirm("변경사항을 버리고 실행 중인 표를 편집할까요?")) return;
      this._rows = scheduleClone(this._data?.active_rows || []); this._dirty = true; this._notice = null;
    } else if (action === "add" || action === "copy") {
      if (this._rows.length >= 96) return;
      const source = action === "copy" ? this._rows[Number(button.dataset.index)] : this._rows.at(-1);
      const base = source && source.mode !== "feed" ? source : { mode: "sine", upper_flow: 40, lower_flow: 40, frequency: 80 };
      let minute = source ? (scheduleMinute(source.time) + (action === "copy" ? 1 : 60)) % 1440 : 0;
      if (!Number.isFinite(minute)) minute = 0;
      while (this._rows.some((r) => scheduleMinute(r.time) === minute)) minute = (minute + 1) % 1440;
      this._rows.push({ ...scheduleCleanRow(base), time: scheduleTime(minute) });
      this._rows.sort((a, b) => scheduleMinute(a.time) - scheduleMinute(b.time)); this._dirty = true;
    } else if (action === "delete") {
      this._rows.splice(Number(button.dataset.index), 1); this._dirty = true;
    } else if (action === "feeding") {
      this._feedingTarget="gmp";
      this._feedingForm = !this._feedingForm;
      this._feedingTime = this._feedingTime || "";
    } else if (action === "insert-feed") {
      try {
        const key=this._feedingTarget === "mcp" ? "_mcpRows" : "_rows";
        this[key] = scheduleInsertFeeding(this[key], this.shadowRoot.querySelector("#feeding-time").value);
        this._dirty = true; this._feedingForm = false; this._notice = null;
      } catch (error) { this._notice = { kind: "error", text: error.message }; }
    } else if (action === "export") {
      const blob = new Blob([JSON.stringify(this._exportDocument(), null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = "aquarium-gmp-schedule.json"; link.click(); URL.revokeObjectURL(url); return;
    }
    this._render();
  }
  async _send(operation) {
    if (this._data?.maintenance?.enabled && ["activate","pause"].includes(operation)) return;
    const readPause = operation === "pause" && this._data?.communication?.query
      && this._pending?.operation !== "pause";
    if (!this._online() || this._mcpPending || ((this._pending || this._data.busy) && !readPause)) return;
    if (["save", "activate"].includes(operation) && this._errors().length) return;
    if (operation === "activate" && (this._dirty || !this._draft)) return;
    if (["save", "activate"].includes(operation) && this._templateId
      && this._templateId !== this._data.selected_template_id) return;
    const id = crypto.randomUUID().replaceAll("-", "");
    const request = { schema_version: 1, request_id: id, issued_at: new Date().toISOString(), operation,
      ...(operation === "save" ? { rows: this._rows.map(scheduleCleanRow), expected_draft: this._draft,
        ...(this._data.mcp_schedule_scope === "stored_reference_only" ? { mcp_rows: this._mcpRows.map(scheduleMcpCleanRow), expected_library: this._data.templates_sha256,
          ...(this._mcpDraft ? { expected_mcp: this._mcpDraft } : {}) } : {}) }
        : { expected_active: this._data.active_sha256,
          ...(operation === "activate" ? { schedule_sha256: this._draft } : {}) }),
      ...(["save", "activate"].includes(operation) && this._templateId ? { template_id: this._templateId } : {}) };
    await this._publish(request);
  }
  async _publish(request) {
    const { request_id: id, operation } = request;
    this._pending = { id, operation, at: Date.now() }; this._notice = null; this._render();
    try {
      await this._hass.callService("mqtt", "publish", { topic: this._data.command_topic,
        payload: JSON.stringify(request), qos: 0, retain: false });
    } catch (_) {
      // Delivery may be ambiguous. Keep the request pending; never auto-resend it.
      this._notice = { kind: "error", text: "요청 전달 결과를 확인할 수 없어요. 재송신하지 않고 실행기 결과를 기다립니다." };
      this._renderStatus();
    }
  }
  async _templateClick(button) {
    const action = button.dataset.templateAction;
    const selected = this._data?.templates?.find((t) => t.id === this._templateId);
    if (button.dataset.templateSubmit !== undefined) {
      const form = this._templateForm; if (!form || !form.name.trim()) return;
      if (form.kind === "rename") {
        void this._sendTemplate("template_rename", { template_id: form.templateId, name: form.name.trim(), expected_library: form.library });
      } else {
        const flow = Math.min(this._data?.flow_max || 45, Math.max(this._data?.flow_min || 30, 40));
        const rows = form.kind === "duplicate" ? form.rows
          : [{ time: "00:00", mode: "constant", upper_flow: flow, lower_flow: flow }];
        void this._sendTemplate("template_create", { name: form.name.trim(), rows, expected_library: form.library,
          ...(this._data.mcp_schedule_scope === "stored_reference_only" ? { mcp_rows: form.kind === "duplicate" ? form.mcpRows : [] } : {}) });
      }
      return;
    }
    if (action === "cancel") this._templateForm = null;
    else if (action === "delete") {
      const templateId = this._templateId, library = this._data?.templates_sha256;
      if (await this._confirm(`‘${selected?.name || "선택한 템플릿"}’을 목록에서 삭제할까요?${this._dirty ? " 저장하지 않은 편집도 사라져요." : ""} 과거 실행 기록과 현재 운전은 유지돼요.`, "템플릿 삭제", "삭제", true))
        void this._sendTemplate("template_delete", { template_id: templateId, expected_library: library });
      return;
    } else {
      if (action === "new" && this._dirty && !await this._confirm("변경사항을 버리고 새 템플릿을 만들까요?")) return;
      let name = action === "rename" ? selected?.name || "" : action === "duplicate"
        ? `${selected?.name || "시간표"} 복사` : "새 시간표";
      if (action !== "rename") {
        const base = name.slice(0, 42); name = base; let i = 2;
        while (this._data?.templates?.some((t) => t.name === name)) name = `${base} ${i++}`;
      }
      this._templateForm = { kind: action, name, templateId: this._templateId,
        library: this._data?.templates_sha256, rows: this._rows.map(scheduleCleanRow), mcpRows: this._mcpRows.map(scheduleMcpCleanRow) };
    }
    this._render();
  }
  async _sendTemplate(operation, fields) {
    if (!this._online() || this._pending || this._mcpPending || this._data.busy || !this._data.templates_sha256) return;
    if (operation === "template_create" && scheduleErrors(fields.rows, this._data.flow_min, this._data.flow_max).length) return;
    await this._publish({ schema_version: 1, request_id: crypto.randomUUID().replaceAll("-", ""),
      issued_at: new Date().toISOString(), operation, expected_library: this._data.templates_sha256, ...fields });
  }
  async _mcpClick(button) {
    if (this._data?.maintenance?.enabled && ["timer","apply"].includes(button.dataset.mcpAction)) return;
    if (this._pending || this._mcpPending || this._data?.busy) return;
    const action=button.dataset.mcpAction;
    try {
      if (["preview","apply"].includes(action)) { await this._mcpRequest(action); return; }
      if (action === "timer") {
        const entity=this._config.mcp_timer_entity, state=this._hass.states[entity]?.state;
        if (!["on","off"].includes(state)) return;
        this._mcpPending={operation:"timer",goal:state === "on" ? "off" : "on",at:Date.now()};
        this._render();
        await this._hass.callService("switch",state === "on" ? "turn_off" : "turn_on",{entity_id:entity});
        return;
      }
      if (action === "import") {
        const rows=scheduleMcpObservedRows(this._mcpState()?.attributes?.observed);
        if (this._dirty && !await this._confirm("편집 중인 바형 행을 장치가 보고한 시간표로 바꿀까요? GMP 편집 행은 유지돼요.")) return;
        this._mcpRows=rows; this._dirty=true;
      } else if (action === "feeding") {
        this._feedingTarget="mcp";this._feedingForm=true;this._feedingTime="";
      } else if (action === "add" || action === "copy") {
        if (this._mcpRows.length>=48) throw Error("바형은 최대 48구간까지 적용할 수 있어요.");
        const source=action === "copy" ? this._mcpRows[Number(button.dataset.index)] : this._mcpRows.at(-1);
        const base=source && source.mode !== "feed" ? source : {mode:"sine",flow:40,frequency:80};
        let minute=source ? (scheduleMinute(source.time)+(action === "copy" ? 1 : 60))%1440 : 0;
        while(this._mcpRows.some(r=>scheduleMinute(r.time) === minute)) minute=(minute+1)%1440;
        this._mcpRows.push(scheduleMcpCleanRow({...base,time:scheduleTime(minute)}));
        this._mcpRows.sort((a,b)=>scheduleMinute(a.time)-scheduleMinute(b.time));this._dirty=true;
      } else if (action === "delete") { this._mcpRows.splice(Number(button.dataset.index),1);this._dirty=true; }
      this._mcpNotice=null;this._render();
    } catch (error) {
      this._mcpNotice=this._mcpPending ? "바형 요청 결과 확인 중 · 변경 명령을 재송신하지 않습니다" : error.message;
      this._render();
    }
  }
  async _mcpRequest(operation) {
    if (this._data?.maintenance?.enabled && operation === "apply") return;
    if (!["preview","apply"].includes(operation)) return;
    if (this._pending || this._mcpPending || this._data?.busy) return;
    if (operation === "apply" && (this._dirty || !this._online() || this._templateId !== this._data?.selected_template_id)) return;
    const state=this._mcpState(), topic=state?.attributes?.schedule_request_topic;
    const route=topic?.match(/^(.*)\/local\/devices\/([^/]+)\/functions\/schedule\/request$/);
    if (!route || !this._hass.connection?.subscribeMessage || ["unavailable","unknown"].includes(state.state)
      || !Number.isFinite(Date.parse(state.attributes.observed_at))
      || Date.now()-Date.parse(state.attributes.observed_at)>=60000) throw Error("바형 API·최신 상태·MQTT 구독 권한을 확인해주세요.");
    const plan=this._mcpPlan(), id=crypto.randomUUID().replaceAll("-", "");
    if (operation === "apply" && scheduleMcpMatches(plan,state.attributes.observed)) {
      this._mcpNotice="바형 장치 표와 이미 일치해요. 중복 적용하지 않았습니다.";this._render();return;
    }
    const pending={id,operation,plan,at:Date.now(),member:route[2],sent:false};
    this._mcpPending=pending;this._mcpNotice=null;this._render();
    try {
      pending.digest=await scheduleMcpDigest(plan);
      const unsub=await this._hass.connection.subscribeMessage((message)=>this._mcpResult(message),{
        type:"mqtt/subscribe",topic:`${route[1]}/local/requests/${id}/result`,qos:0,
      });
      if (this.isConnected === false || this._mcpPending !== pending) {
        unsub();if(this._mcpPending === pending) this._mcpPending=null;return;
      }
      this._mcpUnsubscribe=unsub;
      pending.sent=true;
      await this._hass.callService("mqtt","publish",{topic,payload:JSON.stringify({operation,request_id:id,schedule:plan}),qos:0,retain:false});
    } catch (error) {
      if (!pending.sent) {
        this._mcpPending=null;this._mcpUnsubscribe?.();this._mcpUnsubscribe=null;
        this._mcpNotice="바형 요청은 송신하지 않았어요. 결과 구독·연결 권한을 확인해주세요.";
      } else this._mcpNotice="바형 요청 전달 결과 확인 중 · 같은 명령은 재송신하지 않습니다";
      this._render();
    }
  }
  _mcpResult(message) {
    const p=this._mcpPending;if(!p || message.retain) return;
    let result;try{result=JSON.parse(message.payload);}catch(_){return;}
    if(result.request_id !== p.id) return;
    if(p.operation === "preview") {
      const valid=result.operation === "preview" && result.hardware_write === false
        && result.family === "bar" && result.logical_member === p.member && result.image_sha256 === p.digest;
      this._mcpNotice=valid ? `바형 ${p.plan.slots.length}구간 · 384바이트 미리보기 일치 · 장비 송신 0회`
        : "바형 미리보기 응답이 편집 표와 다릅니다. 적용하지 않았어요.";
      this._mcpPending=null;this._mcpUnsubscribe?.();this._mcpUnsubscribe=null;
    } else if(result.accepted === true && result.phase === "verified") p.accepted=true;
    else {
      this._mcpNotice=`바형 적용 미완료 · ${result.phase || "결과 오류"} · 장치 보고를 확인해주세요`;
      this._mcpPending=null;this._mcpUnsubscribe?.();this._mcpUnsubscribe=null;
    }
    this._render();
  }
  _exportDocument() {
    return { schema_version: 1, timezone: "Asia/Seoul", rows: this._rows.map(scheduleCleanRow), mcp_rows: this._mcpRows.map(scheduleMcpCleanRow) };
  }
  _confirm(text, title="편집 내용 확인", accept="계속", danger=false) {
    if (this._confirmResolve) return Promise.resolve(false);
    return new Promise((resolve)=>{
      this._confirmation={text,title,accept,danger}; this._confirmResolve=resolve; this._render();
    });
  }
  _finishConfirm(value) {
    const resolve=this._confirmResolve;this._confirmResolve=null;this._confirmation=null;
    this._render(); if (resolve) resolve(value);
  }
  _closeDialog() {
    if (this._confirmResolve) { this._finishConfirm(false); return; }
    this._templateForm=null;this._feedingForm=false;this._render();
    this.shadowRoot.querySelector("[data-template-selector]")?.focus();
  }
  _dialogMarkup() {
    const form=this._templateForm, confirm=this._confirmation;
    if (!form && !this._feedingForm && !confirm) return "";
    const title=confirm?.title || (form ? ({rename:"템플릿 이름 변경",duplicate:"시간표 복제",new:"새 시간표 템플릿"})[form.kind] : `${this._feedingTarget === "mcp" ? "바형 " : ""}Feeding 구간 추가`);
    const body=confirm ? `<p class="dialog-text">${scheduleEscape(confirm.text)}</p><div class="dialog-actions"><button data-dialog-answer="no">취소</button><button class="${confirm.danger ? "danger" : "primary"}" data-dialog-answer="yes">${scheduleEscape(confirm.accept)}</button></div>`
      : form ? `<div class="template-form"><label><span>템플릿 이름</span><input data-template-name maxlength="48" value="${scheduleEscape(form.name)}" aria-label="템플릿 이름" ${this._pending ? "disabled" : ""} autofocus></label>
        <p class="muted">${form.kind === "new" ? "00:00 Constant 한 구간으로 시작해요. 만든 후 시각·모드·Flow를 편집하세요." : form.kind === "rename" ? "선택한 템플릿의 이름만 변경해요. 시간표 내용은 그대로 유지됩니다." : "현재 편집 중인 시간표를 새 이름으로 보관해요."}</p>
        <div class="dialog-callout">템플릿 생성·이름 변경은 <b>장비 송신 0회</b>예요. 현재 운전은 그대로 유지됩니다.</div>
        <div class="dialog-actions"><button data-dialog-close>취소</button><button class="primary" data-template-submit>${form.kind === "rename" ? "이름 저장" : "템플릿 만들기"}</button></div></div>`
      : `<div class="feed-form"><label><span>명령 시작 시각</span><input id="feeding-time" type="time" step="60" value="${scheduleEscape(this._feedingTime)}" aria-label="Feeding 시작 시각" autofocus></label>
        <div class="dialog-callout"><b>정지 6분 → 이전 운전 설정으로 복귀</b><br>선택한 시각의 Feeding 행과 6분 뒤 운전 행을 함께 추가해요. 저장·활성화 전에는 장비를 변경하지 않습니다.</div>
        <div class="dialog-actions"><button data-dialog-close>취소</button><button class="primary" data-action="insert-feed">6분 구간 추가</button></div></div>`;
    return `<dialog aria-labelledby="schedule-dialog-title"><div class="dialog-head"><div class="dialog-symbol"><ha-icon icon="${confirm?.danger ? "mdi:delete-outline" : this._feedingForm ? "mdi:fish" : "mdi:calendar-edit"}"></ha-icon></div><div><div class="eyebrow">AQUARIUM · SCHEDULE</div><h2 id="schedule-dialog-title">${scheduleEscape(title)}</h2></div><button class="dialog-close" data-dialog-close aria-label="닫기">×</button></div>${body}${this._notice?.kind === "error" ? `<div class="notice error" role="alert">${scheduleEscape(this._notice.text)}</div>` : ""}</dialog>`;
  }
  _showDialog() {
    const dialog=this.shadowRoot.querySelector("dialog");if (this.isConnected===false || !dialog || typeof dialog.showModal!=="function") return;
    dialog.addEventListener("cancel",(event)=>{event.preventDefault();this._closeDialog();});
    if (!dialog.open) dialog.showModal();
  }
  _row(row, index) {
    const disabled = this._pending || this._mcpPending ? "disabled" : "";
    const field = (name, type, value, bounds = "") => `<input ${disabled} type="${type}" data-index="${index}" data-field="${name}" value="${scheduleEscape(value)}" ${bounds} aria-label="${index + 1}행 ${row.mode === "custom" && name === "frequency" ? "Run (%)" : name === "custom_frequency" ? "Stop (%)" : name}">`;
    const feed = row.mode === "feed", min = this._data?.flow_min || 30, max = this._data?.flow_max || 45;
    const options = feed ? '<span class="feeding-note">6분</span>'
      : scheduleWithFrequency(row.mode) ? `<div class="mode-options">${row.mode === "custom" ? '<span class="option-label">Run (%)</span>' : ""}${field("frequency", "number", row.frequency, 'min="5" max="100" step="1"')}${row.mode === "custom" ? `<div><span class="option-label">Stop (%)</span>${field("custom_frequency", "number", row.custom_frequency, 'min="5" max="100" step="1"')}</div>` : ""}</div>`
        : '<span class="empty-option">해당 없음</span>';
    return `<div class="row">
      <label><span class="cell-label">명령 시작</span>${field("time", "time", row.time, 'step="60"')}</label>
      <div><span class="cell-label">구간 종료</span><span class="end">${scheduleEscape(this._rows[index + 1]?.time || "24:00")}</span></div>
      <label class="mode-cell"><span class="cell-label">GMP 공통 Mode</span><select ${disabled} data-index="${index}" data-field="mode" aria-label="${index + 1}행 Mode">${Object.entries(AQUA_SCHEDULE_MODES).map(([value, label]) => `<option value="${value}" ${row.mode === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label><span class="cell-label">상단 Flow</span>${feed ? '<span class="feeding-note">정지</span>' : field("upper_flow", "number", row.upper_flow, `min="${min}" max="${max}" step="1"`)}</label>
      <label><span class="cell-label">하단 Flow</span>${feed ? '<span class="feeding-note">정지</span>' : field("lower_flow", "number", row.lower_flow, `min="${min}" max="${max}" step="1"`)}</label>
      <div><span class="cell-label">모드 옵션</span>${options}</div>
      <div class="row-actions"><button ${disabled} class="icon" data-action="copy" data-index="${index}" title="구간 복제" aria-label="${index + 1}행 복제">＋</button><button ${disabled} class="icon" data-action="delete" data-index="${index}" title="구간 삭제" aria-label="${index + 1}행 삭제">×</button></div>
    </div>`;
  }
  _timeline() {
    return this._rows.map((r, i) => {
      const start = scheduleMinute(r.time), end = scheduleMinute(this._rows[i + 1]?.time || "") || 1440;
      const width = Math.max(0, (end - start) / 1440 * 100);
      return `<span class="segment ${Object.hasOwn(AQUA_SCHEDULE_MODES, r.mode) ? r.mode : ""}" style="width:${width}%" title="${scheduleEscape(`${r.time} · ${AQUA_SCHEDULE_MODES[r.mode] || r.mode}`)}"></span>`;
    }).join("");
  }
  _mcpMarkup() {
    const disabled=this._pending || this._mcpPending ? "disabled" : "", [min,max]=this._mcpBounds();
    const field=(r,i,key,type,bounds="")=>`<input ${disabled} type="${type}" data-mcp-index="${i}" data-field="${key}" value="${scheduleEscape(r[key])}" ${bounds} aria-label="바형 ${i+1}행 ${key}">`;
    const modes=(this._data?.mcp_editor_modes || ["pulse","sine","constant","feed"])
      .filter(m=>Object.hasOwn(AQUA_MCP_MODES,m));
    return `<details class="mcp-companion" open><summary>MCP-70 · 바형 장치 시간표 · ${this._mcpRows.length}구간</summary>
      <div class="mcp-head"><p class="muted">템플릿 저장은 <b>장비 송신 0회</b>예요. <b>GMP 활성화로 MCP에 송신하지 않습니다.</b><br>바형 적용은 아래 전용 버튼만 사용해요. 장치가 자체 운전하며 타이머·전원·GMP 운전은 유지됩니다.</p><span class="mcp-status" role="status" aria-live="polite"></span></div>
      <div class="actions mcp-tools"><button data-mcp-action="import">장치 표 불러오기</button><button data-mcp-action="add">＋ 운전 구간</button><button data-mcp-action="feeding">＋ Feeding 6분</button><button data-mcp-action="preview">미리보기 확인</button><button class="primary" data-mcp-action="apply">저장한 바형 표 장비에 적용</button>${this._config?.mcp_timer_entity ? '<button class="pause" data-mcp-action="timer">시간표 운전</button>' : ""}</div>
      <div class="timeline">${this._mcpRows.map((r,i)=>`<span class="segment ${r.mode}" style="width:${Math.max(0,((scheduleMinute(this._mcpRows[i+1]?.time)||1440)-scheduleMinute(r.time))/14.4)}%"></span>`).join("")}</div><div class="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
      <div class="legend">${modes.map(m=>`<span><i class="${m}"></i>${scheduleEscape(AQUA_MCP_MODES[m])}</span>`).join("")}</div>
      <div class="mcp-list"><div class="mcp-row mcp-columns"><span>시작</span><span>종료</span><span>MCP Mode</span><span>Flow</span><span>Frequency</span><span></span></div>${this._mcpRows.map((r,i)=>`<div class="mcp-row">
      <label><span class="cell-label">시작</span>${field(r,i,"time","time",'step="60"')}</label><span class="end">${scheduleEscape(this._mcpRows[i+1]?.time || "24:00")}</span>
      <label class="mcp-mode"><span class="cell-label">MCP Mode</span><select ${disabled} data-mcp-index="${i}" data-field="mode" aria-label="바형 ${i+1}행 Mode">${modes.map(m=>`<option value="${m}" ${r.mode === m ? "selected" : ""}>${scheduleEscape(AQUA_MCP_MODES[m])}</option>`).join("")}</select></label>
      <label><span class="cell-label">Flow</span>${["feed","stopped"].includes(r.mode) ? '<span class="feeding-note">정지</span>' : field(r,i,"flow","number",`min="${min}" max="${max}" step="1"`)}</label>
      <label><span class="cell-label">Frequency</span>${r.mode === "feed" ? '<span class="feeding-note">6분</span>' : scheduleMcpFrequency(r.mode) ? field(r,i,"frequency","number",'min="5" max="100" step="1"') : '<span class="empty-option">해당 없음</span>'}</label>
      <div class="row-actions"><button ${disabled} class="icon" data-mcp-action="copy" data-index="${i}" aria-label="바형 ${i+1}행 복제">＋</button><button ${disabled} class="icon" data-mcp-action="delete" data-index="${i}" aria-label="바형 ${i+1}행 삭제">×</button></div></div>`).join("")}</div>
      <p class="muted">${this._mcpRows.length ? "바형 표를 수정한 뒤 위의 템플릿 저장 → 바형 적용 순서로 실행해요." : "현재 장치 표를 불러오거나 운전 구간을 추가하세요."} 최대 48구간 · 적용 Flow ${min}~${max} · Feeding 6분</p></details>`;
  }
  _render() {
    const d = this._data, role = (r) => r === "master" ? "Master" : r === "async_slave" ? "Async Slave" : "확인 중";
    this.shadowRoot.innerHTML = `<style>${AQUA_SCHEDULE_CSS}</style><ha-card>
      <div class="head"><div><div class="eyebrow">AQUARIUM · DAILY SCHEDULE</div><h1>수류모터 시간표</h1><p class="muted">GMP 2대 외부 시간표 · MCP-70 바형 장치 시간표</p></div><span class="status"></span></div>
      <div class="contract"><span>상단 <b>${role(d?.upper_role)}</b></span><span>하단 <b>${role(d?.lower_role)}</b></span><span class="next"></span></div>
      <aqua-maintenance-control></aqua-maintenance-control>
      <div class="template-bar"><label class="template-picker"><span>편집할 템플릿 · ${d?.templates?.length || 1}/${d?.max_templates || 32}</span><select data-template-selector aria-label="시간표 템플릿">${(d?.templates || [{ id:"", name:"기본 시간표", row_count:this._rows.length }]).map((t) => `<option value="${scheduleEscape(t.id)}" ${t.id === this._templateId ? "selected" : ""}>${scheduleEscape(t.name)} · ${t.row_count}구간${t.id === d?.active_template_id ? d?.controller_status === "active" ? " · 운전 중" : " · 마지막 실행본" : ""}</option>`).join("")}</select></label><button data-template-action="new">＋ 새 템플릿</button><button data-template-action="duplicate">복제</button><button data-template-action="rename">이름 변경</button><button class="delete-template" data-template-action="delete">삭제</button></div>
      <div class="actions"><button data-action="save">템플릿 저장</button><button class="primary" data-action="activate">선택 GMP 템플릿 활성화</button><button class="pause" data-action="pause">일시정지</button><span class="revision"></span></div>
      <div class="notice"><b class="active-template"></b><br>템플릿 선택·저장·복제는 <b>장비 송신 0회</b>입니다. GMP 활성화와 바형 장비 적용은 각각 별도 버튼으로 실행해요. 시각은 명령 시작 시각입니다.</div>
      <div class="feedback" role="status" aria-live="polite"></div><div class="validation" role="alert"></div>
      <div class="timeline">${this._timeline()}</div><div class="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
      <div class="legend">${Object.entries(AQUA_SCHEDULE_MODES).map(([value, label]) => `<span><i class="${value}"></i>${label}</span>`).join("")}</div>
      <div class="tools"><button data-action="add">＋ 운전 구간</button><button data-action="feeding">＋ Feeding 6분</button><button data-action="reload">저장본 불러오기</button><button data-action="active">실행본 불러오기</button><label class="file">JSON 가져오기<input type="file" accept="application/json,.json" ${this._pending || this._mcpPending ? "disabled" : ""}></label><button data-action="export">내보내기</button></div>
      <div class="rows"><div class="columns"><span>명령 시작</span><span>종료</span><span>GMP 공통 Mode</span><span>상단 Flow</span><span>하단 Flow</span><span>모드 옵션</span><span></span></div>${this._rows.map((r, i) => this._row(r, i)).join("")}</div>
      ${this._mcpMarkup()}
      <p class="footer">GMP의 9개 모드를 모두 편집합니다. Pulse · Sine · Random · Cross-flow에는 Frequency, Custom Wave에는 앱과 동일한 Run (%)·Stop (%)이 표시됩니다. Run 40·Stop 20은 40%·20%이며 초 단위가 아닙니다. Constant · Tide Rise and Fall · Nutrient Transport에는 Frequency가 없습니다. Feeding은 6분 뒤 다음 운전 행으로 복귀합니다. MCP-70은 바형 전용 적용 버튼으로만 변경하며 장치가 자체 시간표를 운전합니다.</p>
    </ha-card>${this._dialogMarkup()}`;
    this._renderStatus();
    this._showDialog();
  }
}
if (!customElements.get("aqua-schedule-editor")) {
  customElements.define("aqua-schedule-editor", AquaScheduleEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "aqua-schedule-editor", name: "Aqua Schedule", description: "GMP external schedule and separate native Bar schedule application" });
}

// One pending intent per HA entity across overview/editor mounts. Navigating or
// HA re-rendering must not turn an in-flight request into a second button press.
const AQUA_MAINTENANCE_PENDING = new Map();
class AquaMaintenanceControl extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:"open"});
    this.shadowRoot.addEventListener("click", e => {
      const button = e.target.closest("button[data-maintenance]");
      if (button && !button.disabled) void this._send(button.dataset.maintenance);
    });
  }
  set hass(value) { this._hass=value; this._render(); }
  connectedCallback() { this._timer=setInterval(()=>this._render(),1000); }
  disconnectedCallback() { clearInterval(this._timer); }
  _state() {
    const entity=this.entity || "sensor.aquarium_gmp_schedule_editor";
    const s=this._hass?.states?.[entity], d=s?.attributes, m=d?.maintenance;
    const age=Date.now()-Date.parse(d?.sampled_at || "");
    const online=!!m && m.available && !["unavailable","unknown"].includes(s?.state)
      && Number.isFinite(age) && age>=-10000 && age<60000;
    let pending=AQUA_MAINTENANCE_PENDING.get(entity);
    if (pending && d?.last_result?.request_id===pending.id) {
      AQUA_MAINTENANCE_PENDING.delete(entity);pending=null;
    }
    return {entity,d,m,online,pending};
  }
  async _send(operation) {
    const {entity,d,m,online,pending}=this._state();
    if (!online || pending || (d.busy && !d.communication?.query)) return;
    if (operation!=="maintenance_on" && operation!=="maintenance_off") return;
    if ((operation==="maintenance_on" && m.enabled && m.phase!=="error")
      || (operation==="maintenance_off" && !m.enabled)) return;
    const id=crypto.randomUUID().replaceAll("-", "");
    const request={schema_version:1,request_id:id,issued_at:new Date().toISOString(),
      operation,expected_active:d.active_sha256,expected_maintenance:m.generation};
    AQUA_MAINTENANCE_PENDING.set(entity,{id,at:Date.now(),operation});this._render();
    try {
      await this._hass.callService("mqtt","publish",{topic:d.command_topic,
        payload:JSON.stringify(request),qos:0,retain:false});
    } catch (_) {
      // The message might have arrived. Keep waiting for its correlated result.
      const current=AQUA_MAINTENANCE_PENDING.get(entity);
      if (current?.id===id) current.deliveryError=true;
      this._render();
    }
  }
  _render() {
    const {d,m,online,pending}=this._state();
    const enabled=!!m?.enabled, query=d?.communication?.query;
    const verifiedAge=Date.now()-Date.parse(m?.verified_at || "");
    const observed=m?.observed;
    const verified=online && m?.phase==="powered_off" && !query
      && Number.isFinite(verifiedAge) && verifiedAge>=-10000 && verifiedAge<60000
      && observed?.master_power===false && observed?.slave_power===false
      && observed?.bar_power===false && observed?.bar_timer===false;
    const label=!online ? "실행기 연결 확인 중" : pending ? "요청 처리 중"
      : query ? "장비 통신 확인 중" : !enabled ? d?.controller_status==="active" ? "OFF · 시간표 운전" : "OFF · 일정 일시정지"
      : verified ? "정지 중 · 세 모터 전원 OFF 확인"
      : ({starting:"세 모터 전원 OFF 전환 중",checking:"전원 OFF 유지 확인 중",
          powered_off:"전원 상태 재확인 필요",resuming:"전원 ON · 현재 시간표 복귀 중",
          feeding:"이전 Feeding 방식 · 종료 후 전원 OFF 방식 사용",
          error:"정지·복귀 확인 필요 · 시간표 보류"}[m.phase] || "장치 상태 확인 중");
    const busy=!!pending || (d?.busy && !query);
    const startDisabled=!online || busy || (enabled && m.phase!=="error");
    const endDisabled=!online || busy || !enabled;
    const lastError=d?.last_result?.operation?.startsWith("maintenance_")
      && d.last_result.outcome==="error" ? d.last_result.code : null;
    const note=pending?.deliveryError ? "요청 전달 응답이 없어 실행기 결과를 기다립니다. 자동 재송신하지 않습니다."
      : lastError ? `요청 결과: ${lastError}${m?.error_type ? " · "+m.error_type : ""}${m?.failure_stage ? " · "+m.failure_stage : ""}`
      : enabled ? "시간이 지나도 자동 복귀하지 않아요. 종료 버튼을 누르면 전원을 켜고 현재 시간표로 복귀해요."
      : "GMP 2대와 바형의 전원을 모두 끄고, 복귀할 때까지 두 시간표를 보류해요.";
    const powerText=value=>value===false ? "OFF" : value===true ? "ON" : "확인 필요";
    const powerRows=enabled && observed ? `<div class="targets" aria-label="마지막 장치 전원 응답">
      <span>상단 GMP <b>${powerText(d?.upper_role==="master" ? observed.master_power : observed.slave_power)}</b></span>
      <span>하단 GMP <b>${powerText(d?.lower_role==="master" ? observed.master_power : observed.slave_power)}</b></span>
      <span>바형 <b>${powerText(observed.bar_power)}</b></span></div><small>마지막 장치 응답 기준 · 응답이 없으면 정지를 확인할 수 없어요.</small>` : "";
    // Keep controls mounted during HA updates and freshness checks.
    if (!this.shadowRoot.querySelector('.box')) this.shadowRoot.innerHTML=`<style>
      :host{display:block;margin:14px 0 22px;color:#e9f6fc;font-family:inherit}
      .box{display:flex;align-items:center;gap:22px;padding:20px 22px;border:1px solid #2c4c60;
        border-radius:18px;background:#122a38}
      .box.enabled{border-color:#54877b;background:linear-gradient(110deg,#173c3c,#122b34)}
      .heading{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:700}
      ha-icon{color:#5fcebc;--mdc-icon-size:23px}.copy{flex:1;min-width:0}
      .status{font-size:12px;color:#a1bccb;margin:8px 0}.enabled .status{color:#8ce3c7}
      small{display:block;font-size:11px;color:#8eaaba;line-height:1.7}
      .targets{display:flex;flex-wrap:wrap;gap:8px;margin:9px 0 4px}.targets span{border:1px solid #365969;border-radius:7px;padding:4px 8px;font-size:11px}.targets b{margin-left:5px}
      .buttons{display:flex;gap:8px;flex-shrink:0}button{min-height:44px;padding:11px 17px;
        border:1px solid #426278;border-radius:12px;background:#1e3c4f;color:#d9ecf7;font:inherit;font-size:13px;line-height:1.5;word-break:keep-all;cursor:pointer}
      button.on{background:#226356;border-color:#3d9b85;color:#d5fff0}
      button:disabled{opacity:.4;cursor:default}button:focus-visible{outline:2px solid #64c5ff;outline-offset:3px}
      @media(max-width:760px){.box{align-items:stretch;flex-direction:column;gap:14px;padding:16px}.buttons button{flex:1}}
    </style><section class="box" aria-label="청소·환수 모드"><div class="copy">
      <div class="heading"><ha-icon icon="mdi:water-sync"></ha-icon>청소·환수 모드</div>
      <div class="status" role="status" aria-live="polite"></div>
      <div data-power-rows></div>
      <small data-note></small>
      <small>장치의 전원 OFF 명령이며, 전원 공급 자체를 차단하지는 않아요.</small>
    </div><div class="buttons"><button class="on" data-maintenance="maintenance_on"></button>
    <button data-maintenance="maintenance_off">환수 종료 · 전원·시간표 복귀</button></div></section>`;
    const root=this.shadowRoot;
    root.querySelector('.box').classList.toggle('enabled',enabled);
    const text=(selector,value)=>{const element=root.querySelector(selector);if(element.textContent!==value)element.textContent=value;};
    text('.status',`● ${label}`);text('[data-note]',note);
    const powers=root.querySelector('[data-power-rows]');
    if (powers.innerHTML!==powerRows) powers.innerHTML=powerRows;
    text('[data-maintenance="maintenance_on"]',enabled&&m?.phase==="error"?"전원 OFF 다시 확인":"환수 시작 · 세 모터 끄기");
    root.querySelector('[data-maintenance="maintenance_on"]').disabled=!!startDisabled;
    root.querySelector('[data-maintenance="maintenance_off"]').disabled=!!endDisabled;
  }
}
if (!customElements.get("aqua-maintenance-control")) customElements.define("aqua-maintenance-control",AquaMaintenanceControl);
