/* global Chart */
// ==============================
// MONERO MINING DASHBOARD
// ==============================

// ==============================
// CONFIGURATION & CONSTANTS
// ==============================
const CONFIG = {
  // Time constants (in seconds)
  SECONDS_PER_MINUTE: 60,
  SECONDS_PER_HOUR: 3600,
  SECONDS_PER_DAY: 86400,

  // Mining constants
  BLOCKS_PER_DAY: 720,
  BLOCK_TIME_SECONDS: 120,
  PPLNS_WINDOW_MAX_SHARES: 5,

  // Conversion multipliers for earnings periods
  PERIOD_MULTIPLIERS: {
    hour: 1 / 24,
    day: 1,
    week: 7,
    month: 30,
    year: 365
  },

  // Default values
  DEFAULT_RANGE_HOURS: 24,
  DEFAULT_MIN_PAYMENT: 0.01,
  RANGE_STORAGE_KEY: "p2pool.chartRangeHours",
  REFRESH_INTERVAL_MS: 10000, // match server.py's 10s log cadence — faster polls fetch identical data
  OBSERVER_SHARES_LIMIT: 10000,
  // The tide table reads the 5-minute rollup, so polling it faster gains nothing
  WEEK_REFRESH_MS: 60000,
  SEASON_REFRESH_MS: 900000,
  WEEK_DAYS: 7,
  SEASON_DAYS: 90,

  // Hashrate scaling thresholds
  HASHRATE_SCALE: {
    GIGA: 1e9,
    MEGA: 1e6,
    KILO: 1e3
  },

  // Monero units
  ATOMIC_UNITS_PER_XMR: 1e12
};

// ==============================
// STATE MANAGEMENT
// ==============================
const state = {
  history: null,
  // Chart series come from /stats_history.json, which is downsampled server-side
  // and reaches back further than the 24h buffer `history` holds for the maths.
  chartWindow: null,
  hashrateChart: null,
  priceChart: null,
  currentRangeHours: CONFIG.DEFAULT_RANGE_HOURS,
  observerBase: null,
  observerWallet: null,
  oldStatsData: {},
  isTabVisible: true,
  // Live miner reading for the state band; null until the first poll lands
  live: null,
  weekDays: null,
  weekPeak: 1,
  blockReward: 0
};

// ==============================
// DOM ELEMENT CACHE
// ==============================
const DOM = {};

function cacheDOMElements() {
  const ids = [
    "hashrateChart",
    "priceChart",
    "myHashrate",
    "poolHashrate",
    "netHashrate",
    "price",
    "earnXMR",
    "earnEUR",
    "earnPeriod",
    "paymentsTable",
    "pool-status",
    "pool-status-text",
    "user-hashrate-24h",
    "reward-share",
    "workers-list",
    "rangeControl",
    "state",
    "stateLine",
    "stateFigure",
    "tideCaption",
    "stateDetail",
    "todayTide",
    "weekRows",
    "seasonGrid",
    "hashrateEmpty",
    "priceEmpty"
  ];

  ids.forEach((id) => {
    DOM[id] = document.getElementById(id);
  });
}

// ==============================
// UTILITY FUNCTIONS
// ==============================

function isValidObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isPositiveNumber(value) {
  return typeof value === "number" && !Number.isNaN(value) && value > 0;
}

