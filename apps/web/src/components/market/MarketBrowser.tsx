"use client";

import { useState } from "react";
import { Field, inputClass } from "@/components/registry/fields";
import { WalletGate } from "@/components/registry/WalletGate";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { DEFAULT_FILTERS, type ListingFilters, useListings } from "@/lib/registry-queries";
import { ListingCard } from "./ListingCard";

const SORTS: { value: ListingFilters["sort"]; label: string }[] = [
  { value: "newest", label: "Newest" },
  { value: "price_asc", label: "Price, low to high" },
  { value: "price_desc", label: "Price, high to low" },
];

/** Listings with filters, sort and the "show unavailable" toggle (hidden by default). */
export function MarketBrowser() {
  const [filters, setFilters] = useState<ListingFilters>(DEFAULT_FILTERS);
  const [stateText, setStateText] = useState("");
  const [priceText, setPriceText] = useState("");
  const { data, isPending, isError, error, refetch, isFetching } = useListings(filters);

  const set = (patch: Partial<ListingFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const applyText = () =>
    set({
      state: stateText.trim() || undefined,
      max_price:
        priceText !== "" && Number.isInteger(Number(priceText)) ? Number(priceText) : undefined,
    });

  return (
    <div className="space-y-5">
      <form
        aria-label="Filter listings"
        className="grid gap-3 rounded-[var(--radius-card)] border border-line bg-panel p-4 sm:grid-cols-2 lg:grid-cols-5"
        onSubmit={(e) => {
          e.preventDefault();
          applyText();
        }}
      >
        <Field label="Credit type">
          {({ id }) => (
            <select
              id={id}
              className={inputClass}
              value={filters.credit_type ?? ""}
              onChange={(e) =>
                set({ credit_type: (e.target.value || undefined) as ListingFilters["credit_type"] })
              }
            >
              <option value="">All</option>
              <option value="temporary">tCCC (temporary)</option>
              <option value="long_term">lCCC (long-term)</option>
            </select>
          )}
        </Field>
        <Field label="State">
          {({ id }) => (
            <input
              id={id}
              className={inputClass}
              value={stateText}
              placeholder="e.g. Odisha"
              onChange={(e) => setStateText(e.target.value)}
              onBlur={applyText}
            />
          )}
        </Field>
        <Field label="Max price per unit" hint="Payment-token base units">
          {({ id, describedBy }) => (
            <input
              id={id}
              aria-describedby={describedBy}
              className={inputClass}
              inputMode="numeric"
              value={priceText}
              onChange={(e) => setPriceText(e.target.value)}
              onBlur={applyText}
            />
          )}
        </Field>
        <Field label="Sort">
          {({ id }) => (
            <select
              id={id}
              className={inputClass}
              value={filters.sort}
              onChange={(e) => set({ sort: e.target.value as ListingFilters["sort"] })}
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="flex items-end gap-2 pb-2">
          <input
            id="show-unavailable"
            type="checkbox"
            className="size-4 accent-[var(--color-lagoon-mint)]"
            checked={filters.include_unavailable}
            onChange={(e) => set({ include_unavailable: e.target.checked })}
          />
          <label htmlFor="show-unavailable" className="text-sm">
            Show unavailable listings
          </label>
        </div>
        <button type="submit" className="sr-only">
          Apply filters
        </button>
      </form>

      <div aria-live="polite" className="text-sm text-fg-muted">
        {data &&
          `${data.total} listing${data.total === 1 ? "" : "s"}${filters.include_unavailable ? "" : " available"}`}
        {isFetching && !isPending && " · updating…"}
      </div>

      {isPending ? (
        <LoadingState label="Loading listings" />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : data.items.length === 0 ? (
        <EmptyState title="No listings match">
          {filters.include_unavailable
            ? "Nothing is listed with these filters."
            : "No available listings with these filters. Try showing unavailable listings."}
        </EmptyState>
      ) : (
        <ul className="space-y-4">
          {data.items.map((l) => (
            <li key={l.listing_id}>
              <ListingCard listing={l}>
                {l.available !== false && <WalletGate action="Buying these credits" />}
              </ListingCard>
            </li>
          ))}
        </ul>
      )}
      {data && data.total > data.items.length && (
        <p className="text-xs text-fg-muted">
          Showing the first {data.items.length} of {data.total}; narrow the filters to see others.
        </p>
      )}
    </div>
  );
}
