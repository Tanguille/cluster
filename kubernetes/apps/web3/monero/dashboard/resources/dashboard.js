let history = { timestamps: [], myHash: [], price: [] };
let hashrateChart, priceChart;
let currentRangeHours = 24;
const STORAGE_KEY = 'p2pool_history';
const MAX_HISTORY_AGE = 7 * 24 * 3600 * 1000; // 7 days in milliseconds

const PERIOD_MULT = { hour: 1 / 24, day: 1, week: 7, month: 30, year: 365 };

function scaleHashrate(hashrateValue) {
  if (hashrateValue >= 1e9) return `${(hashrateValue / 1e9).toFixed(2)} GH/s`;
  if (hashrateValue >= 1e6) return `${(hashrateValue / 1e6).toFixed(2)} MH/s`;
  if (hashrateValue >= 1e3) return `${(hashrateValue / 1e3).toFixed(2)} kH/s`;
  return `${Math.round(hashrateValue)} H/s`;
}

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

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url);
  return response.json();
}

// Load history from localStorage
function loadHistory() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      history = JSON.parse(stored);
      // Clean old data
      const cutoff = Date.now() - MAX_HISTORY_AGE;
      const idx = history.timestamps.findIndex(t => t >= cutoff / 1000);
      if (idx > 0) {
        history.timestamps = history.timestamps.slice(idx);
        history.myHash = history.myHash.slice(idx);
        history.price = history.price.slice(idx);
      }
    }
  } catch (error) {
    console.warn('Failed to load history:', error);
    history = { timestamps: [], myHash: [], price: [] };
  }
}

// Save history to localStorage
function saveHistory() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch (error) {
    console.warn('Failed to save history:', error);
  }
}

function sliceHistory(hours) {
  const cutoff = Date.now() / 1000 - hours * 3600;
  const idx = history.timestamps.findIndex(timestamp => timestamp >= cutoff);
  const startIndex = idx === -1 ? 0 : idx;
  return {
    labels: history.timestamps.slice(startIndex).map(timestamp => timestamp * 1000),
    myHash: history.myHash.slice(startIndex),
    price: history.price.slice(startIndex)
  };
}

function updateCharts() {
  const historyData = sliceHistory(currentRangeHours);

  // HASHRATE CHART
  if (!hashrateChart) {
    hashrateChart = new Chart(document.getElementById("hashrateChart"), {
      type: "line",
      data: {
        labels: historyData.labels,
        datasets: [{ label: "Your Hashrate", data: historyData.myHash }]
      },
      options: {
        scales: {
          x: { type: "time" },
          y: { ticks: { callback: scaleHashrate } }
        },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } }
      }
    });
  } else {
    hashrateChart.data.labels = historyData.labels;
    hashrateChart.data.datasets[0].data = historyData.myHash;
    hashrateChart.update();
  }

  // PRICE CHART
  if (!priceChart) {
    priceChart = new Chart(document.getElementById("priceChart"), {
      type: "line",
      data: {
        labels: historyData.labels,
        datasets: [{ label: "XMR Price (EUR)", data: historyData.price }]
      },
      options: {
        scales: { x: { type: "time" } },
        elements: { point: { radius: 0 }, line: { tension: 0.25 } }
      }
    });
  } else {
    priceChart.data.labels = historyData.labels;
    priceChart.data.datasets[0].data = historyData.price;
    priceChart.update();
  }
}

// Fetch XMR price from multiple APIs
async function getXmrPrice() {
  // Try multiple APIs with fallback
  const apis = [
    "https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=eur",
    "https://api.kraken.com/0/public/Ticker?pair=XMREUR",
    "https://api.price2sheet.com/json/xmr/eur"
  ];

  for (const api of apis) {
    try {
      const response = await fetch(api);
      if (!response.ok) continue;

      const data = await response.json();
      let price = 0;

      if (api.includes('coingecko')) {
        price = data.monero.eur;
      } else if (api.includes('kraken')) {
        price = parseFloat(data.result.XXMRZEUR.c[0]);
      } else if (api.includes('price2sheet')) {
        price = data.price;
      }

      if (price > 0) return price;
    } catch (error) {
      continue;
    }
  }
  return 0;
}

