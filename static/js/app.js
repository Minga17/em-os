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
