/*
 * Wave D — interactive floor plan editing (mode toggle, drag-to-calibrate, place picker).
 *
 * This layers on TOP of the existing viewer (floorplan.js) WITHOUT editing its pan/zoom/tooltip/
 * highlight bodies. It self-mounts by watching #floor-plan-svg for the injected <svg> and tears
 * itself down cleanly when that node is replaced, so there are no leaked document-level listeners
 * across the reload-after-place flow.
 *
 * Confirmed-blocker resolutions (see docs/dev/wave_d_spec.md "Adversarial Critiques"):
 *  - BLOCKER 1 (event model): a capture-phase `mousedown` listener on the SVG calls
 *    stopPropagation() for a marker/handle gesture in an edit mode. Because the pan handler is a
 *    bubble-phase property handler (svgElement.onmousedown) on the same element, halting the event
 *    in the capture phase means it never runs — so `isPanning` is never set and the untouched
 *    onmousemove early-returns. preventDefault() on pointerdown is NOT relied on for mouse (it only
 *    suppresses touch/pen compat events); it is still called for touch/pen parity.
 *  - BLOCKER 2 (placement is an explicit gesture): placing an object is a button commit that drops
 *    the marker at the center of the current view. There is no "next canvas click places", so an
 *    empty-canvas pan can never drop a marker.
 *  - BLOCKER 3 (reload leaks): all document/window listeners are installed exactly once; per-SVG
 *    listeners live on the SVG node and die with it; the MutationObserver and overlay rAF are torn
 *    down on unmount.
 *  - persistence BREAK 1/2: makePatchChannel serializes writes per key (never two in flight, so the
 *    server cannot reorder them) and flush() resolves only after every in-flight write is acked, so
 *    a reload cannot race an outstanding geometry PATCH.
 *  - Rotated-corner math (Finding 4): the OPPOSITE WORLD corner is held pixel-fixed; the pointer is
 *    resolved in the blueprint's rotated frame and the new world center is reconstructed from the
 *    fixed corner, so a rotated blueprint resizes without wobble.
 */
