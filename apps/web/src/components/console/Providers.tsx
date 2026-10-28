"use client";

import { ApiError } from "@aquacreds/api-client";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";

/** React Query + "session ended" handling: a 401 that survived the BFF refresh means sign in again. */
export function Providers({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [client] = useState(() => {
    const onError = (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) {
        router.replace(`/console/login?next=${encodeURIComponent(pathname)}&expired=1`);
      }
    };
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        queries: {
          staleTime: 60_000,
          refetchOnWindowFocus: false,
          retry: (count, err) =>
            !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
        },
      },
    });
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
