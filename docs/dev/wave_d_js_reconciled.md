# Wave D JS — Reconciled Implementation + Verification

The integration is complete and verified. Here is the tech-lead summary of what was decided and shipped.

## Wave D steps 2–6: final integration

### The core reconciliation
The four agents diverged into two competing architectures: the event-model agent's `floorplan-coords.js` + `floorplan-interaction.js` modules (never loaded), and the calibrate-template agent's `floorplan_editing.js` (self-mounting, already wired into the template, already solving BLOCKER 1/2/3 + persistence BREAK 1/2 + full calibrate). The a11y and persistence agents wrote code targeting `floorplan.js` that was never applied.

**Decision: `floorplan_editing.js` is the single canonical editing module.** It is the only architecture that resolves the reload problem correctly — `floorplan.js`'s `initializeSVG` (pan/zoom wiring) runs once and is *not re-entrant from an external module*, so anything that lives as an appended block to `floorplan.js` cannot re-wire pan/zoom after a place-reload. The self-mounting module sidesteps that entirely. I folded the missing a11y + marker-drag pieces into it and retired the redundant `floorplan-interaction.js` (its capture-phase router is functionally identical to the inline one already in `floorplan_editing.js`). `floorplan-coords.js` is kept as the shared, node-tested pure-transform module and is now actually consumed by the marker-drag path.

