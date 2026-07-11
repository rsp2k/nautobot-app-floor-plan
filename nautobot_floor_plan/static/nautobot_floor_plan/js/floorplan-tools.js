/**
 * Edit-tools drawer toggle for the floor-plan canvas.
 *
 * Keeps View mode clean: the mode toggle, place picker, blueprint/sizing controls all live in a
 * floating drawer that the gear reveals. Purely presentational — it only shows/hides the drawer that
 * already-loaded modules (floorplan_editing.js, floorplan-import.js) bind their controls into.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("edit-tools-button");
    const panel = document.getElementById("floor-plan-tools-panel");
    if (!button || !panel) return;

    function setOpen(open) {
      panel.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
      button.classList.toggle("active", open);
    }

    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(panel.hidden);
    });

    // Close on Escape (unless focus is inside a control mid-edit), and on a click outside the drawer.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) {
        setOpen(false);
        button.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (panel.hidden) return;
      if (panel.contains(event.target) || button.contains(event.target)) return;
      setOpen(false);
    });
  });
})();