function initFloorPlanEditing() {
    "use strict";

    // ── Wave D constants ──────────────────────────────────────────────────────
    var PATCH_DEBOUNCE_MS = 200;
    var DRAG_THRESHOLD_PX = 3;
    var ROTATE_SNAP_DEG = 15;
    var HANDLE_PX = 12; // on-screen size of a calibrate corner handle
    var ROTATE_ARM_PX = 28; // on-screen distance of the rotate grip above the top edge
    var MIN_NORM = 0.02; // smallest normalized blueprint width/height
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var container = document.getElementById("floor-plan-svg");
    if (!container) return;

    // The single live controller for the currently mounted SVG (replaced on reload). Document-level
    // listeners (installed once, below) delegate through this so nothing stacks across reloads.
    var current = null;

    // ── CSRF ──────────────────────────────────────────────────────────────────
    function getCookie(name) {
        var match = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
        return match ? decodeURIComponent(match.pop()) : "";
    }
    function csrfToken() {
        // Nautobot uses the default CSRF cookie name; fall back to a rendered hidden input if present.
        return (
            getCookie("csrftoken") ||
            (document.querySelector("input[name=csrfmiddlewaretoken]") || {}).value ||
            ""
        );
    }

    // ── §A Coordinate helpers (pure geometry) ─────────────────────────────────
    function readContentRect(svg) {
        return {
            x: parseFloat(svg.getAttribute("data-content-x")) || 0,
            y: parseFloat(svg.getAttribute("data-content-y")) || 0,
            w: parseFloat(svg.getAttribute("data-content-w")) || 1,
            h: parseFloat(svg.getAttribute("data-content-h")) || 1,
        };
    }
    function screenToUser(svg, clientX, clientY) {
        var pt = svg.createSVGPoint();
        pt.x = clientX;
        pt.y = clientY;
        var ctm = svg.getScreenCTM(); // FRESH every call — never cache
        if (!ctm) return { x: clientX, y: clientY };
        return pt.matrixTransform(ctm.inverse());
    }
    function userScale(svg) {
        var ctm = svg.getScreenCTM();
        return ctm ? Math.abs(ctm.a) || 1 : 1;
    }
    function rotatePoint(px, py, cx, cy, deg) {
        var rad = (deg * Math.PI) / 180;
        var cos = Math.cos(rad);
        var sin = Math.sin(rad);
        var dx = px - cx;
        var dy = py - cy;
        return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos };
    }
    // Unit direction vectors of a frame rotated by `deg`: u = local +x, v = local +y, in world space.
    function frame(deg) {
        var rad = (deg * Math.PI) / 180;
        return { u: { x: Math.cos(rad), y: Math.sin(rad) }, v: { x: -Math.sin(rad), y: Math.cos(rad) } };
    }
    function dot(a, b) {
        return a.x * b.x + a.y * b.y;
    }
    function clamp(v, lo, hi) {
        return Math.min(hi, Math.max(lo, v));
    }
    function normalizeAngle(deg) {
        var a = deg % 360;
        return a < 0 ? a + 360 : a;
    }

    // ── §B Patch channel: serialized (never two in flight) + drain-aware flush ─
    // Serializing per key means the server observes writes in send order (fixes the out-of-order
    // clobber), and flush() resolving only after the in-flight fetch settles means a reload cannot
    // read stale state (fixes the read-after-write race).
    function makePatchChannel(url, opts) {
        opts = opts || {};
        var delay = opts.delay == null ? PATCH_DEBOUNCE_MS : opts.delay;
        var onError = opts.onError || function () {};
        var pending = null;
        var timer = null;
        var inflight = null; // Promise while a request is on the wire
        var controller = null;
        var aborted = false;

        function pump() {
            timer = null;
            if (inflight || aborted || !pending) return;
            var body = pending;
            pending = null;
            controller = new AbortController();
            inflight = fetch(url, {
                method: "PATCH",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                body: JSON.stringify(body),
                keepalive: true,
                signal: controller.signal,
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        return resp
                            .json()
                            .catch(function () {
                                return null;
                            })
                            .then(function (data) {
                                onError(data, resp.status);
                            });
                    }
                })
                .catch(function (err) {
                    if (err && err.name !== "AbortError") onError(err, 0);
                })
                .then(function () {
                    inflight = null;
                    if (pending && !aborted) pump(); // coalesced writes that arrived mid-flight
                });
            return inflight;
        }

        return {
            schedule: function (partial) {
                pending = Object.assign(pending || {}, partial);
                if (!timer && !inflight) timer = setTimeout(pump, delay);
            },
            // Resolves only once every queued/in-flight write to this key has been acknowledged.
            flush: function () {
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
                pump();
                var self = this;
                return Promise.resolve(inflight).then(function () {
                    if (pending && !aborted) return self.flush();
                });
            },
            abort: function () {
                aborted = true;
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                }
                if (controller) controller.abort();
                pending = null;
            },
        };
    }

    // ── The controller: one per mounted SVG ───────────────────────────────────
    function mountEditing(svg) {
        if (!svg || svg.__fpEditingMounted) return;
        svg.__fpEditingMounted = true;

        var content = readContentRect(svg);
        var image = svg.querySelector("#blueprint-image");
        var uiMode = "view";
        var activeDrag = null; // { type, ... }
        var suppressClick = false; // swallow the synthetic click after a moved drag
        var overlay = null;
        var overlayRaf = 0;
        var channels = {}; // key -> patch channel
        var reloadHook = null; // set by place/persistence agent; falls back to full reload

        // Live blueprint state, mirrored to/from #blueprint-image data-bg-*.
        var bg = readBg();

        // Cached DOM controls (may be absent depending on permissions / blueprint presence).
        var modeGroup = document.getElementById("floor-plan-mode-group");
        var modeButtons = modeGroup ? Array.prototype.slice.call(modeGroup.querySelectorAll("button[data-mode]")) : [];
        var toggleBtn = document.getElementById("toggle-zoom-mode");
        var statusRegion = document.getElementById("floor-plan-status");
        var opacityRange = document.getElementById("blueprint-opacity-range");
        var opacityNumber = document.getElementById("blueprint-opacity-number");
        var placePanel = document.getElementById("place-object-panel");

        var floorplanUrl = container.getAttribute("data-floorplan-api");

        function channelFor(key, url) {
            if (!channels[key]) {
                channels[key] = makePatchChannel(url, {
                    onError: function (data) {
                        announce("Change rejected by the server.");
                        if (data && typeof data === "object") {
                            var msgs = [];
                            Object.keys(data).forEach(function (k) {
                                msgs.push(k + ": " + [].concat(data[k]).join(" "));
                            });
                            if (msgs.length) announce("Change rejected. " + msgs.join("; "));
                        }
                    },
                });
            }
            return channels[key];
        }
        function flushAllPatches() {
            return Promise.all(
                Object.keys(channels).map(function (k) {
                    return channels[k].flush();
                })
            );
        }

        function announce(msg) {
            if (statusRegion) statusRegion.textContent = msg;
        }

        // ── Blueprint state helpers ──
        function readBg() {
            if (!image) return null;
            var d = image.dataset;
            return {
                nx: parseFloat(d.bgX) || 0,
                ny: parseFloat(d.bgY) || 0,
                nw: parseFloat(d.bgWidth) || 1,
                nh: parseFloat(d.bgHeight) || 1,
                rot: parseFloat(d.bgRotation) || 0,
            };
        }
        function bgUserRect(state) {
            return {
                x: content.x + state.nx * content.w,
                y: content.y + state.ny * content.h,
                w: state.nw * content.w,
                h: state.nh * content.h,
            };
        }
        function bgCenter(state) {
            var r = bgUserRect(state);
            return { x: r.x + r.w / 2, y: r.y + r.h / 2 };
        }
        // Apply the live blueprint state to the <image>, mirror data-bg-*, reposition the overlay.
        function applyBlueprintTransform() {
            if (!image) return;
            var r = bgUserRect(bg);
            var c = { x: r.x + r.w / 2, y: r.y + r.h / 2 };
            image.setAttribute("x", r.x);
            image.setAttribute("y", r.y);
            image.setAttribute("width", Math.max(1e-3, r.w));
            image.setAttribute("height", Math.max(1e-3, r.h));
            image.setAttribute("preserveAspectRatio", "none");
            if (bg.rot) {
                image.setAttribute("transform", "rotate(" + bg.rot + " " + c.x + " " + c.y + ")");
            } else {
                image.removeAttribute("transform");
            }
            image.dataset.bgX = bg.nx;
            image.dataset.bgY = bg.ny;
            image.dataset.bgWidth = bg.nw;
            image.dataset.bgHeight = bg.nh;
            image.dataset.bgRotation = bg.rot;
            image.dataset.bgAutofit = "false";
            positionOverlay();
        }

        // ── §C Mode state machine ──
        function forcePanMode() {
            // Drive the existing box-zoom toggle back to PAN without touching its internals: if it
            // currently reads as zoom-on, click it (its own handler flips zoomMode), then disable it.
            if (toggleBtn) {
                if ((toggleBtn.textContent || "").trim() === "Switch to Pan Mode") toggleBtn.click();
                toggleBtn.disabled = true;
            }
        }
        function restorePanControls() {
            if (toggleBtn) toggleBtn.disabled = false;
        }
        // The deep-link highlight sets #floor-plan-svg pointer-events:none for a few seconds and can
        // resetZoom mid-edit; restore interactivity immediately when entering an edit mode so the
        // canvas is never dead. (The setTimeout-based cleanup in floorplan.js is harmless once
        // pointer events are back; the a11y agent additionally cancels those timers at the source.)
        function clearHighlightFreeze() {
            container.style.pointerEvents = "auto";
        }

        function setMode(next) {
            if (next !== "view" && next !== "place" && next !== "calibrate") next = "view";
            if (activeDrag) cancelDrag(false);
            flushAllPatches();
            clearHighlightFreeze();
            uiMode = next;
            svg.dataset.uiMode = next;
            // role="application" in place/calibrate so Arrow keys reach our handlers; role="group" in
            // view so a reading screen-reader keeps its virtual cursor over the marker labels.
            syncRoleForMode(next);
            if (next === "view") {
                restorePanControls();
                teardownCalibrate();
            } else {
                forcePanMode();
                if (next === "calibrate") buildCalibrate();
                else teardownCalibrate();
            }
            if (placePanel) placePanel.hidden = next !== "place";
            syncModeButtons();
            announce(next + " mode");
        }
        function syncModeButtons() {
            modeButtons.forEach(function (btn) {
                var on = btn.dataset.mode === uiMode;
                btn.setAttribute("aria-pressed", on ? "true" : "false");
                btn.classList.toggle("active", on);
            });
        }

        // ── §D Capture-phase input routers (attached to the SVG; die with the node) ──
        function editTarget(target) {
            if (uiMode === "calibrate") return target.closest(".calibrate-handle, .calibrate-body");
            if (uiMode === "place") return target.closest("g.object[data-tile-id]");
            return null;
        }
        function onPointerDownCapture(e) {
            if (uiMode === "view") return;
            if (e.button != null && e.button !== 0) return;
            var hit = editTarget(e.target);
            if (!hit) return; // empty canvas → let the untouched pan handler run
            // preventDefault suppresses touch/pen compat events; stopPropagation halts the capture so
            // the bubble-phase onmousedown pan never fires (the real mouse fix is onMouseDownCapture).
            e.preventDefault();
            e.stopPropagation();
            if (window.gsap) gsap.killTweensOf(svg);
            if (uiMode === "calibrate") {
                startCalibrate(hit, e);
            } else if (uiMode === "place" && current && current.placeDragStart) {
                current.placeDragStart(hit, e); // place/a11y agent registers the marker-drag handler
            }
        }
        // BLOCKER 1: neutralize the bubble-phase pan property handler for marker/handle gestures.
        function onMouseDownCapture(e) {
            if (uiMode === "view") return;
            if (activeDrag || editTarget(e.target)) e.stopPropagation();
        }
        function onClickCapture(e) {
            if (suppressClick) {
                // A drag just moved: swallow the trailing click so an anchor marker can't navigate.
                suppressClick = false;
                e.preventDefault();
                e.stopPropagation();
                return;
            }
            if (uiMode === "place") {
                // A sub-threshold press on a marker is a SELECT (focus it for keyboard nudging), not
                // "follow the link". Suppress the anchor navigation and move roving focus here.
                var g = e.target.closest && e.target.closest("g.object[data-tile-id]");
                if (g) {
                    e.preventDefault();
                    e.stopPropagation();
                    focusMarker(g);
                }
                return;
            }
            if (uiMode === "calibrate" && e.target.closest(".calibrate-overlay")) {
                e.preventDefault();
                e.stopPropagation();
            }
        }
        function onWheelCapture(e) {
            if (activeDrag) {
                e.preventDefault();
                e.stopPropagation();
            }
        }
        // BLOCKER 1, second half: while OUR drag is live, the native mousemove still bubbles to the
        // untouched svgElement.onmousemove pan handler, which pans iff its private `isPanning` closure
        // is stale-true (its initial value, or left over from a prior pan). We cannot write that
        // closure from here, so we stop the mousemove in the capture phase — the pan handler never
        // runs during a marker/calibrate drag. Empty-canvas pan is untouched (no activeDrag).
        function onMouseMoveCapture(e) {
            if (activeDrag) e.stopPropagation();
        }

        svg.addEventListener("pointerdown", onPointerDownCapture, true);
        svg.addEventListener("mousedown", onMouseDownCapture, true);
        svg.addEventListener("mousemove", onMouseMoveCapture, true);
        svg.addEventListener("click", onClickCapture, true);
        svg.addEventListener("wheel", onWheelCapture, { capture: true, passive: false });

        // ── §H Calibrate module ──────────────────────────────────────────────
        var CORNERS = [
            { key: "nw", opp: "se", ux: 0, uy: 0 },
            { key: "ne", opp: "sw", ux: 1, uy: 0 },
            { key: "se", opp: "nw", ux: 1, uy: 1 },
            { key: "sw", opp: "ne", ux: 0, uy: 1 },
        ];
        function SVGEL(name, attrs) {
            var el = document.createElementNS("http://www.w3.org/2000/svg", name);
            if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
            return el;
        }
        function buildCalibrate() {
            if (!image || overlay) return;
            bg = readBg();
            overlay = SVGEL("g", { class: "calibrate-overlay", "data-calibrate-chrome": "true" });
            overlay.appendChild(SVGEL("polygon", { class: "calibrate-body", "data-calibrate-kind": "body" }));
            overlay.appendChild(SVGEL("line", { class: "calibrate-rot-arm", "data-calibrate-chrome": "true" }));
            CORNERS.forEach(function (c) {
                overlay.appendChild(
                    SVGEL("rect", {
                        class: "calibrate-handle",
                        "data-calibrate-kind": "corner",
                        "data-corner": c.key,
                        tabindex: "0",
                        role: "slider",
                        "aria-label": "Blueprint " + c.key + " corner",
                    })
                );
            });
            overlay.appendChild(
                SVGEL("circle", {
                    class: "calibrate-handle calibrate-rotate",
                    "data-calibrate-kind": "rotate",
                    tabindex: "0",
                    role: "slider",
                    "aria-label": "Blueprint rotation",
                })
            );
            svg.appendChild(overlay);
            overlay.addEventListener("keydown", onCalibrateKeydown);
            positionOverlay();
            startOverlayRaf();
            announce("Calibrate mode. Drag a corner to resize, the top grip to rotate, or the body to move.");
        }
        function teardownCalibrate() {
            stopOverlayRaf();
            if (overlay) {
                overlay.removeEventListener("keydown", onCalibrateKeydown);
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                overlay = null;
            }
        }
        function positionOverlay() {
            if (!overlay || !image) return;
            var r = bgUserRect(bg);
            var c = { x: r.x + r.w / 2, y: r.y + r.h / 2 };
            var scale = userScale(svg);
            var hs = HANDLE_PX / scale; // handle stays ~constant on screen at any zoom
            var pts = {};
            CORNERS.forEach(function (corner) {
                var lx = r.x + corner.ux * r.w;
                var ly = r.y + corner.uy * r.h;
                pts[corner.key] = rotatePoint(lx, ly, c.x, c.y, bg.rot);
            });
            var poly = overlay.querySelector(".calibrate-body");
            poly.setAttribute(
                "points",
                [pts.nw, pts.ne, pts.se, pts.sw]
                    .map(function (p) { return p.x + "," + p.y; })
                    .join(" ")
            );
            overlay.querySelectorAll(".calibrate-handle[data-corner]").forEach(function (h) {
                var p = pts[h.getAttribute("data-corner")];
                h.setAttribute("x", p.x - hs / 2);
                h.setAttribute("y", p.y - hs / 2);
                h.setAttribute("width", hs);
                h.setAttribute("height", hs);
            });
            // Rotate grip: above the top edge midpoint, along the outward (local -y) normal.
            var topMid = rotatePoint(r.x + r.w / 2, r.y, c.x, c.y, bg.rot);
            var fr = frame(bg.rot);
            var arm = ROTATE_ARM_PX / scale;
            var grip = { x: topMid.x - fr.v.x * arm, y: topMid.y - fr.v.y * arm };
            var rot = overlay.querySelector(".calibrate-rotate");
            rot.setAttribute("cx", grip.x);
            rot.setAttribute("cy", grip.y);
            rot.setAttribute("r", (HANDLE_PX / 2 + 1) / scale);
            var line = overlay.querySelector(".calibrate-rot-arm");
            line.setAttribute("x1", topMid.x);
            line.setAttribute("y1", topMid.y);
            line.setAttribute("x2", grip.x);
            line.setAttribute("y2", grip.y);
        }
        function startOverlayRaf() {
            stopOverlayRaf();
            var tick = function () {
                positionOverlay();
                overlayRaf = window.requestAnimationFrame(tick);
            };
            overlayRaf = window.requestAnimationFrame(tick);
        }
        function stopOverlayRaf() {
            if (overlayRaf) {
                window.cancelAnimationFrame(overlayRaf);
                overlayRaf = 0;
            }
        }

        function startCalibrate(handle, e) {
            var kind = handle.getAttribute("data-calibrate-kind");
            try {
                handle.setPointerCapture(e.pointerId);
            } catch (err) {
                /* synthetic/keyboard path has no pointer */
            }
            var startBg = Object.assign({}, bg);
            var snapshot = { kind: kind, handle: handle, moved: false, startBg: startBg, pointerId: e.pointerId };
            if (kind === "corner") {
                var r0 = bgUserRect(startBg);
                var c0 = { x: r0.x + r0.w / 2, y: r0.y + r0.h / 2 };
                var cornerDef = CORNERS.filter(function (c) { return c.key === handle.getAttribute("data-corner"); })[0];
                var opp = CORNERS.filter(function (c) { return c.key === cornerDef.opp; })[0];
                // Fixed opposite corner in WORLD space (rotated about the start center).
                var oppLocal = { x: r0.x + opp.ux * r0.w, y: r0.y + opp.uy * r0.h };
                snapshot.oppWorld = rotatePoint(oppLocal.x, oppLocal.y, c0.x, c0.y, startBg.rot);
                snapshot.fr = frame(startBg.rot);
                snapshot.aspect = r0.h / r0.w; // for Shift lock
            } else if (kind === "rotate") {
                snapshot.center = bgCenter(startBg);
            } else if (kind === "body") {
                snapshot.startPointer = screenToUser(svg, e.clientX, e.clientY);
                snapshot.startRect = bgUserRect(startBg);
            }
            activeDrag = { type: "calibrate", snapshot: snapshot };
            handle.addEventListener("pointermove", onCalibrateMove);
            handle.addEventListener("pointerup", onCalibrateUp);
            handle.addEventListener("pointercancel", onCalibrateUp);
        }

        function calibrateCornerTo(snap, pointerUser, shift) {
            // Reconstruct the rect from the FIXED opposite world corner. a,b are the dragged corner's
            // extents along the blueprint's rotated axes; the world center is the midpoint of the two
            // diagonal corners, so the opposite corner stays pixel-fixed for any rotation.
            var d = { x: pointerUser.x - snap.oppWorld.x, y: pointerUser.y - snap.oppWorld.y };
            var a = dot(d, snap.fr.u);
            var b = dot(d, snap.fr.v);
            if (shift) {
                var mag = Math.max(Math.abs(a), Math.abs(b) / (snap.aspect || 1));
                a = (a < 0 ? -1 : 1) * mag;
                b = (b < 0 ? -1 : 1) * mag * (snap.aspect || 1);
            }
            var minWUser = MIN_NORM * content.w;
            var minHUser = MIN_NORM * content.h;
            var w = Math.max(minWUser, Math.abs(a));
            var h = Math.max(minHUser, Math.abs(b));
            var aC = (a < 0 ? -1 : 1) * w;
            var bC = (b < 0 ? -1 : 1) * h;
            var center = {
                x: snap.oppWorld.x + (snap.fr.u.x * aC) / 2 + (snap.fr.v.x * bC) / 2,
                y: snap.oppWorld.y + (snap.fr.u.y * aC) / 2 + (snap.fr.v.y * bC) / 2,
            };
            var ux = center.x - w / 2;
            var uy = center.y - h / 2;
            bg.nx = (ux - content.x) / content.w;
            bg.ny = (uy - content.y) / content.h;
            bg.nw = w / content.w;
            bg.nh = h / content.h;
            // rotation unchanged during a corner drag
        }

        function onCalibrateMove(e) {
            if (!activeDrag) return;
            var snap = activeDrag.snapshot;
            var p = screenToUser(svg, e.clientX, e.clientY);
            if (!snap.moved) {
                var start = snap.startPointer || snap.center || snap.oppWorld;
                var scale = userScale(svg);
                if (start && Math.hypot((p.x - start.x) * scale, (p.y - start.y) * scale) < DRAG_THRESHOLD_PX) return;
                snap.moved = true;
            }
            if (snap.kind === "corner") {
                calibrateCornerTo(snap, p, e.shiftKey);
                applyBlueprintTransform();
                channelFor("floorplan", floorplanUrl).schedule({
                    bg_x: bg.nx,
                    bg_y: bg.ny,
                    bg_width: bg.nw,
                    bg_height: bg.nh,
                });
            } else if (snap.kind === "rotate") {
                var ang = (Math.atan2(p.y - snap.center.y, p.x - snap.center.x) * 180) / Math.PI;
                var rot = normalizeAngle(ang + 90); // grip home points up (local -y)
                if (e.shiftKey) rot = Math.round(rot / ROTATE_SNAP_DEG) * ROTATE_SNAP_DEG;
                bg.rot = rot;
                applyBlueprintTransform();
                channelFor("floorplan", floorplanUrl).schedule({ bg_rotation: bg.rot });
            } else if (snap.kind === "body") {
                var dx = p.x - snap.startPointer.x;
                var dy = p.y - snap.startPointer.y;
                bg.nx = (snap.startRect.x + dx - content.x) / content.w;
                bg.ny = (snap.startRect.y + dy - content.y) / content.h;
                applyBlueprintTransform();
                channelFor("floorplan", floorplanUrl).schedule({ bg_x: bg.nx, bg_y: bg.ny });
            }
        }
        function onCalibrateUp() {
            if (!activeDrag) return;
            var snap = activeDrag.snapshot;
            snap.handle.removeEventListener("pointermove", onCalibrateMove);
            snap.handle.removeEventListener("pointerup", onCalibrateUp);
            snap.handle.removeEventListener("pointercancel", onCalibrateUp);
            try {
                snap.handle.releasePointerCapture(snap.pointerId);
            } catch (err) {
                /* no-op */
            }
            if (snap.moved) {
                suppressClick = true;
                channelFor("floorplan", floorplanUrl).flush();
                announce("Blueprint calibration updated.");
            }
            activeDrag = null;
        }
        function cancelDrag(commit) {
            if (!activeDrag) return;
            if (activeDrag.type === "marker") {
                endMarkerPointer(activeDrag);
                if (!commit) {
                    activeDrag.el.setAttribute("transform", activeDrag.start.transform);
                    activeDrag.el.dataset.posX = activeDrag.start.nx;
                    activeDrag.el.dataset.posY = activeDrag.start.ny;
                    activeDrag.el.dataset.rotation = activeDrag.start.rot;
                }
                activeDrag.el.classList.remove("dragging");
                activeDrag = null;
                return;
            }
            var snap = activeDrag.snapshot;
            if (activeDrag.type === "calibrate") {
                snap.handle.removeEventListener("pointermove", onCalibrateMove);
                snap.handle.removeEventListener("pointerup", onCalibrateUp);
                snap.handle.removeEventListener("pointercancel", onCalibrateUp);
                if (commit && snap.moved) {
                    channelFor("floorplan", floorplanUrl).flush();
                } else {
                    bg = Object.assign({}, snap.startBg);
                    applyBlueprintTransform();
                }
            }
            activeDrag = null;
        }

        // Keyboard parity for calibrate handles (arrows nudge; Shift = coarse; Esc reverts).
        function onCalibrateKeydown(e) {
            var handle = e.target.closest(".calibrate-handle");
            if (!handle) return;
            var kind = handle.getAttribute("data-calibrate-kind");
            if (e.key === "Escape") {
                bg = readBg();
                applyBlueprintTransform();
                return;
            }
            var arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
            if (!arrows[e.key] && kind !== "rotate") return;
            e.preventDefault();
            if (kind === "rotate") {
                var step = e.shiftKey ? ROTATE_SNAP_DEG : 1;
                if (e.key === "ArrowLeft" || e.key === "ArrowDown") bg.rot = normalizeAngle(bg.rot - step);
                else if (e.key === "ArrowRight" || e.key === "ArrowUp") bg.rot = normalizeAngle(bg.rot + step);
                else return;
                applyBlueprintTransform();
                channelFor("floorplan", floorplanUrl).schedule({ bg_rotation: bg.rot });
                channelFor("floorplan", floorplanUrl).flush();
                return;
            }
            // Corner nudge: synthesize a pointer at the current dragged corner + a small world delta.
            var cornerDef = CORNERS.filter(function (c) { return c.key === handle.getAttribute("data-corner"); })[0];
            var opp = CORNERS.filter(function (c) { return c.key === cornerDef.opp; })[0];
            var r0 = bgUserRect(bg);
            var c0 = { x: r0.x + r0.w / 2, y: r0.y + r0.h / 2 };
            var oppLocal = { x: r0.x + opp.ux * r0.w, y: r0.y + opp.uy * r0.h };
            var snap = {
                oppWorld: rotatePoint(oppLocal.x, oppLocal.y, c0.x, c0.y, bg.rot),
                fr: frame(bg.rot),
                aspect: r0.h / r0.w,
            };
            var dragged = rotatePoint(r0.x + cornerDef.ux * r0.w, r0.y + cornerDef.uy * r0.h, c0.x, c0.y, bg.rot);
            var stepUser = (e.shiftKey ? 1 : 8) / userScale(svg);
            var mv = arrows[e.key];
            calibrateCornerTo(snap, { x: dragged.x + mv[0] * stepUser, y: dragged.y + mv[1] * stepUser }, e.shiftKey);
            applyBlueprintTransform();
            var ch = channelFor("floorplan", floorplanUrl);
            ch.schedule({ bg_x: bg.nx, bg_y: bg.ny, bg_width: bg.nw, bg_height: bg.nh });
            ch.flush();
        }

        // ── Opacity control (shares the floorplan channel) ──
        if (opacityRange || opacityNumber) {
            var setOpacityLive = function (v) {
                v = clamp(parseInt(v, 10) || 0, 0, 100);
                if (opacityRange) opacityRange.value = v;
                if (opacityNumber) opacityNumber.value = v;
                if (image) image.setAttribute("opacity", v / 100);
                return v;
            };
            var commitOpacity = function (v) {
                channelFor("floorplan", floorplanUrl).schedule({ background_opacity: setOpacityLive(v) });
                channelFor("floorplan", floorplanUrl).flush();
            };
            if (opacityRange) {
                opacityRange.addEventListener("input", function () { setOpacityLive(opacityRange.value); });
                opacityRange.addEventListener("change", function () { commitOpacity(opacityRange.value); });
            }
            if (opacityNumber) {
                opacityNumber.addEventListener("input", function () { setOpacityLive(opacityNumber.value); });
                opacityNumber.addEventListener("change", function () { commitOpacity(opacityNumber.value); });
            }
        }

        // ══ §E/§F  Marker place-drag + keyboard/ARIA (roving tabindex, focus ring, nudge) ══
        // Pure norm/user math is delegated to the shared, node-tested FloorPlanCoords module when it
        // is loaded (fallbacks keep this correct if that <script> is absent).
        var Coords = window.FloorPlanCoords || null;
        var rovingMarkers = [];
        var A11Y_FINE = 0.001; // Shift = 0.1% nudge
        var A11Y_COARSE = 0.01; //         = 1% nudge

        function tileUrl(id) {
            var base = container.getAttribute("data-tile-api") || "";
            return base.replace(/\/$/, "") + "/" + id + "/";
        }
        function markersInReadingOrder() {
            return Array.prototype.slice.call(svg.querySelectorAll("g.object[data-tile-id]")).sort(function (a, b) {
                var ay = parseFloat(a.dataset.posY), by = parseFloat(b.dataset.posY);
                if (ay !== by) return ay - by;
                return parseFloat(a.dataset.posX) - parseFloat(b.dataset.posX);
            });
        }
        function pct(n) { return Math.round(n * 100) + "%"; }
        function markerName(g) { return (g.getAttribute("aria-label") || "marker").split(",")[0]; }
        function relabelMarker(g) {
            var base = g.getAttribute("aria-label") || "";
            g.setAttribute(
                "aria-label",
                base.replace(/at \d+% \d+%/, "at " + pct(parseFloat(g.dataset.posX)) + " " + pct(parseFloat(g.dataset.posY)))
            );
        }
        function normFromUser(ux, uy) {
            if (Coords) return Coords.userToNorm(ux, uy, content);
            return { nx: (ux - content.x) / content.w, ny: (uy - content.y) / content.h };
        }
        function writeMarker(g, nx, ny, rot) {
            var u = Coords ? Coords.normToUser(nx, ny, content) : { ux: content.x + nx * content.w, uy: content.y + ny * content.h };
            g.setAttribute("transform", Coords ? Coords.formatTransform(u.ux, u.uy, rot) : "translate(" + u.ux + "," + u.uy + ") rotate(" + rot + ")");
            g.dataset.posX = nx;
            g.dataset.posY = ny;
            g.dataset.rotation = rot;
        }
        function snapshotMarker(g) {
            return {
                nx: parseFloat(g.dataset.posX),
                ny: parseFloat(g.dataset.posY),
                rot: parseFloat(g.dataset.rotation) || 0,
                transform: g.getAttribute("transform"),
            };
        }
        function syncRoleForMode(mode) {
            svg.setAttribute("role", mode === "view" ? "group" : "application");
        }

        // Roving tabindex (a11y HIGH: one tab stop; the <a> is removed from the tab order; the <g> roves).
        function armRovingTabindex() {
            rovingMarkers = markersInReadingOrder();
            rovingMarkers.forEach(function (g, i) {
                var a = g.closest("a");
                if (a) a.setAttribute("tabindex", "-1");
                g.setAttribute("tabindex", i === 0 ? "0" : "-1");
                if (!g.__a11yBound) {
                    bindMarkerA11y(g);
                    g.__a11yBound = true;
                }
            });
        }
        function focusMarker(g) {
            if (rovingMarkers.indexOf(g) < 0) return;
            rovingMarkers.forEach(function (m) { m.setAttribute("tabindex", "-1"); });
            g.setAttribute("tabindex", "0");
            if (typeof g.focus === "function") g.focus();
        }
        function focusMarkerAt(i) {
            if (!rovingMarkers.length) return;
            focusMarker(rovingMarkers[(i + rovingMarkers.length) % rovingMarkers.length]);
        }
        // Only scroll the SVG element into the browser viewport. Auto-panning the viewBox to a focused
        // off-screen marker is intentionally NOT done: floorplan.js owns the viewBox closure and cannot
        // be resynced from here, so a raw setAttribute pan would make the next manual pan jump. The
        // always-on non-scaling focus ring covers perceivability for in-viewport markers.
        function ensureMarkerVisible(g) {
            var cr = container.getBoundingClientRect();
            var sr = g.getBoundingClientRect();
            var outside = sr.right < cr.left || sr.left > cr.right || sr.bottom < cr.top || sr.top > cr.bottom;
            if (outside && typeof container.scrollIntoView === "function") {
                container.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
            }
        }
        function bindMarkerA11y(g) {
            g.addEventListener("focus", function () {
                g.classList.add("is-focused");
                g.__revert = snapshotMarker(g);
                ensureMarkerVisible(g);
            });
            g.addEventListener("blur", function () { g.classList.remove("is-focused"); });
            g.addEventListener("keydown", onMarkerKeydown);
        }
        function nudgeMarker(g, dnx, dny) {
            var nx = clamp(parseFloat(g.dataset.posX) + dnx, 0, 1);
            var ny = clamp(parseFloat(g.dataset.posY) + dny, 0, 1);
            writeMarker(g, nx, ny, parseFloat(g.dataset.rotation) || 0);
            relabelMarker(g);
            channelFor("tile:" + g.dataset.tileId, tileUrl(g.dataset.tileId)).schedule({ pos_x: nx, pos_y: ny });
            announce(markerName(g) + " at " + pct(nx) + " " + pct(ny) + ".");
        }
        function rotateMarker(g, dDeg) {
            var rot = normalizeAngle((parseFloat(g.dataset.rotation) || 0) + dDeg);
            writeMarker(g, parseFloat(g.dataset.posX), parseFloat(g.dataset.posY), rot);
            channelFor("tile:" + g.dataset.tileId, tileUrl(g.dataset.tileId)).schedule({ rotation: rot });
            announce(markerName(g) + " rotated to " + Math.round(rot) + " degrees.");
        }
        function commitMarker(g) {
            channelFor("tile:" + g.dataset.tileId, tileUrl(g.dataset.tileId)).flush();
            g.__revert = snapshotMarker(g);
            announce("Placement committed for " + markerName(g) + ".");
        }
        function revertMarker(g) {
            var s = g.__revert;
            if (!s) return;
            writeMarker(g, s.nx, s.ny, s.rot);
            relabelMarker(g);
            var ch = channelFor("tile:" + g.dataset.tileId, tileUrl(g.dataset.tileId));
            ch.schedule({ pos_x: s.nx, pos_y: s.ny, rotation: s.rot });
            ch.flush();
        }
        function activateMarkerLink(g) {
            var a = g.closest("a");
            if (!a) return;
            var href = a.getAttribute("href") || a.getAttributeNS("http://www.w3.org/1999/xlink", "href");
            if (!href || href === "#") return;
            if (a.getAttribute("target") === "_blank") window.open(href, "_blank");
            else window.top.location.href = href; // matches svg.py target="_top"
        }
        // Enter/Space are ALWAYS preventDefault-ed so native anchor activation never fires; the action
        // is dispatched by mode (view → navigate, place → commit). Escape reverts + returns to view.
        function onMarkerKeydown(e) {
            var g = e.currentTarget;
            if (uiMode === "view") {
                switch (e.key) {
                    case "ArrowRight": case "ArrowDown": e.preventDefault(); focusMarkerAt(rovingMarkers.indexOf(g) + 1); break;
                    case "ArrowLeft": case "ArrowUp": e.preventDefault(); focusMarkerAt(rovingMarkers.indexOf(g) - 1); break;
                    case "Home": e.preventDefault(); focusMarkerAt(0); break;
                    case "End": e.preventDefault(); focusMarkerAt(rovingMarkers.length - 1); break;
                    case "Enter": case " ": case "Spacebar": e.preventDefault(); activateMarkerLink(g); break;
                    default: break;
                }
                return;
            }
            if (uiMode === "place") {
                if (g.dataset.canMove === "false") return;
                var step = e.shiftKey ? A11Y_FINE : A11Y_COARSE;
                var handled = true;
                switch (e.key) {
                    case "ArrowRight": nudgeMarker(g, step, 0); break;
                    case "ArrowLeft": nudgeMarker(g, -step, 0); break;
                    case "ArrowDown": nudgeMarker(g, 0, step); break;
                    case "ArrowUp": nudgeMarker(g, 0, -step); break;
                    case "r": case "R": rotateMarker(g, e.shiftKey ? 1 : ROTATE_SNAP_DEG); break;
                    case "Enter": case " ": case "Spacebar": commitMarker(g); break;
                    case "Escape":
                        e.stopPropagation(); // handled here; don't also trigger the document Escape
                        revertMarker(g);
                        announce("Move cancelled for " + markerName(g) + ".");
                        setMode("view");
                        break;
                    default: handled = false;
                }
                if (handled) e.preventDefault();
            }
        }

        // Marker pointer-drag lifecycle. Registered as current.placeDragStart, so the capture-phase
        // pointer router (onPointerDownCapture) hands marker gestures here in place mode.
        function endMarkerPointer(drag) {
            window.removeEventListener("pointermove", onMarkerMove);
            window.removeEventListener("pointerup", onMarkerUp);
            window.removeEventListener("pointercancel", onMarkerCancelEv);
            try {
                if (drag.el.hasPointerCapture && drag.el.hasPointerCapture(drag.pointerId)) {
                    drag.el.releasePointerCapture(drag.pointerId);
                }
            } catch (err) { /* noop */ }
        }
        function startMarkerDrag(markerG, e) {
            if (markerG.dataset.canMove === "false") return;
            var xf = { ux: 0, uy: 0, rot: parseFloat(markerG.dataset.rotation) || 0 };
            var m = /translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)/.exec(markerG.getAttribute("transform") || "");
            if (m) { xf.ux = parseFloat(m[1]); xf.uy = parseFloat(m[2]); }
            var p = screenToUser(svg, e.clientX, e.clientY);
            activeDrag = {
                type: "marker",
                el: markerG,
                pointerId: e.pointerId,
                rot: xf.rot,
                grab: { dx: p.x - xf.ux, dy: p.y - xf.uy },
                start: snapshotMarker(markerG),
                startClient: { x: e.clientX, y: e.clientY },
                moved: false,
            };
            try { markerG.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
            markerG.classList.add("dragging");
            focusMarker(markerG);
            window.addEventListener("pointermove", onMarkerMove);
            window.addEventListener("pointerup", onMarkerUp);
            window.addEventListener("pointercancel", onMarkerCancelEv);
        }
        function onMarkerMove(e) {
            if (!activeDrag || activeDrag.type !== "marker") return;
            if (!activeDrag.moved) {
                if (Math.hypot(e.clientX - activeDrag.startClient.x, e.clientY - activeDrag.startClient.y) < DRAG_THRESHOLD_PX) return;
                activeDrag.moved = true;
            }
            var p = screenToUser(svg, e.clientX, e.clientY); // FRESH CTM every frame — never cached
            var n = normFromUser(p.x - activeDrag.grab.dx, p.y - activeDrag.grab.dy);
            var nx = clamp(n.nx, 0, 1);
            var ny = clamp(n.ny, 0, 1);
            writeMarker(activeDrag.el, nx, ny, activeDrag.rot); // direct one-attr rewrite; no gsap on markers
            relabelMarker(activeDrag.el);
            channelFor("tile:" + activeDrag.el.dataset.tileId, tileUrl(activeDrag.el.dataset.tileId)).schedule({ pos_x: nx, pos_y: ny });
        }
        function onMarkerUp() {
            if (!activeDrag || activeDrag.type !== "marker") return;
            var drag = activeDrag;
            endMarkerPointer(drag);
            drag.el.classList.remove("dragging");
            activeDrag = null;
            if (drag.moved) {
                suppressClick = true; // swallow the trailing synthetic click so the <a> can't navigate
                drag.el.__revert = snapshotMarker(drag.el);
                channelFor("tile:" + drag.el.dataset.tileId, tileUrl(drag.el.dataset.tileId)).flush();
                announce(markerName(drag.el) + " moved to " + pct(parseFloat(drag.el.dataset.posX)) + " " + pct(parseFloat(drag.el.dataset.posY)) + ".");
            }
        }
        function onMarkerCancelEv() {
            if (!activeDrag || activeDrag.type !== "marker") return;
            cancelDrag(false);
        }

        // Mode-preserving reload after a place (see "reload-vs-insert" note): flush every channel
        // first (read-after-write safety), stash the mode + new tile id, then do a FULL page reload so
        // floorplan.js re-wires pan/zoom on the fresh SVG. On the next mount we restore mode + focus.
        reloadHook = function (tile) {
            flushAllPatches().then(function () {
                try {
                    sessionStorage.setItem("fpDesiredMode", uiMode);
                    if (tile && tile.id != null) sessionStorage.setItem("fpSelectTile", String(tile.id));
                } catch (err) { /* private-mode / disabled storage: reload still correct, just no restore */ }
                window.location.reload();
            });
        };

        // ── Place picker (type select → object search → POST place) ──
        setupPlacePicker(svg, {
            container: container,
            announce: announce,
            afterPlace: function (tile) {
                if (reloadHook) reloadHook(tile);
                else flushAllPatches().then(function () { window.location.reload(); });
            },
        });

        // ── Wire and enable the mode buttons (they ship disabled) ──
        modeButtons.forEach(function (btn) {
            btn.disabled = false;
            btn.removeAttribute("aria-disabled");
            btn.addEventListener("click", function () { setMode(btn.dataset.mode); });
        });

        // ── Mirror the in-SVG legend into the HTML SR channel ──
        mirrorLegend(svg);

        // ── Arm accessibility on the freshly mounted SVG ──
        svg.dataset.uiMode = "view";
        syncRoleForMode("view");
        armRovingTabindex();

        // Restore the edit mode + focus the just-placed marker after a place-triggered full reload.
        try {
            var desiredMode = sessionStorage.getItem("fpDesiredMode");
            var selectTile = sessionStorage.getItem("fpSelectTile");
            sessionStorage.removeItem("fpDesiredMode");
            sessionStorage.removeItem("fpSelectTile");
            if (desiredMode && desiredMode !== "view") setMode(desiredMode);
            if (selectTile) {
                var esc = window.CSS && CSS.escape ? CSS.escape(selectTile) : selectTile;
                var justPlaced = svg.querySelector('g.object[data-tile-id="' + esc + '"]');
                if (justPlaced) focusMarker(justPlaced);
            }
        } catch (err) { /* storage unavailable: nothing to restore */ }

        current = {
            svg: svg,
            setMode: setMode,
            flushAll: flushAllPatches,
            cancelDrag: cancelDrag,
            get uiMode() { return uiMode; },
            setReloadHook: function (fn) { reloadHook = fn; },
            channelFor: channelFor,
            placeDragStart: startMarkerDrag, // capture router hands place-mode marker gestures here
            teardown: function () {
                if (activeDrag) cancelDrag(false);
                teardownCalibrate();
                Object.keys(channels).forEach(function (k) { channels[k].abort(); });
                svg.removeEventListener("pointerdown", onPointerDownCapture, true);
                svg.removeEventListener("mousedown", onMouseDownCapture, true);
                svg.removeEventListener("mousemove", onMouseMoveCapture, true);
                svg.removeEventListener("click", onClickCapture, true);
                svg.removeEventListener("wheel", onWheelCapture, true);
            },
        };
    }

    // ── Legend mirror (HTML flow copy of the in-SVG, aria-hidden legend) ──
    function mirrorLegend(svg) {
        var host = document.getElementById("floor-plan-legend");
        if (!host) return;
        host.textContent = "";
        var labels = svg.querySelectorAll(".legend-label");
        if (!labels.length) {
            host.hidden = true;
            return;
        }
        host.hidden = false;
        var ul = document.createElement("ul");
        ul.className = "floor-plan-legend-list";
        labels.forEach(function (t) {
            var li = document.createElement("li");
            li.textContent = t.textContent;
            ul.appendChild(li);
        });
        host.appendChild(ul);
    }

    // ── Place picker (self-contained; POSTs to data-place-api) ──
    function setupPlacePicker(svg, ctx) {
        var container = ctx.container;
        var placeApi = container.getAttribute("data-place-api");
        var typesApi = container.getAttribute("data-placeable-types-api");
        var floorplanId = container.getAttribute("data-floorplan-id");
        var typeSelect = document.getElementById("place-type-select");
        var objInput = document.getElementById("place-object-input");
        var listbox = document.getElementById("place-object-listbox");
        var commitBtn = document.getElementById("place-commit");
        if (!placeApi || !typesApi || !typeSelect || !objInput || !commitBtn) return;

        var typesByKey = {};
        var selectedObject = null; // { id, display }
        var searchTimer = null;
        var ctpkCache = {};
        var listUrlCache = {};

        fetch(typesApi, { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                typeSelect.innerHTML = "";
                (data.placeable_types || []).forEach(function (row) {
                    typesByKey[row.content_type] = row;
                    var opt = document.createElement("option");
                    opt.value = row.content_type;
                    opt.textContent = row.label;
                    typeSelect.appendChild(opt);
                });
                if (!typeSelect.options.length) {
                    var opt = document.createElement("option");
                    opt.value = "";
                    opt.textContent = "No placeable types";
                    typeSelect.appendChild(opt);
                } else {
                    objInput.disabled = false;
                }
            })
            .catch(function () {
                typeSelect.innerHTML = '<option value="">Failed to load types</option>';
            });

        function contentTypePk(dotted) {
            if (ctpkCache[dotted]) return Promise.resolve(ctpkCache[dotted]);
            var parts = dotted.split(".");
            return fetch("/api/extras/content-types/?app_label=" + parts[0] + "&model=" + parts[1] + "&limit=1", {
                headers: { Accept: "application/json" },
            })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    var pk = d.results && d.results[0] && d.results[0].id;
                    if (pk) ctpkCache[dotted] = pk;
                    return pk;
                });
        }
        // Resolve a model's REST list URL from the app API root (no fragile client-side pluralization).
        function listUrlFor(dotted) {
            if (listUrlCache[dotted]) return Promise.resolve(listUrlCache[dotted]);
            var parts = dotted.split(".");
            var app = parts[0];
            var model = parts[1];
            return fetch("/api/" + app + "/", { headers: { Accept: "application/json" } })
                .then(function (r) { return r.json(); })
                .then(function (root) {
                    var best = null;
                    Object.keys(root).forEach(function (slug) {
                        var norm = slug.replace(/-/g, "").replace(/(es|s)$/, "");
                        if (norm === model || slug.replace(/-/g, "") === model + "s") best = root[slug];
                    });
                    if (best) listUrlCache[dotted] = best;
                    return best;
                });
        }

        function closeList() {
            listbox.hidden = true;
            listbox.innerHTML = "";
            objInput.setAttribute("aria-expanded", "false");
        }
        function refreshCommitState() {
            commitBtn.disabled = !(typeSelect.value && selectedObject);
        }
        function runSearch() {
            var dotted = typeSelect.value;
            var row = typesByKey[dotted];
            var q = objInput.value.trim();
            if (!dotted || !row) return;
            listUrlFor(dotted).then(function (listUrl) {
                if (!listUrl) return;
                var params = new URLSearchParams(row.object_source.params || {});
                if (q) params.set("q", q);
                params.set("limit", "20");
                params.set("depth", "0");
                fetch(listUrl + "?" + params.toString(), { headers: { Accept: "application/json" } })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        listbox.innerHTML = "";
                        (data.results || []).forEach(function (obj) {
                            var li = document.createElement("li");
                            li.setAttribute("role", "option");
                            li.textContent = obj.display || obj.name || obj.id;
                            li.addEventListener("mousedown", function (ev) {
                                ev.preventDefault();
                                selectedObject = { id: obj.id, display: li.textContent };
                                objInput.value = li.textContent;
                                closeList();
                                refreshCommitState();
                            });
                            listbox.appendChild(li);
                        });
                        listbox.hidden = !listbox.children.length;
                        objInput.setAttribute("aria-expanded", listbox.hidden ? "false" : "true");
                    });
            });
        }

        typeSelect.addEventListener("change", function () {
            selectedObject = null;
            objInput.value = "";
            closeList();
            refreshCommitState();
        });
        objInput.addEventListener("input", function () {
            selectedObject = null;
            refreshCommitState();
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(runSearch, 200);
        });
        objInput.addEventListener("focus", runSearch);
        objInput.addEventListener("blur", function () { setTimeout(closeList, 150); });

        commitBtn.addEventListener("click", function () {
            var dotted = typeSelect.value;
            if (!dotted || !selectedObject) return;
            // Drop at the center of the CURRENT view so it is visible; user drags it into place.
            var vb = (svg.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
            var content = readContentRect(svg);
            var cxUser = vb.length === 4 ? vb[0] + vb[2] / 2 : content.x + content.w / 2;
            var cyUser = vb.length === 4 ? vb[1] + vb[3] / 2 : content.y + content.h / 2;
            var posX = clamp((cxUser - content.x) / content.w, 0, 1);
            var posY = clamp((cyUser - content.y) / content.h, 0, 1);
            commitBtn.disabled = true;
            contentTypePk(dotted).then(function (ctpk) {
                if (!ctpk) {
                    ctx.announce("Could not resolve the content type.");
                    commitBtn.disabled = false;
                    return;
                }
                fetch(placeApi, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                    body: JSON.stringify({
                        floor_plan: floorplanId,
                        placed_content_type: ctpk,
                        placed_object_id: selectedObject.id,
                        pos_x: posX,
                        pos_y: posY,
                        rotation: 0,
                    }),
                })
                    .then(function (resp) {
                        return resp.json().then(function (body) { return { ok: resp.ok, body: body }; });
                    })
                    .then(function (res) {
                        if (res.ok) {
                            ctx.announce("Placed " + selectedObject.display + ". Drag it into position.");
                            selectedObject = null;
                            objInput.value = "";
                            ctx.afterPlace(res.body);
                        } else {
                            var msg = typeof res.body === "object" ? JSON.stringify(res.body) : String(res.body);
                            ctx.announce("Placement rejected. " + msg);
                            commitBtn.disabled = false;
                        }
                    })
                    .catch(function () {
                        ctx.announce("Placement failed (network).");
                        commitBtn.disabled = false;
                    });
            });
        });
    }

    // ── Install-once document/window listeners (delegate through `current`) ──
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape" || !current) return;
        // Let the place combobox handle its own Escape; don't yank the user out of the mode.
        var ae = document.activeElement;
        if (ae && ae.closest && ae.closest("#place-object-panel")) return;
        if (current.uiMode === "view") return;
        current.setMode("view");
    });
    window.addEventListener("pagehide", function () {
        if (current) current.flushAll();
    });
    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "hidden" && current) current.flushAll();
    });

    // ── Self-mount: watch the container for the injected <svg> and (re)mount editing ──
    function tryMount() {
        var svg = container.querySelector("svg");
        if (svg && !svg.__fpEditingMounted) {
            if (current && current.svg !== svg) {
                current.teardown();
                current = null;
            }
            mountEditing(svg);
        }
    }
    var mountObserver = new MutationObserver(tryMount);
    mountObserver.observe(container, { childList: true });
    tryMount(); // in case the SVG is already present

    // Public surface for the place / a11y agents to integrate against.
    window.FloorPlanEditing = {
        getController: function () { return current; },
        setReloadHook: function (fn) { if (current) current.setReloadHook(fn); },
    };
}

// The <script> is included before the #floor-plan-svg container in the template (matching
// floorplan.js), so defer until the DOM is parsed before self-mounting.
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFloorPlanEditing);
} else {
    initFloorPlanEditing();
}
