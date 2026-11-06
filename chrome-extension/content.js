(() => {
  const BUTTON_CLASS = "career-agent-seek-save";
  const TITLE_SELECTOR = 'a[data-automation="jobTitle"], a[id^="job-title-"], a[href*="/job/"]';
  const AU_LOCATION = /\b(?:australia|remote\s*[-,]?\s*australia|melbourne|sydney|brisbane|perth|adelaide|canberra|hobart|darwin|geelong|newcastle|wollongong|gold coast|sunshine coast|cairns|townsville|ballarat|bendigo|toowoomba|victoria|new south wales|queensland|western australia|south australia|tasmania|northern territory|australian capital territory|vic|nsw|qld|wa|sa|tas|act|nt)\b/i;

  function clean(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function parseSalaryRange(rawText) {
    const text = clean(rawText).replace(/,/g, "");
    if (!text) return { salary_min: null, salary_max: null };
    const matches = [...text.matchAll(/\$?\s*(\d{2,3}(?:\.\d+)?)\s*(k)?/gi)];
    const values = matches
      .map(m => {
        const base = parseFloat(m[1]);
        if (Number.isNaN(base)) return null;
        return m[2] || base < 1000 ? Math.round(base * 1000) : Math.round(base);
      })
      .filter(v => v != null && v >= 20000 && v <= 900000);
    if (!values.length) return { salary_min: null, salary_max: null };
    return {
      salary_min: Math.min(...values),
      salary_max: values.length > 1 ? Math.max(...values) : Math.min(...values)
    };
  }

  function getCard(anchor) {
    const knownCard = anchor.closest(
      '[data-automation="normalJob"], [data-automation="premiumJob"], [data-testid*="job-card"], article'
    );
    if (knownCard && containsOnlyThisJob(knownCard, anchor)) return knownCard;

    let card = null;
    let ancestor = anchor.parentElement;
    for (let depth = 0; ancestor && depth < 8; depth += 1, ancestor = ancestor.parentElement) {
      if (!containsOnlyThisJob(ancestor, anchor)) break;
      card = ancestor;
    }
    return card;
  }

  function canonicalJobUrl(anchor) {
    try {
      const url = new URL(anchor.getAttribute("href"), window.location.href);
      return `${url.origin}${url.pathname}`.replace(/\/$/, "");
    } catch {
      return "";
    }
  }

  function containsOnlyThisJob(container, targetAnchor) {
    const links = [...container.querySelectorAll('a[href*="/job/"]')];
    links.push(targetAnchor);
    const jobUrls = new Set(links.map(canonicalJobUrl).filter(Boolean));
    return jobUrls.size === 1 && jobUrls.has(canonicalJobUrl(targetAnchor));
  }

  function extractJob(anchor, card, isBatch = false) {
    const titleLinks = [
      ...card.querySelectorAll('a[data-automation="jobTitle"], a[id^="job-title-"], a[data-testid*="job-title"], a[href*="/job/"]')
    ].filter(link => canonicalJobUrl(link) === canonicalJobUrl(anchor));
    titleLinks.sort((left, right) => {
      const score = link =>
        (link.getAttribute("data-automation") === "jobTitle" ? 8 : 0) +
        (clean(link.innerText || link.textContent) ? 4 : 0) +
        (link.getClientRects().length ? 2 : 0);
      return score(right) - score(left);
    });
    const title = clean(anchor.innerText || anchor.textContent) ||
      clean(titleLinks[0]?.innerText || titleLinks[0]?.textContent) ||
      clean(titleLinks[0]?.getAttribute("aria-label") || titleLinks[0]?.title).replace(/^view job:?\s*/i, "");
    const url = canonicalJobUrl(anchor) || new URL(anchor.getAttribute("href"), window.location.href).toString();
    const companyElement = card.querySelector(
      '[data-automation="jobCompany"], [data-automation*="companyName"], [data-automation*="jobCompany"], [data-testid*="company"]'
    );
    const locationElements = [
      ...card.querySelectorAll('[data-automation="jobLocation"], [data-automation="jobCardLocation"], [data-automation*="location"], [data-testid*="location"]')
    ];
    const salaryElement = card.querySelector(
      '[data-automation="jobSalary"], [data-testid*="salary"]'
    );
    const lines = (card.innerText || "").split(/\n+/).map(clean).filter(Boolean);
    const locationText = locationElements.map(el => clean(el.innerText)).filter(Boolean).join(", ");
    const location = locationText || lines.find(line => AU_LOCATION.test(line)) || "Australia";
    const companyLine = lines.find(line => /^at\s+\S/i.test(line));
    const company = clean(companyElement?.innerText) ||
      (companyLine ? clean(companyLine.replace(/^at\s+/i, "")) : "Private Advertiser");
    const descriptionElement = card.querySelector(
      '[data-automation*="jobShortDescription"], [data-automation*="jobDescription"]'
    );
    const bulletText = [...card.querySelectorAll("ul li")].map(li => clean(li.innerText)).filter(Boolean).join(" • ");
    const description = clean(descriptionElement?.innerText || bulletText).slice(0, 12000);
    const postedAt = card.querySelector("time[datetime]")?.getAttribute("datetime") || null;
    const { salary_min, salary_max } = parseSalaryRange(salaryElement?.innerText || "");

    return {
      title,
      company,
      location,
      description,
      url,
      posted_at: postedAt,
      salary_min,
      salary_max,
      allow_any_scope: true
    };
  }

  function setStatus(button, text, isError = false) {
    button.textContent = text;
    button.dataset.error = isError ? "true" : "false";
    button.disabled = !isError;
  }

  function addSaveButton(anchor) {
    const card = getCard(anchor);
    if (!card || card.querySelector(`.${BUTTON_CLASS}`)) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = BUTTON_CLASS;
    button.textContent = "Save to Career Agent";
    button.setAttribute("aria-label", `Save ${clean(anchor.innerText || anchor.textContent)} to Career Agent`);
    button.style.cssText = "display:block;margin:8px 0;padding:7px 10px;border:0;border-radius:4px;background:#195c47;color:#fff;font:600 12px Arial,sans-serif;cursor:pointer;";

    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      setStatus(button, "Saving...", false);
      chrome.runtime.sendMessage(
        { type: "CAREER_AGENT_SAVE_SEEK_JOB", job: extractJob(anchor, card, false) },
        response => {
          if (chrome.runtime.lastError) {
            setStatus(button, "Save failed. Retry", true);
            button.title = "Make sure Career Agent is running locally.";
            return;
          }
          if (response?.ok) {
            setStatus(button, "Saved to Career Agent", false);
            button.title = response.message || "Saved";
          } else {
            setStatus(button, "Save failed. Retry", true);
            button.title = response?.error || "The job could not be saved.";
          }
        }
      );
    });

    card.appendChild(button);
  }

  function scanVisibleResultLinks() {
    for (const anchor of getResultLinks()) {
      addSaveButton(anchor);
    }
  }

  function getResultLinks() {
    const titleAnchors = [...document.querySelectorAll(TITLE_SELECTOR)].filter(anchor =>
      !anchor.closest("header, nav") && /\/job\//i.test(anchor.getAttribute("href") || "")
    );
    titleAnchors.sort((left, right) => {
      const score = anchor =>
        (anchor.getAttribute("data-automation") === "jobTitle" ? 8 : 0) +
        (clean(anchor.innerText || anchor.textContent) ? 4 : 0) +
        (anchor.getClientRects().length ? 2 : 0);
      return score(right) - score(left);
    });
    const seen = new Set();
    return titleAnchors.filter(anchor => {
      const url = canonicalJobUrl(anchor);
      if (!url || seen.has(url) || !getCard(anchor)) return false;
      seen.add(url);
      return true;
    });
  }

  function getNextResultsPageUrl() {
    const directNext = document.querySelector('a[data-automation="page-next"], a[rel="next"]');
    if (directNext && directNext.href && directNext.href !== window.location.href) {
      return directNext.href;
    }
    const links = [...document.querySelectorAll('a[aria-label], a[title], nav a')];
    for (const link of links) {
      const label = clean(link.getAttribute("aria-label") || link.getAttribute("title") || link.textContent);
      if (!/^next(?:\s+(?:page|results?))?$/i.test(label)) continue;
      try {
        const url = new URL(link.href, window.location.href);
        if (/^(?:[a-z0-9-]+\.)*seek\.com\.au$|^au\.seek\.com$/i.test(url.hostname) &&
            /jobs/i.test(url.pathname) && url.href !== window.location.href) {
          return url.href;
        }
      } catch {
        // Ignore malformed pagination links.
      }
    }
    return "";
  }

  function waitForResultLinks() {
    return new Promise(resolve => {
      const deadline = Date.now() + 10000;
      let previousCount = -1;
      let stablePolls = 0;
      const check = () => {
        const count = getResultLinks().length;
        stablePolls = count > 0 && count === previousCount ? stablePolls + 1 : 0;
        previousCount = count;
        if ((count > 0 && stablePolls >= 2) || Date.now() >= deadline) {
          resolve();
          return;
        }
        setTimeout(check, 250);
      };
      check();
    });
  }

  function showBatchStatus(message, isError = false) {
    let banner = document.getElementById("career-agent-seek-batch-status");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "career-agent-seek-batch-status";
      banner.style.cssText = "position:fixed;z-index:2147483647;top:12px;right:12px;max-width:320px;padding:10px 14px;border-radius:5px;background:#195c47;color:#fff;font:13px/1.4 Arial,sans-serif;box-shadow:0 3px 14px #0003;";
      document.documentElement.appendChild(banner);
    }
    banner.style.background = isError ? "#8f3429" : "#195c47";
    banner.textContent = message;
  }

  async function saveFirstPageResults(term) {
    await waitForResultLinks();
    const unique = new Map();
    let extractionSkipped = 0;
    const entries = getResultLinks().map(anchor => ({ anchor, card: getCard(anchor), job: null }));
    for (const entry of entries) {
      const { anchor, card } = entry;
      if (!card) {
        extractionSkipped += 1;
        continue;
      }
      const job = extractJob(anchor, card, true);
      entry.job = job;
      if (job.title && job.url) {
        unique.set(canonicalJobUrl(anchor), job);
      } else {
        extractionSkipped += 1;
      }
    }

    chrome.runtime.sendMessage(
      {
        type: "CAREER_AGENT_PROCESS_SEEK_BATCH",
        term,
        detected: unique.size + extractionSkipped,
        extractionSkipped,
        missingCompany: entries.filter(entry => entry.card && !entry.job?.company).length,
        missingTitle: entries.filter(entry => !entry.job?.title).length,
        missingUrl: entries.filter(entry => !entry.job?.url).length,
        missingLocation: entries.filter(entry => entry.card && !entry.job?.location).length,
        nextPageUrl: getNextResultsPageUrl(),
        jobs: [...unique.values()]
      },
      response => {
        window.__careerAgentBatchBusy = false;
        if (chrome.runtime.lastError || !response?.ok) {
          showBatchStatus(response?.error || "Could not save this results page.", true);
          return;
        }
        if (response.complete) {
          const detected = response.completedTerms.reduce((total, item) => total + item.detected, 0);
          const reason = response.completedTerms.flatMap(item => item.failures || [])[0]?.error;
          showBatchStatus(
            `Search run complete: ${response.pages} pages, ${detected} cards detected, ${response.completedTerms.reduce((total, item) => total + item.extractionSkipped, 0)} incomplete skipped; ${response.inserted} new, ${response.updated} updated, ${response.skipped} unchanged, ${response.rejected} rejected.` +
              (reason ? ` First error: ${reason}` : " Review and apply manually."),
            Boolean(reason)
          );
        } else if (response.nextPage) {
          showBatchStatus(
            `${response.term}: saved page ${response.page}; ${response.inserted} new, ${response.updated} updated, ${response.skipped} unchanged. Loading the next results page...`,
            Boolean(response.failures?.[0]?.error)
          );
        } else {
          const reason = response.failures?.[0]?.error;
          showBatchStatus(
            `${response.term}: ${response.detected} cards detected, ${response.extractionSkipped} incomplete skipped, ${response.inserted} new, ${response.updated} updated, ${response.skipped} unchanged, ${response.rejected} rejected.` +
              (reason ? ` First error: ${reason}` : " Moving to next search."),
            Boolean(reason)
          );
        }
      }
    );
  }

  function checkBatchState() {
    if (window.__careerAgentBatchBusy) return;
    chrome.runtime.sendMessage({ type: "CAREER_AGENT_SEEK_BATCH_READY" }, response => {
      if (chrome.runtime.lastError) return;
      if (response?.autoSave) {
        window.__careerAgentBatchBusy = true;
        showBatchStatus(`Saving displayed jobs for: ${response.term}`);
        saveFirstPageResults(response.term);
        return;
      }
      scanVisibleResultLinks();
    });
  }

  window.__careerAgentCheckBatch = checkBatchState;
  checkBatchState();

  if (!window.__careerAgentSeekSaverLoaded) {
    window.__careerAgentSeekSaverLoaded = true;
    const observer = new MutationObserver(scanVisibleResultLinks);
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
