// Markdown -> safe HTML, plus a small syntax highlighter. No dependencies.
//
// Safety model: every character of model/user text is HTML-escaped before any
// markup is added, link targets are limited to http(s)/mailto/relative, and
// nothing is ever evaluated — so a reply can't inject markup or scripts.
//
// Built for streaming: an unterminated ``` fence or half-written table simply
// renders as far as it has got, so the same function is called on every chunk.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

// ── syntax highlighting ───────────────────────────────────────────────────
const KW = {
  python: "and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
  js: "async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield interface type enum implements readonly public private protected declare namespace abstract as satisfies keyof",
  c: "auto break case catch char class const constexpr continue default delete do double else enum explicit extern float for friend goto if inline int long namespace new operator private protected public register return short signed sizeof static struct switch template this throw try typedef typename union unsigned using virtual void volatile while fn let mut impl trait pub use mod match loop where async await move ref self Self crate func package import defer go chan map range select var interface type string bool byte final abstract extends implements instanceof synchronized throws override internal sealed readonly",
  sql: "select from where and or not null is in like between join inner left right outer full on as group by order having limit offset insert into values update set delete create table index view drop alter add column primary key foreign references unique default distinct union all case when then else end exists with returning asc desc count sum avg min max",
  bash: "if then else elif fi for while until do done case esac in function return exit local export readonly declare unset shift break continue echo printf read cd source alias eval exec trap set",
  powershell: "if else elseif switch foreach for while do until break continue return function param begin process end try catch finally throw filter in class enum using module",
};
const LIT = {
  python: "None True False self cls print len range str int float list dict set tuple open isinstance super enumerate zip map filter sorted",
  js: "null undefined true false NaN Infinity console window document Math JSON Object Array String Number Boolean Promise Map Set Date Error",
  c: "null nullptr NULL true false nil None Some Ok Err String Vec Option Result println printf std",
  sql: "true false",
  bash: "true false",
  powershell: "$true $false $null",
};
const ALIAS = {
  py: "python", python3: "python", ipython: "python",
  javascript: "js", jsx: "js", ts: "js", tsx: "js", typescript: "js", mjs: "js", cjs: "js", node: "js",
  cpp: "c", "c++": "c", h: "c", hpp: "c", cs: "c", csharp: "c", java: "c", kotlin: "c", go: "c", golang: "c",
  rust: "c", rs: "c", swift: "c", scala: "c", dart: "c", php: "c", zig: "c",
  sh: "bash", shell: "bash", zsh: "bash", console: "bash", cmd: "bash", bat: "bash",
  ps1: "powershell", pwsh: "powershell", ps: "powershell",
  jsonc: "json", json5: "json", yml: "yaml", ini: "toml", conf: "toml", cfg: "toml", env: "toml", dockerfile: "bash",
  xml: "html", svg: "html", vue: "html", htm: "html", scss: "css", less: "css",
  mysql: "sql", postgres: "sql", postgresql: "sql", sqlite: "sql", tsql: "sql", psql: "sql",
};
const sets = {};
const wordSet = (lang, table) => {
  const key = table === KW ? "k:" + lang : "l:" + lang;
  if (!sets[key]) sets[key] = new Set((table[lang] || "").split(" ").filter(Boolean));
  return sets[key];
};