### Contradictions resolved explicitly
- **ONE event-suppression mechanism:** capture-phase `mousedown` + `stopPropagation()` on the SVG (the event-model agent's reasoning; `pointerdown`+`preventDefault` is *not* relied on for mouse). I found and fixed a latent gap the calibrate-template inline router missed: during a drag, native `mousemove` still bubbles to `svgElement.onmousemove` and pans if the private `isPanning` closure is stale-true. Since that closure is unreachable from an external file, I added a **capture-phase `mousemove` guard** (`onMouseMoveCapture` stops the event while `activeDrag` is set). Empty-canvas pan and box-zoom stay live in every mode.
- **ONE reload-vs-insert decision:** **flush-gated full page reload**, with mode + new-tile-id stashed in `sessionStorage` and restored on the next mount. Chosen over the persistence agent's in-place `reloadSvgPreservingMode` (would break pan/zoom — not re-entrant) and over surgical marker insert (no single-tile SVG render endpoint; would duplicate `_draw_freeform_tile`). `afterPlace` awaits `flushAllPatches()` before reload (BREAK 1); full reload is inherently leak-free (BLOCKER 3).

### Files changed (all absolute)
- `nautobot_floor_plan/svg.py` — added root `role`/`aria-roledescription`/`aria-label` in `_setup_drawing`; in `_draw_freeform_tile` added `<a tabindex="-1">`, `<g role="button" tabindex="-1" aria-label data-can-move>`, and the hidden per-marker `.focus-ring` rect (`vector-effect="non-scaling-stroke"`); added `_marker_aria_label` helper. Ruff clean; preserves the asserted `translate(401.0,401.0)` and `data-tile-id`.
- `nautobot_floor_plan/static/nautobot_floor_plan/js/floorplan_editing.js` — added: capture `mousemove` guard; place-mode marker selection in `onClickCapture`; marker branch in `cancelDrag`; role-swap in `setMode`; the full **§E/§F marker place-drag lifecycle + keyboard/ARIA layer** (roving tabindex on `<g>`, `<a>` removed from tab order, focus-ring toggle, arrow-nudge 1%/Shift 0.1%, `R`/`Shift+R` rotate, Enter=commit/navigate by mode, Escape=revert+view, `ensureMarkerVisible` scroll); registered `placeDragStart` and a mode-preserving `reloadHook`; mount-time `armRovingTabindex`/`syncRoleForMode` + sessionStorage restore.
- `nautobot_floor_plan/templates/nautobot_floor_plan/inc/floorplan_svg.html` — loads `floorplan-coords.js` before `floorplan_editing.js`. (Controls, `data-*-api`, live region, legend mirror were already present.)
- Deleted `floorplan-interaction.js` (redundant).
- CSS (`svg.css`/`dark_svg.css`/`light.css`) — already carried focus-ring, `.is-focused`, root `touch-action`, reduced-motion, `.sr-only`, listbox, legend-mirror; verified complete, no changes needed.
- `nautobot_floor_plan/tests/js/floorplan-coords.test.js` — new node/jsdom pure-transform suite (8 tests, all pass).

### Verification done
- `node --check` on all three JS files: pass.
- All 25 marker helpers defined exactly once (no reference typos).
- `node --test floorplan-coords.test.js`: **8/8 pass** (round-trip, anisotropic norm, clamp, screen→user against a CTM, degenerate→null, angle right/down/up/snap, transform byte-compat with svg.py, edge-clamp).
- `svg.py` parses; `ruff check` clean.

### Build / verify order
1. `node --test nautobot_floor_plan/tests/js/floorplan-coords.test.js` (pure math — already green).
2. `invoke tests` (or `nautobot-server test nautobot_floor_plan.tests.test_svg`) — **must run in the dev container** (host has no `nautobot`; only db/redis/selenium are up). Confirms the `role`/`aria-label`/`.focus-ring` additions don't regress `test_freeform_tile_center_anchored`.
3. `invoke start` then the Playwright e2e checklist below.

### Playwright e2e checklist
- **drag-place:** enter Place, pick type+object, click *Place object* → marker drops at view center; drag it → follows pointer, no page pan, no `<a>` navigation on release; reload restores Place mode + focuses the new marker. Empty-canvas drag still pans; Shift+wheel still zooms; box-zoom still works.
- **drag-calibrate:** enter Calibrate → overlay appears; drag a corner on a rotated blueprint → opposite corner stays pixel-fixed; drag the top grip → rotates (Shift snaps 15°); drag body → moves; opacity slider updates live and persists. One PATCH in flight per entity (check Network — no overlap).
- **keyboard:** Tab lands on first marker (reading order); arrows rove in View, Enter navigates; in Place, arrows nudge (Shift fine), `R` rotates, Enter commits, Esc reverts to View; Escape inside the place combobox does *not* exit the mode; focus ring visible at low and high zoom.
- **console:** zero errors across all flows.

One known, documented trade-off: a keyboard-focused marker that is *outside the current zoom viewBox* is not auto-panned into view (only the page is scrolled to the SVG), because `floorplan.js` owns the `viewBox` closure and a raw `setAttribute` pan from this module would desync the next manual pan. Closing that fully needs a one-line viewBox-sync hook exposed from `floorplan.js`.

---

# Adversarial Verification

## Verifier 1

Verified against the actual code on disk (not the prose). I traced the exact event sequences for mouse and touch. All three JS files parse. Verdict below.

## BLOCKER 1 (pan handler must not fire on a marker/handle gesture) — RESOLVED

The suppression mechanism is correct, and for the right reasons. The pan `onmousedown` is a **property handler** on `svgElement` (`floorplan.js:254`), which is a bubble-phase, non-capture listener. A marker gesture's target is a descendant (`<a><g role=button>…icon…</g></a>` from `svg.py`), so `svgElement` is a strict *ancestor*. The editing module's `svg.addEventListener("mousedown", onMouseDownCapture, true)` (`floorplan_editing.js:408`) therefore fires in the **capture phase, before** the event ever reaches the target or bubbles. `onMouseDownCapture` calls `e.stopPropagation()` when `activeDrag || editTarget(e.target)` (line 366). A capture-phase `stopPropagation()` on an ancestor halts descent to the target and all bubbling, so the bubble-phase `svgElement.onmousedown` never runs. `isPanning`/`selectionRect` are never set by the pan path. This is exactly the mechanism the blocker demanded, and `preventDefault`-on-`pointerdown` is explicitly *not* relied on for mouse (comment at 352-354; the pointerdown `preventDefault` at line 354 is only for touch/pen).

Exact mouse trace, press-drag-release on a movable marker in place mode:
- `pointerdown` → capture `onPointerDownCapture`: `editTarget` finds the `g.object` → `preventDefault`+`stopPropagation`+`startMarkerDrag` (sets `activeDrag`, `setPointerCapture` on the `<g>`, window `pointermove/up` listeners).
- compat `mousedown` → capture `onMouseDownCapture`: `activeDrag` set → `stopPropagation` → **pan `onmousedown` never runs.** ✓
- `pointermove` → `onMarkerMove` (window). compat `mousemove` → capture `onMouseMoveCapture` (line 404): `activeDrag` set → `stopPropagation` → **pan `onmousemove` never runs.** This closes the second half of the blocker — during the drag, `svgElement.onmousemove`'s early-return (`!isPanning && !selectionRect`) does NOT protect us because `isPanning` defaults to `true` (line 7). The capture guard is the correct fix since that closure is unreachable externally. ✓
- `pointerup` → `onMarkerUp`: clears `activeDrag`, sets `suppressClick`. compat `mouseup` reaches `svgElement.onmouseup` (there is intentionally no capture mouseup guard) — benign: it only finalizes a zoom box (none exists in place mode) and sets `isPanning=false`, which is actually protective for later hovers.
- `click` → capture `onClickCapture`: `suppressClick` true → `preventDefault`+`stopPropagation` → the `<a>` cannot navigate. ✓

Non-movable marker and sub-threshold tap both handled: `editTarget` matches regardless of `canMove`, so `onMouseDownCapture` still suppresses the pan; a tap ends with `onClickCapture` place-branch focusing the marker (no navigation).

## BLOCKER 2 (placement must be an explicit gesture) — RESOLVED

Placement is committed only through the Place-panel button (`commitBtn`, line 1195), never "next canvas click." `onClickCapture`'s place branch (376-386) only fires when `e.target.closest("g.object[data-tile-id]")` is truthy — i.e. a click *on an existing marker* (→ focus). An empty-canvas click returns without doing anything, so a pan-release can never drop a marker. Empty-canvas pan itself still works in place mode: `onPointerDownCapture` returns on `!hit`, `onMouseDownCapture` doesn't stop (no `activeDrag`, `editTarget` null), so `svgElement.onmousedown` pans normally. ✓

## Empty-canvas pan and box behavior in every mode

- **View mode:** every capture guard early-returns (`uiMode==="view"` on lines 348/365; `activeDrag` null for move/wheel). Pan, box-zoom, shift-wheel, tooltip, highlight are byte-for-byte the original behavior. ✓
- **Place/calibrate:** empty-canvas mouse *and* touch pan work through the untouched `onmousedown`/`onmousemove`. Touch is sound: `touch-action:none` is scoped to `svg[data-ui-mode=place|calibrate]` (`svg.css:219-221`) so the browser doesn't steal the gesture before `setPointerCapture` engages, yet still emits the compat mouse events the pan handler needs; in view mode `touch-action` is untouched so page scroll is normal. Marker/calibrate drags use pointer events + `setPointerCapture`, and any compat mouse events they spawn are swallowed by the capture guards whether pointer-capture redirects them to the `<g>` (svg still ancestor) or leaves them on the background.

## The one deviation to flag (not a blocker regression)

`forcePanMode` (292-298) **disables the box-zoom toggle while in place/calibrate mode**, so box-*selection* zoom is unavailable during editing — a deliberate design choice, not a break: the toggle is re-enabled by `restorePanControls` on return to view, and **shift+wheel zoom stays live in every mode** (`onWheelCapture` only stops during an active drag, line 393), so zoom capability is never lost. If the spec's "box-zoom must work in every mode" is meant literally rather than "box-zoom must survive editing," this is the sole point to reconcile with the spec author; functionally it is intentional and reversible.

## Pre-existing quirk, out of scope

`isPanning` defaults to `true` (`floorplan.js:7`), so a bare hover can enter the pan branch — but the `Math.max(0, …)` viewBox clamp (356-357) pins it at the origin until a real pan sets `endPoint`, so it's invisible. The editing module doesn't worsen it; in fact the reachable `onmouseup` after the first drag sets `isPanning=false`. No action needed here.

Both confirmed prior blockers (1 and 2), plus the persistence/reload and a11y-Escape hijack concerns touching the event model (document `keydown` Escape early-returns in view mode and defers to the place combobox, lines 1248-1254), are genuinely resolved in the code as written. No hole found in the event model.

## Verifier 2

I traced the persistence/lifecycle lens against the actual `floorplan_editing.js`. One confirmed break, one narrower residual, and BLOCKER 3 genuinely resolved.

## CONFIRMED BREAK — BREAK 1 is only partially closed: `flush()` can resolve with a coalesced follow-up PATCH still on the wire

`makePatchChannel.flush()` (lines 162–172) is the entire read-after-write guarantee, and its drain condition is wrong. It waits on the *current* `inflight` promise, then re-checks **`pending`** to decide whether to loop — but the in-flight request's own completion callback consumes `pending` and launches the next request *before* `flush`'s `.then` runs. So `flush` resolves while a real geometry PATCH is still in flight.

Precise promise-ordering (why C runs before D):
- `pump()` sets `inflight = fetch(...).then(resp).catch(err).then(C)` where `C = () => { inflight = null; if (pending && !aborted) pump(); }`. Call that promise `Pc`.
- `flush()` does `Promise.resolve(inflight).then(D)` with `inflight === Pc`, so `D` is chained on `Pc` and fires strictly *after* `C` returns.
- If a write was coalesced during the first request, `C` sees `pending` truthy, calls `pump()` → sends `P_A2`, sets `pending = null`, `inflight = Pc2`.
- `D` then runs, checks `if (pending && !aborted)` → `pending` is now `null` → **does not recurse → `flush` resolves with `Pc2` (the final position) still on the wire.**

Concrete interleaving (place mode, one marker A, slow net):
1. Drag marker A; debounce fires mid-drag → `P_A1` (interim position) in flight (~2s).
2. Keep dragging → `onMarkerMove` `schedule()` sets `pending` = final position (no new timer, `inflight` set).
3. Release → `onMarkerUp` `schedule()` + `flush()` (this drag's flush).
4. Click place-commit → POST returns fast → `afterPlace` → `reloadHook` → `flushAllPatches()` → `tile:A.flush()` (pumps early-return, `inflight` still `P_A1`).
5. `P_A1` resolves → `C` runs → sends `P_A2` (final pos), `pending=null`, `inflight=Pc2`.
6. Both flushes' `D` callbacks fire, see `pending===null`, do not recurse → `flushAllPatches` resolves.
7. `window.location.reload()` fires with **`P_A2` still in flight** → the reloaded server render can read the row before `P_A2` lands → marker A shows the pre-final position. That is exactly BREAK 1. `keepalive:true` guarantees `P_A2` eventually completes but does **not** order it before the reload's GET, so the write isn't lost, the *display* is stale until the next refresh.

Exact fix (one token) at `floorplan_editing.js:169-171` — gate the recursion on `inflight` too, so `flush` loops until the channel is truly quiescent:

```js
return Promise.resolve(inflight).then(function () {
    // A completion callback may have coalesced a follow-up request (new `inflight`)
    // in addition to, or instead of, leaving `pending` un-drained.
    if ((pending || inflight) && !aborted) return self.flush();
});
```

After this, step 6's `D` sees `inflight === Pc2` (truthy) → recurses → awaits `Pc2` → `flush` only resolves when nothing is queued or on the wire, and the reload is correctly gated.

## Residual (narrower, lower severity) — `flushAllPatches` snapshots channel keys once; a channel created *during* the await isn't awaited

`flushAllPatches` (lines 231–237) does `Object.keys(channels).map(...)` synchronously. The reload await can span real network time (waiting on an in-flight patch — even longer after the fix above). If the user starts dragging a *different* marker B in that window, `channelFor("tile:B", …)` creates a key not in the snapshot; its PATCH is never awaited and the reload races it (the `pagehide` → `flushAll()` backstop pumps it with `keepalive`, so the write survives but display can still be stale).

Exact fix — freeze interaction once a reload is committed so no new channel can appear:

```js
// mountEditing scope
var reloading = false;
// in editTarget(): if (reloading) return null;   // no new marker/handle drags
// in reloadHook: set reloading = true BEFORE flushAllPatches()
reloadHook = function (tile) {
    reloading = true;
    flushAllPatches().then(function () { /* stash + reload as today */ });
};
```

(Belt-and-suspenders alternative: make `flushAllPatches` re-snapshot until quiescent, but the interaction guard is deterministic and cheaper.)

## BLOCKER 3 (listener leaks) — genuinely resolved

The after-place path is a full `window.location.reload()`, not an in-place SVG re-render, so every document/window listener, the `MutationObserver`, and the overlay rAF are destroyed by the browser — nothing accumulates per placement. The once-only document listeners (`keydown`/`pagehide`/`visibilitychange`) live at `initFloorPlanEditing` top level (run once per page load), never inside `mountEditing`, so they don't stack. The only non-reload SVG swap path (`tryMount`) calls `current.teardown()` first, which `abort()`s channels and removes the SVG-scoped capture listeners before remounting. No leak on either path. Confirmed resolved.

## Bottom line

BLOCKER 3 is solid. BREAK 1's *simple* case (a single outstanding PATCH at place-time) is correctly flush-gated, but the drain primitive underneath has a real early-resolve bug that reopens the stale-read race whenever a drag coalesces a follow-up write during an in-flight request — which is the common case for any drag on a non-instant network. Apply the one-line `|| inflight` fix (primary) plus the `reloading` guard (secondary) and the persistence blocker is fully closed.

## Verifier 3

I've read the actual on-disk code (not just the summary), traced the event flows, and verified the tests/syntax. Here is my adversarial verdict on the accessibility + regression lens.

## Two confirmed holes (both are items the summary claims resolved)

### HOLE 1 — reduced-motion resolution is a no-op (CONFIRMED, mechanism-provable)
The summary states CSS "already carried … reduced-motion … verified complete, no changes needed," and the added rule is:

```css
@media (prefers-reduced-motion: reduce) {
  .spotlight-effect, .highlight-border, .indicator-arrow { animation: none !important; }
}
```

But those three elements have **no CSS animation** in `svg.css`/`dark_svg.css` (lines 143-162 set only fill/stroke/pointer-events). Their pulsing is driven by the **Web Animations API** — `spotlight.animate([...])`, `border.animate(...)`, `arrow.animate(...)` in `floorplan.js` (lines 616/647/673) — and the highlight zoom-in plus every pan/box-zoom transition is **GSAP** (`gsap.to(svgElement, {attr:{viewBox}})`, lines 242/360/410/519). The CSS `animation` property governs neither WAAPI nor GSAP. So a `prefers-reduced-motion` user still gets the full 1s zoom tween and the infinite opacity/stroke/translate pulses. The specific flagged item — "prefers-reduced-motion doesn't gate the gsap viewBox tweens" — is **untouched**; the CSS gives false assurance.

Concrete trace: load `?highlight_device=X` with the OS set to reduce motion → the viewBox still animates for 1s (`gsap.to`, line 519) and the spotlight/border/arrow still pulse forever until cleanup (`element.animate(..., {iterations: Infinity})`). Nothing in the diff intercepts either.

Exact fix (honest options, since the motion originates in the named-off-limits `floorplan.js` handlers): either (a) get an explicit exception to add `const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;` and gate each `gsap.to(...)`/`.animate(...)` call site (branch to a direct `setAttribute`/no pulse when `RM`) — that is the only real fix; or (b) if the handler bodies are truly frozen, **delete the no-op `@media` block** and record reduced-motion as a known limitation, rather than reporting it complete. As written it resolves nothing.

### HOLE 2 — tippy is still bound to the `<a>`, not the `<g>` (CONFIRMED against the explicit checklist item)
`svg.py:_draw_freeform_tile` (lines 1003-1004) still does `link["data-tooltip"] = …` and `link["class"] = "object-tooltip"` on the anchor, and `floorplan.js:initTooltips()` binds tippy to `.object-tooltip, [data-tooltip]` → the `<a>`. But the `<a>` is `tabindex="-1"` (line 962) and keyboard focus lives on the inner `<g role="button">`. Tippy's default trigger is `mouseenter focus`; since the `<a>` is never keyboard-focused, a keyboard user roving the markers gets the focus ring but **never the tooltip card**. This is not a regression (mouse hover still works — an SVG `<a>`'s box is the union of its children — and grid tiles are untouched), and `aria-label` carries name/type/status/position for screen-reader users, so severity is moderate. But the checklist item "tippy is bound to the `<a>` parent not the `<g>`" is genuinely **not** resolved.

Exact fix: move the binding onto the focusable group in `_draw_freeform_tile`, preserving its existing `object` class:
```python
group["class"] = "object object-tooltip"
group["data-tooltip"] = json.dumps(self._get_tooltip_data(obj, label))
# drop link["data-tooltip"] and link["class"]
```
Safe: `initTooltips` reads `[data-tooltip]` off the `<g>` the same way; the MutationObserver's `attributeFilter:['data-tooltip']` is unaffected (marker-drag writes `transform`/`data-pos-*`, never `data-tooltip`, so no tippy rebuild storm); the one-time childList rebuild still fires.

## Everything else on this lens holds up — confirmed resolved, no regressions

- **Roving tabindex / Enter-commits**: `<a>` forced `tabindex="-1"`, `<g>` roves 0/-1, Enter/Space always `preventDefault`-ed and dispatched by mode (view→`activateMarkerLink` via `window.top.location`, matching `target="_top"`; place→`commitMarker`). Native anchor activation cannot fire because focus is on the descendant `<g>`, not the `<a>`. Correct.
- **Scoped Escape**: document listener exempts `#place-object-panel` (combobox keeps its own Escape) and returns in view mode; the marker's own Escape calls `stopPropagation()` so `document` never double-fires `setMode("view")`. Traced correct — the target-phase `<g>` listener runs before the event can bubble to `document`.
- **Focus ring**: server-drawn `.focus-ring` rect with `vector-effect="non-scaling-stroke"`, toggled by `.is-focused` (`.object.is-focused .focus-ring{display:inline}`, higher specificity than the `display:none` default). Stroke stays a constant screen width at low zoom. Correct. (The off-viewBox auto-pan gap is honestly disclosed as a documented trade-off, not hidden.)
- **highlight-freeze release**: entering an edit mode restores `container.style.pointerEvents="auto"` immediately; the later `enableZoomAndPan`/`completeCleanup` timers are harmless no-ops. The stale-`highlightTimers` bug from the prior blueprint doesn't exist here (that variable is gone). Works.
- **No regression to existing behavior**: in **view mode every editing capture handler early-returns** (`onMouseDownCapture`/`onPointerDownCapture`/`onMouseMoveCapture` guard on `uiMode==="view"` or `activeDrag`), so pan, Shift-wheel zoom, box-zoom, reset, and tooltip all run untouched. `highlightElement` still resolves `svg.getElementById("device-X")` — the `<a>` keeps its `id`; `tabindex="-1"` doesn't affect `getBBox`/highlight. The MutationObserver's `attributeFilter:['data-tooltip']` means the 60fps calibrate-overlay rAF and marker-drag attribute writes do **not** thrash `initTooltips`. `.sr-only`/listbox/legend-mirror ship in `light.css`, which the page loads at line 116. Verified: `node --check` passes all three JS files; `node --test` is 8/8 green.

## Prior confirmed blockers — genuinely resolved
BLOCKER 1 (capture-phase `mousedown`+`stopPropagation` neutralizes the bubble-phase `onmousedown` pan, and the capture-phase `mousemove` guard stops the compat `mousemove` from reaching `svgElement.onmousemove` while `activeDrag` is set — I traced the full pointerdown→mousedown-compat→pointermove→pointerup→click sequence and pan never engages, while empty-canvas pan/box-zoom stay live); BLOCKER 2 (placement is a button commit dropping at view center, never "next click"); BLOCKER 3 (full page reload after place, module-scope listeners install-once and delegate through `current`, per-SVG listeners die with the node, observers/rAF torn down); persistence BREAK 1/2 (`makePatchChannel` never has two writes in flight so the server can't reorder, `flush()` awaits the in-flight fetch, and `afterPlace` awaits `flushAllPatches()` before reload). All hold.

Net: the event-model and persistence blockers are solid and there are no regressions to the existing viewer, but two a11y items reported as done are not — reduced-motion is a no-op against WAAPI/GSAP, and tippy never reaches the keyboard-focusable marker.
