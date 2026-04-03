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
    year: 365,
  },

  // Default values
  DEFAULT_RANGE_HOURS: 24,
  DEFAULT_MIN_PAYMENT: 0.01,
  REFRESH_INTERVAL_MS: 5000,
  MOVING_AVERAGE_WINDOW_SECONDS: 600,
  OBSERVER_SHARES_LIMIT: 10000,

  // Hashrate scaling thresholds
  HASHRATE_SCALE: {
    GIGA: 1e9,
    MEGA: 1e6,
    KILO: 1e3,
  },

  // Monero units
  ATOMIC_UNITS_PER_XMR: 1e12,
};

// ==============================
// STATE MANAGEMENT
// ==============================
const state = {
  history: null,
  hashrateChart: null,
  priceChart: null,
  currentRangeHours: CONFIG.DEFAULT_RANGE_HOURS,
  observerBase: null,
  observerWallet: null,
  oldStatsData: {},
  refreshIntervalId: null,
  isTabVisible: true,
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
    "blockReward",
    "poolShare",
    "price",
    "earnXMR",
    "earnEUR",
    "earnPeriod",
    "earnLegend",
    "lastRefreshed",
    "payoutInterval",
    "paymentsStatus",
    "totalEarned",
    "totalEurEarned",
    "paymentsTable",
    "sharesSinceLastPayout",
    "unclesSinceLastPayout",
    "totalSharesMined",
    "totalUnclesMined",
    "luckFactor",
    "trueLuckFactor",
    "xmrThisWindow",
    "eurThisWindow",
    "dayHash",
    "pplnsStart",
    "currentEffort",
    "pool-status",
    "pool-status-text",
    "user-hashrate-24h",
    "shares-found",
    "shares-failed",
    "connections",
    "reward-share",
    "blocks-found",
    "last-share-time",
    "last-block-time",
    "workers-list",
  ];

  ids.forEach((id) => {
    DOM[id] = document.getElementById(id);
  });
}

// ==============================
// UTILITY FUNCTIONS
// ==============================

/**
 * Validates that a value is a non-null object
 * @param {*} value - value to check
 * @returns {boolean}
 */
function isValidObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Validates that a value is a positive number
 * @param {*} value - value to check
 * @returns {boolean}
 */
function isPositiveNumber(value) {
  return typeof value === "number" && !Number.isNaN(value) && value > 0;
}

/**
 * Safely parses an integer with fallback
 * @param {*} value - value to parse
 * @param {number} fallback - fallback value if parsing fails
 * @returns {number}
 */