const STR = '"""[\\s\\S]*?(?:"""|$)|\'\'\'[\\s\\S]*?(?:\'\'\'|$)|"(?:\\\\.|[^"\\\\\\n])*"?|\'(?:\\\\.|[^\'\\\\\\n])*\'?';
const NUM = "\\b0[xX][\\da-fA-F_]+\\b|\\b\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b";
const IDENT = "[A-Za-z_$][\\w$]*";
const GRAMMAR = {
  python: new RegExp(`(#.*)|(${STR})|(${NUM})|(${IDENT})`, "g"),
  js: new RegExp(`(\\/\\/.*|\\/\\*[\\s\\S]*?(?:\\*\\/|$))|(\`(?:\\\\.|[^\`\\\\])*\`?|${STR})|(${NUM})|(${IDENT})`, "g"),
  c: new RegExp(`(\\/\\/.*|\\/\\*[\\s\\S]*?(?:\\*\\/|$))|(${STR})|(${NUM})|(${IDENT})`, "g"),
  sql: new RegExp(`(--.*|\\/\\*[\\s\\S]*?(?:\\*\\/|$))|(${STR})|(${NUM})|(${IDENT})`, "g"),
  bash: new RegExp(`(#.*)|(${STR})|(${NUM})|(\\$\\{?[\\w@#?*!-]+\\}?|${IDENT})`, "g"),
  powershell: new RegExp(`(#.*|<#[\\s\\S]*?(?:#>|$))|(${STR})|(${NUM})|(\\$[\\w:]+|[A-Za-z_][\\w-]*)`, "g"),
  toml: new RegExp(`(#.*|;.*)|(${STR})|(${NUM})|(${IDENT})`, "g"),
  yaml: new RegExp(`(#.*)|(${STR})|(${NUM})|(${IDENT})`, "g"),
  json: new RegExp(`(\\/\\/.*)|(${STR})|(${NUM})|(${IDENT})`, "g"),
};

/** Highlight `code` for `lang`; unknown languages come back merely escaped. */
export function highlight(code, lang) {
  let name = String(lang || "").toLowerCase();
  name = ALIAS[name] || name;
  if (name === "html") return highlightMarkup(code);
  if (name === "css") return highlightCss(code);
  const re = GRAMMAR[name];
  if (!re || code.length > 60000) return esc(code);
  const kw = wordSet(name, KW), lit = wordSet(name, LIT);
  const ci = name === "sql";
  let out = "", last = 0, m;
  re.lastIndex = 0;
  while ((m = re.exec(code))) {
    if (m[0] === "") { re.lastIndex++; continue; }
    out += esc(code.slice(last, m.index));
    last = m.index + m[0].length;
    const text = esc(m[0]);
    if (m[1] !== undefined) out += `<span class="tok-c">${text}</span>`;
    else if (m[2] !== undefined) {
      // a JSON/YAML/TOML key reads better as a name than as a string
      const isKey = (name === "json" || name === "yaml") && /^\s*:/.test(code.slice(last, last + 3));
      out += `<span class="${isKey ? "tok-f" : "tok-s"}">${text}</span>`;
    } else if (m[3] !== undefined) out += `<span class="tok-n">${text}</span>`;
    else {
      const word = ci ? m[0].toLowerCase() : m[0];
      const next = code[last];
      if (kw.has(word)) out += `<span class="tok-k">${text}</span>`;
      else if (lit.has(word) || /^(?:true|false|null|none|nil)$/i.test(word) || m[0][0] === "$")
        out += `<span class="tok-b">${text}</span>`;
      else if (next === "(") out += `<span class="tok-f">${text}</span>`;
      else if ((name === "toml" || name === "yaml") && /^\s*[=:]/.test(code.slice(last, last + 4)))
        out += `<span class="tok-f">${text}</span>`;
      else out += text;
    }
  }
  return out + esc(code.slice(last));
}

function highlightMarkup(code) {
  const re = /(<!--[\s\S]*?(?:-->|$))|(<\/?[A-Za-z][\w:-]*)|("[^"]*"?|'[^']*'?)|(\/?>)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    last = m.index + m[0].length;
    const t = esc(m[0]);
    out += m[1] !== undefined ? `<span class="tok-c">${t}</span>`
      : m[3] !== undefined ? `<span class="tok-s">${t}</span>`
      : `<span class="tok-k">${t}</span>`;
  }
  return out + esc(code.slice(last));
}

