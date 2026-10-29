"""Build a self-contained, escaped human index from the real SMB client listing."""
import collections
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent;WORK=HERE.parents[1]
SOURCE=WORK/'work/project-library-production-list.json'
TARGET=WORK/'outputs/Project-Library-2026-10-29/Project-Index.html'
data=json.loads(SOURCE.read_text(encoding='utf-8-sig'));projects=data['projects']
assert len(projects)==135 and sum(p['files']>0 for p in projects)==131

def esc(value):return html.escape(str(value),quote=True)
def category(p):
    prefix=p['id'].split('-')[0]
    return {'magix':'MAGIX','videos':'Videos','media':'Media assets','stats':'The Stats','codex':'Codex','workspace':'Codex','mw5':'MW5','sales':'Sales projects','migration':'Guides','video':'Tools'}.get(prefix,'Other projects')
def size(value):
    if value>=1024**3:return f'{value/1024**3:,.2f} GiB'
    if value>=1024**2:return f'{value/1024**2:,.1f} MiB'
    if value>=1024:return f'{value/1024:,.1f} KiB'
    return f'{value:,} B'
def notes_list(title,values):
    return '<h4>'+esc(title)+'</h4><ul>'+''.join('<li>'+esc(value)+'</li>' for value in values)+'</ul>' if values else ''

rows=[];categories=sorted({category(p) for p in projects})
for project in sorted(projects,key=lambda p:(category(p),p['name'].casefold(),p['id'])):
    group=category(project);cloud=project['files']==0
    warnings=project.get('warnings',[]);notes=project.get('notes',[])
    search=' '.join([project['name'],project['id'],group,project.get('description',''),*warnings,*notes])
    details='<p>'+esc(project.get('description',''))+'</p>' if project.get('description') else ''
    if cloud:details+='<p class="cloud-note">This entry has no published local payload. Its cloud files must become available and be published before fetching.</p>'
    details+=notes_list('Warnings',warnings)+notes_list('Notes',notes)
    if project.get('workspaceRelative'):details+='<h4>Workspace inside cache</h4><code>'+esc(project['workspaceRelative'])+'</code>'
    summary='Details'
    if warnings or notes:summary+=f' · {len(warnings)} warnings · {len(notes)} notes'
    status='Awaiting cloud files' if cloud else 'Local files published'
    count='0 local files' if cloud else f'{project["files"]:,} files'
    amount='Not yet local' if cloud else size(project['bytes'])
    rows.append(f'''<tr data-project="{esc(project['id'])}" data-category="{esc(group)}" data-availability="{'cloud' if cloud else 'local'}" data-search="{esc(search)}">
<th scope="row"><span class="project-name">{esc(project['name'])}</span><span class="category">{esc(group)}</span><code class="project-id">{esc(project['id'])}</code></th>
<td class="size"><span>{esc(count)}</span><strong title="{project['bytes']:,} published local bytes">{esc(amount)}</strong></td>
<td><span class="status {'cloud' if cloud else 'local'}">{status}</span><details><summary>{esc(summary)}</summary>{details}</details></td></tr>''')

