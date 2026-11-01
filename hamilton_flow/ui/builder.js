/* ---------------- Builder ---------------- */
const B={on:false,spec:{semantic_models:[],metrics:[]},tables:[],issues:[],yaml:'',dirty:false,opts:{aggregations:[],metric_types:[],grains:[]},view:'explore',timer:null,preview:null};
const get=(o,p)=>p.split('.').reduce((a,k)=>a?.[k],o);
function setp(o,p,v){const ks=p.split('.');const last=ks.pop();const t=ks.reduce((a,k)=>a[k],o);t[last]=v}
const opt=(vals,cur,blank)=>(blank!=null?`<option value="">${esc(blank)}</option>`:'')+vals.map(v=>{const [val,lab]=Array.isArray(v)?v:[v,v];return `<option value="${esc(val)}" ${val===cur?'selected':''}>${esc(lab)}</option>`}).join('');
const inp=(path,label,ph='',type='text')=>`<label class="f"><span>${label}</span><input type="${type}" data-p="${path}" value="${esc(get(B.spec,path)??'')}" placeholder="${esc(ph)}"></label>`;
const sel=(path,label,options,blank)=>`<label class="f"><span>${label}</span><select data-p="${path}">${opt(options,get(B.spec,path)??'',blank)}</select></label>`;

function touch(){B.dirty=true;clearTimeout(B.timer);B.timer=setTimeout(recheck,350);barUpdate()}
async function recheck(){const r=await api('/builder/check',{spec:B.spec});if(r.ok){B.issues=r.json.issues;B.yaml=r.json.yaml}else B.issues=[{level:'error',message:r.json.error?.message||'Check failed'}];barUpdate();if(typeof onChecked==='function')onChecked()}
function barUpdate(){
  if(B.on&&B.view!=='explore')$('#summary').textContent=`${B.spec.metrics.length} metric${B.spec.metrics.length===1?'':'s'}${B.dirty?' (unsaved)':''}`;
  $('#bar').hidden=!(B.on&&B.view!=='explore');
  const errs=B.issues.filter(i=>i.level==='error'),info=B.issues.filter(i=>i.level==='info');
  const empty=!B.spec.metrics.length;
  $('#bar-status').innerHTML=errs.length?`<span class="issue">${esc(errs[0].message)}</span>`:info.length?`<span class="issue info">${esc(info[0].message)}</span>`:B.dirty?'<span class="ok">Model is valid. Unsaved changes.</span>':'<span class="muted">All changes saved and applied.</span>';
  $('#save').disabled=!!errs.length||empty||!B.dirty;
  $('#yaml').textContent=B.yaml;
}
function bindInputs(root){
  root.querySelectorAll('[data-p]').forEach(e=>{const h=()=>{setp(B.spec,e.dataset.p,e.value);touch()};e.oninput=h;if(e.tagName==='SELECT')e.onchange=h});
  root.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>actions[e.dataset.act]?.(e.dataset));
}
const rerender=()=>{renderData();renderMetrics()};

