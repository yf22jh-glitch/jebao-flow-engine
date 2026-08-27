const FLOW_CARD_VERSION = "0.1.0";

const PATTERNS = {
  constant: ["고정", "mdi:minus"],
  sync: ["동기", "mdi:swap-horizontal-bold"],
  anti_phase: ["교대", "mdi:swap-horizontal"],
  lagoon: ["라군", "mdi:waves"],
  reef_crest: ["리프 크레스트", "mdi:chart-bell-curve-cumulative"],
  gyre: ["자이어", "mdi:rotate-360"],
  tidal_swell: ["타이달 스웰", "mdi:weather-windy"],
  nutrient_transport: ["영양염 배출", "mdi:water-sync"],
};

const ROLE_LABELS = {
  left: "왼쪽 메인",
  right: "오른쪽 메인",
  crossflow: "바형 크로스플로우",
  support: "보조 수류",
};

const STATUS_LABELS = {
  stopped: "정지",
  starting: "시작 중",
  running: "운전 중",
  feeding: "급여 모드",
  maintenance: "정비 모드",
  degraded: "제한 운전",
  error: "오류",
  emergency_stop: "비상 정지",
};

const EXECUTION_STRATEGY_LABELS = {
  software_independent: "소프트웨어 독립 제어",
  native_linked: "네이티브 페어 + 독립 보조",
};

const ACTUATION_LABELS = {
  software_independent: "독립 소프트웨어 제어",
  native_master: "네이티브 마스터",
  native_sync_slave: "네이티브 Sync 종속",
  native_async_slave: "네이티브 Async 종속",
};

const SCHEDULE_MODE_LABELS = {
  stopped: "정지",
  constant: "고정",
  pulse: "펄스",
  sine: "사인",
  random: "랜덤",
  classic: "클래식",
  tidal: "조석",
  nutrient_transport: "영양염 배출",
  circulation: "순환",
  feed: "급여",
  custom: "사용자 설정",
  auto: "자동",
};

