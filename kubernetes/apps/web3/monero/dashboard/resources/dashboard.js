// ==============================
// GLOBAL VARIABLES
// ==============================
let history;             // Holds historical mining and price data
let hashrateChart;       // Chart.js instance for the user's hashrate chart
let priceChart;          // Chart.js instance for the XMR price chart
let currentRangeHours = 24; // Default time range for charts (24 hours)
let observerConfig = null;
let observerBase = null;
let observerWallet = null;

// Conversion multipliers for earnings periods
const PERIOD_MULT = { hour: 1 / 24, day: 1, week: 7, month: 30, year: 365 };

// ==============================
// P2POOL OBSERVER CONFIG
// ==============================
const MAX_RECENT_PAYMENTS = 5;

// ==============================
// HELPER FUNCTIONS
// ==============================

/**
 * Converts a hashrate value to a human-readable string with units
 * @param {number} v - hashrate in H/s
 * @returns {string} formatted hashrate
 */
function scaleHashrate(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(2) + " GH/s";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + " MH/s";
  if (v >= 1e3) return (v / 1e3).toFixed(2) + " kH/s";
  return Math.round(v) + " H/s";
}

// Format date in DD/MM/YYYY HH:MM:SS
function formatDate24(date) {
  const d = String(date.getDate()).padStart(2, '0');
  const m = String(date.getMonth() + 1).padStart(2, '0'); // month is 0-indexed
  const y = date.getFullYear();
  const h = String(date.getHours()).padStart(2, '0');
  const min = String(date.getMinutes()).padStart(2, '0');
  const s = String(date.getSeconds()).padStart(2, '0');
  return `${d}/${m}/${y} ${h}:${min}:${s}`;
}

// Find out relative time compared to timestamp
function formatRelativeTime(ts) {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/**
 * Fetch JSON data from a URL, throwing an error if request fails
 * @param {string} url - endpoint to fetch
 * @returns {Promise<Object>} JSON response
 */
async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url);
  return response.json();
}

async function loadObserverConfig() {
  try {
    const cfg = await fetchJSON("/observer_config");
    if (!cfg.wallet || !cfg.observers || cfg.observers.length === 0) {
      return null;
    }
    observerConfig = cfg;
    observerWallet = cfg.wallet;
    observerBase = cfg.observers[0]; // default to first enabled observer
    return cfg;
  } catch (e) {
    console.warn("Observer config unavailable");
    return null;
  }
}

/**
 * Slice historical data to only include the last X hours
 * @param {number} hours - number of hours to include
 * @param {Object} hist - history object
 * @returns {Object} sliced history with labels and datasets
 */
function sliceHistory(hours, hist) {
  const now = Date.now() / 1000;
  const cutoff = now - hours * 3600;
  const idx = hist.timestamps.findIndex(t => t >= cutoff);
  const i = idx === -1 ? 0 : idx;

  return {
    labels: hist.timestamps.slice(i).map(t => t * 1000), // JS timestamps in ms
    myHash: hist.myHash.slice(i),
    poolHash: hist.poolHash.slice(i),
    netHash: hist.netHash.slice(i),
    price: hist.price.slice(i)
  };
}

/**
 * Compute a moving average over a given time window
 * @param {number[]} timestamps - array of timestamps in seconds
 * @param {number[]} values - array of corresponding values
 * @param {number} windowSeconds - window size in seconds
 * @returns {number[]} smoothed values array
 */
function movingAverage(timestamps, values, windowSeconds = 600) {
  if (!timestamps || !values || timestamps.length === 0) return 0;

  let smoothed = [];
  for (let i = 0; i < values.length; i++) {
    const start = timestamps[i] - windowSeconds;
    let sum = 0, count = 0;
    for (let j = 0; j <= i; j++) {
      if (timestamps[j] >= start) {
        sum += values[j];
        count++;
      }
    }
    smoothed.push(count ? sum / count : values[i]);
  }
  return smoothed.at(-1); // return the latest smoothed value
}