function safeParseInt(value, fallback = 0) {
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

/**
 * Converts a hashrate value to a human-readable string with units
 * @param {number} hashrate - hashrate in H/s
 * @returns {string} formatted hashrate
 */
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

/**
 * Pads a number to 2 digits with leading zero
 * @param {number} n - number to pad
 * @returns {string}
 */
function pad2(n) {
  return String(n).padStart(2, "0");
}

/**
 * Formats a date as DD/MM/YYYY HH:MM:SS
 * @param {Date} date - date to format
 * @returns {string}
 */
function formatDate24(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "Invalid date";
  }

  return `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)}/${date.getFullYear()} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/**
 * Formats time as HH:MM:SS
 * @param {Date} date - date to format
 * @returns {string}
 */
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
  const {
    zeroLabel = "Never",
    invalidLabel = "Unknown",
    underMinute,
  } = opts;

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
    zeroLabel: "Never",
    invalidLabel: "Invalid",
    underMinute: () => "Just now",
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
    labels: historyData.timestamps.slice(startIndex).map((t) => t * 1000),
    myHash: historyData.myHash?.slice(startIndex) || [],
    poolHash: historyData.poolHash?.slice(startIndex) || [],
    netHash: historyData.netHash?.slice(startIndex) || [],
    price: historyData.price?.slice(startIndex) || [],
  };
}

/**
 * Compute a moving average over a given time window
 * @param {number[]} timestamps - array of timestamps in seconds
 * @param {number[]} values - array of corresponding values
 * @param {number} windowSeconds - window size in seconds
 * @returns {number} latest smoothed value
 */
function movingAverage(
  timestamps,
  values,
  windowSeconds = CONFIG.MOVING_AVERAGE_WINDOW_SECONDS,
) {
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

/**
 * Extract hashrate from XMRig data
 * @param {Object} xmrigData - XMRig summary data
 * @returns {number} hashrate in H/s
 */
function extractXMRigHashrate(xmrigData) {
  if (!isValidObject(xmrigData)) return 0;
  return xmrigData.hashrate?.total?.[0] || xmrigData.hashrate?.total || 0;
}

/**
 * Extract pool hashrate from pool data
 * @param {Object} poolData - pool statistics data
 * @returns {number} hashrate in H/s
 */
function extractPoolHashrate(poolData) {
  if (!isValidObject(poolData)) return 0;
  return (
    poolData.pool_statistics?.hashRate ||
    poolData.pool_statistics?.hashrate ||
    0
  );
}

/**
 * Calculate network hashrate from difficulty
 * @param {Object} networkData - network statistics data
 * @returns {number} hashrate in H/s
 */
function calculateNetworkHashrate(networkData) {
  if (
    !isValidObject(networkData) ||
    !isPositiveNumber(networkData.difficulty)
  ) {
    return 0;
  }
  return networkData.difficulty / CONFIG.BLOCK_TIME_SECONDS;
}

/**
 * Calculate moving averages from history data
 * @param {number} windowHours - hours of history to use
 * @param {Object} historyData - history data object
 * @param {number} instMyHash - instantaneous my hashrate
 * @param {number} instPoolHash - instantaneous pool hashrate
 * @param {number} instNetHash - instantaneous network hashrate
 * @returns {Object} averaged hashrates
 */
function calculateMovingAverages(
  windowHours,
  historyData,
  instMyHash,
  instPoolHash,
  instNetHash,
) {
  const fallback = {
    avgMyHash: instMyHash,
    avgPoolHash: instPoolHash,
    avgNetHash: instNetHash,
  };

  if (windowHours <= 0 || !historyData) {
    return fallback;
  }

  const sliced = sliceHistory(windowHours, historyData);
  if (!sliced) {
    return fallback;
  }

  const windowSeconds = windowHours * CONFIG.SECONDS_PER_HOUR;
  const timestamps = sliced.labels.map((t) => t / 1000);

  return {
    avgMyHash:
      movingAverage(timestamps, sliced.myHash, windowSeconds) || instMyHash,
    avgPoolHash:
      movingAverage(timestamps, sliced.poolHash, windowSeconds) || instPoolHash,
    avgNetHash:
      movingAverage(timestamps, sliced.netHash, windowSeconds) || instNetHash,
  };
}

// ==============================
// OBSERVER CONFIGURATION
// ==============================

async function loadObserverConfig() {
  try {
    const cfg = await fetchJSON("/observer_config");
    if (
      !isValidObject(cfg) ||
      !cfg.wallet ||
      !Array.isArray(cfg.observers) ||
      cfg.observers.length === 0
    ) {
      return null;
    }

    state.observerBase = cfg.observers[0];
    state.observerWallet = cfg.wallet;
    return cfg;
  } catch {
    console.warn("Observer config unavailable");
    return null;
  }
}

async function getWindowStartTimestamp() {
  if (!state.observerBase) return Date.now() / 1000;

  try {
    const newestShare = await fetchJSON(`/observer/shares?limit=1`);
    if (!Array.isArray(newestShare) || newestShare.length === 0) {
      console.warn("No shares returned from API");
      return Date.now() / 1000;
    }

    const windowDepth = newestShare[0].window_depth;
    if (!isPositiveNumber(windowDepth)) {
      return Date.now() / 1000;
    }

    const sharesInWindow = await fetchJSON(
      `/observer/shares?limit=${windowDepth}`,
    );
    if (!Array.isArray(sharesInWindow) || sharesInWindow.length === 0) {
      console.warn("No shares returned for PPLNS window");
      return Date.now() / 1000;
    }

    const windowStartShare = sharesInWindow[sharesInWindow.length - 1];
    return Math.floor(windowStartShare?.timestamp || Date.now() / 1000);
  } catch {
    console.warn("Failed to get window start timestamp");
    return Date.now() / 1000;
  }
}

/**
 * Check if observer API is configured and ready to use.
 * @returns {boolean}
 */
function isObserverReady() {
  return !!state.observerWallet && !!state.observerBase;
}

// ==============================
// UI UPDATE HELPERS
// ==============================

function setTextContent(elementId, text) {
  const el = DOM[elementId];
  if (el) el.textContent = text;
}

/**
 * Sets the title on an existing tooltip element.
 * @param {string} tooltipId - id of the tooltip element
 * @param {string} content - tooltip text
 */
function setTooltipContent(tooltipId, content) {
  const tooltip = document.getElementById(tooltipId);
  if (tooltip && content) {
    tooltip.title = content;
  }
}

// ==============================
// CHART FUNCTIONS
// ==============================

/**
 * Shared Chart.js options used by both hashrate and price charts.
 * Override yTicks or tooltipCallbacks as needed.
 */
function sharedChartOptions(overrides = {}) {
  const { yTicks, tooltipCallbacks } = overrides;

  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#111827",
        titleColor: "#e5e7eb",
        bodyColor: "#9ca3af",
        borderColor: "#1f2937",
        borderWidth: 1,
        padding: 10,
        displayColors: false,
        ...(tooltipCallbacks ? { callbacks: tooltipCallbacks } : {}),
      },
    },
    scales: {
      x: {
        type: "time",
        time: {
          tooltipFormat: "dd/MM/yyyy HH:mm:ss",
          displayFormats: {
            hour: "dd/MM/yyyy HH:mm",
            minute: "dd/MM/yyyy HH:mm",
          },
        },
        grid: {
          color: "rgba(31, 41, 55, 0.5)",
          drawBorder: false,
        },
        ticks: {
          color: "#6b7280",
          font: { size: 10 },
          maxTicksLimit: 6,
        },
      },
      y: {
        ...(yTicks ? { ticks: yTicks } : {}),
        grid: {
          color: "rgba(31, 41, 55, 0.5)",
          drawBorder: false,
        },
      },
    },
    interaction: {
      intersect: false,
      mode: "index",
    },
  };
}

/**
 * Shared dataset config for line charts.
 */
function lineDataset(label, data) {
  return {
    label,
    data,
    borderColor: "#ef4444",
    backgroundColor: "rgba(239, 68, 68, 0.08)",
    borderWidth: 1.5,
    fill: true,
    tension: 0.3,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: "#ef4444",
  };
}

/**
 * Creates or updates a Chart.js instance.
 * @param {HTMLCanvasElement} canvas
 * @param {Object} state - state object
 * @param {string} stateKey - key in state (e.g. 'hashrateChart')
 * @param {Object} chartConfig - { label, data, yTicks, tooltipCallbacks }
 */
function initOrUpdateChart(canvas, state, stateKey, chartConfig) {
  if (!canvas) return;

  const config = {
    type: "line",
    data: {
      labels: chartConfig.labels,
      datasets: [lineDataset(chartConfig.label, chartConfig.data)],
    },
    options: sharedChartOptions({
      yTicks: chartConfig.yTicks,
      tooltipCallbacks: chartConfig.tooltipCallbacks,
    }),
  };

  if (!state[stateKey]) {
    state[stateKey] = new Chart(canvas, config);
  } else {
    state[stateKey].data.labels = chartConfig.labels;
    state[stateKey].data.datasets[0].data = chartConfig.data;
    state[stateKey].update();
  }
}

function initializeCharts() {
  if (!state.history) return;
  if (typeof Chart === "undefined") {
    console.warn("Chart.js not loaded");
    return;
  }

  const slicedHistoryData = sliceHistory(
    state.currentRangeHours,
    state.history,
  );
  if (!slicedHistoryData) return;

  initializeHashrateChart(slicedHistoryData);
  initializePriceChart(slicedHistoryData);
}

function initializeHashrateChart(slicedHistoryData) {
  try {
    initOrUpdateChart(DOM.hashrateChart, state, "hashrateChart", {
      label: "Your Hashrate",
      data: slicedHistoryData.myHash,
      labels: slicedHistoryData.labels,
      yTicks: {
        callback: scaleHashrate,
        color: "#6b7280",
        font: { size: 10 },
      },
    });
  } catch (error) {
    console.error("Failed to initialize hashrate chart:", error);
  }
}

function initializePriceChart(slicedHistoryData) {
  try {
    initOrUpdateChart(DOM.priceChart, state, "priceChart", {
      label: "XMR Price (EUR)",
      data: slicedHistoryData.price,
      labels: slicedHistoryData.labels,
      yTicks: {
        callback: (v) => `€${v.toFixed(2)}`,
        color: "#6b7280",
        font: { size: 10 },
      },
      tooltipCallbacks: {
        label: (ctx) => `€${ctx.parsed.y.toFixed(2)}`,
      },
    });
  } catch (error) {
    console.error("Failed to initialize price chart:", error);
  }
}

// ==============================
// PAYMENTS & SHARES FUNCTIONS
// ==============================

async function updateRecentPayments(payouts, priceEUR) {
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

    const sortedPayouts = [...payouts].sort((a, b) => b.timestamp - a.timestamp);
    const newestPayoutTime = sortedPayouts[0].timestamp;

    const totalXMR = sortedPayouts.reduce(
      (sum, p) => sum + p.coinbase_reward / CONFIG.ATOMIC_UNITS_PER_XMR,
      0,
    );

    setTextContent("totalEarned", `${totalXMR.toFixed(6)} XMR`);
    setTextContent(
      "totalEurEarned",
      typeof priceEUR === "number"
        ? `≈ €${(totalXMR * priceEUR).toFixed(2)}`
        : "",
    );

    // Update table
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
            <td class="cell-right">${xmr.toFixed(6)}</td>
            <td class="cell-right">${eurValue}</td>
          </tr>
        `;
        })
        .join("");
    }

    setTextContent(
      "paymentsStatus",
      `Showing last ${Math.min(CONFIG.PPLNS_WINDOW_MAX_SHARES, sortedPayouts.length)} payouts`,
    );

    return [newestPayoutTime, totalXMR];
  } catch {
    setTextContent("paymentsStatus", "Payout data unavailable");
    setTextContent("totalEarned", "–");
    if (tbody) tbody.innerHTML = "";
    return [null, 0];
  }
}

