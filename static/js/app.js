// EM OS: the only JavaScript in the app. Everything else is Python + HTML.

// 0. Text in your language (the server puts the translations in the page)
const TEXTS = (function () {
  try { return JSON.parse(document.getElementById("js-texts").textContent); } catch (e) { return {}; }
})();
function t(text) { return TEXTS[text] || text; }

// 1. Pop-up "toast" messages the server sends after instant (htmx) actions
document.addEventListener("toast", function (event) {
  (event.detail.messages || []).forEach(showToast);
});

function showToast(text) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = text;          // textContent, never innerHTML: no code injection
  box.appendChild(toast);
  while (box.children.length > 3) box.firstElementChild.remove();   // never cover the screen
  setTimeout(function () { toast.classList.add("hide"); }, 2600);
  setTimeout(function () { toast.remove(); }, 3000);
}

// Lets the CSS know JavaScript is running (e.g. to hide buttons we press for you)
document.documentElement.classList.add("js");

// 2. A tiny vibration when you tick a habit (Android phones support this)
document.addEventListener("click", function (event) {
  if (event.target.closest(".tick") && navigator.vibrate) navigator.vibrate(12);
});

// 3. Register the service worker: makes EM OS installable and gives an offline screen
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js");
  });
}

// 4. Daily reminders (Settings page): turn notifications on or off for this device
const pushPanel = document.getElementById("push-panel");
if (pushPanel) setUpReminders(pushPanel);

function setUpReminders(panel) {
  const status = panel.querySelector("[data-push-status]");
  const onButton = panel.querySelector("[data-push-on]");
  const offButton = panel.querySelector("[data-push-off]");
  const testButton = panel.querySelector("[data-push-test]");
  const isIPhone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;

  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    status.textContent = isIPhone && !installed
      ? t("On iPhone: tap Share → Add to Home Screen, open EM OS from your Home Screen, then come back here.")
      : t("This browser can't show notifications. Try Chrome, Edge, Firefox or Safari.");
    return;
  }

  function show(subscribed) {
    onButton.hidden = subscribed;
    offButton.hidden = !subscribed;
    testButton.hidden = !subscribed;
    if (Notification.permission === "denied") {
      status.textContent = t("Notifications are blocked for EM OS. Allow them in your browser or phone settings, then reload this page.");
      onButton.hidden = true;
    } else {
      status.textContent = subscribed
        ? t("✅ Reminders are on for this device.")
        : t("Reminders are off for this device.");
    }
  }

  function updatePage(data) {
    // Show the new reminder time and device count without reloading the page
    const select = document.getElementById("reminder_time");
    if (select && data.reminder_time) select.value = data.reminder_time;
    const count = document.querySelector("[data-push-count]");
    if (count && data.devices_text) count.textContent = data.devices_text;
  }

  function send(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || t("Something went wrong."));
        return data;
      });
    });
  }

  function keyBytes(base64) {
    const padded = (base64 + "===".slice((base64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), function (c) { return c.charCodeAt(0); });
  }

  navigator.serviceWorker.ready.then(function (registration) {
    registration.pushManager.getSubscription().then(function (sub) {
      // Keep the server up to date in case the browser renewed the subscription
      if (sub) send("/reminders/subscribe", sub.toJSON()).catch(function () {});
      show(Boolean(sub));
    });

    onButton.addEventListener("click", async function () {
      onButton.disabled = true;
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") { show(false); return; }
        const sub = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: keyBytes(panel.dataset.key),
        });
        updatePage(await send("/reminders/subscribe", sub.toJSON()));
        show(true);
        showToast(t("🔔 Reminders are on!"));
      } catch (error) {
        status.textContent = "Couldn't turn on reminders: " + error.message;
      } finally {
        onButton.disabled = false;
      }
    });

    offButton.addEventListener("click", async function () {
      const sub = await registration.pushManager.getSubscription();
      if (sub) {
        await send("/reminders/unsubscribe", { endpoint: sub.endpoint }).then(updatePage).catch(function () {});
        await sub.unsubscribe();
      }
      show(false);
      showToast(t("Reminders turned off for this device."));
    });

    testButton.addEventListener("click", function () {
      send("/reminders/test")
        .then(function (data) {
          showToast(data.sent ? t("Test sent! Check your notifications.") : t("No devices got it. Try turning reminders off and on."));
        })
        .catch(function (error) { showToast(error.message); });
    });
  });
}

// 5. 3-tap check-in (Today): saves by itself once energy, mood and focus are all picked
document.addEventListener("change", function (event) {
  const form = event.target.closest("form[data-autosubmit]");
  if (!form) return;
  const groups = new Set(Array.from(form.querySelectorAll("input[type=radio]")).map(function (i) { return i.name; }));
  const picked = Array.from(groups).every(function (name) {
    return form.querySelector("input[name='" + name + "']:checked");
  });
  if (navigator.vibrate) navigator.vibrate(8);
  if (picked) setTimeout(function () { form.requestSubmit(); }, 250);   // a moment to see the third tap
});

