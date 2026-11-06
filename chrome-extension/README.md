# Career Agent SEEK Saver

A small Chrome extension for running user-provided SEEK Australia searches and saving postings from each normal results page to the local Career Agent SQLite database. It supports both `www.seek.com.au` and SEEK's `au.seek.com` redirect host. It only reads a result card when it can isolate one unique SEEK job link and read title, company, and location from that card. Company names are read from SEEK metadata or its visible “at Company Name” text; ambiguous/incomplete cards are skipped and counted. Descriptions and posted dates are saved only when the result card exposes them; the extension does not guess missing values. It does not apply for jobs or use private/internal endpoints.

## Load the extension

1. Start the Career Agent local app at `http://127.0.0.1:8765`.
2. In Chrome, open `chrome://extensions` and enable **Developer mode**.
3. Select **Load unpacked** and choose this `chrome-extension` folder.
4. After changing extension files, click **Reload** for the extension in `chrome://extensions`.
5. Refresh the Career Agent dashboard once so its saved-jobs list starts its 15-second refresh cycle.
6. Open the extension, enter one search term per line, and confirm that your use follows SEEK's terms.
7. Choose **Search and save all result pages**. The extension opens one background SEEK tab, searches each term in sequence, follows SEEK's normal next-page links, and imports every valid job card on each page.
8. The background tab closes when the batch is complete. Open the extension popup again to inspect per-search loaded/saved/rejected counts and sample errors. The dashboard refreshes its saved-job list automatically. Open each SEEK job yourself and apply manually if you choose.

You can also browse SEEK manually and use **Save to Career Agent** on a single result. SEEK imports require a title and official SEEK Australia job URL; company, location, description, and posted date are retained when available and may be blank. Repeated listings are compared by SEEK job URL: identical records are skipped, changed records are updated, and new URLs are inserted.

Each batch is started by your click and limited to 12 terms. It reads result cards SEEK has rendered on each normal results page and follows the visible next-page link, but does not click load-more controls, use private/internal endpoints, or apply for jobs. Use it only if this use is permitted by SEEK's current terms and your account permissions.
