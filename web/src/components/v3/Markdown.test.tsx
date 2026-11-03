/**
 * Markdown in chat text. The first case is the answer from the 2026-11-03
 * screenshot, which showed "**Device**" with its asterisks and the three
 * list lines run together.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./Markdown";

function html(text: string): HTMLElement {
  const { container } = render(<Markdown text={text} />);
  return container;
}

describe("Markdown", () => {
  it("renders the 11-03 answer as a list with bold labels", () => {
    const c = html(
      "I can see a device is connected:\n\n- **Device**: Pixel 7 (panther)\n- **Serial**: 11223344\n- **State**: device (online)\n\nWhat would you like to do?",
    );
    const items = c.querySelectorAll("ul > li");
    expect(items).toHaveLength(3);
    expect(items[0]?.querySelector("strong")?.textContent).toBe("Device");
    expect(items[0]?.textContent).toBe("Device: Pixel 7 (panther)");
    expect(c.textContent).not.toContain("**");
    expect(c.querySelectorAll("p")).toHaveLength(2);
  });

  it("keeps a single line break inside a paragraph", () => {
    const c = html("第一行\n第二行");
    expect(c.querySelectorAll("p")).toHaveLength(1);
    expect(c.querySelectorAll("p br")).toHaveLength(1);
  });

  it("numbered lists keep their start and nest by indent", () => {
    const c = html("3. 重启\n4. 抓日志\n   - logcat\n   - dmesg");
    const ol = c.querySelector("ol");
    expect(ol?.getAttribute("start")).toBe("3");
    expect(ol?.querySelectorAll(":scope > li")).toHaveLength(2);
    expect(ol?.querySelectorAll("li ul li")).toHaveLength(2);
  });

  it("a plain line right after a list is its own paragraph", () => {
    const c = html("- a\n- b\n要我做什么?");
    expect(c.querySelectorAll("li")).toHaveLength(2);
    expect(c.querySelector("p")?.textContent).toBe("要我做什么?");
  });

  it("fenced code is a terminal block and its contents stay literal", () => {
    const c = html("看这里:\n```sh\nadb shell **x** `y`\n```\n完");
    const pre = c.querySelector("pre.term");
    expect(pre?.textContent).toBe("adb shell **x** `y`");
    expect(pre?.querySelector("strong")).toBeNull();
  });

  it("an answer cut off inside a code block still shows the code", () => {
    const c = html("```\nline 1\nline 2");
    expect(c.querySelector("pre")?.textContent).toBe("line 1\nline 2");
  });

  it("tables get a header, rows and alignment", () => {
    const c = html("| 项 | 值 |\n|---|---:|\n| CPU | 8 |\n| 内存 | `4G` |");
    expect([...c.querySelectorAll("th")].map((e) => e.textContent)).toEqual(["项", "值"]);
    const tds = c.querySelectorAll("td");
    expect(tds).toHaveLength(4);
    expect((tds[1] as HTMLElement).style.textAlign).toBe("right");
    expect(tds[3]?.querySelector("code")?.textContent).toBe("4G");
  });

  it("headings, quotes and rules", () => {
    const c = html("## 结论\n> 板子在线\n\n---\n正文");
    expect(c.querySelector("h4")?.textContent).toBe("结论");
    expect(c.querySelector("blockquote")?.textContent).toBe("板子在线");
    expect(c.querySelector("hr")).not.toBeNull();
  });

  it("underscores in tool names do not turn italic; asterisks do", () => {
    const c = html("调了 alb_status 和 __init__,*注意* 2*3*4");
    expect(c.querySelector("em")?.textContent).toBe("注意");
    expect(c.querySelectorAll("em")).toHaveLength(1);
    expect(c.textContent).toContain("alb_status");
    expect(c.textContent).toContain("2*3*4");
  });

  it("inline code, strike and bold nest without losing their place", () => {
    const c = html("**用 `alb_shell` 跑** 然后 ~~重启~~ 再看 `dmesg`");
    expect(c.querySelector("strong code")?.textContent).toBe("alb_shell");
    expect(c.querySelector("del")?.textContent).toBe("重启");
    const codes = [...c.querySelectorAll("code")].map((e) => e.textContent);
    expect(codes).toEqual(["alb_shell", "dmesg"]);
  });

  it("links: http(s) only, opened in a new tab", () => {
    const c = html("[文档](https://example.com/a) [坏](javascript:alert(1)) 见 https://example.com/b。");
    const links = [...c.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://example.com/a", "https://example.com/b"]);
    expect(links.every((a) => a.getAttribute("rel") === "noopener noreferrer" && a.target === "_blank")).toBe(true);
    expect(c.textContent).toContain("[坏](javascript:alert(1))");
  });

  it("HTML in the text is shown as text, never parsed", () => {
    const c = html('<img src=x onerror="alert(1)"> <script>alert(2)</script> **ok**');
    expect(c.querySelector("img")).toBeNull();
    expect(c.querySelector("script")).toBeNull();
    expect(c.textContent).toContain("<img src=x");
    expect(c.querySelector("strong")?.textContent).toBe("ok");
  });
});
