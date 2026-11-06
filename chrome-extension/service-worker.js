const SEEK_HOST = /^(?:[a-z0-9-]+\.)*seek\.com\.au$|^au\.seek\.com$/i;
const IMPORT_URL = "http://127.0.0.1:8765/api/jobs/import";
const BATCH_PREFIX = "seekBatch_";
const MAX_TERMS = 12;

function batchKey(tabId) {
  return `${BATCH_PREFIX}${tabId}`;
}

function makeSearchUrl(term) {
  const url = new URL("https://www.seek.com.au/jobs");
  url.searchParams.set("keywords", term);
  url.searchParams.set("where", "All Australia");
  return url.toString();
}

function isTrustedExtensionPage(sender) {
  return sender.id === chrome.runtime.id &&
    String(sender.url || "").startsWith(`chrome-extension://${chrome.runtime.id}/`);
}

async function clearStaleBatches() {
  const allSession = await chrome.storage.session.get(null);
  const keys = Object.keys(allSession).filter(k => k.startsWith(BATCH_PREFIX));
  if (keys.length) {
    await chrome.storage.session.remove(keys);
  }
}

async function startBatch(terms) {
  await clearStaleBatches();
  await chrome.storage.local.remove("seekBatchRuntimeError");
  let localResponse;
  try {
    localResponse = await fetch("http://127.0.0.1:8765/api/status", { cache: "no-store" });
  } catch {
    throw new Error("Career Agent is unreachable at http://127.0.0.1:8765. Start its local app first.");
  }
  if (!localResponse.ok) {
    throw new Error(`Career Agent returned HTTP ${localResponse.status} from /api/status.`);
  }

  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  const batch = {
    tabId: tab.id,
    terms,
    index: 0,
    phase: "loading search page",
    jobsTotal: 0,
    jobsProcessed: 0,
    processing: false,
    processedUrl: "",
    inserted: 0,
    updated: 0,
    skipped: 0,
    saved: 0,
    rejected: 0,
    pages: 0,
    termPages: 0,
    termDetected: 0,
    termExtractionSkipped: 0,
    termInserted: 0,
    termUpdated: 0,
    termSkipped: 0,
    termRejected: 0,
    termMissingCompany: 0,
    termMissingLocation: 0,
    termMissingTitle: 0,
    termMissingUrl: 0,
    termFailures: [],
    completedTerms: []
  };
  await chrome.storage.session.set({ [batchKey(tab.id)]: batch });
  await chrome.tabs.update(tab.id, { url: makeSearchUrl(terms[0]) });
  return tab.id;
}

async function prepareBatchPage(sender, sendResponse) {
  const tabId = sender.tab?.id;
  if (tabId == null || !SEEK_HOST.test(new URL(sender.url || "").hostname)) {
    sendResponse({ autoSave: false });
    return;
  }

  const key = batchKey(tabId);
  const stored = await chrome.storage.session.get(key);
  const batch = stored[key];
  if (!batch || batch.processing) {
    sendResponse({ autoSave: false });
    return;
  }

  if (batch.processedUrl === sender.url) {
    sendResponse({ autoSave: false });
    return;
  }

  batch.processing = true;
  batch.phase = "reading result cards";
  batch.jobsTotal = 0;
  batch.jobsProcessed = 0;
  batch.processedUrl = sender.url;
  await chrome.storage.session.set({ [key]: batch });
  sendResponse({ autoSave: true, term: batch.terms[batch.index] });
}

