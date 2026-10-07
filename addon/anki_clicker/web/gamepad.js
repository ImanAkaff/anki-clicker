/*
 * Controller reader. Anki loads this into its review screen, which is a web
 * page running in a built-in Chromium browser, so we can use the browser's
 * Gamepad API. No drivers or libraries for your friends to install.
 *
 * How the Gamepad API works:
 *   - The browser does NOT fire an event when a button is pressed. Instead,
 *     navigator.getGamepads() returns a snapshot of every button right now.
 *   - So we POLL: take a snapshot every animation frame (~60 times/second).
 *   - Controllers stay invisible to the page until the user presses a button
 *     once (a privacy rule), so the first press "wakes" it up.
 *
 * Edge detection:
 *   A button held for half a second reads "pressed" in ~30 snapshots in a row.
 *   We only want ONE action per press, so we compare each snapshot with the
 *   previous one and act only on the not-pressed -> pressed change (the
 *   "rising edge"). Same idea as detecting a 0 -> 1 bit transition.
 *
 * Talking to Python:
 *   pycmd("...") is a function Anki provides inside its web views. It sends a
 *   string to the Python side, where __init__.py's on_js_message receives it.
 */
(function () {
  // The review screen can reload this script; make sure only one loop runs.
  if (window.__ankiClickerLoaded) return;
  window.__ankiClickerLoaded = true;

  // Anki creates pycmd (our channel to Python) a moment AFTER the page starts
  // loading, so calling it straight away throws an error and kills this whole
  // script. Wait for it, then start.
  (function whenBridgeReady() {
    if (typeof window.pycmd === "function") return start();
    setTimeout(whenBridgeReady, 100);
  })();

  function start() {

  if (typeof navigator.getGamepads !== "function") {
    pycmd("ankiclicker:status:" + JSON.stringify({ api: false }));
    return;
  }
  pycmd("ankiclicker:status:" + JSON.stringify({ api: true }));

  // Diagnostics for the first minute: report which controllers the page can
  // see, whenever that changes. Controllers stay hidden from web pages until
  // a button is pressed (a browser privacy rule), so "0 seen" before any
  // press is normal; "0 seen" AFTER pressing buttons means Windows/Anki isn't
  // passing the controller through.
  var lastSeen = null, diagStart = Date.now();
  function reportSeen() {
    var pads = Array.prototype.filter.call(navigator.getGamepads() || [], Boolean);
    var seen = pads.map(function (p) {
      return p.id + " [mapping=" + (p.mapping || "none") + ", buttons=" + p.buttons.length + ", axes=" + p.axes.length + "]";
    }).join(" | ") || "none";
    if (seen !== lastSeen) {
      lastSeen = seen;
      pycmd("ankiclicker:diag:" + "controllers seen: " + seen + " (focused=" + document.hasFocus() + ")");
    }
    if (Date.now() - diagStart < 60000) setTimeout(reportSeen, 1000);
  }
  reportSeen();

  var AXIS_THRESHOLD = 0.6; // how far a stick/d-pad axis must move to count
  var previous = {};         // controller index -> {buttonId: true/false}

  // Smooth scrolling with the stick. Python sets which axis scrolls (from the
  // add-on config) before this script runs; axis 1 is the left stick's
  // up/down on most controllers. Set to -1 in the config to turn it off.
  // "auto" (the default) picks per controller: a single Joy-Con held
  // sideways (Nintendo, only 2 axes) has its stick rotated 90°, so what you
  // feel as up/down arrives on axis 0; other controllers use axis 1.
  var SCROLL_SETTING = window.ankiClickerScrollAxis;
  var AUTO = typeof SCROLL_SETTING !== "number";
  // In "auto" mode the first stick (axes 0 and 1) scrolls whichever way it's
  // pushed hardest, so it works however the controller is held: a sideways
  // Joy-Con's stick is rotated 90 degrees compared with an Xbox pad's.
  function scrollAxisFor(pad) {
    if (!AUTO) return SCROLL_SETTING;
    if (pad.axes.length < 2) return 0;
    return Math.abs(pad.axes[0]) > Math.abs(pad.axes[1]) ? 0 : 1;
  }
  function isScrollAxis(pad, a) {
    return AUTO ? a <= 1 : a === SCROLL_SETTING;
  }
  var SCROLL_INVERT = window.ankiClickerScrollInvert === true;
  var DEADZONE = 0.2;    // ignore tiny drift: sticks rarely rest at exactly 0
  var MAX_SPEED = 28;    // pixels per frame at full tilt (~1700 px/second)

  function scrollWith(pad) {
    var axis = scrollAxisFor(pad);
    if (axis < 0 || axis >= pad.axes.length) return;
    var v = pad.axes[axis];
    if (Math.abs(v) < DEADZONE) return;
    // Squaring the tilt gives fine control near the middle and speed at the
    // edge: a light push creeps, a full push flies.
    var strength = (Math.abs(v) - DEADZONE) / (1 - DEADZONE);
    var speed = strength * strength * MAX_SPEED * (v > 0 ? 1 : -1) * (SCROLL_INVERT ? -1 : 1);
    window.scrollBy(0, speed);
  }

  // Turn one controller snapshot into {buttonId: pressed}.
  // Real buttons get their own index (0, 1, 2, ...). Some controllers, a
  // single sideways Joy-Con among them, report the d-pad or stick as AXES
  // (numbers from -1 to 1) instead of buttons, so we turn each axis direction
  // into a virtual button: axis 0 left = 100, axis 0 right = 101, axis 1 = 102/103...
  function readState(pad) {
    var state = {};
    for (var i = 0; i < pad.buttons.length; i++) {
      state[i] = pad.buttons[i].pressed;
    }
    for (var a = 0; a < pad.axes.length; a++) {
      if (isScrollAxis(pad, a)) continue; // the scroll stick scrolls; it's not a button
      state[100 + a * 2] = pad.axes[a] < -AXIS_THRESHOLD;
      state[101 + a * 2] = pad.axes[a] > AXIS_THRESHOLD;
    }
    return state;
  }

  function poll() {
    var pads = navigator.getGamepads() || [];
    for (var p = 0; p < pads.length; p++) {
      var pad = pads[p];
      if (!pad) continue;
      scrollWith(pad);
      var now = readState(pad);
      var before = previous[pad.index] || {};
      for (var id in now) {
        if (now[id] && !before[id]) {
          pycmd("ankiclicker:btn:" + id); // rising edge: one press
        }
      }
      previous[pad.index] = now;
    }
    requestAnimationFrame(poll); // schedule the next snapshot
  }

  window.addEventListener("gamepadconnected", function (e) {
    pycmd("ankiclicker:connected:" + JSON.stringify({
      id: e.gamepad.id,           // controller's name, e.g. "Joy-Con (R)"
      mapping: e.gamepad.mapping, // "standard" = Xbox-style layout
      buttons: e.gamepad.buttons.length,
      axes: e.gamepad.axes.length
    }));
  });

  requestAnimationFrame(poll);
  }
})();
