/*
 * android-llm-bridge — timed figures on the feature pages.
 *
 *   <div data-play [data-play-loop] [data-play-rest="view=1"]>
 *     <p data-at="1200">…</p>                       shown 1.2 s after the start
 *     <i class="cue" data-at="1500" data-set="link=dial;step=1"></i>
 *                                                   sets data-link / data-step on the root
 *     <button data-play-btn="replay|end">           controls
 *     <button data-play-set="view=2">               jump: stop the timers, set data-view
 *
 * Every element is plain markup. Without this script, or with
 * prefers-reduced-motion, everything is simply visible and the root keeps the
 * attributes written in the HTML (or data-play-rest).
 */
(function () {
  "use strict";

  function parse(str) {
    return (str || "").split(";").map(function (pair) {
      var i = pair.indexOf("=");
      return i < 0 ? null : [pair.slice(0, i).trim(), pair.slice(i + 1).trim()];
    }).filter(function (p) { return p && p[0]; });
  }

  function init(root) {
    var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var loop = root.hasAttribute("data-play-loop");
    var setters = Array.prototype.slice.call(root.querySelectorAll("[data-play-set]"));
    var timers = [];
    var stoppedByUser = false;

    function items() {
      return Array.prototype.slice.call(root.querySelectorAll("[data-at]"))
        .map(function (el, i) { return { el: el, at: Number(el.getAttribute("data-at")), i: i }; })
        .sort(function (a, b) { return a.at - b.at || a.i - b.i; });
    }
    function paintSetters() {
      setters.forEach(function (b) {
        var on = parse(b.getAttribute("data-play-set")).every(function (p) {
          return root.getAttribute("data-" + p[0]) === p[1];
        });
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    function apply(str) {
      parse(str).forEach(function (p) { root.setAttribute("data-" + p[0], p[1]); });
      paintSetters();
    }
    function show(el) {
      if (el.hasAttribute("data-set")) apply(el.getAttribute("data-set"));
      if (!el.classList.contains("cue")) el.classList.add("is-on");
    }
    function stop() {
      timers.forEach(clearTimeout);
      timers = [];
      root.classList.remove("is-playing");
    }
    function end() {
      stop();
      items().forEach(function (it) { show(it.el); });
      if (root.hasAttribute("data-play-rest")) apply(root.getAttribute("data-play-rest"));
    }
    function play() {
      stop();
      var list = items();
      list.forEach(function (it) { it.el.classList.remove("is-on"); });
      var total = (list.length ? list[list.length - 1].at : 0) + 1200;
      root.style.setProperty("--play-ms", total + "ms");
      root.classList.add("is-armed");
      void root.offsetWidth;
      root.classList.add("is-playing");
      list.forEach(function (it) { timers.push(setTimeout(function () { show(it.el); }, it.at)); });
      timers.push(setTimeout(function () {
        root.classList.remove("is-playing");
        if (loop && !stoppedByUser) timers.push(setTimeout(play, 2600));
      }, total));
    }

    Array.prototype.forEach.call(root.querySelectorAll("[data-play-btn]"), function (b) {
      b.addEventListener("click", function () {
        if (b.getAttribute("data-play-btn") === "replay") { stoppedByUser = false; play(); }
        else { stoppedByUser = true; end(); }
      });
    });
    setters.forEach(function (b) {
      b.addEventListener("click", function () {
        stoppedByUser = true;
        stop();
        items().forEach(function (it) { if (!it.el.classList.contains("cue")) it.el.classList.add("is-on"); });
        apply(b.getAttribute("data-play-set"));
      });
    });

    paintSetters();
    if (reduce) { end(); return; }
    // Hide the lines right away so the finished state does not flash first.
    root.classList.add("is-armed");
    var first = root.querySelector('.cue[data-at="0"]');
    if (first) apply(first.getAttribute("data-set"));
    if (!("IntersectionObserver" in window)) { play(); return; }
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (en) { return en.isIntersecting; })) { io.disconnect(); play(); }
    }, { threshold: 0.25 });
    io.observe(root);
  }

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-play]"), init);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
