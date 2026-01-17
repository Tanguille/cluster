// ==============================
// GLOBAL VARIABLES
// ==============================
let history; // Holds historical mining and price data
let hashrateChart; // Chart.js instance for the user's hashrate chart
let priceChart; // Chart.js instance for the XMR price chart
let currentRangeHours = 24; // Default time range for charts (24 hours)
let observerConfig = null;
let observerBase = null;
let observerWallet = null;
const dayInSeconds = 60 * 60 * 24;

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
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)} GH/s`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)} MH/s`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} kH/s`;
  return `${Math.round(v)} H/s`;
}

// Format date in DD/MM/YYYY HH:MM:SS
function formatDate24(date) {
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0"); // month is 0-indexed
  const year = date.getFullYear();
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${day}/${month}/${year} ${hours}:${minutes}:${seconds}`;
}

function formatDate24Hours(date) {
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
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
  const r = await fetch(url);
  if (!r.ok) throw new Error(url);
  return r.json();
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
  const idx = hist.timestamps.findIndex((t) => t >= cutoff);
  const i = idx === -1 ? 0 : idx;

  return {
    labels: hist.timestamps.slice(i).map((t) => t * 1000), // JS timestamps in ms
    myHash: hist.myHash.slice(i),
    poolHash: hist.poolHash.slice(i),
    netHash: hist.netHash.slice(i),
    price: hist.price.slice(i),
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
    let sum = 0,
      count = 0;
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

async function getWindowStartTimestamp() {
  // Step 1: Fetch the newest share to get the current PPLNS window depth
  const newestShare = await fetchJSON(`${observerBase}/shares?limit=1`);

  if (!newestShare || newestShare.length === 0) {
    console.warn("No shares returned from API");
    return Date.now() / 1000;
  }

  const windowDepth = newestShare[0].window_depth; // number of shares in the current PPLNS window

  // Step 2: Fetch exactly that many recent shares
  const sharesInWindow = await fetchJSON(
    `${observerBase}/shares?limit=${windowDepth}`,
  );

  if (!sharesInWindow || sharesInWindow.length === 0) {
    console.warn("No shares returned for PPLNS window");
    return Date.now() / 1000;
  }

  // Step 3: Oldest share in this array is the start of the PPLNS window
  const windowStartShare = sharesInWindow[sharesInWindow.length - 1];

  // Step 4: Return its timestamp in seconds
  return Math.floor(windowStartShare.timestamp);
}

// ==============================
// CHART INITIALIZATION & UPDATES
// ==============================
async function updateRecentPayments() {
  if (!observerBase || !observerWallet) {
    document.getElementById("paymentsStatus").textContent =
      "Observer not configured";
    document.getElementById("totalEarned").textContent = "–";
    return;
  }
  const statusEl = document.getElementById("paymentsStatus");
  const totalEl = document.getElementById("totalEarned");
  const totalEurEl = document.getElementById("totalEurEarned");
  const tbody = document.querySelector("#paymentsTable tbody");

  try {
    const payouts = await fetchJSON(
      `${observerBase}/payouts/${observerWallet}`,
    );

    if (!Array.isArray(payouts) || payouts.length === 0) {
      statusEl.textContent = "No payouts yet";
      totalEl.textContent = "0.000000 XMR";
      tbody.innerHTML = "";
      return;
    }

    // Sort newest first
    payouts.sort((a, b) => b.timestamp - a.timestamp);

    const newestPayoutTime = payouts[0].timestamp;

    // Lifetime total
    let totalXMR = 0;
    for (const p of payouts) {
      totalXMR += p.coinbase_reward / 1e12;
    }

    const priceEUR = history.price.at(-1) || 0;

    if (typeof priceEUR === "number") {
      totalEl.textContent = `${totalXMR.toFixed(6)} XMR`;
      totalEurEl.textContent = `≈ €${(totalXMR * priceEUR).toFixed(2)}`;
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

    return [newestPayoutTime, totalXMR];
  } catch (e) {
    console.error("Observer payouts error:", e);
    statusEl.textContent = "Payout data unavailable";
    totalEl.textContent = "–";
    tbody.innerHTML = "";
    return null;
  }
}

async function updateSharesCard() {
  try {
    if (!observerWallet || !observerBase) return null; // fallback

    // Fetch payouts & shares
    const [payouts, shares] = await Promise.all([
      fetchJSON(`${observerBase}/payouts/${observerWallet}`),
      fetchJSON(`${observerBase}/shares?miner=${observerWallet}`),
    ]);

    // Determine last payout timestamp
    const lastPayoutTS = payouts?.length ? payouts[0].timestamp : 0;

    // Filter shares after last payout
    const sharesAfter = shares.filter((s) => s.timestamp > lastPayoutTS);

    // Counts
    const sharesSince = sharesAfter.length;
    const unclesSince = sharesAfter.filter((s) => s.inclusion === 0).length;

    const totalShares = shares.length;
    const totalUncles = shares.filter((s) => s.inclusion === 0).length;

    // Update DOM
    document.getElementById("sharesSinceLastPayout").textContent = sharesSince;
    document.getElementById("unclesSinceLastPayout").textContent = unclesSince;

    document.getElementById("totalSharesMined").textContent =
      `Total shares: ${totalShares}`;
    document.getElementById("totalUnclesMined").textContent =
      `Total uncles: ${totalUncles}`;
  } catch (e) {
    console.error("Error updating Shares & Uncles card:", e);
    // fallback display
    document.getElementById("sharesSinceLastPayout").textContent = "–";
    document.getElementById("unclesSinceLastPayout").textContent = "–";
    document.getElementById("totalSharesMined").textContent = "–";
    document.getElementById("totalUnclesMined").textContent = "–";
    return null;
  }
}

async function updateWindowLuck(
  pplnsWeight,
  avgPoolHashPPLNS,
  avgMyHashPPLNS,
  windowStart,
  windowDuration,
  priceEUR,
  blockReward,
) {
  try {
    if (!observerWallet || !observerBase) return null;

    // Step 1: Get all shares (most recent first)
    const shares = (
      await fetchJSON(`${observerBase}/shares?miner=${observerWallet}`)
    ).sort((a, b) => a.timestamp - b.timestamp); // oldest → newest

    // Step 3: Filter your shares to only those in the current PPLNS window
    const myWindowShares = shares.filter(
      (share) => share.timestamp >= windowStart,
    );

    // Step 4: Sum difficulty of your shares
    const totalDifficulty = myWindowShares.reduce(
      (sum, share) => sum + share.difficulty,
      0,
    );

    // Step 5: Your share of total window
    const difficultyShare = totalDifficulty / pplnsWeight;

    // Step 5: Compute your window hashrate and luck factor
    const myWindowHash = totalDifficulty / windowDuration;
    const luckFactor = myWindowHash / avgMyHashPPLNS;

    // Get current pool effort
    const poolInfo = await fetchJSON(`${observerBase}/pool_info`);
    const lastBlockTimestamp = (
      await fetchJSON(`${observerBase}/found_blocks?limit=1`)
    )[0].main_block.timestamp;
    const currentEffort = poolInfo.sidechain.effort.current;
    const avgCurrentEffort = poolInfo.sidechain.effort.average200;

    const betterLuckFactor = luckFactor * (1 / (avgCurrentEffort / 100));

    // Get extra info
    const accumulatedXMR = difficultyShare * blockReward;
    const accumulatedEUR = accumulatedXMR * priceEUR;
    const pplnsStart = new Date(windowStart * 1000);

    // Ensure tooltip exists
    const luckFactorDiv = document.getElementById("luckFactor");
    let luckTooltip = document.getElementById("luckTooltip");
    if (!luckTooltip) {
      luckTooltip = document.createElement("span");
      luckTooltip.id = "luckTooltip";
      luckTooltip.className = "tooltip-icon";
      luckTooltip.textContent = "ⓘ";
      luckFactorDiv.appendChild(luckTooltip);
    }

    // Luck factor tooltip
    luckTooltip.title = `
Luck factor is based on your performance in the
current PPLNS window compared to your expected performance.
Basically it is your hashrate calculated based on the summed
difficulty of your hashes compared to the total difficuly in the
current PPLNS window divided by your actual moving average
(as long as the PPLNS window age) hashrate from XMRig. And that
also multiplied by the pool luck (derrived from the pool effort).
`;

    document.getElementById("luckFactor").textContent =
      betterLuckFactor.toFixed(2);
    document.getElementById("xmrThisWindow").textContent =
      accumulatedXMR.toFixed(12);
    document.getElementById("eurThisWindow").textContent =
      `≈ €${accumulatedEUR.toFixed(2)}`;
    document.getElementById("dayHash").textContent =
      scaleHashrate(myWindowHash);
    document.getElementById("pplnsStart").textContent =
      formatDate24Hours(pplnsStart);
    document.getElementById("currentEffort").textContent =
      currentEffort.toFixed(2);
  } catch (e) {
    console.error("Error updating PPLNS Window Luck card:", e);
    return null;
  }
}

async function updateTrueLuck(
  startedMiningTimestamp,
  newestPayoutTime,
  xmrPerDayAvg,
  totalXMR,
) {
  try {
    const luckFactorDiv = document.getElementById("trueLuckFactor");
    let luckTooltip = document.getElementById("trueLuckTooltip");
    if (!luckTooltip) {
      luckTooltip = document.createElement("span");
      luckTooltip.id = "trueLuckTooltip";
      luckTooltip.className = "tooltip-icon";
      luckTooltip.textContent = "ⓘ";
      luckFactorDiv.appendChild(trueLuckTooltip);
    }

    // Luck factor tooltip
    luckTooltip.title = `
The estimated true luck factor is based on how much you have
been paid out since you started mining divided by how much you
were expected to earn in that time. But the longer the time window,
the less accurate it is, because the way the expected amount is
calculated is using (max 24h) moving average hashrates. But if
they have changed in a significant way from when you started
mining up until now, the true luck factor could be less accurate.
That's also why it is called the estimated true luck factor.
`;
    // Calculate time window
    const timeWindow = newestPayoutTime - startedMiningTimestamp;
    if (timeWindow <= 0 || Number.isNaN(startedMiningTimestamp)) {
      throw new Error(
        "Start of mining was after last payment or there has not been a payment yet",
      );
    }

    // Calculate expected amount of XMR in the time window
    const timeWindowDays = timeWindow / dayInSeconds;
    const expectedXMR = xmrPerDayAvg * timeWindowDays;

    // Calculate estimated true luck factor
    const trueLuckFactor = totalXMR / expectedXMR;
    document.getElementById("trueLuckFactor").textContent =
      trueLuckFactor.toFixed(2);
  } catch (e) {
    console.error("Error updating Estimated True Luck card:", e);
    return null;
  }
}

function updateCharts() {
  if (!history) return;
  const d = sliceHistory(currentRangeHours, history);

  // --- HASHRATE CHART ---
  if (!hashrateChart) {
    hashrateChart = new Chart(document.getElementById("hashrateChart"), {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [{ label: "Your Hashrate", data: d.myHash }],
      },
      options: {
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "dd/MM/yyyy HH:mm:ss", // tooltip format on hover
              displayFormats: {
                hour: "dd/MM/yyyy HH:mm", // x-axis label when zoomed out
                minute: "dd/MM/yyyy HH:mm",
              },
            },
          },
          y: { ticks: { callback: scaleHashrate } },
        },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } },
      },
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
      data: {
        labels: d.labels,
        datasets: [{ label: "XMR Price (EUR)", data: d.price }],
      },
      options: {
        scales: {
          x: {
            type: "time",
            time: {
              tooltipFormat: "dd/MM/yyyy HH:mm:ss", // tooltip on hover
              displayFormats: {
                hour: "dd/MM/yyyy HH:mm", // x-axis label when zoomed out
                minute: "dd/MM/yyyy HH:mm",
              },
            },
          },
        },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } },
      },
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
// Old dashboard data (from /local/stratum)
let oldStatsData = {};

