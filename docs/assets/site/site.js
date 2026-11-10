/*
 * android-llm-bridge — project site behaviour shared by every page:
 * language (EN / 中文), light / dark theme, the mobile menu and the top bar
 * shadow. Load it with `defer`; the inline snippet in each <head> sets the
 * language and theme before first paint so nothing flickers.
 */
(function () {
  "use strict";
  var root = document.documentElement;

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* private window or blocked storage: keep the default */ }
    return null;
  }

  // ---- language -------------------------------------------------------
  function setLang(lang) {
    root.setAttribute("lang", lang);
    store("alb-site-lang", lang);
    document.querySelectorAll("[data-lang-btn]").forEach(function (b) {
      b.textContent = lang.indexOf("zh") === 0 ? "EN" : "中";
      b.setAttribute("aria-label", lang.indexOf("zh") === 0 ? "Switch to English" : "切换到中文");
    });
    var t = document.querySelector('meta[name="alb-title-' + (lang.indexOf("zh") === 0 ? "zh" : "en") + '"]');
    if (t) document.title = t.getAttribute("content");
    document.dispatchEvent(new CustomEvent("alb:lang", { detail: lang }));
  }

  // ---- theme ----------------------------------------------------------
  function effectiveTheme() {
    var pinned = root.getAttribute("data-theme");
    if (pinned === "light" || pinned === "dark") return pinned;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintThemeButtons() {
    var dark = effectiveTheme() === "dark";
    document.querySelectorAll("[data-theme-btn]").forEach(function (b) {
      b.innerHTML = dark
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>';
      b.setAttribute("aria-label", dark ? "Light theme" : "Dark theme");
      b.setAttribute("title", dark ? "Light theme" : "Dark theme");
    });
  }

  function init() {
    setLang(root.getAttribute("lang") || "en");
    paintThemeButtons();

    document.querySelectorAll("[data-lang-btn]").forEach(function (b) {
      b.addEventListener("click", function () {
        setLang((root.getAttribute("lang") || "en").indexOf("zh") === 0 ? "en" : "zh-CN");
      });
    });
    document.querySelectorAll("[data-theme-btn]").forEach(function (b) {
      b.addEventListener("click", function () {
        var next = effectiveTheme() === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        store("alb-site-theme", next);
        paintThemeButtons();
      });
    });
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", paintThemeButtons);
    }

    var menuBtn = document.querySelector("[data-menu-btn]");
    var nav = document.querySelector(".nav");
    if (menuBtn && nav) {
      menuBtn.addEventListener("click", function () {
        var open = nav.classList.toggle("is-open");
        menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      });
      nav.addEventListener("click", function (e) {
        if (e.target.closest("a")) { nav.classList.remove("is-open"); menuBtn.setAttribute("aria-expanded", "false"); }
      });
    }

    var bar = document.querySelector(".topbar");
    if (bar) {
      var onScroll = function () { bar.classList.toggle("is-scrolled", window.scrollY > 8); };
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
    }

    // Tabs: <div role="tablist" data-tabs> with <button role="tab" aria-controls>.
    document.querySelectorAll("[data-tabs]").forEach(function (list) {
      var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
      function select(tab) {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          var panel = document.getElementById(t.getAttribute("aria-controls"));
          if (panel) panel.hidden = !on;
        });
      }
      tabs.forEach(function (t, i) {
        t.addEventListener("click", function () { select(t); });
        t.addEventListener("keydown", function (e) {
          var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : null;
          if (j === null) return;
          var n = tabs[(j + tabs.length) % tabs.length];
          select(n); n.focus(); e.preventDefault();
        });
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
