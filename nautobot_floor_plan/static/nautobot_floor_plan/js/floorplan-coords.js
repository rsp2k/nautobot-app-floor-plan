/*
 * floorplan-coords.js — pure coordinate transforms for the freeform interaction layer.
 *
 * Zero DOM dependency: screenToUser takes a plain affine matrix (the a,b,c,d,e,f
 * you get from svgElement.getScreenCTM(), or any {a,b,c,d,e,f}) and inverts it by
 * hand instead of leaning on SVGPoint.matrixTransform, so every function here runs
 * under node/jsdom with plain objects. UMD wrapper: require() in tests, global
 * FloorPlanCoords in the browser (floorplan.js is loaded as a bare <script>, no bundler).
 *
 * Normalized space is anisotropic: nx = (ux - rect.x) / rect.w, ny = (uy - rect.y) / rect.h,
 * where rect is the content rect published by svg.py as data-content-{x,y,w,h}. Keep all
 * math in user units; divide by w/h only at store time.
 */
(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.FloorPlanCoords = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    // Invert a 2-D affine [a c e / b d f]. Returns null for a degenerate matrix.
    function invertAffine(m) {
        var det = m.a * m.d - m.b * m.c;
        if (!det || !isFinite(det)) return null;
        var id = 1 / det;
        return {
            a: m.d * id,
            b: -m.b * id,
            c: -m.c * id,
            d: m.a * id,
            e: (m.c * m.f - m.d * m.e) * id,
            f: (m.b * m.e - m.a * m.f) * id,
        };
    }

    // Screen/client px -> SVG user units, via the INVERSE of a getScreenCTM()-shaped matrix.
    // ctm may be an SVGMatrix or a plain {a,b,c,d,e,f}. Returns null if ctm is not invertible.
    function screenToUser(clientX, clientY, ctm) {
        var inv = invertAffine(ctm);
        if (!inv) return null;
        return {
            ux: inv.a * clientX + inv.c * clientY + inv.e,
            uy: inv.b * clientX + inv.d * clientY + inv.f,
        };
    }

    // User units -> normalized [0..1] within the content rect (anisotropic).
    function userToNorm(ux, uy, rect) {
        return { nx: (ux - rect.x) / rect.w, ny: (uy - rect.y) / rect.h };
    }

    // Normalized [0..1] -> user units within the content rect.
    function normToUser(nx, ny, rect) {
        return { ux: rect.x + nx * rect.w, uy: rect.y + ny * rect.h };
    }

    // Clamp a single normalized scalar to [0, 1].
    function clampNorm(n) {
        return Math.min(1, Math.max(0, n));
    }

    // Angle (deg, 0..360) from center (cx,cy) to point (ux,uy), SVG y-down.
    // snap>0 rounds to the nearest multiple (e.g. 15) and re-wraps to [0,360).
    function angleDeg(cx, cy, ux, uy, snap) {
        var deg = (Math.atan2(uy - cy, ux - cx) * 180) / Math.PI;
        deg = ((deg % 360) + 360) % 360;
        if (snap && snap > 0) {
            deg = ((Math.round(deg / snap) * snap) % 360 + 360) % 360;
        }
        return deg;
    }

    // Parse the translate()+rotate() transform emitted by _draw_freeform_tile:
    //   "translate(123.4,567.8) rotate(30)"  (comma or whitespace separated).
    // Returns {ux, uy, rot}; missing pieces default to 0.
    function parseTransform(str) {
        var out = { ux: 0, uy: 0, rot: 0 };
        if (!str) return out;
        var t = /translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)/.exec(str);
        if (t) {
            out.ux = parseFloat(t[1]);
            out.uy = parseFloat(t[2]);
        }
        var r = /rotate\(\s*(-?[\d.]+)/.exec(str);
        if (r) out.rot = parseFloat(r[1]);
        return out;
    }

    // Compose the transform string in the exact shape svg.py emits, so a client
    // rewrite and a server re-render are byte-compatible.
    function formatTransform(ux, uy, rot) {
        return "translate(" + ux + "," + uy + ") rotate(" + (rot || 0) + ")";
    }

    return {
        invertAffine: invertAffine,
        screenToUser: screenToUser,
        userToNorm: userToNorm,
        normToUser: normToUser,
        clampNorm: clampNorm,
        angleDeg: angleDeg,
        parseTransform: parseTransform,
        formatTransform: formatTransform,
    };
});
