// Тема применяется до отрисовки страницы, чтобы не было мигания
(function () {
  var t = "auto";
  try { t = localStorage.getItem("dn-theme") || "auto"; } catch (e) {}
  document.documentElement.setAttribute("data-theme", t);
})();
