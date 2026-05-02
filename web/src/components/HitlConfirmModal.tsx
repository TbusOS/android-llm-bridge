/**
 * Reusable Human-in-the-loop confirm modal (PR-E.v2 + PR-H lift).
 *
 * Two consumers as of N=2:
 *  - ShellTab — terminal_route emits {type:"hitl_request"} when a
 *    typed command matches a deny / ask rule. User confirms /
 *    denies, optionally for the rest of the session.
 *  - FilesTab — files_route returns {requires_confirm:true} on push
 *    to a sensitive remote prefix (/system, /vendor, /data, …).
 *
 * Pattern lifted from FilesTab's inline modal at PR-H ship; this
 * component is the natural "ABC 第 1 个非首例消费者 = 免费 stress
 * test" (L-020). API kept narrow on purpose — both consumers
 * exercise it differently (Shell needs allow_session, Files doesn't),
 * but both the structure (header / details kv / actions row) and the
 * a11y wiring (role=dialog, ESC, click-outside) are identical.
 */

import { useEffect } from "react";
import type { ReactNode } from "react";

export interface HitlConfirmModalProps {
  /** When true the modal renders. False = unmounted (no fade-out). */
  open: boolean;
  /** Heading text (≤ 60 chars). */
  title: string;
  /** Body paragraph explaining what's about to happen + risk. */
  description: ReactNode;
  /** Key-value rows shown in monospace, useful for command/path/reason. */
  details?: Record<string, ReactNode>;
  /** Cancel/no button label (default "Cancel"). */
  cancelLabel?: string;
  /** Primary confirm button label (e.g. "Approve once" / "Confirm push"). */
  approveLabel: string;
  /** Optional secondary confirm — e.g. "Approve for this session". */
  approveSessionLabel?: string;
  /** When true the approve button uses the danger style (red bg). */
  approveDanger?: boolean;
  /** Pending state — disables both approve buttons + shows label. */
  pending?: boolean;
  onCancel: () => void;
  onApprove: () => void;
  onApproveSession?: () => void;
}

export function HitlConfirmModal(p: HitlConfirmModalProps) {
  // ESC closes (cancel). Focus management is browser-default — full
  // a11y trap can land in v3 if the modal usage grows; for 2 callers
  // it's not yet worth importing react-aria-modal.
  useEffect(() => {
    if (!p.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        p.onCancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [p.open, p.onCancel]);

  if (!p.open) return null;

  return (
    <div
      className="hitl-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="hitl-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) p.onCancel();
      }}
    >
      <div className="hitl-modal__card">
        <h3 id="hitl-modal-title">{p.title}</h3>
        <p>{p.description}</p>
        {p.details && Object.keys(p.details).length > 0 ? (
          <dl className="hitl-modal__kv">
            {Object.entries(p.details).map(([k, v]) => (
              <div key={k} className="hitl-modal__kv-row">
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <div className="hitl-modal__actions">
          <button type="button" className="btn" onClick={p.onCancel}>
            {p.cancelLabel ?? "Cancel"}
          </button>
          {p.onApproveSession ? (
            <button
              type="button"
              className="btn"
              onClick={p.onApproveSession}
              disabled={!!p.pending}
            >
              {p.approveSessionLabel ?? "Approve for session"}
            </button>
          ) : null}
          <button
            type="button"
            className={
              p.approveDanger ? "btn btn--danger" : "btn btn--primary"
            }
            onClick={p.onApprove}
            disabled={!!p.pending}
          >
            {p.approveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
