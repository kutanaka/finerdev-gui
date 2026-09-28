// devgui frontend: builds the whole screen from GET /api/layout (see
// docs/design.md section 2) and keeps it live via the /ws WebSocket.
// No build step, no framework - see design.md section 2 for why.

const OPERATOR_TOKEN_KEY = "devgui_operator_token";

const state = {
  layout: null,
  ws: null,
  clientId: null,
  operatorToken: sessionStorage.getItem(OPERATOR_TOKEN_KEY),
  isHolder: false,
  outgoingRequestId: null, // our own pending takeover request, if any
  incomingRequestId: null, // a pending request we (the holder) must respond to
  incomingCountdownTimer: null,
};

// name -> { panel, badge, openBtn, closeBtn, errorEl, lastState, lastError }
const deviceElements = {};

// widget id -> { container, unit, maxRows }
const displayElements = {};

// widget id -> owning device name, for routing a failed call's error to
// its panel (design.md section 10: "該当パネルに直近のエラーを表示する").
const widgetOwner = {};

async function loadLayout() {
  const res = await fetch("/api/layout");
  state.layout = await res.json();
  if (state.layout.title) {
    document.getElementById("app-title").textContent = state.layout.title;
    document.title = state.layout.title;
  }
  renderTabs();
}

function renderTabs() {
  const tabBar = document.getElementById("tab-bar");
  const panels = document.getElementById("panels");
  tabBar.innerHTML = "";
  panels.innerHTML = "";

  state.layout.categories.forEach((category, index) => {
    const tabButton = document.createElement("button");
    tabButton.type = "button";
    tabButton.className = "tab-button";
    tabButton.textContent = category.title;
    tabButton.addEventListener("click", () => showCategory(index));
    tabBar.appendChild(tabButton);

    const grid = document.createElement("div");
    grid.className = "panel-grid";
    grid.id = `category-${index}`;
    grid.hidden = index !== 0;
    category.devices.forEach((device) => grid.appendChild(renderDevicePanel(device)));
    panels.appendChild(grid);
  });

  updateActiveTab(0);
}

function showCategory(index) {
  document.querySelectorAll(".panel-grid").forEach((el) => {
    el.hidden = true;
  });
  document.getElementById(`category-${index}`).hidden = false;
  updateActiveTab(index);
}

function updateActiveTab(activeIndex) {
  document.querySelectorAll(".tab-button").forEach((btn, i) => {
    btn.classList.toggle("active", i === activeIndex);
  });
}

function renderDevicePanel(device) {
  const panel = document.createElement("div");
  panel.className = "device-panel";
  panel.dataset.device = device.name;

  const header = document.createElement("div");
  header.className = "device-header";

  const nameEl = document.createElement("span");
  nameEl.className = "device-name";
  nameEl.textContent = device.name;
  header.appendChild(nameEl);

  const badge = document.createElement("span");
  badge.className = "state-badge";
  header.appendChild(badge);

  let openBtn = null;
  let closeBtn = null;

  if (device.can_open) {
    openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.textContent = "接続";
    openBtn.addEventListener("click", () => callDeviceAction(device.name, "open"));
    header.appendChild(openBtn);
  }

  if (device.can_close) {
    closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.textContent = "切断";
    closeBtn.addEventListener("click", () => callDeviceAction(device.name, "close"));
    header.appendChild(closeBtn);
  }

  panel.appendChild(header);

  const errorEl = document.createElement("div");
  errorEl.className = "device-error";
  errorEl.hidden = true;
  panel.appendChild(errorEl);

  const body = document.createElement("div");
  body.className = "device-body";
  device.widgets.forEach((widget) => {
    widgetOwner[widget.id] = device.name;
    body.appendChild(renderWidget(widget));
  });
  panel.appendChild(body);

  deviceElements[device.name] = { panel, badge, openBtn, closeBtn, errorEl };
  applyDeviceState(device.name, device.state, device.error_message);

  return panel;
}

function stateLabel(stateValue) {
  return (
    {
      not_installed: "未実装",
      disconnected: "未接続",
      connecting: "接続中...",
      connected: "接続済み",
      disconnecting: "切断中...",
      error: "エラー",
    }[stateValue] || stateValue
  );
}

