(function () {
  const params = new URLSearchParams(location.search);
  const pwFromQR = params.get("pw") || "";
  let needsPassword = false;

  function generateUUID() {
    // crypto.randomUUID() requires secure context (HTTPS/localhost).
    // Phones connect over HTTP on LAN, so we need a fallback.
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      try { return crypto.randomUUID(); } catch (e) {}
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function getClientId() {
    let id = localStorage.getItem("cuelight_client_id");
    if (!id) {
      id = generateUUID();
      localStorage.setItem("cuelight_client_id", id);
    }
    return id;
  }

  async function init() {
    const res = await fetch("/api/info");
    const info = await res.json();
    if (info.password_enabled && !pwFromQR) {
      needsPassword = true;
      document.getElementById("passwordField").classList.add("visible");
    }
  }

  document.getElementById("joinBtn").addEventListener("click", async () => {
    const label = document.getElementById("labelInput").value.trim();
    if (!label) {
      showError("Please enter a position name.");
      return;
    }

    const pw = pwFromQR || document.getElementById("passwordInput").value.trim();
    if (needsPassword) {
      const res = await fetch("/api/check_password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      const result = await res.json();
      if (!result.ok) {
        showError("Incorrect password.");
        return;
      }
    }

    const checkRes = await fetch("/api/check_label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: label, client_id: getClientId() }),
    });
    const checkResult = await checkRes.json();
    if (!checkResult.ok) {
      showError(checkResult.reason || "Label already in use.");
      return;
    }

    localStorage.setItem("cuelight_label", label);
    localStorage.setItem("cuelight_client_id", getClientId());
    location.href = "/position";
  });

  document.getElementById("labelInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("joinBtn").click();
  });

  document.getElementById("callerLink").addEventListener("click", () => {
    location.href = "/";
  });

  document.getElementById("observerLink").addEventListener("click", async () => {
    const pw = pwFromQR || document.getElementById("passwordInput").value.trim();
    if (needsPassword) {
      const res = await fetch("/api/check_password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      const result = await res.json();
      if (!result.ok) {
        showError("Incorrect password.");
        return;
      }
    }
    localStorage.setItem("cuelight_observer_pw", pw);
    location.href = "/observer";
  });

  function showError(msg) {
    const el = document.getElementById("errorMsg");
    el.textContent = msg;
    el.classList.add("visible");
  }

  const savedError = localStorage.getItem("cuelight_join_error");
  if (savedError) {
    showError(savedError);
    localStorage.removeItem("cuelight_join_error");
  }

  init();
})();
