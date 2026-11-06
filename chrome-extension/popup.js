const DEFAULT_TERMS = [
  "business intelligence",
  "tableau",
  "power bi",
  "data engineer",
  "analytics engineer",
  "AI engineer",
  "machine learning",
  "data scientist",
  "data analyst",
  "machine learning engineer",
  "generative AI"
].join("\n");
const termsInput = document.getElementById("terms");
const status = document.getElementById("status");
const details = document.getElementById("details");
const searchButton = document.getElementById("searchButton");
const stopButton = document.getElementById("stopButton");
const connStatus = document.getElementById("connStatus");
document.getElementById("extensionVersion").textContent = `Extension version ${chrome.runtime.getManifest().version}`;

async function checkLocalBackend() {
  try {
    const response = await fetch("http://127.0.0.1:8765/api/status", { cache: "no-store" });
    if (response.ok) {
      connStatus.textContent = "Server Online";
      connStatus.className = "conn-pill online";
      return true;
    }
  } catch {
    // Server offline
  }
  connStatus.textContent = "Server Offline";
  connStatus.className = "conn-pill offline";
  return false;
}

function renderSummary(summary) {
  if (!summary) return;
  status.textContent = `Last run: ${summary.inserted} new, ${summary.updated} updated, ${summary.skipped} unchanged, ${summary.rejected} rejected.`;
  details.hidden = false;
  details.textContent = (summary.completedTerms || []).map(item => {
    const firstError = item.failures?.[0]?.error;
    const missingFields = item.extractionSkipped
      ? ` (title: ${item.missingTitle || 0} missing, URL: ${item.missingUrl || 0} missing; company: ${item.missingCompany || 0}, location: ${item.missingLocation || 0})`
      : "";
    return `${item.term}: ${item.pages || 1} pages, ${item.detected} cards, ${item.extractionSkipped} incomplete${missingFields}, ${item.inserted} new, ${item.updated} updated, ${item.skipped} unchanged, ${item.rejected} rejected${firstError ? `\n  ${firstError}` : ""}`;
  }).join("\n");
}

chrome.storage.local.get({ seekSearchTerms: DEFAULT_TERMS, seekBatchSummary: null, seekBatchRuntimeError: "" }, values => {
  termsInput.value = values.seekSearchTerms;
  if (values.seekBatchRuntimeError) status.textContent = values.seekBatchRuntimeError;
  if (values.seekBatchSummary) renderSummary(values.seekBatchSummary);
});

function showActiveBatch() {
  chrome.storage.session.get(null, values => {
    const active = Object.entries(values).find(([key, value]) => key.startsWith("seekBatch_") && value?.terms);
    if (!active) {
      stopButton.hidden = true;
      chrome.storage.local.get({ seekBatchRuntimeError: "", seekBatchSummary: null }, localValues => {
        if (localValues.seekBatchRuntimeError) {
          status.textContent = localValues.seekBatchRuntimeError;
        } else if (localValues.seekBatchSummary) {
          renderSummary(localValues.seekBatchSummary);
        }
      });
      return;
    }
    const [sessionKey, batch] = active;
    const tabId = Number(sessionKey.replace("seekBatch_", ""));
    if (!Number.isNaN(tabId)) {
      chrome.tabs.get(tabId, tab => {
        if (chrome.runtime.lastError || !tab) {
          chrome.storage.session.remove(sessionKey);
          stopButton.hidden = true;
          return;
        }
        stopButton.hidden = false;
        const term = batch.terms[batch.index] || "search";
        const progress = batch.phase === "saving jobs"
          ? `saving job ${batch.jobsProcessed || 0} of ${batch.jobsTotal || 0}`
          : (batch.phase || (batch.processing ? "reading result cards" : "loading SEEK results"));
        status.textContent = `Run in progress: ${term} (${batch.index + 1} of ${batch.terms.length}, page ${(batch.termPages || 0) + 1}) — ${progress}.`;
      });
    }
  });
}

checkLocalBackend();
showActiveBatch();
setInterval(() => {
  checkLocalBackend();
  showActiveBatch();
}, 1500);

stopButton.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "CAREER_AGENT_STOP_SEEK_BATCH" }, () => {
    stopButton.hidden = true;
    status.textContent = "Stopped active SEEK search batch.";
  });
});

searchButton.addEventListener("click", async () => {
  const terms = [...new Set(termsInput.value.split(/\r?\n/).map(term => term.trim()).filter(Boolean))];
  if (!terms.length) {
    status.textContent = "Enter at least one search term.";
    return;
  }
  if (terms.length > 12) {
    status.textContent = "Use no more than 12 terms per run.";
    return;
  }
  if (!document.getElementById("termsConsent").checked) {
    status.textContent = "Confirm the usage note before starting the search run.";
    return;
  }

  chrome.storage.local.set({ seekSearchTerms: terms.join("\n") });
  searchButton.disabled = true;
  status.textContent = `Starting ${terms.length} Australia-wide searches...`;
  chrome.runtime.sendMessage({ type: "CAREER_AGENT_START_SEEK_BATCH", terms }, response => {
    searchButton.disabled = false;
    if (chrome.runtime.lastError) {
      status.textContent = "Could not start the search batch.";
      return;
    }
    status.textContent = response?.ok
      ? `Started search 1 of ${terms.length}. Keep the SEEK tab open while it runs.`
      : (response?.error || "Could not start the search batch.");
  });
});

document.getElementById("appButton").addEventListener("click", () => {
  chrome.tabs.create({ url: "http://127.0.0.1:8765/" });
});