// Format time helper from old dashboard
function formatTime(timestamp) {
  if (!timestamp || timestamp === 0) return "Never";
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
}

// Update old dashboard stats
function updateOldDashboardStats(poolData) {
  // Pool Status
  const poolStatus = document.getElementById("pool-status");
  const poolStatusText = document.getElementById("pool-status-text");
  if (poolStatus && poolStatusText && oldStatsData) {
    if (oldStatsData.connections > 0) {
      poolStatus.className = "status-indicator status-active";
      poolStatusText.textContent = "Active";
    } else {
      poolStatus.className = "status-indicator status-inactive";
      poolStatusText.textContent = "Inactive";
    }
  }

  // Your Hashrate (24h)
  const userHashrate24hEl = document.getElementById("user-hashrate-24h");
  if (userHashrate24hEl && oldStatsData) {
    userHashrate24hEl.textContent = oldStatsData.hashrate_24h
      ? scaleHashrate(oldStatsData.hashrate_24h)
      : "–";
  }

  // Shares Found/Failed
  const sharesFoundEl = document.getElementById("shares-found");
  if (sharesFoundEl)
    sharesFoundEl.textContent = oldStatsData.shares_found || "–";

  const sharesFailedEl = document.getElementById("shares-failed");
  if (sharesFailedEl)
    sharesFailedEl.textContent = oldStatsData.shares_failed || "–";

  // Active Connections
  const connectionsEl = document.getElementById("connections");
  if (connectionsEl)
    connectionsEl.textContent = oldStatsData.connections || "–";

  // Block Reward Share
  const rewardShareEl = document.getElementById("reward-share");
  if (rewardShareEl) {
    rewardShareEl.textContent = oldStatsData.block_reward_share_percent
      ? `${oldStatsData.block_reward_share_percent.toFixed(3)}%`
      : "–";
  }

  // Blocks Found
  const blocksFoundEl = document.getElementById("blocks-found");
  if (blocksFoundEl && poolData) {
    blocksFoundEl.textContent =
      poolData.pool_statistics?.totalBlocksFound || "–";
  }

  // Last Share/Block Found
  const lastShareTimeEl = document.getElementById("last-share-time");
  if (lastShareTimeEl && oldStatsData) {
    lastShareTimeEl.textContent = formatTime(
      oldStatsData.last_share_found_time,
    );
  }

  const lastBlockTimeEl = document.getElementById("last-block-time");
  if (lastBlockTimeEl && poolData) {
    lastBlockTimeEl.textContent = formatTime(
      poolData.pool_statistics?.lastBlockFoundTime,
    );
  }

  // Connected Miners
  const workersList = document.getElementById("workers-list");
  if (workersList && oldStatsData.workers && oldStatsData.workers.length > 0) {
    const workersHtml = oldStatsData.workers
      .map((workerStr) => {
        const parts = workerStr.split(",");
        const ipPort = parts[0] || "Unknown";
        const hashrate = parseInt(parts[1]) || 0;
        const totalHashes = parseInt(parts[2]) || 0;
        const port = parts[3] || "";
        let name = parts[4] || ipPort;

        if (!name || name === "x" || name.trim() === "") {
          const ip = ipPort.split(":")[0];
          name = `Miner @ ${ip}`;
        }

        return `<div class="worker-item" style="margin: 5px 0; padding: 10px; background: rgba(255,255,255,0.1); border-radius: 5px;">
                          <strong>${name}</strong> - ${scaleHashrate(hashrate)} - Shares: ${oldStatsData.shares_found || 0}/${oldStatsData.shares_failed || 0}
                      </div>`;
      })
      .join("");
    workersList.innerHTML = workersHtml;
  } else if (workersList) {
    workersList.innerHTML = '<div class="no-workers">No miners connected</div>';
  }
}