function highlightCss(code) {
  const re = /(\/\*[\s\S]*?(?:\*\/|$))|("[^"]*"?|'[^']*'?)|(#[\da-fA-F]{3,8}\b|\b\d[\d.]*(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?\b)|([A-Za-z-]+)(?=\s*:)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    last = m.index + m[0].length;
    const t = esc(m[0]);
    out += m[1] !== undefined ? `<span class="tok-c">${t}</span>`
      : m[2] !== undefined ? `<span class="tok-s">${t}</span>`
      : m[3] !== undefined ? `<span class="tok-n">${t}</span>`
      : `<span class="tok-f">${t}</span>`;
  }
  return out + esc(code.slice(last));
}

// ── inline markup ─────────────────────────────────────────────────────────
const SAFE_URL = /^(?:https?:\/\/|mailto:|\/(?!\/)|#|\.{0,2}\/)/i;

function inline(text) {
  // 1. lift code spans out first so nothing inside them is formatted
  const codes = [];
  text = text.replace(/(`+)(?!`)([\s\S]*?[^`])\1(?!`)/g, (_, _t, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  // 2. escape everything that remains
  text = esc(text);
  // 3. links (the URL is already escaped, so it can't break out of the attribute)
  const links = [];
  const hold = (html) => { links.push(html); return `\u0001${links.length - 1}\u0001`; };
  text = text.replace(/!?\[([^\]\n]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*?&quot;)?\)/g, (m, label, url) =>
    SAFE_URL.test(url) ? hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`) : m);
  text = text.replace(/(^|[\s(])(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g, (m, pre, url) =>
    pre + hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
  // 4. emphasis
  text = text
    .replace(/\*\*([^\s*](?:[^*\n]*?[^\s*])?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\w_])__([^\s_](?:[^_\n]*?[^\s_])?)__(?![\w_])/g, "$1<strong>$2</strong>")
    .replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?![*\w])/g, "$1<em>$2</em>")
    .replace(/(^|[^\w_])_([^\s_](?:[^_\n]*?[^\s_])?)_(?![\w_])/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+?)~~/g, "<del>$1</del>");
  // 5. put the held pieces back
  text = text.replace(/\u0001(\d+)\u0001/g, (_, i) => links[+i]);
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
}

// ── block structure ───────────────────────────────────────────────────────
const RE_FENCE = /^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)\s*$/;
const RE_HEAD = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RE_HR = /^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/;
const RE_LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const RE_TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$/;
const RE_QUOTE = /^\s{0,3}>\s?(.*)$/;

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function codeBlock(text, lang, opts) {
  const body = highlight(text, lang);
  const label = esc(lang || "");
  if (opts.codeHeader) {
    return `<div class="code"><div class="code-head"><span>${label || "text"}</span>` +
      `<button type="button" class="code-copy" data-copy>copy</button></div>` +
      `<pre><code>${body}</code></pre></div>`;
  }
  return `<pre${label ? ` data-lang="${label}"` : ""}><code>${body}</code></pre>`;
}

function renderList(lines, start, opts) {
  // lines[start] is a list item; consume the whole (possibly nested) list
  const first = RE_LIST.exec(lines[start]);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  let html = ordered
    ? `<ol${parseInt(first[2], 10) !== 1 ? ` start="${parseInt(first[2], 10)}"` : ""}>`
    : "<ul>";
  let i = start;
  while (i < lines.length) {
    const m = RE_LIST.exec(lines[i]);
    if (!m || m[1].length < baseIndent) break;
    if (m[1].length > baseIndent) {           // deeper than us with no parent: treat as ours
      const [sub, next] = renderList(lines, i, opts);
      html = html.replace(/<\/li>$/, "") + sub + "</li>";
      i = next;
      continue;
    }
    if (/\d/.test(m[2]) !== ordered) break;
    let body = m[3];
    i++;
    const kids = [];
    // continuation lines + nested lists belong to this item
    while (i < lines.length) {
      const line = lines[i];
      const lm = RE_LIST.exec(line);
      if (lm) {
        if (lm[1].length > baseIndent) { kids.push(i); const [, nx] = renderList(lines, i, opts); i = nx; continue; }
        break;
      }
      if (!line.trim()) {
        // a blank line ends the item unless more of the list follows it
        const nxt = lines[i + 1];
        if (nxt !== undefined && RE_LIST.test(nxt) && RE_LIST.exec(nxt)[1].length >= baseIndent) { i++; continue; }
        break;
      }
      if (/^\s{2,}\S/.test(line) && !RE_FENCE.test(line)) { body += "\n" + line.trim(); i++; continue; }
      break;
    }
    const task = /^\[([ xX])\]\s+(.*)$/s.exec(body);
    let item = task
      ? `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> ${inline(task[2])}`
      : inline(body);
    item = item.replace(/\n/g, "<br>");
    for (const k of kids) item += renderList(lines, k, opts)[0];
    html += `<li${task ? ' class="task"' : ""}>${item}</li>`;
  }
  return [html + (ordered ? "</ol>" : "</ul>"), i];
}

/** Render markdown to HTML. opts.codeHeader adds a language bar + copy button
 *  to code blocks (the page wires up [data-copy]). */
export function renderMarkdown(src, opts = {}) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  let html = "";
  let para = [];
  const flush = () => {
    if (para.length) { html += `<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`; para = []; }
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    const fence = RE_FENCE.exec(line);
    if (fence) {
      flush();
      const marker = fence[2][0], width = fence[2].length, indent = fence[1].length;
      const body = [];
      i++;
      while (i < lines.length) {
        const close = RE_FENCE.exec(lines[i]);
        if (close && close[2][0] === marker && close[2].length >= width && !close[3]) { i++; break; }
        body.push(lines[i].slice(Math.min(indent, lines[i].length - lines[i].trimStart().length)));
        i++;
      }
      html += codeBlock(body.join("\n"), fence[3], opts);
      continue;
    }
    if (!line.trim()) { flush(); i++; continue; }

    const head = RE_HEAD.exec(line);
    if (head) { flush(); const n = head[1].length; html += `<h${n}>${inline(head[2])}</h${n}>`; i++; continue; }
    if (RE_HR.test(line)) { flush(); html += "<hr>"; i++; continue; }

    if (RE_QUOTE.test(line)) {
      flush();
      const inner = [];
      while (i < lines.length && (RE_QUOTE.test(lines[i]) || (lines[i].trim() && inner.length && !RE_LIST.test(lines[i]) && !RE_HEAD.test(lines[i]) && !RE_FENCE.test(lines[i])))) {
        const q = RE_QUOTE.exec(lines[i]);
        inner.push(q ? q[1] : lines[i]);
        i++;
      }
      html += `<blockquote>${renderMarkdown(inner.join("\n"), opts)}</blockquote>`;
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const headCells = splitRow(line);
      const sep = splitRow(lines[i + 1]);
      if (headCells.length === sep.length) {
        flush();
        const align = sep.map((c) => (/^:-+:$/.test(c) ? "center" : /-:$/.test(c) ? "right" : ""));
        const cell = (tag, c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${inline(c)}</${tag}>`;
        let t = "<table><thead><tr>" + headCells.map((c, k) => cell("th", c, k)).join("") + "</tr></thead><tbody>";
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const cells = splitRow(lines[i]);
          t += "<tr>" + headCells.map((_, k) => cell("td", cells[k] ?? "", k)).join("") + "</tr>";
          i++;
        }
        html += t + "</tbody></table>";
        continue;
      }
    }

    if (RE_LIST.test(line)) {
      flush();
      const [list, next] = renderList(lines, i, opts);
      html += list;
      i = next;
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flush();
  return html;
}

/** Markdown -> plain prose for text-to-speech (code and symbols removed). */
export function speakable(md) {
  return String(md ?? "")
    .replace(/```[\s\S]*?(?:```|$)/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!?\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " link ")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
    .replace(/\|/g, ", ")
    .replace(/[*_~#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