const SCHEDULE_PARAMETER_LABELS = {
  flow: "출력",
  frequency: "주파수",
  feed_time: "급여 값",
  pulse_tide: "펄스/조석",
  custom_frequency: "사용자 주파수",
  gears: "출력/단계",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isScheduleTime(value) {
  if (typeof value !== "string") return false;
  if (value === "24:00") return true;
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
}

function scheduleScalar(value) {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "켜짐" : "꺼짐";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value.slice(0, 80) || "—";
  return undefined;
}

function scheduleParameterValue(key, value) {
  if (key.toLowerCase() === "pulse_tide") {
    if (value === false || value === 0) return "펄스";
    if (value === true || value === 1) return "조석";
  }
  return scheduleScalar(value);
}

function normalizeScheduleEntry(value) {
  if (!isPlainObject(value)) return null;
  const slot = value.slot;
  const modeCode = value.mode_code;
  if (!Number.isInteger(slot) || slot < 0 || slot >= 48) return null;
  if (!isScheduleTime(value.start) || !isScheduleTime(value.end)) return null;
  if (typeof value.mode !== "string" || !value.mode.trim()) return null;
  if (!Number.isInteger(modeCode) || modeCode < 0 || modeCode > 255) return null;

  const parameters = [];
  if (isPlainObject(value.parameters)) {
    Object.entries(value.parameters).slice(0, 16).forEach(([key, parameter]) => {
      const normalizedKey = key.trim().slice(0, 64);
      if (!normalizedKey
        || normalizedKey.toLowerCase().includes("hex")
        || normalizedKey.toLowerCase().startsWith("raw")) return;
      const formatted = scheduleParameterValue(normalizedKey, parameter);
      if (formatted === undefined) return;
      parameters.push({ key: normalizedKey, value: formatted });
    });
  }
  return {
    slot,
    start: value.start,
    end: value.end,
    mode: value.mode.trim().slice(0, 64),
    modeCode,
    parameters,
  };
}

function normalizeSchedule(entity) {
  const attributes = entity?.attributes;
  if (!isPlainObject(attributes) || !Array.isArray(attributes.entries)) return null;
  const entries = attributes.entries
    .slice(0, 48)
    .map(normalizeScheduleEntry)
    .filter((entry) => entry !== null)
    .sort((left, right) => left.slot - right.slot);
  const invalidSlots = Array.isArray(attributes.invalid_slots)
    ? attributes.invalid_slots
      .slice(0, 48)
      .filter((slot) => Number.isInteger(slot) && slot >= 0 && slot < 48)
    : [];
  return {
    enabled: typeof attributes.enabled === "boolean" ? attributes.enabled : null,
    entries,
    invalidSlots,
  };
}

function scheduleModeLabel(mode) {
  const key = mode.toLowerCase();
  return Object.hasOwn(SCHEDULE_MODE_LABELS, key)
    ? SCHEDULE_MODE_LABELS[key]
    : `기타 모드 (${mode})`;
}

function scheduleParameterLabel(key) {
  return Object.hasOwn(SCHEDULE_PARAMETER_LABELS, key)
    ? SCHEDULE_PARAMETER_LABELS[key]
    : key;
}

function renderScheduleDetails(entity) {
  const schedule = normalizeSchedule(entity);
  if (!schedule) return "";
  const stateLabel = schedule.enabled === true
    ? "사용"
    : schedule.enabled === false ? "사용 안 함" : "상태 미확인";
  const rows = schedule.entries.map((entry) => {
    const parameters = entry.parameters.length
      ? `<div class="schedule-parameters">${entry.parameters.map((parameter) => (
        `<span>${escapeHtml(scheduleParameterLabel(parameter.key))} ${escapeHtml(parameter.value)}</span>`
      )).join("")}</div>`
      : "";
    return `<li>
      <span class="schedule-slot">슬롯 ${escapeHtml(entry.slot)}</span>
      <time>${escapeHtml(entry.start)}–${escapeHtml(entry.end)}</time>
      <strong>${escapeHtml(scheduleModeLabel(entry.mode))}</strong>
      <small>코드 ${escapeHtml(entry.modeCode)}</small>
      ${parameters}
    </li>`;
  }).join("") || `<p class="schedule-empty">유효한 시간 구간이 없습니다.</p>`;
  const invalid = schedule.invalidSlots.length
    ? `<p class="schedule-warning">해석 실패 슬롯: ${schedule.invalidSlots.map((slot) => (
      escapeHtml(slot)
    )).join(", ")}</p>`
    : "";
  return `<details class="device-schedule">
    <summary><span><ha-icon icon="mdi:calendar-clock"></ha-icon>장비 시간표</span><small>${stateLabel} · ${escapeHtml(schedule.entries.length)}개</small></summary>
    <div class="schedule-body">${rows.startsWith("<li>") ? `<ol>${rows}</ol>` : rows}${invalid}</div>
  </details>`;
}

function numericState(entity, fallback = 0) {
  const value = Number(entity?.state);
  return Number.isFinite(value) ? value : fallback;
}

function isUsableEntity(reference) {
  const state = isPlainObject(reference?.state) ? reference.state : reference;
  return Boolean(
    state
    && typeof state.state === "string"
    && state.state !== "unavailable",
  );
}

function formatTimestamp(value) {
  if (!value) return "—";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(timestamp);
}

function normalizeCardConfig(config, fields) {
  const normalized = { ...config };
  fields.forEach((field) => {
    if (config[field] === undefined) return;
    if (typeof config[field] !== "string" || !config[field].trim()) {
      throw new Error(`${field} must be a non-empty string`);
    }
    normalized[field] = config[field].trim();
  });
  if (normalized.topic_prefix) {
    normalized.topic_prefix = normalized.topic_prefix.replace(/^\/+|\/+$/g, "");
    if (!normalized.topic_prefix) throw new Error("topic_prefix must be a non-empty string");
  }
  return normalized;
}

class JebaoFlowCard extends HTMLElement {
  setConfig(config) {
    this._config = normalizeCardConfig(config, ["group", "instance", "topic_prefix", "entry"]);
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  static getStubConfig() {
    return { group: "main_flow", title: "메인 수류" };
  }

  _entities() {
    if (!this._hass) return {};
    const candidates = Object.entries(this._hass.states).filter(([, state]) => {
      const groupId = state.attributes?.jebao_flow_group_id;
      const instanceId = state.attributes?.jebao_flow_instance_id;
      const entryId = state.attributes?.jebao_flow_entry_id;
      const topicPrefix = state.attributes?.jebao_flow_topic_prefix;
      return groupId
        && instanceId
        && entryId
        && (!this._config?.group || groupId === this._config.group)
        && (!this._config?.instance || instanceId === this._config.instance)
        && (!this._config?.topic_prefix || topicPrefix === this._config.topic_prefix)
        && (!this._config?.entry || entryId === this._config.entry);
    });
    if (!candidates.length) return {};
    const entryIds = new Set(
      candidates.map(([, state]) => state.attributes.jebao_flow_entry_id),
    );
    if (entryIds.size !== 1) return { _ambiguous: true };
    const selectedEntry = [...entryIds][0];
    const selectedGroup = this._config?.group || candidates[0][1].attributes.jebao_flow_group_id;
    return Object.fromEntries(
      candidates
        .filter(([, state]) => state.attributes.jebao_flow_group_id === selectedGroup
          && state.attributes.jebao_flow_entry_id === selectedEntry)
        .map(([entityId, state]) => [
          state.attributes.jebao_flow_control,
          { entityId, state },
        ]),
    );
  }

  _deviceEntities(entryId) {
    if (!this._hass) return {};
    const devices = {};
    Object.entries(this._hass.states).forEach(([entityId, state]) => {
      const deviceId = state.attributes?.jebao_flow_device_id;
      const control = state.attributes?.jebao_flow_control;
      if (!deviceId || !control || state.attributes?.jebao_flow_entry_id !== entryId) return;
      devices[deviceId] = devices[deviceId] || {};
      devices[deviceId][control] = { entityId, state };
    });
    return devices;
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    const entities = this._entities();
    if (entities._ambiguous) {
      this.shadowRoot.innerHTML = `${this._styles()}<ha-card><div class="empty">
        <ha-icon icon="mdi:alert-circle-outline"></ha-icon><strong>인스턴스 선택 필요</strong>
        <span>같은 ID를 쓰는 데몬이 여러 개입니다.</span>
        <small>카드 설정에 topic_prefix를 지정하세요.</small>
      </div></ha-card>`;
      return;
    }
    const statusEntity = entities.status?.state;
    if (!statusEntity) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          <div class="empty">
            <ha-icon icon="mdi:waves-arrow-right"></ha-icon>
            <strong>Jebao Flow Engine</strong>
            <span>그룹 상태를 기다리는 중입니다.</span>
            <small>통합의 MQTT 토픽 접두사와 jebao-flowd 연결을 확인하세요.</small>
          </div>
        </ha-card>`;
      return;
    }

    const entryId = statusEntity.attributes.jebao_flow_entry_id;
    const deviceEntities = this._deviceEntities(entryId);

    const status = statusEntity.state;
    const members = statusEntity.attributes.members || {};
    const locked = Boolean(statusEntity.attributes.hardware_writes_locked);
    const observerMode = statusEntity.attributes.jebao_flow_runtime_mode === "observer";
    const executionStrategy = statusEntity.attributes.execution_strategy || "software_independent";
    const nativePair = isPlainObject(statusEntity.attributes.native_pair)
      ? statusEntity.attributes.native_pair
      : null;
    const available = entities.availability?.state?.state === "on";
    const canUseGroupControl = (control) => (
      available && !locked && isUsableEntity(entities[control])
    );
    const canTogglePower = canUseGroupControl("enabled");
    const canSetPower = canUseGroupControl("power");
    const canSelectPattern = canUseGroupControl("pattern");
    const desiredEnabled = entities.enabled?.state?.state === "on";
    const actualEnabled = statusEntity.attributes.actual_enabled;
    const enabled = observerMode || !canTogglePower
      ? actualEnabled === true
      : desiredEnabled;
    const power = numericState(entities.power?.state, 0);
    const minPower = numericState(entities.min_power?.state, 0);
    const maxPower = numericState(entities.max_power?.state, 100);
    const period = numericState(entities.period?.state, 10);
    const transition = numericState(entities.transition?.state, 0);
    const selectedPattern = entities.pattern?.state?.state || "constant";
    const patternOptions = entities.pattern?.state?.attributes.options || Object.keys(PATTERNS);
    const title = this._config.title || statusEntity.attributes.friendly_name?.replace(/ 상태$/, "") || "메인 수류";
    const tuningControls = [
      ["min_power", "최소 출력", minPower, 0, 100, 1, "%"],
      ["max_power", "최대 출력", maxPower, 0, 100, 1, "%"],
      ["period", "패턴 주기", period, 1, 3600, 1, "초"],
      ["transition", "전환 시간", transition, 0, 600, 1, "초"],
    ].filter(([control]) => canUseGroupControl(control));
    const actionDefinitions = [
      ["start_feed", "feed", "mdi:fishbowl-outline", "급여 시작"],
      ["stop_feed", "", "mdi:play-circle-outline", "급여 종료"],
      ["resume_all_members", "", "mdi:source-merge", "전체 그룹 복귀"],
      status === "emergency_stop"
        ? ["clear_emergency", "warning", "mdi:lock-open-check-outline", "잠금 해제"]
        : ["emergency_stop", "danger", "mdi:alert-octagon", "비상 정지"],
    ].filter(([control]) => canUseGroupControl(control));

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card class="flow-card ${enabled ? "is-running" : ""}">
        <div class="header">
          <div class="title-block">
            <span class="eyebrow">JEBAO FLOW ENGINE</span>
            <h2>${escapeHtml(title)}</h2>
            <div class="status-line">
              <span class="status-dot ${available ? "online" : "offline"}"></span>
              <span>${escapeHtml(available ? (STATUS_LABELS[status] || status) : "서버 연결 끊김")}</span>
              <span class="divider">·</span>
              <span>${escapeHtml(observerMode ? "외부 또는 장비 스케줄" : (PATTERNS[selectedPattern]?.[0] || selectedPattern))}</span>
              <span class="divider">·</span>
              <span>${escapeHtml(EXECUTION_STRATEGY_LABELS[executionStrategy] || executionStrategy)}</span>
            </div>
          </div>
          ${observerMode ? `<span class="observer-chip"><ha-icon icon="mdi:eye-outline"></ha-icon>LAN 관찰</span>` : canTogglePower ? `<button class="power-button ${enabled ? "on" : ""}" data-toggle-power
            aria-label="그룹 운전 전환">
            <ha-icon icon="mdi:power"></ha-icon>
          </button>` : `<span class="observer-chip"><ha-icon icon="mdi:lock-outline"></ha-icon>그룹 제어 잠금</span>`}
        </div>

        ${locked ? `
          <div class="safety-banner">
            <ha-icon icon="mdi:shield-lock-outline"></ha-icon>
            <div><strong>${observerMode ? "읽기 전용 관찰 모드" : "하드웨어 쓰기 잠금"}</strong><span>${observerMode ? "기존 앱·장비 스케줄의 결과만 읽으며 제어 명령은 모두 거부합니다." : "화면과 패턴 계산만 동작하며 실제 펌프에는 명령을 보내지 않습니다."}</span></div>
          </div>` : ""}

        ${executionStrategy === "native_linked" ? `
          <div class="safety-banner native-linkage-banner">
            <ha-icon icon="mdi:lan-pending"></ha-icon>
            <div><strong>네이티브 Linkage 실험 기능 잠금</strong><span>${escapeHtml(nativePair?.relation || "unknown")} 관계는 현장 qualification 전까지 제어할 수 없으며, slave 개별 출력은 지원되는 것으로 간주하지 않습니다.</span></div>
          </div>` : ""}

        ${observerMode ? `<section class="observer-summary">
          <div><span>연결된 펌프</span><strong>${Number(statusEntity.attributes.online_member_count || 0)} / ${Number(statusEntity.attributes.member_count || 0)}</strong></div>
          <div><span>마지막 실제 변화</span><strong>${escapeHtml(formatTimestamp(statusEntity.attributes.last_changed_at))}</strong></div>
          <div><span>마지막 설정 단서 변화</span><strong>${escapeHtml(formatTimestamp(statusEntity.attributes.last_configuration_changed_at))}</strong></div>
        </section>` : canSetPower ? `<section class="hero-power">
          <div class="power-readout">
            <span>기준 출력</span>
            <strong data-power-readout>${Math.round(power)}<small>%</small></strong>
          </div>
          ${this._slider("power", power, minPower, maxPower, 1, "%", true)}
          <div class="range-labels"><span>MIN ${Math.round(minPower)}%</span><span>MAX ${Math.round(maxPower)}%</span></div>
        </section>` : ""}

        ${observerMode || !canSelectPattern || !patternOptions.length ? "" : `<section>
          <div class="section-title"><span>FLOW MODE</span><small>빠른 파형은 펌프 내장 모드, 긴 흐름은 서버 패턴으로 제어합니다.</small></div>
          <div class="mode-grid">
            ${patternOptions.map((pattern) => {
              const info = PATTERNS[pattern] || [pattern, "mdi:waves"];
              return `<button class="mode ${pattern === selectedPattern ? "selected" : ""}" data-pattern="${escapeHtml(pattern)}">
                <ha-icon icon="${escapeHtml(info[1])}"></ha-icon><span>${escapeHtml(info[0])}</span>
              </button>`;
            }).join("")}
          </div>
        </section>`}

        <section>
          <div class="section-title"><span>THREE-PUMP FLOW</span><small>${executionStrategy === "native_linked" ? "동일 Pro 두 대는 네이티브 페어, 바형 펌프는 독립 보조로 표현합니다." : observerMode ? "두 메인 펌프와 바형 크로스플로우의 실제 LAN 관찰값입니다." : "세 펌프를 독립 제어하며 gain/phase를 적용한 현재 계산값입니다."}</small></div>
          <div class="pump-grid">${this._memberCards(members, deviceEntities, observerMode)}</div>
        </section>

        ${observerMode || !tuningControls.length ? "" : `<section class="tuning">
          <div class="section-title"><span>FINE TUNING</span></div>
          <div class="tuning-grid">
            ${tuningControls.map((control) => this._compactSlider(...control)).join("")}
          </div>
        </section>`}

        ${observerMode || !actionDefinitions.length ? "" : `<div class="actions">
          ${actionDefinitions.map(([control, style, icon, label]) => (
            `<button class="action ${style}" data-button-control="${control}"><ha-icon icon="${icon}"></ha-icon>${label}</button>`
          )).join("")}
        </div>`}
      </ha-card>`;

    this._attachHandlers(entities, deviceEntities);
  }

  _memberCards(members, deviceEntities, observerMode) {
    const entries = Object.entries(members).sort(([, a], [, b]) => {
      const order = { left: 0, right: 1, crossflow: 2, support: 3 };
      return (order[a.role] ?? 9) - (order[b.role] ?? 9);
    });
    if (!entries.length) return `<div class="no-members">펌프 계산 상태를 기다리는 중입니다.</div>`;
    return entries.map(([deviceId, member]) => {
      const target = Number(member.target_power ?? 0);
      const statusAttributes = deviceEntities[deviceId]?.device_status?.state?.attributes || {};
      const actual = statusAttributes.actual_power ?? member.actual_power;
      const online = statusAttributes.online ?? member.online;
      const actualEnabled = statusAttributes.actual_enabled ?? member.actual_enabled;
      const actualMode = statusAttributes.actual_mode ?? member.actual_mode;
      const lastSeen = statusAttributes.last_seen_at ?? member.last_seen_at;
      const observedAttributes = statusAttributes.observed_attributes || member.observed_attributes || {};
      const error = statusAttributes.error ?? member.error;
      const scheduleState = deviceEntities[deviceId]?.schedule?.state;
      const displayPower = observerMode ? Number(actual ?? 0) : target;
      const manual = member.control_mode === "manual_override";
      const actuation = member.actuation || "software_independent";
      const individualControls = Array.isArray(member.individual_controls)
        ? member.individual_controls
        : ["enabled", "power", "manual_override"];
      const nativeFollower = actuation === "native_sync_slave" || actuation === "native_async_slave";
      const devicePowerEntity = deviceEntities[deviceId]?.device_power;
      const deviceEnabledEntity = deviceEntities[deviceId]?.device_enabled;
      const resumeGroupEntity = deviceEntities[deviceId]?.resume_group;
      const devicePower = devicePowerEntity?.state;
      const deviceEnabled = deviceEnabledEntity?.state;
      const minimum = Number(devicePower?.attributes.min ?? 0);
      const maximum = Number(devicePower?.attributes.max ?? 100);
      return `<article class="pump ${escapeHtml(member.role)}">
        <div class="pump-head">
          <ha-icon icon="${member.role === "crossflow" ? "mdi:arrow-expand-horizontal" : "mdi:fan"}"></ha-icon>
          <div><strong>${escapeHtml(member.name || deviceId)}</strong><span>${escapeHtml(ROLE_LABELS[member.role] || member.role)}</span></div>
          ${!observerMode && isUsableEntity(deviceEnabledEntity) && individualControls.includes("enabled") ? `<button class="member-power ${deviceEnabled.state === "on" ? "on" : ""}" data-device-toggle="${escapeHtml(deviceId)}" aria-label="${escapeHtml(member.name || deviceId)} 개별 전원"><ha-icon icon="mdi:power"></ha-icon></button>`
            : `<span class="member-dot ${online === false ? "offline" : online === true ? "online" : "unknown"}"></span>`}
        </div>
        <div class="pump-output"><strong>${actual == null && observerMode ? "—" : `${Math.round(displayPower)}%`}</strong><span>${nativeFollower ? "장비 보고 Flow" : observerMode ? "장비 보고 출력" : "목표 출력"}</span></div>
        <div class="meter"><i style="width:${Math.max(0, Math.min(100, displayPower))}%"></i></div>
        ${!observerMode && isUsableEntity(devicePowerEntity) && individualControls.includes("power") ? `<div class="individual-control">
          <span>${manual ? "개별 제어 중" : "개별 출력 조정"}</span>
          <input type="range" min="${minimum}" max="${maximum}" step="1" value="${numericState(devicePower, target)}" data-device-power="${escapeHtml(deviceId)}">
          ${manual && isUsableEntity(resumeGroupEntity)
            ? `<button data-resume-device="${escapeHtml(deviceId)}">그룹 복귀</button>`
            : ""}
        </div>` : ""}
        <div class="pump-meta">${actuation === "software_independent" ? `
          <span>GAIN ${Number(member.gain ?? 1).toFixed(2)}</span>
          <span>PHASE ${Math.round(Number(member.phase ?? 0))}°</span>` : `
          <span>${escapeHtml(ACTUATION_LABELS[actuation] || actuation)}</span>
          <span>개별 제어 잠금</span>`}
          <span>REPORTED ${actual == null ? "—" : `${Math.round(Number(actual))}%`}</span>
        </div>
        ${observerMode ? `<div class="observation-meta">
          <span>${online === true ? (actualEnabled === true ? "운전 중" : "정지") : online === false ? "연결 끊김" : "매핑 대기"}</span>
          <span>MODE ${escapeHtml(actualMode || "—")}</span>
          <span>TIMER ${observedAttributes.TimerON === true ? "ON" : observedAttributes.TimerON === false ? "OFF" : "—"}</span>
          <div class="observed-settings">${this._observedSettings(observedAttributes)}</div>
          <small>확인 ${escapeHtml(formatTimestamp(lastSeen))}</small>
          ${error ? `<small class="error">${escapeHtml(error)}</small>` : ""}
        </div>` : ""}
        ${renderScheduleDetails(scheduleState)}
      </article>`;
    }).join("");
  }

  _observedSettings(attributes) {
    const labels = {
      AutoMode: "AUTO MODE",
      AutoFlow: "AUTO FLOW",
      AutoFreq: "AUTO FREQ",
      FeedSwitch: "FEED",
      Linkage: "LINK",
      PulseTide: "PULSE/TIDE",
      AutoGears: "AUTO GEAR",
    };
    const values = Object.entries(labels)
      .filter(([key]) => Object.hasOwn(attributes, key))
      .slice(0, 5)
      .map(([key, label]) => `<span>${label} ${escapeHtml(attributes[key])}</span>`);
    return values.join("") || "<span>추가 설정 단서 —</span>";
  }

  _slider(control, value, min, max, step, unit, hero = false) {
    return `<input class="slider ${hero ? "hero" : ""}" type="range" data-number-control="${control}"
      min="${min}" max="${max}" step="${step}" value="${value}" aria-label="${control}" data-unit="${unit}">`;
  }

  _compactSlider(control, label, value, min, max, step, unit) {
    return `<label class="compact-control">
      <span>${label}<strong data-value-for="${control}">${Math.round(value)}${unit}</strong></span>
      ${this._slider(control, value, min, max, step, unit)}
    </label>`;
  }

  _attachHandlers(entities, deviceEntities) {
    this.shadowRoot.querySelector("[data-toggle-power]")?.addEventListener("click", () => {
      const target = entities.enabled;
      if (!isUsableEntity(target)) return;
      this._hass.callService("switch", target.state.state === "on" ? "turn_off" : "turn_on", {
        entity_id: target.entityId,
      });
    });

    this.shadowRoot.querySelectorAll("[data-pattern]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!isUsableEntity(entities.pattern)) return;
        this._hass.callService("select", "select_option", {
          entity_id: entities.pattern.entityId,
          option: button.dataset.pattern,
        });
      });
    });

    this.shadowRoot.querySelectorAll("[data-number-control]").forEach((slider) => {
      slider.addEventListener("input", () => {
        const value = Math.round(Number(slider.value));
        const control = slider.dataset.numberControl;
        const label = this.shadowRoot.querySelector(`[data-value-for="${control}"]`);
        if (label) label.textContent = `${value}${slider.dataset.unit}`;
        if (control === "power") {
          const readout = this.shadowRoot.querySelector("[data-power-readout]");
          if (readout) readout.innerHTML = `${value}<small>%</small>`;
        }
      });
      slider.addEventListener("change", () => {
        const target = entities[slider.dataset.numberControl];
        if (!isUsableEntity(target)) return;
        this._hass.callService("number", "set_value", {
          entity_id: target.entityId,
          value: Number(slider.value),
        });
      });
    });

    this.shadowRoot.querySelectorAll("[data-button-control]").forEach((button) => {
      button.addEventListener("click", () => {
        const control = button.dataset.buttonControl;
        const target = entities[control];
        if (!isUsableEntity(target)) return;
        if (control === "emergency_stop" && !window.confirm("메인 수류를 비상 정지할까요? 자동으로 해제되지 않습니다.")) return;
        this._hass.callService("button", "press", { entity_id: target.entityId });
      });
    });

    this.shadowRoot.querySelectorAll("[data-device-power]").forEach((slider) => {
      slider.addEventListener("change", () => {
        const target = deviceEntities[slider.dataset.devicePower]?.device_power;
        if (!isUsableEntity(target)) return;
        this._hass.callService("number", "set_value", {
          entity_id: target.entityId,
          value: Number(slider.value),
        });
      });
    });

    this.shadowRoot.querySelectorAll("[data-device-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = deviceEntities[button.dataset.deviceToggle]?.device_enabled;
        if (!isUsableEntity(target)) return;
        this._hass.callService("switch", target.state.state === "on" ? "turn_off" : "turn_on", {
          entity_id: target.entityId,
        });
      });
    });

    this.shadowRoot.querySelectorAll("[data-resume-device]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = deviceEntities[button.dataset.resumeDevice]?.resume_group;
        if (isUsableEntity(target)) {
          this._hass.callService("button", "press", { entity_id: target.entityId });
        }
      });
    });
  }

  _styles() {
    return `<style>
      :host { --flow-cyan:#55d9e8; --flow-blue:#246df5; --flow-navy:#07131f; --flow-panel:#102231; display:block; }
      * { box-sizing:border-box; }
      ha-card { overflow:hidden; padding:22px; color:var(--primary-text-color); background:
        radial-gradient(circle at 92% 0%, rgba(36,109,245,.24), transparent 36%),
        linear-gradient(145deg, rgba(7,19,31,.98), rgba(12,35,52,.96)); border:1px solid rgba(85,217,232,.18); }
      .header { display:flex; justify-content:space-between; align-items:center; gap:16px; }
      .eyebrow { color:var(--flow-cyan); font:700 10px/1.2 sans-serif; letter-spacing:.18em; }
      h2 { margin:5px 0 7px; color:#f5fbff; font-size:25px; }
      .status-line { display:flex; flex-wrap:wrap; align-items:center; gap:6px; color:#9fb4c3; font-size:12px; }
      .status-dot,.member-dot { width:8px; height:8px; border-radius:50%; background:#70818d; box-shadow:0 0 0 3px rgba(112,129,141,.12); }
      .status-dot.online,.member-dot.online { background:#4be3aa; box-shadow:0 0 10px rgba(75,227,170,.7); }
      .status-dot.offline,.member-dot.offline { background:#ff6477; }
      .member-dot.unknown { background:#687d8c; }
      .power-button { width:52px; height:52px; display:grid; place-items:center; border-radius:50%; border:1px solid #385267; color:#8299a8; background:#0a1824; cursor:pointer; }
      .power-button.on { color:#e9feff; border-color:var(--flow-cyan); background:linear-gradient(145deg,#14758a,#235bd0); box-shadow:0 0 24px rgba(85,217,232,.25); }
      .power-button ha-icon { width:27px; height:27px; }
      .observer-chip { display:flex; align-items:center; gap:6px; padding:8px 11px; color:#c9fbff; background:rgba(20,117,138,.25); border:1px solid rgba(85,217,232,.45); border-radius:999px; font-size:10px; }
      .observer-chip ha-icon { width:17px; }
      .safety-banner { display:flex; gap:11px; align-items:center; margin:18px 0 0; padding:11px 13px; color:#ffd989; background:rgba(255,169,44,.1); border:1px solid rgba(255,183,72,.35); border-radius:11px; }
      .safety-banner ha-icon { flex:0 0 auto; }
      .safety-banner div { display:flex; flex-direction:column; gap:2px; }
      .safety-banner strong { font-size:12px; }
      .safety-banner span { color:#d4b978; font-size:11px; }
      .native-linkage-banner { color:#c8b7ff; background:rgba(125,88,255,.1); border-color:rgba(151,120,255,.35); }
      .native-linkage-banner span { color:#aa9bd6; }
      section { margin-top:22px; }
      .observer-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
      .observer-summary div { display:flex; flex-direction:column; gap:5px; min-width:0; padding:13px; border-radius:11px; background:rgba(7,18,28,.62); border:1px solid rgba(148,199,220,.13); }
      .observer-summary span { color:#7892a3; font-size:9px; }
      .observer-summary strong { overflow:hidden; color:#e7f8ff; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
      .hero-power { padding:19px; border-radius:15px; background:rgba(7,18,28,.62); border:1px solid rgba(148,199,220,.13); }
      .power-readout { display:flex; justify-content:space-between; align-items:end; margin-bottom:9px; color:#a9bbc8; font-size:12px; }
      .power-readout strong { color:#f7fdff; font:700 36px/1 sans-serif; }
      .power-readout small { margin-left:2px; color:var(--flow-cyan); font-size:16px; }
      .slider { width:100%; margin:9px 0; accent-color:var(--flow-cyan); cursor:pointer; }
      .slider.hero { height:8px; }
      .range-labels { display:flex; justify-content:space-between; color:#688092; font:600 9px/1 sans-serif; letter-spacing:.08em; }
      .section-title { display:flex; justify-content:space-between; align-items:end; gap:12px; margin-bottom:10px; }
      .section-title > span { color:#dceaf2; font:700 11px/1 sans-serif; letter-spacing:.12em; }
      .section-title small { color:#6f8798; font-size:10px; text-align:right; }
      .mode-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
      .mode { min-height:68px; display:flex; flex-direction:column; justify-content:center; align-items:center; gap:6px; padding:8px 4px; color:#87a0b1; background:rgba(17,39,55,.76); border:1px solid rgba(130,172,196,.14); border-radius:11px; cursor:pointer; }
      .mode ha-icon { width:23px; height:23px; }
      .mode span { font-size:10px; }
      .mode.selected { color:#ecfeff; border-color:rgba(85,217,232,.75); background:linear-gradient(145deg,rgba(20,117,138,.72),rgba(35,91,208,.58)); box-shadow:inset 0 0 14px rgba(85,217,232,.1); }
      .pump-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }
      .pump { min-width:0; padding:13px; background:rgba(16,34,49,.88); border:1px solid rgba(120,168,194,.15); border-radius:13px; }
      .pump.crossflow { border-color:rgba(120,124,255,.35); background:linear-gradient(145deg,rgba(22,38,59,.92),rgba(32,31,74,.82)); }
      .pump-head { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:8px; align-items:center; }
      .pump-head ha-icon { width:22px; color:var(--flow-cyan); }
      .pump-head div { min-width:0; display:flex; flex-direction:column; }
      .pump-head strong { overflow:hidden; color:#e7f4fa; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
      .pump-head span { color:#728b9c; font-size:9px; }
      .member-power { width:27px; height:27px; display:grid; place-items:center; padding:0; border-radius:50%; color:#617a8b; background:#0c1d29; border:1px solid #2c4657; cursor:pointer; }
      .member-power.on { color:#eaffff; border-color:#55d9e8; background:#176477; }
      .member-power ha-icon { width:15px; height:15px; }
      .pump-output { display:flex; justify-content:space-between; align-items:end; margin:15px 0 6px; }
      .pump-output strong { color:#fff; font-size:23px; }
      .pump-output span { color:#6f8798; font-size:8px; }
      .meter { height:4px; overflow:hidden; border-radius:3px; background:#263d4c; }
      .meter i { display:block; height:100%; background:linear-gradient(90deg,var(--flow-blue),var(--flow-cyan)); }
      .individual-control { margin-top:10px; padding-top:9px; border-top:1px solid rgba(120,168,194,.12); }
      .individual-control span { display:block; margin-bottom:4px; color:#8ca4b4; font-size:8px; }
      .individual-control input { width:100%; accent-color:#8a7dff; }
      .individual-control button { width:100%; margin-top:5px; padding:5px; color:#dcd8ff; background:#302d62; border:1px solid #6660aa; border-radius:7px; font-size:8px; cursor:pointer; }
      .pump-meta { display:flex; justify-content:space-between; gap:4px; margin-top:9px; color:#698091; font:600 7px/1 sans-serif; }
      .observation-meta { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:5px; margin-top:10px; padding-top:9px; border-top:1px solid rgba(120,168,194,.12); color:#91a9b7; font-size:8px; }
      .observation-meta small { grid-column:1/-1; color:#637d8d; }
      .observation-meta .error { color:#ff8795; }
      .observed-settings { grid-column:1/-1; display:flex; flex-wrap:wrap; gap:4px; }
      .observed-settings span { padding:3px 5px; color:#9fb9c7; background:rgba(67,107,130,.18); border-radius:5px; }
      .device-schedule { margin-top:10px; padding-top:9px; border-top:1px solid rgba(120,168,194,.12); }
      .device-schedule summary { display:flex; justify-content:space-between; align-items:center; gap:7px; color:#a9bfcb; font-size:9px; cursor:pointer; }
      .device-schedule summary span { display:flex; align-items:center; gap:5px; }
      .device-schedule summary ha-icon { width:15px; height:15px; color:#73dbe6; }
      .device-schedule summary small { color:#718b9b; font-size:8px; }
      .schedule-body { margin-top:8px; }
      .schedule-body ol { display:grid; gap:6px; margin:0; padding:0; list-style:none; }
      .schedule-body li { display:grid; grid-template-columns:auto auto minmax(0,1fr) auto; align-items:center; gap:5px; padding:6px; color:#90a7b5; background:rgba(5,18,28,.42); border-radius:6px; font-size:8px; }
      .schedule-body li strong { overflow:hidden; color:#d7e9ef; font-size:8px; text-overflow:ellipsis; white-space:nowrap; }
      .schedule-body li small,.schedule-slot { color:#637d8d; font-size:7px; }
      .schedule-parameters { grid-column:1/-1; display:flex; flex-wrap:wrap; gap:3px; }
      .schedule-parameters span { padding:2px 4px; color:#a7c5cf; background:rgba(67,107,130,.2); border-radius:4px; }
      .schedule-empty,.schedule-warning { margin:5px 0 0; font-size:8px; }
      .schedule-empty { color:#6f8798; }
      .schedule-warning { color:#ffb27e; }
      .no-members { grid-column:1/-1; padding:24px; color:#708798; text-align:center; }
      .tuning-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 17px; }
      .compact-control { display:block; padding:10px 12px; border-radius:10px; background:rgba(12,29,42,.62); }
      .compact-control > span { display:flex; justify-content:space-between; color:#8da2b0; font-size:10px; }
      .compact-control strong { color:#d8e9f1; }
      .compact-control .slider { margin:9px 0 0; }
      .actions { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-top:22px; }
      .action { display:flex; align-items:center; justify-content:center; gap:6px; min-height:40px; padding:8px; color:#b4c6d1; background:#102738; border:1px solid #29465a; border-radius:10px; font-size:10px; cursor:pointer; }
      .action ha-icon { width:18px; }
      .action.feed { color:#dffff5; border-color:#267b69; background:#125346; }
      .action.warning { color:#fff1bf; border-color:#8c712b; background:#5e4815; }
      .action.danger { color:#ffd7db; border-color:#843846; background:#56212b; }
      .empty { min-height:190px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; padding:24px; color:#a4bac8; text-align:center; }
      .empty ha-icon { width:36px; height:36px; color:var(--flow-cyan); }
      .empty strong { color:#edfaff; }
      .empty small { color:#6f8798; }
      @media (max-width:700px) {
        ha-card { padding:16px; }
        .mode-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .pump-grid { grid-template-columns:1fr; }
        .observer-summary { grid-template-columns:1fr; }
        .tuning-grid { grid-template-columns:1fr; }
        .actions { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .section-title small { display:none; }
      }
    </style>`;
  }
}

