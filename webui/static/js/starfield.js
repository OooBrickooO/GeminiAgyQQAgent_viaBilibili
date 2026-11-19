/**
 * webui/static/js/starfield.js — 赛博空间星海微粒交互背景引擎
 */
const Starfield = {
  canvas: null,
  ctx: null,
  stars: [],
  starCount: 80,
  maxDistance: 110,
  mouse: { x: -1000, y: -1000, radius: 140 },

  init() {
    this.canvas = document.getElementById("starfield-canvas");
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext("2d");

    this.resize();
    window.addEventListener("resize", () => this.resize());

    window.addEventListener("mousemove", (e) => {
      this.mouse.x = e.clientX;
      this.mouse.y = e.clientY;
    });

    window.addEventListener("mouseleave", () => {
      this.mouse.x = -1000;
      this.mouse.y = -1000;
    });

    this.createStars();
    this.animate();
  },

  resize() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  },

  createStars() {
    this.stars = [];
    const colors = ["#ec4899", "#38bdf8", "#c084fc", "#ffffff"];
    for (let i = 0; i < this.starCount; i++) {
      this.stars.push({
        x: Math.random() * this.canvas.width,
        y: Math.random() * this.canvas.height,
        radius: Math.random() * 1.6 + 0.4,
        color: colors[Math.floor(Math.random() * colors.length)],
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        alpha: Math.random() * 0.7 + 0.3
      });
    }
  },

  animate() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    for (let i = 0; i < this.stars.length; i++) {
      const s = this.stars[i];
      s.x += s.vx;
      s.y += s.vy;

      if (s.x < 0) s.x = this.canvas.width;
      if (s.x > this.canvas.width) s.x = 0;
      if (s.y < 0) s.y = this.canvas.height;
      if (s.y > this.canvas.height) s.y = 0;

      // 绘制粒子
      this.ctx.beginPath();
      this.ctx.arc(s.x, s.y, s.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = s.color;
      this.ctx.globalAlpha = s.alpha * 0.6;
      this.ctx.fill();

      // 邻近星链连线
      for (let j = i + 1; j < this.stars.length; j++) {
        const s2 = this.stars[j];
        const dx = s.x - s2.x;
        const dy = s.y - s2.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < this.maxDistance) {
          this.ctx.beginPath();
          this.ctx.moveTo(s.x, s.y);
          this.ctx.lineTo(s2.x, s2.y);
          this.ctx.strokeStyle = "rgba(192, 132, 252, 0.15)";
          this.ctx.lineWidth = 0.6 * (1 - dist / this.maxDistance);
          this.ctx.globalAlpha = 0.3 * (1 - dist / this.maxDistance);
          this.ctx.stroke();
        }
      }
    }

    this.ctx.globalAlpha = 1.0;
    requestAnimationFrame(() => this.animate());
  }
};