// 6. Focus timer: counts down to the time the server gave us, then logs the session
const focusTimer = document.getElementById("focus-timer");
if (focusTimer) {
  const endsAt = Number(focusTimer.dataset.endsAt);
  const startedAt = Number(focusTimer.dataset.startedAt);
  const label = focusTimer.querySelector("[data-remaining]");
  const dial = focusTimer.querySelector(".focus-dial");
  const finishForm = focusTimer.querySelector("[data-finish]");
  const title = document.title;
  let done = false;

  function chime() {
    try {
      const audio = new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.25, 0.5].forEach(function (delay) {
        const tone = audio.createOscillator();
        const volume = audio.createGain();
        tone.frequency.value = 880;
        volume.gain.setValueAtTime(0.15, audio.currentTime + delay);
        volume.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + delay + 0.2);
        tone.connect(volume).connect(audio.destination);
        tone.start(audio.currentTime + delay);
        tone.stop(audio.currentTime + delay + 0.2);
      });
    } catch (error) { /* sound isn't allowed: that's fine */ }
  }

  function tick() {
    const left = Math.max(0, endsAt - Date.now());
    const minutes = Math.floor(left / 60000);
    const seconds = Math.floor((left % 60000) / 1000);
    const text = minutes + ":" + String(seconds).padStart(2, "0");
    label.textContent = text;
    document.title = text + " · " + t("Focus");
    dial.style.setProperty("--p", Math.min(100, ((Date.now() - startedAt) / (endsAt - startedAt)) * 100));
    if (left === 0 && !done) {
      done = true;
      document.title = title;
      chime();
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      setTimeout(function () { finishForm.submit(); }, 1500);
    }
  }
  tick();
  setInterval(tick, 1000);
}

// 7. Celebrations: confetti and the level-up screen
const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function confetti() {
  if (calm || document.querySelector(".confetti")) return;      // no motion for people who asked for none
  const canvas = document.createElement("canvas");
  canvas.className = "confetti";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const scale = window.devicePixelRatio || 1;
  canvas.width = innerWidth * scale;
  canvas.height = innerHeight * scale;
  ctx.scale(scale, scale);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#4a9eff";
  const colors = [accent, "#22c55e", "#f59e0b", "#ec4899", "#a855f7", "#ffffff"];
  const pieces = Array.from({ length: 160 }, function () {
    return {
      x: innerWidth / 2 + (Math.random() - 0.5) * 120, y: innerHeight * 0.35,
      vx: (Math.random() - 0.5) * 14, vy: -Math.random() * 14 - 4,
      size: 5 + Math.random() * 6, spin: Math.random() * 6, turn: (Math.random() - 0.5) * 0.3,
      color: colors[Math.floor(Math.random() * colors.length)],
    };
  });
  const start = performance.now();
  (function frame(now) {
    const t = now - start;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    ctx.globalAlpha = Math.max(0, 1 - t / 2800);
    pieces.forEach(function (p) {
      p.vy += 0.35; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.spin += p.turn;
      ctx.save();
      ctx.translate(p.x, p.y); ctx.rotate(p.spin);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    });
    if (t < 2800) requestAnimationFrame(frame); else canvas.remove();
  })(start);
  if (navigator.vibrate) navigator.vibrate([30, 40, 30]);
}

function levelUp(info) {
  const old = document.querySelector(".level-up");
  if (old) old.remove();
  const overlay = document.createElement("div");
  overlay.className = "level-up";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", t("Level up"));
  const card = document.createElement("div");
  card.className = "level-up-card";
  function add(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    el.textContent = text;                   // textContent only: safe
    card.appendChild(el);
    return el;
  }
  add("div", "level-up-eyebrow", t("LEVEL UP!"));
  add("div", "level-up-badge", String(info.level));
  add("div", "level-up-title", info.title);
  add("p", "level-up-text", t("You reached level {level} with {xp} XP. {to_next} XP to the next level.")
      .replace("{level}", info.level).replace("{xp}", info.xp).replace("{to_next}", info.to_next));
  (info.unlocks || []).forEach(function (thing) { add("p", "level-up-unlock", t("🔓 New: {thing}").replace("{thing}", thing)); });
  const button = add("button", "level-up-close", t("Keep going →"));
  function close() { overlay.remove(); document.removeEventListener("keydown", onKey); }
  function onKey(event) { if (event.key === "Escape") close(); }
  button.addEventListener("click", close);
  overlay.addEventListener("click", function (event) { if (event.target === overlay) close(); });
  document.addEventListener("keydown", onKey);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  button.focus();
}

