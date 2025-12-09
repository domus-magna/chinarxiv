import { onRequestOptions as __api_request_figure_translation_js_onRequestOptions } from "/Users/alexanderhuth/chinaxiv-english/functions/api/request-figure-translation.js"
import { onRequestPost as __api_request_figure_translation_js_onRequestPost } from "/Users/alexanderhuth/chinaxiv-english/functions/api/request-figure-translation.js"

export const routes = [
    {
      routePath: "/api/request-figure-translation",
      mountPath: "/api",
      method: "OPTIONS",
      middlewares: [],
      modules: [__api_request_figure_translation_js_onRequestOptions],
    },
  {
      routePath: "/api/request-figure-translation",
      mountPath: "/api",
      method: "POST",
      middlewares: [],
      modules: [__api_request_figure_translation_js_onRequestPost],
    },
  ]