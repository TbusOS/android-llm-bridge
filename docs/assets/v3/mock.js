/*
 * Runtime for the static v3 design mockups (docs/webui-preview-v3*.html).
 * Builds the shared shell (top app bar, navigation, assistant sheet) around
 * each page's <main>, and wires language, theme, menus, tabs, segmented
 * buttons, confirmations and snackbars. Sample data only; nothing is sent.
 * The React app implements the same shell as components (web/src/layouts).
 */
(function () {
  "use strict";
  var root = document.documentElement;
  var ICONS = window.ALB_ICONS || {};
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } }
  };

  function icon(name, cls) {
    return '<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  }

  /* ---------------- language ---------------- */
  var lang = store.get("alb-v3-lang") || ((navigator.language || "").toLowerCase().indexOf("zh") === 0 ? "zh" : "en");
  function t(zh, en) { return lang === "en" ? en : zh; }
  function applyLang() {
    document.querySelectorAll("[data-en]").forEach(function (el) {
      if (el.dataset.zh === undefined) el.dataset.zh = el.innerHTML;
      el.innerHTML = lang === "en" ? el.dataset.en : el.dataset.zh;
    });
    document.querySelectorAll("[data-en-ph]").forEach(function (el) {
      if (el.dataset.zhPh === undefined) el.dataset.zhPh = el.getAttribute("placeholder") || "";
      el.setAttribute("placeholder", lang === "en" ? el.dataset.enPh : el.dataset.zhPh);
    });
    document.querySelectorAll("[data-en-label]").forEach(function (el) {
      if (el.dataset.zhLabel === undefined) el.dataset.zhLabel = el.getAttribute("aria-label") || "";
      el.setAttribute("aria-label", lang === "en" ? el.dataset.enLabel : el.dataset.zhLabel);
    });
    root.lang = lang === "en" ? "en" : "zh-CN";
    hydrateIcons();
    document.dispatchEvent(new CustomEvent("alb:lang", { detail: lang }));
  }

  /* ---------------- theme ---------------- */
  var themes = ["system", "light", "dark"];
  var theme = store.get("alb-v3-theme") || "system";
  function applyTheme() {
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    var b = document.getElementById("theme-btn");
    if (b) {
      b.innerHTML = icon({ system: "monitor", light: "sun", dark: "moon" }[theme]);
      b.dataset.zhLabel = { system: "主题:跟随系统", light: "主题:浅色", dark: "主题:深色" }[theme];
      b.dataset.enLabel = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" }[theme];
      b.setAttribute("aria-label", lang === "en" ? b.dataset.enLabel : b.dataset.zhLabel);
      b.title = b.getAttribute("aria-label");
    }
  }

  function hydrateIcons(scope) {
    (scope || document).querySelectorAll("i[data-ic]").forEach(function (el) {
      var tmp = document.createElement("span");
      tmp.innerHTML = icon(el.dataset.ic, el.className);
      el.replaceWith(tmp.firstChild);
    });
  }

  /* ---------------- shell ---------------- */
  var NAV = [
    { zh: "设备", en: "Device", items: [
      ["overview", "overview", "总览", "Overview", "webui-preview-v3.html"],
      ["console", "terminal", "控制台", "Console", "webui-preview-v3-console.html"],
      ["logs", "logs", "日志", "Logs", "webui-preview-v3-logs.html"],
      ["timeline", "timeline", "时间轴", "Timeline", "webui-preview-v3-timeline.html", "new"],
      ["perf", "chart", "性能", "Performance", ""],
      ["files", "folder", "文件", "Files", ""],
      ["apps", "apps", "应用", "Apps", ""],
      ["screen", "phone", "屏幕", "Screen", ""],
      ["problems", "bug", "问题", "Problems", "", "4"],
      ["info", "info", "系统信息", "System info", ""],
      ["power", "battery", "电源", "Power", ""]
    ] },
    { zh: "烧录", en: "Flash", items: [
      ["flash", "bolt", "烧录", "Flash", "webui-preview-v3-flash.html"],
      ["board-config", "sliders", "配置分区", "Board config", ""]
    ] },
    { zh: "台架", en: "Bench", items: [
      ["bench", "usb", "台架", "Bench", "webui-preview-v3-bench.html"]
    ] },
    { zh: "助手", en: "Assistant", items: [
      ["chat", "chat", "对话", "Chat", "webui-preview-v3-chat.html"],
      ["playground", "flask", "模型试验", "Playground", ""],
      ["sessions", "history", "会话记录", "Sessions", ""]
    ] },
    { zh: "系统", en: "System", items: [
      ["doctor", "check-circle", "环境检查", "Health check", ""],
      ["audit", "shield", "操作记录", "Audit log", ""],
      ["connections", "link", "连接", "Connections", ""]
    ] }
  ];

  function navHtml(page) {
    var h = "";
    NAV.forEach(function (g, gi) {
      if (gi) h += '<span class="nav__sep" aria-hidden="true"></span>';
      h += '<div class="nav__label" data-en="' + g.en + '">' + g.zh + "</div>";
      g.items.forEach(function (it) {
        var badge = it[5] ? '<span class="nav__badge' + (it[5] === "new" ? " nav__badge--new" : "") + '"' + (it[5] === "new" ? ' data-en="new">新' : ">" + it[5]) + "</span>" : "";
        h += '<a class="nav__item" href="' + (it[4] || "#") + '"' + (it[0] === page ? ' aria-current="page"' : "") +
          (it[4] ? "" : ' data-todo="1"') + ">" + icon(it[1]) + '<span data-en="' + it[3] + '">' + it[2] + "</span>" + badge + "</a>";
      });
    });
    return h;
  }

  var TOPBAR =
    '<button class="icon-btn topbar__menu" id="menu-btn" aria-label="打开导航" data-en-label="Open navigation">' + icon("menu") + "</button>" +
    '<a class="brand" href="webui-preview-v3.html"><span class="brand__mark">' + icon("bridge") + '</span><span class="brand__name">alb</span><span class="brand__sub">android-llm-bridge</span></a>' +
    '<span class="menu-anchor"><button class="board-switch" data-menu="board-menu" aria-haspopup="menu" aria-expanded="false"><span class="dot dot--live"></span>board-a<span class="board-switch__meta mono">board_x</span>' + icon("chevron-down", "ic--sm") + "</button>" +
    '<div class="menu" id="board-menu" role="menu" hidden>' +
      '<button class="menu__item" role="menuitem" data-toast-zh="设计稿:串口切到 board-a。" data-toast-en="Draft: serial now points at board-a."><span class="dot dot--ok"></span><span>board-a · <span class="mono">COM7</span></span><span class="pill pill--info" data-en="current">当前</span><span class="menu__desc" data-en="adb · serial · in Android">adb · 串口 · 已进系统</span></button>' +
      '<button class="menu__item" role="menuitem" data-toast-zh="设计稿:串口切到 board-b,不用重启 hub。" data-toast-en="Draft: serial now points at board-b, no hub restart."><span class="dot dot--warn"></span><span>board-b · <span class="mono">COM9</span></span><span></span><span class="menu__desc" data-en="serial only · stopped in bootloader">只有串口 · 停在 bootloader</span></button>' +
      '<button class="menu__item" role="menuitem" data-toast-zh="设计稿:board-c 两天没有信号。" data-toast-en="Draft: board-c has been silent for two days."><span class="dot"></span><span>board-c · <span class="mono">COM12</span></span><span></span><span class="menu__desc" data-en="no signal for 2 days">两天没有信号</span></button>' +
      '<div class="menu__sep"></div><a class="menu__item" href="webui-preview-v3-bench.html">' + icon("sliders") + '<span data-en="Manage boards">管理板子</span><span></span></a>' +
    "</div></span>" +
    '<div class="chans" aria-label="channels">' +
      '<span class="chip chip--sm" title="adb"><span class="dot dot--ok"></span><b>adb</b><span>root · 89 ms</span></span>' +
      '<span class="chip chip--sm"><span class="dot dot--ok"></span><b data-en="serial">串口</b><span class="mono">COM7 115200</span></span>' +
      '<span class="chip chip--sm"><span class="dot"></span><b>fastboot</b><span data-en="not listed">未出现</span></span>' +
      '<span class="chip chip--sm"><span class="dot dot--ok"></span><b data-en="bench">台架</b><span>bench-01</span></span>' +
    "</div>" +
    '<span class="topbar__spacer"></span>' +
    '<button class="search" data-toast-zh="设计稿:这里打开命令面板(⌘K)。" data-toast-en="Draft: opens the command palette (⌘K).">' + icon("search") + '<span data-en="Search pages, commands, logs">搜索页面、命令或日志</span><kbd>⌘K</kbd></button>' +
    '<button class="icon-btn" id="assist-btn" aria-label="助手" data-en-label="Assistant" aria-expanded="false" title="助手 / Assistant">' + icon("spark") + "</button>" +
    '<button class="icon-btn" id="src-btn" aria-pressed="false" aria-label="显示数据来源" data-en-label="Show data sources" title="数据来源 / Data sources">' + icon("code") + "</button>" +
    '<button class="icon-btn" id="lang-btn" aria-label="切换到英文" data-en-label="Switch to Chinese" title="中文 / English">' + icon("lang") + "</button>" +
    '<button class="icon-btn" id="theme-btn"></button>';

  var SHEET =
    '<div class="sheet__head">' + icon("spark") + '<h2 data-en="Assistant">助手</h2><span class="pill pill--info">board-a</span>' +
      '<a class="icon-btn icon-btn--sm" href="webui-preview-v3-chat.html" aria-label="全屏打开" data-en-label="Open full screen" title="全屏 / Full screen">' + icon("external") + "</a>" +
      '<button class="icon-btn icon-btn--sm" id="sheet-close" aria-label="关闭" data-en-label="Close">' + icon("close") + "</button></div>" +
    '<div class="seg seg--sm sheet__src" role="group" aria-label="模型来源" data-en-label="Model source">' +
      '<button aria-pressed="true" data-src="host">' + icon("monitor", "ic--sm") + '<span data-en="Host">本机</span></button>' +
      '<button aria-pressed="false" data-src="api">' + icon("link", "ic--sm") + '<span data-en="Cloud API">云端 API</span></button>' +
      '<button aria-pressed="false" data-src="board">' + icon("chip", "ic--sm") + '<span data-en="On board">板上</span></button></div>' +
    '<label class="sheet__agent"><input type="checkbox" class="switch" checked><span><b data-en="Agent mode">Agent 模式</b><small data-en="May run alb tools. Read-only tools run at once; anything that changes the board asks you first.">可以调用 alb 工具。只读的直接跑,会改动板子的先问你。</small></span></label>' +
    '<div class="sheet__body convo" id="sheet-chat">' +
      '<div class="msg msg--user"><span class="ctx" data-en="@ Problem · kernel panic (pstore)">@ 问题 · 内核崩溃(pstore)</span><p data-en="What caused the kernel panic on the previous boot?">上一次开机的内核崩溃是怎么回事?</p></div>' +
      '<div class="tool">' + icon("check-circle", "ic--sm") + '<code>alb_diag_pstore</code><span class="pill" data-en="read-only · 0.4 s">只读 · 0.4 s</span><small>console-ramoops-0 · 2 records</small></div>' +
      '<div class="tool">' + icon("check-circle", "ic--sm") + '<code>alb_log_search "sensors_poll"</code><span class="pill" data-en="read-only · 1.1 s">只读 · 1.1 s</span><small>dmesg 9 · logcat 31</small></div>' +
      '<div class="msg msg--ai"><div class="msg__who">' + icon("robot", "ic--sm") + 'qwen2.5:7b · <span data-en="host">本机</span></div>' +
        '<p data-en="The crash is in sensors_poll+0x4c reading address 0x8: a null pointer plus an 8-byte offset. In the same second 14 SELinux denials stop the sensor HAL from opening iio:device0. Likely order: open fails, driver data never set, poll dereferences it. This is an inference.">崩溃点在 sensors_poll+0x4c,读的地址是 0x8:空指针加 8 字节偏移。同一秒有 14 条 SELinux 拒绝,传感器 HAL 打不开 iio:device0。推测顺序:打开失败 → 驱动数据没建 → poll 用到空指针。这一步是推测。</p>' +
        '<p class="cites"><span data-en="Evidence">依据</span> <a href="webui-preview-v3-timeline.html" class="cite">pstore:112</a><a href="webui-preview-v3-timeline.html" class="cite">dmesg 09:31:04.218</a></p></div>' +
      '<div class="confirm confirm--inline">' + icon("alert") + '<p data-en="To check it, reproduce once with SELinux permissive. Changes the board until reboot.">要验证,临时放开 SELinux 复现一次。会改变板子状态,重启后恢复。</p><code class="mono">setenforce 0</code><div class="confirm__actions"><button class="btn btn--sm btn--filled" data-toast-zh="设计稿:不会真的执行。" data-toast-en="Draft: nothing runs." data-en="Run">执行</button><button class="btn btn--sm btn--text" data-en="Not now">先不用</button></div></div>' +
    "</div>" +
    '<div class="sheet__body ondev" id="sheet-board" hidden><h3 data-en="Talk to a model on the board">和板子上跑的模型对话</h3><p data-en="alb reaches the board&#39;s local inference server through the device port channel; the bench agent does not change.">alb 经设备端口通道连到板上的推理服务,台架 agent 不用改。</p>' +
      '<ul class="list"><li class="list__item"><span class="list__lead">' + icon("chip") + '</span><span class="list__title">llama.cpp server</span><span class="list__trail"><span class="pill" data-en="not found">未发现</span></span><span class="list__meta mono">tcp:8080 /v1/chat/completions</span></li>' +
      '<li class="list__item"><span class="list__lead">' + icon("chip") + '</span><span class="list__title">Android AICore</span><span class="list__trail"><span class="pill" data-en="not found">未发现</span></span><span class="list__meta" data-en="needs a small bridge app on the board">需要板上装桥接应用</span></li></ul>' +
      '<button class="btn btn--tonal btn--sm" data-toast-zh="设计稿:会经 alb port 试 8080、8000、11434。" data-toast-en="Draft: tries 8080, 8000 and 11434 through alb port.">' + icon("search") + '<span data-en="Look for a server">探测板上服务</span></button></div>' +
    '<form class="sheet__input composer" onsubmit="return false"><div class="row"><span class="ctx" id="sheet-ctx" data-en="@ Problem · kernel panic">@ 问题 · 内核崩溃</span><button type="button" class="ctx ctx--add" data-en="+ this page">+ 当前页面</button></div>' +
      '<label class="visually-hidden" for="sheet-q">Message</label><textarea class="textarea" id="sheet-q" rows="2" placeholder="问这块板子…用 @ 引用日志、问题或一段时间" data-en-ph="Ask about this board… use @ to cite logs, a problem or a time range"></textarea>' +
      '<div class="row row--between"><span class="t-body-small t-muted mono" data-en="qwen2.5:7b · host · 6.2k / 32k tokens">qwen2.5:7b · 本机 · 6.2k / 32k tokens</span><button class="btn btn--filled btn--sm" type="submit" data-toast-zh="设计稿:不会真的发给模型。" data-toast-en="Draft: nothing is sent to a model.">' + icon("send", "ic--sm") + '<span data-en="Send">发送</span></button></div></form>';

  var app = document.getElementById("app");
  var main = app.querySelector("main");
  var page = app.dataset.page;
  var top = document.createElement("header"); top.className = "topbar"; top.innerHTML = TOPBAR;
  var nav = document.createElement("nav"); nav.className = "nav"; nav.setAttribute("aria-label", "Sections"); nav.innerHTML = navHtml(page);
  var sheet = document.createElement("aside"); sheet.className = "sheet"; sheet.id = "sheet"; sheet.hidden = true; sheet.setAttribute("aria-label", "Assistant"); sheet.innerHTML = SHEET;
  var scrim = document.createElement("div"); scrim.className = "scrim"; scrim.hidden = true;
  var snack = document.createElement("div"); snack.className = "snackbar"; snack.setAttribute("role", "status"); snack.hidden = true;
  app.insertBefore(top, main); app.insertBefore(nav, main); app.appendChild(sheet);
  document.body.appendChild(scrim); document.body.appendChild(snack);

  /* ---------------- behaviour ---------------- */
  var snackTimer = 0;
  function toast(msg) {
    snack.textContent = msg; snack.hidden = false;
    clearTimeout(snackTimer); snackTimer = setTimeout(function () { snack.hidden = true; }, 2800);
  }

  function navMode() {
    var w = window.innerWidth;
    app.dataset.nav = w >= 1200 ? "drawer" : "rail";
    if (w >= 600) app.dataset.navOpen = "false";
  }
  window.addEventListener("resize", navMode);

  function setSheet(open) {
    sheet.hidden = !open;
    app.dataset.sheet = open ? "open" : "closed";
    scrim.hidden = !(open && window.innerWidth < 1200);
    document.getElementById("assist-btn").setAttribute("aria-expanded", String(open));
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest("button, a, [data-toast-zh]");
    if (!el) { closeMenus(); return; }

    if (el.dataset.menu) {
      e.stopPropagation();
      var m = document.getElementById(el.dataset.menu), open = m.hidden;
      closeMenus(); m.hidden = !open; el.setAttribute("aria-expanded", String(open));
      return;
    }
    if (!el.closest(".menu")) closeMenus();

    if (el.id === "menu-btn") { app.dataset.navOpen = app.dataset.navOpen === "true" ? "false" : "true"; scrim.hidden = app.dataset.navOpen !== "true"; return; }
    if (el.id === "assist-btn") { setSheet(sheet.hidden); return; }
    if (el.id === "sheet-close") { setSheet(false); return; }
    if (el.id === "lang-btn") { lang = lang === "en" ? "zh" : "en"; store.set("alb-v3-lang", lang); applyLang(); applyTheme(); return; }
    if (el.id === "theme-btn") { theme = themes[(themes.indexOf(theme) + 1) % 3]; store.set("alb-v3-theme", theme); applyTheme(); return; }
    if (el.id === "src-btn") {
      var on = !document.body.classList.contains("show-src");
      document.body.classList.toggle("show-src", on); el.setAttribute("aria-pressed", String(on)); return;
    }
    if (el.dataset.assist) {
      var ctx = document.getElementById("sheet-ctx");
      ctx.dataset.zh = "@ " + el.dataset.assist; ctx.dataset.en = "@ " + (el.dataset.assistEn || el.dataset.assist);
      ctx.textContent = lang === "en" ? ctx.dataset.en : ctx.dataset.zh;
      setSheet(true); return;
    }
    if (el.dataset.confirm) { var c = document.getElementById(el.dataset.confirm); c.hidden = false; var go = c.querySelector(".btn--danger, .btn--filled"); if (go) go.focus(); return; }
    if (el.hasAttribute("data-confirm-cancel")) { el.closest(".confirm").hidden = true; return; }
    if (el.dataset.src && el.closest(".sheet__src")) {
      el.parentNode.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b === el)); });
      document.getElementById("sheet-chat").hidden = el.dataset.src === "board";
      document.getElementById("sheet-board").hidden = el.dataset.src !== "board";
      return;
    }
    if (el.getAttribute("role") === "tab") {
      var list = el.closest("[role=tablist]");
      list.querySelectorAll("[role=tab]").forEach(function (tb) {
        var sel = tb === el; tb.setAttribute("aria-selected", String(sel));
        var p = tb.getAttribute("aria-controls"); if (p) document.getElementById(p).hidden = !sel;
      });
      return;
    }
    if (el.closest(".seg") && el.hasAttribute("aria-pressed") && !el.closest(".sheet__src")) {
      var seg = el.closest(".seg");
      if (seg.dataset.multi !== undefined) { el.setAttribute("aria-pressed", String(el.getAttribute("aria-pressed") !== "true")); }
      else seg.querySelectorAll("button").forEach(function (b) {
        var sel = b === el; b.setAttribute("aria-pressed", String(sel));
        var p = b.getAttribute("aria-controls"); if (p) document.getElementById(p).hidden = !sel;
      });
      return;
    }
    if (el.classList.contains("chip") && el.hasAttribute("aria-pressed")) { el.setAttribute("aria-pressed", String(el.getAttribute("aria-pressed") !== "true")); return; }
    if (el.dataset.todo) { e.preventDefault(); toast(t("设计稿:这一页沿用现有页面,只换新样式。", "Draft: this page keeps its current content in the new style.")); return; }
    if (el.dataset.toastZh) { e.preventDefault(); toast(t(el.dataset.toastZh, el.dataset.toastEn)); }
  });
  scrim.addEventListener("click", function () { setSheet(false); app.dataset.navOpen = "false"; scrim.hidden = true; });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeMenus(); if (!sheet.hidden) setSheet(false); }
  });
  function closeMenus() {
    document.querySelectorAll(".menu").forEach(function (m) { m.hidden = true; });
    document.querySelectorAll("[data-menu]").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
  }

  /* ---------------- charts ---------------- */
  function rng(seed) { return function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }
  function walk(seed, n, start, lo, hi, step) {
    var r = rng(seed), v = start, out = [];
    for (var i = 0; i < n; i++) { v += (r() - 0.5) * step; v = Math.max(lo, Math.min(hi, v)); out.push(v); }
    return out;
  }
  var NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) if (attrs[k] !== "") e.setAttribute(k, attrs[k]); return e; }
  function spark(svg, lines, lo, hi, opts) {
    opts = opts || {};
    var w = 200, h = opts.h || 44, pad = 3, span = hi - lo || 1;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!opts.small) [0.25, 0.5, 0.75].forEach(function (f) { svg.appendChild(svgEl("line", { x1: 0, x2: w, y1: h * f, y2: h * f, "class": "grid-line", "vector-effect": "non-scaling-stroke" })); });
    lines.forEach(function (data, idx) {
      var d = "";
      for (var i = 0; i < data.length; i++) {
        var x = (i / (data.length - 1)) * w, y = pad + (1 - (data[i] - lo) / span) * (h - 2 * pad);
        d += (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }
      if (idx === 0) svg.appendChild(svgEl("path", { d: d + "L" + w + " " + h + "L0 " + h + "Z", fill: "currentColor", "fill-opacity": "0.12", stroke: "none" }));
      svg.appendChild(svgEl("path", { d: d, fill: "none", stroke: idx === 0 ? "currentColor" : "", "class": idx ? "line2" : "", "stroke-width": "1.6", "stroke-dasharray": idx ? "3 3" : "", "vector-effect": "non-scaling-stroke" }));
    });
    var last = lines[0][lines[0].length - 1];
    svg.appendChild(svgEl("circle", { cx: w - 1, cy: (pad + (1 - (last - lo) / span) * (h - 2 * pad)).toFixed(1), r: opts.small ? 2.4 : 3, fill: "currentColor" }));
  }

  window.alb = { t: t, toast: toast, icon: icon, rng: rng, walk: walk, spark: spark, svgEl: svgEl, lang: function () { return lang; } };

  navMode();
  applyLang();
  applyTheme();
  setSheet(false);
})();