function safeParseInt(value, fallback = 0) {
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function scaleHashrate(hashrate) {
  if (!isPositiveNumber(hashrate)) return "0 H/s";

  if (hashrate >= CONFIG.HASHRATE_SCALE.GIGA) {
    return `${(hashrate / CONFIG.HASHRATE_SCALE.GIGA).toFixed(2)} GH/s`;
  }
  if (hashrate >= CONFIG.HASHRATE_SCALE.MEGA) {
    return `${(hashrate / CONFIG.HASHRATE_SCALE.MEGA).toFixed(2)} MH/s`;
  }
  if (hashrate >= CONFIG.HASHRATE_SCALE.KILO) {
    return `${(hashrate / CONFIG.HASHRATE_SCALE.KILO).toFixed(2)} kH/s`;
  }
  return `${Math.round(hashrate)} H/s`;
}

/** Approximate EUR; sub-cent amounts are the norm here and "≈ €0.00" would read as nothing earned. */
function eurText(eur) {
  return eur > 0 && eur < 0.01 ? "< €0.01" : `≈ €${eur.toFixed(2)}`;
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatDate24Hours(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "Invalid time";
  }

  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/**
 * Formats relative time (e.g., "5m ago")
 * @param {number} timestamp - Unix timestamp in seconds
 * @param {Object} opts - options for edge-case labels
 * @param {string} [opts.zeroLabel="Never"] - label for null/zero timestamps
 * @param {string} [opts.invalidLabel="Unknown"] - label for invalid timestamps
 * @param {(diff: number) => string} [opts.underMinute] - formatter for <60s; default: `${diff}s ago`
 * @returns {string}
 */
function formatRelativeTime(timestamp, opts = {}) {
  const { zeroLabel = "Never", invalidLabel = "Unknown", underMinute } = opts;

  if (!timestamp || timestamp === 0) return zeroLabel;
  if (!isPositiveNumber(timestamp)) return invalidLabel;

  const diff = Math.floor(Date.now() / 1000 - timestamp);

  if (diff < CONFIG.SECONDS_PER_MINUTE)
    return underMinute ? underMinute(diff) : `${diff}s ago`;
  if (diff < CONFIG.SECONDS_PER_HOUR)
    return `${Math.floor(diff / CONFIG.SECONDS_PER_MINUTE)}m ago`;
  if (diff < CONFIG.SECONDS_PER_DAY)
    return `${Math.floor(diff / CONFIG.SECONDS_PER_HOUR)}h ago`;
  return `${Math.floor(diff / CONFIG.SECONDS_PER_DAY)}d ago`;
}

/**
 * Formats time difference from now (legacy alias with "Just now" / "Invalid" labels)
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {string}
 */
function formatTime(timestamp) {
  return formatRelativeTime(timestamp, {
    invalidLabel: "Invalid",
    underMinute: () => "Just now"
  });
}

/**
 * Fetch JSON data from a URL with error handling
 * @param {string} url - endpoint to fetch
 * @returns {Promise<Object|null>} JSON response or null on error
 */
async function fetchJSON(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
    return await response.json();
  } catch (error) {
    console.warn(`Fetch failed for ${url}:`, error.message);
    return null;
  }
}

// ==============================
// DATA PROCESSING FUNCTIONS
// ==============================

/**
 * Slice historical data to only include the last X hours
 * @param {number} hours - number of hours to include
 * @param {Object} historyData - history object
 * @returns {Object|null} sliced history or null if invalid
 */
function sliceHistory(hours, historyData) {
  if (!isValidObject(historyData) || !Array.isArray(historyData.timestamps)) {
    return null;
  }

  if (!isPositiveNumber(hours)) {
    return null;
  }

  const now = Date.now() / 1000;
  const cutoff = now - hours * CONFIG.SECONDS_PER_HOUR;
  const idx = historyData.timestamps.findIndex((t) => t >= cutoff);
  const startIndex = idx === -1 ? 0 : idx;

  return {
    timestamps: historyData.timestamps.slice(startIndex),
    myHash: historyData.myHash?.slice(startIndex) || [],
    poolHash: historyData.poolHash?.slice(startIndex) || [],
    netHash: historyData.netHash?.slice(startIndex) || []
  };
}

/**
 * Compute a moving average over a given time window
 * @param {number[]} timestamps - array of timestamps in seconds
 * @param {number[]} values - array of corresponding values
 * @param {number} windowSeconds - window size in seconds
 * @returns {number} latest smoothed value
 */
function movingAverage(timestamps, values, windowSeconds) {
  if (
    !Array.isArray(timestamps) ||
    !Array.isArray(values) ||
    timestamps.length === 0 ||
    values.length === 0
  ) {
    return 0;
  }

  const windowStart = timestamps[timestamps.length - 1] - windowSeconds;
  let sum = 0;
  let count = 0;

  for (let i = 0; i < values.length; i++) {
    if (timestamps[i] >= windowStart) {
      sum += values[i];
      count++;
    }
  }

  return count > 0 ? sum / count : values[values.length - 1] || 0;
}

function extractXMRigHashrate(xmrigData) {
  return xmrigData.hashrate?.total?.[0] || 0;
}

function extractPoolHashrate(poolData) {
  if (!isValidObject(poolData)) return 0;
  return poolData.pool_statistics?.hashRate || 0;
}

function calculateNetworkHashrate(networkData) {
  if (
    !isValidObject(networkData) ||
    !isPositiveNumber(networkData.difficulty)
  ) {
    return 0;
  }
  return networkData.difficulty / CONFIG.BLOCK_TIME_SECONDS;
}

function calculateMovingAverages(
  windowHours,
  historyData,
  instMyHash,
  instPoolHash,
  instNetHash
) {
  const fallback = {
    avgMyHash: instMyHash,
    avgPoolHash: instPoolHash,
    avgNetHash: instNetHash
  };

  if (windowHours <= 0 || !historyData) {
    return fallback;
  }

  const sliced = sliceHistory(windowHours, historyData);
  if (!sliced) {
    return fallback;
  }

  const windowSeconds = windowHours * CONFIG.SECONDS_PER_HOUR;
  const { timestamps } = sliced;

  return {
    avgMyHash:
      movingAverage(timestamps, sliced.myHash, windowSeconds) || instMyHash,
    avgPoolHash:
      movingAverage(timestamps, sliced.poolHash, windowSeconds) || instPoolHash,
    avgNetHash:
      movingAverage(timestamps, sliced.netHash, windowSeconds) || instNetHash
  };
}

// ==============================
// OBSERVER CONFIGURATION
// ==============================

async function loadObserverConfig() {
  const cfg = await fetchJSON("/observer_config");
  if (!isValidObject(cfg) || !cfg.wallet || !cfg.observer) {
    return null;
  }

  state.observerBase = cfg.observer;
  state.observerWallet = cfg.wallet;
  return cfg;
}

async function getWindowStartTimestamp(allShares) {
  if (!state.observerBase) return Date.now() / 1000;

  try {
    if (!Array.isArray(allShares) || allShares.length === 0) {
      console.warn("No shares available for window calculation");
      return Date.now() / 1000;
    }

    const windowDepth = allShares[0].window_depth;
    if (!isPositiveNumber(windowDepth)) {
      return Date.now() / 1000;
    }

    // Only hit the API again if the shared fetch was shorter than the window.
    const sharesInWindow =
      allShares.length >= windowDepth
        ? allShares
        : await fetchJSON(`/observer/shares?limit=${windowDepth}`);
    if (!Array.isArray(sharesInWindow) || sharesInWindow.length === 0) {
      console.warn("No shares returned for PPLNS window");
      return Date.now() / 1000;
    }

    const windowStartShare =
      sharesInWindow[Math.min(windowDepth, sharesInWindow.length) - 1];
    return Math.floor(windowStartShare?.timestamp || Date.now() / 1000);
  } catch {
    console.warn("Failed to get window start timestamp");
    return Date.now() / 1000;
  }
}

function isObserverReady() {
  return !!state.observerWallet && !!state.observerBase;
}

// ==============================
// UI UPDATE HELPERS
// ==============================

function setTextContent(elementId, text) {
  // Ids outside the cacheDOMElements list resolve through getElementById.
  const el = DOM[elementId] || document.getElementById(elementId);
  // Unchanged text is skipped so the aria-live state band is not re-announced every poll
  const next = String(text);
  if (el && el.textContent !== next) el.textContent = next;
}

function setTooltipContent(tooltipId, content) {
  const tooltip = document.getElementById(tooltipId);
  if (tooltip && content) {
    tooltip.title = content;
  }
}

// ==============================
// CHART FUNCTIONS
// ==============================

// Chart.js takes plain colour strings, so the palette is read straight off
// :root rather than transcribed — one source of truth with dashboard.css.
// Canvas accepts oklch()/color-mix() as-is in every browser this targets.
const ROOT_STYLE = getComputedStyle(document.documentElement);
const CHART_COLORS = Object.fromEntries(
  Object.entries({
    accent: "--accent",
    accentFill: "--chart-fill",
    alt: "--chart-alt",
    altFill: "--chart-alt-fill",
    surface: "--surface-raised",
    line: "--line",
    grid: "--chart-grid",
    ink: "--ink-3",
    inkStrong: "--ink"
  }).map(([name, token]) => [name, ROOT_STYLE.getPropertyValue(token).trim()])
);

/**
 * Shared Chart.js options used by both hashrate and price charts.
 * Override yTicks or tooltipCallbacks as needed.
 */
function sharedChartOptions(overrides = {}) {
  const { yTicks, tooltipCallbacks } = overrides;

  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 160 },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: CHART_COLORS.surface,
        titleColor: CHART_COLORS.inkStrong,
        bodyColor: CHART_COLORS.ink,
        borderColor: CHART_COLORS.line,
        borderWidth: 1,
        padding: 10,
        displayColors: false,
        ...(tooltipCallbacks ? { callbacks: tooltipCallbacks } : {})
      }
    },
    scales: {
      x: {
        type: "time",
        time: {
          tooltipFormat: "dd/MM/yyyy HH:mm",
          // The range control spans 1h to 90d, so every unit Chart.js may pick
          // needs a format — an unset one falls back to an unreadable default.
          displayFormats: {
            minute: "HH:mm",
            hour: "dd/MM HH:mm",
            day: "dd/MM",
            week: "dd/MM",
            month: "MMM yyyy"
          }
        },
        grid: { display: false },
        border: { color: CHART_COLORS.line },
        ticks: {
          color: CHART_COLORS.ink,
          font: { size: 10 },
          maxTicksLimit: 6,
          maxRotation: 0,
          autoSkipPadding: 16
        }
      },
      y: {
        ...(yTicks ? { ticks: yTicks } : {}),
        border: { display: false },
        grid: {
          color: CHART_COLORS.grid,
          drawTicks: false
        }
      }
    },
    interaction: {
      intersect: false,
      mode: "index"
    }
  };
}

