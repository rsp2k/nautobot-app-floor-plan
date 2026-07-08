/**
 * Blueprint PDF import: upload a PDF, render its pages server-side (Nautobot Job), pick a page,
 * crop the drawing region, orient it, and set it as the floor plan's background_image.
 *
 * Self-contained and decoupled from the drag/calibrate layer in floorplan_editing.js: it wires a
 * standalone "Import from PDF" button and a modal. Crop is done on an <img> of the rendered page
 * (crop-then-rotate contract), so it never touches the calibrate overlay, which keeps its own job
 * of aligning an already-set background to the grid.
 */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return match ? match.pop() : "";
  }

  function injectStyles() {
    if (document.getElementById("blueprint-import-styles")) return;
    const css = `
      .blueprint-import-modal { position: fixed; inset: 0; z-index: 1050; background: rgba(0,0,0,0.5);
        display: flex; align-items: center; justify-content: center; }
      .blueprint-import-dialog { background: var(--nb-body-bg, #fff); color: var(--nb-body-color, #1a1a1a);
        width: min(920px, 94vw); max-height: 92vh; overflow: auto; border-radius: 8px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.35); }
      .blueprint-import-head { display: flex; align-items: center; justify-content: space-between;
        padding: 12px 16px; border-bottom: 1px solid var(--nb-border-color, #cfd6dd); }
      .blueprint-import-body { padding: 16px; }
      .blueprint-import-pages { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 10px; }
      .blueprint-page-tile { border: 1px solid var(--nb-border-color, #cfd6dd); border-radius: 6px;
        padding: 6px; background: #fff; cursor: pointer; text-align: center; }
      .blueprint-page-tile:hover, .blueprint-page-tile:focus-visible { outline: 2px solid #1c7ed6; }
      .blueprint-page-tile img { width: 100%; height: auto; display: block; }
      .blueprint-page-tile .label { font-size: 12px; color: var(--nb-text-muted, #6b7f95); margin-top: 4px; }
      .blueprint-import-crop-tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
        margin-bottom: 10px; }
      .blueprint-import-stage { position: relative; display: inline-block; max-width: 100%;
        transform-origin: center center; }
      .blueprint-import-stage img { display: block; max-width: 100%; height: auto; user-select: none; }
      .blueprint-crop-box { position: absolute; border: 2px dashed #1c7ed6; background: rgba(28,126,214,0.08);
        cursor: move; box-sizing: border-box; }
      .blueprint-crop-handle { position: absolute; width: 14px; height: 14px; background: #fff;
        border: 2px solid #1c7ed6; border-radius: 2px; }
      .blueprint-crop-handle[data-corner="nw"] { left: -8px; top: -8px; cursor: nwse-resize; }
      .blueprint-crop-handle[data-corner="ne"] { right: -8px; top: -8px; cursor: nesw-resize; }
      .blueprint-crop-handle[data-corner="se"] { right: -8px; bottom: -8px; cursor: nwse-resize; }
      .blueprint-crop-handle[data-corner="sw"] { left: -8px; bottom: -8px; cursor: nesw-resize; }
    `;
    const style = document.createElement("style");
    style.id = "blueprint-import-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const container = document.getElementById("floor-plan-svg");
    if (!container) return;
    const importApi = container.getAttribute("data-import-pdf-api");
    const pagesApi = container.getAttribute("data-pages-api");
    const extractApi = container.getAttribute("data-extract-api");
    const jobResultApi = container.getAttribute("data-jobresult-api");
    const button = document.getElementById("import-pdf-button");
    const modal = document.getElementById("blueprint-import-modal");
    if (!importApi || !pagesApi || !extractApi || !button || !modal) return;

    injectStyles();
    button.hidden = false;

    const els = {
      close: document.getElementById("blueprint-import-close"),
      file: document.getElementById("blueprint-import-file"),
      uploadBtn: document.getElementById("blueprint-import-upload-btn"),
      status: document.getElementById("blueprint-import-status"),
      upload: document.getElementById("blueprint-import-upload"),
      pages: document.getElementById("blueprint-import-pages"),
      crop: document.getElementById("blueprint-import-crop"),
      cropImage: document.getElementById("blueprint-crop-image"),
      cropBox: document.getElementById("blueprint-crop-box"),
      stage: document.getElementById("blueprint-crop-stage"),
      back: document.getElementById("blueprint-crop-back"),
      rotL: document.getElementById("blueprint-crop-rotate-l"),
      rotR: document.getElementById("blueprint-crop-rotate-r"),
      commit: document.getElementById("blueprint-crop-commit"),
    };
    const csrf = getCookie("csrftoken");
    const headers = { "X-CSRFToken": csrf };
    let rotation = 0;
    let currentPage = null;

    function setStatus(text) {
      els.status.textContent = text || "";
    }
    function showStep(step) {
      els.upload.hidden = step !== "upload";
      els.pages.hidden = step !== "pages";
      els.crop.hidden = step !== "crop";
    }
    function openModal() {
      modal.hidden = false;
      showStep("upload");
      setStatus("");
      els.file.value = "";
      loadPages();  // if pages already exist from a prior render, jump to the picker
    }
    function closeModal() {
      modal.hidden = true;
    }

    button.addEventListener("click", openModal);
    els.close.addEventListener("click", closeModal);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });

    async function loadPages() {
      try {
        const resp = await fetch(pagesApi, { headers: { Accept: "application/json" } });
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.pages && data.pages.length) renderGrid(data.pages);
      } catch (err) {
        /* no-op: upload will populate */
      }
    }

    function renderGrid(pages) {
      els.pages.innerHTML = "";
      pages.forEach((page) => {
        const tile = document.createElement("div");
        tile.className = "blueprint-page-tile";
        tile.tabIndex = 0;
        tile.setAttribute("role", "button");
        tile.innerHTML =
          `<img src="${page.thumbnail_url}" alt="Page ${page.page_number}">` +
          `<div class="label">Page ${page.page_number}</div>`;
        const open = () => openCrop(page);
        tile.addEventListener("click", open);
        tile.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        });
        els.pages.appendChild(tile);
      });
      showStep("pages");
    }

    els.uploadBtn.addEventListener("click", async () => {
      const file = els.file.files && els.file.files[0];
      if (!file) {
        setStatus("Choose a PDF first.");
        return;
      }
      const form = new FormData();
      form.append("file", file);
      setStatus("Uploading…");
      els.uploadBtn.disabled = true;
      try {
        const resp = await fetch(importApi, { method: "POST", headers, body: form });
        if (!resp.ok) {
          const detail = await resp.json().catch(() => ({}));
          setStatus("Upload failed: " + (detail.file || detail.detail || resp.status));
          return;
        }
        const data = await resp.json();
        setStatus("Rendering pages…");
        await waitForRender(data.job_result);
      } catch (err) {
        setStatus("Upload error: " + err);
      } finally {
        els.uploadBtn.disabled = false;
      }
    });

    async function waitForRender(jobResultId) {
      const deadline = Date.now() + 120000;  // 2 min
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        // Prefer page count; fall back to job status for failures.
        const resp = await fetch(pagesApi, { headers: { Accept: "application/json" } });
        if (resp.ok) {
          const data = await resp.json();
          if (data.pages && data.pages.length) {
            renderGrid(data.pages);
            return;
          }
        }
        if (jobResultId && jobResultApi) {
          const jr = await fetch(`${jobResultApi}${jobResultId}/`, { headers: { Accept: "application/json" } });
          if (jr.ok) {
            const jrData = await jr.json();
            const st = (jrData.status && (jrData.status.value || jrData.status)) || "";
            if (String(st).toLowerCase() === "failure") {
              setStatus("Rendering failed — check the Job Result for details.");
              return;
            }
          }
        }
      }
      setStatus("Timed out waiting for rendering.");
    }

    // ----- crop step -----
    function openCrop(page) {
      currentPage = page;
      rotation = 0;
      els.stage.style.transform = "rotate(0deg)";
      els.cropBox.hidden = true;
      els.cropImage.onload = () => initCropBox();
      els.cropImage.src = page.image_url;
      showStep("crop");
    }

    function initCropBox() {
      const w = els.cropImage.offsetWidth;
      const h = els.cropImage.offsetHeight;
      // Default to a 10% inset so the box is visible and grabbable.
      setBox(w * 0.1, h * 0.1, w * 0.8, h * 0.8);
      els.cropBox.hidden = false;
    }

    function setBox(left, top, width, height) {
      const imgW = els.cropImage.offsetWidth;
      const imgH = els.cropImage.offsetHeight;
      const MIN = 20;
      width = Math.max(MIN, Math.min(width, imgW));
      height = Math.max(MIN, Math.min(height, imgH));
      left = Math.max(0, Math.min(left, imgW - width));
      top = Math.max(0, Math.min(top, imgH - height));
      els.cropBox.style.left = left + "px";
      els.cropBox.style.top = top + "px";
      els.cropBox.style.width = width + "px";
      els.cropBox.style.height = height + "px";
    }

    function boxRect() {
      return {
        left: parseFloat(els.cropBox.style.left) || 0,
        top: parseFloat(els.cropBox.style.top) || 0,
        width: parseFloat(els.cropBox.style.width) || 0,
        height: parseFloat(els.cropBox.style.height) || 0,
      };
    }

    let drag = null;
    els.cropBox.addEventListener("pointerdown", (e) => {
      const corner = e.target.getAttribute && e.target.getAttribute("data-corner");
      drag = { corner, startX: e.clientX, startY: e.clientY, rect: boxRect() };
      els.cropBox.setPointerCapture(e.pointerId);
      e.preventDefault();
      e.stopPropagation();
    });
    els.cropBox.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      const r = drag.rect;
      if (!drag.corner) {
        setBox(r.left + dx, r.top + dy, r.width, r.height);
        return;
      }
      let { left, top, width, height } = r;
      if (drag.corner.includes("w")) {
        left = r.left + dx;
        width = r.width - dx;
      }
      if (drag.corner.includes("e")) {
        width = r.width + dx;
      }
      if (drag.corner.includes("n")) {
        top = r.top + dy;
        height = r.height - dy;
      }
      if (drag.corner.includes("s")) {
        height = r.height + dy;
      }
      setBox(left, top, width, height);
    });
    const endDrag = () => {
      drag = null;
    };
    els.cropBox.addEventListener("pointerup", endDrag);
    els.cropBox.addEventListener("pointercancel", endDrag);

    function applyRotation() {
      els.stage.style.transform = `rotate(${rotation}deg)`;
    }
    els.rotL.addEventListener("click", () => {
      rotation = (rotation + 270) % 360;
      applyRotation();
    });
    els.rotR.addEventListener("click", () => {
      rotation = (rotation + 90) % 360;
      applyRotation();
    });
    els.back.addEventListener("click", () => showStep("pages"));

    els.commit.addEventListener("click", async () => {
      if (!currentPage) return;
      const imgW = els.cropImage.offsetWidth;
      const imgH = els.cropImage.offsetHeight;
      const r = boxRect();
      const cropBox = [r.left / imgW, r.top / imgH, r.width / imgW, r.height / imgH];
      els.commit.disabled = true;
      try {
        const resp = await fetch(extractApi, {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, headers),
          body: JSON.stringify({ page_number: currentPage.page_number, crop_box: cropBox, rotation }),
        });
        if (!resp.ok) {
          const detail = await resp.json().catch(() => ({}));
          setStatus("Extract failed: " + (detail.detail || JSON.stringify(detail)));
          return;
        }
        // Background set — reload so the plan re-renders with the new blueprint + calibrate controls.
        window.location.reload();
      } catch (err) {
        setStatus("Extract error: " + err);
      } finally {
        els.commit.disabled = false;
      }
    });
  });
})();
