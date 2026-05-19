/**
 * DOM helpers — small, typed wrappers around the noisy parts of the
 * native DOM API. Used everywhere in src/ so the panel code reads as
 * intent rather than ceremony.
 */

/** Strict querySelector — returns the first element OR throws. Use
 * when you KNOW the element exists (controllers wiring up their own
 * markup, etc.). */
export function qs<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T {
  const el = root.querySelector<T>(selector);
  if (!el) {
    throw new Error(`qs: element not found for selector ${JSON.stringify(selector)}`);
  }
  return el;
}

/** Lenient querySelector — returns the element or null. Use when the
 * markup might not be on this page (knowledge_bank.html etc.). */
export function qsMaybe<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T | null {
  return root.querySelector<T>(selector);
}

/** Find-by-id with no fuss. */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

/** querySelectorAll → real array. */
export function qsa<T extends Element = HTMLElement>(
  selector: string,
  root: ParentNode = document,
): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

/** Typed event binding. Returns an "off" function. */
export function on<K extends keyof HTMLElementEventMap>(
  el: HTMLElement | Document | Window,
  event: K,
  handler: (e: HTMLElementEventMap[K]) => void,
  opts?: AddEventListenerOptions,
): () => void {
  el.addEventListener(event, handler as EventListener, opts);
  return () => el.removeEventListener(event, handler as EventListener, opts);
}

/** Escape an untrusted string for safe interpolation into innerHTML. */
export function escapeHtml(s: unknown): string {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Create an element with optional attrs + children in one call. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | boolean | number> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'class' || k === 'className') {
      node.className = String(v);
    } else if (k === 'style' && typeof v === 'string') {
      node.setAttribute('style', v);
    } else if (k.startsWith('data-') || k.startsWith('aria-') || k === 'role') {
      node.setAttribute(k, String(v));
    } else if (k in node) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (node as any)[k] = v;
    } else {
      node.setAttribute(k, String(v));
    }
  }
  for (const c of children) {
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

/** Debounce — fires fn at most once per `wait` ms. */
export function debounce<T extends (...a: unknown[]) => unknown>(
  fn: T,
  wait: number,
): (...args: Parameters<T>) => void {
  let t: ReturnType<typeof setTimeout> | undefined;
  return (...args: Parameters<T>): void => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** Throttle — at most one call per `wait` ms. */
export function throttle<T extends (...a: unknown[]) => unknown>(
  fn: T,
  wait: number,
): (...args: Parameters<T>) => void {
  let last = 0;
  return (...args: Parameters<T>): void => {
    const now = Date.now();
    if (now - last >= wait) {
      last = now;
      fn(...args);
    }
  };
}
