/*
 * Pure-transform tests for floorplan-coords.js (Wave D).
 *
 * floorplan-coords.js has ZERO DOM dependency (it inverts a plain {a,b,c,d,e,f} affine by hand
 * instead of using SVGPoint), so these run under bare node — no jsdom needed. Run with:
 *     node --test nautobot_floor_plan/tests/js/floorplan-coords.test.js
 * If you prefer a jsdom environment (to mirror the browser globals), wrap with jsdom-global; the
 * assertions below are environment-independent.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../static/nautobot_floor_plan/js/floorplan-coords.js");

const RECT = { x: 100, y: 50, w: 800, h: 600 };
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} ≈ ${b}`);

test("userToNorm/normToUser round-trip is identity", () => {
    for (const [ux, uy] of [[100, 50], [500, 350], [900, 650], [123.4, 567.8]]) {
        const n = C.userToNorm(ux, uy, RECT);
        const u = C.normToUser(n.nx, n.ny, RECT);
        near(u.ux, ux);
        near(u.uy, uy);
    }
});

test("userToNorm is anisotropic (independent x/y scaling)", () => {
    const n = C.userToNorm(RECT.x + RECT.w, RECT.y, RECT);
    near(n.nx, 1);
    near(n.ny, 0);
});

test("clampNorm bounds to [0,1]", () => {
    assert.equal(C.clampNorm(-0.2), 0);
    assert.equal(C.clampNorm(1.4), 1);
    assert.equal(C.clampNorm(0.5), 0.5);
});

test("screenToUser inverts a scale+translate CTM", () => {
    // ctm maps user->screen: screen = 2*user + 30
    const ctm = { a: 2, b: 0, c: 0, d: 2, e: 30, f: 30 };
    const back = C.screenToUser(2 * 500 + 30, 2 * 350 + 30, ctm);
    near(back.ux, 500);
    near(back.uy, 350);
});

test("screenToUser returns null for a degenerate matrix", () => {
    assert.equal(C.screenToUser(1, 1, { a: 0, b: 0, c: 0, d: 0, e: 0, f: 0 }), null);
});

test("angleDeg: right=0, down=90, up=270, snap", () => {
    near(C.angleDeg(0, 0, 10, 0, 0), 0);
    near(C.angleDeg(0, 0, 0, 10, 0), 90);
    near(C.angleDeg(0, 0, 0, -10, 0), 270);
    // 7° snapped to nearest 15° -> 0; 8° -> 15
    near(C.angleDeg(0, 0, Math.cos((8 * Math.PI) / 180), Math.sin((8 * Math.PI) / 180), 15), 15);
});

test("parseTransform/formatTransform are byte-compatible with svg.py", () => {
    const t = C.parseTransform("translate(123.4,567.8) rotate(30)");
    near(t.ux, 123.4);
    near(t.uy, 567.8);
    near(t.rot, 30);
    // svg.py emits exactly this shape: "translate(x,y) rotate(r)"
    assert.equal(C.formatTransform(123.4, 567.8, 30), "translate(123.4,567.8) rotate(30)");
    assert.equal(C.formatTransform(1, 2, 0), "translate(1,2) rotate(0)");
});

test("normalized drag clamps a marker dragged past an edge", () => {
    // Simulate a pointer 200px past the right/bottom edge; normalized must clamp to 1.
    const u = C.normToUser(2.5, -0.3, RECT); // out-of-range norm
    const n = C.userToNorm(u.ux, u.uy, RECT);
    assert.equal(C.clampNorm(n.nx), 1);
    assert.equal(C.clampNorm(n.ny), 0);
});
