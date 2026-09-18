/**
 * The outbound address, where an operator can copy it.
 *
 * Zerodha's API settings ask for addresses to be entered by hand, and an
 * operator reading them off a terminal and typing them into a browser is how a
 * digit gets transposed. So the value is shown here with a copy button.
 *
 * Two things this must not do. It must not imply an address is registered when
 * none is: an unrecorded address reads NOT RECORDED, not blank. And it must not
 * present a detected address as a verified one — Sterling never asks the
 * internet what its own address is, because a safety check that depends on a
 * third party fails when that third party does. What the panel shows is what
 * the deployment recorded, and the command to record it.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../utils/api';

export interface EgressStatus {
  expected_ip: string | null;
  observed_ip: string | null;
  observed_at: string | null;
  router_generation: string | null;
  verified: boolean | null;
  reason: string;
  changed_from: string | null;
  record_command: string;
}

export const EGRESS_PATH = '/api/v1/operations/egress';

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={`copy ${label}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => setCopied(false),
        );
      }}
      style={{
        padding: '1px 6px',
        borderRadius: 4,
        border: '1px solid var(--k-border, #d3d6da)',
        background: 'transparent',
        color: copied ? '#59a96a' : '#475569',
        fontSize: 9.5,
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      {copied ? 'copied' : 'copy'}
    </button>
  );
}

export function EgressAddressRow() {
  // Unavailability is a value, not a thrown error. This widget sits inside the
  // session card and must never take it down, and a rejected query landing
  // after the card unmounts is a noisy failure with no operator meaning.
  const { data } = useQuery<EgressStatus | null>({
    queryKey: ['operations-egress'],
    queryFn: async () => {
      try {
        return await api.get<EgressStatus>(EGRESS_PATH);
      } catch {
        return null;
      }
    },
    staleTime: 60_000,
    retry: false,
  });

  const isError = data === null;

  const observed = data?.observed_ip ?? null;
  const expected = data?.expected_ip ?? null;
  const verified = data?.verified ?? null;

  const tone =
    verified === true ? '#59a96a' : verified === false ? '#e95420' : '#f4a261';
  const word =
    isError ? 'UNAVAILABLE' : verified === true ? 'VERIFIED' : verified === false ? 'MISMATCH' : 'UNVERIFIED';

  return (
    <div
      data-testid="egress-address"
      style={{
        marginTop: 10,
        paddingTop: 10,
        borderTop: '1px solid rgba(15,23,42,.07)',
        fontSize: 9.5,
        color: '#4a4f56',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontWeight: 700, letterSpacing: 0.3 }}>OUTBOUND IP</span>
        <span style={{ color: tone, fontWeight: 700 }} aria-label={`egress ${word}`}>
          {word}
        </span>
        <span style={{ marginLeft: 'auto', color: '#8b93a7' }}>for the broker's allowlist</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ minWidth: 62, color: '#8b93a7' }}>observed</span>
        {observed ? (
          <>
            <code
              style={{
                fontFamily: 'var(--k-mono, ui-monospace, Menlo, monospace)',
                fontSize: 11,
                userSelect: 'all',
              }}
            >
              {observed}
            </code>
            <CopyButton value={observed} label="observed outbound IP" />
          </>
        ) : (
          <span style={{ color: '#f4a261', fontWeight: 600 }}>
            NOT RECORDED — run <code>{data?.record_command ?? 'sterlingctl egress record <ip>'}</code>
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
        <span style={{ minWidth: 62, color: '#8b93a7' }}>registered</span>
        {expected ? (
          <>
            <code
              style={{
                fontFamily: 'var(--k-mono, ui-monospace, Menlo, monospace)',
                fontSize: 11,
                userSelect: 'all',
              }}
            >
              {expected}
            </code>
            <CopyButton value={expected} label="registered outbound IP" />
          </>
        ) : (
          <span style={{ color: '#f4a261', fontWeight: 600 }}>
            UNSET — no address is registered with the broker
          </span>
        )}
      </div>

      {data?.changed_from && (
        <div style={{ marginTop: 3, color: '#e95420' }}>
          changed from <code>{data.changed_from}</code> — re-approve it with the broker
        </div>
      )}

      {data?.reason && verified !== true && (
        <div style={{ marginTop: 3, color: '#8b93a7' }}>{data.reason}</div>
      )}
    </div>
  );
}

export default EgressAddressRow;