/* Data tab */
function renderData(){
  const root=$('#view-data');
  const tables=B.tables.map(t=>{const used=B.spec.semantic_models.some(m=>m.table===t.table);
    return `<div class="card"><h3>${esc(t.table)} ${used?'<span class="badge">modeled</span>':''}<span class="sp" style="flex:1"></span>
      <button class="sm" data-act="preview" data-t="${esc(t.table)}">Preview</button>
      <button class="sm primary" data-act="model" data-t="${esc(t.table)}">${used?'Re-draft model':'Model this table'}</button>
      <button class="sm x" data-act="drop" data-t="${esc(t.table)}" title="Drop table">✕</button></h3>
      <div class="chips">${t.columns.map(c=>`<span class="chip">${esc(c.name)} <span class="muted">${esc(c.type)}</span></span>`).join('')}</div></div>`}).join('')||'<p class="muted">No tables yet. Upload a CSV, Parquet or JSON file.</p>';
  const models=B.spec.semantic_models.map((m,i)=>modelCard(m,i)).join('')||'<p class="muted">Model a table to define entities, dimensions and measures.</p>';
  root.innerHTML=`<div class="two"><div>
    <h2>Data inputs</h2>
    <div class="card"><div class="drop" id="drop">Drop a file here or <label style="color:var(--accent);cursor:pointer">choose<input type="file" id="file" accept=".csv,.tsv,.parquet,.json,.jsonl,.ndjson" hidden></label><br><span class="muted">CSV, TSV, Parquet, JSON (max 50 MB)</span></div>
      <div class="row" style="margin-top:8px;align-items:end"><label class="f"><span>Table name</span><input type="text" id="tname" placeholder="orders"></label><label class="item" style="flex:0 0 auto"><input type="checkbox" id="replace"> replace</label></div>
      <div id="upmsg" class="muted" style="margin-top:6px"></div></div>
    ${tables}</div>
    <div><h2>Semantic models</h2>${models}${B.preview?previewCard():''}</div></div>`;
  bindInputs(root);
  const drop=$('#drop'),file=$('#file');
  const pick=f=>{if(!f)return;if(!$('#tname').value)$('#tname').value=f.name.replace(/\.[^.]+$/,'').toLowerCase().replace(/[^a-z0-9_]/g,'_').replace(/^(\d)/,'t_$1');upload(f)};
  file.onchange=()=>pick(file.files[0]);
  drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};drop.ondragleave=()=>drop.classList.remove('over');
  drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');pick(e.dataTransfer.files[0])};
}
function previewCard(){const p=B.preview;return `<div class="card"><h3>Preview: ${esc(p.table)} <span class="muted">${p.row_count} rows</span></h3><div class="scroll"><table><thead><tr>${p.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${p.rows.map(r=>`<tr>${p.columns.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`}
function modelCard(m,i){
  const P=`semantic_models.${i}`,timeDims=(m.dimensions||[]).filter(d=>d.type==='time').map(d=>d.name);
  return `<div class="card"><h3>${esc(m.name)||'(unnamed)'} <span class="badge">${esc(m.table)}</span><span style="flex:1"></span><button class="sm x" data-act="rmmodel" data-i="${i}">Remove</button></h3>
  <div class="grid">${inp(P+'.name','Name','orders')}${inp(P+'.table','Table','main.orders')}${sel(P+'.agg_time_dimension','Aggregation time',timeDims,'(none)')}</div>
  <h2>Entities <span class="muted">keys that join models</span></h2>${(m.entities||[]).map((e,j)=>`<div class="line"><input data-p="${P}.entities.${j}.name" value="${esc(e.name)}" placeholder="name"><select data-p="${P}.entities.${j}.type">${opt(['primary','unique','foreign','natural'],e.type)}</select><input data-p="${P}.entities.${j}.expr" value="${esc(e.expr||'')}" placeholder="column or SQL"><button class="sm x" data-act="rm" data-k="entities" data-i="${i}" data-j="${j}">✕</button></div>`).join('')}
  <button class="sm" data-act="add" data-k="entities" data-i="${i}">+ Entity</button>
  <h2>Dimensions <span class="muted">what you can group and filter by</span></h2>${(m.dimensions||[]).map((d,j)=>`<div class="line dim"><input data-p="${P}.dimensions.${j}.name" value="${esc(d.name)}" placeholder="name"><select data-p="${P}.dimensions.${j}.type">${opt(['categorical','time'],d.type)}</select><select data-p="${P}.dimensions.${j}.granularity" ${d.type==='time'?'':'disabled'}>${opt(B.opts.grains,d.granularity||'day')}</select><input data-p="${P}.dimensions.${j}.expr" value="${esc(d.expr||'')}" placeholder="column or SQL"><button class="sm x" data-act="rm" data-k="dimensions" data-i="${i}" data-j="${j}">✕</button></div>`).join('')}
  <button class="sm" data-act="add" data-k="dimensions" data-i="${i}">+ Dimension</button>
  <h2>Measures <span class="muted">numbers that metrics aggregate</span></h2>${(m.measures||[]).map((x,j)=>`<div class="line"><input data-p="${P}.measures.${j}.name" value="${esc(x.name)}" placeholder="name"><select data-p="${P}.measures.${j}.agg">${opt(B.opts.aggregations,x.agg)}</select><input data-p="${P}.measures.${j}.expr" value="${esc(x.expr||'')}" placeholder="column or SQL"><button class="sm x" data-act="rm" data-k="measures" data-i="${i}" data-j="${j}">✕</button></div>`).join('')}
  <button class="sm" data-act="add" data-k="measures" data-i="${i}">+ Measure</button></div>`;
}
async function upload(f){
  const name=$('#tname').value.trim(),msg=$('#upmsg');msg.textContent='Uploading…';
  const r=await fetch(`/builder/upload?filename=${encodeURIComponent(f.name)}&table=${encodeURIComponent(name)}&replace=${$('#replace').checked}`,{method:'POST',body:await f.arrayBuffer()});
  const j=await r.json();
  if(!r.ok){msg.innerHTML=`<span class="issue">${esc(j.error?.message||'Upload failed')}</span>`;return}
  await loadState(true);$('#upmsg').textContent=`Loaded ${j.table} (${j.columns.length} columns).`;
}
const actions={
  async model({t}){const r=await api('/builder/suggest',{table:t});if(!r.ok)return;const d=r.json;
    const i=B.spec.semantic_models.findIndex(m=>m.table===t);i>=0?B.spec.semantic_models[i]=d.semantic_model:B.spec.semantic_models.push(d.semantic_model);
    for(const m of d.metrics){if(!B.spec.metrics.some(x=>x.name===m.name))B.spec.metrics.push(m)}
    touch();rerender();if(d.needs_time_dimension)$('#bar-status').innerHTML='<span class="issue">This table has no date column, so measures cannot be aggregated over time. Add a time dimension.</span>'},
  async preview({t}){const [s,n]=t.split('.');const r=await api(`/builder/tables/${s}/${n}`);if(r.ok){B.preview={...r.json,table:t};renderData()}},
  async drop({t}){if(!confirm(`Drop table ${t}? This deletes its data.`))return;const [s,n]=t.split('.');await fetch(`/builder/tables/${s}/${n}`,{method:'DELETE'});B.preview=null;await loadState(true)},
  rmmodel({i}){B.spec.semantic_models.splice(+i,1);touch();rerender()},
  add({k,i}){const m=B.spec.semantic_models[+i];(m[k]??=[]).push(k==='entities'?{name:'',type:'foreign',expr:''}:k==='dimensions'?{name:'',type:'categorical',expr:'',granularity:'day'}:{name:'',agg:'sum',expr:''});touch();renderData()},
  rm({k,i,j}){B.spec.semantic_models[+i][k].splice(+j,1);touch();renderData()},
};

/* navigation + load/save */
function show(view){B.view=view;
  for(const v of ['explore','data','metrics'])$(`#view-${v}`).hidden=v!==view;
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.view===view));
  if(view==='data')renderData();if(view==='metrics'){M.reach={};renderMetrics()}barUpdate();
  if(view==='explore')reloadCatalog();
}
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>show(b.dataset.view));
$('#show-yaml').onclick=()=>{$('#yaml').hidden=!$('#yaml').hidden;$('#show-yaml').textContent=$('#yaml').hidden?'View YAML':'Hide YAML'};
$('#save').onclick=async()=>{
  $('#save').disabled=true;const r=await api2('/builder/model','PUT',{spec:B.spec});
  if(r.ok&&r.json.saved){B.dirty=false;B.issues=r.json.issues;await loadState(false);reloadCatalog();$('#bar-status').innerHTML='<span class="ok">Saved and applied. Switch to Explore to query it.</span>'}
  else{B.issues=r.json.issues||[{level:'error',message:r.json.error?.message||'Save failed'}];barUpdate()}
};
async function loadState(keepSpec){
  const r=await api('/builder/state');if(!r.ok)return false;
  B.on=true;B.tables=r.json.tables;B.opts=r.json.options;B.yaml=r.json.yaml;
  if(!keepSpec){B.spec=r.json.spec;B.issues=r.json.issues}
  document.querySelectorAll('#nav [hidden]').forEach(b=>b.hidden=false);
  if(B.view==='data')renderData();if(B.view==='metrics')renderMetrics();barUpdate();return true;
}
async function reloadCatalog(){
  const c=await api('/explorer/catalog');
  if(c.ok){state.metrics=c.json.metrics;state.selected=new Set([...state.selected].filter(n=>state.metrics.some(m=>m.name===n)));
    $('#summary').textContent=c.json.empty?'No model yet: open "Data & models"':`${state.metrics.length} metrics`;renderMetrics_explore();}
}