/**
 * Shared dataset config for line charts.
 */
function lineDataset(label, data, line = CHART_COLORS.accent, fill = CHART_COLORS.accentFill) {
  return {
    label,
    data,
    borderColor: line,
    backgroundColor: fill,
    borderWidth: 1.5,
    fill: true,
    // Straight segments: over 90d the smoothing invented dips between buckets
    tension: 0,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: line,
    pointHoverBorderColor: CHART_COLORS.surface,
    pointHoverBorderWidth: 2
  };
}

/**
 * Creates or updates a Chart.js instance.
 * @param {HTMLCanvasElement} canvas
 * @param {Object} chartState - state object
 * @param {string} stateKey - key in state (e.g. 'hashrateChart')
 * @param {Object} chartConfig - { label, data, yTicks, tooltipCallbacks }
 */
function initOrUpdateChart(canvas, chartState, stateKey, chartConfig) {
  if (!canvas) return;

  if (!chartState[stateKey]) {
    // options are read only at construction; building them per tick was pure waste
    chartState[stateKey] = new Chart(canvas, {
      type: "line",
      data: {
        labels: chartConfig.labels,
        datasets: [
          lineDataset(chartConfig.label, chartConfig.data, chartConfig.line, chartConfig.fill)
        ]
      },
      options: sharedChartOptions({
        yTicks: chartConfig.yTicks,
        tooltipCallbacks: chartConfig.tooltipCallbacks
      })
    });
  } else {
    chartState[stateKey].data.labels = chartConfig.labels;
    chartState[stateKey].data.datasets[0].data = chartConfig.data;
    chartState[stateKey].update();
  }
}

/**
 * Fetch the chart window for the selected range. The server downsamples to a
 * fixed point budget and switches to the 5-minute rollup past 24h, so a 90d
 * request costs the same on the wire as a 1h one.
 */
function fetchChartWindow() {
  const hours = state.currentRangeHours;
  return fetchJSON(`/stats_history.json?hours=${hours}`).then((data) =>
    data ? { ...data, hours } : data
  );
}

/** Draw an already-fetched window; a null response leaves the last one up. */
function applyChartWindow(payload) {
  if (!isValidObject(payload) || !Array.isArray(payload.timestamps)) return;
  // A slower response for a range the user has since left must not redraw.
  if (payload.hours !== state.currentRangeHours) return;

  state.chartWindow = {
    labels: payload.timestamps.map((t) => t * 1000),
    myHash: payload.myHash || [],
    price: payload.price || []
  };
  initializeCharts();
}

/** Fetch and draw. Only for the range control — the poll batches its own fetch. */
async function refreshChartWindow() {
  applyChartWindow(await fetchChartWindow());
}

function initializeCharts() {
  if (!state.chartWindow) return;
  if (typeof Chart === "undefined") {
    console.warn("Chart.js not loaded");
    return;
  }

  initializeHashrateChart(state.chartWindow);
  initializePriceChart(state.chartWindow);
}

/** Show the empty note only when the range genuinely holds no samples. */
function toggleChartEmpty(noteId, canvas, isEmpty) {
  const note = DOM[noteId];
  if (note) note.hidden = !isEmpty;
  if (canvas) canvas.style.visibility = isEmpty ? "hidden" : "";
}

function initializeHashrateChart(chartWindow) {
  try {
    toggleChartEmpty(
      "hashrateEmpty",
      DOM.hashrateChart,
      chartWindow.myHash.length === 0
    );
    initOrUpdateChart(DOM.hashrateChart, state, "hashrateChart", {
      label: "Your Hashrate",
      data: chartWindow.myHash,
      labels: chartWindow.labels,
      yTicks: {
        callback: scaleHashrate,
        color: CHART_COLORS.ink,
        font: { size: 10 }
      },
      tooltipCallbacks: {
        label: (ctx) =>
          ctx.parsed.y == null ? "No data" : scaleHashrate(ctx.parsed.y)
      }
    });
  } catch (error) {
    console.error("Failed to initialize hashrate chart:", error);
  }
}

function initializePriceChart(chartWindow) {
  try {
    toggleChartEmpty(
      "priceEmpty",
      DOM.priceChart,
      chartWindow.price.length === 0
    );
    initOrUpdateChart(DOM.priceChart, state, "priceChart", {
      label: "XMR Price (EUR)",
      line: CHART_COLORS.alt,
      fill: CHART_COLORS.altFill,
      data: chartWindow.price,
      labels: chartWindow.labels,
      yTicks: {
        callback: (v) => `€${v.toFixed(2)}`,
        color: CHART_COLORS.ink,
        font: { size: 10 }
      },
      tooltipCallbacks: {
        // The server nulls out logger gaps; hovering one has no price to show
        label: (ctx) =>
          ctx.parsed.y == null ? "No data" : `€${ctx.parsed.y.toFixed(2)}`
      }
    });
  } catch (error) {
    console.error("Failed to initialize price chart:", error);
  }
}

// ==============================
// PAYMENTS & SHARES FUNCTIONS
// ==============================

function updateRecentPayments(payouts, priceEUR) {
  if (!isObserverReady()) {
    setTextContent("paymentsStatus", "Observer not configured");
    setTextContent("totalEarned", "–");
    return [null, 0];
  }

  const tbody = DOM.paymentsTable?.querySelector("tbody");

  try {
    if (!Array.isArray(payouts) || payouts.length === 0) {
      setTextContent("paymentsStatus", "No payouts yet");
      setTextContent("totalEarned", "0.000000 XMR");
      if (tbody) tbody.innerHTML = "";
      return [null, 0];
    }

    const sortedPayouts = [...payouts].sort(
      (a, b) => b.timestamp - a.timestamp
    );
    const newestPayoutTime = sortedPayouts[0].timestamp;

    const totalXMR = sortedPayouts.reduce(
      (sum, p) => sum + p.coinbase_reward / CONFIG.ATOMIC_UNITS_PER_XMR,
      0
    );

    setTextContent("totalEarned", `${totalXMR.toFixed(6)} XMR`);
    setTextContent(
      "totalEurEarned",
      typeof priceEUR === "number"
        ? eurText(totalXMR * priceEUR)
        : ""
    );

    if (tbody) {
      tbody.innerHTML = sortedPayouts
        .slice(0, CONFIG.PPLNS_WINDOW_MAX_SHARES)
        .map((p) => {
          const xmr = p.coinbase_reward / CONFIG.ATOMIC_UNITS_PER_XMR;
          const eurValue =
            typeof priceEUR === "number" ? (xmr * priceEUR).toFixed(2) : "–";
          return `
          <tr>
            <td>${formatRelativeTime(p.timestamp)}</td>
            <td class="num">${xmr.toFixed(6)}</td>
            <td class="num">${eurValue}</td>
          </tr>
        `;
        })
        .join("");
    }

    setTextContent("paymentsStatus", "");

    return [newestPayoutTime, totalXMR];
  } catch (error) {
    console.error("Error updating recent payments:", error);
    setTextContent("paymentsStatus", "Payout data unavailable");
    setTextContent("totalEarned", "–");
    if (tbody) tbody.innerHTML = "";
    return [null, 0];
  }
}