function applyDeviceState(name, stateValue, errorMessage) {
  const el = deviceElements[name];
  if (!el) return;

  el.lastState = stateValue;
  el.lastError = errorMessage;

  el.badge.textContent = stateLabel(stateValue);
  el.badge.className = `state-badge state-${stateValue}`;

  el.errorEl.hidden = !errorMessage;
  el.errorEl.textContent = errorMessage || "";

  updateDeviceControls(name);
}

// Operant widgets and the connect/disconnect buttons require BOTH the
// device to be connected (or, for open, disconnected/error) AND this
// browser to hold the operator right (design.md section 9.1) - the
// server enforces this for real; disabling here is only a convenience.
function updateDeviceControls(name) {
  const el = deviceElements[name];
  if (!el) return;

  const stateValue = el.lastState;
  if (el.openBtn) {
    el.openBtn.disabled = !(
      state.isHolder &&
      (stateValue === "disconnected" || stateValue === "error")
    );
  }
  if (el.closeBtn) {
    el.closeBtn.disabled = !(state.isHolder && stateValue === "connected");
  }
  setOperantWidgetsEnabled(el.panel, state.isHolder && stateValue === "connected");
}

function refreshAllDeviceControls() {
  Object.keys(deviceElements).forEach(updateDeviceControls);
}

function setOperantWidgetsEnabled(panel, enabled) {
  panel.querySelectorAll('[data-operant="true"]').forEach((el) => {
    el.disabled = !enabled;
  });
}

function renderWidget(widget) {
  const wrap = document.createElement("div");
  wrap.className = "widget";

  if (widget.type !== "Button") {
    const label = document.createElement("label");
    label.textContent = widget.unit ? `${widget.label} [${widget.unit}]` : widget.label;
    wrap.appendChild(label);
  }

  switch (widget.type) {
    case "Button": {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = widget.label;
      btn.dataset.operant = "true";
      btn.addEventListener("click", () => {
        if (widget.confirm && !window.confirm(`${widget.label} を実行しますか?`)) return;
        callWidget(widget.id, undefined);
      });
      wrap.appendChild(btn);
      break;
    }
    case "NumberInput": {
      const input = document.createElement("input");
      input.type = "number";
      if (widget.step != null) input.step = widget.step;
      if (widget.min != null) input.min = widget.min;
      if (widget.max != null) input.max = widget.max;
      if (widget.default != null) input.value = widget.default;
      input.dataset.operant = "true";

      const setBtn = document.createElement("button");
      setBtn.type = "button";
      setBtn.textContent = "設定";
      setBtn.dataset.operant = "true";
      setBtn.addEventListener("click", () => callWidget(widget.id, Number(input.value)));

      wrap.appendChild(input);
      wrap.appendChild(setBtn);
      break;
    }
    case "Toggle": {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!widget.default;
      input.dataset.operant = "true";
      input.addEventListener("change", () => callWidget(widget.id, input.checked));
      wrap.appendChild(input);
      break;
    }
    case "Select": {
      const select = document.createElement("select");
      select.dataset.operant = "true";
      widget.options.forEach((optionKey) => {
        const optionEl = document.createElement("option");
        optionEl.value = optionKey;
        optionEl.textContent = optionKey;
        select.appendChild(optionEl);
      });
      select.addEventListener("change", () => callWidget(widget.id, select.value));
      wrap.appendChild(select);
      break;
    }
    case "TextInput": {
      const input = document.createElement("input");
      input.type = "text";
      input.value = widget.default || "";
      input.dataset.operant = "true";

      const sendBtn = document.createElement("button");
      sendBtn.type = "button";
      sendBtn.textContent = "送信";
      sendBtn.dataset.operant = "true";
      sendBtn.addEventListener("click", () => callWidget(widget.id, input.value));

      wrap.appendChild(input);
      wrap.appendChild(sendBtn);
      break;
    }
    case "Display": {
      const log = document.createElement("div");
      log.className = "display-log";
      log.style.setProperty("--visible-rows", widget.visible_rows);
      wrap.appendChild(log);
      displayElements[widget.id] = {
        container: log,
        unit: widget.unit,
        maxRows: widget.max_rows,
      };
      break;
    }
    default:
      break;
  }

  return wrap;
}

