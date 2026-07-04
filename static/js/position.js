(function () {
  const clientId = localStorage.getItem("cuelight_client_id");
  const label = localStorage.getItem("cuelight_label");
  if (!clientId || !label) {
    location.href = "/join";
    return;
  }

  document.getElementById("labelDisplay").textContent = label;

  const standbyBtn = document.getElementById("standbyBtn");
  const goBtn = document.getElementById("goBtn");
  const headerStrip = document.getElementById("headerStrip");
  const sceneDisplay = document.getElementById("sceneDisplay");
  const cueDisplay = document.getElementById("cueDisplay");
  const callerWarning = document.getElementById("callerWarning");
  const healthDot = document.getElementById("healthDot");
  const lockOverlay = document.getElementById("lockOverlay");
  const noteDisplay = document.getElementById("noteDisplay");
  const flashOverlay = document.getElementById("flashOverlay");

  let ws = null;
  let standbyState = "idle";
  let goState = "idle";
  let reconnectDelay = 500;

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/position`);

    ws.onopen = () => {
      reconnectDelay = 500;
      ws.send(JSON.stringify({ client_id: clientId, label: label }));
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      handleMessage(msg);
    };

    ws.onclose = () => {
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "joined":
        document.getElementById("labelDisplay").textContent = msg.label;
        applyColor(msg.color || "");
        setAttention(!!msg.attention);
        setStandby(msg.standby);
        setGo(msg.go);
        toggleLock(msg.locked);
        callerWarning.classList.toggle("visible", !msg.caller_connected);
        if (msg.scene) sceneDisplay.textContent = `Scene ${msg.scene}`;
        if (msg.cue_number) cueDisplay.textContent = msg.cue_number;
        showNote(msg.note || "");
        break;

      case "ping":
        ws.send(JSON.stringify({ type: "pong", ts: msg.ts }));
        break;

      case "health":
        updateHealth(msg.latency_ms);
        callerWarning.classList.toggle("visible", !msg.caller_connected);
        break;

      case "standby_called":
        hideFlash();
        setStandby("called");
        cueAlert("standby");
        break;

      case "go_called":
        hideFlash();
        setStandby("idle");
        setGo("called");
        cueAlert("go");
        break;

      case "flash":
        flashOverlay.classList.add("visible");
        break;

      case "state_reset":
        setStandby("idle");
        setGo("idle");
        break;

      case "lock_changed":
        toggleLock(msg.locked);
        break;

      case "label_changed":
        document.getElementById("labelDisplay").textContent = msg.label;
        break;

      case "color_changed":
        applyColor(msg.color || "");
        break;

      case "cue_info":
        sceneDisplay.textContent = msg.scene ? `Scene ${msg.scene}` : "";
        cueDisplay.textContent = msg.cue_number || "";
        showNote(msg.note || "");
        break;

      case "attention_cleared":
        setAttention(false);
        showToast("✓ Seen by caller");
        break;

      case "show_started":
        showToast("⏱ Show clock started");
        break;

      case "caller_disconnected":
        callerWarning.classList.add("visible");
        break;

      case "show_ended":
        if (ws) { ws.onclose = null; ws.close(); }
        document.body.innerHTML =
          '<div style="display:flex;align-items:center;justify-content:center;height:100dvh;font-size:24px;color:#888;">Show ended — please close this window.</div>';
        break;

      case "removed":
        if (ws) { ws.onclose = null; ws.close(); }
        localStorage.removeItem("cuelight_label");
        document.body.innerHTML =
          '<div style="display:flex;align-items:center;justify-content:center;height:100dvh;font-size:24px;color:#ff2222;text-align:center;padding:24px;">You have been removed from the show.</div>';
        break;

      case "join_rejected":
        localStorage.setItem("cuelight_join_error", msg.reason);
        localStorage.removeItem("cuelight_label");
        location.href = "/join";
        break;
    }
  }

  function setStandby(state) {
    standbyState = state;
    standbyBtn.className = "btn-standby";
    if (state === "called") {
      standbyBtn.classList.add("called", "flashing");
    } else if (state === "acked") {
      standbyBtn.classList.add("acked");
    }
  }

  function setGo(state) {
    goState = state;
    goBtn.className = "btn-go";
    if (state === "called") {
      goBtn.classList.add("called");
    }
  }

  function updateHealth(latencyMs) {
    healthDot.className = "health-dot";
    if (latencyMs > 3000) healthDot.classList.add("red");
    else if (latencyMs > 1000) healthDot.classList.add("yellow");
  }

  function showNote(text) {
    noteDisplay.textContent = text;
    noteDisplay.classList.toggle("visible", !!text);
  }

  function applyColor(color) {
    var el = document.getElementById("labelDisplay");
    el.style.setProperty("--label-background", color || "transparent");
  }

  function toggleLock(locked) {
    lockOverlay.classList.toggle("visible", locked);
  }

  function hideFlash() {
    flashOverlay.classList.remove("visible");
  }

  flashOverlay.addEventListener("click", () => {
    hideFlash();
    ws.send(JSON.stringify({ type: "ack_flash" }));
  });

  standbyBtn.addEventListener("click", () => {
    if (standbyState === "called") {
      setStandby("acked");
      ws.send(JSON.stringify({ type: "ack_standby" }));
    }
  });

  goBtn.addEventListener("click", () => {
    if (goState === "called") {
      setGo("idle");
      ws.send(JSON.stringify({ type: "ack_go" }));
    }
  });

  document.getElementById("resetBtn").addEventListener("click", () => {
    ws.send(JSON.stringify({ type: "disconnect" }));
    localStorage.removeItem("cuelight_label");
    location.href = "/join";
  });

  // --- Running-mode dimming (cycles off → dim → red → off) ---
  const dimOverlay = document.getElementById("dimOverlay");
  const dimBtn = document.getElementById("dimBtn");
  const DIM_MODES = ["off", "dim", "red"];
  let dimMode = localStorage.getItem("cuelight_dim_mode") || "off";
  if (DIM_MODES.indexOf(dimMode) === -1) dimMode = "off";

  function applyDim() {
    dimOverlay.className = "dim-overlay" + (dimMode === "off" ? "" : " " + dimMode);
    dimBtn.textContent =
      dimMode === "off" ? "DIM" : dimMode === "dim" ? "DIM: ON" : "DIM: RED";
    localStorage.setItem("cuelight_dim_mode", dimMode);
  }

  dimBtn.addEventListener("click", () => {
    dimMode = DIM_MODES[(DIM_MODES.indexOf(dimMode) + 1) % DIM_MODES.length];
    applyDim();
  });

  applyDim();

  // --- Toast (transient feedback: "seen by caller", show clock) ---
  const toast = document.getElementById("toast");
  let toastTimer = null;

  function showToast(text) {
    toast.textContent = text;
    toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 3000);
  }

  // --- Cue alert: optional beep + vibrate on standby/GO (off by default) ---
  // WebAudio needs a user gesture to start on iOS; the toggle tap (or the
  // first tap on the page when re-enabled from a previous session) arms it.
  const alertBtn = document.getElementById("alertBtn");
  let alertOn = localStorage.getItem("cuelight_alert_mode") === "on";
  let audioCtx = null;

  function ensureAudio() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) audioCtx = new AC();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  }

  function beep(freq, dur, delay) {
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const t = audioCtx.currentTime + delay;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  function cueAlert(kind) {
    if (!alertOn) return;
    ensureAudio();
    if (audioCtx && audioCtx.state === "running") {
      if (kind === "standby") {
        beep(880, 0.15, 0);
        beep(880, 0.15, 0.22);
      } else {
        beep(660, 0.25, 0);
      }
    }
    if (navigator.vibrate) {
      try { navigator.vibrate(kind === "standby" ? [200, 100, 200] : 300); } catch (e) {}
    }
  }

  function applyAlert() {
    alertBtn.textContent = alertOn ? "ALERT: ON" : "ALERT";
    alertBtn.classList.toggle("active", alertOn);
    localStorage.setItem("cuelight_alert_mode", alertOn ? "on" : "off");
  }

  alertBtn.addEventListener("click", () => {
    alertOn = !alertOn;
    if (alertOn) ensureAudio();
    applyAlert();
  });

  document.addEventListener("pointerdown", () => {
    if (alertOn) ensureAudio();
  }, { once: true });

  applyAlert();

  // --- Attention: report a problem to the caller ---
  const attentionBtn = document.getElementById("attentionBtn");
  const attentionPanel = document.getElementById("attentionPanel");
  const attentionTitle = document.getElementById("attentionTitle");
  const attentionPresets = document.getElementById("attentionPresets");
  const attentionInput = document.getElementById("attentionInput");
  const attentionSendBtn = document.getElementById("attentionSendBtn");
  const attentionWithdrawBtn = document.getElementById("attentionWithdrawBtn");
  let attentionRaised = false;

  function setAttention(raised) {
    attentionRaised = raised;
    attentionBtn.classList.toggle("raised", raised);
  }

  function openAttentionPanel() {
    attentionTitle.textContent = attentionRaised
      ? "Report sent — waiting for caller"
      : "Report a problem to the caller";
    attentionPresets.style.display = attentionRaised ? "none" : "flex";
    attentionInput.style.display = attentionRaised ? "none" : "";
    attentionSendBtn.style.display = attentionRaised ? "none" : "";
    attentionWithdrawBtn.style.display = attentionRaised ? "" : "none";
    attentionPanel.classList.add("visible");
  }

  function closeAttentionPanel() {
    attentionPanel.classList.remove("visible");
  }

  function raiseAttention(message) {
    ws.send(JSON.stringify({ type: "raise_attention", message: message || "" }));
    setAttention(true);
    attentionInput.value = "";
    closeAttentionPanel();
  }

  attentionBtn.addEventListener("click", openAttentionPanel);
  document.getElementById("lockAttentionBtn").addEventListener("click", openAttentionPanel);
  document.getElementById("attentionCancelBtn").addEventListener("click", closeAttentionPanel);

  attentionPresets.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => raiseAttention(chip.textContent));
  });

  attentionSendBtn.addEventListener("click", () => {
    raiseAttention(attentionInput.value.trim());
  });

  attentionWithdrawBtn.addEventListener("click", () => {
    ws.send(JSON.stringify({ type: "clear_attention" }));
    setAttention(false);
    closeAttentionPanel();
  });

  attentionPanel.addEventListener("click", (e) => {
    if (e.target === attentionPanel) closeAttentionPanel();
  });

  connect();
})();