function updateSharesCard(shares, payouts) {
  if (!isObserverReady()) {
    updateSharesDisplay("–", "–", "–", "–");
    return;
  }

  try {
    if (!Array.isArray(payouts) || !Array.isArray(shares)) {
      updateSharesDisplay("–", "–", "–", "–");
      return;
    }

    const lastPayoutTS = payouts.length > 0 ? payouts[0].timestamp : 0;

    let sharesSince = 0;
    let unclesSince = 0;
    let totalUncles = 0;

    for (const share of shares) {
      const isUncle = share.inclusion === 0;
      if (isUncle) totalUncles++;
      if (share.timestamp > lastPayoutTS) {
        sharesSince++;
        if (isUncle) unclesSince++;
      }
    }

    const totalShares = shares.length;

    updateSharesDisplay(sharesSince, unclesSince, totalShares, totalUncles);
  } catch (error) {
    console.error("Error updating shares card:", error);
    updateSharesDisplay("–", "–", "–", "–");
  }
}

function updateSharesDisplay(
  sharesSince,
  unclesSince,
  totalShares,
  totalUncles
) {
  setTextContent(
    "sharesSinceLastPayout",
    sharesSince === 0 ? "None yet" : sharesSince
  );
  setTextContent("unclesSinceLastPayout", unclesSince);
  // The <dt> carries the label; the value is the bare count
  setTextContent("totalSharesMined", totalShares);
  setTextContent("totalUnclesMined", totalUncles);
}

// ==============================
// LUCK CALCULATION FUNCTIONS
// ==============================

function updateWindowLuck({
  shares,
  pplnsWeight,
  avgMyHashPPLNS,
  windowStart,
  windowDuration,
  priceEUR,
  blockReward,
  poolInfo
}) {
  if (!isObserverReady()) return;

  try {
    if (!Array.isArray(shares)) {
      console.error("Invalid shares data in updateWindowLuck");
      return;
    }

    const myWindowShares = shares.filter(
      (share) => share.timestamp >= windowStart
    );
    const hasShares = myWindowShares.length > 0;
    const totalDifficulty = myWindowShares.reduce(
      (sum, share) => sum + (share.difficulty || 0),
      0
    );
    const difficultyShare = pplnsWeight > 0 ? totalDifficulty / pplnsWeight : 0;

    const myWindowHash =
      windowDuration > 0 ? totalDifficulty / windowDuration : 0;
    const luckFactor = avgMyHashPPLNS > 0 ? myWindowHash / avgMyHashPPLNS : 0;

    const avgCurrentEffort = poolInfo?.sidechain?.effort?.average200 || 100;
    const betterLuckFactor =
      avgCurrentEffort > 0
        ? luckFactor * (1 / (avgCurrentEffort / 100))
        : luckFactor;

    const accumulatedXMR = difficultyShare * blockReward;
    const accumulatedEUR = accumulatedXMR * priceEUR;

    setTooltipContent(
      "luckTooltip",
      `
Luck factor is based on your performance in the
current PPLNS window compared to your expected performance.
Basically it is your hashrate calculated based on the summed
difficulty of your hashes compared to the total difficulty in the
current PPLNS window divided by your actual moving average
(as long as the PPLNS window age) hashrate from XMRig. And that
also multiplied by the pool luck (derived from the pool effort).
    `.trim()
    );

    // Idle for the whole window: there is no expected rate to divide by
    setTextContent(
      "luckFactor",
      avgMyHashPPLNS > 0 && hasShares ? `${betterLuckFactor.toFixed(2)}×` : "–"
    );
    // 12 decimals is one atomic unit — six is the payout threshold's own precision
    setTextContent(
      "xmrThisWindow",
      hasShares ? `${accumulatedXMR.toFixed(6)} XMR` : "No shares yet"
    );
    setTextContent("eurThisWindow", hasShares ? eurText(accumulatedEUR) : "");
    setTextContent("dayHash", scaleHashrate(myWindowHash));
    setTextContent(
      "pplnsStart",
      formatDate24Hours(new Date(windowStart * 1000))
    );
    setTextContent(
      "currentEffort",
      (poolInfo?.sidechain?.effort?.current || 0).toFixed(2)
    );
  } catch (error) {
    console.error("Error updating PPLNS Window Luck card:", error);
  }
}

function updateTrueLuck(payouts, newestPayoutTime, xmrPerDayAvg, totalXMR) {
  const reset = () => {
    setTextContent("trueLuckFactor", "–");
  };

  try {
    // The window has to span the payouts being compared. Anchoring it to the
    // 24h log's first sample made it negative whenever the newest payout was
    // more than a day old — which, on a solar-gated miner, is most of the time.
    const oldestPayoutTime = payouts?.length
      ? Math.min(...payouts.map((p) => p.timestamp))
      : 0;

    if (
      !isPositiveNumber(newestPayoutTime) ||
      !isPositiveNumber(oldestPayoutTime)
    ) {
      reset();
      return;
    }

    const timeWindow = newestPayoutTime - oldestPayoutTime;
    if (timeWindow <= 0) {
      reset();
      return;
    }

    // Cap window at 7 days — beyond that, 24h avg extrapolation is meaningless
    const MAX_WINDOW_DAYS = 7;
    const timeWindowDays = timeWindow / CONFIG.SECONDS_PER_DAY;
    const cappedWindowDays = Math.min(timeWindowDays, MAX_WINDOW_DAYS);
    const expectedXMR = xmrPerDayAvg * cappedWindowDays;

    if (expectedXMR <= 0) {
      reset();
      return;
    }

    const trueLuckFactor = totalXMR / expectedXMR;
    setTextContent("trueLuckFactor", `${trueLuckFactor.toFixed(2)}×`);

    // Show the time window context so users understand accuracy
    const displayHours = cappedWindowDays * 24;
    const windowText =
      displayHours < 1
        ? `${Math.round(timeWindow / 60)}m window`
        : displayHours < 24
          ? `${displayHours.toFixed(1)}h window`
          : `${cappedWindowDays.toFixed(1)}d window`;
    setTooltipContent(
      "trueLuckTooltip",
      `
Over a ${windowText}.
Estimated true luck: total payouts divided by expected earnings
over the same period. Expected earnings use a 24h moving average
of hashrates, so accuracy decreases the further back the window
goes. Shows "–" if no payouts yet or not enough data.
    `.trim()
    );
  } catch (error) {
    console.error("Error updating Estimated True Luck card:", error);
    reset();
  }
}

