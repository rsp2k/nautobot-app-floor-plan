/**
 * Floor plan layers: client-side show/hide and dim of placed-object markers.
 *
 * Two kinds of group compose, with named layers taking precedence:
 *   - Named layers (data-layers-api), whose membership the server resolved into each marker's
 *     `data-layers`. If a marker is in any named layer, ONLY its layers govern it.
 *   - Content-type toggles, derived from each marker's `data-content-type`, govern markers that
 *     belong to no named layer (the "AP layer" for free, with no server state).
 * So a layer's checkbox hides its own members directly, and turning a type off only touches the
 * ungrouped markers of that type. A marker is visible when any of its governing groups is on
 * (OR across a marker's layers). Opacity is the strongest (lowest) dim among the groups showing it.
 * Status color is never touched.
 *
 * Purely client-side (CSS display/opacity on already-rendered markers), so it can't affect the
 * viewBox-based pan/zoom. Standalone, like floorplan-import.js.
 */
(function () {
  "use strict";

  function injectStyles() {
    if (document.getElementById("floor-plan-layers-styles")) return;
    const css = `
      .floor-plan-layers-panel { display: inline-block; vertical-align: top; margin-top: 6px;
        padding: 8px 10px; border: 1px solid var(--nb-border-color, #cfd6dd); border-radius: 6px;
        background: var(--nb-body-bg, #fff); min-width: 240px; }
      .floor-plan-layers-panel .layer-row { display: flex; align-items: center; gap: 8px;
        padding: 3px 0; }
      .floor-plan-layers-panel .layer-row label { margin: 0; flex: 1 1 auto; cursor: pointer;
        white-space: nowrap; }
      .floor-plan-layers-panel .layer-swatch { width: 12px; height: 12px; border-radius: 2px;
        display: inline-block; border: 1px solid rgba(0,0,0,0.2); }
      .floor-plan-layers-panel input[type="range"] { width: 90px; }
      .floor-plan-layers-panel .layer-heading { font-size: 12px; color: var(--nb-text-muted, #6b7f95);
        text-transform: uppercase; margin: 6px 0 2px; }
      .floor-plan-layers-panel .layer-actions { display: flex; gap: 8px; margin-top: 8px; }
    `;
    const style = document.createElement("style");
    style.id = "floor-plan-layers-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  function prettifyType(key) {
    const model = (key.split(".")[1] || key).replace(/[_-]+/g, " ");
    return model.replace(/\b\w/g, (c) => c.toUpperCase());
  }

  document.addEventListener("DOMContentLoaded", function () {
    const container = document.getElementById("floor-plan-svg");
    const button = document.getElementById("layers-button");
    const panel = document.getElementById("layers-panel");
    if (!container || !button || !panel) return;

    injectStyles();

    const layersApi = container.getAttribute("data-layers-api"); // Slice B; may be null
    // group state: content-type groups keyed "ct:<dotted>", named layers keyed "layer:<uuid>"
    const groups = new Map();
    let namedLayers = []; // [{id, name, color, opacity, default_visible}]

    function markers() {
      const svg = container.querySelector("svg");
      return svg ? svg.querySelectorAll("g.object[data-content-type]") : [];
    }

    function applyLayers() {
      markers().forEach((g) => {
        // Named layers take precedence: a marker in any named layer is governed only by its layers.
        // A marker in no named layer falls back to its content-type group.
        const layerMemberships = (g.getAttribute("data-layers") || "")
          .split(/\s+/)
          .filter(Boolean)
          .map((id) => groups.get("layer:" + id))
          .filter(Boolean);
        const memberships = layerMemberships.length
          ? layerMemberships
          : [groups.get("ct:" + g.getAttribute("data-content-type"))].filter(Boolean);

        // Visible when any governing group is on (OR across a multi-layer marker's layers).
        const visible = memberships.length === 0 || memberships.some((m) => m.visible);
        const target = g.closest("a") || g;
        target.style.display = visible ? "" : "none";

        // Dim to the strongest (lowest) opacity among the groups currently showing it.
        const opacities = memberships.filter((m) => m.visible).map((m) => m.opacity);
        const op = opacities.length ? Math.min.apply(null, opacities) : 100;
        g.style.opacity = op >= 100 ? "" : String(op / 100);
      });
    }

    function rowFor(key, title, opts) {
      opts = opts || {};
      const state = { visible: opts.visible !== false, opacity: opts.opacity == null ? 100 : opts.opacity };
      groups.set(key, state);
      const row = document.createElement("div");
      row.className = "layer-row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.visible;
      const label = document.createElement("label");
      if (opts.color) {
        const sw = document.createElement("span");
        sw.className = "layer-swatch";
        sw.style.background = "#" + opts.color;
        label.appendChild(sw);
        label.appendChild(document.createTextNode(" "));
      }
      label.appendChild(document.createTextNode(title));
      const dim = document.createElement("input");
      dim.type = "range";
      dim.min = "0";
      dim.max = "100";
      dim.value = String(state.opacity);
      dim.title = "Dim";
      cb.addEventListener("change", () => {
        state.visible = cb.checked;
        applyLayers();
      });
      dim.addEventListener("input", () => {
        state.opacity = parseInt(dim.value, 10);
        applyLayers();
      });
      row.appendChild(cb);
      row.appendChild(label);
      row.appendChild(dim);
      return row;
    }

    function buildPanel() {
      groups.clear();
      panel.innerHTML = "";

      if (namedLayers.length) {
        const h = document.createElement("div");
        h.className = "layer-heading";
        h.textContent = "Layers";
        panel.appendChild(h);
        namedLayers
          .slice()
          .sort((a, b) => (a.display_order || 0) - (b.display_order || 0))
          .forEach((layer) => {
            panel.appendChild(
              rowFor("layer:" + layer.id, layer.name, {
                visible: layer.default_visible !== false,
                opacity: layer.opacity == null ? 100 : layer.opacity,
                color: layer.color || null,
              })
            );
          });
      }

      const types = new Set();
      markers().forEach((g) => types.add(g.getAttribute("data-content-type")));
      if (types.size) {
        const h = document.createElement("div");
        h.className = "layer-heading";
        h.textContent = namedLayers.length ? "By type" : "Object types";
        panel.appendChild(h);
        Array.from(types)
          .sort()
          .forEach((ct) => panel.appendChild(rowFor("ct:" + ct, prettifyType(ct))));
      }

      if (!types.size && !namedLayers.length) {
        panel.textContent = "No placed objects to filter.";
      }
      applyLayers();
    }

    async function loadNamedLayers() {
      if (!layersApi) return;
      try {
        const resp = await fetch(layersApi, { headers: { Accept: "application/json" } });
        if (resp.ok) {
          const data = await resp.json();
          namedLayers = data.layers || [];
        }
      } catch (err) {
        /* type toggles still work */
      }
    }

    // Reveal the button only once the SVG has markers (it loads asynchronously).
    let built = false;
    const waiter = setInterval(() => {
      if (markers().length) {
        clearInterval(waiter);
        button.hidden = false;
      }
    }, 250);
    setTimeout(() => clearInterval(waiter), 15000);

    button.addEventListener("click", async () => {
      const show = panel.hidden;
      if (show && !built) {
        await loadNamedLayers();
        buildPanel();
        built = true;
      }
      panel.hidden = !show;
      button.setAttribute("aria-expanded", String(show));
    });
  });
})();