async function updateStats() {
  try {
    // Fetch all data
    const [stratumData, pool, network] = await Promise.all([
      fetchJSON("/local/stratum"),
      fetchJSON("/pool/stats"),
      fetchJSON("/network/stats")
    ]);

    const myHash = stratumData.hashrate_15m || stratumData.hashrate_1m || 0;
    const poolHash = pool.pool_statistics.hashRate;
    const netHash = network.difficulty / 120;
    const blockReward = network.reward / 1e12;

    document.getElementById("myHashrate").textContent = scaleHashrate(myHash);
    document.getElementById("poolHashrate").textContent = scaleHashrate(poolHash);
    document.getElementById("netHashrate").textContent = scaleHashrate(netHash);
    document.getElementById("blockReward").textContent = blockReward.toFixed(6);

    // Update mining statistics
    document.getElementById("user-hashrate-24h").textContent = scaleHashrate(stratumData.hashrate_24h || 0);
    document.getElementById("shares-found").textContent = stratumData.shares_found || 0;
    document.getElementById("shares-failed").textContent = stratumData.shares_failed || 0;
    document.getElementById("connections").textContent = stratumData.connections || 0;

    document.getElementById("reward-share").textContent = stratumData.block_reward_share_percent ?
      stratumData.block_reward_share_percent.toFixed(3) + "%" : "0.000%";

    document.getElementById("current-effort").textContent = stratumData.current_effort ?
      stratumData.current_effort.toFixed(3) + "%" : "0.000%";

    document.getElementById("blocks-found").textContent = pool.totalBlocksFound || 0;

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
    document.getElementById("last-share-time").textContent = formatTime(stratumData.last_share_found_time);
    document.getElementById("last-block-time").textContent = formatTime(pool.lastBlockFoundTime);

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
      workersList.innerHTML = '<div class="no-workers">No miners connected</div>';
    }

    const poolShare = (myHash / poolHash) * 100;
    document.getElementById("poolShare").textContent = poolShare.toFixed(4) + "%";

    // Get price and store in history
    const price = await getXmrPrice();
    document.getElementById("price").textContent = `€${price.toFixed(2)}`;

    // Store historical data
    const now = Date.now() / 1000;
    history.timestamps.push(now);
    history.myHash.push(myHash);
    history.price.push(price);
    saveHistory();

    // Update charts
    updateCharts();

    // Earnings
    const blocksPerDay = 720;
    const myNetShare = myHash / netHash;
    const xmrPerDay = myNetShare * blocksPerDay * blockReward;
    const period = document.getElementById("earnPeriod").value;
    const xmr = xmrPerDay * PERIOD_MULT[period];
    document.getElementById("earnXMR").textContent = `${xmr.toFixed(6)} XMR`;
    document.getElementById("earnEUR").textContent = `≈ €${(xmr * price).toFixed(2)}`;

    // Payout interval
    const moneroBlockTime = 120;
    const fracPool = myHash / poolHash;
    const etaSeconds = moneroBlockTime / (fracPool || 1);
    const hoursPayout = Math.floor(etaSeconds / 3600);
    const minutesPayout = Math.floor((etaSeconds % 3600) / 60);
    const secondsPayout = Math.floor(etaSeconds % 60);
    document.getElementById("payoutInterval").textContent = `${hoursPayout}h ${minutesPayout}m ${secondsPayout}s`;

  } catch (error) {
    console.error("Error fetching stats:", error);
  }
}

// Dropdown for earnings
document.getElementById("earnPeriod").onchange = updateStats;

(async () => {
  loadHistory();
  updateCharts();
  updateStats();
  setInterval(updateStats, 30000); // Update every 30 seconds instead of 5
})();
