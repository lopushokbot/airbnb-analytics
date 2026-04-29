/* Airbnb search-results extractor.
 *
 * Paste the body of this function into mcp__playwright__browser_evaluate after
 * navigating to a search URL. Returns:
 *   {
 *     scraped_at: ISO timestamp,
 *     url: page URL,
 *     currency: "PLN" | "AED" | other (detected from price suffix),
 *     comp_count: int,
 *     prices_total: [int],     // guest-totals for the stay window
 *     median_total: int | null,
 *     p25_total: int | null,
 *     p75_total: int | null,
 *     listings: [{name, listing_id, total, url}]
 *   }
 *
 * The geo_keywords filter is configured per listing — pass it in via the page
 * URL hash like #keywords=La%20Mer,Port%20de%20la,Le%20Pont and the function
 * reads window.location.hash.
 */
(() => {
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const keywordsCSV = hash.get("keywords") || "";
  const excludeIds = (hash.get("exclude") || "").split(",").filter(Boolean);
  const keywords = keywordsCSV.split(",").filter(Boolean);
  const keywordRegex = keywords.length
    ? new RegExp(keywords.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "i")
    : null;

  const cards = document.querySelectorAll('[itemprop="itemListElement"], [data-testid="card-container"]');
  const seen = new Set();
  const listings = [];
  let detectedCurrency = null;

  cards.forEach(c => {
    const link = c.querySelector('a[href*="/rooms/"]')?.href?.split('?')[0];
    if (!link) return;
    const idMatch = link.match(/\/rooms\/(\d+)/);
    const listingId = idMatch ? idMatch[1] : null;
    if (!listingId || seen.has(listingId)) return;
    if (excludeIds.includes(listingId)) return;
    seen.add(listingId);

    const text = c.innerText || "";
    if (!/1 bedroom/i.test(text)) return;
    if (keywordRegex && !keywordRegex.test(text)) return;

    // Match a price + optional currency suffix.
    // Patterns seen: "1,234 zł total", "AED 1,234 total", "$1,234 total", "1,234 total".
    let m = text.match(/([A-Z$€£]{1,3}|zł)?\s*([\d,]+)\s*(zł|AED|USD|EUR|GBP)?\s*total/i);
    if (!m) {
      // try "for X nights" form
      m = text.match(/([A-Z$€£]{1,3}|zł)?\s*([\d,]+)\s*(zł|AED|USD|EUR|GBP)?\s*for\s+\d+\s+night/i);
    }
    if (!m) return;
    const total = parseInt(m[2].replace(/,/g, ""), 10);
    if (!total || total < 50) return;
    const cur = (m[3] || m[1] || "").trim();
    if (cur && !detectedCurrency) detectedCurrency = cur === "zł" ? "PLN" : cur.toUpperCase();

    const titleLine = text.split('\n').find(l =>
      keywordRegex ? keywordRegex.test(l) : true
    ) || text.split('\n')[0] || "";

    listings.push({
      name: titleLine.slice(0, 60),
      listing_id: listingId,
      total,
      url: link,
    });
  });

  const sorted = listings.map(l => l.total).sort((a, b) => a - b);
  const pctile = p => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))] : null;

  return {
    scraped_at: new Date().toISOString(),
    url: window.location.href,
    currency: detectedCurrency || "PLN",
    comp_count: listings.length,
    prices_total: sorted,
    median_total: pctile(0.5),
    p25_total: pctile(0.25),
    p75_total: pctile(0.75),
    listings,
  };
})();