class JebaoEquipmentCard extends HTMLElement {
  setConfig(config) {
    this._config = normalizeCardConfig(config, ["instance", "topic_prefix", "entry"]);
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 3; }

  _devices() {
    const discovered = {};
    const candidates = Object.entries(this._hass.states).filter(([, state]) => {
      const attributes = state.attributes || {};
      return attributes.jebao_flow_device_id
        && ["return_pump", "dosing_pump"].includes(attributes.jebao_flow_device_type)
        && attributes.jebao_flow_control
        && (!this._config.instance
          || attributes.jebao_flow_instance_id === this._config.instance)
        && (!this._config.topic_prefix
          || attributes.jebao_flow_topic_prefix === this._config.topic_prefix)
        && (!this._config.entry
          || attributes.jebao_flow_entry_id === this._config.entry);
    });
    const entryIds = new Set(
      candidates.map(([, state]) => state.attributes.jebao_flow_entry_id),
    );
    this._ambiguous = entryIds.size > 1;
    const selectedEntry = entryIds.size === 1 ? [...entryIds][0] : null;
    candidates.forEach(([entityId, state]) => {
      const attributes = state.attributes || {};
      const deviceId = attributes.jebao_flow_device_id;
      const type = attributes.jebao_flow_device_type;
      const control = attributes.jebao_flow_control;
      if (attributes.jebao_flow_entry_id !== selectedEntry) return;
      discovered[deviceId] = discovered[deviceId] || {
        name: attributes.friendly_name?.replace(/ (개별 운전|개별 출력|개별 상태|개별 연결|실제 출력|장비 보고 출력|장비 보고 Flow|실제 모드|실제 운전|장비 오류|장비 시간표)$/, "") || deviceId,
        type: type === "dosing_pump" ? "dosing" : "return",
        observerMode: false,
      };
      discovered[deviceId].observerMode ||= attributes.jebao_flow_runtime_mode === "observer";
      if (control === "device_enabled") discovered[deviceId].enabled = entityId;
      if (control === "device_power") discovered[deviceId].power = entityId;
      if (control === "device_status") discovered[deviceId].status = entityId;
      if (control === "actual_power") discovered[deviceId].actualPower = entityId;
      if (control === "device_availability") discovered[deviceId].availability = entityId;
      if (control === "schedule") discovered[deviceId].schedule = entityId;
    });
    return Object.values(discovered);
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const devices = this._devices();
    if (this._ambiguous) {
      this.shadowRoot.innerHTML = `<style>*{box-sizing:border-box}ha-card{padding:18px}</style>
        <ha-card><strong>인스턴스 선택 필요</strong><p>카드 설정에 topic_prefix를 지정하세요.</p></ha-card>`;
      return;
    }
    const rows = devices.map((device, index) => {
      const powerState = device.power ? this._hass.states[device.power] : null;
      const enabledState = device.enabled ? this._hass.states[device.enabled] : null;
      const statusState = device.status ? this._hass.states[device.status] : null;
      const actualPowerState = device.actualPower ? this._hass.states[device.actualPower] : null;
      const scheduleState = device.schedule ? this._hass.states[device.schedule] : null;
      const attributes = statusState?.attributes || {};
      const observerMode = attributes.jebao_flow_runtime_mode === "observer" || device.observerMode;
      const online = attributes.online === true;
      const actualEnabled = attributes.actual_enabled;
      const actualPower = attributes.actual_power ?? numericState(actualPowerState, null);
      const isDosing = device.type === "dosing";
      return `<article class="equipment-row">
        <ha-icon icon="${isDosing ? "mdi:test-tube" : "mdi:pump"}"></ha-icon>
        <div class="equipment-main"><strong>${escapeHtml(device.name || (isDosing ? "도징펌프" : "리턴펌프"))}</strong><span>${escapeHtml(observerMode ? (online ? (attributes.error ? "오류" : actualEnabled === true ? "운전 중" : "정지") : "연결 끊김") : (statusState?.state || (enabledState?.state === "on" ? "운전 중" : "정지")))}</span>${observerMode ? `<small>확인 ${escapeHtml(formatTimestamp(attributes.last_seen_at))}</small>` : ""}</div>
        ${observerMode && actualPower != null ? `<div class="actual-output"><b>${Math.round(Number(actualPower))}%</b><span>장비 보고 출력</span></div>` : ""}
        ${!observerMode && isUsableEntity(powerState) ? `<label><input type="range" min="0" max="100" step="1" value="${numericState(powerState)}" data-equipment-power="${index}"><b>${Math.round(numericState(powerState))}%</b></label>` : ""}
        ${!observerMode && isUsableEntity(enabledState) ? `<button class="toggle ${enabledState.state === "on" ? "on" : ""}" data-equipment-toggle="${index}"><ha-icon icon="mdi:power"></ha-icon></button>` : `<span class="member-dot ${online ? "online" : "offline"}"></span>`}
        ${renderScheduleDetails(scheduleState)}
      </article>`;
    }).join("") || `<div class="empty-equipment">리턴·도징 장비 상태를 기다리는 중입니다.</div>`;
    this.shadowRoot.innerHTML = `<style>
      *{box-sizing:border-box} ha-card{padding:18px;background:linear-gradient(145deg,#0b1b28,#102c3c);color:#e9f7fc}
      h3{margin:0 0 13px;font-size:16px}.equipment-row{display:grid;grid-template-columns:auto minmax(100px,1fr) minmax(100px,1.2fr) auto;align-items:center;gap:12px;padding:12px 0;border-top:1px solid rgba(130,180,205,.15)}
      .equipment-row>ha-icon{color:#55d9e8}.equipment-main{display:flex;flex-direction:column}.equipment-main strong{font-size:12px}.equipment-main span{color:#7892a3;font-size:10px}.equipment-main small{margin-top:3px;color:#60798a;font-size:8px}
      .actual-output{display:flex;align-items:baseline;justify-content:flex-end;gap:5px}.actual-output b{font-size:17px}.actual-output span{color:#7892a3;font-size:8px}.member-dot{width:8px;height:8px;border-radius:50%;background:#ff6477}.member-dot.online{background:#4be3aa;box-shadow:0 0 10px rgba(75,227,170,.7)}
      label{display:flex;align-items:center;gap:7px}input{min-width:0;width:100%;accent-color:#55d9e8}b{font-size:10px}.toggle{width:35px;height:35px;border-radius:50%;border:1px solid #345064;background:#102433;color:#718a9a}.toggle.on{color:white;border-color:#55d9e8;background:#1b7185}
      .device-schedule{grid-column:1/-1;padding:9px 0 2px;border-top:1px solid rgba(130,180,205,.12)}.device-schedule summary{display:flex;justify-content:space-between;align-items:center;gap:8px;color:#a9bfcb;font-size:10px;cursor:pointer}.device-schedule summary span{display:flex;align-items:center;gap:5px}.device-schedule summary ha-icon{width:16px;height:16px;color:#73dbe6}.device-schedule summary small{color:#718b9b;font-size:9px}.schedule-body{margin-top:8px}.schedule-body ol{display:grid;gap:6px;margin:0;padding:0;list-style:none}.schedule-body li{display:grid;grid-template-columns:auto auto minmax(0,1fr) auto;align-items:center;gap:7px;padding:7px;color:#90a7b5;background:rgba(5,18,28,.42);border-radius:6px;font-size:9px}.schedule-body li strong{overflow:hidden;color:#d7e9ef;text-overflow:ellipsis;white-space:nowrap}.schedule-body li small,.schedule-slot{color:#637d8d;font-size:8px}.schedule-parameters{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px}.schedule-parameters span{padding:2px 5px;color:#a7c5cf;background:rgba(67,107,130,.2);border-radius:4px}.schedule-empty,.schedule-warning{margin:5px 0 0;font-size:9px}.schedule-empty{color:#6f8798}.schedule-warning{color:#ffb27e}
      @media(max-width:600px){.equipment-row{grid-template-columns:auto 1fr auto}.equipment-row label{grid-column:2/4}}
    </style><ha-card><h3>${escapeHtml(this._config.title || "펌프 장비")}</h3>${rows}</ha-card>`;
    this.shadowRoot.querySelectorAll("[data-equipment-toggle]").forEach((button) => button.addEventListener("click", () => {
      const device = devices[Number(button.dataset.equipmentToggle)];
      const state = this._hass.states[device.enabled];
      if (!isUsableEntity(state)) return;
      this._hass.callService("switch", state.state === "on" ? "turn_off" : "turn_on", { entity_id: device.enabled });
    }));
    this.shadowRoot.querySelectorAll("[data-equipment-power]").forEach((slider) => slider.addEventListener("change", () => {
      const device = devices[Number(slider.dataset.equipmentPower)];
      if (!isUsableEntity(this._hass.states[device.power])) return;
      this._hass.callService("number", "set_value", { entity_id: device.power, value: Number(slider.value) });
    }));
  }
}

if (!customElements.get("jebao-flow-card")) customElements.define("jebao-flow-card", JebaoFlowCard);
if (!customElements.get("jebao-equipment-card")) customElements.define("jebao-equipment-card", JebaoEquipmentCard);

window.customCards = window.customCards || [];
[
  {
    type: "jebao-flow-card",
    name: "Jebao Flow Group",
    description: "Server-owned three-pump flow group controls",
    preview: true,
  },
  {
    type: "jebao-equipment-card",
    name: "Jebao Equipment",
    description: "Simple return and dosing pump controls backed by Home Assistant entities",
    preview: true,
  },
].forEach((metadata) => {
  if (!window.customCards.some((card) => card.type === metadata.type)) {
    window.customCards.push(metadata);
  }
});

console.info(`%c JEBAO-FLOW-CARD %c ${FLOW_CARD_VERSION} `, "color:white;background:#14758a;font-weight:bold", "color:#55d9e8;background:#07131f");