// ==============================
// CHART INITIALIZATION & UPDATES
// ==============================
async function updateRecentPayments() {
  if (!observerBase || !observerWallet) {
    document.getElementById("paymentsStatus").textContent = "Observer not configured";
    document.getElementById("totalEarned").textContent = "–";
    return;
  }
  const statusEl = document.getElementById("paymentsStatus");
  const totalEl = document.getElementById("totalEarned");
  const tbody = document.querySelector("#paymentsTable tbody");

  try {
    const payouts = await fetchJSON(
      `${observerBase}/payouts/${observerWallet}`
    );

    if (!Array.isArray(payouts) || payouts.length === 0) {
      statusEl.textContent = "No payouts yet";
      totalEl.textContent = "0.000000 XMR";
      tbody.innerHTML = "";
      return;
    }

    // Sort newest first
    payouts.sort((a, b) => b.timestamp - a.timestamp);

    // Lifetime total
    let totalXMR = 0;
    for (const p of payouts) {
      totalXMR += p.coinbase_reward / 1e12;
    }

    const priceEUR = history.price.at(-1) || 0;

    if (typeof priceEUR === "number") {
      totalEl.textContent =
        `${totalXMR.toFixed(6)} XMR (€${(totalXMR * priceEUR).toFixed(2)})`;
    } else {
      totalEl.textContent = `${totalXMR.toFixed(6)} XMR`;
    }

    // Recent payouts table
    tbody.innerHTML = "";
    for (const p of payouts.slice(0, MAX_RECENT_PAYMENTS)) {
      const tr = document.createElement("tr");

      const timeTd = document.createElement("td");
      timeTd.textContent = formatRelativeTime(p.timestamp);

      const xmr = p.coinbase_reward / 1e12;

      const amtTd = document.createElement("td");
      amtTd.style.textAlign = "right";
      amtTd.textContent = xmr.toFixed(6);

      const eurTd = document.createElement("td");
      eurTd.style.textAlign = "right";

      if (typeof priceEUR === "number") {
        eurTd.textContent = (xmr * priceEUR).toFixed(2);
      } else {
        eurTd.textContent = "–";
      }

      tr.appendChild(timeTd);
      tr.appendChild(amtTd);
      tr.appendChild(eurTd);
      tbody.appendChild(tr);
    }

    statusEl.textContent = `Showing last ${Math.min(MAX_RECENT_PAYMENTS, payouts.length)} payouts`;

  } catch (e) {
    console.error("Observer payouts error:", e);
    statusEl.textContent = "Payout data unavailable";
    totalEl.textContent = "–";
    tbody.innerHTML = "";
  }
}

async function updateSharesCard() {
  try {
    if (!observerWallet || !observerBase) return; // fallback

    // Fetch payouts & shares
    const [payouts, shares] = await Promise.all([
      fetchJSON(`${observerBase}/payouts/${observerWallet}`),
      fetchJSON(`${observerBase}/shares?miner=${observerWallet}`)
    ]);

    // Determine last payout timestamp
    const lastPayoutTS = payouts?.length ? payouts[0].timestamp : 0;

    // Filter shares after last payout
    const sharesAfter = shares.filter(s => s.timestamp > lastPayoutTS);

    // Counts
    const sharesSince = sharesAfter.length;
    const unclesSince = sharesAfter.filter(s => s.inclusion === 0).length;

    const totalShares = shares.length;
    const totalUncles = shares.filter(s => s.inclusion === 0).length;

    // Update DOM
    document.getElementById("sharesSinceLastPayout").textContent = sharesSince;
    document.getElementById("unclesSinceLastPayout").textContent = unclesSince;

    document.getElementById("totalSharesMined").textContent = `Total shares: ${totalShares}`;
    document.getElementById("totalUnclesMined").textContent = `Total uncles: ${totalUncles}`;

  } catch (e) {
    console.error("Error updating Shares & Uncles card:", e);
    // fallback display
    document.getElementById("sharesSinceLastPayout").textContent = "–";
    document.getElementById("unclesSinceLastPayout").textContent = "–";
    document.getElementById("totalSharesMined").textContent = "–";
    document.getElementById("totalUnclesMined").textContent = "–";
  }
}


function updateCharts() {
  if (!history) return;
  const d = sliceHistory(currentRangeHours, history);

  // --- HASHRATE CHART ---
  if (!hashrateChart) {
    hashrateChart = new Chart(document.getElementById("hashrateChart"), {
      type: "line",
      data: { labels: d.labels, datasets: [{ label: "Your Hashrate", data: d.myHash }] },
      options: {
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "dd/MM/yyyy HH:mm:ss", // tooltip format on hover
              displayFormats: {
                hour: "dd/MM/yyyy HH:mm",   // x-axis label when zoomed out
                minute: "dd/MM/yyyy HH:mm"
              }
            }
          },
          y: { ticks: { callback: scaleHashrate } }
        },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } }
      }

    });
  } else {
    hashrateChart.data.labels = d.labels;
    hashrateChart.data.datasets[0].data = d.myHash;
    hashrateChart.update();
  }

  // --- PRICE CHART ---
  if (!priceChart) {
    priceChart = new Chart(document.getElementById("priceChart"), {
      type: "line",
      data: { labels: d.labels, datasets: [{ label: "XMR Price (EUR)", data: d.price }] },
      options: {
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "dd/MM/yyyy HH:mm:ss", // tooltip on hover
              displayFormats: {
                hour: "dd/MM/yyyy HH:mm",  // x-axis label when zoomed out
                minute: "dd/MM/yyyy HH:mm"
              }
            }
          }
        },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } }
      }
    });
  } else {
    priceChart.data.labels = d.labels;
    priceChart.data.datasets[0].data = d.price;
    priceChart.update();
  }
}

