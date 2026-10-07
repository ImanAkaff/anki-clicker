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

  if (typeof navigator.getGamepads !== "function") {
    pycmd("ankiclicker:status:" + JSON.stringify({ api: false }));
    return;
  }
  pycmd("ankiclicker:status:" + JSON.stringify({ api: true }));

  var AXIS_THRESHOLD = 0.6; // how far a stick/d-pad axis must move to count
  var previous = {};         // controller index -> {buttonId: true/false}

  // Smooth scrolling with the stick. Python sets which axis scrolls (from the
  // add-on config) before this script runs; axis 1 is the left stick's
  // up/down on most controllers. Set to -1 in the config to turn it off.
  var SCROLL_AXIS = typeof window.ankiClickerScrollAxis === "number" ? window.ankiClickerScrollAxis : 1;
  var SCROLL_INVERT = window.ankiClickerScrollInvert === true;
  var DEADZONE = 0.2;    // ignore tiny drift: sticks rarely rest at exactly 0
  var MAX_SPEED = 28;    // pixels per frame at full tilt (~1700 px/second)

  function scrollWith(pad) {
    if (SCROLL_AXIS < 0 || SCROLL_AXIS >= pad.axes.length) return;
    var v = pad.axes[SCROLL_AXIS];
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
      if (a === SCROLL_AXIS) continue; // the scroll axis scrolls; it's not a button
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
})();
