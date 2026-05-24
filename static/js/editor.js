(function () {
  const cueBody = document.getElementById("cueBody");
  let cues = [];
  let currentFilename = "";

  async function loadFileList() {
    const res = await fetch("/api/showfiles");
    const data = await res.json();
    const sel = document.getElementById("fileSelect");
    sel.innerHTML = '<option value="">— select —</option>';
    data.files.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      sel.appendChild(opt);
    });
  }

  document.getElementById("loadBtn").addEventListener("click", async () => {
    const filename = document.getElementById("fileSelect").value;
    if (!filename) return;
    const res = await fetch(`/api/showfile/${encodeURIComponent(filename)}`);
    if (!res.ok) { showStatus("File not found", true); return; }
    const data = await res.json();
    currentFilename = filename;
    document.getElementById("showName").value = data.show_name || "";
    document.getElementById("showVersion").value = data.version || 1;
    cues = (data.cues || []).map((c) => ({
      sequence: c.sequence,
      scene: c.scene || "",
      targets: (c.targets || []).map((t) => `${t.position}:${t.cue_number}`).join(", "),
      note: c.note || "",
    }));
    renderCues();
    showStatus(`Loaded ${filename}`);
  });

  document.getElementById("newBtn").addEventListener("click", () => {
    const name = document.getElementById("newFilename").value.trim();
    if (!name) return;
    currentFilename = name.endsWith(".json") ? name : name + ".json";
    document.getElementById("showName").value = "";
    document.getElementById("showVersion").value = "1";
    cues = [];
    renderCues();
    showStatus(`New file: ${currentFilename}`);
  });

  document.getElementById("addCueBtn").addEventListener("click", () => {
    const nextSeq = cues.length > 0 ? Math.max(...cues.map((c) => c.sequence)) + 1 : 1;
    cues.push({ sequence: nextSeq, scene: "", targets: "", note: "" });
    renderCues();
  });

  document.getElementById("saveBtn").addEventListener("click", async () => {
    readFromDOM();
    if (!currentFilename) { showStatus("No filename set", true); return; }

    const data = {
      show_name: document.getElementById("showName").value.trim(),
      version: parseInt(document.getElementById("showVersion").value) || 1,
      cues: cues.map((c) => ({
        sequence: c.sequence,
        scene: c.scene,
        targets: parseTargets(c.targets),
        note: c.note,
      })),
    };

    const res = await fetch(`/api/showfile/${encodeURIComponent(currentFilename)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    if (res.ok) {
      showStatus("Saved!");
      loadFileList();
    } else {
      const err = await res.json();
      showStatus("Errors: " + (err.errors || []).join("; "), true);
    }
  });

  function parseTargets(str) {
    return str
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const [position, ...rest] = s.split(":");
        return { position: position.trim(), cue_number: rest.join(":").trim() || "1" };
      });
  }

  function readFromDOM() {
    const rows = cueBody.querySelectorAll("tr");
    cues = [];
    rows.forEach((row) => {
      const inputs = row.querySelectorAll("input");
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
    cues.forEach((cue, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="number" value="${cue.sequence}"></td>
        <td><input type="text" value="${esc(cue.scene)}"></td>
        <td><input type="text" value="${esc(cue.targets)}" placeholder="LX:1, SND:1"></td>
        <td><input type="text" value="${esc(cue.note)}"></td>
        <td><button class="btn-del" data-idx="${idx}">✕</button></td>
      `;
      tr.querySelector(".btn-del").addEventListener("click", () => {
        readFromDOM();
        cues.splice(idx, 1);
        renderCues();
      });
      cueBody.appendChild(tr);
    });
  }

  function showStatus(msg, isError) {
    const el = document.getElementById("statusMsg");
    el.textContent = msg;
    el.className = "status-msg" + (isError ? " error" : "");
  }

  function esc(s) {
    return (s || "").replace(/"/g, "&quot;");
  }

  loadFileList();
})();
