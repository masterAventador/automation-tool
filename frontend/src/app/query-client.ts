import { QueryClient } from "@tanstack/react-query";

export function createDesktopQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}

export const desktopQueryClient = createDesktopQueryClient();
