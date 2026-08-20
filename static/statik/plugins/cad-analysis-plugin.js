(() => {
  "use strict";

  class StructuralSurfaceResultView {
    constructor(canvas) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d");
      this.result = null;
      this.message = "Flächenergebnis wird vorbereitet";
      this.resizeObserver = new ResizeObserver(() => this.draw());
      this.resizeObserver.observe(canvas.parentElement || canvas);
    }

    setMessage(message) {
      this.result = null;
      this.message = message;
      this.draw();
    }

    render(result) {
      this.result = result;
      this.draw();
    }

    size() {
      const rect = this.canvas.getBoundingClientRect();
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.round(rect.width * ratio));
      const height = Math.max(1, Math.round(rect.height * ratio));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { width: rect.width, height: rect.height };
    }

    color(value) {
      const stops = [
        [20, 64, 205], [13, 164, 213], [34, 197, 128], [229, 213, 58], [244, 138, 37], [218, 54, 45],
      ];
      const scaled = Math.max(0, Math.min(.999, value)) * (stops.length - 1);
      const index = Math.floor(scaled);
      const amount = scaled - index;
      const first = stops[index];
      const second = stops[Math.min(stops.length - 1, index + 1)];
      const rgb = first.map((channel, position) => Math.round(channel + (second[position] - channel) * amount));
      return `rgb(${rgb.join(",")})`;
    }

    draw() {
      const { width, height } = this.size();
      const ctx = this.context;
      ctx.clearRect(0, 0, width, height);
      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, "#f8fafb");
      gradient.addColorStop(1, "#e9eff2");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, width, height);
      const rows = this.result?.grid?.rows;
      if (!rows?.length) {
        ctx.fillStyle = "#173b55";
        ctx.font = "600 15px Segoe UI, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(this.message, width / 2, height / 2);
        return;
      }
      const values = rows.flat().map((item) => Math.abs(Number(item.w_mm) || 0));
      const maximum = Math.max(...values, .0001);
      const nx = rows[0].length;
      const ny = rows.length;
      const modelWidth = Math.max(Number(rows[0][nx - 1].x_m) || 1, 1);
      const modelDepth = Math.max(Number(rows[ny - 1][0].y_m) || 1, 1);
      const scale = Math.min((width - 120) / (modelWidth + modelDepth * .72), (height - 145) / (modelDepth * .55 + 1.7));
      const originX = width / 2 - (modelWidth - modelDepth * .72) * scale / 2;
      const originY = Math.max(94, height * .26);
      const project = (point) => {
        const normalized = Math.abs(Number(point.w_mm) || 0) / maximum;
        return {
          x: originX + (Number(point.x_m) - Number(point.y_m) * .72) * scale,
          y: originY + Number(point.y_m) * .55 * scale + normalized * Math.min(120, height * .2),
          normalized,
        };
      };
      for (let row = ny - 2; row >= 0; row -= 1) {
        for (let column = 0; column < nx - 1; column += 1) {
          const points = [project(rows[row][column]), project(rows[row][column + 1]), project(rows[row + 1][column + 1]), project(rows[row + 1][column])];
          const ratio = points.reduce((sum, point) => sum + point.normalized, 0) / 4;
          ctx.beginPath();
          ctx.moveTo(points[0].x, points[0].y);
          points.slice(1).forEach((point) => ctx.lineTo(point.x, point.y));
          ctx.closePath();
          ctx.fillStyle = this.color(ratio);
          ctx.fill();
          ctx.strokeStyle = "rgba(20,55,75,.23)";
          ctx.lineWidth = .45;
          ctx.stroke();
        }
      }
      ctx.fillStyle = "#173b55";
      ctx.font = "700 11px Segoe UI, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${this.result.theory || "Flächentheorie"}`, 18, 28);
      ctx.font = "10px Segoe UI, sans-serif";
      ctx.fillStyle = "#5c717c";
      ctx.fillText(`w max = ${maximum.toFixed(3)} mm · ${nx} × ${ny} Ergebnispunkte`, 18, 45);
      const legendX = width - 35;
      const legendY = Math.max(80, height / 2 - 100);
      for (let index = 0; index < 100; index += 1) {
        ctx.fillStyle = this.color(1 - index / 99);
        ctx.fillRect(legendX, legendY + index * 2, 10, 2.2);
      }
      ctx.fillStyle = "#405966";
      ctx.font = "8px Segoe UI, sans-serif";
      ctx.fillText(maximum.toFixed(2), legendX - 2, legendY - 5);
      ctx.fillText("0", legendX + 2, legendY + 214);
    }
  }

  window.VectoplanStatikSurfacePlugin = { StructuralSurfaceResultView };
})();
