let statsData = {};
let networkData = {};
let poolData = {};

async function fetchData() {
  try {
    const [statsResponse, networkResponse, poolResponse] = await Promise.all([
      fetch("/local/stratum"),
      fetch("/network/stats"),
      fetch("/pool/stats"),
    ]);

    if (statsResponse.ok) statsData = await statsResponse.json();
    if (networkResponse.ok) networkData = await networkResponse.json();
    if (poolResponse.ok) poolData = await poolResponse.json();

    // Only update UI if we have at least some data
    if (statsData || networkData || poolData) {
      updateUI();
      const errorEl = document.getElementById("error");
      const loadingEl = document.getElementById("loading");
      const statsEl = document.getElementById("stats");

      if (errorEl) errorEl.style.display = "none";
      if (loadingEl) loadingEl.style.display = "none";
      if (statsEl) statsEl.style.display = "block";
    }
  } catch (error) {
    console.error("Error fetching data:", error);
    const errorEl = document.getElementById("error");
    if (errorEl) {
      errorEl.textContent = "Failed to load mining data. Retrying...";
      errorEl.style.display = "block";
    }
  }
}

function formatHashrate(hashrate) {
  if (hashrate >= 1000000) {
    return (hashrate / 1000000).toFixed(2) + " MH/s";
  } else if (hashrate >= 1000) {
    return (hashrate / 1000).toFixed(2) + " KH/s";
  } else {
    return hashrate.toFixed(0) + " H/s";
  }
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

function updateUI() {
  const lastUpdateEl = document.getElementById("last-update");
  if (lastUpdateEl) {
    lastUpdateEl.textContent = new Date().toLocaleTimeString();
  }

  const poolStatus = document.getElementById("pool-status");
  const poolStatusText = document.getElementById("pool-status-text");
  if (poolStatus && poolStatusText) {
    if (statsData.connections > 0) {
      poolStatus.className = "status-indicator status-active";
      poolStatusText.textContent = "Active";
    } else {
      poolStatus.className = "status-indicator status-inactive";
      poolStatusText.textContent = "Inactive";
    }
  }

  // Pool stats are nested under pool_statistics
  const poolStats = poolData.pool_statistics || {};
  const networkHashrateEl = document.getElementById("network-hashrate");
  if (networkHashrateEl) {
    networkHashrateEl.textContent = poolStats.hashRate
      ? formatHashrate(poolStats.hashRate)
      : "0 H/s";
  }

  const userHashrate24hEl = document.getElementById("user-hashrate-24h");
  if (userHashrate24hEl) {
    userHashrate24hEl.textContent = statsData.hashrate_24h
      ? formatHashrate(statsData.hashrate_24h)
      : "0 H/s";
  }

  const sharesFoundEl = document.getElementById("shares-found");
  if (sharesFoundEl) sharesFoundEl.textContent = statsData.shares_found || 0;

  const sharesFailedEl = document.getElementById("shares-failed");
  if (sharesFailedEl) sharesFailedEl.textContent = statsData.shares_failed || 0;

  const connectionsEl = document.getElementById("connections");
  if (connectionsEl) connectionsEl.textContent = statsData.connections || 0;

  const rewardShareEl = document.getElementById("reward-share");
  if (rewardShareEl) {
    rewardShareEl.textContent = statsData.block_reward_share_percent
      ? statsData.block_reward_share_percent.toFixed(3) + "%"
      : "0.000%";
  }

  const currentEffortEl = document.getElementById("current-effort");
  if (currentEffortEl) {
    currentEffortEl.textContent = statsData.current_effort
      ? statsData.current_effort.toFixed(3) + "%"
      : "0.000%";
  }

  const blocksFoundEl = document.getElementById("blocks-found");
  if (blocksFoundEl)
    blocksFoundEl.textContent = poolStats.totalBlocksFound || 0;

  const lastShareTimeEl = document.getElementById("last-share-time");
  if (lastShareTimeEl) {
    lastShareTimeEl.textContent = formatTime(statsData.last_share_found_time);
  }

  const lastBlockTimeEl = document.getElementById("last-block-time");
  if (lastBlockTimeEl) {
    lastBlockTimeEl.textContent = formatTime(poolStats.lastBlockFoundTime);
  }

  const workersList = document.getElementById("workers-list");
  if (statsData.workers && statsData.workers.length > 0) {
    const workersHtml = statsData.workers
      .map((workerStr) => {
        // Parse p2pool worker format: "ip:port,hashrate,total_hashes,port,name"
        const parts = workerStr.split(",");
        const ipPort = parts[0] || "Unknown";
        const hashrate = parseInt(parts[1]) || 0;
        const totalHashes = parseInt(parts[2]) || 0;
        const port = parts[3] || "";
        let name = parts[4] || ipPort;

        // Improve miner name - if it's just "x" or empty, use IP address
        if (!name || name === "x" || name.trim() === "") {
          const ip = ipPort.split(":")[0];
          name = `Miner @ ${ip}`;
        }

        return `<div class="worker-item" style="margin: 5px 0; padding: 10px; background: rgba(255,255,255,0.1); border-radius: 5px;">
                  <strong>${name}</strong> -
                  ${formatHashrate(hashrate)} -
                  Shares: ${statsData.shares_found || 0}/${statsData.shares_failed || 0}
              </div>`;
      })
      .join("");
    workersList.innerHTML = workersHtml;
  } else {
    workersList.innerHTML = '<div class="no-workers">No miners connected</div>';
  }
}

fetchData();
setInterval(fetchData, 30000);
