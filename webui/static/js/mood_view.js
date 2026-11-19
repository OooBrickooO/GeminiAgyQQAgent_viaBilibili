/**
 * webui/static/js/mood_view.js — 情绪雷达与心境心电图可视化控制器
 */
const MoodView = {
  radarChart: null,
  ekgChart: null,

  dimColors: {
    happy: "linear-gradient(90deg, #f59e0b, #ec4899)",
    calm: "linear-gradient(90deg, #06b6d4, #3b82f6)",
    sweet: "linear-gradient(90deg, #ec4899, #f43f5e)",
    proud: "linear-gradient(90deg, #8b5cf6, #c084fc)",
    worry: "linear-gradient(90deg, #f97316, #ef4444)",
    sulky: "linear-gradient(90deg, #6366f1, #3b82f6)",
    jealous: "linear-gradient(90deg, #e11d48, #9333ea)"
  },

  initCharts() {
    const radarDom = document.getElementById("mood-radar-chart");
    const ekgDom = document.getElementById("mood-ekg-chart");

    if (radarDom && window.echarts) {
      this.radarChart = echarts.init(radarDom);
    }
    if (ekgDom && window.echarts) {
      this.ekgChart = echarts.init(ekgDom);
    }

    window.addEventListener("resize", () => {
      this.radarChart?.resize();
      this.ekgChart?.resize();
    });
  },

  updateView(moodData) {
    if (!moodData || !moodData.success) return;

    const mood = moodData.mood;
    const dims = moodData.dims || [];
    const radar = moodData.radar || {};
    const ekg = moodData.timeline_ekg || [];

    // 1. 独白文本与产生场景
    const monologueEl = document.getElementById("mood-last-reason");
    const sceneEl = document.getElementById("mood-scene-badge");
    if (monologueEl) {
      monologueEl.textContent = mood.last_reason || "心情平静空白（对熟人热情，对陌生人正常自然）";
    }
    if (sceneEl) {
      sceneEl.textContent = `场景: ${mood.last_source_scene || "日常陪伴"}`;
    }

    // 2. 衰减比率与半衰进度条
    const decayTextEl = document.getElementById("decay-factor-text");
    const decayFillEl = document.getElementById("decay-meter-fill");
    if (decayTextEl && decayFillEl) {
      const pct = Math.round((mood.decay_factor_mood || 1.0) * 100);
      const hlHours = mood.half_life_mood_hours || 5;
      decayTextEl.textContent = `心境回归平滑比率: ${pct}% (半衰期${hlHours}h)`;
      decayFillEl.style.width = `${pct}%`;
    }

    // 3. 7 维微观维度进度条列表渲染
    const dimsContainer = document.getElementById("dims-bars-container");
    if (dimsContainer) {
      dimsContainer.innerHTML = dims.map(d => {
        const bgGrad = this.dimColors[d.key] || "linear-gradient(90deg, #06b6d4, #ec4899)";
        return `
          <div class="dim-bar-row">
            <div class="dim-bar-meta">
              <span class="dim-name">${d.name} (${d.key})</span>
              <span class="dim-val">${d.value.toFixed(1)} <small style="color:#6b7280">/ 100</small></span>
            </div>
            <div class="dim-track">
              <div class="dim-fill" style="width: ${d.percentage}%; background: ${bgGrad};"></div>
            </div>
          </div>
        `;
      }).join("");
    }

    // 4. 渲染 ECharts 7D 雷达图
    if (this.radarChart && radar.indicators) {
      const option = {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "item",
          backgroundColor: "rgba(17, 24, 39, 0.9)",
          borderColor: "rgba(236, 72, 153, 0.4)",
          textStyle: { color: "#f3f4f6" }
        },
        radar: {
          indicator: radar.indicators,
          shape: "circle",
          splitNumber: 4,
          axisName: {
            color: "#cbd5e1",
            fontSize: 11
          },
          splitLine: {
            lineStyle: {
              color: "rgba(255, 255, 255, 0.08)"
            }
          },
          splitArea: {
            show: true,
            areaStyle: {
              color: [
                "rgba(255, 255, 255, 0.01)",
                "rgba(255, 255, 255, 0.03)",
                "rgba(255, 255, 255, 0.01)",
                "rgba(255, 255, 255, 0.04)"
              ]
            }
          },
          axisLine: {
            lineStyle: {
              color: "rgba(255, 255, 255, 0.1)"
            }
          }
        },
        series: [{
          name: "微观情绪画像",
          type: "radar",
          data: [
            {
              value: radar.decayed_values,
              name: "实时衰减后",
              symbol: "circle",
              symbolSize: 4,
              itemStyle: { color: "#ec4899" },
              lineStyle: { width: 2, color: "#ec4899" },
              areaStyle: { color: "rgba(236, 72, 153, 0.3)" }
            },
            {
              value: radar.raw_values,
              name: "事件瞬时脉冲",
              symbol: "none",
              lineStyle: { width: 1, type: "dashed", color: "rgba(6, 182, 212, 0.6)" }
            }
          ]
        }]
      };
      this.radarChart.setOption(option);
    }

    // 5. 渲染 24 小时心境 EKG 心电图
    if (this.ekgChart && ekg.length > 0) {
      const times = ekg.map(p => p.time);
      const moods = ekg.map(p => p.mood);
      const baseVal = Number(mood.baseline ?? 60.0);

      const option = {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(17, 24, 39, 0.9)",
          borderColor: "rgba(59, 130, 246, 0.4)",
          textStyle: { color: "#f3f4f6" },
          formatter: (params) => {
            const p = params[0];
            return `<div style="font-size:12px"><b>${p.axisValue}</b><br/>心境评分: <span style="color:#f472b6;font-weight:bold">${p.data}</span> (基准线 ${baseVal.toFixed(1)})</div>`;
          }
        },
        grid: {
          top: "15%",
          left: "3%",
          right: "4%",
          bottom: "8%",
          containLabel: true
        },
        xAxis: {
          type: "category",
          data: times,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "rgba(255, 255, 255, 0.15)" } },
          axisLabel: { color: "#9ca3af", fontSize: 11 }
        },
        yAxis: {
          type: "value",
          min: 30,
          max: 100,
          splitLine: { lineStyle: { color: "rgba(255, 255, 255, 0.05)" } },
          axisLabel: { color: "#9ca3af", fontSize: 11 }
        },
        series: [
          {
            name: "宏观心境",
            type: "line",
            smooth: 0.35,
            data: moods,
            symbol: "circle",
            symbolSize: 6,
            showSymbol: false,
            itemStyle: { color: "#38bdf8" },
            lineStyle: {
              width: 3,
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: "#38bdf8" },
                { offset: 0.5, color: "#818cf8" },
                { offset: 1, color: "#ec4899" }
              ])
            },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: "rgba(236, 72, 153, 0.25)" },
                { offset: 1, color: "rgba(56, 189, 248, 0.0)" }
              ])
            },
            markLine: {
              silent: true,
              symbol: "none",
              data: [
                {
                  yAxis: baseVal,
                  lineStyle: { color: "rgba(255, 255, 255, 0.25)", type: "dashed" },
                  label: { formatter: `基准线 ${baseVal.toFixed(1)}`, color: "#9ca3af", position: "insideEndTop" }
                }
              ]
            }
          }
        ]
      };
      this.ekgChart.setOption(option);
    }
  }
};

