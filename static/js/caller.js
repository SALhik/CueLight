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
  };
  let reconnectDelay = 500;
  let lockHoldTimer = null;
  let renamingClientId = null;

  // --- DOM ---
  const grid = document.getElementById("positionsGrid");
  const transportRow = document.getElementById("transportRow");
  const transportScene = document.getElementById("transportScene");
  const transportCue = document.getElementById("transportCue");
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
        render();
        break;

      case "ping":
        send({ type: "pong", ts: msg.ts });
        break;
    }
  }

  // --- Render ---
  function render() {
    renderGrid();
    renderTransport();
    renderWarnings();
    renderLock();
  }

  function renderGrid() {
    // Preserve order: sort by label for consistency
    const posIds = Object.keys(state.positions);

    // Build map of existing columns to reuse
    const existingCols = {};
    grid.querySelectorAll(".position-col").forEach((col) => {
      existingCols[col.dataset.clientId] = col;
    });

    // Clear and rebuild
    grid.innerHTML = "";

    posIds.forEach((cid) => {
      const pos = state.positions[cid];
      const col = document.createElement("div");
      col.className = "position-col" + (pos.connected ? "" : " disconnected");
      col.dataset.clientId = cid;

      // Header
      const header = document.createElement("div");
      header.className = "col-header";
      header.innerHTML = `
        <div class="pos-label">${escHtml(pos.label)}</div>
        <div class="cue-indicator">${escHtml(pos.cue_indicator)}</div>
        <div class="disconnect-badge">DISCONNECTED</div>
      `;
      header.addEventListener("click", () => openRename(cid, pos.label));
      col.appendChild(header);

      // Standby
      const sbBtn = document.createElement("button");
      sbBtn.className = "col-btn btn-standby-caller";
      if (pos.standby === "called") sbBtn.classList.add("called", "flashing");
      else if (pos.standby === "acked") sbBtn.classList.add("acked");
      sbBtn.textContent = "STANDBY";
      sbBtn.addEventListener("click", () => {
        send({ type: "standby", client_id: cid });
      });
      col.appendChild(sbBtn);

      // Preset (arm)
      const preBtn = document.createElement("button");
      preBtn.className = "col-btn btn-preset-caller" + (pos.armed ? " armed" : "");
      preBtn.textContent = "PRESET";
      preBtn.addEventListener("click", () => {
        send({ type: "toggle_arm", client_id: cid });
      });
      col.appendChild(preBtn);

      // Go
      const goBtn = document.createElement("button");
      goBtn.className = "col-btn btn-go-caller";
      if (pos.go === "called") goBtn.classList.add("called");
      goBtn.textContent = "GO";
      goBtn.addEventListener("click", () => {
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
      send({ type: "exit" });
      document.body.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100dvh;font-size:24px;color:#888;">Show ended.</div>';
    }
  });

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

  async function populateSettings() {
    // Health list
    const list = document.getElementById("healthList");
    list.innerHTML = "";
    Object.values(state.positions).forEach((pos) => {
      const li = document.createElement("li");
      const dot = `<span class="health-dot${pos.health === "yellow" ? " yellow" : pos.health === "red" ? " red" : ""}"></span>`;
      const warning = !pos.connected ? ' ⚠️' : '';
      li.innerHTML = `${dot} <span>${escHtml(pos.label)}${warning}</span> <span style="color:var(--text-dim);margin-left:auto">${Math.round(pos.latency_ms)}ms</span>`;
      list.appendChild(li);
    });

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

    // Network
    try {
      const res = await fetch("/api/info");
      const info = await res.json();
      document.getElementById("networkInfo").textContent = `${info.ip}:${info.port}`;
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
      const res = await fetch("/api/info");
      const info = await res.json();
      const url = `http://${info.ip}:${info.port}/join`;
      document.getElementById("urlText").textContent = url;

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
