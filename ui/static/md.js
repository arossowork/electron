// Tiny Markdown renderer. Every piece of input text is HTML-escaped before any tag is added, so the output
// can only contain the tags written below. Links keep only http(s), mailto and relative targets.
function mdEsc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function mdInline(raw) {
  const codes = [];
  let s = mdEsc(raw).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
    const url = u.replace(/&amp;/g, '&');
    return /^(https?:|mailto:|[#\/.]|[\w-]+(\/|$))/i.test(url) && !/^\s*(javascript|data|vbscript):/i.test(url)
      ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>` : m;
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, '$1<em>$2</em>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}
function renderMarkdown(text) {
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n'), out = [];
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push(`<pre><code>${mdEsc(buf.join('\n'))}</code></pre>`); continue;
    }
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) { out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push('<hr>'); i++; continue; }
    if (l.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push('<table><thead><tr>' + head.map(c => `<th>${mdInline(c)}</th>`).join('') + '</tr></thead><tbody>'
        + rows.map(r => '<tr>' + r.map(c => `<td>${mdInline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
      continue;
    }
    if (/^>\s?/.test(l)) {
      const buf = []; while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
      out.push(`<blockquote>${mdInline(buf.join(' '))}</blockquote>`); continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const ord = /^\s*\d+[.)]/.test(l), buf = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, ''));
      out.push(`<${ord ? 'ol' : 'ul'}>` + buf.map(x => `<li>${mdInline(x)}</li>`).join('') + `</${ord ? 'ol' : 'ul'}>`); continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|>\s?|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])) buf.push(lines[i++]);
    out.push(`<p>${buf.map(mdInline).join('<br>')}</p>`);
  }
  return out.join('\n');
}
if (typeof module !== 'undefined') module.exports = { renderMarkdown };