// ==============================
// UPDATE DASHBOARD STATISTICS
// ==============================
async function updateStats() {
  try {
    // Fetch all required data in parallel
    const [stratumData, xmrig, pool, network, thresholdObj, hist] = await Promise.all([
      fetchJSON("/local/stratum"),
      fetchJSON("/xmrig_summary"),
      fetchJSON("/pool/stats"),
      fetchJSON("/network/stats"),
      fetchJSON("/min_payment_threshold"),
      fetchJSON("/stats_log.json")
    ]);

    history = hist;
    updateCharts();

    // --- INSTANTANEOUS VALUES ---
    const instMyHash = xmrig.hashrate.total[0];
    const instPoolHash = pool.pool_statistics.hashRate;
    const instNetHash = network.difficulty / 120; // approx network hashrate
    const blockReward = network.reward / 1e12;
    const minPaymentThreshold = thresholdObj.minPaymentThreshold;

    // Determine averaging window (max 24h)
    const now = Date.now() / 1000;
    let avgWindowHours = 24;
    if (history && history.timestamps.length > 0) {
      const earliest = history.timestamps[0];
      const availableHours = (now - earliest) / 3600;
      if (availableHours < 24) avgWindowHours = availableHours;
      if (avgWindowHours <= 0) avgWindowHours = 0;
    }

    // Compute moving averages over 10-minute intervals
    let avgMyHash = instMyHash;
    let avgPoolHash = instPoolHash;
    let avgNetHash = instNetHash;
    if (avgWindowHours > 0) {
      const sliced = sliceHistory(avgWindowHours, history);
      avgMyHash = movingAverage(sliced.labels.map(t => t / 1000), sliced.myHash, 600);
      avgPoolHash = movingAverage(sliced.labels.map(t => t / 1000), sliced.poolHash, 600);
      avgNetHash = movingAverage(sliced.labels.map(t => t / 1000), sliced.netHash, 600);
    }

    // --- UPDATE DOM ELEMENTS ---
    document.getElementById("myHashrate").textContent = scaleHashrate(instMyHash);
    document.getElementById("poolHashrate").textContent = scaleHashrate(instPoolHash);
    document.getElementById("netHashrate").textContent = scaleHashrate(instNetHash);
    document.getElementById("blockReward").textContent = blockReward.toFixed(6);

    // Update mining statistics
    document.getElementById("user-hashrate-24h").textContent = scaleHashrate(
      stratumData.hashrate_24h || 0,
    );
    document.getElementById("shares-found").textContent =
      stratumData.shares_found || 0;
    document.getElementById("shares-failed").textContent =
      stratumData.shares_failed || 0;
    document.getElementById("connections").textContent =
      stratumData.connections || 0;

    document.getElementById("reward-share").textContent =
      stratumData.block_reward_share_percent
        ? `${stratumData.block_reward_share_percent.toFixed(3)}%`
        : "0.000%";

    document.getElementById("current-effort").textContent =
      stratumData.current_effort
        ? `${stratumData.current_effort.toFixed(3)}%`
        : "0.000%";

    document.getElementById("blocks-found").textContent =
      pool.totalBlocksFound || 0;

    // Update status indicator
    const poolStatus = document.getElementById("pool-status");
    const poolStatusText = document.getElementById("pool-status-text");
    if (stratumData.connections > 0) {
      poolStatus.className = "status-indicator status-active";
      poolStatusText.textContent = "Active";
    } else {
      poolStatus.className = "status-indicator status-inactive";
      poolStatusText.textContent = "Inactive";
    }

    // Update timestamps
    document.getElementById("last-share-time").textContent = formatTime(
      stratumData.last_share_found_time,
    );
    document.getElementById("last-block-time").textContent = formatTime(
      pool.lastBlockFoundTime,
    );

    // Update worker list
    const workersList = document.getElementById("workers-list");
    if (stratumData.workers && stratumData.workers.length > 0) {
      const workersHtml = stratumData.workers
        .map((workerStr) => {
          // Parse p2pool worker format: "ip:port,hashrate,total_hashes,port,name"
          const parts = workerStr.split(",");
          const ipPort = parts[0] || "Unknown";
          const hashrate = parseInt(parts[1]) || 0;
          let name = parts[4] || ipPort;

          // Improve miner name - if it's just "x" or empty, use IP address
          if (!name || name === "x" || name.trim() === "") {
            const ip = ipPort.split(":")[0];
            name = `Miner @ ${ip}`;
          }

          return `<div class="worker-item">
                        <strong>${name}</strong> -
                        ${scaleHashrate(hashrate)}
                    </div>`;
        })
        .join("");
      workersList.innerHTML = workersHtml;
    } else {
      workersList.innerHTML =
        '<div class="no-workers">No miners connected</div>';
    }

    // Pool share percentage
    const poolShare = (instMyHash / instPoolHash) * 100;
    document.getElementById("poolShare").textContent = poolShare.toFixed(4) + "%";

    // Latest XMR price
    const priceEUR = history.price.at(-1) || 0;
    document.getElementById("price").textContent = "€" + priceEUR.toFixed(2);

    // --- ESTIMATED EARNINGS ---
    const blocksPerDay = 720;
    const myNetShareAvg = avgMyHash / avgNetHash;
    const xmrPerDayAvg = myNetShareAvg * blocksPerDay * blockReward;
    const period = document.getElementById("earnPeriod").value;
    const xmr = xmrPerDayAvg * PERIOD_MULT[period];
    const eur = xmr * priceEUR;

    // Update #earnXMR while preserving tooltip
    const earnXMRDiv = document.getElementById("earnXMR");
    earnXMRDiv.textContent = xmr.toFixed(6) + " XMR";

    document.getElementById("earnEUR").textContent = `≈ €${eur.toFixed(2)}`;

    // Ensure tooltip exists
    let earnTooltip = document.getElementById("earnTooltip");
    if (!earnTooltip) {
      earnTooltip = document.createElement("span");
      earnTooltip.id = "earnTooltip";
      earnTooltip.className = "tooltip-icon";
      earnTooltip.textContent = "ⓘ";
      earnXMRDiv.appendChild(earnTooltip);
    }

    // Tooltip shows moving averages
    const avgWindowLabel = avgWindowHours >= 24 ? "24h moving average" : `${avgWindowHours.toFixed(1)}h moving average`;
    earnTooltip.title = `Estimated earnings based on ${avgWindowLabel}.
Avg your hashrate: ${scaleHashrate(avgMyHash)}
Avg pool hashrate: ${scaleHashrate(avgPoolHash)}
Avg network hashrate: ${scaleHashrate(avgNetHash)}`;

    // Earnings legend text
    const legendText = avgWindowHours >= 24 ? "Based on 24h moving average" : `Based on ${avgWindowHours.toFixed(1)}h moving average`;
    document.getElementById("earnLegend").textContent = legendText;

    // Last refreshed timestamp
    const date = new Date();
    document.getElementById("lastRefreshed").textContent = `Last refreshed: ${formatDate24(date)}`;

    // --- PAYOUT INTERVAL CALCULATION (adjusted for pool size) ---
    const poolBlocksPerDay = blocksPerDay * (avgPoolHash / avgNetHash); // expected pool blocks per day
    const xmrPerBlock = (avgMyHash / avgPoolHash) * blockReward; // your expected XMR per pool block

    // Expected total XMR per day
    const expectedXMRPerDay = poolBlocksPerDay * xmrPerBlock;

    // Average days per payout to reach minPaymentThreshold
    const avgDaysPerPayout = expectedXMRPerDay > 0 ? minPaymentThreshold / expectedXMRPerDay : Infinity;

    // Expected payouts per day
    const expectedPayoutsPerDay = isFinite(avgDaysPerPayout) && avgDaysPerPayout > 0 ? (1 / avgDaysPerPayout) : 0;

    // Interval in hours
    const intervalHours = isFinite(avgDaysPerPayout) ? (avgDaysPerPayout * 24).toFixed(1) : "N/A";
    const intervalText = `${expectedPayoutsPerDay.toFixed(2)} payouts/day (~${intervalHours}h/payout)`;
    document.getElementById("payoutInterval").textContent = intervalText;

    const tooltipIcon = document.querySelector(".bottom-stats .tooltip-icon");
    if (tooltipIcon) {
      tooltipIcon.title = `Average payout interval: ~${intervalHours} hours
Your actual payouts can be shorter or longer, depending on mining luck.`;
    }

    // Update dashboard to show recent payments
    updateRecentPayments();

    // Update dashboard to show recent shares
    updateSharesCard();

  } catch (e) {
    console.error("Error fetching stats:", e);
  }
}

// Update stats when user changes the earnings period dropdown
document.getElementById("earnPeriod").onchange = updateStats;

// ==============================
// INITIALIZATION
// Fetch historical data and start periodic updates
// ==============================
(async () => {
  try {
    history = await fetchJSON("/stats_log.json");
  } catch (e) {
    history = null;
  }

  await loadObserverConfig();

  updateCharts();
  updateStats();
  setInterval(updateStats, 5000); // refresh stats every 5 seconds
})();
