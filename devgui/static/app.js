// devgui frontend: builds the whole screen from GET /api/layout (see
// docs/design.md section 2) and keeps it live via the /ws WebSocket.
// No build step, no framework - see design.md section 2 for why.

const state = {
  layout: null,
  ws: null,
  clientId: null,
};

// name -> { panel, badge, openBtn, closeBtn, errorEl }
const deviceElements = {};

async function loadLayout() {
  const res = await fetch("/api/layout");
  state.layout = await res.json();
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
  device.widgets.forEach((widget) => body.appendChild(renderWidget(widget)));
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

  el.badge.textContent = stateLabel(stateValue);
  el.badge.className = `state-badge state-${stateValue}`;

  if (el.openBtn) {
    el.openBtn.disabled = !(stateValue === "disconnected" || stateValue === "error");
  }
  if (el.closeBtn) {
    el.closeBtn.disabled = stateValue !== "connected";
  }

  el.errorEl.hidden = !errorMessage;
  el.errorEl.textContent = errorMessage || "";

  setOperantWidgetsEnabled(el.panel, stateValue === "connected");
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
      // Polling + log rendering is added in a later step (design.md
      // section 8); this is just a placeholder container for now.
      const log = document.createElement("div");
      log.className = "display-log";
      log.textContent = "(ログ表示は未実装)";
      wrap.appendChild(log);
      break;
    }
    default:
      break;
  }

  return wrap;
}

async function callWidget(widgetId, value) {
  try {
    const res = await fetch(`/api/call/${encodeURIComponent(widgetId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    const body = await res.json();
    if (!body.ok) {
      showToast(`エラー: ${body.error}`, true);
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
}

async function callDeviceAction(name, action) {
  try {
    const res = await fetch(`/api/devices/${encodeURIComponent(name)}/${action}`, {
      method: "POST",
    });
    const body = await res.json();
    if (!body.ok) {
      showToast(`エラー: ${body.error}`, true);
    }
  } catch (err) {
    showToast(`通信エラー: ${err}`, true);
  }
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

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.addEventListener("open", () => setWsStatus(true));
  ws.addEventListener("close", () => setWsStatus(false));
  ws.addEventListener("error", () => setWsStatus(false));
  ws.addEventListener("message", (event) => {
    handleWsMessage(JSON.parse(event.data));
  });
}

function handleWsMessage(message) {
  switch (message.type) {
    case "hello":
      state.clientId = message.client_id;
      break;
    case "snapshot":
      Object.entries(message.devices).forEach(([name, info]) => {
        applyDeviceState(name, info.state, info.error_message);
      });
      break;
    case "device_state":
      applyDeviceState(message.name, message.state, message.error_message);
      break;
    default:
      // display_entry / operator_* / takeover_* / notice: later steps.
      break;
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  await loadLayout();
  connectWebSocket();
});