async function importOne(job) {
  let controller;
  let timeout;
  try {
    const request = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(job)
    };
    if (typeof AbortController === "function") {
      controller = new AbortController();
      request.signal = controller.signal;
    }
    const response = await Promise.race([
      fetch(IMPORT_URL, request),
      new Promise((_, reject) => {
        timeout = setTimeout(() => {
          controller?.abort();
          reject(new Error("Import request timed out after 15 seconds."));
        }, 15000);
      })
    ]);
    const result = await response.json();
    if (!response.ok) {
      return { rejected: 1, error: result.error || `Import API returned HTTP ${response.status}.` };
    }
    if (!["inserted", "updated", "skipped"].includes(result.action)) {
      return { rejected: 1, error: "Import API returned an unknown save outcome." };
    }
    return {
      inserted: result.action === "inserted" ? 1 : 0,
      updated: result.action === "updated" ? 1 : 0,
      skipped: result.action === "skipped" ? 1 : 0,
      rejected: 0
    };
  } catch (error) {
    return { saved: 0, rejected: 1, error: error.message || "Local Career Agent is unavailable." };
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function failBatch(tabId, error) {
  if (tabId != null) await chrome.storage.session.remove(batchKey(tabId));
  const detail = error?.message || String(error) || "Unknown extension error.";
  await chrome.storage.local.set({ seekBatchRuntimeError: `SEEK batch stopped: ${detail}` });
  if (tabId != null) await chrome.tabs.remove(tabId).catch(() => {});
}

async function processBatchResults(sender, message, sendResponse) {
  const tabId = sender.tab?.id;
  if (tabId == null || !SEEK_HOST.test(new URL(sender.url || "").hostname)) {
    sendResponse({ ok: false, error: "Batch results must come from SEEK Australia." });
    return;
  }

  const key = batchKey(tabId);
  const stored = await chrome.storage.session.get(key);
  const batch = stored[key];
  if (!batch || !batch.processing || message.term !== batch.terms[batch.index]) {
    sendResponse({ ok: false, error: "This search is no longer active." });
    return;
  }

  const jobs = Array.isArray(message.jobs) ? message.jobs : [];
  const term = batch.terms[batch.index];
  batch.phase = "saving jobs";
  batch.jobsTotal = jobs.length;
  batch.jobsProcessed = 0;
  await chrome.storage.session.set({ [key]: batch });
  let inserted = 0;
  let updated = 0;
  let skipped = 0;
  let rejected = 0;
  const failures = [];
  for (const [index, job] of jobs.entries()) {
    const outcome = await importOne({ ...job, search_term: term, allow_any_scope: true });
    inserted += outcome.inserted || 0;
    updated += outcome.updated || 0;
    skipped += outcome.skipped || 0;
    rejected += outcome.rejected;
    if (outcome.error && failures.length < 8) {
      failures.push({ title: job.title || "Untitled job", url: job.url || "", error: outcome.error });
    }
    batch.jobsProcessed = index + 1;
    await chrome.storage.session.set({ [key]: batch });
  }

  const detected = Number(message.detected) || jobs.length;
  const extractionSkipped = Number(message.extractionSkipped) || 0;
  const saved = inserted + updated;
  batch.inserted += inserted;
  batch.updated += updated;
  batch.skipped += skipped;
  batch.saved += saved;
  batch.rejected += rejected;
  batch.pages += 1;
  batch.termPages += 1;
  batch.termDetected += detected;
  batch.termExtractionSkipped += extractionSkipped;
  batch.termInserted += inserted;
  batch.termUpdated += updated;
  batch.termSkipped += skipped;
  batch.termRejected += rejected;
  batch.termMissingCompany += Number(message.missingCompany) || 0;
  batch.termMissingLocation += Number(message.missingLocation) || 0;
  batch.termMissingTitle += Number(message.missingTitle) || 0;
  batch.termMissingUrl += Number(message.missingUrl) || 0;
  batch.termFailures.push(...failures);

  const nextPageUrl = String(message.nextPageUrl || "");
  if (nextPageUrl && nextPageUrl !== sender.url && batch.termPages < 15) {
    batch.processing = false;
    batch.phase = "loading next results page";
    batch.processedUrl = "";
    await chrome.storage.session.set({ [key]: batch });
    await chrome.tabs.update(tabId, { url: nextPageUrl });
    sendResponse({
      ok: true,
      nextPage: true,
      term,
      page: batch.termPages,
      detected,
      extractionSkipped,
      loaded: jobs.length,
      inserted,
      updated,
      skipped,
      rejected,
      failures
    });
    return;
  }

  batch.completedTerms.push({
    term,
    pages: batch.termPages,
    detected: batch.termDetected,
    extractionSkipped: batch.termExtractionSkipped,
    inserted: batch.termInserted,
    updated: batch.termUpdated,
    skipped: batch.termSkipped,
    rejected: batch.termRejected,
    missingCompany: batch.termMissingCompany,
    missingLocation: batch.termMissingLocation,
    missingTitle: batch.termMissingTitle,
    missingUrl: batch.termMissingUrl,
    failures: batch.termFailures.slice(0, 8)
  });
  batch.index += 1;
  batch.termPages = 0;
  batch.termDetected = 0;
  batch.termExtractionSkipped = 0;
  batch.termInserted = 0;
  batch.termUpdated = 0;
  batch.termSkipped = 0;
  batch.termRejected = 0;
  batch.termMissingCompany = 0;
  batch.termMissingLocation = 0;
  batch.termMissingTitle = 0;
  batch.termMissingUrl = 0;
  batch.termFailures = [];

  if (batch.index < batch.terms.length) {
    batch.processing = false;
    batch.phase = "loading next search term";
    batch.processedUrl = "";
    await chrome.storage.session.set({ [key]: batch });
    await chrome.tabs.update(tabId, { url: makeSearchUrl(batch.terms[batch.index]) });
    sendResponse({
      ok: true,
      next: true,
      term,
      detected,
      extractionSkipped,
      loaded: jobs.length,
      inserted,
      updated,
      skipped,
      rejected,
      failures
    });
    return;
  }

  await chrome.storage.session.remove(key);
  const summary = {
    inserted: batch.inserted,
    updated: batch.updated,
    skipped: batch.skipped,
    saved: batch.saved,
    rejected: batch.rejected,
    pages: batch.pages,
    completedTerms: batch.completedTerms,
    completedAt: new Date().toISOString()
  };
  await chrome.storage.local.set({ seekBatchSummary: summary });
  await chrome.action.setBadgeBackgroundColor({ color: "#195c47" });
  await chrome.action.setBadgeText({ text: String(Math.min(batch.saved, 99)) });
  sendResponse({ ok: true, complete: true, ...summary });
  await chrome.tabs.remove(tabId).catch(() => {});
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "CAREER_AGENT_STOP_SEEK_BATCH") {
    clearStaleBatches()
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (message?.type === "CAREER_AGENT_START_SEEK_BATCH") {
    if (!isTrustedExtensionPage(sender)) {
      sendResponse({ ok: false, error: "Start searches from the extension popup." });
      return false;
    }
    const terms = [...new Set((Array.isArray(message.terms) ? message.terms : [])
      .map(term => String(term).trim().slice(0, 120))
      .filter(Boolean))].slice(0, MAX_TERMS);
    if (!terms.length) {
      sendResponse({ ok: false, error: "Enter at least one search term." });
      return false;
    }
    startBatch(terms)
      .then(tabId => sendResponse({ ok: true, tabId, total: terms.length }))
      .catch(error => sendResponse({ ok: false, error: error.message || "Could not start the SEEK search." }));
    return true;
  }

  if (message?.type === "CAREER_AGENT_SEEK_BATCH_READY") {
    prepareBatchPage(sender, sendResponse).catch(() => sendResponse({ autoSave: false }));
    return true;
  }

  if (message?.type === "CAREER_AGENT_PROCESS_SEEK_BATCH") {
    processBatchResults(sender, message, sendResponse)
      .catch(async error => {
        const messageText = error?.message || "Could not save this SEEK results page.";
        await failBatch(sender.tab?.id, error);
        sendResponse({ ok: false, error: messageText });
      });
    return true;
  }

  if (message?.type !== "CAREER_AGENT_SAVE_SEEK_JOB") return false;

  const senderUrl = sender.url || sender.tab?.url || "";
  let senderHost = "";
  try {
    senderHost = new URL(senderUrl).hostname;
  } catch {
    sendResponse({ ok: false, error: "Open a SEEK Australia page first." });
    return false;
  }
  if (!SEEK_HOST.test(senderHost)) {
    sendResponse({ ok: false, error: "Jobs can only be imported from SEEK Australia." });
    return false;
  }

  fetch(IMPORT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.job || {})
  })
    .then(async response => {
      const result = await response.json();
      sendResponse({
        ok: response.ok,
        message: response.ok
          ? `${result.action === "skipped" ? "Already current" : result.action === "updated" ? "Updated" : "Saved"}: ${result.job.title}`
          : result.error,
        error: response.ok ? undefined : result.error
      });
    })
    .catch(() => sendResponse({
      ok: false,
      error: "Could not reach Career Agent. Make sure its local app is running."
    }));

  return true;
});

chrome.tabs.onRemoved.addListener(tabId => {
  chrome.storage.session.remove(batchKey(tabId));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;

  let hostname = "";
  try {
    hostname = new URL(tab.url).hostname;
  } catch {
    return;
  }
  if (!SEEK_HOST.test(hostname)) return;

  chrome.storage.session.get(batchKey(tabId)).then(async stored => {
    const batch = stored[batchKey(tabId)];
    if (!batch || batch.processing || batch.processedUrl === tab.url) return;
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"]
    });
  }).catch(async error => {
    await chrome.storage.local.set({
      seekBatchRuntimeError: `Could not start the SEEK page saver: ${error.message || "script injection failed"}`
    });
  });
});
