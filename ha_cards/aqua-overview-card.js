/* Aqua overview: read-only Home Assistant state presentation. No service calls. */
const AQUA_MODE_NAMES = Object.freeze({
  pulse: "Pulse", sine: "Sine", constant: "Constant", random: "Random",
  tidal: "Tide Rise and Fall", nutrient_transport: "Nutrient Transport",
  circulation: "Cross-flow", "Cross-flow": "Cross-flow", classic: "Classic", feed: "Feeding",
  custom: "Custom Wave", stopped: "Stop", auto: "Auto",
});
const AQUA_ROLE_NAMES = Object.freeze({
  independent: "Independent", master: "Master", sync_slave: "Sync Slave",
  async_slave: "Async Slave", slave: "Slave",
});

const aquaEscape = (value) => String(value ?? "—").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const aquaKnown = (value) => value !== undefined && value !== null &&
  value !== "" && value !== "unknown" && value !== "unavailable";
const aquaMode = (value) => AQUA_MODE_NAMES[value] || (aquaKnown(value) ? String(value) : "조회 불가");
const aquaRole = (value) => AQUA_ROLE_NAMES[value] || (aquaKnown(value) ? String(value) : "역할 미확인");
const aquaTemperature = (value, unit) => {
  if (!aquaKnown(value)) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return unit === "°F" || unit === "F" ? (parsed - 32) * 5 / 9 : parsed;
};
const aquaTempText = (value) => value === null ? "—" : value.toFixed(1);
const aquaHistoryTime = (row) => {
  const value = row.lu ?? row.lc ?? row.last_updated ?? row.last_changed;
  if (typeof value === "number") return Number.isFinite(value) ? value * 1000 : null;
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
};

const AQUA_OUTPUT_NAMES = Object.freeze({
  heating: "가열중", cooling: "냉각중", idle: "대기", off: "꺼짐",
});
const AQUA_OUTPUT_COLORS = Object.freeze({ heating: "#f35c63", cooling: "#63aeff" });
const aquaOutputAction = (value) => ["heating", "cooling", "idle", "off"].includes(value) ? value : "unknown";
// State history is a step function, not a temperature-derived estimate. Keep
// unavailable intervals and the not-yet-queried tail distinct from idle.
const aquaOutputSegments = (points, start, end, knownUntil) => {
  const limit = Math.max(start, Math.min(end, knownUntil ?? start));
  const ordered = (points || []).filter(p => Number.isFinite(p.time) && p.time <= limit)
    .slice().sort((a, b) => a.time - b.time);
  const segments = [];
  let cursor = start, action = "unknown";
  const append = (from, to, state) => {
    if (to <= from) return;
    const previous = segments.at(-1);
    if (previous?.action === state && previous.end === from) previous.end = to;
    else segments.push({ start: from, end: to, action: state });
  };
  for (const point of ordered) {
    if (point.time > start) {
      append(cursor, point.time, action);
      cursor = point.time;
    }
    action = aquaOutputAction(point.action);
  }
  append(cursor, limit, action);
  append(limit, end, "unknown");
  return segments;
};

