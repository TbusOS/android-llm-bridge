/**
 * Markdown for chat text (model answers and the person's own messages).
 *
 * Everything becomes React elements; nothing in the text is ever read as
 * HTML, so an answer cannot inject markup or script, and a link is only
 * made for http(s) addresses. Covers what models actually write:
 * paragraphs (a single line break is kept, the way chat text is meant),
 * headings, bullet / numbered lists with nesting, fenced code, tables,
 * quotes and rules; inline **bold**, *italic*, `code`, ~~strike~~ and
 * links. Underscores are left alone on purpose: tool names such as
 * alb_status must not turn italic.
 *
 * Before 2026-11-03 answers were shown as plain text: "**Device**" kept
 * its asterisks and a list ran into one line.
 */
import { Fragment, type ReactNode } from "react";

/* ---------------- blocks ---------------- */

type Align = "left" | "center" | "right" | null;

interface ListItem {
  text: string;
  children: Block[];
}

export type Block =
  | { kind: "code"; text: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: ListItem[] }
  | { kind: "table"; head: string[]; align: Align[]; rows: string[][] }
  | { kind: "quote"; blocks: Block[] }
  | { kind: "hr" }
  | { kind: "para"; lines: string[] };

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

function indentOf(s: string): number {
  let n = 0;
  for (const ch of s) {
    if (ch === " ") n += 1;
    else if (ch === "\t") n += 4;
    else break;
  }
  return n;
}

function isTableStart(lines: string[], i: number): boolean {
  const next = lines[i + 1];
  return lines[i]?.includes("|") === true && next !== undefined && next.includes("|") && next.includes("-") && TABLE_SEP.test(next);
}

function startsBlock(lines: string[], i: number): boolean {
  const l = lines[i] ?? "";
  return FENCE.test(l) || HEADING.test(l) || HR.test(l) || ITEM.test(l) || QUOTE.test(l) || isTableStart(lines, i);
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function cellAlign(sep: string): Align {
  const s = sep.trim();
  const l = s.startsWith(":");
  const r = s.endsWith(":");
  return l && r ? "center" : r ? "right" : l ? "left" : null;
}

interface Entry {
  indent: number;
  ordered: boolean;
  num: number;
  text: string;
}

function buildList(entries: Entry[], from: number): { block: Block & { kind: "list" }; next: number } {
  const first = entries[from] as Entry;
  const block: Block & { kind: "list" } = { kind: "list", ordered: first.ordered, start: first.num, items: [] };
  let k = from;
  while (k < entries.length) {
    const e = entries[k] as Entry;
    if (e.indent < first.indent) break;
    if (e.indent > first.indent) {
      const { block: child, next } = buildList(entries, k);
      const last = block.items[block.items.length - 1];
      if (last) last.children.push(child);
      else block.items.push({ text: "", children: [child] });
      k = next;
      continue;
    }
    // a bullet list followed straight by a numbered one is two lists
    if (e.ordered !== block.ordered && block.items.length) break;
    block.items.push({ text: e.text, children: [] });
    k += 1;
  }
  return { block, next: k };
}

function parseList(region: string[]): Block[] {
  const entries: Entry[] = [];
  for (const l of region) {
    const m = l.match(ITEM);
    if (m) {
      const marker = m[2] as string;
      const ordered = /\d/.test(marker);
      entries.push({ indent: indentOf(m[1] as string), ordered, num: ordered ? Number.parseInt(marker, 10) : 1, text: m[3] as string });
    } else {
      // an indented line under an item continues that item
      const last = entries[entries.length - 1];
      if (last) last.text += `\n${l.trim()}`;
    }
  }
  const out: Block[] = [];
  let k = 0;
  while (k < entries.length) {
    const { block, next } = buildList(entries, k);
    out.push(block);
    k = next;
  }
  return out;
}

export function parseBlocks(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] as string;
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const fence = line.match(FENCE);
    if (fence) {
      const mark = fence[1] as string;
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] as string).trim().startsWith(mark)) {
        body.push(lines[i] as string);
        i += 1;
      }
      i += 1; // the closing fence, or past the end when the answer stopped inside the code
      out.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const h = line.match(HEADING);
    if (h) {
      out.push({ kind: "heading", level: (h[1] as string).length, text: h[2] as string });
      i += 1;
      continue;
    }
    if (HR.test(line)) {
      out.push({ kind: "hr" });
      i += 1;
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1] as string).map(cellAlign);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && (lines[i] as string).trim() && (lines[i] as string).includes("|")) {
        rows.push(splitRow(lines[i] as string));
        i += 1;
      }
      out.push({ kind: "table", head, align, rows });
      continue;
    }
    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i] as string)) {
        inner.push(((lines[i] as string).match(QUOTE) as RegExpMatchArray)[1] as string);
        i += 1;
      }
      out.push({ kind: "quote", blocks: parseBlocks(inner) });
      continue;
    }
    if (ITEM.test(line)) {
      const region: string[] = [];
      while (i < lines.length) {
        const l = lines[i] as string;
        if (!l.trim()) {
          // a blank line inside a list: keep going only if the list does
          let j = i + 1;
          while (j < lines.length && !(lines[j] as string).trim()) j += 1;
          const nx = lines[j];
          if (nx !== undefined && (ITEM.test(nx) || indentOf(nx) >= 2)) {
            i = j;
            continue;
          }
          break;
        }
        // a plain line that is not indented ends the list (chat text
        // seldom means it as a continuation of the last item)
        if (!ITEM.test(l) && indentOf(l) < 2) break;
        region.push(l);
        i += 1;
      }
      out.push(...parseList(region));
      continue;
    }
    const para: string[] = [line.trim()];
    i += 1;
    while (i < lines.length && (lines[i] as string).trim() && !startsBlock(lines, i)) {
      para.push((lines[i] as string).trim());
      i += 1;
    }
    out.push({ kind: "para", lines: para });
  }
  return out;
}