async function updateSharesCard(shares, payouts) {
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
  } catch {
    updateSharesDisplay("–", "–", "–", "–");
  }
}

function updateSharesDisplay(
  sharesSince,
  unclesSince,
  totalShares,
  totalUncles,
) {
  setTextContent("sharesSinceLastPayout", sharesSince);
  setTextContent("unclesSinceLastPayout", unclesSince);
  setTextContent("totalSharesMined", `Total shares: ${totalShares}`);
  setTextContent("totalUnclesMined", `Total uncles: ${totalUncles}`);
}

// ==============================
// LUCK CALCULATION FUNCTIONS
// ==============================

async function updateWindowLuck(
  shares,
  pplnsWeight,
  avgPoolHashPPLNS,
  avgMyHashPPLNS,
  windowStart,
  windowDuration,
  priceEUR,
  blockReward,
) {
  if (!isObserverReady()) return;

  try {
    if (!Array.isArray(shares)) {
      console.error("Invalid shares data in updateWindowLuck");
      return;
    }

    const sortedShares = [...shares].sort((a, b) => a.timestamp - b.timestamp);

    const myWindowShares = sortedShares.filter(
      (share) => share.timestamp >= windowStart,
    );
    const totalDifficulty = myWindowShares.reduce(
      (sum, share) => sum + (share.difficulty || 0),
      0,
    );
    const difficultyShare = pplnsWeight > 0 ? totalDifficulty / pplnsWeight : 0;

    const myWindowHash =
      windowDuration > 0 ? totalDifficulty / windowDuration : 0;
    const luckFactor = avgMyHashPPLNS > 0 ? myWindowHash / avgMyHashPPLNS : 0;

    const poolInfo = await fetchJSON(`/observer/pool_info`);

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
    `.trim(),
    );

    setTextContent("luckFactor", betterLuckFactor.toFixed(2));
    setTextContent("xmrThisWindow", accumulatedXMR.toFixed(12));
    setTextContent("eurThisWindow", `≈ €${accumulatedEUR.toFixed(2)}`);
    setTextContent("dayHash", scaleHashrate(myWindowHash));
    setTextContent(
      "pplnsStart",
      formatDate24Hours(new Date(windowStart * 1000)),
    );
    setTextContent(
      "currentEffort",
      (poolInfo?.sidechain?.effort?.current || 0).toFixed(2),
    );
  } catch (error) {
    console.error("Error updating PPLNS Window Luck card:", error);
  }
}

function updateTrueLuck(
  startedMiningTimestamp,
  newestPayoutTime,
  xmrPerDayAvg,
  totalXMR,
) {
  const reset = () => {
    setTextContent("trueLuckFactor", "–");
    setTextContent("trueLuckWindow", "–");
  };

  try {
    if (
      !isPositiveNumber(newestPayoutTime) ||
      !isPositiveNumber(startedMiningTimestamp)
    ) {
      reset();
      return;
    }

    const timeWindow = newestPayoutTime - startedMiningTimestamp;
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
    setTextContent("trueLuckFactor", trueLuckFactor.toFixed(2));

    // Show the time window context so users understand accuracy
    const displayDays = Math.min(timeWindowDays, MAX_WINDOW_DAYS);
    const displayHours = displayDays * 24;
    const windowText =
      displayHours < 1
        ? `${Math.round(timeWindow / 60)}m window`
        : displayHours < 24
          ? `${displayHours.toFixed(1)}h window`
          : `${displayDays.toFixed(1)}d window`;
    setTextContent("trueLuckWindow", windowText);

    setTooltipContent(
      "trueLuckTooltip",
      `
Estimated true luck: total payouts divided by expected earnings
over the same period. Expected earnings use a 24h moving average
of hashrates, so accuracy decreases the further back the window
goes. Shows "–" if no payouts yet or not enough data.
    `.trim(),
    );
  } catch (error) {
    console.error("Error updating Estimated True Luck card:", error);
    reset();
  }
}

// ==============================
// OLD DASHBOARD STATS (REFACTORED)
// ==============================

function updatePoolStatus() {
  const poolStatus = DOM["pool-status"];
  const poolStatusText = DOM["pool-status-text"];

  if (!poolStatus || !poolStatusText) return;

  const isActive = state.oldStatsData?.connections > 0;
  poolStatus.className = `status-indicator status-${isActive ? "active" : "inactive"}`;
  poolStatusText.textContent = isActive ? "Active" : "Inactive";
}

function updateHashrate24hDisplay() {
  const el = DOM["user-hashrate-24h"];
  if (!el) return;

  const hashrate = state.oldStatsData?.hashrate_24h;
  el.textContent = isPositiveNumber(hashrate) ? scaleHashrate(hashrate) : "–";
}

function updateSharesDisplayOld() {
  setTextContent("shares-found", state.oldStatsData?.shares_found ?? "–");
  setTextContent("shares-failed", state.oldStatsData?.shares_failed ?? "–");
}

function updateConnectionsDisplay() {
  setTextContent("connections", state.oldStatsData?.connections ?? "–");
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
    formatTime(poolData?.pool_statistics?.lastBlockFoundTime),
  );
}

function updateShareTimes() {
  setTextContent(
    "last-share-time",
    formatTime(state.oldStatsData?.last_share_found_time),
  );
}

function updateWorkersList() {
  const workersList = DOM["workers-list"];
  if (!workersList) return;

  const workers = state.oldStatsData?.workers;
  if (!Array.isArray(workers) || workers.length === 0) {
    workersList.innerHTML = '<div class="no-workers">No miners connected</div>';
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
  updatePoolStatus();
  updateHashrate24hDisplay();
  updateSharesDisplayOld();
  updateConnectionsDisplay();
  updateRewardShareDisplay();
  updateBlockInfo(poolData);
  updateShareTimes();
  updateWorkersList();
}

// ==============================
// MAIN STATS UPDATE (REFACTORED)
// ==============================

async function fetchDashboardData() {
  const results = await Promise.allSettled([
    fetchJSON("/xmrig_summary"),
    fetchJSON("/pool/stats"),
    fetchJSON("/network/stats"),
    fetchJSON("/min_payment_threshold"),
    fetchJSON("/stats_log.json"),
    fetchJSON("/local/stratum"),
  ]);

  return results.map((r) => (r.status === "fulfilled" ? r.value : null));
}

function calculateAveragingWindow(historyData) {
  if (
    !isValidObject(historyData) ||
    !Array.isArray(historyData.timestamps) ||
    historyData.timestamps.length === 0
  ) {
    return { avgWindowHours: 0, hasEnoughData: false };
  }

  const now = Date.now() / 1000;
  const earliest = historyData.timestamps[0];
  const availableHours = (now - earliest) / CONFIG.SECONDS_PER_HOUR;

  const avgWindowHours = Math.max(
    0,
    Math.min(availableHours, CONFIG.DEFAULT_RANGE_HOURS),
  );

  return { avgWindowHours, hasEnoughData: avgWindowHours > 0 };
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
  minPaymentThreshold,
) {
  const xmrPerBlock =
    avgPoolHash > 0 ? (avgMyHash / avgPoolHash) * blockReward : 0;

  if (xmrPerBlock <= 0) return { intervalHours: null, intervalText: "N/A" };

  const intervalHours = (minPaymentThreshold / xmrPerBlock) * 24;
  return {
    intervalHours,
    intervalText: `~${intervalHours.toFixed(1)}h/payout`,
  };
}

function updateEarningsTooltip(
  avgMyHash,
  avgPoolHash,
  avgNetHash,
  avgWindowHours,
) {
  const avgWindowLabel =
    avgWindowHours >= CONFIG.DEFAULT_RANGE_HOURS
      ? "24h moving average"
      : `${avgWindowHours.toFixed(1)}h moving average`;

  setTooltipContent(
    "earnTooltip",
    `
Estimated earnings based on ${avgWindowLabel}.
Avg your hashrate: ${scaleHashrate(avgMyHash)}
Avg pool hashrate: ${scaleHashrate(avgPoolHash)}
Avg network hashrate: ${scaleHashrate(avgNetHash)}
  `.trim(),
  );
}

function updateStatsDisplay(
  instMyHash,
  instPoolHash,
  instNetHash,
  blockReward,
  poolShare,
  priceEUR,
  earnings,
  payoutInfo,
  avgWindowHours,
) {
  setTextContent("myHashrate", scaleHashrate(instMyHash));
  setTextContent("poolHashrate", scaleHashrate(instPoolHash));
  setTextContent("netHashrate", scaleHashrate(instNetHash));
  setTextContent("blockReward", blockReward.toFixed(6));
  setTextContent("poolShare", poolShare > 0 ? `${poolShare.toFixed(4)}%` : "–");
  setTextContent("price", `€${priceEUR.toFixed(2)}`);
  setTextContent("earnXMR", `${earnings.xmr.toFixed(6)} XMR`);
  setTextContent("earnEUR", `≈ €${earnings.eur.toFixed(2)}`);

  const legendText =
    avgWindowHours >= CONFIG.DEFAULT_RANGE_HOURS
      ? "Based on 24h moving average"
      : `Based on ${avgWindowHours.toFixed(1)}h moving average`;
  setTextContent("earnLegend", legendText);

  setTextContent(
    "lastRefreshed",
    `Last refreshed: ${formatDate24(new Date())}`,
  );
  setTextContent("payoutInterval", payoutInfo.intervalText);

  // Update payout tooltip
  const tooltipIcon = document.querySelector(".bottom-stats .tooltip-icon");
  if (tooltipIcon && payoutInfo.intervalHours !== null) {
    tooltipIcon.title = `Average payout interval: ~${payoutInfo.intervalHours.toFixed(1)} hours\nYour actual payouts can be shorter or longer, depending on mining luck.`;
  }
}

async function updateStats() {
  if (!state.isTabVisible) return;

  try {
    const [xmrigData, poolData, networkData, thresholdObj, hist, oldStats] =
      await fetchDashboardData();

    state.history = hist;
    state.oldStatsData = oldStats || {};

    if (state.history) {
      initializeCharts();
    }

    // Extract instantaneous values
    const instMyHash = extractXMRigHashrate(xmrigData);
    const instPoolHash = extractPoolHashrate(poolData);
    const instNetHash = calculateNetworkHashrate(networkData);
    const blockReward =
      isValidObject(networkData) && isPositiveNumber(networkData.reward)
        ? networkData.reward / CONFIG.ATOMIC_UNITS_PER_XMR
        : 0;
    const minPaymentThreshold =
      thresholdObj?.minPaymentThreshold || CONFIG.DEFAULT_MIN_PAYMENT;

    // Calculate averaging window
    const { avgWindowHours, hasEnoughData } = calculateAveragingWindow(
      state.history,
    );

    // Calculate moving averages
    const { avgMyHash, avgPoolHash, avgNetHash } = hasEnoughData
      ? calculateMovingAverages(
          avgWindowHours,
          state.history,
          instMyHash,
          instPoolHash,
          instNetHash,
        )
      : {
          avgMyHash: instMyHash,
          avgPoolHash: instPoolHash,
          avgNetHash: instNetHash,
        };

    // Calculate pool share
    const poolShare = instPoolHash > 0 ? (instMyHash / instPoolHash) * 100 : 0;

    // Get current price
    const priceEUR =
      state.history?.price?.[state.history.price.length - 1] || 0;

    // Calculate earnings
    const earnings = calculateEarnings(
      avgMyHash,
      avgNetHash,
      blockReward,
      priceEUR,
    );

    // Calculate payout interval
    const payoutInfo = calculatePayoutInterval(
      avgMyHash,
      avgPoolHash,
      blockReward,
      minPaymentThreshold,
    );

    // Update all displays
    updateStatsDisplay(
      instMyHash,
      instPoolHash,
      instNetHash,
      blockReward,
      poolShare,
      priceEUR,
      earnings,
      payoutInfo,
      avgWindowHours,
    );
    updateEarningsTooltip(avgMyHash, avgPoolHash, avgNetHash, avgWindowHours);

    // Fetch observer data once — shared by payments, shares, and luck
    // Note: p2pool.observer /shares does NOT support ?miner= filter,
    // so we fetch all recent shares and filter client-side.
    const [payouts, allShares] = state.observerBase && state.observerWallet
      ? await Promise.all([
          fetchJSON(`/observer/payouts/${state.observerWallet}`),
          fetchJSON(`/observer/shares?limit=${CONFIG.OBSERVER_SHARES_LIMIT}`),
        ])
      : [null, null];

    // Filter shares to only this miner's
    // Note: API returns `miner` as a numeric ID, so we match on `miner_address`
    const minerShares = Array.isArray(allShares)
      ? allShares.filter((s) => s.miner_address === state.observerWallet)
      : [];

    // Update payments
    const [newestPayoutTime, totalXMR] = await updateRecentPayments(payouts, priceEUR);

    // Update shares
    await updateSharesCard(minerShares, payouts);

    // Calculate PPLNS window data
    const pplnsWeight =
      poolData?.pool_statistics?.pplnsWeight ||
      poolData?.pool_statistics?.pplns_weight ||
      0;
    const windowStart = await getWindowStartTimestamp();
    const windowEnd = Date.now() / 1000;
    const windowDuration = windowEnd - windowStart;

    // Calculate PPLNS moving averages
    const pplnsWindowHours = windowDuration / CONFIG.SECONDS_PER_HOUR;
    const { avgMyHash: avgMyHashPPLNS, avgPoolHash: avgPoolHashPPLNS } =
      calculateMovingAverages(
        Math.min(pplnsWindowHours, avgWindowHours || pplnsWindowHours),
        state.history,
        instMyHash,
        instPoolHash,
        instNetHash,
      );

    // Update luck cards
    await updateWindowLuck(
      minerShares,
      pplnsWeight,
      avgPoolHashPPLNS,
      avgMyHashPPLNS,
      windowStart,
      windowDuration,
      priceEUR,
      blockReward,
    );

    // Update true luck — auto-detect mining start from history data
    if (state.history && Array.isArray(state.history.timestamps) && state.history.timestamps.length > 0) {
      const historyStart = state.history.timestamps[0];
      updateTrueLuck(
        historyStart,
        newestPayoutTime,
        earnings.xmrPerDayAvg,
        totalXMR,
      );
    }

    // Update old dashboard stats
    updateOldDashboardStats(poolData);
  } catch (error) {
    console.error("Error in updateStats:", error);
    handleStatsError();
  }
}

function handleStatsError() {
  const errorElements = [
    "myHashrate",
    "poolHashrate",
    "netHashrate",
    "price",
    "earnXMR",
    "earnEUR",
  ];
  errorElements.forEach((id) => {
    const el = DOM[id];
    if (el?.textContent === "Loading…") {
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
  }
}

function handleEarnPeriodChange() {
  updateStats();
}

function cleanup() {
  if (state.refreshIntervalId) {
    clearInterval(state.refreshIntervalId);
    state.refreshIntervalId = null;
  }
}

// ==============================
// INITIALIZATION
// ==============================

async function initialize() {
  cacheDOMElements();

  // Load initial history
  try {
    state.history = await fetchJSON("/stats_log.json");
  } catch {
    state.history = null;
  }

  await loadObserverConfig();

  // Setup event listeners
  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener("beforeunload", cleanup);

  if (DOM.earnPeriod) {
    DOM.earnPeriod.addEventListener("change", handleEarnPeriodChange);
  }

  // Initialize charts and stats
  initializeCharts();
  await updateStats();

  // Start periodic updates
  state.refreshIntervalId = setInterval(
    updateStats,
    CONFIG.REFRESH_INTERVAL_MS,
  );
}

// Start the application
initialize();