options=''.join('<option value="'+esc(c)+'">'+esc(c)+'</option>' for c in categories)
source_sha=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
document='''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MSI Project Library — Project Index</title>
<style>
:root{color-scheme:light;--ink:#172b3a;--muted:#4b5d6a;--line:#d4dee4;--blue:#174e77}
*{box-sizing:border-box}body{margin:0;background:#f5f7f9;color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 24px 48px}header{margin-bottom:24px}h1{font-size:2rem;line-height:1.2;margin:0 0 10px}h2{font-size:1.15rem;margin:0 0 14px}p{margin:9px 0}a{color:var(--blue);text-underline-offset:3px}.intro{max-width:940px;color:var(--muted)}.links{display:flex;gap:22px;flex-wrap:wrap;margin:15px 0}.notice{padding:12px 15px;border-left:4px solid #c58b24;background:#fff8e7;font-size:.94rem}
.filters{display:grid;grid-template-columns:minmax(200px,1fr) 210px 210px auto;gap:12px;align-items:end;margin:24px 0 12px}label{display:block;font-size:.86rem;font-weight:600;margin-bottom:5px}input,select,button{font:inherit;border:1px solid #9eafb9;background:white;color:var(--ink);border-radius:5px;padding:9px 10px;width:100%;min-height:44px}button{cursor:pointer;white-space:nowrap}button:hover{background:#e8f0f5}input:focus,select:focus,button:focus,summary:focus{outline:3px solid #80badb;outline-offset:2px}.count{font-size:.9rem;color:var(--muted);margin-bottom:12px}
table{width:100%;border-collapse:collapse;background:white;border:1px solid var(--line)}caption{text-align:left;font-size:.9rem;color:var(--muted);padding:0 0 9px}thead{background:#e9eff3;text-align:left}th,td{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:top}thead th{font-size:.86rem}tbody th{font-weight:400;text-align:left;width:48%;overflow-wrap:anywhere}.project-name{display:block;font-weight:650}.category{display:block;color:var(--muted);font-size:.83rem;margin-top:4px}code{font-family:Consolas,"Cascadia Code",monospace;font-size:.8rem;overflow-wrap:anywhere}.project-id{display:block;margin-top:4px;color:#536975}.size{width:15%;white-space:nowrap;font-size:.88rem}.size strong{display:block;font-size:1rem;margin-top:3px}.status{display:block;font-size:.86rem;font-weight:600}.local{color:#246043}.cloud{color:#865400}.cloud-note{color:#865400}details{margin-top:8px;font-size:.86rem;overflow-wrap:anywhere}summary{cursor:pointer;color:var(--blue)}details h4{font-size:.86rem;margin:13px 0 4px}details ul{padding-left:19px;margin:4px 0}details li{margin:5px 0}tr[hidden]{display:none}.empty{padding:22px;background:white;border:1px solid var(--line)}footer{font-size:.8rem;color:var(--muted);margin-top:24px;overflow-wrap:anywhere}
@media(max-width:850px){.filters{grid-template-columns:1fr 1fr}.search-field{grid-column:1/-1}main{padding:24px 14px}th,td{padding:12px 10px}.size{white-space:normal}h1{font-size:1.65rem}}
@media(max-width:540px){.filters{grid-template-columns:1fr}.search-field{grid-column:auto}thead{display:none}table,tbody,tr,th,td{display:block;width:100%!important}tbody tr{border-bottom:2px solid var(--line)}th,td{border-bottom:0}.size span,.size strong{display:inline;margin-right:12px}.size{padding-top:0;padding-bottom:4px}}
@media print{body{background:white}main{max-width:none;padding:0}.filters,.links,button{display:none}details:not([open]){display:none}thead{display:table-header-group}tr{break-inside:avoid}}
</style></head><body><main>
<header><h1>MSI Project Library</h1><p class="intro">Find a project, then search its name or ID in the local <strong>Project-Library.cmd</strong> client to fetch a working copy.</p>
<p><strong>135 entries</strong> · 131 with local files · 4 awaiting cloud files</p>
<p class="intro">Publication complete: 85,154 selected local files, 301,702,469,063 bytes (280.98 GiB). Entries can overlap; row sizes should not be added to estimate unique storage.</p>
<nav class="links" aria-label="Handoff files"><a href="Project-Library-Client.zip" download>Client ZIP</a><a href="MSI-SERVER-HANDOFF.txt">Server handoff and instructions</a></nav>
<div class="notice">21 originally deferred cloud files remain unavailable locally. MAGIX dependency warnings still apply; “local files published” does not mean every project is runnable. Keep one active working copy and read the handoff before changing original MSI files.</div>
</header>
<section aria-labelledby="index-heading"><h2 id="index-heading">Project index</h2>
<div class="filters"><div class="search-field"><label for="search">Search names, IDs, notes, or warnings</label><input id="search" type="search" placeholder="For example: The Stats, Rialto, Codex" autocomplete="off"></div>
<div><label for="category">Category</label><select id="category"><option value="">All categories</option>''' + options + '''</select></div>
<div><label for="availability">Availability</label><select id="availability"><option value="">All entries</option><option value="local">Local files published</option><option value="cloud">Awaiting cloud files</option></select></div>
<button id="clear" type="button">Clear filters</button></div>
<p id="count" class="count" role="status" aria-live="polite">Showing 135 of 135 entries.</p>
<noscript><p>Filtering requires JavaScript. All entries remain visible below.</p></noscript>
<table><caption>Sizes are the published local selection for each entry. Open Details for its warnings and notes.</caption><thead><tr><th scope="col">Project and client search ID</th><th scope="col">Local payload</th><th scope="col">Availability and notes</th></tr></thead><tbody>''' + '\n'.join(rows) + '''</tbody></table>
<p id="empty" class="empty" hidden>No entries match these filters. Try a shorter search or clear the filters.</p></section>
<footer>Snapshot: 29 October 2026. Source: the shipped client’s successful real-SMB production list, catalog '''+esc(data['catalogId'])+'''. Use the client for later catalog changes. This page works offline and loads no external resources.<br>Source-list SHA256: '''+source_sha+'''</footer>
<script>
(() => {
  'use strict';
  const search = document.getElementById('search');
  const category = document.getElementById('category');
  const availability = document.getElementById('availability');
  const rows = Array.from(document.querySelectorAll('tbody tr'));
  const normalize = value => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase();
  const searchable = rows.map(row => normalize(row.dataset.search));
  function filter() {
    const words = normalize(search.value).trim().split(/\\s+/).filter(Boolean);
    let visible = 0;
    rows.forEach((row, index) => {
      const matches = words.every(word => searchable[index].includes(word)) &&
        (!category.value || row.dataset.category === category.value) &&
        (!availability.value || row.dataset.availability === availability.value);
      row.hidden = !matches;
      if (matches) visible++;
    });
    document.getElementById('count').textContent = `Showing ${visible} of ${rows.length} entries.`;
    document.getElementById('empty').hidden = visible !== 0;
  }
  search.addEventListener('input', filter);
  category.addEventListener('change', filter);
  availability.addEventListener('change', filter);
  document.getElementById('clear').addEventListener('click', () => {
    search.value = ''; category.value = ''; availability.value = '';
    filter(); search.focus();
  });
  filter();
})();
</script></main></body></html>
'''
TARGET.write_text(document,encoding='utf-8')

