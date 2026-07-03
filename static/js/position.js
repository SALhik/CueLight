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
  const showBanner = document.getElementById("showBanner");
  const problemPanel = document.getElementById("problemPanel");
  const problemBtn = document.getElementById("problemBtn");
  const problemInput = document.getElementById("problemInput");

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
        setStandby(msg.standby);
        setGo(msg.go);
        setProblem(!!msg.problem);
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
        hideProblemPanel();
        setStandby("called");
        alertStandby();
        break;

      case "go_called":
        hideFlash();
        hideProblemPanel();
        setStandby("idle");
        setGo("called");
        alertGo();
        break;

      case "problem_changed":
        setProblem(!!msg.problem);
        break;

      case "show_started":
        showStartBanner();
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

  // --- Problem signal ---
  let problemActive = false;
  let bannerTimer = null;

  function setProblem(active) {
    problemActive = active;
    problemBtn.classList.toggle("active", active);
    problemPanel.classList.toggle("active", active);
    if (!active) hideProblemPanel();
  }

  function hideProblemPanel() {
    problemPanel.classList.remove("visible");
  }

  function raiseProblem(message) {
    ws.send(JSON.stringify({ type: "raise_problem", message: (message || "").slice(0, 60) }));
    hideProblemPanel();
    problemInput.value = "";
    problemInput.blur();
  }

  problemBtn.addEventListener("click", () => {
    problemPanel.classList.toggle("visible");
  });

  problemPanel.querySelectorAll(".problem-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      raiseProblem(btn.dataset.msg);
    });
  });

  document.getElementById("problemRaiseBtn").addEventListener("click", () => {
    raiseProblem(problemInput.value.trim());
  });

  document.getElementById("problemClearBtn").addEventListener("click", () => {
    ws.send(JSON.stringify({ type: "clear_problem" }));
    hideProblemPanel();
  });

  // --- Show started notice (transient, non-blocking) ---
  function showStartBanner() {
    showBanner.classList.add("visible");
    if (bannerTimer) clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => {
      showBanner.classList.remove("visible");
      bannerTimer = null;
    }, 4000);
  }

  // --- Operator alerts: beep + vibration on incoming cues (opt-in) ---
  // WebAudio needs a user gesture to start (same constraint as keepawake),
  // so the context is created/resumed on taps while alerts are on.
  // navigator.vibrate is Android-only; iOS relies on the beep.
  const alertBtn = document.getElementById("alertBtn");
  let alertsOn = localStorage.getItem("cuelight_alerts") === "on";
  let audioCtx = null;

  function ensureAudio() {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!audioCtx) {
      try { audioCtx = new AC(); } catch (e) { return; }
    }
    if (audioCtx.state === "suspended") audioCtx.resume().catch(function () {});
  }

  function beepAt(t, freq, dur) {
    var osc = audioCtx.createOscillator();
    var gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.4, t + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  function vibrate(pattern) {
    if (navigator.vibrate) {
      try { navigator.vibrate(pattern); } catch (e) {}
    }
  }

  function alertStandby() {
    if (!alertsOn) return;
    vibrate([120, 90, 120]);
    if (audioCtx && audioCtx.state === "running") {
      var t = audioCtx.currentTime;
      beepAt(t, 880, 0.12);       // two short beeps = standby
      beepAt(t + 0.22, 880, 0.12);
    }
  }

  function alertGo() {
    if (!alertsOn) return;
    vibrate([400]);
    if (audioCtx && audioCtx.state === "running") {
      beepAt(audioCtx.currentTime, 587, 0.45); // one long beep = GO
    }
  }

  function applyAlerts() {
    alertBtn.textContent = alertsOn ? "ALERT: ON" : "ALERT";
    alertBtn.classList.toggle("active", alertsOn);
    localStorage.setItem("cuelight_alerts", alertsOn ? "on" : "off");
  }

  alertBtn.addEventListener("click", () => {
    alertsOn = !alertsOn;
    applyAlerts();
    if (alertsOn) {
      // This tap is the unlock gesture; a soft blip confirms sound works
      ensureAudio();
      if (audioCtx && audioCtx.state === "running") {
        beepAt(audioCtx.currentTime, 880, 0.08);
      }
    }
  });

  document.addEventListener("touchend", function () { if (alertsOn) ensureAudio(); }, true);
  document.addEventListener("click", function () { if (alertsOn) ensureAudio(); }, true);

  applyAlerts();

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

  connect();
})();
