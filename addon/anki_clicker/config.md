**Anki Clicker settings**

Easiest way to connect: **Tools → Anki Clicker → Connect…** and paste your code. You don't need to edit anything here.

- `server_url`: the address Azeem gave you, e.g. `https://anki-clicker.example.com`. Leave empty to keep everything on this computer only.
- `token`: your personal key from Azeem. Keep it private.
- `sync_enabled`: `true` to upload your review timings, `false` to stop.
- `button_map`: which controller button does what. Actions: `show_answer`, `again`, `hard`, `good`, `easy`, `undo`, `scroll_up`, `scroll_down`.
- `scroll_axis`: which stick axis scrolls the card smoothly (`1` = left stick up/down on most controllers; `-1` turns stick scrolling off).
- `scroll_invert`: `true` if pushing the stick up scrolls the wrong way.

Pressing any answer button while the question is showing just reveals the answer first, so you can't grade a card you haven't seen.

To find a button's number, press it during a review. If it isn't mapped yet, a message shows its number. Add it here, e.g. `"7": "good"`.

Only timings and button presses are uploaded, never the text of your cards.
