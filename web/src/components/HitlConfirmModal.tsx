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

import { useEffect, useRef } from "react";
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
  // L-029 a11y 三件套：
  //   1. 初始 focus 落 Cancel 按钮（approveDanger 时；非 danger 也是
  //      安全默认）—— 用户从背景按 Tab 不会落到危险按钮
  //   2. ESC 关闭 + Enter = 默认 Cancel（避免误触发 destructive action）
  //   3. 危险按钮顺序: Cancel | Approve session | Approve once（最右）—
  //      destructive 离手指最远
  // 用 capture-phase document listener + stopImmediatePropagation 防
  // 多 modal 实例并发挂载 race（code-review 2026-05-02 MID 4）
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!p.open) return;
    // 初始 focus
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        p.onCancel();
      } else if (e.key === "Enter") {
        // Enter = Cancel（非危险默认），不等于 Approve
        const tag = (e.target as HTMLElement | null)?.tagName;
        // 让 button 自带 click on Enter 优先（焦点在 button 上时浏览器
        // 自动触发 click），仅当焦点不在任何 button 上时兜底走 Cancel
        if (tag !== "BUTTON") {
          e.stopImmediatePropagation();
          p.onCancel();
        }
      }
    };
    document.addEventListener("keydown", onKey, true); // capture
    return () => document.removeEventListener("keydown", onKey, true);
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
      <div className="hitl-modal__card" tabIndex={-1}>
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
          <button
            ref={cancelRef}
            type="button"
            className="btn"
            onClick={p.onCancel}
          >
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
          {/* approve(once) 排最右：destructive action 离手指最远 */}
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
