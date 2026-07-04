(function () {
  // --- Client ID ---
  function generateUUID() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      try { return crypto.randomUUID(); } catch (e) {}
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function getClientId() {
    let id = localStorage.getItem("cuelight_observer_id");
    if (!id) {
      id = generateUUID();
      localStorage.setItem("cuelight_observer_id", id);
    }
    return id;
  }

  // --- State ---
  let ws = null;
  let state = {
    positions: {},
    locked: false,
    showfile: null,
    current_cue_index: 0,
    cue_info: {},
    missing_positions: [],
    caller_connected: false,
  };
  let reconnectDelay = 500;

  // --- DOM ---
  const grid = document.getElementById("positionsGrid");
  const transportRow = document.getElementById("transportRow");
  const transportScene = document.getElementById("transportScene");
  const transportCue = document.getElementById("transportCue");
  const transportNote = document.getElementById("transportNote");
  const warningBanner = document.getElementById("warningBanner");
  const callerStatus = document.getElementById("callerStatus");
  const lockTag = document.getElementById("lockTag");
  const takeOverBtn = document.getElementById("takeOverBtn");

  // --- WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/observer`);

    ws.onopen = () => {
      reconnectDelay = 500;
      ws.send(JSON.stringify({
        client_id: getClientId(),
        password: localStorage.getItem("cuelight_observer_pw") || "",
      }));
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      handleMessage(msg);
    };

    ws.onclose = () => {
      callerStatus.textContent = "Reconnecting…";
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "role_rejected":
        if (ws) { ws.onclose = null; ws.close(); }
        localStorage.setItem("cuelight_join_error", "Observer rejected: wrong password.");
        location.href = "/join";
        break;

      case "full_state":
        state.positions = msg.positions || {};
        state.locked = msg.locked;
        state.showfile = msg.showfile;
        state.current_cue_index = msg.current_cue_index;
        state.cue_info = msg.cue_info || {};
        state.missing_positions = msg.missing_positions || [];
        state.caller_connected = !!msg.caller_connected;
        render();
        break;

      case "ping":
        ws.send(JSON.stringify({ type: "pong", ts: msg.ts }));
        break;
    }
  }

  // --- Render ---
  function render() {
    renderGrid();
    renderTransport();
    renderWarnings();
    renderStatus();
  }

  function renderGrid() {
    grid.innerHTML = "";
    Object.keys(state.positions).forEach(function (cid) {
      var pos = state.positions[cid];
      var isOsc = pos.type === "osc";
      var col = document.createElement("div");
      col.className = "position-col";
      if (!pos.connected) col.classList.add("disconnected");
      if (isOsc) col.classList.add("osc-col");
      if (pos.problem) col.classList.add("problem");

      var header = document.createElement("div");
      header.className = "col-header";
      var badgeHtml;
      if (isOsc) {
        badgeHtml = '<div class="col-badge osc-badge">OSC</div>';
      } else if (!pos.connected) {
        badgeHtml = '<div class="col-badge disconnect-badge">DISCONNECTED</div>';
      } else if (pos.problem) {
        badgeHtml = '<div class="col-badge problem-badge">⚠ PROBLEM</div>';
      } else {
        badgeHtml = '<div class="col-badge"></div>';
      }
      var pillStyle = pos.color ? ' style="background:' + escHtml(pos.color) + '"' : "";
      header.innerHTML =
        badgeHtml +
        '<div class="pos-label"><span class="pos-label-pill"' + pillStyle + ">" + escHtml(pos.label) + "</span></div>" +
        '<div class="cue-indicator">' + escHtml(pos.cue_indicator) + "</div>";
      col.appendChild(header);

      // Read-only: the observer sees the message inline under the header
      if (pos.problem && pos.problem_message) {
        var msgEl = document.createElement("div");
        msgEl.className = "problem-msg";
        msgEl.textContent = pos.problem_message;
        col.appendChild(msgEl);
      }

      var sbBtn = document.createElement("div");
      sbBtn.className = "col-btn btn-standby-caller";
      if (isOsc) {
        if (pos.osc_probe === "probing") sbBtn.classList.add("osc-probing");
        else if (pos.osc_probe === "confirmed") sbBtn.classList.add("osc-confirmed");
        else if (pos.osc_probe === "failed") sbBtn.classList.add("osc-failed");
        else sbBtn.classList.add("osc-unverified");
      } else {
        if (pos.standby === "called") sbBtn.classList.add("called", "flashing");
        else if (pos.standby === "acked") sbBtn.classList.add("acked");
      }
      sbBtn.textContent = "STANDBY";
      col.appendChild(sbBtn);

      var preBtn = document.createElement("div");
      preBtn.className = "col-btn btn-preset-caller" + (pos.armed ? " armed" : "");
      preBtn.textContent = "PRESET";
      col.appendChild(preBtn);

      var goBtn = document.createElement("div");
      goBtn.className = "col-btn btn-go-caller";
      if (pos.go === "called") goBtn.classList.add("called");
      goBtn.textContent = "GO";
      col.appendChild(goBtn);

      grid.appendChild(col);
    });
  }

  function renderTransport() {
    if (state.showfile) {
      transportRow.classList.add("visible");
      const ci = state.cue_info;
      transportScene.textContent = ci.scene ? `Scene ${ci.scene} — ` : "";
      if (ci.targets && ci.targets.length > 0) {
        transportCue.textContent = ci.targets
          .map((t) => `${t.position} ${t.cue_number}`)
          .join(", ");
      } else {
        transportCue.textContent = "";
      }
      const note = ci.note || "";
      transportNote.textContent = note;
      transportNote.classList.toggle("visible", !!note);
    } else {
      transportRow.classList.remove("visible");
    }
  }

  function renderWarnings() {
    if (state.missing_positions.length > 0) {
      warningBanner.textContent =
        `Cue ${state.current_cue_index + 1}: ${state.missing_positions.join(", ")} not connected`;
      warningBanner.classList.add("visible");
    } else {
      warningBanner.classList.remove("visible");
    }
  }

  function renderStatus() {
    if (state.caller_connected) {
      callerStatus.textContent = "Caller connected";
      callerStatus.classList.remove("off");
      takeOverBtn.style.display = "none";
    } else {
      callerStatus.textContent = "CALLER DISCONNECTED";
      callerStatus.classList.add("off");
      takeOverBtn.style.display = "";
    }
    lockTag.style.display = state.locked ? "" : "none";
  }

  // --- Take over (manual, only offered while no caller is connected) ---
  takeOverBtn.addEventListener("click", () => {
    if (confirm("Take over as Caller? This device will become the show's caller.")) {
      location.href = "/";
    }
  });

  // --- Utility ---
  function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  // --- Start ---
  connect();
})();
