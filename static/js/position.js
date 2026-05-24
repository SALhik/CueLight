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
        setStandby(msg.standby);
        setGo(msg.go);
        toggleLock(msg.locked);
        callerWarning.classList.toggle("visible", !msg.caller_connected);
        if (msg.scene) sceneDisplay.textContent = `Scene ${msg.scene}`;
        if (msg.cue_number) cueDisplay.textContent = msg.cue_number;
        break;

      case "ping":
        ws.send(JSON.stringify({ type: "pong", ts: msg.ts }));
        break;

      case "health":
        updateHealth(msg.latency_ms);
        callerWarning.classList.toggle("visible", !msg.caller_connected);
        break;

      case "standby_called":
        setStandby("called");
        break;

      case "go_called":
        setStandby("idle");
        setGo("called");
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

      case "cue_info":
        sceneDisplay.textContent = msg.scene ? `Scene ${msg.scene}` : "";
        cueDisplay.textContent = msg.cue_number || "";
        break;

      case "caller_disconnected":
        callerWarning.classList.add("visible");
        break;

      case "show_ended":
        document.body.innerHTML =
          '<div style="display:flex;align-items:center;justify-content:center;height:100dvh;font-size:24px;color:#888;">Show ended — please close this window.</div>';
        if (ws) ws.close();
        break;

      case "removed":
        if (ws) ws.close();
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

  function toggleLock(locked) {
    lockOverlay.classList.toggle("visible", locked);
  }

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

  connect();
})();
