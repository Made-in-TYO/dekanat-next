// Небольшие улучшения интерфейса. Без них все страницы и формы тоже работают.
(function () {
  "use strict";
  var root = document.documentElement;

  // ---------- Тема оформления ----------
  var THEMES = ["auto", "light", "dark"];
  var LABELS = { auto: "Тема: как в системе", light: "Тема: светлая", dark: "Тема: темная" };
  function getTheme() { return root.getAttribute("data-theme") || "auto"; }
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try { localStorage.setItem("dn-theme", t); } catch (e) {}
    syncTheme();
  }
  function syncTheme() {
    var t = getTheme();
    document.querySelectorAll("[data-theme-label]").forEach(function (el) { el.textContent = LABELS[t]; });
    document.querySelectorAll("[data-set-theme]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-set-theme") === t ? "true" : "false");
    });
  }
  document.addEventListener("click", function (e) {
    var tg = e.target.closest("[data-theme-toggle]");
    if (tg) { setTheme(THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length]); }
    var st = e.target.closest("[data-set-theme]");
    if (st) { setTheme(st.getAttribute("data-set-theme")); }
  });
  syncTheme();

  // ---------- Обратный отсчет до пары (по времени вуза) ----------
  function plural(n, one, few, many) {
    n = Math.abs(n);
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return few;
    return many;
  }
  // Время вуза: Москва (UTC+3), независимо от часового пояса устройства
  function campusNow() { var d = new Date(); return new Date(d.getTime() + d.getTimezoneOffset() * 60000 + 3 * 3600000); }
  var reloading = false;
  function tick() {
    document.querySelectorAll("[data-countdown]").forEach(function (el) {
      var target = new Date(el.getAttribute("data-countdown"));
      var toEnd = el.getAttribute("data-countdown-mode") === "end";
      var mins = Math.floor((target - campusNow()) / 60000);
      // Пара началась или закончилась: обновляем страницу, чтобы табло показало актуальное занятие
      if (target - campusNow() <= 0) {
        // Не чаще раза в минуту: если часы устройства спешат, страница не уйдет в бесконечное обновление
        var last = 0;
        try { last = +sessionStorage.getItem("dn-board-reload") || 0; } catch (e) {}
        if (!reloading && Date.now() - last > 60000) {
          reloading = true;
          try { sessionStorage.setItem("dn-board-reload", String(Date.now())); } catch (e) {}
          window.location.reload();
        }
        return;
      }
      var pre = toEnd ? "" : "через ";
      if (mins < 60) { el.textContent = pre + Math.max(mins, 1) + " мин"; return; }
      if (mins < 1440) { var h = Math.floor(mins / 60), m = mins % 60; el.textContent = pre + h + " ч" + (m ? " " + m + " мин" : ""); return; }
      var n = campusNow();
      var d0 = new Date(n.getFullYear(), n.getMonth(), n.getDate());
      var d1 = new Date(target.getFullYear(), target.getMonth(), target.getDate());
      var days = Math.round((d1 - d0) / 86400000);
      el.textContent = days === 1 ? "завтра" : days === 2 ? "послезавтра" : "через " + days + " " + plural(days, "день", "дня", "дней");
    });
  }
  if (document.querySelector("[data-countdown]")) { tick(); setInterval(tick, 30000); }

  // ---------- Сообщение после действия ----------
  var toast = document.querySelector("[data-toast]");
  if (toast) {
    setTimeout(function () { toast.classList.add("is-hiding"); }, 4500);
    setTimeout(function () { toast.remove(); }, 5200);
    if (window.history.replaceState) {
      var u = new URL(window.location.href);
      u.searchParams.delete("ok");
      window.history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
    }
  }

  // ---------- Формы: защита от двойной отправки и состояние загрузки ----------
  document.querySelectorAll("form[data-submit-once]").forEach(function (f) {
    f.addEventListener("submit", function () {
      if (f.dataset.sent) { return; }
      f.dataset.sent = "1";
      var b = f.querySelector("[type=submit]");
      if (b) {
        b.setAttribute("aria-busy", "true");
        if (b.dataset.busyText) { b.dataset.label = b.innerHTML; b.textContent = b.dataset.busyText; }
        setTimeout(function () { b.disabled = true; }, 0);
      }
    });
  });

  // ---------- Показать пароль ----------
  document.querySelectorAll("[data-pw-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var i = document.getElementById(b.getAttribute("aria-controls"));
      var show = i.type === "password";
      i.type = show ? "text" : "password";
      b.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
      b.classList.toggle("is-on", show);
    });
  });

  // ---------- Печать справки ----------
  document.querySelectorAll("[data-print]").forEach(function (b) {
    b.addEventListener("click", function () { window.print(); });
  });

  // ---------- Калькулятор баллов (шкала ИТМО: 60 зачет, больше 90 отлично) ----------
  function gradeText(p, control) {
    if (control === "credit") return p >= 60 ? "зачтено" : "не зачтено";
    if (p < 60) return "неудовлетворительно";
    if (p <= 67) return "удовлетворительно (E)";
    if (p <= 74) return "удовлетворительно (D)";
    if (p <= 83) return "хорошо (C)";
    if (p <= 90) return "хорошо (B)";
    return "отлично (A)";
  }
  document.querySelectorAll("[data-calc]").forEach(function (box) {
    var base = parseFloat(box.getAttribute("data-points"));
    var control = box.getAttribute("data-control");
    var input = box.querySelector("input[type=range]");
    var out = box.querySelector("[data-calc-out]");
    function upd() {
      var add = parseFloat(input.value);
      var total = Math.min(103, Math.round(base + add));
      out.textContent = "Наберете еще " + add + " " + plural(add, "балл", "балла", "баллов") + ": итого " + total + " " + plural(total, "балл", "балла", "баллов") + ", это " + gradeText(total, control);
    }
    input.addEventListener("input", upd);
  });

  // ---------- Форма изменения расписания у сотрудника ----------
  document.querySelectorAll("[data-change-form]").forEach(function (f) {
    var sel = f.querySelector("[data-change-type]");
    function upd() {
      f.querySelectorAll("[data-for]").forEach(function (el) {
        var w = el.getAttribute("data-for");
        el.hidden = w === "any" ? sel.value === "revert" : w !== sel.value;
      });
    }
    sel.addEventListener("change", upd); upd();
  });

  // ---------- Количество экземпляров только для бумажной справки ----------
  var copies = document.querySelector("[data-copies]");
  if (copies) {
    var upd = function () {
      // Если выбора формата нет (справка бывает только бумажной), поле видно всегда
      var radios = document.querySelectorAll("input[data-fmt]");
      var paper = document.querySelector("input[data-fmt][value=paper]");
      copies.hidden = radios.length > 0 && !(paper && paper.checked);
    };
    document.querySelectorAll("input[data-fmt]").forEach(function (r) { r.addEventListener("change", upd); });
    upd();
  }

  // ---------- Подсказка для жюри ----------
  document.querySelectorAll("[data-tour-hide]").forEach(function (b) {
    b.addEventListener("click", function () {
      try { localStorage.setItem("dn-tour-hidden", "1"); } catch (e) {}
      root.setAttribute("data-tour", "hidden");
    });
  });

  // ---------- Работа как приложение на телефоне ----------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }
})();
