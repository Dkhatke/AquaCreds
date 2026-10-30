import { StatusPill } from "@/components/ui";
import { explorerTxUrl } from "@/lib/chain-links";
import { shortHash } from "@/lib/format";

/** A transaction hash: explorer link on Amoy, plain "local chain" text otherwise. */
export function TxLink({
  chainId,
  txHash,
  explorerUrl,
}: {
  chainId: number | null;
  txHash: string | null | undefined;
  /** Explorer URL from the API when it sent one (still only used for chain 80002). */
  explorerUrl?: string | null | undefined;
}) {
  if (!txHash) return <span className="text-fg-muted">Not broadcast yet</span>;
  if (chainId === null) {
    return (
      <code className="tabular text-xs" title={txHash}>
        {shortHash(txHash, 8, 6)}
      </code>
    );
  }
  const href = chainId === 80002 ? (explorerUrl ?? explorerTxUrl(chainId, txHash)) : null;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <code className="tabular text-xs" title={txHash}>
        {shortHash(txHash, 8, 6)}
      </code>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs underline underline-offset-2"
        >
          Explorer<span className="sr-only"> (opens in a new tab)</span>
        </a>
      ) : (
        <StatusPill status="neutral">Local chain {chainId}</StatusPill>
      )}
    </span>
  );
}
