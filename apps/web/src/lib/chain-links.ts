import type { Schema } from "@aquacreds/api-client";
import type { Status } from "@/components/ui";

/** Polygon Amoy testnet: the only chain with a public explorer for these registries. */
export const AMOY_CHAIN_ID = 80002;
const AMOY_EXPLORER = "https://amoy.polygonscan.com";

/** Explorer link for a transaction, or null when the chain is a local/dev chain. */
export function explorerTxUrl(chainId: number, txHash: string | null | undefined): string | null {
  return chainId === AMOY_CHAIN_ID && txHash ? `${AMOY_EXPLORER}/tx/${txHash}` : null;
}

/** An API-supplied explorer URL is only trusted for Amoy; anything else is a local chain. */
export function explorerUrlFor(chainId: number, apiUrl: string | null | undefined): string | null {
  return chainId === AMOY_CHAIN_ID && apiUrl ? apiUrl : null;
}

export function chainLabel(chainId: number): string {
  return chainId === AMOY_CHAIN_ID ? "Polygon Amoy testnet" : `Local chain ${chainId}`;
}

type TxStatus = Schema<"ChainTxStatus">;

export const TX_STATUS: Record<TxStatus, { status: Status; label: string }> = {
  queued: { status: "neutral", label: "Queued" },
  waiting_for_gas: { status: "review", label: "Waiting for gas" },
  submitted: { status: "review", label: "Submitted" },
  confirmed: { status: "ok", label: "Confirmed" },
  failed: { status: "failed", label: "Failed" },
  reverted: { status: "failed", label: "Reverted" },
};

export const TX_PURPOSE: Record<Schema<"ChainTxPurpose">, string> = {
  register_project: "Project registration",
  submit_report: "Report submission",
  approve_report: "Report approval",
  reject_report: "Report rejection",
  issue_credits: "Credit issuance",
  forward_fund_offer: "Forward-fund offer",
  forward_fund_withdraw: "Forward-fund withdrawal",
  gas_top_up: "Gas top-up",
  other: "Other",
};
