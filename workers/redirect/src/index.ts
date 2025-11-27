/**
 * Redirect Worker for ChinaRxiv
 * Redirects .com and www variants to the primary domain (chinarxiv.org)
 */

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // Redirect to primary domain, preserving path and query string
    const targetUrl = `https://chinarxiv.org${url.pathname}${url.search}`;

    return Response.redirect(targetUrl, 301);
  },
};