// From instant (htmx) actions...
document.addEventListener("confetti", function () { confetti(); });
document.addEventListener("levelUp", function (event) { levelUp(event.detail); });
// ...and from normal page loads (saved by the server for this page)
const waiting = document.getElementById("celebrations");
if (waiting) {
  try {
    const found = JSON.parse(waiting.textContent || "{}");
    if (found.levelUp) levelUp(found.levelUp);
    if (found.confetti) setTimeout(confetti, 200);
  } catch (error) { /* nothing to celebrate */ }
}

// 8. Wide scrolling boxes (the year heatmap) start at the newest end
document.querySelectorAll("[data-scroll-end]").forEach(function (box) { box.scrollLeft = box.scrollWidth; });

// 9. Weekly recap: draw the card as a picture (1080×1350) to share or save
const recapData = document.getElementById("recap-data");
if (recapData) {
  const recap = JSON.parse(recapData.textContent);
  const shareButton = document.querySelector("[data-recap-share]");
  const saveButton = document.querySelector("[data-recap-save]");

  function drawRecap() {
    const canvas = document.createElement("canvas");
    canvas.width = 1080; canvas.height = 1350;
    const c = canvas.getContext("2d");
    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue("--accent").trim() || "#4a9eff";
    const font = "system-ui, 'Segoe UI', sans-serif";

    const sky = c.createLinearGradient(0, 0, 1080, 1350);
    sky.addColorStop(0, "#0d1117"); sky.addColorStop(1, "#1b1438");
    c.fillStyle = sky; c.fillRect(0, 0, 1080, 1350);
    const glow = c.createRadialGradient(880, 160, 20, 880, 160, 600);
    glow.addColorStop(0, accent + "88"); glow.addColorStop(1, "transparent");
    c.fillStyle = glow; c.fillRect(0, 0, 1080, 1350);

    c.fillStyle = accent; c.font = "800 44px " + font; c.fillText("EM OS", 80, 130);
    c.fillStyle = "#ffffff"; c.font = "800 96px " + font;
    c.fillText(recap.heading, 80, 260);
    c.fillStyle = "#94a3b8"; c.font = "500 40px " + font; c.fillText(recap.range, 80, 325);

    recap.stats.forEach(function (stat, i) {
      const x = 80 + (i % 2) * 470, y = 400 + Math.floor(i / 2) * 260;
      c.fillStyle = "#ffffff12";
      c.beginPath(); c.roundRect(x, y, 450, 230, 32); c.fill();
      c.fillStyle = "#ffffff"; c.font = "60px " + font; c.fillText(stat[0], x + 36, y + 90);   // solid, so emoji aren't faded
      c.fillStyle = "#ffffff"; c.font = "800 84px " + font; c.fillText(String(stat[2]), x + 36, y + 180);
      c.fillStyle = "#cbd5e1"; c.font = "500 34px " + font; c.fillText(stat[1], x + 130, y + 78);
    });

    c.fillStyle = "#e2e8f0"; c.font = "600 38px " + font;
    c.fillText(recap.level_line, 80, 1230);
    if (recap.best_line) { c.textAlign = "right"; c.fillText(recap.best_line, 1000, 1230); c.textAlign = "left"; }
    c.fillStyle = "#94a3b8"; c.font = "500 30px " + font; c.fillText("em-os.onrender.com", 80, 1290);
    return canvas;
  }

  function asFile() {
    return new Promise(function (resolve) {
      drawRecap().toBlob(function (blob) {
        resolve(new File([blob], "em-os-week.png", { type: "image/png" }));
      }, "image/png");
    });
  }

  asFile().then(function (file) {
    if (navigator.canShare && navigator.canShare({ files: [file] })) shareButton.hidden = false;
  });
  shareButton.addEventListener("click", function () {
    asFile().then(function (file) {
      return navigator.share({ files: [file], title: t("My week on EM OS") });
    }).catch(function () { /* the person closed the share sheet */ });
  });
  saveButton.addEventListener("click", function () {
    asFile().then(function (file) {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(file);
      link.download = file.name;
      link.click();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 5000);
      showToast(t("📸 Image saved"));
    });
  });
}

// 10. Phone menu button: opens/closes the menu, works with the keyboard, says if it's open
const menuButton = document.querySelector(".hamburger");
const menuToggle = document.getElementById("nav-toggle");
if (menuButton && menuToggle) {
  function setMenu(open) {
    menuToggle.checked = open;
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute("aria-label", open ? t("Close menu") : t("Open menu"));
  }
  menuButton.addEventListener("click", function () {
    setMenu(!menuToggle.checked);
    if (menuToggle.checked) { const first = document.querySelector(".side-links a"); if (first) first.focus(); }
  });
  menuToggle.addEventListener("change", function () { setMenu(menuToggle.checked); });   // tapping the dark backdrop
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && menuToggle.checked) { setMenu(false); menuButton.focus(); }
  });
}
