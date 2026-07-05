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
    let id = localStorage.getItem("cuelight_caller_id");
    if (!id) {
      id = generateUUID();
      localStorage.setItem("cuelight_caller_id", id);
    }
    return id;
  }

  var COLOR_PALETTE = [
    "#5b8def", "#a855f7", "#f97316", "#ec4899",
    "#06b6d4", "#6366f1", "#64748b", "#d946ef",
  ];

  // --- State ---
  let ws = null;
  let state = {
    positions: {},
    locked: false,
    showfile: null,
    current_cue_index: 0,
    paused: false,
    cue_info: {},
    missing_positions: [],
    password_enabled: false,
    password: "",
    osc_patch_filename: "",
    auto_standby: false,
    show_started_at: 0,
    last_go_at: 0,
  };
  // Server-vs-device clock skew, measured from heartbeat pings, so the show
  // clock reads server time even if the iPad's clock drifts.
  let serverClockOffset = 0;
  let reconnectDelay = 500;
  let lockHoldTimer = null;
  let renamingClientId = null;
  let oscResultTimers = {};
  let exited = false;

  // --- DOM ---
  const grid = document.getElementById("positionsGrid");
  const transportRow = document.getElementById("transportRow");
  const transportScene = document.getElementById("transportScene");
  const transportCue = document.getElementById("transportCue");
  const transportNote = document.getElementById("transportNote");
  const warningBanner = document.getElementById("warningBanner");
  const lockBtn = document.getElementById("lockBtn");
  const joinInfoScreen = document.getElementById("joinInfoScreen");

  // --- WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/caller`);

    ws.onopen = () => {
      reconnectDelay = 500;
      ws.send(JSON.stringify({ client_id: getClientId() }));
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      handleMessage(msg);
    };

    ws.onclose = () => {
      if (exited) return;
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "role_rejected":
        // Caller taken, redirect to join as position
        location.href = "/join";
        break;

      case "role_assigned":
        break;

      case "full_state":
        state.positions = msg.positions || {};
        state.locked = msg.locked;
        state.showfile = msg.showfile;
        state.current_cue_index = msg.current_cue_index;
        state.paused = msg.paused;
        state.cue_info = msg.cue_info || {};
        state.missing_positions = msg.missing_positions || [];
        state.password_enabled = msg.password_enabled;
        state.password = msg.password || "";
        state.osc_patch_filename = msg.osc_patch_filename || "";
        state.auto_standby = !!msg.auto_standby;
        state.show_started_at = msg.show_started_at || 0;
        state.last_go_at = msg.last_go_at || 0;
        render();
        break;

      case "osc_result":
        handleOscResult(msg.client_id, msg.result);
        break;

      case "ping":
        serverClockOffset = Date.now() / 1000 - msg.ts;
        send({ type: "pong", ts: msg.ts });
        break;
    }
  }

  function handleOscResult(clientId, result) {
    if (oscResultTimers[clientId]) {
      clearTimeout(oscResultTimers[clientId].timer);
    }
    oscResultTimers[clientId] = {
      result: result,
      timer: setTimeout(function () {
        delete oscResultTimers[clientId];
        render();
      }, 2000),
    };
    render();
  }

  // --- Render ---
  function render() {
    renderGrid();
    renderTransport();
    renderWarnings();
    renderAttention();
    renderClock();
    renderLock();
    // Keep the roll-call/health list live while Settings is open
    if (document.getElementById("settingsModal").classList.contains("visible")) {
      renderHealthList();
    }
  }

  function renderGrid() {
    var posIds = Object.keys(state.positions);

    grid.innerHTML = "";

    posIds.forEach(function (cid) {
      var pos = state.positions[cid];
      var isOsc = pos.type === "osc";
      var col = document.createElement("div");
      col.className = "position-col";
      if (!pos.connected) col.classList.add("disconnected");
      if (isOsc) col.classList.add("osc-col");
      if (pos.attention) col.classList.add("attention");
      col.dataset.clientId = cid;

      // Header
      var header = document.createElement("div");
      header.className = "col-header";
      var badgeHtml;
      if (pos.attention) {
        badgeHtml = '<div class="col-badge attention-badge flashing">⚠ ATTENTION</div>';
      } else if (isOsc) {
        badgeHtml = '<div class="col-badge osc-badge">OSC</div>';
      } else if (!pos.connected) {
        badgeHtml = '<div class="col-badge disconnect-badge">DISCONNECTED</div>';
      } else {
        badgeHtml = '<div class="col-badge"></div>';
      }
      var healthDotHtml = "";
      if (isOsc) {
        var dotCls = "health-dot";
        if (pos.osc_trust === "none") dotCls += " osc-unverified";
        else if (pos.osc_probe === "confirmed") dotCls += "";
        else if (pos.osc_probe === "failed") dotCls += " red";
        else dotCls += " osc-unverified";
        healthDotHtml = '<span class="' + dotCls + '"></span> ';
      }
      var pillStyle = pos.color ? ' style="background:' + escHtml(pos.color) + '"' : "";
      header.innerHTML =
        badgeHtml +
        '<div class="pos-label"><span class="pos-label-pill"' + pillStyle + ">" + healthDotHtml + escHtml(pos.label) + "</span></div>" +
        '<div class="cue-indicator">' + escHtml(pos.cue_indicator) + "</div>";
      header.addEventListener("click", function () {
        openRename(cid, pos.label);
      });
      col.appendChild(header);

      // Standby
      var sbBtn = document.createElement("button");
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
      sbBtn.addEventListener("click", function () {
        send({ type: "standby", client_id: cid });
      });
      col.appendChild(sbBtn);

      // Preset (arm)
      var preBtn = document.createElement("button");
      preBtn.className = "col-btn btn-preset-caller" + (pos.armed ? " armed" : "");
      preBtn.textContent = "PRESET";
      preBtn.addEventListener("click", function () {
        send({ type: "toggle_arm", client_id: cid });
      });
      col.appendChild(preBtn);

      // Go
      var goBtn = document.createElement("button");
      goBtn.className = "col-btn btn-go-caller";
      var oscResult = oscResultTimers[cid];
      if (oscResult) {
        if (oscResult.result === "sent") {
          goBtn.classList.add("osc-sent");
          goBtn.textContent = "SENT";
        } else if (oscResult.result === "no_reply") {
          goBtn.classList.add("osc-no-reply");
          goBtn.textContent = "NO REPLY";
        }
      } else {
        if (pos.go === "called") goBtn.classList.add("called");
        goBtn.textContent = "GO";
      }
      goBtn.addEventListener("click", function () {
        send({ type: "go", client_id: cid });
      });
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
      const pauseBtn = document.getElementById("pauseBtn");
      pauseBtn.classList.toggle("active", state.paused);
      pauseBtn.textContent = state.paused ? "RESUME" : "PAUSE";
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

  function renderLock() {
    lockBtn.classList.toggle("locked", state.locked);
    lockBtn.textContent = state.locked ? "UNLOCK" : "LOCK";
    lockOverlay.classList.toggle("visible", state.locked);
  }

  // --- Operator attention banner ---
  function renderAttention() {
    const banner = document.getElementById("attentionBanner");
    const raised = Object.entries(state.positions).filter(([, p]) => p.attention);
    banner.innerHTML = "";
    if (raised.length === 0) {
      banner.classList.remove("visible");
      return;
    }
    raised.forEach(([cid, pos]) => {
      const row = document.createElement("div");
      row.className = "attention-row";
      row.innerHTML =
        '<span class="who">⚠ ' + escHtml(pos.label) + "</span>" +
        (pos.attention_message
          ? '<span class="msg">' + escHtml(pos.attention_message) + "</span>"
          : "");
      const btn = document.createElement("button");
      btn.textContent = "CLEAR";
      btn.addEventListener("click", () => {
        send({ type: "clear_attention", client_id: cid });
      });
      row.appendChild(btn);
      banner.appendChild(row);
    });
    banner.classList.add("visible");
  }

  // --- Show clock ---
  const clockStrip = document.getElementById("clockStrip");
  let clockPrefs;
  try {
    clockPrefs = JSON.parse(localStorage.getItem("cuelight_clock_prefs")) || {};
  } catch (e) {
    clockPrefs = {};
  }
  clockPrefs = Object.assign({ time: true, elapsed: true, sinceGo: false }, clockPrefs);

  function saveClockPrefs() {
    localStorage.setItem("cuelight_clock_prefs", JSON.stringify(clockPrefs));
  }

  function fmtDur(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return h > 0
      ? h + ":" + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0")
      : m + ":" + String(sec).padStart(2, "0");
  }

  function serverNow() {
    return Date.now() / 1000 - serverClockOffset;
  }

  function renderClock() {
    const parts = [];
    if (clockPrefs.time) {
      parts.push(new Date().toLocaleTimeString([], {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      }));
    }
    if (clockPrefs.elapsed) {
      parts.push(state.show_started_at > 0
        ? "SHOW " + fmtDur(serverNow() - state.show_started_at)
        : "SHOW --:--");
    }
    if (clockPrefs.sinceGo) {
      parts.push(state.last_go_at > 0
        ? "GO +" + fmtDur(serverNow() - state.last_go_at)
        : "GO --");
    }
    clockStrip.textContent = parts.length ? parts.join("  ·  ") : "⏱";
    clockStrip.classList.toggle("running", state.show_started_at > 0);
  }

  setInterval(renderClock, 1000);

  clockStrip.addEventListener("click", () => {
    const started = state.show_started_at > 0;
    document.getElementById("clockStatus").textContent = started
      ? "Show started at " +
        new Date(state.show_started_at * 1000).toLocaleTimeString() +
        " — elapsed " + fmtDur(serverNow() - state.show_started_at)
      : "Clock not started. START SHOW records the time in the show log and " +
        "notifies all positions.";
    document.getElementById("startShowBtn").classList.toggle("hidden", started);
    document.getElementById("clearClockBtn").classList.toggle("hidden", !started);
    document.getElementById("clockModal").classList.add("visible");
  });

  document.getElementById("startShowBtn").addEventListener("click", () => {
    send({ type: "start_show_clock" });
    closeModal("clockModal");
  });

  document.getElementById("clearClockBtn").addEventListener("click", () => {
    if (confirm("Clear the show clock?")) {
      send({ type: "clear_show_clock" });
      closeModal("clockModal");
    }
  });

  // --- Master buttons ---
  document.getElementById("masterStandby").addEventListener("click", () => {
    if (!state.locked) send({ type: "standby_armed" });
  });

  document.getElementById("masterReset").addEventListener("click", () => {
    if (!state.locked) send({ type: "reset_armed" });
  });

  document.getElementById("masterGo").addEventListener("click", () => {
    if (!state.locked) send({ type: "go_armed" });
  });

  // --- Lock (2-second hold to unlock) ---
  lockBtn.addEventListener("mousedown", startLockHold);
  lockBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startLockHold(); });
  lockBtn.addEventListener("mouseup", endLockHold);
  lockBtn.addEventListener("mouseleave", endLockHold);
  lockBtn.addEventListener("touchend", endLockHold);
  lockBtn.addEventListener("touchcancel", endLockHold);

  function startLockHold() {
    if (!state.locked) {
      send({ type: "lock", locked: true });
      return;
    }
    lockHoldTimer = setTimeout(() => {
      send({ type: "lock", locked: false });
      lockHoldTimer = null;
    }, 2000);
  }

  function endLockHold() {
    if (lockHoldTimer) {
      clearTimeout(lockHoldTimer);
      lockHoldTimer = null;
    }
  }

  // --- Reset All ---
  document.getElementById("resetAllBtn").addEventListener("click", () => {
    if (!state.locked) send({ type: "reset_all" });
  });

  // --- Exit ---
  document.getElementById("exitBtn").addEventListener("click", () => {
    if (confirm("End the show and disconnect all positions?")) {
      exited = true;
      send({ type: "exit" });
      showEndedScreen();
    }
  });

  async function showEndedScreen() {
    document.body.innerHTML =
      '<div style="display:flex;flex-direction:column;gap:24px;align-items:center;justify-content:center;height:100dvh;">' +
      '<div style="font-size:24px;color:#888;">Show ended.</div>' +
      '<button id="resumeShowBtn" style="display:none;padding:14px 28px;font-size:16px;border-radius:8px;background:var(--osc-accent-dim);color:var(--text);">RESUME SHOW</button>' +
      "</div>";
    // The exit message and this fetch race: the server may not have written
    // the backup yet when the first request lands, so poll briefly.
    for (let i = 0; i < 10; i++) {
      try {
        const info = await (await fetch("/api/backup_info")).json();
        if (info.exists) {
          const btn = document.getElementById("resumeShowBtn");
          btn.style.display = "";
          btn.addEventListener("click", async () => {
            const res = await (await fetch("/api/resume_show", { method: "POST", headers: pwAuth() })).json();
            if (res.ok) location.reload();
          });
          return;
        }
      } catch (e) {}
      await new Promise((r) => setTimeout(r, 300));
    }
  }

  // --- Transport ---
  document.getElementById("prevCueBtn").addEventListener("click", () => {
    send({ type: "prev_cue" });
  });

  document.getElementById("pauseBtn").addEventListener("click", () => {
    send({ type: "pause", paused: !state.paused });
  });

  document.getElementById("jumpCueBtn").addEventListener("click", () => {
    openJumpModal();
  });

  // --- Jump modal ---
  function openJumpModal() {
    if (!state.showfile) return;
    const list = document.getElementById("cueList");
    list.innerHTML = "";
    state.showfile.cues.forEach((cue, idx) => {
      const item = document.createElement("div");
      item.className = "cue-item" + (idx === state.current_cue_index ? " current" : "");
      item.innerHTML = `
        <span>Cue ${cue.sequence} — Scene ${cue.scene}</span>
        <span>${cue.targets.map((t) => t.position + " " + t.cue_number).join(", ")}</span>
      `;
      item.addEventListener("click", () => {
        send({ type: "jump_to_cue", index: idx });
        closeModal("jumpModal");
      });
      list.appendChild(item);
    });
    document.getElementById("jumpModal").classList.add("visible");
  }

  // --- Rename modal ---
  function openRename(clientId, currentLabel) {
    renamingClientId = clientId;
    document.getElementById("renameInput").value = currentLabel;
    document.getElementById("renameError").style.display = "none";

    var picker = document.getElementById("colorPicker");
    picker.innerHTML = "";
    var currentColor = (state.positions[clientId] || {}).color || "";
    COLOR_PALETTE.forEach(function (c) {
      var swatch = document.createElement("div");
      swatch.className = "color-swatch" + (c === currentColor ? " selected" : "");
      swatch.style.background = c;
      swatch.addEventListener("click", function () {
        picker.querySelectorAll(".color-swatch").forEach(function (s) { s.classList.remove("selected"); });
        swatch.classList.add("selected");
        send({ type: "set_color", client_id: clientId, color: c });
      });
      picker.appendChild(swatch);
    });

    document.getElementById("renameModal").classList.add("visible");
    document.getElementById("renameInput").focus();
  }

  document.getElementById("renameSaveBtn").addEventListener("click", () => {
    const newLabel = document.getElementById("renameInput").value.trim();
    const errorEl = document.getElementById("renameError");
    errorEl.style.display = "none";
    if (!newLabel || !renamingClientId) return;

    const duplicate = Object.entries(state.positions).some(
      ([cid, pos]) => cid !== renamingClientId && pos.label.toLowerCase() === newLabel.toLowerCase()
    );
    if (duplicate) {
      errorEl.textContent = "Label \"" + newLabel + "\" is already in use.";
      errorEl.style.display = "block";
      return;
    }

    send({ type: "rename", client_id: renamingClientId, label: newLabel });
    closeModal("renameModal");
  });

  document.getElementById("removePositionBtn").addEventListener("click", () => {
    if (!renamingClientId) return;
    const pos = state.positions[renamingClientId];
    const label = pos ? pos.label : "this position";
    if (confirm("Remove " + label + " from the show?")) {
      send({ type: "remove_position", client_id: renamingClientId });
      closeModal("renameModal");
    }
  });

  document.getElementById("renameInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("renameSaveBtn").click();
  });

  // --- Settings modal ---
  document.getElementById("settingsBtn").addEventListener("click", async () => {
    await populateSettings();
    document.getElementById("settingsModal").classList.add("visible");
  });

  function renderHealthList() {
    const list = document.getElementById("healthList");
    list.innerHTML = "";
    Object.values(state.positions).forEach((pos) => {
      const li = document.createElement("li");
      var dotClass = "health-dot";
      if (pos.type === "osc") {
        if (pos.osc_trust === "none") dotClass += " osc-unverified";
        else if (pos.osc_probe === "confirmed") dotClass += "";
        else if (pos.osc_probe === "failed") dotClass += " red";
        else dotClass += " osc-unverified";
      } else {
        if (pos.health === "yellow") dotClass += " yellow";
        else if (pos.health === "red") dotClass += " red";
      }
      const dot = `<span class="${dotClass}"></span>`;
      const tag = pos.type === "osc" ? " [OSC]" : "";
      const warning = !pos.connected && pos.type !== "osc" ? ' ⚠️' : '';
      let flashMark = "";
      if (pos.flash === "pending") flashMark = ' <span style="color:var(--yellow-bright)">⏳ waiting</span>';
      else if (pos.flash === "confirmed") flashMark = ' <span style="color:var(--green-bright)">✔ here</span>';
      const metric = pos.type === "osc"
        ? pos.osc_trust === "osc_reply" ? "app confirmed"
          : pos.osc_trust === "tcp_port" ? "port listening"
          : "unverified"
        : Math.round(pos.latency_ms) + "ms";
      li.innerHTML = `${dot} <span>${escHtml(pos.label)}${tag}${warning}${flashMark}</span> <span style="color:var(--text-dim);margin-left:auto">${metric}</span>`;
      list.appendChild(li);
    });
  }

  async function populateSettings() {
    renderHealthList();

    // Showfiles
    try {
      const res = await fetch("/api/showfiles");
      const data = await res.json();
      const sel = document.getElementById("showfileSelect");
      sel.innerHTML = "";
      data.files.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        sel.appendChild(opt);
      });
    } catch (e) {}

    document.getElementById("currentShowfile").textContent =
      state.showfile ? `Loaded: ${state.showfile.filename}` : "No showfile loaded";

    document.getElementById("autoStandbyToggle").checked = state.auto_standby;

    // Show clock display preferences (device-local)
    document.getElementById("clockShowTime").checked = clockPrefs.time;
    document.getElementById("clockShowElapsed").checked = clockPrefs.elapsed;
    document.getElementById("clockShowGo").checked = clockPrefs.sinceGo;

    // Patches
    try {
      const res = await fetch("/api/patches");
      const data = await res.json();
      const sel = document.getElementById("patchSelect");
      sel.innerHTML = "";
      data.files.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        sel.appendChild(opt);
      });
    } catch (e) {}

    document.getElementById("currentPatch").textContent =
      state.osc_patch_filename ? `Loaded: ${state.osc_patch_filename}` : "No patch loaded";

    // Network
    let netText = window.location.host;
    try {
      const info = await (await fetch("/api/info")).json();
      if (info.mdns_host) netText += " — or http://" + info.mdns_host + ":" + info.port + "/";
    } catch (e) {}
    document.getElementById("networkInfo").textContent = netText;

    // Previous show (backup left by EXIT)
    try {
      const info = await (await fetch("/api/backup_info")).json();
      const section = document.getElementById("resumeSection");
      section.style.display = info.exists ? "" : "none";
      if (info.exists) {
        document.getElementById("resumeInfo").textContent =
          (info.showfile_filename || "no showfile") + ", " + info.position_count +
          (info.position_count === 1 ? " position" : " positions");
      }
    } catch (e) {}

    // Password
    document.getElementById("pwToggle").checked = state.password_enabled;
    document.getElementById("pwValueField").style.display = state.password_enabled ? "flex" : "none";
    document.getElementById("pwInput").value = state.password || "";
  }

  document.getElementById("loadShowfileBtn").addEventListener("click", () => {
    const filename = document.getElementById("showfileSelect").value;
    if (filename) {
      send({ type: "load_showfile", filename });
      closeModal("settingsModal");
    }
  });

  document.getElementById("unloadShowfileBtn").addEventListener("click", () => {
    send({ type: "unload_showfile" });
    closeModal("settingsModal");
  });

  document.getElementById("editShowfileBtn").addEventListener("click", () => {
    window.open("/editor", "_blank");
  });

  document.getElementById("autoStandbyToggle").addEventListener("change", (e) => {
    send({ type: "set_auto_standby", enabled: e.target.checked });
  });

  document.getElementById("clockShowTime").addEventListener("change", (e) => {
    clockPrefs.time = e.target.checked;
    saveClockPrefs();
    renderClock();
  });

  document.getElementById("clockShowElapsed").addEventListener("change", (e) => {
    clockPrefs.elapsed = e.target.checked;
    saveClockPrefs();
    renderClock();
  });

  document.getElementById("clockShowGo").addEventListener("change", (e) => {
    clockPrefs.sinceGo = e.target.checked;
    saveClockPrefs();
    renderClock();
  });

  document.getElementById("loadPatchBtn").addEventListener("click", () => {
    const filename = document.getElementById("patchSelect").value;
    if (filename) {
      send({ type: "load_patch", filename: filename });
      closeModal("settingsModal");
    }
  });

  document.getElementById("unloadPatchBtn").addEventListener("click", () => {
    send({ type: "unload_patch" });
    closeModal("settingsModal");
  });

  document.getElementById("editPatchBtn").addEventListener("click", () => {
    window.open("/editor#patches", "_blank");
  });

  document.getElementById("resumeShowSettingsBtn").addEventListener("click", async () => {
    if (!confirm("Replace the current show with the previous one?")) return;
    try {
      const res = await (await fetch("/api/resume_show", { method: "POST", headers: pwAuth() })).json();
      if (res.ok) closeModal("settingsModal");
    } catch (e) {}
  });

  // --- Flash roll call ---
  document.getElementById("flashAllBtn").addEventListener("click", () => {
    send({ type: "flash_all" });
  });

  document.getElementById("clearFlashBtn").addEventListener("click", () => {
    send({ type: "clear_flash" });
  });

  // --- Show log ---
  // Protected endpoints want the join password when one is set; the caller
  // already has it from full_state. It goes in a header, never a query
  // param — query strings end up in server access logs and browser history.
  function pwAuth() {
    return state.password_enabled && state.password
      ? { "X-CueLight-Password": state.password }
      : {};
  }

  // The tab must be opened synchronously — Safari's popup blocker only
  // allows window.open inside the tap gesture, not after an await.
  async function viewAuthed(path) {
    const win = window.open("", "_blank");
    if (!win) return;
    try {
      const res = await fetch(path, { headers: pwAuth() });
      if (!res.ok) throw new Error(res.status);
      const text = await res.text();
      win.location = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    } catch (e) {
      win.close();
    }
  }

  async function downloadAuthed(path, filename) {
    try {
      const res = await fetch(path, { headers: pwAuth() });
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {}
  }

  document.getElementById("downloadLogBtn").addEventListener("click", () => {
    downloadAuthed("/api/showlog?format=csv", "showlog.csv");
  });

  document.getElementById("viewLogBtn").addEventListener("click", () => {
    viewAuthed("/api/showlog");
  });

  document.getElementById("showReportBtn").addEventListener("click", () => {
    viewAuthed("/api/showreport?format=txt");
  });

  document.getElementById("pwToggle").addEventListener("change", (e) => {
    const enabled = e.target.checked;
    document.getElementById("pwValueField").style.display = enabled ? "flex" : "none";
    if (!enabled) {
      send({ type: "set_password", enabled: false });
    }
  });

  document.getElementById("pwSaveBtn").addEventListener("click", () => {
    const pw = document.getElementById("pwInput").value.trim();
    send({ type: "set_password", enabled: true, password: pw });
  });

  // --- Join info ---
  document.getElementById("showJoinInfo").addEventListener("click", async () => {
    try {
      const url = `http://${window.location.host}/join`;
      document.getElementById("urlText").textContent = url;

      const mdnsText = document.getElementById("mdnsText");
      mdnsText.style.display = "none";
      try {
        const info = await (await fetch("/api/info")).json();
        if (info.mdns_host) {
          mdnsText.textContent = `or http://${info.mdns_host}:${info.port}/join`;
          mdnsText.style.display = "block";
        }
      } catch (e) {}

      let qrUrl = `/api/qr`;
      if (state.password_enabled && state.password) {
        qrUrl += `?password=${encodeURIComponent(state.password)}`;
        document.getElementById("pwText").textContent = `Password: ${state.password}`;
        document.getElementById("pwText").style.display = "block";
      } else {
        document.getElementById("pwText").style.display = "none";
      }
      document.getElementById("qrImg").src = qrUrl;
    } catch (e) {}
    joinInfoScreen.style.display = "flex";
    document.getElementById("showJoinInfo").style.display = "none";
    document.getElementById("hideJoinInfo").style.display = "";
  });

  document.getElementById("hideJoinInfo").addEventListener("click", () => {
    joinInfoScreen.style.display = "none";
    document.getElementById("hideJoinInfo").style.display = "none";
    document.getElementById("showJoinInfo").style.display = "";
  });

  // --- Modal helpers ---
  function closeModal(id) {
    document.getElementById(id).classList.remove("visible");
  }

  document.getElementById("closeSettings").addEventListener("click", () => closeModal("settingsModal"));
  document.getElementById("closeJump").addEventListener("click", () => closeModal("jumpModal"));
  document.getElementById("closeRename").addEventListener("click", () => closeModal("renameModal"));
  document.getElementById("closeClock").addEventListener("click", () => closeModal("clockModal"));

  // Close modals on overlay click
  document.querySelectorAll(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.remove("visible");
    });
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
