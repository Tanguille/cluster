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
  REFRESH_INTERVAL_MS: 5000,
  MOVING_AVERAGE_WINDOW_SECONDS: 600,

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
  hashrateChart: null,
  priceChart: null,
  currentRangeHours: CONFIG.DEFAULT_RANGE_HOURS,
  observerConfig: null,
  observerBase: null,
  observerWallet: null,
  oldStatsData: {},
  refreshIntervalId: null,
  isTabVisible: true
};

// ==============================
// DOM ELEMENT CACHE
// ==============================
let DOM = {};

function cacheDOMElements() {
  const ids = [
    'hashrateChart', 'priceChart', 'myHashrate', 'poolHashrate', 'netHashrate',
    'blockReward', 'poolShare', 'price', 'earnXMR', 'earnEUR', 'earnPeriod',
    'earnLegend', 'lastRefreshed', 'payoutInterval', 'paymentsStatus', 'totalEarned',
    'totalEurEarned', 'paymentsTable', 'sharesSinceLastPayout', 'unclesSinceLastPayout',
    'totalSharesMined', 'totalUnclesMined', 'luckFactor', 'trueLuckFactor',
    'xmrThisWindow', 'eurThisWindow', 'dayHash', 'pplnsStart', 'currentEffort',
    'startedMining', 'pool-status', 'pool-status-text', 'user-hashrate-24h',
    'shares-found', 'shares-failed', 'connections', 'reward-share', 'blocks-found',
    'last-share-time', 'last-block-time', 'workers-list'
  ];

  ids.forEach(id => {
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
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Validates that a value is a positive number
 * @param {*} value - value to check
 * @returns {boolean}
 */
function isPositiveNumber(value) {
  return typeof value === 'number' && !Number.isNaN(value) && value > 0;
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
  if (!isPositiveNumber(hashrate)) return '0 H/s';

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
 * Formats a date as DD/MM/YYYY HH:MM:SS
 * @param {Date} date - date to format
 * @returns {string}
 */
function formatDate24(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return 'Invalid date';
  }

  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * Formats time as HH:MM:SS
 * @param {Date} date - date to format
 * @returns {string}
 */
function formatDate24Hours(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return 'Invalid time';
  }

  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * Formats relative time (e.g., "5m ago")
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {string}
 */
function formatRelativeTime(timestamp) {
  if (!isPositiveNumber(timestamp)) return 'Unknown';

  const diff = Math.floor(Date.now() / 1000 - timestamp);

  if (diff < CONFIG.SECONDS_PER_MINUTE) return `${diff}s ago`;
  if (diff < CONFIG.SECONDS_PER_HOUR) return `${Math.floor(diff / CONFIG.SECONDS_PER_MINUTE)}m ago`;
  if (diff < CONFIG.SECONDS_PER_DAY) return `${Math.floor(diff / CONFIG.SECONDS_PER_HOUR)}h ago`;
  return `${Math.floor(diff / CONFIG.SECONDS_PER_DAY)}d ago`;
}

/**
 * Formats time difference from now
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {string}
 */
function formatTime(timestamp) {
  if (!timestamp || timestamp === 0) return 'Never';

  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return 'Invalid';

  const diffMs = Date.now() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
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
    price: historyData.price?.slice(startIndex) || []
  };
}

/**
 * Compute a moving average over a given time window
 * @param {number[]} timestamps - array of timestamps in seconds
 * @param {number[]} values - array of corresponding values
 * @param {number} windowSeconds - window size in seconds
 * @returns {number} latest smoothed value
 */
function movingAverage(timestamps, values, windowSeconds = CONFIG.MOVING_AVERAGE_WINDOW_SECONDS) {
  if (!Array.isArray(timestamps) || !Array.isArray(values) || timestamps.length === 0 || values.length === 0) {
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
  return poolData.pool_statistics?.hashRate || poolData.pool_statistics?.hashrate || 0;
}

/**
 * Calculate network hashrate from difficulty
 * @param {Object} networkData - network statistics data
 * @returns {number} hashrate in H/s
 */
function calculateNetworkHashrate(networkData) {
  if (!isValidObject(networkData) || !isPositiveNumber(networkData.difficulty)) {
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
function calculateMovingAverages(windowHours, historyData, instMyHash, instPoolHash, instNetHash) {
  if (windowHours <= 0 || !historyData) {
    return { avgMyHash: instMyHash, avgPoolHash: instPoolHash, avgNetHash: instNetHash };
  }

  const sliced = sliceHistory(windowHours, historyData);
  if (!sliced) {
    return { avgMyHash: instMyHash, avgPoolHash: instPoolHash, avgNetHash: instNetHash };
  }

  const windowSeconds = windowHours * CONFIG.SECONDS_PER_HOUR;
  const timestamps = sliced.labels.map((t) => t / 1000);

  return {
    avgMyHash: movingAverage(timestamps, sliced.myHash, windowSeconds) || instMyHash,
    avgPoolHash: movingAverage(timestamps, sliced.poolHash, windowSeconds) || instPoolHash,
    avgNetHash: movingAverage(timestamps, sliced.netHash, windowSeconds) || instNetHash
  };
}

// ==============================
// OBSERVER CONFIGURATION
// ==============================

async function loadObserverConfig() {
  try {
    const cfg = await fetchJSON('/observer_config');
    if (!isValidObject(cfg) || !cfg.wallet || !Array.isArray(cfg.observers) || cfg.observers.length === 0) {
      return null;
    }

    state.observerConfig = cfg;
    state.observerWallet = cfg.wallet;
    state.observerBase = cfg.observers[0];
    return cfg;
  } catch {
    console.warn('Observer config unavailable');
    return null;
  }
}

async function getWindowStartTimestamp() {
  if (!state.observerBase) return Date.now() / 1000;

  try {
    const newestShare = await fetchJSON(`${state.observerBase}/shares?limit=1`);
    if (!Array.isArray(newestShare) || newestShare.length === 0) {
      console.warn('No shares returned from API');
      return Date.now() / 1000;
    }

    const windowDepth = newestShare[0].window_depth;
    if (!isPositiveNumber(windowDepth)) {
      return Date.now() / 1000;
    }

    const sharesInWindow = await fetchJSON(`${state.observerBase}/shares?limit=${windowDepth}`);
    if (!Array.isArray(sharesInWindow) || sharesInWindow.length === 0) {
      console.warn('No shares returned for PPLNS window');
      return Date.now() / 1000;
    }

    const windowStartShare = sharesInWindow[sharesInWindow.length - 1];
    return Math.floor(windowStartShare?.timestamp || Date.now() / 1000);
  } catch {
    console.warn('Failed to get window start timestamp');
    return Date.now() / 1000;
  }
}

// ==============================
// UI UPDATE HELPERS
// ==============================

function setTextContent(elementId, text) {
  const el = DOM[elementId];
  if (el) el.textContent = text;
}

function createTooltipIfNeeded(containerId, tooltipId, content) {
  const container = DOM[containerId];
  if (!container) return null;

  let tooltip = document.getElementById(tooltipId);
  if (!tooltip) {
    tooltip = document.createElement('span');
    tooltip.id = tooltipId;
    tooltip.className = 'tooltip-icon';
    tooltip.textContent = 'ⓘ';
    container.appendChild(tooltip);
  }

  if (content) {
    tooltip.title = content;
  }

  return tooltip;
}

// ==============================
// CHART FUNCTIONS
// ==============================

function initializeCharts() {
  if (!state.history) return;
  if (typeof Chart === 'undefined') {
    console.warn('Chart.js not loaded');
    return;
  }

  const slicedHistoryData = sliceHistory(state.currentRangeHours, state.history);
  if (!slicedHistoryData) return;

  initializeHashrateChart(slicedHistoryData);
  initializePriceChart(slicedHistoryData);
}

function initializeHashrateChart(slicedHistoryData) {
  const canvas = DOM.hashrateChart;
  if (!canvas) return;

  try {
    if (!state.hashrateChart) {
      state.hashrateChart = new Chart(canvas, {
        type: 'line',
        data: {
          labels: slicedHistoryData.labels,
          datasets: [{ label: 'Your Hashrate', data: slicedHistoryData.myHash }]
        },
        options: {
          scales: {
            x: {
              type: 'time',
              time: {
                tooltipFormat: 'dd/MM/yyyy HH:mm:ss',
                displayFormats: {
                  hour: 'dd/MM/yyyy HH:mm',
                  minute: 'dd/MM/yyyy HH:mm'
                }
              }
            },
            y: { ticks: { callback: scaleHashrate } }
          },
          elements: { point: { radius: 0 }, line: { tension: 0.25 } }
        }
      });
    } else {
      state.hashrateChart.data.labels = slicedHistoryData.labels;
      state.hashrateChart.data.datasets[0].data = slicedHistoryData.myHash;
      state.hashrateChart.update();
    }
  } catch (error) {
    console.error('Failed to initialize hashrate chart:', error);
  }
}

function initializePriceChart(slicedHistoryData) {
  const canvas = DOM.priceChart;
  if (!canvas) return;

  try {
    if (!state.priceChart) {
      state.priceChart = new Chart(canvas, {
        type: 'line',
        data: {
          labels: slicedHistoryData.labels,
          datasets: [{ label: 'XMR Price (EUR)', data: slicedHistoryData.price }]
        },
        options: {
          scales: {
            x: {
              type: 'time',
              time: {
                tooltipFormat: 'dd/MM/yyyy HH:mm:ss',
                displayFormats: {
                  hour: 'dd/MM/yyyy HH:mm',
                  minute: 'dd/MM/yyyy HH:mm'
                }
              }
            }
          },
          elements: { point: { radius: 0 }, line: { tension: 0.25 } }
        }
      });
    } else {
      state.priceChart.data.labels = slicedHistoryData.labels;
      state.priceChart.data.datasets[0].data = slicedHistoryData.price;
      state.priceChart.update();
    }
  } catch (error) {
    console.error('Failed to initialize price chart:', error);
  }
}

// ==============================
// PAYMENTS & SHARES FUNCTIONS
// ==============================

async function updateRecentPayments() {
  if (!state.observerBase || !state.observerWallet) {
    setTextContent('paymentsStatus', 'Observer not configured');
    setTextContent('totalEarned', '–');
    return [null, 0];
  }

  const tbody = DOM.paymentsTable?.querySelector('tbody');

  try {
    const payouts = await fetchJSON(`${state.observerBase}/payouts/${state.observerWallet}`);

    if (!Array.isArray(payouts) || payouts.length === 0) {
      setTextContent('paymentsStatus', 'No payouts yet');
      setTextContent('totalEarned', '0.000000 XMR');
      if (tbody) tbody.innerHTML = '';
      return [null, 0];
    }

    payouts.sort((a, b) => b.timestamp - a.timestamp);
    const newestPayoutTime = payouts[0].timestamp;

    const totalXMR = payouts.reduce((sum, p) => sum + (p.coinbase_reward / CONFIG.ATOMIC_UNITS_PER_XMR), 0);
    const priceEUR = state.history?.price?.[state.history.price.length - 1] || 0;

    setTextContent('totalEarned', `${totalXMR.toFixed(6)} XMR`);
    setTextContent('totalEurEarned', typeof priceEUR === 'number' ? `≈ €${(totalXMR * priceEUR).toFixed(2)}` : '');

    // Update table
    if (tbody) {
      tbody.innerHTML = payouts.slice(0, CONFIG.PPLNS_WINDOW_MAX_SHARES).map((p) => {
        const xmr = p.coinbase_reward / CONFIG.ATOMIC_UNITS_PER_XMR;
        const eurValue = typeof priceEUR === 'number' ? (xmr * priceEUR).toFixed(2) : '–';
        return `
          <tr>
            <td>${formatRelativeTime(p.timestamp)}</td>
            <td style="text-align: right">${xmr.toFixed(6)}</td>
            <td style="text-align: right">${eurValue}</td>
          </tr>
        `;
      }).join('');
    }

    setTextContent('paymentsStatus', `Showing last ${Math.min(CONFIG.PPLNS_WINDOW_MAX_SHARES, payouts.length)} payouts`);

    return [newestPayoutTime, totalXMR];
  } catch {
    setTextContent('paymentsStatus', 'Payout data unavailable');
    setTextContent('totalEarned', '–');
    if (tbody) tbody.innerHTML = '';
    return [null, 0];
  }
}

async function updateSharesCard() {
  if (!state.observerWallet || !state.observerBase) {
    updateSharesDisplay('–', '–', '–', '–');
    return;
  }

  try {
    const [payouts, shares] = await Promise.all([
      fetchJSON(`${state.observerBase}/payouts/${state.observerWallet}`),
      fetchJSON(`${state.observerBase}/shares?miner=${state.observerWallet}`)
    ]);

    if (!Array.isArray(payouts) || !Array.isArray(shares)) {
      throw new Error('Invalid response data');
    }

    const lastPayoutTS = payouts.length > 0 ? payouts[0].timestamp : 0;
    const sharesAfter = shares.filter((s) => s.timestamp > lastPayoutTS);

    const sharesSince = sharesAfter.length;
    const unclesSince = sharesAfter.filter((s) => s.inclusion === 0).length;
    const totalShares = shares.length;
    const totalUncles = shares.filter((s) => s.inclusion === 0).length;

    updateSharesDisplay(sharesSince, unclesSince, totalShares, totalUncles);
  } catch {
    updateSharesDisplay('–', '–', '–', '–');
  }
}

function updateSharesDisplay(sharesSince, unclesSince, totalShares, totalUncles) {
  setTextContent('sharesSinceLastPayout', sharesSince);
  setTextContent('unclesSinceLastPayout', unclesSince);
  setTextContent('totalSharesMined', `Total shares: ${totalShares}`);
  setTextContent('totalUnclesMined', `Total uncles: ${totalUncles}`);
}

// ==============================
// LUCK CALCULATION FUNCTIONS
// ==============================

async function updateWindowLuck(
  pplnsWeight,
  avgPoolHashPPLNS,
  avgMyHashPPLNS,
  windowStart,
  windowDuration,
  priceEUR,
  blockReward
) {
  if (!state.observerWallet || !state.observerBase) return;

  try {
    const shares = await fetchJSON(`${state.observerBase}/shares?miner=${state.observerWallet}`);
    if (!Array.isArray(shares)) throw new Error('Invalid shares data');

    shares.sort((a, b) => a.timestamp - b.timestamp);

    const myWindowShares = shares.filter((share) => share.timestamp >= windowStart);
    const totalDifficulty = myWindowShares.reduce((sum, share) => sum + (share.difficulty || 0), 0);
    const difficultyShare = pplnsWeight > 0 ? totalDifficulty / pplnsWeight : 0;

    const myWindowHash = windowDuration > 0 ? totalDifficulty / windowDuration : 0;
    const luckFactor = avgMyHashPPLNS > 0 ? myWindowHash / avgMyHashPPLNS : 0;

    const poolInfo = await fetchJSON(`${state.observerBase}/pool_info`);
    const foundBlocks = await fetchJSON(`${state.observerBase}/found_blocks?limit=1`);

    const avgCurrentEffort = poolInfo?.sidechain?.effort?.average200 || 100;
    const betterLuckFactor = avgCurrentEffort > 0 ? luckFactor * (1 / (avgCurrentEffort / 100)) : luckFactor;

    const accumulatedXMR = difficultyShare * blockReward;
    const accumulatedEUR = accumulatedXMR * priceEUR;

    createTooltipIfNeeded('luckFactor', 'luckTooltip', `
Luck factor is based on your performance in the
current PPLNS window compared to your expected performance.
Basically it is your hashrate calculated based on the summed
difficulty of your hashes compared to the total difficulty in the
current PPLNS window divided by your actual moving average
(as long as the PPLNS window age) hashrate from XMRig. And that
also multiplied by the pool luck (derived from the pool effort).
    `.trim());

    setTextContent('luckFactor', betterLuckFactor.toFixed(2));
    setTextContent('xmrThisWindow', accumulatedXMR.toFixed(12));
    setTextContent('eurThisWindow', `≈ €${accumulatedEUR.toFixed(2)}`);
    setTextContent('dayHash', scaleHashrate(myWindowHash));
    setTextContent('pplnsStart', formatDate24Hours(new Date(windowStart * 1000)));
    setTextContent('currentEffort', (poolInfo?.sidechain?.effort?.current || 0).toFixed(2));
  } catch (error) {
    console.error('Error updating PPLNS Window Luck card:', error);
  }
}

async function updateTrueLuck(
  startedMiningTimestamp,
  newestPayoutTime,
  xmrPerDayAvg,
  totalXMR
) {
  try {
    createTooltipIfNeeded('trueLuckFactor', 'trueLuckTooltip', `
The estimated true luck factor is based on how much you have
been paid out since you started mining divided by how much you
were expected to earn in that time. But the longer the time window,
the less accurate it is, because the way the expected amount is
calculated is using (max 24h) moving average hashrates. But if
they have changed in a significant way from when you started
mining up until now, the true luck factor could be less accurate.
That's also why it is called the estimated true luck factor.
    `.trim());

    if (!isPositiveNumber(newestPayoutTime) || !isPositiveNumber(startedMiningTimestamp)) {
      throw new Error('Invalid timestamps');
    }

    const timeWindow = newestPayoutTime - startedMiningTimestamp;
    if (timeWindow <= 0) {
      throw new Error('Invalid time window');
    }

    const timeWindowDays = timeWindow / CONFIG.SECONDS_PER_DAY;
    const expectedXMR = xmrPerDayAvg * timeWindowDays;

    if (expectedXMR <= 0) {
      throw new Error('Invalid expected XMR');
    }

    const trueLuckFactor = totalXMR / expectedXMR;
    setTextContent('trueLuckFactor', trueLuckFactor.toFixed(2));
  } catch (error) {
    console.error('Error updating Estimated True Luck card:', error);
    setTextContent('trueLuckFactor', '–');
  }
}

// ==============================
// OLD DASHBOARD STATS (REFACTORED)
// ==============================

function updatePoolStatus() {
  const poolStatus = DOM['pool-status'];
  const poolStatusText = DOM['pool-status-text'];

  if (!poolStatus || !poolStatusText) return;

  const isActive = state.oldStatsData?.connections > 0;
  poolStatus.className = `status-indicator status-${isActive ? 'active' : 'inactive'}`;
  poolStatusText.textContent = isActive ? 'Active' : 'Inactive';
}

function updateHashrate24hDisplay() {
  const el = DOM['user-hashrate-24h'];
  if (!el) return;

  const hashrate = state.oldStatsData?.hashrate_24h;
  el.textContent = isPositiveNumber(hashrate) ? scaleHashrate(hashrate) : '–';
}

function updateSharesDisplayOld() {
  setTextContent('shares-found', state.oldStatsData?.shares_found ?? '–');
  setTextContent('shares-failed', state.oldStatsData?.shares_failed ?? '–');
}

function updateConnectionsDisplay() {
  setTextContent('connections', state.oldStatsData?.connections ?? '–');
}

function updateRewardShareDisplay() {
  const el = DOM['reward-share'];
  if (!el) return;

  const share = state.oldStatsData?.block_reward_share_percent;
  el.textContent = isPositiveNumber(share) ? `${share.toFixed(3)}%` : '–';
}

function updateBlockInfo(poolData) {
  const blocksFound = poolData?.pool_statistics?.totalBlocksFound;
  setTextContent('blocks-found', blocksFound ?? '–');
  setTextContent('last-block-time', formatTime(poolData?.pool_statistics?.lastBlockFoundTime));
}

function updateShareTimes() {
  setTextContent('last-share-time', formatTime(state.oldStatsData?.last_share_found_time));
}

function updateWorkersList() {
  const workersList = DOM['workers-list'];
  if (!workersList) return;

  const workers = state.oldStatsData?.workers;
  if (!Array.isArray(workers) || workers.length === 0) {
    workersList.innerHTML = '<div class="no-workers">No miners connected</div>';
    return;
  }

  workersList.innerHTML = workers.map((workerStr) => {
    const parts = workerStr.split(',');
    const ipPort = parts[0] || 'Unknown';
    const hashrate = safeParseInt(parts[1], 0);
    const name = parts[4]?.trim() && parts[4] !== 'x' ? parts[4] : `Miner @ ${ipPort.split(':')[0]}`;

    return `
      <div class="worker-item" style="margin: 5px 0; padding: 10px; background: rgba(255,255,255,0.1); border-radius: 5px;">
        <strong>${name}</strong> - ${scaleHashrate(hashrate)} - Shares: ${state.oldStatsData?.shares_found ?? 0}/${state.oldStatsData?.shares_failed ?? 0}
      </div>
    `;
  }).join('');
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
    fetchJSON('/xmrig_summary'),
    fetchJSON('/pool/stats'),
    fetchJSON('/network/stats'),
    fetchJSON('/min_payment_threshold'),
    fetchJSON('/stats_log.json'),
    fetch('/local/stratum').then((r) => (r.ok ? r.json() : {})).catch(() => ({}))
  ]);

  return results.map((r) => (r.status === 'fulfilled' ? r.value : null));
}

function calculateAveragingWindow(historyData) {
  if (!isValidObject(historyData) || !Array.isArray(historyData.timestamps) || historyData.timestamps.length === 0) {
    return { avgWindowHours: 0, hasEnoughData: false };
  }

  const now = Date.now() / 1000;
  const earliest = historyData.timestamps[0];
  const availableHours = (now - earliest) / CONFIG.SECONDS_PER_HOUR;

  const avgWindowHours = Math.max(0, Math.min(availableHours, CONFIG.DEFAULT_RANGE_HOURS));

  return { avgWindowHours, hasEnoughData: avgWindowHours > 0 };
}

function calculateEarnings(avgMyHash, avgNetHash, blockReward, priceEUR) {
  const myNetShareAvg = avgNetHash > 0 ? avgMyHash / avgNetHash : 0;
  const xmrPerDayAvg = myNetShareAvg * CONFIG.BLOCKS_PER_DAY * blockReward;

  const period = DOM.earnPeriod?.value || 'day';
  const multiplier = CONFIG.PERIOD_MULTIPLIERS[period] || 1;

  const xmr = xmrPerDayAvg * multiplier;
  const eur = xmr * priceEUR;

  return { xmr, eur, xmrPerDayAvg };
}

function calculatePayoutInterval(avgMyHash, avgPoolHash, blockReward, minPaymentThreshold) {
  const xmrPerBlock = avgPoolHash > 0 ? (avgMyHash / avgPoolHash) * blockReward : 0;

  if (xmrPerBlock <= 0) return { intervalHours: null, intervalText: 'N/A' };

  const intervalHours = (minPaymentThreshold / xmrPerBlock) * 24;
  return { intervalHours, intervalText: `~${intervalHours.toFixed(1)}h/payout` };
}

function updateEarningsTooltip(avgMyHash, avgPoolHash, avgNetHash, avgWindowHours) {
  const avgWindowLabel = avgWindowHours >= CONFIG.DEFAULT_RANGE_HOURS
    ? '24h moving average'
    : `${avgWindowHours.toFixed(1)}h moving average`;

  createTooltipIfNeeded('earnXMR', 'earnTooltip', `
Estimated earnings based on ${avgWindowLabel}.
Avg your hashrate: ${scaleHashrate(avgMyHash)}
Avg pool hashrate: ${scaleHashrate(avgPoolHash)}
Avg network hashrate: ${scaleHashrate(avgNetHash)}
  `.trim());
}

function updateStatsDisplay(instMyHash, instPoolHash, instNetHash, blockReward, poolShare, priceEUR, earnings, payoutInfo, avgWindowHours) {
  setTextContent('myHashrate', scaleHashrate(instMyHash));
  setTextContent('poolHashrate', scaleHashrate(instPoolHash));
  setTextContent('netHashrate', scaleHashrate(instNetHash));
  setTextContent('blockReward', blockReward.toFixed(6));
  setTextContent('poolShare', poolShare > 0 ? `${poolShare.toFixed(4)}%` : '–');
  setTextContent('price', `€${priceEUR.toFixed(2)}`);
  setTextContent('earnXMR', `${earnings.xmr.toFixed(6)} XMR`);
  setTextContent('earnEUR', `≈ €${earnings.eur.toFixed(2)}`);

  const legendText = avgWindowHours >= CONFIG.DEFAULT_RANGE_HOURS
    ? 'Based on 24h moving average'
    : `Based on ${avgWindowHours.toFixed(1)}h moving average`;
  setTextContent('earnLegend', legendText);

  setTextContent('lastRefreshed', `Last refreshed: ${formatDate24(new Date())}`);
  setTextContent('payoutInterval', payoutInfo.intervalText);

  // Update payout tooltip
  const tooltipIcon = document.querySelector('.bottom-stats .tooltip-icon');
  if (tooltipIcon && payoutInfo.intervalHours !== null) {
    tooltipIcon.title = `Average payout interval: ~${payoutInfo.intervalHours.toFixed(1)} hours\nYour actual payouts can be shorter or longer, depending on mining luck.`;
  }
}

async function updateStats() {
  if (!state.isTabVisible) return;

  try {
    const [xmrigData, poolData, networkData, thresholdObj, hist, oldStats] = await fetchDashboardData();

    state.history = hist;
    state.oldStatsData = oldStats || {};

    if (state.history) {
      initializeCharts();
    }

    // Extract instantaneous values
    const instMyHash = extractXMRigHashrate(xmrigData);
    const instPoolHash = extractPoolHashrate(poolData);
    const instNetHash = calculateNetworkHashrate(networkData);
    const blockReward = isValidObject(networkData) && isPositiveNumber(networkData.reward)
      ? networkData.reward / CONFIG.ATOMIC_UNITS_PER_XMR
      : 0;
    const minPaymentThreshold = thresholdObj?.minPaymentThreshold || CONFIG.DEFAULT_MIN_PAYMENT;

    // Calculate averaging window
    const { avgWindowHours, hasEnoughData } = calculateAveragingWindow(state.history);

    // Calculate moving averages
    const { avgMyHash, avgPoolHash, avgNetHash } = hasEnoughData
      ? calculateMovingAverages(avgWindowHours, state.history, instMyHash, instPoolHash, instNetHash)
      : { avgMyHash: instMyHash, avgPoolHash: instPoolHash, avgNetHash: instNetHash };

    // Calculate pool share
    const poolShare = instPoolHash > 0 ? (instMyHash / instPoolHash) * 100 : 0;

    // Get current price
    const priceEUR = state.history?.price?.[state.history.price.length - 1] || 0;

    // Calculate earnings
    const earnings = calculateEarnings(avgMyHash, avgNetHash, blockReward, priceEUR);

    // Calculate payout interval
    const payoutInfo = calculatePayoutInterval(avgMyHash, avgPoolHash, blockReward, minPaymentThreshold);

    // Update all displays
    updateStatsDisplay(instMyHash, instPoolHash, instNetHash, blockReward, poolShare, priceEUR, earnings, payoutInfo, avgWindowHours);
    updateEarningsTooltip(avgMyHash, avgPoolHash, avgNetHash, avgWindowHours);

    // Update payments
    const [newestPayoutTime, totalXMR] = await updateRecentPayments();

    // Update shares
    await updateSharesCard();

    // Calculate PPLNS window data
    const pplnsWeight = poolData?.pool_statistics?.pplnsWeight || poolData?.pool_statistics?.pplns_weight || 0;
    const windowStart = await getWindowStartTimestamp();
    const windowEnd = Date.now() / 1000;
    const windowDuration = windowEnd - windowStart;

    // Calculate PPLNS moving averages
    const pplnsWindowHours = windowDuration / CONFIG.SECONDS_PER_HOUR;
    const { avgMyHash: avgMyHashPPLNS, avgPoolHash: avgPoolHashPPLNS } = calculateMovingAverages(
      Math.min(pplnsWindowHours, avgWindowHours || pplnsWindowHours),
      state.history,
      instMyHash,
      instPoolHash,
      instNetHash
    );

    // Update luck cards
    await updateWindowLuck(pplnsWeight, avgPoolHashPPLNS, avgMyHashPPLNS, windowStart, windowDuration, priceEUR, blockReward);

    const startedMining = DOM.startedMining?.value;
    if (startedMining) {
      const startedMiningTimestamp = Math.floor(new Date(startedMining).getTime() / 1000);
      if (!Number.isNaN(startedMiningTimestamp)) {
        await updateTrueLuck(startedMiningTimestamp, newestPayoutTime, earnings.xmrPerDayAvg, totalXMR);
      }
    }

    // Update old dashboard stats
    updateOldDashboardStats(poolData);

  } catch (error) {
    console.error('Error in updateStats:', error);
    handleStatsError();
  }
}

function handleStatsError() {
  const errorElements = ['myHashrate', 'poolHashrate', 'netHashrate', 'price', 'earnXMR', 'earnEUR'];
  errorElements.forEach((id) => {
    const el = DOM[id];
    if (el?.textContent === 'Loading…') {
      el.textContent = 'Error';
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
    state.history = await fetchJSON('/stats_log.json');
  } catch {
    state.history = null;
  }

  await loadObserverConfig();

  // Setup event listeners
  document.addEventListener('visibilitychange', handleVisibilityChange);
  window.addEventListener('beforeunload', cleanup);

  if (DOM.earnPeriod) {
    DOM.earnPeriod.addEventListener('change', handleEarnPeriodChange);
  }

  // Initialize charts and stats
  initializeCharts();
  await updateStats();

  // Start periodic updates
  state.refreshIntervalId = setInterval(updateStats, CONFIG.REFRESH_INTERVAL_MS);
}

// Start the application
initialize();
