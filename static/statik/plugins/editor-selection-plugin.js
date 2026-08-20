(() => {
  "use strict";

  class StructuralSelection3DView {
    constructor(canvas, options = {}) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d");
      this.model = null;
      this.selectedRef = null;
      this.faces = [];
      this.onSelect = options.onSelect || (() => {});
      this.resizeObserver = new ResizeObserver(() => this.draw());
      this.resizeObserver.observe(canvas.parentElement || canvas);
      canvas.addEventListener("click", (event) => this.pick(event));
    }

    setModel(model) { this.model = model; this.draw(); }
    setSelected(elementRef) { this.selectedRef = elementRef; this.draw(); }

    size() {
      const rect = this.canvas.getBoundingClientRect();
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      this.canvas.width = Math.max(1, Math.round(rect.width * ratio));
      this.canvas.height = Math.max(1, Math.round(rect.height * ratio));
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { width: rect.width, height: rect.height };
    }

    toModel(point) { return [(Number(point[0]) - 120) / 70, (Number(point[1]) - 120) / 70]; }

    project(point, width, height) {
      const scale = Math.min(width / 15, height / 9.2);
      const [x, y, z] = point;
      return [width * .48 + (x - y) * .82 * scale, height * .68 + (x + y) * .33 * scale - z * .88 * scale];
    }

    polygon(points, fill, stroke, elementRef, depth) {
      this.faces.push({ points, fill, stroke, elementRef, depth });
    }

    box(x, y, z, dx, dy, dz, color, elementRef, width, height) {
      const p = (a, b, c) => this.project([a, b, c], width, height);
      const top = [p(x, y, z + dz), p(x + dx, y, z + dz), p(x + dx, y + dy, z + dz), p(x, y + dy, z + dz)];
      const front = [p(x, y + dy, z), p(x + dx, y + dy, z), p(x + dx, y + dy, z + dz), p(x, y + dy, z + dz)];
      const side = [p(x + dx, y, z), p(x + dx, y + dy, z), p(x + dx, y + dy, z + dz), p(x + dx, y, z + dz)];
      this.polygon(front, color.side, color.stroke, elementRef, x + y + .2);
      this.polygon(side, color.dark, color.stroke, elementRef, x + y + .3);
      this.polygon(top, color.top, color.stroke, elementRef, x + y + dz + 1);
    }

    build(width, height) {
      this.faces = [];
      if (!this.model) return;
      const selected = { top: "#19b8b5", side: "#128d99", dark: "#116579", stroke: "#075467" };
      const neutral = { top: "#dce7e9", side: "#a9bec5", dark: "#7895a0", stroke: "#526d78" };
      const structural = { top: "#91bfc5", side: "#568b98", dark: "#376f80", stroke: "#244f61" };
      this.model.elements.forEach((element) => {
        const color = element.element_ref === this.selectedRef ? selected : (["beam", "column"].includes(element.kind) ? structural : neutral);
        const geometry = element.geometry || {};
        const elevation = element.level_ref === "level_ug" ? -2.8 : element.level_ref === "level_og" ? 2.9 : 0;
        if (geometry.type === "polygon" && geometry.points?.length) {
          const modelPoints = geometry.points.map((point) => this.toModel(point));
          const xs = modelPoints.map((point) => point[0]);
          const ys = modelPoints.map((point) => point[1]);
          this.box(Math.min(...xs), Math.min(...ys), elevation + 2.75, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), .2, color, element.element_ref, width, height);
        } else if (geometry.type === "line") {
          const start = this.toModel(geometry.start);
          const end = this.toModel(geometry.end);
          const minX = Math.min(start[0], end[0]);
          const minY = Math.min(start[1], end[1]);
          const dx = Math.max(.18, Math.abs(end[0] - start[0]));
          const dy = Math.max(.18, Math.abs(end[1] - start[1]));
          if (element.kind === "wall") this.box(minX, minY, elevation, dx, dy, 2.9, color, element.element_ref, width, height);
          else this.box(minX, minY, elevation + 2.62, dx, dy, .35, color, element.element_ref, width, height);
        } else if (geometry.type === "point") {
          const point = this.toModel(geometry.point);
          const size = element.kind === "foundation" ? .85 : .34;
          this.box(point[0] - size / 2, point[1] - size / 2, elevation, size, size, element.kind === "foundation" ? .3 : 2.9, color, element.element_ref, width, height);
        }
      });
      this.faces.sort((a, b) => a.depth - b.depth);
    }

    draw() {
      const { width, height } = this.size();
      const ctx = this.context;
      const background = ctx.createLinearGradient(0, 0, 0, height);
      background.addColorStop(0, "#f8fafb");
      background.addColorStop(1, "#e7eef1");
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = "rgba(69,101,113,.12)";
      ctx.lineWidth = 1;
      for (let x = -2; x <= 13; x += 1) {
        const a = this.project([x, -2, -2.85], width, height);
        const b = this.project([x, 8, -2.85], width, height);
        ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
      }
      for (let y = -2; y <= 8; y += 1) {
        const a = this.project([-2, y, -2.85], width, height);
        const b = this.project([13, y, -2.85], width, height);
        ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
      }
      this.build(width, height);
      this.faces.forEach((face) => {
        ctx.beginPath(); ctx.moveTo(...face.points[0]); face.points.slice(1).forEach((point) => ctx.lineTo(...point)); ctx.closePath();
        ctx.fillStyle = face.fill; ctx.fill(); ctx.strokeStyle = face.stroke; ctx.lineWidth = face.elementRef === this.selectedRef ? 1.8 : .75; ctx.stroke();
      });
      ctx.fillStyle = "#173b55";
      ctx.font = "700 11px Segoe UI, sans-serif";
      ctx.fillText("Strukturelles Auswahlmodell", 18, 28);
      ctx.fillStyle = "#607681";
      ctx.font = "10px Segoe UI, sans-serif";
      ctx.fillText("Bauteile anklicken · feste Prüfkameraperspektive · kein Rundgang", 18, 45);
      ctx.fillStyle = "#0b9fa2"; ctx.fillRect(18, 57, 12, 8);
      ctx.fillStyle = "#526d78"; ctx.font = "8px Segoe UI, sans-serif"; ctx.fillText("ausgewähltes Bauteil", 36, 65);
    }

    inside(point, polygon) {
      let inside = false;
      for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
        const xi = polygon[i][0], yi = polygon[i][1], xj = polygon[j][0], yj = polygon[j][1];
        if (((yi > point[1]) !== (yj > point[1])) && point[0] < (xj - xi) * (point[1] - yi) / ((yj - yi) || .00001) + xi) inside = !inside;
      }
      return inside;
    }

    pick(event) {
      const rect = this.canvas.getBoundingClientRect();
      const point = [event.clientX - rect.left, event.clientY - rect.top];
      const face = [...this.faces].reverse().find((candidate) => this.inside(point, candidate.points));
      if (face?.elementRef) this.onSelect(face.elementRef);
    }
  }

  window.VectoplanStatikEditorPlugin = { StructuralSelection3DView };
})();
