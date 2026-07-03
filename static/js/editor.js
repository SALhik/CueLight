(function () {
  // --- Tab switching ---
  var tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    });
  });

  if (window.location.hash === "#patches") {
    tabBtns.forEach(function (b) { b.classList.remove("active"); });
    document.querySelector('[data-tab="patches"]').classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
    document.getElementById("tab-patches").classList.add("active");
  }

  // ========== SHOWFILE EDITOR ==========
  var cueBody = document.getElementById("cueBody");
  var cues = [];
  var currentFilename = "";

  async function loadFileList() {
    var res = await fetch("/api/showfiles");
    var data = await res.json();
    var sel = document.getElementById("fileSelect");
    sel.innerHTML = '<option value="">— select —</option>';
    data.files.forEach(function (f) {
      var opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      sel.appendChild(opt);
    });
  }

  document.getElementById("loadBtn").addEventListener("click", async function () {
    var filename = document.getElementById("fileSelect").value;
    if (!filename) return;
    var res = await fetch("/api/showfile/" + encodeURIComponent(filename));
    if (!res.ok) { showStatus("statusMsg", "File not found", true); return; }
    var data = await res.json();
    currentFilename = filename;
    document.getElementById("showName").value = data.show_name || "";
    document.getElementById("showVersion").value = data.version || 1;
    cues = (data.cues || []).map(function (c) {
      return {
        sequence: c.sequence,
        scene: c.scene || "",
        targets: (c.targets || []).map(function (t) { return t.position + ":" + t.cue_number; }).join(", "),
        note: c.note || "",
      };
    });
    renderCues();
    showStatus("statusMsg", "Loaded " + filename);
  });

  document.getElementById("newBtn").addEventListener("click", function () {
    var name = document.getElementById("newFilename").value.trim();
    if (!name) return;
    currentFilename = name.endsWith(".json") ? name : name + ".json";
    document.getElementById("showName").value = "";
    document.getElementById("showVersion").value = "1";
    cues = [];
    renderCues();
    showStatus("statusMsg", "New file: " + currentFilename);
  });

  document.getElementById("addCueBtn").addEventListener("click", function () {
    var nextSeq = cues.length > 0 ? Math.max.apply(null, cues.map(function (c) { return c.sequence; })) + 1 : 1;
    cues.push({ sequence: nextSeq, scene: "", targets: "", note: "" });
    renderCues();
  });

  document.getElementById("saveBtn").addEventListener("click", async function () {
    readCuesFromDOM();
    if (!currentFilename) { showStatus("statusMsg", "No filename set", true); return; }
    var data = {
      show_name: document.getElementById("showName").value.trim(),
      version: parseInt(document.getElementById("showVersion").value) || 1,
      cues: cues.map(function (c) {
        return {
          sequence: c.sequence,
          scene: c.scene,
          targets: parseTargets(c.targets),
          note: c.note,
        };
      }),
    };
    var res = await fetch("/api/showfile/" + encodeURIComponent(currentFilename), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      showStatus("statusMsg", "Saved!");
      loadFileList();
    } else {
      var err = await res.json();
      showStatus("statusMsg", "Errors: " + (err.errors || []).join("; "), true);
    }
  });

  // --- CSV import/export ---
  var csvFileInput = document.getElementById("csvFileInput");

  document.getElementById("importCsvBtn").addEventListener("click", function () {
    csvFileInput.value = "";
    csvFileInput.click();
  });

  csvFileInput.addEventListener("change", function () {
    var file = csvFileInput.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = async function () {
      var res = await fetch("/api/csv/import", {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body: reader.result,
      });
      var data = await res.json();
      if (!res.ok) {
        showStatus("statusMsg", "CSV errors: " + (data.errors || []).join("; "), true);
        return;
      }
      readCuesFromDOM();
      if (cues.length > 0 && !confirm("Replace the current cue list with the imported CSV?")) return;
      cues = data.cues.map(function (c) {
        return {
          sequence: c.sequence,
          scene: c.scene || "",
          targets: (c.targets || []).map(function (t) { return t.position + ":" + t.cue_number; }).join(", "),
          note: c.note || "",
        };
      });
      renderCues();
      showStatus("statusMsg", "Imported " + cues.length + " cues from " + file.name);
    };
    reader.readAsText(file);
  });

  document.getElementById("exportCsvBtn").addEventListener("click", async function () {
    readCuesFromDOM();
    var data = {
      cues: cues.map(function (c) {
        return { sequence: c.sequence, scene: c.scene, targets: parseTargets(c.targets), note: c.note };
      }),
    };
    var res = await fetch("/api/csv/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) { showStatus("statusMsg", "Export failed", true); return; }
    var blob = await res.blob();
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (currentFilename || "showfile").replace(/\.json$/, "") + ".csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });

  function parseTargets(str) {
    return str.split(",").map(function (s) { return s.trim(); }).filter(Boolean).map(function (s) {
      var parts = s.split(":");
      var position = parts[0].trim();
      var rest = parts.slice(1).join(":").trim() || "1";
      return { position: position, cue_number: rest };
    });
  }

  function readCuesFromDOM() {
    var rows = cueBody.querySelectorAll("tr");
    cues = [];
    rows.forEach(function (row) {
      var inputs = row.querySelectorAll("input");
      cues.push({
        sequence: parseInt(inputs[0].value) || 0,
        scene: inputs[1].value,
        targets: inputs[2].value,
        note: inputs[3].value,
      });
    });
  }

  function renderCues() {
    cueBody.innerHTML = "";
    cues.forEach(function (cue, idx) {
      var tr = document.createElement("tr");

      var tdSeq = document.createElement("td");
      var inSeq = document.createElement("input");
      inSeq.type = "number";
      inSeq.value = cue.sequence;
      tdSeq.appendChild(inSeq);

      var tdScene = document.createElement("td");
      var inScene = document.createElement("input");
      inScene.type = "text";
      inScene.value = cue.scene;
      tdScene.appendChild(inScene);

      var tdTargets = document.createElement("td");
      var inTargets = document.createElement("input");
      inTargets.type = "text";
      inTargets.value = cue.targets;
      inTargets.placeholder = "LX:1, SND:1";
      tdTargets.appendChild(inTargets);

      var tdNote = document.createElement("td");
      var inNote = document.createElement("input");
      inNote.type = "text";
      inNote.value = cue.note;
      tdNote.appendChild(inNote);

      var tdDel = document.createElement("td");
      var btnDel = document.createElement("button");
      btnDel.className = "btn-del";
      btnDel.textContent = "✕";
      btnDel.addEventListener("click", function () {
        readCuesFromDOM();
        cues.splice(idx, 1);
        renderCues();
      });
      tdDel.appendChild(btnDel);

      tr.appendChild(tdSeq);
      tr.appendChild(tdScene);
      tr.appendChild(tdTargets);
      tr.appendChild(tdNote);
      tr.appendChild(tdDel);
      cueBody.appendChild(tr);
    });
  }

  // ========== PATCH EDITOR ==========
  var PRESETS = {
    qlab5: { port: 53000, protocol: "tcp", go_template: "/cue/{cue}/start", ping_template: "/version", expect_reply: true },
    grandma3: { port: 8000, protocol: "udp", go_template: "/gma3/cmd", go_args: "Go+ Sequence 1", ping_template: "", expect_reply: false },
    theatremix: { port: 8000, protocol: "udp", go_template: "/TheatreMix/Go", ping_template: "", expect_reply: false },
    custom: { port: 8000, protocol: "udp", go_template: "", ping_template: "", expect_reply: false },
  };

  var deviceBody = document.getElementById("deviceBody");
  var devices = [];
  var currentPatchFilename = "";

  async function loadPatchList() {
    var res = await fetch("/api/patches");
    var data = await res.json();
    var sel = document.getElementById("patchFileSelect");
    sel.innerHTML = '<option value="">— select —</option>';
    data.files.forEach(function (f) {
      var opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      sel.appendChild(opt);
    });
  }

  document.getElementById("patchLoadBtn").addEventListener("click", async function () {
    var filename = document.getElementById("patchFileSelect").value;
    if (!filename) return;
    var res = await fetch("/api/patch/" + encodeURIComponent(filename));
    if (!res.ok) { showStatus("patchStatusMsg", "File not found", true); return; }
    var data = await res.json();
    currentPatchFilename = filename;
    document.getElementById("patchName").value = data.name || "";
    devices = (data.devices || []).map(function (d) {
      return {
        name: d.name || "",
        preset: d.preset || "custom",
        ip: d.ip || "",
        port: d.port || 8000,
        protocol: d.protocol || "udp",
        go_template: d.go_template || "",
        go_args: (d.go_args || []).join(", "),
        ping_template: d.ping_template || "",
        expect_reply: d.expect_reply !== false,
      };
    });
    renderDevices();
    showStatus("patchStatusMsg", "Loaded " + filename);
  });

  document.getElementById("patchNewBtn").addEventListener("click", function () {
    var name = document.getElementById("newPatchFilename").value.trim();
    if (!name) return;
    currentPatchFilename = name.endsWith(".json") ? name : name + ".json";
    document.getElementById("patchName").value = "";
    devices = [];
    renderDevices();
    showStatus("patchStatusMsg", "New patch: " + currentPatchFilename);
  });

  document.getElementById("addDeviceBtn").addEventListener("click", function () {
    devices.push({
      name: "", preset: "custom", ip: "", port: 8000, protocol: "udp",
      go_template: "", go_args: "", ping_template: "", expect_reply: false,
    });
    renderDevices();
  });

  document.getElementById("patchSaveBtn").addEventListener("click", async function () {
    readDevicesFromDOM();
    if (!currentPatchFilename) { showStatus("patchStatusMsg", "No filename set", true); return; }
    var data = {
      name: document.getElementById("patchName").value.trim(),
      devices: devices.map(function (d) {
        var dev = {
          name: d.name,
          preset: d.preset,
          ip: d.ip,
          port: parseInt(d.port) || 8000,
          protocol: d.protocol,
          go_template: d.go_template,
          ping_template: d.ping_template,
          expect_reply: d.expect_reply,
        };
        var args = d.go_args.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        if (args.length > 0) dev.go_args = args;
        return dev;
      }),
    };
    var res = await fetch("/api/patch/" + encodeURIComponent(currentPatchFilename), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      showStatus("patchStatusMsg", "Saved!");
      loadPatchList();
    } else {
      var err = await res.json();
      showStatus("patchStatusMsg", "Errors: " + (err.errors || []).join("; "), true);
    }
  });

  function readDevicesFromDOM() {
    var rows = deviceBody.querySelectorAll("tr");
    devices = [];
    rows.forEach(function (row) {
      var inputs = row.querySelectorAll("input");
      var selects = row.querySelectorAll("select");
      devices.push({
        name: inputs[0].value,
        preset: selects[0].value,
        ip: inputs[1].value,
        port: inputs[2].value,
        protocol: selects[1].value,
        go_template: inputs[3].value,
        go_args: inputs[4].value,
        ping_template: inputs[5].value,
        expect_reply: inputs[6].checked,
      });
    });
  }

  function makeSelect(options, selected, className) {
    var sel = document.createElement("select");
    if (className) sel.className = className;
    options.forEach(function (val) {
      var opt = document.createElement("option");
      opt.value = val;
      opt.textContent = val;
      if (val === selected) opt.selected = true;
      sel.appendChild(opt);
    });
    return sel;
  }

  function renderDevices() {
    deviceBody.innerHTML = "";
    devices.forEach(function (dev, idx) {
      var tr = document.createElement("tr");

      var tdName = document.createElement("td");
      var inName = document.createElement("input");
      inName.type = "text";
      inName.value = dev.name;
      inName.placeholder = "SOUND";
      tdName.appendChild(inName);

      var tdPreset = document.createElement("td");
      var selPreset = makeSelect(["custom", "qlab5", "grandma3", "theatremix"], dev.preset, "preset-sel");
      tdPreset.appendChild(selPreset);

      var tdIp = document.createElement("td");
      var inIp = document.createElement("input");
      inIp.type = "text";
      inIp.value = dev.ip;
      inIp.placeholder = "192.168.1.50";
      tdIp.appendChild(inIp);

      var tdPort = document.createElement("td");
      var inPort = document.createElement("input");
      inPort.type = "number";
      inPort.value = dev.port;
      inPort.min = "1";
      inPort.max = "65535";
      tdPort.appendChild(inPort);

      var tdProto = document.createElement("td");
      var selProto = makeSelect(["udp", "tcp"], dev.protocol);
      tdProto.appendChild(selProto);

      var tdGoTpl = document.createElement("td");
      var inGoTpl = document.createElement("input");
      inGoTpl.type = "text";
      inGoTpl.value = dev.go_template;
      inGoTpl.placeholder = "/cue/{cue}/start";
      tdGoTpl.appendChild(inGoTpl);

      var tdGoArgs = document.createElement("td");
      var inGoArgs = document.createElement("input");
      inGoArgs.type = "text";
      inGoArgs.value = dev.go_args;
      inGoArgs.placeholder = "arg1, arg2";
      tdGoArgs.appendChild(inGoArgs);

      var tdPing = document.createElement("td");
      var inPing = document.createElement("input");
      inPing.type = "text";
      inPing.value = dev.ping_template;
      inPing.placeholder = "/version";
      tdPing.appendChild(inPing);

      var tdReply = document.createElement("td");
      tdReply.style.textAlign = "center";
      var inReply = document.createElement("input");
      inReply.type = "checkbox";
      inReply.checked = dev.expect_reply;
      tdReply.appendChild(inReply);

      var tdTest = document.createElement("td");
      var btnTest = document.createElement("button");
      btnTest.className = "btn-test";
      btnTest.textContent = "Test";
      tdTest.appendChild(btnTest);

      var tdDel = document.createElement("td");
      var btnDel = document.createElement("button");
      btnDel.className = "btn-del";
      btnDel.textContent = "✕";
      tdDel.appendChild(btnDel);

      tr.appendChild(tdName);
      tr.appendChild(tdPreset);
      tr.appendChild(tdIp);
      tr.appendChild(tdPort);
      tr.appendChild(tdProto);
      tr.appendChild(tdGoTpl);
      tr.appendChild(tdGoArgs);
      tr.appendChild(tdPing);
      tr.appendChild(tdReply);
      tr.appendChild(tdTest);
      tr.appendChild(tdDel);

      selPreset.addEventListener("change", function (e) {
        var preset = PRESETS[e.target.value];
        if (!preset) return;
        readDevicesFromDOM();
        var d = devices[idx];
        d.preset = e.target.value;
        d.port = preset.port;
        d.protocol = preset.protocol;
        d.go_template = preset.go_template;
        d.go_args = preset.go_args || "";
        d.ping_template = preset.ping_template;
        d.expect_reply = preset.expect_reply;
        renderDevices();
      });

      btnTest.addEventListener("click", async function () {
        readDevicesFromDOM();
        var d = devices[idx];
        btnTest.textContent = "...";
        btnTest.classList.remove("ok", "fail");
        try {
          var payload = {
            name: "test", ip: d.ip, port: parseInt(d.port) || 8000,
            protocol: d.protocol, ping_template: d.ping_template,
            expect_reply: d.expect_reply,
          };
          var res = await fetch("/api/patch/_probe_test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          var result = await res.json();
          btnTest.textContent = result.trust || "?";
          btnTest.classList.add(result.probe === "confirmed" ? "ok" : result.probe === "unverified" ? "" : "fail");
        } catch (err) {
          btnTest.textContent = "err";
          btnTest.classList.add("fail");
        }
      });

      btnDel.addEventListener("click", function () {
        readDevicesFromDOM();
        devices.splice(idx, 1);
        renderDevices();
      });

      deviceBody.appendChild(tr);
    });
  }

  // ========== SHARED ==========
  function showStatus(elId, msg, isError) {
    var el = document.getElementById(elId);
    el.textContent = msg;
    el.className = "status-msg" + (isError ? " error" : "");
  }

  loadFileList();
  loadPatchList();
})();
