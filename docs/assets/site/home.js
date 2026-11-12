/*
 * android-llm-bridge — home page behaviour (docs/index.html):
 *   - the replay in the hero: reveals the lines of one scenario on a timer
 *     and moves the little build-server / bench / board strip along with it;
 *   - the topology stepper and the "full picture / shipped today" switch;
 *   - copy buttons on code panels.
 * Everything the replay shows is plain markup. Without this script, or with
 * prefers-reduced-motion, all lines are simply visible.
 */
(function () {
  "use strict";

  // ---- hero replay ------------------------------------------------------
  function initStage(stage) {
    var scene = stage.querySelector(".scene");
    var tabs = Array.prototype.slice.call(stage.querySelectorAll("[data-scn-tab]"));
    var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var timers = [];
    var current = "1";

    function items(id) {
      var list = Array.prototype.slice.call(stage.querySelectorAll('.scn[data-scn="' + id + '"] [data-at]'));
      // stable sort by time: lines of the terminal and of the side pane interleave
      return list.map(function (el, i) { return { el: el, at: Number(el.getAttribute("data-at")), i: i }; })
        .sort(function (a, b) { return a.at - b.at || a.i - b.i; });
    }
    function applyCue(el) {
      (el.getAttribute("data-set") || "").split(";").forEach(function (pair) {
        var k = pair.split("=")[0].trim();
        if (k) scene.setAttribute("data-" + k, pair.slice(pair.indexOf("=") + 1).trim());
      });
    }
    function show(el) {
      if (el.classList.contains("cue")) applyCue(el);
      else el.classList.add("is-on");
    }
    function stop() {
      timers.forEach(clearTimeout);
      timers = [];
      stage.classList.remove("is-playing");
    }
    function finish(id) {
      stop();
      items(id).forEach(function (it) { show(it.el); });
    }
    function select(id, play) {
      stop();
      current = id;
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-scn-tab") === id;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      Array.prototype.forEach.call(stage.querySelectorAll(".scn"), function (b) {
        b.hidden = b.getAttribute("data-scn") !== id;
      });
      var list = items(id);
      if (!play || reduce) { list.forEach(function (it) { show(it.el); }); return; }

      list.forEach(function (it) { it.el.classList.remove("is-on"); });
      var total = (list.length ? list[list.length - 1].at : 0) + 900;
      stage.style.setProperty("--stage-ms", total + "ms");
      stage.classList.add("is-armed");
      void stage.offsetWidth;                 // restart the progress animation
      stage.classList.add("is-playing");
      list.forEach(function (it) {
        timers.push(setTimeout(function () { show(it.el); }, it.at));
      });
      timers.push(setTimeout(function () { stage.classList.remove("is-playing"); }, total));
    }

    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t.getAttribute("data-scn-tab"), true); });
      t.addEventListener("keydown", function (e) {
        var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : null;
        if (j === null) return;
        var n = tabs[(j + tabs.length) % tabs.length];
        select(n.getAttribute("data-scn-tab"), true);
        n.focus();
        e.preventDefault();
      });
    });
    Array.prototype.forEach.call(stage.querySelectorAll("[data-stage]"), function (b) {
      b.addEventListener("click", function () {
        if (b.getAttribute("data-stage") === "replay") select(current, true);
        else finish(current);
      });
    });

    if (reduce) { select("1", false); return; }
    // Hide the lines right away so the finished state does not flash before
    // the replay starts; start once the stage is on screen.
    items("1").forEach(function (it) { if (!it.el.classList.contains("cue")) it.el.classList.remove("is-on"); });
    stage.classList.add("is-armed");
    var first = stage.querySelector('.scn[data-scn="1"] .cue');
    if (first) applyCue(first);
    if (!("IntersectionObserver" in window)) { select("1", true); return; }
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (en) { return en.isIntersecting; })) {
        io.disconnect();
        select("1", true);
      }
    }, { threshold: 0.2 });
    io.observe(stage);
  }

  // ---- topology stepper -------------------------------------------------
  function initTopo(fig) {
    var stepBtns = Array.prototype.slice.call(document.querySelectorAll(".loop__step"));
    var panels = Array.prototype.slice.call(document.querySelectorAll(".loop__panel [data-panel]"));
    var parts = Array.prototype.slice.call(fig.querySelectorAll("[data-s]"));
    var current = 0;

    function show(n) {
      current = n;
      fig.classList.toggle("is-stepping", n > 0);
      parts.forEach(function (el) {
        var list = (el.getAttribute("data-s") || "").split(/\s+/);
        el.classList.toggle("on", n > 0 && list.indexOf(String(n)) !== -1);
      });
      stepBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(Number(b.dataset.step) === n)); });
      panels.forEach(function (p) { p.hidden = Number(p.dataset.panel) !== n; });
    }
    stepBtns.forEach(function (b) { b.addEventListener("click", function () { show(Number(b.dataset.step)); }); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-loop]"), function (b) {
      b.addEventListener("click", function () {
        var a = b.dataset.loop;
        if (a === "reset") show(0);
        else if (a === "next") show(current >= 6 ? 1 : current + 1);
        else show(current <= 1 ? 6 : current - 1);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll(".seg [data-mode]"), function (b) {
      b.addEventListener("click", function () {
        fig.setAttribute("data-mode", b.dataset.mode);
        Array.prototype.forEach.call(document.querySelectorAll(".seg [data-mode]"), function (o) {
          o.setAttribute("aria-pressed", String(o === b));
        });
      });
    });
  }

  // ---- copy buttons -----------------------------------------------------
  // <button class="copy" data-copy="text to copy">; \n in the attribute is a line break.
  function initCopy() {
    Array.prototype.forEach.call(document.querySelectorAll("button.copy[data-copy]"), function (b) {
      b.addEventListener("click", function () {
        var text = b.getAttribute("data-copy").replace(/\\n/g, "\n");
        var done = function () {
          b.classList.add("is-done");
          setTimeout(function () { b.classList.remove("is-done"); }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () {});
        }
      });
    });
  }

  function init() {
    var stage = document.getElementById("demo");
    if (stage) initStage(stage);
    var fig = document.getElementById("topo");
    if (fig) initTopo(fig);
    initCopy();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