class Check(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.links=[];self.scripts=[];self.inscript=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='tr' and 'data-project' in a:self.rows.append(a)
        if tag=='a':self.links.append(a.get('href'))
        if 'src' in a:assert False,'External/resource src is forbidden'
        if tag=='script':self.inscript=True
    def handle_endtag(self,tag):
        if tag=='script':self.inscript=False
    def handle_data(self,data):
        if self.inscript:self.scripts.append(data)
check=Check();check.feed(document)
assert len(check.rows)==135 and len({r['data-project'] for r in check.rows})==135
assert sum(r['data-availability']=='cloud' for r in check.rows)==4
assert set(check.links)=={'Project-Library-Client.zip','MSI-SERVER-HANDOFF.txt'}
assert esc('<script>"&')=='&lt;script&gt;&quot;&amp;'
validation={'source':str(SOURCE),'sourceSha256':source_sha,'html':str(TARGET),'htmlSha256':hashlib.sha256(TARGET.read_bytes()).hexdigest(),'entries':135,'localEntries':131,'awaitingCloudEntries':4,'externalResources':0,'categories':dict(collections.Counter(category(p) for p in projects)),'relativeLinks':check.links,'bytes':TARGET.stat().st_size}
(HERE/'project-index-validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
(HERE/'project-index-filter.js').write_text(''.join(check.scripts),encoding='utf-8')
(HERE/'project-index-filter-fixture.json').write_text(json.dumps(check.rows,ensure_ascii=False),encoding='utf-8')
print(json.dumps(validation,indent=2))
