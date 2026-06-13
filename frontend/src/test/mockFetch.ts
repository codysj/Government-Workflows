import { vi } from "vitest";

export interface MockRoute {
  match: RegExp;
  method?: string;
  respond: (
    url: string,
    init?: RequestInit,
  ) => { status?: number; body: unknown };
}

export interface RecordedCall {
  url: string;
  init?: RequestInit;
}

/** Install a fetch mock that routes by URL pattern and records every call. */
export function installFetchMock(routes: MockRoute[]) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    calls.push({ url, init });
    const method = (init?.method ?? "GET").toUpperCase();
    for (const route of routes) {
      if (route.match.test(url) && (!route.method || route.method === method)) {
        const { status = 200, body } = route.respond(url, init);
        return {
          ok: status >= 200 && status < 300,
          status,
          json: async () => body,
        } as unknown as Response;
      }
    }
    throw new Error(`Unhandled fetch in test: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}
