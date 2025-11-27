/**
 * Redirect Worker for ChinaRxiv
 * Redirects .com and www variants to the primary domain (chinarxiv.org)
 */

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // Redirect to primary domain, preserving path, query string, and hash fragment
    // Note: Hash fragments are typically not sent to servers by browsers,
    // but we include url.hash for completeness
    const targetUrl = `https://chinarxiv.org${url.pathname}${url.search}${url.hash}`;

    return Response.redirect(targetUrl, 301);
  },
};
