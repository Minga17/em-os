// EM OS: the only JavaScript in the app. Everything else is Python + HTML.

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
  setTimeout(function () { toast.classList.add("hide"); }, 2600);
  setTimeout(function () { toast.remove(); }, 3000);
}

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
      ? "On iPhone: tap Share → Add to Home Screen, open EM OS from your Home Screen, then come back here."
      : "This browser can't show notifications. Try Chrome, Edge, Firefox or Safari.";
    return;
  }

  function show(subscribed) {
    onButton.hidden = subscribed;
    offButton.hidden = !subscribed;
    testButton.hidden = !subscribed;
    if (Notification.permission === "denied") {
      status.textContent = "Notifications are blocked for EM OS. Allow them in your browser or phone settings, then reload this page.";
      onButton.hidden = true;
    } else {
      status.textContent = subscribed
        ? "✅ Reminders are on for this device."
        : "Reminders are off for this device.";
    }
  }

  function updatePage(data) {
    // Show the new reminder time and device count without reloading the page
    const select = document.getElementById("reminder_time");
    if (select && data.reminder_time) select.value = data.reminder_time;
    const count = document.querySelector("[data-push-count]");
    if (count && typeof data.devices === "number") {
      count.textContent = data.devices + (data.devices === 1 ? " device" : " devices") + " signed up for reminders.";
    }
  }

  function send(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || "Something went wrong.");
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
        showToast("🔔 Reminders are on!");
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
      showToast("Reminders turned off for this device.");
    });

    testButton.addEventListener("click", function () {
      send("/reminders/test")
        .then(function (data) {
          showToast(data.sent ? "Test sent! Check your notifications." : "No devices got it. Try turning reminders off and on.");
        })
        .catch(function (error) { showToast(error.message); });
    });
  });
}
