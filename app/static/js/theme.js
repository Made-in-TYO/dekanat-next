// Тема применяется до отрисовки страницы, чтобы не было мигания
(function () {
  var t = "auto";
  try { t = localStorage.getItem("dn-theme") || "auto"; } catch (e) {}
  document.documentElement.setAttribute("data-theme", t);
  try { if (localStorage.getItem("dn-tour-hidden") === "1") document.documentElement.setAttribute("data-tour", "hidden"); } catch (e) {}
})();
