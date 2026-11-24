// Metric charts on the server page: fetches /servers/<id>/metrics.json and draws Chart.js lines.
(() => {
  "use strict";

  const root = document.getElementById("charts");
  if (!root || !window.Chart) return;

  const REFRESH_MS = 30_000;
  const url = root.dataset.metricsUrl;
  let currentRange = root.dataset.range || "1h";

  const css = getComputedStyle(document.documentElement);
  const cssVar = (name) => css.getPropertyValue(name).trim();
  const alpha = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, "0");

  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--grid");
  Chart.defaults.font.family = cssVar("--font");
  Chart.defaults.font.size = 11.5;

  const formatBytesRate = (value) => {
    const units = ["Б/с", "КБ/с", "МБ/с", "ГБ/с"];
    let v = value;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  };

  const timeFormats = {
    "1h": new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }),
    "24h": new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }),
    "7d": new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
  };

  const dataset = (label, colorVar, field, { fill = false } = {}) => {
    const color = cssVar(colorVar);
    return {
      label,
      field,
      data: [],
      borderColor: color,
      backgroundColor: fill ? alpha(color, 0.12) : color,
      fill: fill ? "origin" : false,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.3,
      spanGaps: false, // gaps = agent was silent; do not hide them
    };
  };

  const options = ({ max, tick }) => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "top", align: "end", labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: "circle" } },
      tooltip: {
        backgroundColor: "#0b111d",
        borderColor: cssVar("--border-strong"),
        borderWidth: 1,
        padding: 10,
        callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y === null ? "—" : tick(ctx.parsed.y)}` },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 7, maxRotation: 0, autoSkip: true } },
      y: { beginAtZero: true, max, grid: { color: cssVar("--grid") }, ticks: { maxTicksLimit: 5, callback: tick } },
    },
  });

  const makeChart = (id, datasets, opts) =>
    new Chart(document.getElementById(id), { type: "line", data: { labels: [], datasets }, options: options(opts) });

  const charts = [
    makeChart(
      "chart-usage",
      [
        dataset("CPU", "--c-cpu", "cpu_percent", { fill: true }),
        dataset("RAM", "--c-mem", "memory_percent"),
        dataset("Диск", "--c-disk", "disk_percent"),
      ],
      { max: 100, tick: (v) => `${Math.round(v)}%` },
    ),
    makeChart(
      "chart-load",
      [
        dataset("1 мин", "--c-load1", "load_1", { fill: true }),
        dataset("5 мин", "--c-load5", "load_5"),
        dataset("15 мин", "--c-load15", "load_15"),
      ],
      { tick: (v) => v.toFixed(2) },
    ),
    makeChart(
      "chart-net",
      [
        dataset("Исходящий", "--c-sent", "net_sent_bps", { fill: true }),
        dataset("Входящий", "--c-recv", "net_recv_bps"),
      ],
      { tick: formatBytesRate },
    ),
  ];

  let inFlight = null;

  async function load(range, { quiet = false } = {}) {
    inFlight?.abort();
    const controller = new AbortController();
    inFlight = controller;
    if (!quiet) root.classList.add("is-loading");
    try {
      const response = await fetch(`${url}?range=${encodeURIComponent(range)}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const fmt = timeFormats[payload.range] || timeFormats["1h"];
      const labels = payload.timestamps.map((ms) => fmt.format(new Date(ms)));
      for (const chart of charts) {
        chart.data.labels = labels;
        for (const ds of chart.data.datasets) ds.data = payload.series[ds.field];
        chart.update(quiet ? "none" : undefined);
      }
    } catch (error) {
      if (error.name !== "AbortError") console.error("Failed to load metrics", error);
    } finally {
      if (inFlight === controller) root.classList.remove("is-loading");
    }
  }

  document.querySelectorAll(".segmented__btn[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segmented__btn").forEach((b) => b.classList.toggle("is-active", b === button));
      currentRange = button.dataset.range;
      load(currentRange);
    });
  });

  load(currentRange);
  setInterval(() => {
    if (!document.hidden) load(currentRange, { quiet: true });
  }, REFRESH_MS);
})();
