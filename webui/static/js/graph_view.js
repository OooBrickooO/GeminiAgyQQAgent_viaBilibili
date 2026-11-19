/**
 * webui/static/js/graph_view.js — 认知与二级分层引力拓扑图谱控制器
 * 严格按照草图二级分层架构（弥酱 -> 群 -> 群友），全网无冗长QQ号，并支持 0.5s 交互脱离冗余窗口
 */
const GraphView = {
  chart: null,
  leaveTimer: null,

  init() {
    const dom = document.getElementById("mind-graph-chart");
    if (dom && window.echarts) {
      this.chart = echarts.init(dom);
      window.addEventListener("resize", () => {
        if (document.getElementById("tab-graph")?.classList.contains("active")) {
          this.chart?.resize();
        }
      });
      this.bindBufferEvents();
    }
  },

  bindBufferEvents() {
    if (!this.chart) return;

    // 监听鼠标移入节点：清除退出定时器，保持高亮聚焦
    this.chart.on("mouseover", { dataType: "node" }, (params) => {
      if (this.leaveTimer) {
        clearTimeout(this.leaveTimer);
        this.leaveTimer = null;
      }
      this.chart.dispatchAction({
        type: "highlight",
        seriesIndex: 0,
        dataIndex: params.dataIndex
      });
    });

    // 监听鼠标移出节点：设置 0.5 秒 (500ms) 缓冲冗余窗口，杜绝划过密集目标时的频闪！
    this.chart.on("mouseout", { dataType: "node" }, (params) => {
      if (this.leaveTimer) clearTimeout(this.leaveTimer);
      this.leaveTimer = setTimeout(() => {
        this.chart.dispatchAction({
          type: "downplay",
          seriesIndex: 0
        });
        this.leaveTimer = null;
      }, 500); // 严格设为 0.5s 冗余窗口
    });
  },

  async refreshGraph() {
    if (!this.chart) {
      const dom = document.getElementById("mind-graph-chart");
      if (dom && window.echarts) {
        this.chart = echarts.init(dom);
        this.bindBufferEvents();
      }
    }
    if (!this.chart) return;

    this.chart.resize();

    this.chart.showLoading({
      text: "正在编织二级分层引力星系...",
      color: "#ec4899",
      textColor: "#cbd5e1",
      maskColor: "rgba(11, 15, 25, 0.7)"
    });

    const data = await ApiClient.getMindGraph();
    this.chart.hideLoading();

    if (!data || !data.success || !data.nodes) return;

    const option = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        hideDelay: 500, // 提示框同步 0.5s 冗余平滑延迟
        transitionDuration: 0.25,
        backgroundColor: "rgba(17, 24, 39, 0.95)",
        borderColor: "rgba(236, 72, 153, 0.5)",
        textStyle: { color: "#f3f4f6", fontSize: 12 },
        formatter: (params) => {
          if (params.dataType === "node") {
            const d = params.data;
            return `
              <div style="font-weight:bold;color:#f472b6;margin-bottom:4px;font-size:13px;">${d.name}</div>
              <div style="color:#94a3b8;font-size:11px;">${d.value || ''}</div>
            `;
          }
          return `<div style="font-size:11px;color:#cbd5e1;">${params.data.source} ↔ ${params.data.target}</div>`;
        }
      },
      legend: {
        orient: "horizontal",
        data: data.categories.map(c => c.name),
        textStyle: { color: "#9ca3af", fontSize: 11 },
        top: "8px",
        left: "center",
        itemGap: 16
      },
      series: [
        {
          type: "graph",
          layout: "force",
          data: data.nodes,
          links: data.links,
          categories: data.categories,
          roam: true,
          draggable: true,
          zoom: 0.95,
          cursor: "pointer",
          // 状态切换 350ms 平滑渐变补间动画，避免生硬跳变
          stateAnimation: {
            duration: 350,
            easing: "cubicOut"
          },
          lineStyle: {
            curveness: 0.08
          },
          // 聚焦时相连节点高亮
          emphasis: {
            focus: "adjacency",
            lineStyle: { width: 3.5 }
          },
          // 柔和暗化底色：保持 30% 可见度，像微弱星芒一样垫底，绝非死黑
          blur: {
            itemStyle: { opacity: 0.30 },
            lineStyle: { opacity: 0.15 }
          },
          force: {
            repulsion: 340,
            edgeLength: [80, 210],
            gravity: 0.04,
            friction: 0.70
          }
        }
      ]
    };

    this.chart.setOption(option);
    setTimeout(() => this.chart.resize(), 50);
  }
};