async function updateStats() {
  // Fetch all required data in parallel with individual error handling
  const [xmrig, poolData, network, thresholdObj, hist, oldStats] =
    await Promise.allSettled([
      fetchJSON("/xmrig_summary").catch((e) => {
        console.error("Failed to fetch xmrig_summary:", e);
        return null;
      }),
      fetchJSON("/pool/stats").catch((e) => {
        console.error("Failed to fetch pool/stats:", e);
        return null;
      }),
      fetchJSON("/network/stats").catch((e) => {
        console.error("Failed to fetch network/stats:", e);
        return null;
      }),
      fetchJSON("/min_payment_threshold").catch((e) => {
        console.error("Failed to fetch min_payment_threshold:", e);
        return { minPaymentThreshold: 0.01 };
      }),
      fetchJSON("/stats_log.json").catch((e) => {
        console.error("Failed to fetch stats_log.json:", e);
        return null;
      }),
      fetch("/local/stratum")
        .then((r) => (r.ok ? r.json() : {}))
        .catch(() => ({})),
    ]).then((results) =>
      results.map((r) => (r.status === "fulfilled" ? r.value : r.reason)),
    );

  // Extract values from Promise.allSettled results
  const xmrigData = xmrig;
  const networkData = network;
  const threshold = thresholdObj;
  history = hist;

  // Update charts if we have history
  if (history) {
    updateCharts();
  }

  // --- INSTANTANEOUS VALUES with fallbacks ---
  const instMyHash =
    xmrigData?.hashrate?.total?.[0] || xmrigData?.hashrate?.total || 0;
  const instPoolHash =
    poolData?.pool_statistics?.hashRate || poolData?.pool_statistics?.hashrate || 0;
  const instNetHash = networkData?.difficulty
    ? networkData.difficulty / 120
    : 0; // approx network hashrate
  const blockReward = networkData?.reward ? networkData.reward / 1e12 : 0;
  const minPaymentThreshold = threshold?.minPaymentThreshold || 0.01;

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
    avgMyHash = movingAverage(
      sliced.labels.map((t) => t / 1000),
      sliced.myHash,
      avgWindowHours * 3600,
    );
    avgPoolHash = movingAverage(
      sliced.labels.map((t) => t / 1000),
      sliced.poolHash,
      avgWindowHours * 3600,
    );
    avgNetHash = movingAverage(
      sliced.labels.map((t) => t / 1000),
      sliced.netHash,
      avgWindowHours * 3600,
    );
  }

  // --- UPDATE DOM ELEMENTS ---
  document.getElementById("myHashrate").textContent =
    scaleHashrate(instMyHash);
  document.getElementById("poolHashrate").textContent =
    scaleHashrate(instPoolHash);
  document.getElementById("netHashrate").textContent =
    scaleHashrate(instNetHash);
  document.getElementById("blockReward").textContent = blockReward.toFixed(6);

  // Pool share percentage
  const poolShare =
    instPoolHash > 0 ? (instMyHash / instPoolHash) * 100 : 0;
  document.getElementById("poolShare").textContent =
    poolShare > 0 ? `${poolShare.toFixed(4)}%` : "–";

  // Latest XMR price
  const priceEUR = history.price.at(-1) || 0;
  document.getElementById("price").textContent = `€${priceEUR.toFixed(2)}`;

  // --- ESTIMATED EARNINGS ---
  const blocksPerDay = 720;
  const myNetShareAvg =
    avgNetHash > 0 ? avgMyHash / avgNetHash : 0;
  const xmrPerDayAvg = myNetShareAvg * blocksPerDay * blockReward;
  const period = document.getElementById("earnPeriod").value;
  const xmr = xmrPerDayAvg * PERIOD_MULT[period];
  const eur = xmr * priceEUR;

  // Update #earnXMR while preserving tooltip
  const earnXMRDiv = document.getElementById("earnXMR");
  earnXMRDiv.textContent = `${xmr.toFixed(6)} XMR`;

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
  const avgWindowLabel =
    avgWindowHours >= 24
      ? "24h moving average"
      : `${avgWindowHours.toFixed(1)}h moving average`;
  earnTooltip.title = `Estimated earnings based on ${avgWindowLabel}.
Avg your hashrate: ${scaleHashrate(avgMyHash)}
Avg pool hashrate: ${scaleHashrate(avgPoolHash)}
Avg network hashrate: ${scaleHashrate(avgNetHash)}`;

  // Earnings legend text
  const legendText =
    avgWindowHours >= 24
      ? "Based on 24h moving average"
      : `Based on ${avgWindowHours.toFixed(1)}h moving average`;
  document.getElementById("earnLegend").textContent = legendText;

  // Last refreshed timestamp
  const date = new Date();
  document.getElementById("lastRefreshed").textContent =
    `Last refreshed: ${formatDate24(date)}`;

  // --- PAYOUT INTERVAL CALCULATION (adjusted for pool size) ---
  const poolBlocksPerDay =
    avgNetHash > 0
      ? blocksPerDay * (avgPoolHash / avgNetHash)
      : 0; // expected pool blocks per day
  const xmrPerBlock =
    avgPoolHash > 0 ? (avgMyHash / avgPoolHash) * blockReward : 0; // your expected XMR per pool block

  // Interval in hours
  const intervalHours =
    xmrPerBlock > 0 ? (minPaymentThreshold / xmrPerBlock) * 24 : "N/A";

  const intervalText = `~${intervalHours.toFixed(1)}h/payout`;
  document.getElementById("payoutInterval").textContent = intervalText;

  const tooltipIcon = document.querySelector(".bottom-stats .tooltip-icon");
  if (tooltipIcon) {
    tooltipIcon.title = `Average payout interval: ~${intervalHours.toFixed(1)} hours
Your actual payouts can be shorter or longer, depending on mining luck.`;
  }

  // Update dashboard to show recent payments
  const [newestPayoutTime, totalXMR] = await updateRecentPayments();

  // Update dashboard to show recent shares
  updateSharesCard();

  // Calculate moving average hashrates since start of PPLNS Window
  const pplnsWeight =
    poolData?.pool_statistics?.pplnsWeight || poolData?.pool_statistics?.pplns_weight || 0;
  const windowStart = await getWindowStartTimestamp();
  const windowEnd = Date.now() / 1000; // current timestamp in seconds
  const windowDuration = windowEnd - windowStart; // seconds
  let avgWindowSeconds = windowDuration; // start with actual PPLNS window
  if (history && history.timestamps.length > 0) {
    const earliest = history.timestamps[0] / 1000; // convert ms → s
    const availableSeconds = windowEnd - earliest;
    avgWindowSeconds = Math.min(avgWindowSeconds, availableSeconds);
  }
  let avgMyHashPPLNS = instMyHash;
  let avgPoolHashPPLNS = instPoolHash;

  if (avgWindowSeconds > 0 && history && history.timestamps.length > 0) {
    const sliced = sliceHistory(avgWindowSeconds / 3600, history); // sliceHistory expects hours
    // compute 10-minute (600s) moving average
    avgMyHashPPLNS = movingAverage(
      sliced.labels.map((t) => t / 1000), // timestamps in seconds
      sliced.myHash,
      avgWindowSeconds,
    );
    avgPoolHashPPLNS = movingAverage(
      sliced.labels.map((t) => t / 1000),
      sliced.poolHash,
      avgWindowSeconds,
    );
  }

  // Update window Luck card
  updateWindowLuck(
    pplnsWeight,
    avgPoolHashPPLNS,
    avgMyHashPPLNS,
    windowStart,
    windowDuration,
    priceEUR,
    blockReward,
  );

  // Update True Luck Card
  const startedMining = document.getElementById("startedMining").value;
  const startedMiningTimestamp = Math.floor(
    new Date(startedMining).getTime() / 1000,
  );
  updateTrueLuck(
    startedMiningTimestamp,
    newestPayoutTime,
    xmrPerDayAvg,
    totalXMR,
  );

  // Update old dashboard stats
  updateOldDashboardStats(poolData);
} catch (e) {
  console.error("Error in updateStats:", e);
  // Update UI with error indicators instead of leaving "Loading..."
  const errorElements = [
    "myHashrate",
    "poolHashrate",
    "netHashrate",
    "price",
    "earnXMR",
    "earnEUR",
  ];
  errorElements.forEach((id) => {
    const el = document.getElementById(id);
    if (el && el.textContent === "Loading…") {
      el.textContent = "Error";
    }
  });
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