const AQUA_STYLES = `
  :host { display:block; }
  ha-card { --aqua-text:#ecf7fb; --aqua-muted:#8ca4b4; --aqua-line:#254153;
    display:block; background:transparent; border:0; box-shadow:none; color:var(--aqua-text); }
  * { box-sizing:border-box; }
  .page { max-width:1500px; margin:0 auto; padding:22px 24px 36px;
    font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif; }
  .hero { border:1px solid #23485a; border-radius:26px;
    background:linear-gradient(130deg,#0e2532 0%,#0d1c29 62%,#0b1a25 100%);
    padding:26px; box-shadow:0 24px 60px #0004,inset 0 1px #ffffff0a; }
  .hero-head { display:flex; align-items:center; gap:16px; padding-bottom:23px; border-bottom:1px solid #294655; }
  .hero-icon,.section-icon,.kpi-icon { display:grid; place-items:center; flex:none; border-radius:16px; }
  .hero-icon { width:56px; height:56px; color:#42d9d0; background:#123a45; font-size:31px; }
  .eyebrow { color:#42d9d0; text-transform:uppercase; letter-spacing:.17em; font-size:10px; font-weight:750; }
  h1 { margin:5px 0 5px; font-size:29px; line-height:1.2; letter-spacing:-.04em; }
  .subtitle,.caption { color:var(--aqua-muted); font-size:13px; }
  .hero-pill { margin-left:auto; border-radius:99px; padding:9px 14px; background:#153a36;
    color:#8ae9b8; font-size:12px; font-weight:700; white-space:nowrap; }
  .hero-pill.warn { background:#493828; color:#ffca82; }
  .kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:20px; }
  .kpi { display:flex; align-items:center; gap:12px; min-height:78px; padding:14px 16px;
    background:#112634; border:1px solid #284657; border-radius:16px; }
  .kpi-icon { width:39px; height:39px; background:#173f4c; color:#42d9d0; font-size:21px; }
  .kpi:nth-child(2) .kpi-icon { color:#4bb1ff; background:#193951; }
  .kpi:nth-child(3) .kpi-icon { color:#ff5a9d; background:#422b42; }
  .kpi:nth-child(4) .kpi-icon { color:#ffc46b; background:#45392b; }
  .kpis.with-temperature .kpi:nth-child(1) .kpi-icon,
  .kpis.with-temperature .kpi:nth-child(2) .kpi-icon { color:#42d9d0; background:#173f4c; }
  .label { color:var(--aqua-muted); font-size:11px; margin-bottom:5px; }
  .kpi strong { font-size:19px; letter-spacing:-.03em; }
  .kpi small { color:#a8bcc9; font-size:11px; font-weight:500; margin-left:5px; }
  .card { min-width:0; border:1px solid var(--aqua-line); border-top:3px solid #4bb1ff;
    border-radius:23px; background:linear-gradient(155deg,#112635,#0d1b27); padding:21px;
    box-shadow:0 16px 40px #0003; }
  .temperature { margin-top:18px; border-top-color:#42d9d0; }
  .temperature .section-icon { color:#42d9d0; background:#143c46; }
  .temperature-layout { display:grid; grid-template-columns:minmax(240px,.65fr) minmax(0,1.35fr);
    gap:20px; min-height:270px; margin-top:18px; }
  .thermal-summary { display:flex; flex-direction:column; justify-content:space-between; gap:17px; }
  .temp-reading { font-size:64px; line-height:1; font-weight:780; letter-spacing:-.07em; white-space:nowrap; }
  .temp-reading small { font-size:24px; letter-spacing:0; color:#8adcd8; margin-left:5px; }
  .temp-target { display:flex; justify-content:space-between; gap:10px; color:#a9bdc9; font-size:12px; }
  .temp-target strong { color:#ecf7fb; font-size:19px; }
  .temp-delta { color:#f5c679; font-size:11px; }
  .thermal-state { border:1px solid #2a4755; border-radius:12px; background:#132936;
    padding:11px 12px; min-width:0; }
  .thermal-state.active.cool { background:#123446; border-color:#35768b; }
  .thermal-state.active.heat { background:#3a252f; border-color:#85434d; }
  .thermal-state strong { display:block; font-size:12px; }
  .thermal-state small { display:block; color:#90a9b8; font-size:10px; margin-top:4px; }
  .temperature-chart { display:flex; flex-direction:column; min-height:260px; min-width:0;
    padding:13px 15px 10px; background:#102431; border:1px solid #254452; border-radius:15px; }
  .chart-head,.chart-axis { display:flex; justify-content:space-between; gap:8px;
    color:#abc0cb; font-size:11px; }
  .chart-head { flex-wrap:wrap; align-items:center; }
  .chart-legend { color:#d7bc83; white-space:nowrap; }
  .chart-legends { display:flex; flex-wrap:wrap; gap:6px 12px; font-size:10px; }
  .output-legend { display:inline-flex; align-items:center; gap:5px; color:#a9bdc9; }
  .output-legend::before { content:""; width:8px; height:8px; border-radius:2px; background:${AQUA_OUTPUT_COLORS.heating}; }
  .output-legend.cool::before { background:${AQUA_OUTPUT_COLORS.cooling}; }
  .chart-output-note { color:#8ca4b4; font-size:10px; line-height:1.6; margin:9px 0 0; }
  .chart-output-note .heat { color:${AQUA_OUTPUT_COLORS.heating}; }.chart-output-note .cool { color:#8ec4ff; }
  .thermal-band { pointer-events:all; }.thermal-band:hover { opacity:.4; }
  .chart-plot > path,.chart-plot > circle { pointer-events:none; }
  .chart-plot { width:100%; flex:1; min-height:190px; display:block; margin:8px 0; }
  .chart-empty { display:grid; place-items:center; flex:1; color:#8ca4b4;
    font-size:12px; text-align:center; min-height:190px; }
  .chart-axis { color:#7792a2; font-size:10px; }
  .waves { margin-top:18px; }
  .card-head { display:flex; align-items:center; gap:12px; }
  .section-icon { width:41px; height:41px; background:#183b50; color:#4bb1ff; font-size:23px; }
  .card-title { font-size:18px; font-weight:760; letter-spacing:-.025em; }
  .card-caption { color:var(--aqua-muted); font-size:11px; margin-top:3px; }
  .section-link { margin-left:auto; border:1px solid #32586b; border-radius:99px; padding:8px 12px;
    color:#92d8f1; font-size:11px; text-decoration:none; white-space:nowrap; }
  .section-link:hover,.section-link:focus-visible { background:#1b4051; outline:none; }
  .pump-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }
  .pump { min-width:0; border:1px solid #2a485b; border-radius:16px; background:#142938; padding:16px; }
  .pump-name { font-size:15px; font-weight:750; }
  .pump-detail { color:#9fb5c2; font-size:11px; margin-top:4px; }
  .pump-mode { display:inline-block; margin-top:14px; padding:6px 9px; border-radius:8px;
    background:#153c49; color:#9adbe2; font-size:10px; }
  .pump-mode strong { color:#e3fbff; margin-left:6px; font-size:12px; }
  .waves .card-head { align-items:flex-start; }
  .wave-actions { margin-left:auto; min-width:0; }
  .scheduler-summary { display:grid; grid-template-columns:minmax(0,max-content) max-content;
    align-items:stretch; gap:6px 12px; }
  .scheduler-pill,.wave-actions .section-link { display:inline-flex; align-items:center; justify-content:center;
    min-height:36px; margin:0; padding:8px 12px; line-height:18px; }
  .scheduler-pill { border-radius:99px; font-size:11px;
    font-weight:700; color:#86e9b5; background:#153c35; border:1px solid #2d6654; }
  .scheduler-pill.warn { color:#ffd18e; background:#40362a; border-color:#736046; }
  .scheduler-pill.unknown { color:#a9bcc8; background:#1b303f; border-color:#3b5364; }
  .scheduler-summary small { grid-column:1 / -1; margin:0; text-align:left;
    color:#8ca4b4; font-size:10px; line-height:15px; }
  .pump-schedule { margin-top:14px; padding:11px 12px; border:1px solid #31515f; border-radius:10px;
    background:#102532; }
  .schedule-head { display:flex; justify-content:space-between; align-items:center; gap:8px; color:#96b8c4; font-size:10px; }
  .schedule-match { color:#80dfae; }.schedule-match.warn { color:#ffd18e; }.schedule-match.unknown { color:#9bb0bf; }
  .schedule-mode { display:block; color:#dfedf5; font-size:13px; margin-top:8px; }
  .pump-schedule .pump-options { margin-top:7px; min-height:0; }
  .schedule-time { display:block; margin-top:8px; font-size:10px; color:#8ca4b4; }
  .pump-options { display:flex; flex-wrap:wrap; gap:6px; min-height:24px; margin-top:10px; }
  .pump-option { border:1px solid #365669; border-radius:7px; padding:4px 7px;
    color:#adbfca; font-size:10px; }
  .pump-option b { color:#e8f5fb; margin-left:5px; }
  .pump-bottom { display:flex; justify-content:space-between; align-items:end; gap:8px;
    margin-top:16px; padding-top:12px; border-top:1px solid #2a4555; }
  .pump-bottom strong { font-size:18px; }
  .status { font-size:11px; color:#76dca9; text-align:right; }
  .status.stop { color:#ffc46b; }
  .status.unknown { color:#a7b8c4; }
  .device-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; margin-top:18px; }
  .returns { border-top-color:#4bb1ff; }.lighting { border-top-color:#ff5a9d; }
  .dosing { border-top-color:#ffc46b; }
  .returns .section-icon { color:#4bb1ff; }.lighting .section-icon { color:#ff5a9d; background:#3b2b42; }
  .dosing .section-icon { color:#ffc46b; background:#3e362b; }
  .small-line { display:flex; justify-content:space-between; gap:10px; border-top:1px solid #294454;
    padding:12px 0; font-size:12px; }
  .small-line:first-of-type { margin-top:17px; }
  .small-line span { color:#a9bdc9; }.small-line strong { text-align:right; }
  .small-line .ok { color:#78e0aa; }.small-line .feed { color:#ffc46b; }
  .small-line .muted { color:#9fb2be; }
  .foot { color:#7894a5; font-size:11px; margin:17px 3px 0; line-height:1.5; }
  @media(max-width:1000px) { .pump-list,.device-grid { grid-template-columns:1fr 1fr; } }
  @media(max-width:900px) { .temperature-layout { grid-template-columns:1fr; }
    .temperature-chart { min-height:250px; } }
  @media(max-width:700px) { .page { padding:12px 8px 24px; } .hero { padding:18px 14px; }
    .hero-icon { width:43px; height:43px; font-size:25px; }.hero-head { gap:10px; }
    h1 { font-size:23px; }.subtitle { font-size:11px; }.hero-pill { font-size:10px; padding:7px 9px; }
    .kpis { grid-template-columns:1fr 1fr; gap:8px; }.kpi { padding:10px; min-height:66px; }
    .kpi-icon { width:31px; height:31px; font-size:18px; }.kpi strong { font-size:16px; }
    .card { padding:16px; }.pump-list,.device-grid { grid-template-columns:1fr; }
    .waves .card-head { flex-wrap:wrap; }.wave-actions { width:100%; margin:5px 0 0; }
    .scheduler-summary { grid-template-columns:minmax(0,1fr) max-content; }
    .scheduler-pill { justify-self:start; }
  }
`;

class AquaOverviewCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config || !config.entities) throw new Error("Aqua overview needs entity mapping");
    this._config = config;
    this._loadTemperatureHistory();
    this._loadOutputHistory();
    this._loadScheduleStatus();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._loadTemperatureHistory();
    this._loadOutputHistory();
    this._loadScheduleStatus();
    this._render();
  }

  connectedCallback() {
    if (!this._refreshTimer) this._refreshTimer = setInterval(() => {
      this._loadTemperatureHistory();
      this._loadOutputHistory();
      this._loadScheduleStatus();
      this._render();
    }, 10_000);
    this._loadScheduleStatus();
  }

  disconnectedCallback() {
    clearInterval(this._refreshTimer);
    this._refreshTimer = null;
  }

  async _loadScheduleStatus() {
    if (!this._hass || !this._config || this._scheduleLoading ||
        Date.now() - (this._scheduleFetchedAt || 0) < 10_000) return;
    this._scheduleLoading = true;
    this._scheduleFetchedAt = Date.now();
    try {
      const url = this._config.schedule_status_url || "/local/aqua-overview/status/pro-schedule-status.json";
      const response = await fetch(`${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`, {
        cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error("schedule status unavailable");
      const value = await response.json();
      if (value.schema_version !== 1) throw new Error("schedule status version");
      this._scheduleData = value;
    } catch (_) {
      this._scheduleData = null;
    } finally {
      this._scheduleLoading = false;
      this._render();
    }
  }

  getCardSize() { return 9; }

  _state(id) {
    return this._hass?.states[id];
  }

  _value(id) {
    return this._state(id)?.state;
  }

  _inkbird(e) {
    if (!e) return null;
    const climate = this._state(e.climate);
    // Tuya may report temperature attributes while its HVAC mode is unknown.
    const climateAvailable = climate && climate.state !== "unavailable";
    const source = this._state(e.current);
    const targetSource = this._state(e.target);
    const defaultUnit = this._hass?.config?.unit_system?.temperature || "°C";
    const current = source && aquaKnown(source.state)
      ? aquaTemperature(source.state, source.attributes?.unit_of_measurement || defaultUnit)
      : climateAvailable
        ? aquaTemperature(climate.attributes?.current_temperature, climate.attributes?.temperature_unit || defaultUnit)
        : null;
    const target = targetSource && aquaKnown(targetSource.state)
      ? aquaTemperature(targetSource.state, targetSource.attributes?.unit_of_measurement || defaultUnit)
      : climateAvailable
        ? aquaTemperature(climate.attributes?.temperature, climate.attributes?.temperature_unit || defaultUnit)
        : null;
    const actionSensor = e.action_sensor ? this._state(e.action_sensor) : null;
    const actionClimate = e.action_climate ? this._state(e.action_climate) : climate;
    const sensorAction = actionSensor && actionSensor.state !== "unavailable"
      ? actionSensor.state : null;
    const action = ["heating", "cooling", "idle", "off"].includes(sensorAction)
      ? sensorAction
      : actionClimate && actionClimate.state !== "unavailable"
        ? actionClimate.attributes?.hvac_action : null;
    const relay = (id) => {
      const value = this._value(id);
      return value === "on" ? true : value === "off" ? false : null;
    };
    const knownAction = ["heating", "cooling", "idle", "off"].includes(action);
    const heating = e.heating ? relay(e.heating) : knownAction ? action === "heating" : null;
    const cooling = e.cooling ? relay(e.cooling) : knownAction ? action === "cooling" : null;
    return { current, target, heating, cooling, action };
  }

  _loadTemperatureHistory() {
    const e = this._config?.entities?.inkbird;
    const entity = e?.current || e?.climate;
    if (!entity || !this._hass?.callWS) return;
    const now = Date.now();
    if (this._historyEntity !== entity) {
      this._historyEntity = entity;
      this._historyPoints = null;
      this._historyAt = 0;
      this._historyLoading = false;
    }
    if (this._historyLoading || now - (this._historyAt || 0) < 5 * 60_000) return;
    this._historyLoading = true;
    this._historyAt = now;
    const start = new Date(now - 24 * 60 * 60_000).toISOString();
    const end = new Date(now).toISOString();
    this._hass.callWS({
      type: "history/history_during_period", entity_ids: [entity],
      start_time: start, end_time: end, minimal_response: false, no_attributes: false,
    }).then((history) => {
      if (this._historyEntity !== entity) return;
      const rows = history?.[entity] || [];
      const unit = this._state(entity)?.attributes?.unit_of_measurement
        || this._state(entity)?.attributes?.temperature_unit
        || this._hass?.config?.unit_system?.temperature || "°C";
      this._historyPoints = rows.map((row) => {
        const value = entity.startsWith("climate.") ? row.a?.current_temperature : row.s;
        return { time: aquaHistoryTime(row), value: aquaTemperature(value, unit) };
      }).filter((point) => point.time !== null && point.value !== null);
      this._historyLoading = false;
      this._render();
    }).catch(() => {
      if (this._historyEntity !== entity) return;
      this._historyPoints = null;
      this._historyLoading = false;
      this._historyAt = Date.now() - 4 * 60_000;
      this._render();
    });
  }

  _outputHistorySource() {
    const e = this._config?.entities?.inkbird;
    if (e?.action_sensor) return { entity: e.action_sensor, attribute: false };
    const entity = e?.action_climate || e?.climate;
    return entity ? { entity, attribute: true } : null;
  }

  _loadOutputHistory() {
    const source = this._outputHistorySource();
    const key = source ? JSON.stringify(source) : null;
    if (this._outputHistoryKey !== key) {
      this._outputHistoryKey = key;
      this._outputHistoryPoints = null;
      this._outputHistoryEnd = null;
      this._outputHistoryAt = null;
      this._outputHistoryRequest = null;
      this._outputHistoryError = false;
    }
    if (!source || !this._hass?.callWS) return;
    const now = Date.now();
    if (this._outputHistoryRequest || (this._outputHistoryAt !== null &&
        now - this._outputHistoryAt < 60_000)) return;
    const request = {};
    this._outputHistoryRequest = request;
    this._outputHistoryAt = now;
    this._hass.callWS({
      type: "history/history_during_period", entity_ids: [source.entity],
      start_time: new Date(now - 24 * 60 * 60_000).toISOString(),
      end_time: new Date(now).toISOString(), minimal_response: false, no_attributes: false,
    }).then(history => {
      if (this._outputHistoryRequest !== request) return;
      this._outputHistoryPoints = (history?.[source.entity] || []).map(row => ({
        time: aquaHistoryTime(row),
        action: aquaOutputAction(source.attribute
          ? (row.s === "unavailable" ? null : row.a?.hvac_action) : row.s),
      })).filter(point => point.time !== null);
      this._outputHistoryEnd = now;
      this._outputHistoryError = false;
    }).catch(() => {
      if (this._outputHistoryRequest !== request) return;
      // Retain the historical interval already read, never extend it on failure.
      this._outputHistoryError = true;
    }).finally(() => {
      if (this._outputHistoryRequest !== request) return;
      this._outputHistoryRequest = null;
      this._render();
    });
  }

  _outputSegments(now) {
    return aquaOutputSegments(this._outputHistoryPoints, now - 24 * 60 * 60_000,
      now, this._outputHistoryEnd);
  }

  _outputTime(time) {
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: this._hass?.config?.time_zone || "Asia/Seoul",
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(new Date(time));
  }

  _outputBands(now, x) {
    const colors = { heating: "url(#aqua-output-heating)", cooling: "url(#aqua-output-cooling)", idle: "transparent", off: "#90a2b3" };
    return this._outputSegments(now).filter(segment => segment.action !== "unknown").map(segment => {
      const title = `${AQUA_OUTPUT_NAMES[segment.action]} · ${this._outputTime(segment.start)} – ${this._outputTime(segment.end)}`;
      const opacity = segment.action === "heating" || segment.action === "cooling" ? ".28" : ".2";
      return `<rect class="thermal-band" data-output="${segment.action}" x="${x(segment.start).toFixed(3)}" y="20"
        width="${(x(segment.end) - x(segment.start)).toFixed(3)}" height="160" fill="${colors[segment.action]}" opacity="${opacity}"><title>${aquaEscape(title)}</title></rect>`;
    }).join("");
  }

  _outputHistoryNote() {
    if (!this._outputHistoryKey) return "출력 상태 이력 연결 없음";
    if (this._outputHistoryError) return "출력 이력 조회 실패 · 1분마다 재조회";
    if (!this._outputHistoryPoints?.length) return this._outputHistoryRequest
      ? "가열·냉각 이력 불러오는 중" : "저장된 출력 이력 없음";
    const segments = this._outputSegments(Date.now());
    const duration = action => {
      const ms = segments.filter(s => s.action === action).reduce((total, s) => total + s.end - s.start, 0);
      return ms > 0 && ms < 60_000 ? "1분 미만" : `${Math.floor(ms / 60_000)}분`;
    };
    return `기록된 <span class="heat">가열 ${duration("heating")}</span> · <span class="cool">냉각 ${duration("cooling")}</span>
      · 마지막 조회 ${aquaEscape(this._outputTime(this._outputHistoryEnd))} · 1분 갱신`;
  }

  _temperatureChart(inkbird) {
    const points = this._historyPoints;
    if (!points?.length) {
      const message = this._historyLoading ? "지난 24시간 수온을 불러오는 중" : "지난 24시간 수온 기록이 아직 없습니다";
      return `<div class="chart-empty">${message}</div>`;
    }
    const now = Date.now();
    const start = now - 24 * 60 * 60_000;
    const ordered = [...points].sort((a, b) => a.time - b.time);
    // HA includes the state at the query start. Render time (and the five-minute
    // cache) moves the window forward: carry that state to the new left edge.
    // Without a known predecessor, preserve the genuine leading history gap.
    const initial = ordered.filter((point) => point.time <= start).at(-1);
    const usable = ordered.filter((point) => point.time > start && point.time <= now);
    if (initial) usable.unshift({ time: start, value: initial.value });
    if (!usable.length) return `<div class="chart-empty">지난 24시간 수온 기록이 아직 없습니다</div>`;
    if (inkbird.current !== null && usable.at(-1).time < now) {
      usable.push({ time: now, value: inkbird.current });
    }
    const allValues = usable.map((point) => point.value);
    if (inkbird.target !== null) allValues.push(inkbird.target);
    const low = Math.min(...allValues);
    const high = Math.max(...allValues);
    const span = Math.max(0.8, high - low);
    const min = (high + low) / 2 - span / 2 - 0.2;
    const max = (high + low) / 2 + span / 2 + 0.2;
    const x = (time) => Math.max(0, Math.min(1000, (time - start) / (now - start) * 1000));
    const y = (value) => 180 - (value - min) / (max - min) * 160;
    const line = usable.map((point, index) => `${index ? "L" : "M"}${x(point.time).toFixed(1)} ${y(point.value).toFixed(1)}`).join(" ");
    const firstX = x(usable[0].time).toFixed(1);
    const lastX = x(usable.at(-1).time).toFixed(1);
    const area = `${line} L${lastX} 180 L${firstX} 180 Z`;
    const target = inkbird.target === null ? "" :
      `<path d="M0 ${y(inkbird.target).toFixed(1)} H1000" stroke="#ddb66c" stroke-width="2" stroke-dasharray="8 8" opacity=".8"/>`;
    return `<svg class="chart-plot" viewBox="0 0 1000 190" preserveAspectRatio="none" role="img" aria-label="지난 24시간 실제 수온 기록">
      <defs><linearGradient id="aqua-temperature-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#43d9d0" stop-opacity=".28"/><stop offset="1" stop-color="#43d9d0" stop-opacity="0"/></linearGradient>
      ${Object.entries(AQUA_OUTPUT_COLORS).map(([action, color]) => `<linearGradient id="aqua-output-${action}" gradientUnits="objectBoundingBox" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="${color}" stop-opacity="0"/><stop offset=".22" stop-color="${color}" stop-opacity="1"/>
        <stop offset=".78" stop-color="${color}" stop-opacity="1"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient>`).join("")}
      </defs>
      <g aria-label="실제 출력 상태 이력">${this._outputBands(now, x)}</g>
      <path d="M0 20 H1000 M0 60 H1000 M0 100 H1000 M0 140 H1000 M0 180 H1000" stroke="#274654" stroke-width="1" stroke-dasharray="4 8"/>
      ${target}<path d="${area}" fill="url(#aqua-temperature-fill)"/><path d="${line}" fill="none" stroke="#43d9d0" stroke-width="3" stroke-linejoin="round"/>
      <circle cx="${lastX}" cy="${y(usable.at(-1).value).toFixed(1)}" r="5" fill="#43d9d0"/></svg>`;
  }

  _temperatureCard(inkbird) {
    if (!inkbird) return "";
    const output = inkbird.heating === true && inkbird.cooling === true
      ? ["상태 오류", ""]
      : inkbird.heating === true
        ? ["가열중", "active heat"]
        : inkbird.cooling === true
          ? ["냉각중", "active cool"]
          : inkbird.action === "off"
            ? ["꺼짐", ""]
            : inkbird.heating === false && inkbird.cooling === false
              ? ["대기", ""]
              : ["상태 미확인", ""];
    const delta = inkbird.current !== null && inkbird.target !== null
      ? `목표 대비 ${inkbird.current - inkbird.target >= 0 ? "+" : ""}${(inkbird.current - inkbird.target).toFixed(1)} °C`
      : "목표와의 차이 미확인";
    return `<section class="card temperature"><div class="card-head"><div class="section-icon">♧</div>
      <div><div class="card-title">수온 컨트롤</div><div class="card-caption">INKBIRD ITC-308-WIFI · HA 실제 상태</div></div></div>
      <div class="temperature-layout"><div class="thermal-summary"><div><div class="label">현재 수온</div>
      <div class="temp-reading">${aquaTempText(inkbird.current)}<small>°C</small></div></div>
      <div><div class="temp-target"><span>목표 수온</span><strong>${aquaTempText(inkbird.target)} °C</strong></div>
      <div class="temp-delta">${aquaEscape(delta)}</div></div>
      <div class="thermal-state ${output[1]}" role="status" aria-live="polite"><strong>${output[0]}</strong><small>잉키버드 실제 출력</small></div></div>
      <div class="temperature-chart"><div class="chart-head"><strong>지난 24시간 수온</strong><div class="chart-legends">
      <span class="output-legend">가열중</span><span class="output-legend cool">냉각중</span><span class="chart-legend">— 현재 목표</span></div></div>
      ${this._temperatureChart(inkbird)}<div class="chart-axis"><span>24시간 전</span><span>18시간 전</span><span>12시간 전</span><span>6시간 전</span><span>지금</span></div>
      <div class="chart-output-note">${this._outputHistoryNote()}</div></div></div>
    </section>`;
  }

  _observed(id) {
    const state = this._state(id);
    if (!state || state.state === "unavailable" || state.state === "unknown") return null;
    const when = Date.parse(state?.attributes?.observed_at || "");
    if (!Number.isFinite(when) || Math.abs(Date.now() - when) > 2 * 60_000) return null;
    return state.attributes.observed || null;
  }

  _faults(ids) {
    let count = 0;
    for (const id of ids) {
      const observed = this._observed(id);
      if (!observed) return null;
      count += Object.entries(observed).filter(([key, value]) => key.startsWith("Fault_") && value === true || key === "OpenCircuit" && value === true).length;
    }
    return count;
  }

  _chip(name, value) {
    if (!aquaKnown(value)) return "";
    return `<span class="pump-option">${aquaEscape(name)}<b>${aquaEscape(value)}</b></span>`;
  }

  _pump(name, model, role, mode, flow, options, status, schedule = "") {
    const statusClass = status === "운전 중" ? "" : ["정지", "Feeding", "Mode 상이"].includes(status) ? "stop" : "unknown";
    return `<div class="pump">
      <div class="pump-name">${aquaEscape(name)}</div>
      <div class="pump-detail">${aquaEscape(model)} · ${aquaEscape(role)}</div>
      <div class="pump-mode">장치 보고 모드<strong>${aquaEscape(mode)}</strong></div>
      <div class="pump-options">${options.join("")}</div>
      ${schedule}
      <div class="pump-bottom"><strong>${aquaKnown(flow) ? `Flow ${aquaEscape(flow)}` : "Flow —"}</strong>
        <span class="status ${statusClass}">● ${aquaEscape(status)}</span></div>
    </div>`;
  }

  _scheduleState() {
    const editor = this._hass?.states?.["sensor.aquarium_gmp_schedule_editor"];
    const dataEditor = editor?.attributes;
    if (dataEditor?.maintenance?.enabled && !["unknown","unavailable"].includes(editor.state)
        && Math.abs(Date.now() - Date.parse(dataEditor.sampled_at)) < 60000) {
      return {kind:"warn", label:"청소·환수 · 시간표 보류", data:null};
    }
    const data = this._scheduleData;
    const sampledAt = Date.parse(data?.sampled_at || "");
    if (!data || !Number.isFinite(sampledAt) || Math.abs(Date.now() - sampledAt) > 60_000) {
      return { kind: "unknown", label: "스케줄러 상태 확인 불가", data: null };
    }
    const names = {
      normal: ["", "스케줄러 정상"], paused: ["warn", "스케줄러 일시정지"],
      stopped: ["warn", "스케줄러 중지"], delayed: ["warn", "경계 적용 지연"],
      transitioning: ["warn", "시간표 전환 중"], no_schedule: ["unknown", "활성 시간표 없음"],
      source_error: ["unknown", "스케줄러 상태 확인 불가"],
      state_retry: ["warn", "상태 조회 재시도 중"],
      communication_error: ["warn", "장비 통신 오류 · 재시도 중"],
    };
    const [kind, label] = names[data.health] || names.source_error;
    if (!data.current) return { kind, label, data: null };
    const rowStart = Date.parse(data.current.starts_at);
    const rowEnd = Date.parse(data.current.ends_at);
    if (!Number.isFinite(rowStart) || !Number.isFinite(rowEnd) || Date.now() < rowStart || Date.now() >= rowEnd) {
      return { kind: "warn", label: "시간표 전환 확인 중", data: null };
    }
    return { kind, label, data };
  }

  _proComparison(position, e, data) {
    if (!this._observed(e.current_schedule)) return { kind: "unknown", label: "보고 확인 불가" };
    const row = data.current;
    const required = ["power", "schedule_enable", "role", "mode"];
    if (row.mode !== "feed") required.push("flow");
    if (row.frequency !== null) required.push("frequency");
    if (row.mode === "custom") required.push("custom_frequency");
    if (required.some((key) => !aquaKnown(this._value(e[key])))) return { kind: "unknown", label: "보고 확인 불가" };
    const matches = this._value(e.power) === "on" && this._value(e.schedule_enable) === "off"
      && this._value(e.role) === data[`${position}_role`] && this._value(e.mode) === row.mode
      && (row.mode === "feed" || Number(this._value(e.flow)) === row[`${position}_flow`])
      && (row.frequency === null || Number(this._value(e.frequency)) === row.frequency)
      && (row.mode !== "custom" || Number(this._value(e.custom_frequency)) === row.custom_frequency);
    return matches ? { kind: "", label: "값 일치" } : { kind: "warn", label: "값 불일치" };
  }

  _targetBlock(row, flow, comparison, source) {
    const options = row.mode === "feed" ? [aquaKnown(row.feed_minutes) ? this._chip("Feed Time", `${row.feed_minutes}분`) : ""]
      : [this._chip("Flow", flow)];
    if (aquaKnown(row.frequency) && ["pulse", "sine", "random", "circulation", "classic", "custom"].includes(row.mode)) {
      options.push(this._chip(row.mode === "custom" ? "Run" : "Frequency", row.mode === "custom" ? `${row.frequency}%` : row.frequency));
    }
    if (row.mode === "custom") options.push(this._chip("Stop", aquaKnown(row.custom_frequency) ? `${row.custom_frequency}%` : row.custom_frequency));
    const modeName = row.mode === "classic" ? row.cross_flow ? "Classic · Cross-flow" : "Classic · Pulse" : aquaMode(row.mode);
    return `<div class="pump-schedule"><div class="schedule-head"><span>시간표 목표</span>
      <span class="schedule-match ${comparison.kind}">${aquaEscape(comparison.label)}</span></div>
      <strong class="schedule-mode">${aquaEscape(modeName)}</strong><div class="pump-options">${options.join("")}</div>
      <small class="schedule-time">${aquaEscape(row.start)}–${aquaEscape(row.end)} · ${aquaEscape(source)}</small></div>`;
  }

  _proTarget(position, e) {
    const scheduler = this._scheduleState();
    if (scheduler.label === "청소·환수 · 시간표 보류") return `<div class="pump-schedule"><span class="schedule-head">청소·환수 · Feeding 유지 요청</span><small>종료 시 현재 시간표로 복귀</small></div>`;
    if (!scheduler.data) return `<div class="pump-schedule"><span class="schedule-head">시간표 목표 · 조회 불가</span></div>`;
    const comparison = scheduler.data.health === "normal" ? this._proComparison(position, e, scheduler.data)
      : { kind: scheduler.kind, label: scheduler.label.replace("스케줄러 ", "") };
    return this._targetBlock(scheduler.data.current, scheduler.data.current[`${position}_flow`], comparison, "외부 시간표");
  }

  _schedulerSummary() {
    const scheduler = this._scheduleState();
    let { kind, label } = scheduler;
    if (scheduler.data?.health === "normal") {
      const results = ["upper", "lower"].map((position) => this._proComparison(position, this._config.entities[position], scheduler.data));
      if (results.some((result) => result.kind === "warn")) { kind = "warn"; label = "스케줄러 · 장치값 불일치"; }
      else if (results.some((result) => result.kind === "unknown")) { kind = "unknown"; label = "스케줄러 실행 중 · 장치 확인 불가"; }
    }
    const next = scheduler.data?.current?.end;
    return `<div class="scheduler-summary" role="status" aria-live="polite"><span class="scheduler-pill ${kind}">● ${aquaEscape(label)}</span>
      <a class="section-link" href="/aqua-dashboard/wavemakers">상세 보기 →</a>
      <small>${next ? `다음 전환 ${aquaEscape(next)} · ` : ""}GMP 외부 시간표</small></div>`;
  }

  _pro(label, e, position) {
    const power = this._value(e.power);
    const scheduled = this._value(e.schedule_enable) === "on";
    const auto = scheduled ? this._observed(e.current_schedule) : null;
    const manualKey = this._value(e.mode);
    const modeKey = scheduled ? auto?.AutoMode : manualKey;
    const mode = aquaMode(modeKey);
    const flow = power === "off" || modeKey === "feed" ? null
      : scheduled ? auto?.AutoFlow : this._value(e.flow);
    const frequency = scheduled ? auto?.AutoFreq : this._value(e.frequency);
    const role = aquaRole(this._value(e.role));
    const options = [];
    if (["pulse", "sine", "random", "circulation", "Cross-flow"].includes(modeKey)) {
      options.push(this._chip("Frequency", frequency));
    } else if (modeKey === "custom") {
      options.push(this._chip("Run", aquaKnown(frequency) ? `${frequency}%` : frequency));
      const stop = scheduled ? auto?.Auto_Cust_Wav_Freq : this._value(e.custom_frequency);
      options.push(this._chip("Stop", aquaKnown(stop) ? `${stop}%` : stop));
    } else if (modeKey === "feed") {
      const feedTime = scheduled ? auto?.AutoFeedTime : this._value(e.feed_time);
      if (aquaKnown(feedTime) && Number(feedTime) > 0) options.push(this._chip("Feed Time", `${feedTime}분`));
    }
    if (scheduled && aquaKnown(manualKey) && manualKey !== modeKey) {
      options.push(this._chip("수동 Mode", aquaMode(manualKey)));
    }
    const status = !aquaKnown(power) || !aquaKnown(modeKey) ? "조회 불가"
      : power === "off" ? "정지" : modeKey === "feed" ? "Feeding"
        : scheduled && manualKey === "feed" ? "Mode 상이" : "운전 중";
    return this._pump(label, scheduled ? "장치 시간표" : "수동 / 외부 시간표", role, mode, flow, options, status, this._proTarget(position, e));
  }

  _barTarget(e, mode, flow, frequency, cross) {
    const schedule = this._observed(e.schedule);
    if (!schedule || !Array.isArray(schedule.entries)) return `<div class="pump-schedule"><span class="schedule-head">시간표 목표 · 조회 불가</span></div>`;
    const local = new Intl.DateTimeFormat("en-GB", { timeZone: this._hass.config?.time_zone || "Asia/Seoul", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date());
    const matches = schedule.entries.filter((entry) => entry.start <= local && local < entry.end);
    if (matches.length !== 1) return `<div class="pump-schedule"><span class="schedule-head">시간표 목표 · 현재 구간 없음</span></div>`;
    const entry = matches[0];
    const parameters = entry.parameters || {};
    const row = { start: entry.start, end: entry.end, mode: entry.mode, frequency: parameters.frequency,
      cross_flow: parameters.pulse_tide === true, feed_minutes: parameters.feed_time,
      custom_frequency: parameters.custom_wave_frequency };
    const available = this._observed(e.current_schedule) && aquaKnown(mode)
      && aquaKnown(this._value(e.power)) && aquaKnown(this._value(e.schedule_enable))
      && (row.mode === "feed" || aquaKnown(flow))
      && (!["classic", "sine"].includes(row.mode) || aquaKnown(frequency));
    const same = mode === row.mode && this._value(e.power) === "on" && this._value(e.schedule_enable) === "on"
      && (row.mode === "feed" || Number(flow) === parameters.flow)
      && (!["classic", "sine"].includes(row.mode) || Number(frequency) === row.frequency)
      && (row.mode !== "classic" || cross === row.cross_flow);
    const comparison = !available ? { kind: "unknown", label: "보고 확인 불가" }
      : same ? { kind: "", label: "값 일치" } : { kind: "warn", label: "값 불일치" };
    return this._targetBlock(row, parameters.flow, comparison, "장치 시간표");
  }

  _bar(e) {
    const power = this._value(e.power);
    const feed = this._value(e.feed) === "on";
    const scheduled = this._value(e.schedule_enable) === "on";
    const auto = scheduled ? this._observed(e.current_schedule) : null;
    const modeKey = feed ? "feed" : scheduled ? auto?.AutoMode : this._value(e.mode);
    const flow = power === "off" || modeKey === "feed" || modeKey === "stopped" ? null
      : scheduled ? auto?.AutoFlow : this._value(e.flow);
    const frequency = scheduled ? auto?.AutoFreq : this._value(e.frequency);
    const cross = scheduled ? auto?.AutoPulseTide === true : this._value(e.cross_flow) === "on";
    const options = [];
    if (modeKey === "classic") {
      options.push(`<span class="pump-option">${cross ? "Cross-flow" : "Pulse"}</span>`);
    }
    if (["classic", "sine"].includes(modeKey)) options.push(this._chip("Frequency", frequency));
    if (modeKey === "feed") {
      const feedTime = feed ? this._value(e.feed_time) : scheduled ? auto?.AutoFeedTime : this._value(e.feed_time);
      if (aquaKnown(feedTime) && Number(feedTime) > 0) options.push(this._chip("Feed Time", `${feedTime}분`));
    }
    const status = !aquaKnown(power) || !aquaKnown(modeKey) ? "조회 불가"
      : power === "off" || modeKey === "stopped" ? "정지"
      : modeKey === "feed" ? "Feeding" : "운전 중";
    return this._pump("MCP-70 · 바형", scheduled ? "장치 시간표" : "수동", aquaRole(this._value(e.role)), aquaMode(modeKey), flow, options, status, this._barTarget(e, modeKey, flow, frequency, cross));
  }

  _returnStatus(e, kind) {
    const power = this._value(e.power);
    if (!aquaKnown(power)) return ["조회 불가", "muted"];
    if (power === "off") return ["정지", "feed"];
    if (kind === "aquarium") {
      if (this._value(e.feed) === "on") return ["Feeding", "feed"];
      return ["운전 중", "ok"];
    }
    const scheduled = this._value(e.schedule_enable) === "on";
    const auto = scheduled ? this._observed(e.current_schedule) : null;
    const mode = scheduled ? auto?.AutoMode : this._value(e.mode);
    if (!aquaKnown(mode)) return ["모드 조회 불가", "muted"];
    if (mode === "feed") return ["Feed Mode", "feed"];
    return ["운전 중", "ok"];
  }

  _lightOutput(id, mode) {
    const light = this._state(id);
    if (!light || !aquaKnown(light.state)) return mode === "auto" ? "Auto 모드에서 미제공" : "조회 불가";
    if (light.state === "off") return "OFF";
    if (light.state === "on") {
      const brightness = light.attributes?.brightness;
      if (typeof brightness === "number" && Number.isFinite(brightness)) {
        return `${Math.round(brightness / 255 * 100)}%`;
      }
      return "ON";
    }
    return String(light.state);
  }

  _render() {
    if (!this._config || !this._hass) return;
    const e = this._config.entities;
    const pro = [e.upper, e.lower];
    const pumpPowers = [...pro.map((item) => this._value(item.power)), this._value(e.bar.power)];
    const returnPowers = [this._value(e.main_return.power), this._value(e.uv_return.power)];
    const received = [...pumpPowers, ...returnPowers, this._value(e.dosing.power), this._value(e.light.mode)]
      .filter(aquaKnown).length;
    const faults = this._faults([e.upper.fault, e.lower.fault, e.bar.fault,
      e.main_return.fault, e.uv_return.fault, e.dosing.fault]);
    const lightMode = this._value(e.light.mode);
    const timerStates = e.dosing.timers.map((id) => this._value(id));
    const timerCount = timerStates.filter((value) => value === "on").length;
    const timersKnown = timerStates.every(aquaKnown);
    const [mainStatus, mainStatusClass] = this._returnStatus(e.main_return, "aquarium");
    const [uvStatus, uvStatusClass] = this._returnStatus(e.uv_return, "dc");
    const mainSpeed = this._value(e.main_return.speed);
    const uvAuto = this._observed(e.uv_return.current_schedule);
    const uvFlow = uvStatus === "Feed Mode" ? null
      : this._value(e.uv_return.schedule_enable) === "on" ? uvAuto?.AutoFlow : this._value(e.uv_return.flow);
    const warningLabel = faults === null ? "미확인" : `${faults}건`;
    const inkbird = this._inkbird(e.inkbird);
    const kpis = inkbird ? `<div class="kpi"><div class="kpi-icon">♧</div><div><div class="label">현재 수온</div><strong>${aquaTempText(inkbird.current)} °C</strong></div></div>
          <div class="kpi"><div class="kpi-icon">♧</div><div><div class="label">목표 수온</div><strong>${aquaTempText(inkbird.target)} °C</strong></div></div>
          <div class="kpi"><div class="kpi-icon">≋</div><div><div class="label">수류모터 전원</div><strong>${pumpPowers.filter((value) => value === "on").length}/3</strong><small>ON</small></div></div>
          <div class="kpi"><div class="kpi-icon">◇</div><div><div class="label">장치 경고</div><strong>${aquaEscape(warningLabel)}</strong></div></div>`
      : `<div class="kpi"><div class="kpi-icon">≋</div><div><div class="label">수류모터 전원</div><strong>${pumpPowers.filter((value) => value === "on").length}/3</strong><small>ON</small></div></div>
          <div class="kpi"><div class="kpi-icon">◉</div><div><div class="label">리턴 전원</div><strong>${returnPowers.filter((value) => value === "on").length}/2</strong><small>ON</small></div></div>
          <div class="kpi"><div class="kpi-icon">☼</div><div><div class="label">NP 조명 모드</div><strong>${aquaEscape(aquaKnown(lightMode) ? lightMode.toUpperCase() : "미확인")}</strong></div></div>
          <div class="kpi"><div class="kpi-icon">◇</div><div><div class="label">장치 경고</div><strong>${aquaEscape(warningLabel)}</strong></div></div>`;
    this.shadowRoot.innerHTML = `<style>${AQUA_STYLES}</style><ha-card><main class="page">
      <section class="hero"><div class="hero-head"><div class="hero-icon">◈</div>
        <div><div class="eyebrow">Reef overview</div><h1>우리집 해수어항</h1>
          <div class="subtitle">${inkbird ? "수온 · 수류 · 리턴 · 조명 · 도징 현황" : "수류 · 리턴 · 조명 · 도징 현황"}</div></div>
        <div class="hero-pill ${received < 7 ? "warn" : ""}">● HA 상태 수신 ${received}/7</div></div>
        <div class="kpis ${inkbird ? "with-temperature" : ""}">${kpis}</div></section>
      ${this._temperatureCard(inkbird)}
      <section class="card waves"><div class="card-head"><div class="section-icon">≋</div>
        <div><div class="card-title">수류모터</div><div class="card-caption">GMP 2대 · MCP-70 바형 1대</div></div>
        <div class="wave-actions">${this._schedulerSummary()}</div></div>
        <aqua-maintenance-control></aqua-maintenance-control>
        <div class="pump-list">${this._pro("상단 GMP", e.upper, "upper")}${this._pro("하단 GMP", e.lower, "lower")}${this._bar(e.bar)}</div>
      </section>
      <div class="device-grid">
        <section class="card returns"><div class="card-head"><div class="section-icon">◉</div>
          <div><div class="card-title">리턴 펌프</div><div class="card-caption">메인 리턴 · UV용 리턴</div></div>
          <a class="section-link" href="/aqua-dashboard/return-pumps">상세 →</a></div>
          <div class="small-line"><span>메인 · Aquarium Pump</span><strong class="${mainStatusClass}">${aquaEscape(mainStatus)}</strong></div>
          <div class="small-line"><span>메인 수동 속도 설정</span><strong>${aquaEscape(aquaKnown(mainSpeed) ? mainSpeed : "—")}</strong></div>
          <div class="small-line"><span>UV · DC Pump Pro</span><strong class="${uvStatusClass}">${aquaEscape(uvStatus)}</strong></div>
          <div class="small-line"><span>UV 장치 보고 Flow</span><strong>${aquaEscape(aquaKnown(uvFlow) ? uvFlow : "—")}</strong></div>
        </section>
        <section class="card lighting"><div class="card-head"><div class="section-icon">☼</div>
          <div><div class="card-title">해수어 조명</div><div class="card-caption">Noopsyche K7 · NP 조명</div></div>
          <a class="section-link" href="/aqua-dashboard/lights">상세 →</a></div>
          <div class="small-line"><span>작동 모드</span><strong>${aquaEscape(aquaKnown(lightMode) ? lightMode.toUpperCase() : "조회 불가")}</strong></div>
          <div class="small-line"><span>수동 출력</span><strong>${aquaEscape(this._lightOutput(e.light.manual_output, lightMode))}</strong></div>
        </section>
        <section class="card dosing"><div class="card-head"><div class="section-icon">◇</div>
          <div><div class="card-title">도징</div><div class="card-caption">4채널 도징 펌프</div></div>
          <a class="section-link" href="/aqua-dashboard/dosing">상세 →</a></div>
          <div class="small-line"><span>본체 전원</span><strong class="${this._value(e.dosing.power) === "on" ? "ok" : "feed"}">${aquaEscape(this._value(e.dosing.power) === "on" ? "ON" : this._value(e.dosing.power) === "off" ? "OFF" : "조회 불가")}</strong></div>
          <div class="small-line"><span>시간표 사용 채널</span><strong>${timersKnown ? `${timerCount}/4` : "조회 불가"}</strong></div>
          <div class="small-line"><span>보정 실행</span><strong>${aquaEscape(this._value(e.dosing.calibration) === "on" ? "실행 중" : this._value(e.dosing.calibration) === "off" ? "대기" : "조회 불가")}</strong></div>
        </section>
      </div>
      <div class="foot">표시값은 HA의 장치 보고·상태 엔티티에서 읽습니다. 청소·환수 버튼은 실제 수류모터를 제어합니다.${inkbird ? " 수온 그래프는 HA에 저장된 지난 24시간 기록입니다." : " 수온은 Inkbird 연동 후 추가됩니다."}</div>
    </main></ha-card>`;
    const bindMaintenance = () => {
      const control = this.shadowRoot.querySelector?.("aqua-maintenance-control");
      if (control) control.hass = this._hass;
    };
    if (customElements.get("aqua-maintenance-control")) bindMaintenance();
    else customElements.whenDefined?.("aqua-maintenance-control").then(bindMaintenance);
  }
}

if (!customElements.get("aqua-overview-card")) {
  customElements.define("aqua-overview-card", AquaOverviewCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "aqua-overview-card", name: "Aqua Overview", description: "Read-only live aquarium overview" });
}
