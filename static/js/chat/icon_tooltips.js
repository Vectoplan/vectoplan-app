/* Larger floating labels for the compact workspace and project icon rails. */
(function initIconTooltips() {
  "use strict";

  var TARGET_SELECTOR = ".workspace-rail__button, .vp-project-sidebar__item";
  var tooltip = null;
  var activeTarget = null;

  function isProjectItem(target) {
    return target && target.classList.contains("vp-project-sidebar__item");
  }

  function canShow(target) {
    if (!target || !target.matches(TARGET_SELECTOR)) {
      return false;
    }

    if (!isProjectItem(target)) {
      return true;
    }

    var sidebar = target.closest("[data-vp-project-sidebar]");
    return !!sidebar && (
      sidebar.classList.contains("is-collapsed") ||
      sidebar.getAttribute("data-project-sidebar-collapsed") === "true"
    );
  }

  function getLabel(target) {
    if (!target) {
      return "";
    }

    var hiddenLabel = target.querySelector(".workspace-rail__label");
    return (
      (hiddenLabel && hiddenLabel.textContent) ||
      target.getAttribute("data-project-title") ||
      target.getAttribute("data-icon-tooltip") ||
      target.getAttribute("aria-label") ||
      target.getAttribute("title") ||
      ""
    ).trim();
  }

  function ensureTooltip() {
    if (tooltip) {
      return tooltip;
    }

    tooltip = document.createElement("div");
    tooltip.className = "vp-icon-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.setAttribute("aria-hidden", "true");
    document.body.appendChild(tooltip);
    return tooltip;
  }

  function positionTooltip(target) {
    var node = ensureTooltip();
    var targetRect = target.getBoundingClientRect();
    var tooltipRect = node.getBoundingClientRect();
    var gap = 12;
    var edge = 8;
    var left = targetRect.right + gap;
    var top = targetRect.top + ((targetRect.height - tooltipRect.height) / 2);
    var placeLeft = left + tooltipRect.width > window.innerWidth - edge;

    if (placeLeft) {
      left = targetRect.left - tooltipRect.width - gap;
    }

    left = Math.max(edge, Math.min(left, window.innerWidth - tooltipRect.width - edge));
    top = Math.max(edge, Math.min(top, window.innerHeight - tooltipRect.height - edge));

    node.classList.toggle("is-left", placeLeft);
    node.style.left = Math.round(left) + "px";
    node.style.top = Math.round(top) + "px";
  }

  function show(target) {
    if (!canShow(target)) {
      return;
    }

    var label = getLabel(target);
    if (!label) {
      return;
    }

    var nativeTitle = target.getAttribute("title");
    if (nativeTitle) {
      target.setAttribute("data-vp-icon-tooltip-native-title", nativeTitle);
      target.removeAttribute("title");
    }

    var node = ensureTooltip();
    activeTarget = target;
    node.textContent = label;
    node.classList.remove("is-left");
    node.classList.add("is-visible");
    node.setAttribute("aria-hidden", "false");
    positionTooltip(target);
  }

  function hide() {
    if (!tooltip) {
      activeTarget = null;
      return;
    }

    tooltip.classList.remove("is-visible", "is-left");
    tooltip.setAttribute("aria-hidden", "true");
    activeTarget = null;
  }

  function closestTarget(node) {
    return node && node.closest ? node.closest(TARGET_SELECTOR) : null;
  }

  document.addEventListener("mouseover", function onMouseOver(event) {
    var target = closestTarget(event.target);
    if (!target || target === activeTarget) {
      return;
    }
    show(target);
  });

  document.addEventListener("mouseout", function onMouseOut(event) {
    if (!activeTarget) {
      return;
    }
    if (event.relatedTarget && activeTarget.contains(event.relatedTarget)) {
      return;
    }
    hide();
  });

  document.addEventListener("focusin", function onFocusIn(event) {
    var target = closestTarget(event.target);
    if (target) {
      show(target);
    }
  });

  document.addEventListener("focusout", function onFocusOut(event) {
    if (!activeTarget) {
      return;
    }
    if (event.relatedTarget && activeTarget.contains(event.relatedTarget)) {
      return;
    }
    hide();
  });

  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("keydown", function onKeyDown(event) {
    if (event.key === "Escape") {
      hide();
    }
  });
  window.addEventListener("resize", hide);
  window.addEventListener("scroll", hide, true);
})();