// ==============================
// OLD DASHBOARD STATS
// ==============================

/** No stratum connections is the idle miner, not a dead node: read the sidechain instead. */
function updatePoolStatus(poolData) {
  const poolStatus = DOM["pool-status"];
  if (!poolStatus) return;

  const isSynced = poolData?.pool_statistics?.sidechainHeight > 0;
  poolStatus.className = `dot ${isSynced ? "is-live" : "is-down"}`;
  setTextContent("pool-status-text", isSynced ? "running" : "unreachable");
}


function updateHashrate24hDisplay() {
  const el = DOM["user-hashrate-24h"];
  if (!el) return;

  const hashrate = state.oldStatsData?.hashrate_24h;
  el.textContent = isPositiveNumber(hashrate) ? scaleHashrate(hashrate) : "–";
}

function updateRewardShareDisplay() {
  const el = DOM["reward-share"];
  if (!el) return;

  const share = state.oldStatsData?.block_reward_share_percent;
  el.textContent = isPositiveNumber(share) ? `${share.toFixed(3)}%` : "–";
}

function updateBlockInfo(poolData) {
  const blocksFound = poolData?.pool_statistics?.totalBlocksFound;
  setTextContent("blocks-found", blocksFound ?? "–");
  setTextContent(
    "last-block-time",
    formatTime(poolData?.pool_statistics?.lastBlockFoundTime)
  );
}

function updateWorkersList() {
  const workersList = DOM["workers-list"];
  if (!workersList) return;

  const workers = state.oldStatsData?.workers;
  if (!Array.isArray(workers) || workers.length === 0) {
    workersList.innerHTML =
      '<div class="no-workers">No miners connected. xmrig is scaled to zero until there is surplus solar.</div>';
    return;
  }

  workersList.innerHTML = workers
    .map((workerStr) => {
      const parts = workerStr.split(",");
      const ipPort = parts[0] || "Unknown";
      const hashrate = safeParseInt(parts[1], 0);
      const name =
        parts[4]?.trim() && parts[4] !== "x"
          ? parts[4]
          : `Miner @ ${ipPort.split(":")[0]}`;

      return `
      <div class="worker-item">
        <span class="worker-name">${name}</span>
        <span class="worker-hash">${scaleHashrate(hashrate)}</span>
      </div>
    `;
    })
    .join("");
}

function updateOldDashboardStats(poolData) {
  updatePoolStatus(poolData);
  updateHashrate24hDisplay();
  setTextContent("shares-found", state.oldStatsData?.shares_found ?? "–");
  setTextContent("shares-failed", state.oldStatsData?.shares_failed ?? "–");
  setTextContent("connections", state.oldStatsData?.connections ?? "–");
  updateRewardShareDisplay();
  updateBlockInfo(poolData);
  setTextContent(
    "last-share-time",
    formatTime(state.oldStatsData?.last_share_found_time)
  );
  updateWorkersList();
}

// ==============================
// TIDE TABLE
// ==============================
// KEDA runs xmrig only on surplus solar, so history reads per calendar day:
// when the miners ran, for how long, and what the day yielded.

function startOfDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function shortDate(ms) {
  const d = new Date(ms);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`;
}

function clockTime(ms) {
  const d = new Date(ms);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function hoursMinutes(ms) {
  const minutes = Math.round(ms / 60000);
  return `${Math.floor(minutes / 60)}:${pad2(minutes % 60)}`;
}

/** Median sample spacing; the server's bucket width depends on the range. */
function sampleStepMs(timestamps) {
  const diffs = timestamps.slice(1).map((t, i) => t - timestamps[i]);
  diffs.sort((a, b) => a - b);
  return (diffs[Math.floor(diffs.length / 2)] || 300) * 1000;
}

/**
 * Split a /stats_history.json window into local calendar days, newest first.
 * A null hashrate is "not recorded" (logger down or price-only backfill), which
 * is kept apart from a recorded 0 H/s so it never reads as an idle day.
 */
function splitDays(data, count) {
  const stamps = data?.timestamps;
  if (!Array.isArray(stamps) || stamps.length < 2) return null;
  const stepMs = sampleStepMs(stamps);

  // setDate rather than subtracting 24h, so DST days keep their own length
  const days = Array.from({ length: count }, (_, i) => {
    const start = new Date(startOfDay(Date.now()));
    start.setDate(start.getDate() - i);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    return {
      start: start.getTime(),
      end: end.getTime(),
      stepMs,
      points: [],
      runs: [],
      recorded: false,
      minedMs: 0,
      hashSum: 0,
      peak: 0,
      xmr: 0
    };
  });

  stamps.forEach((t, i) => {
    const ms = t * 1000;
    const day = days.find((d) => ms >= d.start && ms < d.end);
    if (!day) return;
    const hash = data.myHash?.[i];
    day.points.push([ms, hash]);
    if (hash == null) return;
    day.recorded = true;
    if (!(hash > 0)) return;

    day.minedMs += stepMs;
    day.hashSum += hash;
    day.peak = Math.max(day.peak, hash);
    const net = data.netHash?.[i];
    if (net > 0 && state.blockReward > 0) {
      day.xmr +=
        (hash / net) *
        (stepMs / 1000 / CONFIG.BLOCK_TIME_SECONDS) *
        state.blockReward;
    }
    // Adjacent mined samples merge into one window
    const last = day.runs[day.runs.length - 1];
    if (last && ms - last.end <= stepMs / 2) last.end = ms + stepMs;
    else day.runs.push({ start: ms, end: ms + stepMs });
  });
  return days;
}

/** The tide curve: one day's hashrate as a filled step profile on a 1440x100 box. */
function tideSvg(day, peak, { now = false, grid = true } = {}) {
  const span = day.end - day.start;
  const x = (ms) =>
    Math.max(0, Math.min(1440, ((ms - day.start) / span) * 1440)).toFixed(1);
  const y = (hash) => (100 - Math.min(1, hash / peak) * 92).toFixed(1);

  const runs = [];
  let run = null;
  for (const [ms, hash] of day.points) {
    if (!(hash > 0)) {
      run = null;
      continue;
    }
    if (!run || ms - run.end > day.stepMs / 2) {
      run = { start: ms, end: ms, path: "" };
      runs.push(run);
    }
    run.path += `V${y(hash)}H${x(ms + day.stepMs)}`;
    run.end = ms + day.stepMs;
  }
  const area = runs.map((r) => `M${x(r.start)} 100${r.path}V100Z`).join("");

  const parts = [];
  if (grid) parts.push('<path class="tide__grid" d="M360 0V100M720 0V100M1080 0V100"/>');
  if (area) parts.push(`<path class="tide__area" d="${area}"/>`);
  parts.push('<line class="tide__base" x1="0" y1="100" x2="1440" y2="100"/>');
  if (now && Date.now() < day.end) {
    const nx = x(Date.now());
    parts.push(`<rect class="tide__future" x="${nx}" y="0" width="${1440 - nx}" height="100"/>`);
    parts.push(`<line class="tide__now" x1="${nx}" y1="0" x2="${nx}" y2="100"/>`);
  }
  return parts.join("");
}

function dayName(ms, index) {
  if (index === 0) return "Today";
  if (index === 1) return "Yesterday";
  return new Date(ms).toLocaleDateString("en-GB", { weekday: "long" });
}

function renderWeek(days) {
  if (!DOM.weekRows) return;
  if (!days) {
    DOM.weekRows.innerHTML =
      '<tr><td colspan="7" class="tides__empty">The logger has no history to read yet.</td></tr>';
    return;
  }
  const peak = Math.max(1, ...days.map((d) => d.peak));
  state.weekPeak = peak;

  DOM.weekRows.innerHTML = days
    .map((day, i) => {
      const mined = day.runs.length > 0;
      const window = !day.recorded
        ? "No record"
        : mined
          ? `${clockTime(day.runs[0].start)}–${clockTime(day.runs[day.runs.length - 1].end)}`
          : i === 0
            ? "None yet"
            : "No mining";
      const avg = mined ? scaleHashrate((day.hashSum * day.stepMs) / day.minedMs) : "–";
      // A past slack day really earned 0; today simply has not had its window yet
      const settled = day.recorded && (mined || i > 0);
      const minedText = settled ? hoursMinutes(day.minedMs) : "–";
      const xmr = mined ? day.xmr.toFixed(6) : settled ? "0" : "–";
      const extra = mined ? ` · ${minedText} mined · avg ${avg}` : "";
      const classes = [
        i === 0 && "is-today",
        mined && "is-mined",
        day.recorded && !mined && "is-slack"
      ]
        .filter(Boolean)
        .join(" ");

      return `
        <tr class="${classes}">
          <th scope="row">${dayName(day.start, i)}<span class="tides__date">${shortDate(day.start)}</span></th>
          <td class="tides__curve">
            <svg class="tide" viewBox="0 0 1440 100" preserveAspectRatio="none" aria-hidden="true">${tideSvg(day, peak, { now: i === 0 })}</svg>
          </td>
          <td class="tides__window">${window}<span class="tides__extra">${extra}</span></td>
          <td class="num tides__stat">${minedText}</td>
          <td class="num tides__stat">${avg}</td>
          <td class="num tides__stat">${mined ? scaleHashrate(day.peak) : "–"}</td>
          <td class="num tides__xmr">${xmr}</td>
        </tr>`;
    })
    .join("");

}

function renderSeason(days) {
  if (!DOM.seasonGrid || !days) return;
  const peak = Math.max(1, ...days.map((d) => d.peak));
  const ordered = [...days].reverse();
  // Monday-first columns: pad the first week so every day sits under its weekday
  const lead = (new Date(ordered[0].start).getDay() + 6) % 7;
  const pad = '<div class="day day--pad" aria-hidden="true"></div>'.repeat(lead);

  DOM.seasonGrid.innerHTML =
    pad +
    ordered
      .map((day, i) => {
        const date = new Date(day.start);
        const isToday = i === ordered.length - 1;
        const firstOfMonth = date.getDate() === 1;
        const label = date.toLocaleDateString("en-GB", {
          weekday: "short",
          day: "numeric",
          month: "short"
        });
        // 3h buckets cannot time a short burst, but the mean-based estimate holds
        const summary = !day.recorded
          ? "no record"
          : day.runs.length
            ? `est. ${day.xmr.toFixed(6)} XMR`
            : "slack all day";
        const classes = ["day", isToday && "is-today", !day.recorded && "is-unrecorded"]
          .filter(Boolean)
          .join(" ");
        const dateText = firstOfMonth
          ? date.toLocaleDateString("en-GB", { month: "short" })
          : date.getDate();

        return `<div class="${classes}" role="listitem" title="${label}: ${summary}" aria-label="${label}: ${summary}">
          <span class="day__date${firstOfMonth ? " day__date--month" : ""}">${dateText}</span>
          ${day.recorded ? `<svg class="tide" viewBox="0 0 1440 100" preserveAspectRatio="none" aria-hidden="true">${tideSvg(day, peak, { now: isToday, grid: false })}</svg>` : ""}
        </div>`;
      })
      .join("");
}

/** "16:42" today, "yesterday 16:42", otherwise "Mon 20/10 16:42". */
function whenText(ms) {
  const today = startOfDay(Date.now());
  if (ms >= today) return clockTime(ms);
  if (ms >= startOfDay(today - 1)) return `yesterday ${clockTime(ms)}`;
  const weekday = new Date(ms).toLocaleDateString("en-GB", { weekday: "short" });
  return `${weekday} ${shortDate(ms)} ${clockTime(ms)}`;
}

/**
 * The band answers "is it mining" first. Idle is the normal state, so slack
 * water names the last window rather than showing zeros.
 */
function renderStateBand() {
  if (!DOM.state || !state.live) return;
  const { online, hash } = state.live;
  const days = state.weekDays || [];
  const lastIndex = days.findIndex((d) => d.runs.length);
  const lastDay = days[lastIndex];
  const last = lastDay?.runs.at(-1);
  const today = days[0];

  // Idle with no window yet today: show the last day that had one, not an empty box
  const index = online || today?.runs.length ? 0 : Math.max(lastIndex, 0);
  if (DOM.todayTide && days[index]) {
    DOM.todayTide.innerHTML = tideSvg(days[index], state.weekPeak, { now: index === 0 });
    setTextContent(
      "tideCaption",
      index === 0 ? "Today" : `${dayName(days[index].start, index)}, the last day with mining`
    );
  }

  DOM.state.dataset.state = online ? "mining" : "idle";
  if (online) {
    // A window still open at the latest bucket started this run
    const running = last && Date.now() - last.end < 3 * (today?.stepMs || 0);
    const count = state.live.miners.length;
    setTextContent("stateLine", "Mining");
    setTextContent(
      "stateFigure",
      `${scaleHashrate(hash)} · ${count} ${count === 1 ? "miner" : "miners"}`
    );
    setTextContent(
      "stateDetail",
      `${state.live.miners.map((m) => minerName(m.name)).join(", ")} on surplus solar${running ? ` since ${whenText(last.start)}` : ""}.`
    );
    return;
  }

  setTextContent("stateLine", "Idle");
  setTextContent("stateFigure", last ? `since ${whenText(last.end)}` : "");
  setTextContent(
    "stateDetail",
    last
      ? `Last mined ${clockTime(lastDay.runs[0].start)}–${clockTime(last.end)}, ${hoursMinutes(lastDay.minedMs)} in total. The miners start when there is surplus solar.`
      : "No mining in the last 7 days. The miners start when there is surplus solar."
  );
}

/** Pod name to node: "xmrig-control-1-6f7d8c9b5-x2k4p" reads as "control-1". */
function minerName(name) {
  return name.replace(/^xmrig-/, "").replace(/-[a-z0-9]+-[a-z0-9]+$/, "");
}

/** One miner per node, each gated on its own surplus: name every one that is running. */
function renderMiners(miners) {
  setTextContent(
    "minersCount",
    miners.length ? `${miners.length} running` : "None running"
  );
  setTextContent(
    "minersList",
    miners.length
      ? miners.map((m) => `${minerName(m.name)} ${scaleHashrate(m.hashrate)}`).join("\n")
      : "They start on surplus solar"
  );
}

async function refreshWeek() {
  if (!state.isTabVisible) return;
  const data = await fetchJSON(
    `/stats_history.json?hours=${CONFIG.WEEK_DAYS * 24}&points=${CONFIG.WEEK_DAYS * 288}`
  );
  if (!data) return;
  state.weekDays = splitDays(data, CONFIG.WEEK_DAYS);
  renderWeek(state.weekDays);
  renderStateBand();
}

async function refreshSeason() {
  if (!state.isTabVisible) return;
  const data = await fetchJSON(`/stats_history.json?hours=${CONFIG.SEASON_DAYS * 24}`);
  if (data) renderSeason(splitDays(data, CONFIG.SEASON_DAYS));
}

// ==============================
// MAIN STATS UPDATE
// ==============================

function fetchDashboardData() {
  return Promise.all([
    fetchJSON("/xmrig_summary"),
    fetchJSON("/pool/stats"),
    fetchJSON("/network/stats"),
    fetchJSON("/min_payment_threshold"),
    fetchJSON("/stats_log.json"),
    fetchJSON("/local/stratum"),
    fetchChartWindow()
  ]);
}

function calculateAveragingWindow(historyData) {
  if (
    !isValidObject(historyData) ||
    !Array.isArray(historyData.timestamps) ||
    historyData.timestamps.length === 0
  ) {
    return 0;
  }

  const now = Date.now() / 1000;
  const earliest = historyData.timestamps[0];
  const availableHours = (now - earliest) / CONFIG.SECONDS_PER_HOUR;

  return Math.max(0, Math.min(availableHours, CONFIG.DEFAULT_RANGE_HOURS));
}

/** "24h" once the log spans the full day, else the partial span, e.g. "3.5h". */
function avgWindowText(hours) {
  return hours >= CONFIG.DEFAULT_RANGE_HOURS ? "24h" : `${hours.toFixed(1)}h`;
}

function calculateEarnings(avgMyHash, avgNetHash, blockReward, priceEUR) {
  const myNetShareAvg = avgNetHash > 0 ? avgMyHash / avgNetHash : 0;
  const xmrPerDayAvg = myNetShareAvg * CONFIG.BLOCKS_PER_DAY * blockReward;

  const period = DOM.earnPeriod?.value || "day";
  const multiplier = CONFIG.PERIOD_MULTIPLIERS[period] || 1;

  const xmr = xmrPerDayAvg * multiplier;
  const eur = xmr * priceEUR;

  return { xmr, eur, xmrPerDayAvg };
}

function calculatePayoutInterval(
  avgMyHash,
  avgPoolHash,
  blockReward,
  minPaymentThreshold
) {
  const xmrPerBlock =
    avgPoolHash > 0 ? (avgMyHash / avgPoolHash) * blockReward : 0;

  if (xmrPerBlock <= 0) return { intervalHours: null, intervalText: "N/A" };

  const intervalHours = (minPaymentThreshold / xmrPerBlock) * 24;
  return {
    intervalHours,
    // Weeks between payouts are normal at this hashrate; days read better than 697h
    intervalText:
      intervalHours >= 48
        ? `~${Math.round(intervalHours / 24)} days`
        : `~${intervalHours.toFixed(1)} h`
  };
}

function updateEarningsTooltip(
  avgMyHash,
  avgPoolHash,
  avgNetHash,
  avgWindowHours
) {
  const avgWindowLabel = `${avgWindowText(avgWindowHours)} moving average`;

  setTooltipContent(
    "earnTooltip",
    `
Estimated earnings based on ${avgWindowLabel}.
Avg your hashrate: ${scaleHashrate(avgMyHash)}
Avg pool hashrate: ${scaleHashrate(avgPoolHash)}
Avg network hashrate: ${scaleHashrate(avgNetHash)}
  `.trim()
  );
}

function updateStatsDisplay(
  myHash,
  myHashLabel,
  instPoolHash,
  instNetHash,
  blockReward,
  poolShare,
  priceEUR,
  earnings,
  payoutInfo
) {
  setTextContent("myHashrate", scaleHashrate(myHash));
  setTextContent("myHashrateLabel", myHashLabel);
  setTextContent("poolHashrate", scaleHashrate(instPoolHash));
  setTextContent("netHashrate", scaleHashrate(instNetHash));
  setTextContent("blockReward", blockReward.toFixed(6));
  setTextContent("poolShare", poolShare > 0 ? `${poolShare.toFixed(4)}%` : "–");
  setTextContent("price", `€${priceEUR.toFixed(2)}`);
  setTextContent("earnXMR", `${earnings.xmr.toFixed(6)} XMR`);
  setTextContent("earnEUR", eurText(earnings.eur));

  setTextContent("lastRefreshed", `Updated ${formatDate24Hours(new Date())}`);
  setTextContent("payoutInterval", payoutInfo.intervalText);

  const tooltipIcon = document.getElementById("payoutTerm");
  if (tooltipIcon && payoutInfo.intervalHours !== null) {
    tooltipIcon.title = `Average payout interval: ~${payoutInfo.intervalHours.toFixed(1)} hours\nYour actual payouts can be shorter or longer, depending on mining luck.`;
  }
}

async function updateStats() {
  if (!state.isTabVisible) return;

  // Config is fetched once at boot; a tab opened while the backend was down
  // would otherwise stay stuck on "Observer not configured" until reloaded.
  // Unawaited: initialize() already blocks on it, so this is only the retry
  // path, and it must not sit in front of every poll when the wallet is unset.
  if (!isObserverReady()) loadObserverConfig();

  try {
    const [
      xmrigData,
      poolData,
      networkData,
      thresholdObj,
      hist,
      oldStats,
      chartWindow
    ] = await fetchDashboardData();

    state.history = hist;
    state.oldStatsData = oldStats || {};

    applyChartWindow(chartWindow);

    const xmrigOnline = isValidObject(xmrigData);
    const instMyHash = xmrigOnline ? extractXMRigHashrate(xmrigData) : 0;
    const instPoolHash = extractPoolHashrate(poolData);
    const instNetHash = calculateNetworkHashrate(networkData);
    const blockReward =
      isValidObject(networkData) && isPositiveNumber(networkData.reward)
        ? networkData.reward / CONFIG.ATOMIC_UNITS_PER_XMR
        : 0;
    const minPaymentThreshold =
      thresholdObj?.minPaymentThreshold || CONFIG.DEFAULT_MIN_PAYMENT;

    const avgWindowHours = calculateAveragingWindow(state.history);

    const { avgMyHash, avgPoolHash, avgNetHash } = calculateMovingAverages(
      avgWindowHours,
      state.history,
      instMyHash,
      instPoolHash,
      instNetHash
    );

    // Idle (KEDA-gated): show the moving average instead of 0 H/s
    const [myHash, myHashLabel] = xmrigOnline
      ? [instMyHash, "Your hashrate"]
      : [avgMyHash, `Your hashrate, ${avgWindowText(avgWindowHours)} avg`];
    const poolShare = instPoolHash > 0 ? (myHash / instPoolHash) * 100 : 0;

    state.live = {
      online: xmrigOnline,
      hash: instMyHash,
      miners: xmrigOnline ? xmrigData.miners || [] : []
    };
    renderMiners(state.live.miners);
    state.blockReward = blockReward;
    renderStateBand();

    const priceEUR =
      state.history?.price?.[state.history.price.length - 1] || 0;

    const earnings = calculateEarnings(
      avgMyHash,
      avgNetHash,
      blockReward,
      priceEUR
    );

    const payoutInfo = calculatePayoutInterval(
      avgMyHash,
      avgPoolHash,
      blockReward,
      minPaymentThreshold
    );

    updateStatsDisplay(
      myHash,
      myHashLabel,
      instPoolHash,
      instNetHash,
      blockReward,
      poolShare,
      priceEUR,
      earnings,
      payoutInfo
    );
    updateEarningsTooltip(avgMyHash, avgPoolHash, avgNetHash, avgWindowHours);

    // Fetch observer data once — shared by payments, shares, and luck
    // Note: p2pool.observer /shares does NOT support ?miner= filter,
    // so we fetch all recent shares and filter client-side.
    const fetched =
      state.observerBase && state.observerWallet
        ? await Promise.all([
            fetchJSON(`/observer/payouts/${state.observerWallet}`),
            fetchJSON(`/observer/shares?limit=${CONFIG.OBSERVER_SHARES_LIMIT}`),
            fetchJSON("/observer/pool_info")
          ])
        : [null, null, null];
    const [payouts, allShares, poolInfo] = fetched;

    // Filter shares to only this miner's
    // Note: API returns `miner` as a numeric ID, so we match on `miner_address`
    const minerShares = Array.isArray(allShares)
      ? allShares.filter((s) => s.miner_address === state.observerWallet)
      : [];

    const [newestPayoutTime, totalXMR] = await updateRecentPayments(
      payouts,
      priceEUR
    );

    await updateSharesCard(minerShares, payouts);

    const pplnsWeight =
      poolData?.pool_statistics?.pplnsWeight ||
      poolData?.pool_statistics?.pplns_weight ||
      0;
    const windowStart = await getWindowStartTimestamp(allShares);
    const windowEnd = Date.now() / 1000;
    const windowDuration = windowEnd - windowStart;

    const pplnsWindowHours = windowDuration / CONFIG.SECONDS_PER_HOUR;
    const { avgMyHash: avgMyHashPPLNS } = calculateMovingAverages(
      Math.min(pplnsWindowHours, avgWindowHours || pplnsWindowHours),
      state.history,
      instMyHash,
      instPoolHash,
      instNetHash
    );

    updateWindowLuck({
      shares: minerShares,
      pplnsWeight,
      avgMyHashPPLNS,
      windowStart,
      windowDuration,
      priceEUR,
      blockReward,
      poolInfo
    });

    // Update true luck — the window is the span the payouts themselves cover
    updateTrueLuck(payouts, newestPayoutTime, earnings.xmrPerDayAvg, totalXMR);

    updateOldDashboardStats(poolData);
  } catch (error) {
    console.error("Error in updateStats:", error);
    handleStatsError();
  }
}

function handleStatsError() {
  if (!state.live) {
    setTextContent("stateLine", "No reading");
    setTextContent(
      "stateDetail",
      "The dashboard server did not answer. Retrying every 10 seconds."
    );
  }
  const errorElements = [
    "myHashrate",
    "poolHashrate",
    "netHashrate",
    "price",
    "earnXMR",
    "earnEUR"
  ];
  errorElements.forEach((id) => {
    const el = DOM[id];
    // Only the never-populated ones: these still hold their loading skeleton,
    // so blank text means the first fetch failed rather than a transient one.
    if (el && !el.textContent.trim()) {
      el.textContent = "Error";
    }
  });
}

// ==============================
// EVENT HANDLERS
// ==============================

function handleVisibilityChange() {
  state.isTabVisible = !document.hidden;
  if (state.isTabVisible) {
    updateStats();
    refreshWeek();
  }
}

/** Reflect the selected range on the segmented control. */
function markActiveRange() {
  DOM.rangeControl?.querySelectorAll(".range__opt").forEach((button) => {
    const isActive = Number(button.dataset.hours) === state.currentRangeHours;
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function handleRangeChange(event) {
  const button = event.target.closest(".range__opt");
  if (!button) return;

  state.currentRangeHours = Number(button.dataset.hours);
  markActiveRange();
  // localStorage is unavailable in private-mode Safari; the range still works.
  try {
    localStorage.setItem(CONFIG.RANGE_STORAGE_KEY, state.currentRangeHours);
  } catch {
    /* not persisted */
  }
  refreshChartWindow();
}

function restoreRange() {
  let stored = null;
  try {
    stored = Number(localStorage.getItem(CONFIG.RANGE_STORAGE_KEY));
  } catch {
    /* fall through to the default */
  }
  // Only accept a value the control actually offers, so a stale or hand-edited
  // key cannot leave every button unpressed.
  const offered = [
    ...(DOM.rangeControl?.querySelectorAll(".range__opt") || [])
  ];
  if (offered.some((button) => Number(button.dataset.hours) === stored)) {
    state.currentRangeHours = stored;
  }
  markActiveRange();
}

// ==============================
// INITIALIZATION
// ==============================

async function initialize() {
  cacheDOMElements();

  await loadObserverConfig();

  document.addEventListener("visibilitychange", handleVisibilityChange);

  if (DOM.earnPeriod) {
    DOM.earnPeriod.addEventListener("change", updateStats);
  }

  // One delegated listener rather than six: the buttons never change
  DOM.rangeControl?.addEventListener("click", handleRangeChange);
  restoreRange();

  // updateStats() refetches /stats_log.json and calls initializeCharts() itself
  await updateStats();
  // After the first poll: the day estimates need the block reward it reads
  refreshWeek();
  refreshSeason();

  setInterval(updateStats, CONFIG.REFRESH_INTERVAL_MS);
  setInterval(refreshWeek, CONFIG.WEEK_REFRESH_MS);
  setInterval(refreshSeason, CONFIG.SEASON_REFRESH_MS);
}

initialize();