function operatorRequestHeaders(extra) {
  const headers = { "X-Client-Id": state.clientId || "" };
  if (state.operatorToken) headers["X-Operator-Token"] = state.operatorToken;
  return Object.assign(headers, extra || {});
}

// A 403 means the server no longer considers us the holder (never
// acquired, idle-timed-out, or lost after a disconnect past the grace
// period) - the UI was only ever a convenience, so resync it now. This
// only fixes up what we know for certain locally (we are not the holder);
// the holder-name text is left as-is and will self-correct from the
// operator_state broadcast that every such change also triggers.
function handleOperatorRejection() {
  if (state.isHolder) {
    showToast("操作権が失われました", true);
  }
  clearOperatorTokenLocal();
}

async function callWidget(widgetId, value) {
  try {
    const res = await fetch(`/api/call/${encodeURIComponent(widgetId)}`, {
      method: "POST",
      headers: operatorRequestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ value }),
    });
    if (res.status === 403) {
      handleOperatorRejection();
      return;
    }
    const body = await res.json();
    if (!body.ok) {
      showToast(`エラー: ${body.error}`, true);
      showPanelError(widgetOwner[widgetId], body.error);
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

function showPanelError(deviceName, message) {
  const el = deviceElements[deviceName];
  if (!el) return;
  el.errorEl.hidden = false;
  el.errorEl.textContent = message;
}

async function callDeviceAction(name, action) {
  try {
    const res = await fetch(`/api/devices/${encodeURIComponent(name)}/${action}`, {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
    if (res.status === 403) {
      handleOperatorRejection();
      return;
    }
    const body = await res.json();
    if (!body.ok) {
      showToast(`エラー: ${body.error}`, true);
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

// Used when we don't authoritatively know the global state (e.g. a failed
// reclaim attempt): fixes up only what's true locally (we are not the
// holder) and leaves the status text for the next operator_state broadcast
// to correct, rather than guessing "free".
function clearOperatorTokenLocal() {
  state.operatorToken = null;
  state.isHolder = false;
  sessionStorage.removeItem(OPERATOR_TOKEN_KEY);
  document.getElementById("operator-release-btn").hidden = true;
  refreshAllDeviceControls();
}

// Used when we just authoritatively learned the true state from our own
// request's response (acquire/reclaim/release), so the status text and
// button visibility can be updated immediately instead of waiting for the
// operator_state broadcast this same action triggers (avoids a race where
// that broadcast could otherwise arrive and be applied before state.isHolder
// is set, showing the holder name without "(自分)").
function saveOperatorToken(token, displayName) {
  state.operatorToken = token;
  state.isHolder = true;
  sessionStorage.setItem(OPERATOR_TOKEN_KEY, token);
  applyOperatorInfo({ is_held: true, holder_display_name: displayName });
}

async function acquireOperator() {
  try {
    const res = await fetch("/api/operator/acquire", {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showToast(`操作権を取得できません: ${body.detail || res.status}`, true);
      return;
    }
    const body = await res.json();
    saveOperatorToken(body.token, body.display_name);
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

async function releaseOperator() {
  if (!state.operatorToken) return;
  try {
    await fetch("/api/operator/release", {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  } finally {
    state.operatorToken = null;
    state.isHolder = false;
    sessionStorage.removeItem(OPERATOR_TOKEN_KEY);
    applyOperatorInfo({ is_held: false, holder_display_name: null });
  }
}

// After a page reload the WebSocket gets a brand new client_id, but the
// browser may still hold a valid operator token from before (sessionStorage
// survives the reload). Presenting it re-associates the new client_id with
// the same token within the disconnect grace period (design.md section 9.2).
async function attemptReclaimOperator() {
  if (!state.operatorToken) return;
  try {
    const res = await fetch("/api/operator/acquire", {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
    if (res.ok) {
      const body = await res.json();
      saveOperatorToken(body.token, body.display_name);
    } else {
      clearOperatorTokenLocal();
    }
  } catch (err) {
    // Leave the stored token as-is; a later action will retry via 403 handling.
  }
}

function applyOperatorInfo(info) {
  const statusEl = document.getElementById("operator-status");
  const acquireBtn = document.getElementById("operator-acquire-btn");
  const requestBtn = document.getElementById("operator-request-btn");
  const releaseBtn = document.getElementById("operator-release-btn");

  if (!info.is_held) {
    statusEl.textContent = "操作権: 空き";
  } else if (state.isHolder) {
    statusEl.textContent = `操作権: ${info.holder_display_name} (自分)`;
  } else {
    statusEl.textContent = `操作権: ${info.holder_display_name}`;
  }

  acquireBtn.hidden = info.is_held;
  requestBtn.hidden = !info.is_held || state.isHolder;
  releaseBtn.hidden = !state.isHolder;

  // request_pending is only meaningful once we know about it (snapshot and
  // operator_state both always carry it); when it's absent (a locally
  // synthesized info object from our own acquire/release), leave any open
  // dialog alone - the next broadcast will settle it.
  if (info.request_pending === false) {
    if (state.incomingRequestId) closeIncomingTakeoverDialog();
    if (state.outgoingRequestId) hideWaitingDialog();
  }

  refreshAllDeviceControls();
}

function showToast(message, isError) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = isError ? "toast toast-error" : "toast";
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

function setWsStatus(connected) {
  const el = document.getElementById("ws-status");
  el.textContent = connected ? "接続中" : "切断";
  el.className = connected ? "ws-status ws-connected" : "ws-status ws-disconnected";
}

const RECONNECT_INITIAL_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 30000;
let reconnectDelayMs = RECONNECT_INITIAL_DELAY_MS;

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.addEventListener("open", () => {
    setWsStatus(true);
    reconnectDelayMs = RECONNECT_INITIAL_DELAY_MS; // reset backoff once we're back
  });
  ws.addEventListener("close", () => {
    setWsStatus(false);
    // Reconnecting re-sends hello + snapshot, which restores all state
    // (design.md section 11): device states, Display logs, operator status.
    setTimeout(connectWebSocket, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, RECONNECT_MAX_DELAY_MS);
  });
  ws.addEventListener("error", () => setWsStatus(false));
  ws.addEventListener("message", (event) => {
    handleWsMessage(JSON.parse(event.data));
  });
}

function formatClock(isoString) {
  const d = new Date(isoString);
  const pad = (n, len = 2) => String(n).padStart(len, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

function buildDisplayRow(entry, unit) {
  const row = document.createElement("div");
  row.className = entry.error ? "display-row display-row-error" : "display-row";

  const time = document.createElement("span");
  time.className = "display-time";
  time.textContent = formatClock(entry.t);

  const value = document.createElement("span");
  value.className = "display-value";
  value.textContent = unit && !entry.error ? `${entry.value} ${unit}` : entry.value;

  row.appendChild(time);
  row.appendChild(value);
  return row;
}

// New rows are prepended (newest on top, design.md section 8). While the
// user has scrolled away from the top to read older rows, inserting a new
// row above must not change which rows are visible; only when already at
// the top should the view keep following the newest row.
function appendDisplayEntry(widgetId, entry) {
  const el = displayElements[widgetId];
  if (!el) return;

  const atTop = el.container.scrollTop <= 2;
  const prevScrollHeight = el.container.scrollHeight;

  const row = buildDisplayRow(entry, el.unit);
  el.container.insertBefore(row, el.container.firstChild);

  while (el.container.children.length > el.maxRows) {
    el.container.removeChild(el.container.lastChild);
  }

  if (atTop) {
    el.container.scrollTop = 0;
  } else {
    el.container.scrollTop += el.container.scrollHeight - prevScrollHeight;
  }
}

// --- forced takeover (design.md section 9.4) ---

async function requestTakeover() {
  try {
    const res = await fetch("/api/operator/request", {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(`要求できません: ${body.detail || res.status}`, true);
      return;
    }
    if (body.granted_immediately) {
      saveOperatorToken(body.token, body.display_name);
    } else {
      state.outgoingRequestId = body.request_id;
      document.getElementById("takeover-waiting-dialog").hidden = false;
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

function hideWaitingDialog() {
  state.outgoingRequestId = null;
  document.getElementById("takeover-waiting-dialog").hidden = true;
}

async function cancelTakeoverRequest() {
  const requestId = state.outgoingRequestId;
  if (!requestId) return;
  hideWaitingDialog();
  try {
    await fetch(`/api/operator/request/${encodeURIComponent(requestId)}/cancel`, {
      method: "POST",
      headers: operatorRequestHeaders(),
    });
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

function showIncomingTakeoverDialog(requestId, requesterName, waitSeconds) {
  state.incomingRequestId = requestId;
  document.getElementById("takeover-incoming-message").textContent =
    `${requesterName} が操作権を要求しています。`;

  let remaining = Math.ceil(waitSeconds);
  const countdownEl = document.getElementById("takeover-incoming-countdown");
  countdownEl.textContent = remaining;
  clearInterval(state.incomingCountdownTimer);
  state.incomingCountdownTimer = setInterval(() => {
    remaining -= 1;
    countdownEl.textContent = Math.max(remaining, 0);
    if (remaining <= 0) clearInterval(state.incomingCountdownTimer);
  }, 1000);

  document.getElementById("takeover-incoming-dialog").hidden = false;
}

function closeIncomingTakeoverDialog() {
  state.incomingRequestId = null;
  clearInterval(state.incomingCountdownTimer);
  document.getElementById("takeover-incoming-dialog").hidden = true;
}

async function respondToTakeover(accept) {
  const requestId = state.incomingRequestId;
  if (!requestId) return;
  closeIncomingTakeoverDialog();
  try {
    const res = await fetch(`/api/operator/request/${encodeURIComponent(requestId)}/respond`, {
      method: "POST",
      headers: operatorRequestHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ accept }),
    });
    if (res.status === 403) {
      handleOperatorRejection();
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

function handleWsMessage(message) {
  switch (message.type) {
    case "hello":
      state.clientId = message.client_id;
      attemptReclaimOperator();
      break;
    case "snapshot":
      Object.entries(message.devices).forEach(([name, info]) => {
        applyDeviceState(name, info.state, info.error_message);
      });
      Object.entries(message.displays || {}).forEach(([widgetId, entries]) => {
        entries.forEach((entry) => appendDisplayEntry(widgetId, entry));
      });
      applyOperatorInfo(message.operator);
      break;
    case "device_state":
      applyDeviceState(message.name, message.state, message.error_message);
      break;
    case "display_entry":
      appendDisplayEntry(message.widget_id, {
        t: message.t,
        value: message.value,
        error: message.error,
      });
      break;
    case "operator_state":
      applyOperatorInfo(message);
      break;
    case "takeover_request":
      showIncomingTakeoverDialog(
        message.request_id,
        message.requester_display_name,
        message.wait_seconds
      );
      break;
    case "takeover_result":
      hideWaitingDialog();
      showToast(
        message.result === "granted" ? "操作権の要求が許可されました" : "操作権の要求が拒否されました",
        message.result !== "granted"
      );
      break;
    case "operator_granted":
      saveOperatorToken(message.token, message.display_name);
      hideWaitingDialog();
      break;
    case "operator_revoked":
      closeIncomingTakeoverDialog();
      state.operatorToken = null;
      state.isHolder = false;
      sessionStorage.removeItem(OPERATOR_TOKEN_KEY);
      showToast(message.reason || "操作権が移譲されました", true);
      refreshAllDeviceControls();
      break;
    default:
      // notice: a later step.
      break;
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("operator-acquire-btn").addEventListener("click", acquireOperator);
  document.getElementById("operator-release-btn").addEventListener("click", releaseOperator);
  document.getElementById("operator-request-btn").addEventListener("click", requestTakeover);
  document.getElementById("takeover-cancel-btn").addEventListener("click", cancelTakeoverRequest);
  document
    .getElementById("takeover-accept-btn")
    .addEventListener("click", () => respondToTakeover(true));
  document
    .getElementById("takeover-reject-btn")
    .addEventListener("click", () => respondToTakeover(false));
  await loadLayout();
  connectWebSocket();
});