/* ---------------- inline ---------------- */

// One pass, earliest match wins: `code`, **bold**, ~~strike~~, [text](url),
// *italic* (not inside a word, so 2*3*4 stays as it is), bare http(s) URL.
const INLINE =
  /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\*\*(?=\S)([\s\S]*?\S)\*\*|~~(?=\S)([\s\S]*?\S)~~|\[([^\]\n]+)\]\(([^)\s]+)\)|(?<![\w*])\*(?![\s*])([^*\n]*?[^\s*])\*(?![\w*])|(https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"，。；：！？）])/g;

function safeHref(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

export function renderInline(text: string, keyBase = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  // matchAll works on its own copy of the regex, so the recursive calls
  // below (bold inside a link, …) do not move this loop's position.
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyBase}-${n++}`;
    if (m[2] !== undefined) {
      out.push(<code key={key}>{m[2].trim() || m[2]}</code>);
    } else if (m[3] !== undefined) {
      out.push(<strong key={key}>{renderInline(m[3], key)}</strong>);
    } else if (m[4] !== undefined) {
      out.push(<del key={key}>{renderInline(m[4], key)}</del>);
    } else if (m[5] !== undefined) {
      const href = safeHref(m[6] as string);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {renderInline(m[5], key)}
          </a>
        ) : (
          m[0]
        ),
      );
    } else if (m[7] !== undefined) {
      out.push(<em key={key}>{renderInline(m[7], key)}</em>);
    } else if (m[8] !== undefined) {
      out.push(
        <a key={key} href={m[8]} target="_blank" rel="noopener noreferrer">
          {m[8]}
        </a>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Inline text where a line break inside a paragraph or list item is kept. */
function lines(text: string, keyBase: string): ReactNode[] {
  return text.split("\n").map((l, i) => (
    // biome-ignore lint/suspicious/noArrayIndexKey: lines of a fixed text, never reordered
    <Fragment key={`${keyBase}-l${i}`}>
      {i > 0 ? <br /> : null}
      {renderInline(l, `${keyBase}-l${i}`)}
    </Fragment>
  ));
}

/* ---------------- render ---------------- */

function renderBlock(b: Block, key: string): ReactNode {
  switch (b.kind) {
    case "code":
      return (
        <pre className="term" key={key}>
          {b.text}
        </pre>
      );
    case "heading": {
      const Tag = b.level <= 3 ? "h4" : "h5";
      return (
        <Tag className="md-h" key={key}>
          {renderInline(b.text, key)}
        </Tag>
      );
    }
    case "hr":
      return <hr className="md-hr" key={key} />;
    case "quote":
      return (
        <blockquote className="md-quote" key={key}>
          {b.blocks.map((c, i) => renderBlock(c, `${key}-${i}`))}
        </blockquote>
      );
    case "list": {
      const items = b.items.map((it, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: items of a fixed text
        <li key={`${key}-${i}`}>
          {it.text ? lines(it.text, `${key}-${i}`) : null}
          {it.children.map((c, j) => renderBlock(c, `${key}-${i}-${j}`))}
        </li>
      ));
      return b.ordered ? (
        <ol className="md-list" key={key} start={b.start !== 1 ? b.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul className="md-list" key={key}>
          {items}
        </ul>
      );
    }
    case "table":
      return (
        <div className="md-table" key={key}>
          <table>
            <thead>
              <tr>
                {b.head.map((c, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: cells of a fixed text
                  <th key={i} style={b.align[i] ? { textAlign: b.align[i] as Exclude<Align, null> } : undefined}>
                    {renderInline(c, `${key}-h${i}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, ri) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: rows of a fixed text
                <tr key={ri}>
                  {b.head.map((_, ci) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: cells of a fixed text
                    <td key={ci} style={b.align[ci] ? { textAlign: b.align[ci] as Exclude<Align, null> } : undefined}>
                      {renderInline(r[ci] ?? "", `${key}-${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "para":
      return <p key={key}>{lines(b.lines.join("\n"), key)}</p>;
  }
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text.replace(/\r\n?/g, "\n").split("\n"));
  return <>{blocks.map((b, i) => renderBlock(b, `b${i}`))}</>;
}
